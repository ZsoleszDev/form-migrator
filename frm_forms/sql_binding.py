"""The named parameters of the native queries the Java assistant writes: bound where the SQL gets them.

A small model writes the SQL well and loses the bookkeeping around it, most of all when it builds a statement from
pieces (a WHERE assembled from the screen's filters, as the Forms code assembled its DEFAULT_WHERE):

  - a piece's :name that nothing binds (sql.append(") and AIT_ILLIGAZ_KOD = :igkod") without a setParameter): Oracle
    fails at run time, and the variable made for it stays unused (PMD);
  - a piece added under an if, its value bound always (q.setParameter("kod", kod) after the query): when the
    condition is false the statement has no :kod, and Hibernate fails;
  - a piece added always, its value bound under an if (if (x != null) q.setParameter("x", x)): unbound for a null.

None of these depends on what the method does, so the migrator keeps the books. For every query it reads the pieces of
its statement - the createNativeQuery argument, and what it reads: a literal, an SQL constant, a String /
StringBuilder / List<String> built before the query (also one built from another: sql.append(where)) - each with the
conditions it is added under, and the binds (setParameter on the query or its chain, Map puts applied to the query).
A :name bound wrong or not at all is bound where its piece is: an always-present piece on the query, a conditional one
in a Map right next to the piece (sqlParams.put(...), then sqlParams.forEach(query::setParameter)) - plain Java, no
helper. The value: the model's own bind (when its variables are in scope there), the variable of that name, the
java-variables.json entry. With none of them the model is asked once, precisely (problems).

Left as written: a statement whose parameter names are built at run time (" OR X = :p" + i), a builder that is reset,
a piece chosen by ?: - and every bind that is already right.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from .java_scan import java_mask

KEYWORDS = {"true", "false", "null", "new", "this", "super", "instanceof", "return", "var", "final", "int", "long",
            "boolean", "double", "char", "byte", "short", "float", "class", "else", "if", "for", "while", "case"}
NAME = r"[A-Za-z_$][\w$]*"
TYPE = r"(?:[A-Z][\w$.]*(?:\s*<[^;(){}=]*?>)?(?:\s*\[\s*\])*|var|int|long|boolean|double|float|char|short|byte)"
# The variables a statement can be built in, and how each adds a piece.
BUILDERS = r"StringBuilder|StringBuffer|StringJoiner|String|var|(?:List|ArrayList|LinkedList|Collection)\s*<\s*String\s*>"
ADDS = r"append|insert|add|addAll"
RESETS = r"setLength|delete|deleteCharAt|replace|reverse|setCharAt|clear|remove|removeIf|removeAll|retainAll|set"
EMPTINESS = re.compile(r"!?\s*[\w$]+\s*\.\s*(?:isEmpty\s*\(\s*\)|(?:length|size)\s*\(\s*\)\s*(?:[=!<>]=?)\s*0)\s*\?")
CONTROL = re.compile(r"\s*(else\s+if|if|while|for|else)\b\s*")
TERMINAL = re.compile(r"\.\s*(?:getResultList|getSingleResult|executeUpdate|getResultStream)\s*\(")


@dataclass(eq=False)
class Piece:
    start: int              # the statement adding SQL text (after the previous ; { }), its ';' (end)
    end: int
    params: list
    path: tuple             # the conditions it runs under (headers of the blocks around it, brace-less controls)
    proper: int             # the statement itself (after its brace-less if/else/for/while)
    parent: "Piece | None" = None   # the piece that reads the builder this one is added to (None: the argument)
    gated: bool = False     # it reads builders under a ?: (other than "is it empty"): their pieces are not followed


@dataclass(eq=False)
class Bind:
    name: str
    value: str
    kind: str               # chain (on the createNativeQuery call), call (on the query variable), put (into a map)
    remove: tuple | None    # the span that takes it out of the code (None: it cannot go alone)
    position: int
    path: tuple
    group: tuple | None = None   # call: the whole statement when it holds only setParameter calls
    value_span: tuple = ()       # put: the value expression in the code
    target: str = ""             # put: the map


@dataclass(eq=False)
class Query:
    call: tuple             # entityManager.createNativeQuery(...): the receiver's start, the closing paren
    opening: int            # the createNativeQuery paren
    statement: tuple        # the statement creating it: start, ';'
    path: tuple
    variable: str | None
    argument: Piece | None = None
    pieces: list = field(default_factory=list)
    binds: list = field(default_factory=list)
    maps: list = field(default_factory=list)          # (name, position, path) of the maps applied to the query
    executions: list = field(default_factory=list)    # (position, path) of getResultList() ...
    dynamic: bool = False   # bound in a way not read here (a computed name, the query handed on)
    followed: bool = True   # every piece is known (no name built at run time, no reset builder, no ?: piece)


# --- the code's structure -----------------------------------------------------------------------------------------

def braces(mask: str) -> dict[int, int]:
    stack, pairs = [], {}
    for index, char in enumerate(mask):
        if char == "{":
            stack.append(index)
        elif char == "}" and stack:
            pairs[stack.pop()] = index
    return pairs


def closing(mask: str, opening: int) -> int:
    depth = 0
    for index in range(opening, len(mask)):
        depth += mask[index] in "([{"
        depth -= mask[index] in ")]}"
        if depth == 0:
            return index
    return -1


def statement_start(mask: str, position: int) -> int:
    depth, index = 0, position
    while index > 0:
        char = mask[index - 1]
        if char in ")]":
            depth += 1
        elif char in "([":
            depth -= 1
        elif char in ";{}" and depth <= 0:
            return index
        index -= 1
    return 0


def statement_end(mask: str, start: int) -> int:
    depth = 0
    for index in range(start, len(mask)):
        char = mask[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth < 0:
                return index
        elif char == ";" and depth == 0:
            return index
    return len(mask)


def skip_space(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def controls(mask: str, start: int, limit: int) -> tuple[list[str], int]:
    """The brace-less controls a statement starts with (if (...) / else / for (...) / while (...)) and where the
    statement itself starts."""
    found, index = [], start
    while True:
        match = CONTROL.match(mask, index)
        if not match or match.end() > limit:
            break
        if match.group(1) == "else":
            found.append("else")
            index = match.end()
            continue
        if mask[match.end():match.end() + 1] != "(":
            break
        close = closing(mask, match.end())
        if close < 0 or close >= limit:
            break
        found.append(" ".join(mask[match.start(1):close + 1].split()))
        index = close + 1
    return found, skip_space(mask, index)


def header(mask: str, pairs: dict, opening: int) -> str:
    start = statement_start(mask, opening)
    text = " ".join(mask[start:opening].split())
    if re.match(r"(?:else|catch|finally)\b", text) and start > 0 and mask[start - 1] == "}":
        before = next((o for o, c in pairs.items() if c == start - 1), None)
        if before is not None:
            text += " of " + header(mask, pairs, before)
    return text


def path(mask: str, pairs: dict, position: int) -> tuple:
    """The conditions a position is reached under: the headers of the blocks around it (if (...), else of if (...),
    for (...), a lambda ...; not a try or a plain block) and the brace-less controls of its statement."""
    found = []
    for opening in sorted(o for o, c in pairs.items() if o < position < c):
        text = header(mask, pairs, opening)
        if not re.fullmatch(r"(?:try(?:\s*\(.*\))?|finally.*|synchronized\s*\(.*\)|)", text, re.S):
            found.append(text)
    return tuple(found + controls(mask, statement_start(mask, position), position)[0])


def prefix(short: tuple, long: tuple) -> bool:
    return long[:len(short)] == short


def innermost(pairs: dict, position: int) -> int:
    return max((o for o, c in pairs.items() if o < position <= c), default=-1)


def indent_at(code: str, position: int) -> str:
    line = code.rfind("\n", 0, position) + 1
    return re.match(r"[ \t]*", code[line:]).group()


def line_span(code: str, begin: int, stop: int) -> tuple[int, int]:
    """[begin, stop) with its line when nothing else is on it."""
    line = code.rfind("\n", 0, begin) + 1
    newline = code.find("\n", stop)
    newline = len(code) if newline == -1 else newline
    if not code[line:begin].strip() and not code[stop:newline].strip():
        return line, min(newline + 1, len(code))
    return begin, stop


def removal(code: str, mask: str, pairs: dict, start: int, end: int) -> tuple | None:
    """The span that takes a statement out: with its brace-less if, and the braced if around it when nothing else is
    in that block (and no else follows). None when it cannot go alone (it is a branch of an if/else)."""
    begin = skip_space(mask, start)
    found, _ = controls(mask, start, end)
    if re.match(r"else\b", mask[begin:]) or (found and re.match(r"\s*else\b", mask[end + 1:])):
        return None
    block = innermost(pairs, begin)
    if block >= 0 and not found and mask[block + 1:pairs[block]].strip() == mask[begin:end + 1].strip():
        if re.match(r"if\s*\(", header(mask, pairs, block)) and not re.match(r"\s*else\b", mask[pairs[block] + 1:]):
            return line_span(code, skip_space(mask, statement_start(mask, block)), pairs[block] + 1)
    return line_span(code, begin, end + 1)


def drop_empty_ifs(code: str) -> str:
    """if (...) { } left empty by a removed bind (PMD: an empty if): it goes, unless an else follows."""
    for _ in range(50):
        mask = java_mask(code)
        pairs = braces(mask)
        for opening, close in sorted(pairs.items()):
            if mask[opening + 1:close].strip() or re.match(r"\s*else\b", mask[close + 1:]):
                continue
            start = skip_space(mask, statement_start(mask, opening))
            condition = re.match(r"if\s*\(", mask[start:opening])
            if condition and closing(mask, start + condition.end() - 1) == len(mask[:opening].rstrip()) - 1:
                begin, stop = line_span(code, start, close + 1)
                code = code[:begin] + code[stop:]
                break
        else:
            return code
    return code


# --- the pieces of a statement ------------------------------------------------------------------------------------

def literal_runs(code: str, mask: str, start: int, end: int):
    """The string literals of a span, joined where only + is between them: ("text", end of the last literal)."""
    from .answer_checks import CODE_TOKEN, text_of
    text, last = None, None
    for match in CODE_TOKEN.finditer(code, start, end):
        if not match.group().startswith('"'):
            continue
        if last is not None and mask[last:match.start()].replace("+", "").strip():
            yield text, last
            text = None
        text = (text or "") + text_of(match.group()[1:-1])
        last = match.end()
    if text is not None:
        yield text, last


def continues(code: str, mask: str, position: int, end: int) -> bool:
    """After a literal ending in :name and a +: whether the operand is part of the name (":p" + i). Not when it is a
    builder or a String whose text starts with a space or a parenthesis (" AND ..." / ")")."""
    operand = re.match(r"\s*\+\s*(" + NAME + r")(?:\s*\.\s*toString\s*\(\s*\))?(?![\w$])(?!\s*[.(\[])", mask[position:end])
    if not operand:
        return True
    own = re.escape(operand.group(1))
    if re.search(r"(?<![\w$.])(?:final\s+)?(?:StringBuilder|StringBuffer|StringJoiner)\s+" + own + r"\s*=", mask):
        return False
    declaration = re.search(r"(?<![\w$.])(?:final\s+)?(?:String|var)\s+" + own + r"\s*=\s*", mask)
    first = re.match(r'"(.)', code[declaration.end():]) if declaration else None
    return not first or bool(re.match(r"[\w$]", first.group(1)))


def piece_params(code: str, mask: str, start: int, end: int) -> list[str] | None:
    """The :names of the SQL text a statement adds; None when a name is built at run time (":p" + i) or the text is
    chosen by ?: (the piece is conditional in a way the migrator does not follow)."""
    from .answer_checks import sql_parameters
    found = []
    for text, last in literal_runs(code, mask, start, end):
        if re.search(r":[A-Za-z_]?\w*$", text) and re.match(r"\s*\+", mask[last:end]) and continues(code, mask, last, end):
            return None
        found += sql_parameters(text)
    if found and "?" in mask[start:end]:
        return None
    return list(dict.fromkeys(found))


def read_names(mask: str, start: int, end: int) -> list[str]:
    """The variables and constants an expression reads (not the methods it calls, not types)."""
    names = re.findall(r"(?<![\w$.])(" + NAME + r")(?![\w$])(?!\s*\()(?!\s+[A-Za-z_$])", mask[start:end])
    return [n for n in dict.fromkeys(names) if n not in KEYWORDS]


def resolve(code: str, mask: str, pairs: dict, span: tuple, path_: tuple, proper: int, seen: set,
            constants: dict, parent: Piece | None) -> list[Piece] | None:
    """A statement adding SQL: its own :names, and, read from it, an SQL constant or a builder's pieces."""
    start, end = span
    params = piece_params(code, mask, proper, end)
    if params is None:
        return None
    own = Piece(start, end, params, path_, proper, parent)
    # " WHERE " + (where.isEmpty() ? "" : where) keeps the pieces exact; any other ?: decides on its own
    own.gated = "?" in EMPTINESS.sub("", mask[proper:end])
    pieces = [own]
    for name in read_names(mask, proper, end):
        if name in seen:
            continue
        if name in constants:
            from .answer_checks import sql_parameters
            pieces.append(Piece(start, end, sql_parameters(constants[name]), path_, proper, own))
            continue
        if not name[0].islower():
            continue
        found = follow(code, mask, pairs, name, start, seen | {name}, constants, own)
        if found is None:
            return None
        pieces += found
    return pieces


def follow(code: str, mask: str, pairs: dict, name: str, until: int, seen: set, constants: dict,
           parent: Piece) -> list[Piece] | None:
    """The pieces of SQL built in a variable before `until`: its declaration, its appends/adds/+=. A variable of
    another type, or one without :names, adds none; None when it holds :names but cannot be followed (reset)."""
    own = re.escape(name)
    declarations = list(re.finditer(r"(?<![\w$.])(?:final\s+)?(?:" + BUILDERS + r")\s+" + own + r"\s*=(?!=)", mask[:until]))
    if not declarations:
        return []
    declaration = declarations[-1]
    end = statement_end(mask, declaration.end())
    first = resolve(code, mask, pairs, (statement_start(mask, declaration.start()), end),
                    path(mask, pairs, declaration.start()), declaration.start(), seen, constants, parent)
    pieces, reset, sql = first or [], first is None, first is None
    done = {end}
    for use in re.finditer(r"(?<![\w$.])" + own + r"(?![\w$])", mask[:until]):
        if use.start() <= end:
            continue
        after = mask[use.end():until]
        start = statement_start(mask, use.start())
        stop = statement_end(mask, start)
        if stop in done:
            continue
        if re.match(r"\s*\.\s*(?:" + ADDS + r")\s*\(", after) or re.match(r"\s*\+=", after) \
                or re.match(r"\s*=\s*" + own + r"\s*\+", after):
            done.add(stop)
            found = resolve(code, mask, pairs, (start, stop), path(mask, pairs, use.start()),
                            controls(mask, start, use.start())[1], seen, constants, parent)
            reset |= found is None
            sql |= found is None
            pieces += found or []
        elif re.match(r"\s*(?:=(?!=)|\.\s*(?:" + RESETS + r")\s*\()", after):
            reset = True
    sql |= any(p.params for p in pieces)
    if reset:
        # a value (String kod = kod.trim()) holds no SQL and does not matter; a reset SQL builder is not followed
        return None if sql else []
    return pieces


# --- the queries --------------------------------------------------------------------------------------------------

def split_comma(mask: str) -> int | None:
    depth = 0
    for index, char in enumerate(mask):
        depth += char in "([{"
        depth -= char in ")]}"
        if char == "," and depth == 0:
            return index
    return None


def receiver_start(mask: str, position: int) -> int:
    """Where the expression a method is called on starts (this.entityManager. / getEntityManager(). / em.)."""
    index = position
    while True:
        before = len(mask[:index].rstrip())
        if before == 0 or mask[before - 1] != ".":
            return index
        index = len(mask[:before - 1].rstrip())
        if index and mask[index - 1] == ")":
            depth = 0
            while index > 0:
                index -= 1
                depth += mask[index] == ")"
                depth -= mask[index] == "("
                if depth == 0:
                    break
        name = re.search(NAME + r"$", mask[:index])
        if not name:
            return position
        index = name.start()


def bind_calls(code: str, mask: str, position: int, path_: tuple, kind: str) -> tuple[list, int, bool]:
    """The setParameter calls chained from a position: (binds, end of the chain, one has a computed name)."""
    from .answer_checks import LITERAL
    found, index, computed = [], position, False
    while True:
        match = re.compile(r"\s*\.\s*(" + NAME + r")\s*\(").match(mask, index)
        if not match:
            return found, index, computed
        close = closing(mask, match.end() - 1)
        if close < 0:
            return found, index, computed
        if match.group(1) == "setParameter":
            comma = split_comma(mask[match.end():close])
            literal = LITERAL.fullmatch(code[match.end():match.end() + comma].strip()) if comma is not None else None
            if literal:
                found.append(Bind(literal.group(1), code[match.end() + comma + 1:close].strip(), kind,
                                  (match.start(), close + 1), match.start(), path_))
            else:
                computed = True
        index = close + 1


def is_map(mask: str, name: str) -> bool:
    own = re.escape(name)
    return bool(re.search(r"(?<![\w$.])(?:final\s+)?(?:[\w.]*Map\s*<[^;=(){}]*>\s+" + own + r"\s*=|var\s+" + own
                          + r"\s*=\s*new\s+[\w.]*Map\b)", mask))


def map_value_type(mask: str, name: str) -> str:
    own = re.escape(name)
    typed = re.search(r"Map\s*<\s*[\w.]+\s*,\s*([\w.<>?, ]+?)\s*>\s+" + own + r"\s*=", mask)
    if typed:
        return typed.group(1)
    created = re.search(r"var\s+" + own + r"\s*=\s*new\s+[\w.]*Map\s*<\s*(?:[\w.]+\s*,\s*([\w.<>?, ]+?)\s*)?>", mask)
    return (created.group(1) or "Object") if created else ""


def queries(code: str, constants: dict | None = None) -> list[Query]:
    from .answer_checks import CREATE, LITERAL
    constants = constants or {}
    mask = java_mask(code)
    pairs = braces(mask)
    found = []
    creations = list(CREATE.finditer(mask))
    for number, match in enumerate(creations):
        opening = match.end() - 1
        close = closing(mask, opening)
        if close < 0:
            continue
        start = statement_start(mask, match.start())
        end = statement_end(mask, start)
        receiver = receiver_start(mask, match.start())
        assigned = re.search(r"(?<![\w$.])(?:(?:final\s+)?" + TYPE + r"\s+)?(" + NAME + r")\s*=\s*(?:\([\w.<>?\[\], ]+\)\s*)?$",
                             mask[start:receiver])
        executed = TERMINAL.search(mask, close + 1, end)
        variable = assigned.group(1) if assigned and not executed and assigned.group(1) not in KEYWORDS else None
        query = Query((receiver, close), opening, (start, end), path(mask, pairs, match.start()), variable)
        chain_binds, _, query.dynamic = bind_calls(code, mask, close + 1, query.path, "chain")
        query.binds += chain_binds
        if executed:
            query.executions.append((executed.start(), query.path))
        comma = split_comma(mask[opening + 1:close])
        argument_end = opening + 1 + comma if comma is not None else close
        pieces = resolve(code, mask, pairs, (start, argument_end), query.path, opening + 1, set(), constants, None)
        if pieces is None:
            query.followed = False
        else:
            query.argument, query.pieces = pieces[0], pieces
        if variable:
            own = re.escape(variable)
            later = [m for m in creations[number + 1:]
                     if re.search(r"(?<![\w$.])" + own + r"\s*=[^=]", mask[statement_start(mask, m.start()):m.start()])]
            until = later[0].start() if later else len(mask)
            for use in re.finditer(r"(?<![\w$.])" + own + r"(?![\w$])", mask[end:until]):
                position = end + use.start()
                statement = statement_start(mask, position)
                stop = statement_end(mask, statement)
                after = mask[position + len(variable):]
                computed = bool(re.match(r"\s*::\s*setParameter", after))
                if re.match(r"\s*\.", after):
                    calls, last, computed = bind_calls(code, mask, position + len(variable), path(mask, pairs, position), "call")
                    bare = (not mask[last:stop].strip()
                            and skip_space(mask, controls(mask, statement, position)[1]) == position)
                    group = removal(code, mask, pairs, statement, stop) if bare else None
                    for bind_ in calls:
                        bind_.group = group
                        if bare:
                            bind_.remove = group if len(calls) == 1 else (bind_.remove if group else None)
                    query.binds += calls
                    terminal = TERMINAL.search(mask, position, stop)
                    if terminal:
                        query.executions.append((terminal.start(), path(mask, pairs, position)))
                elif not computed and not re.match(r"\s*=[^=]", after):
                    query.dynamic = True  # returned, passed on: bound or run elsewhere
                if computed:
                    # params.forEach(q::setParameter), (k, v) -> q.setParameter(k, v), a loop over params.entrySet()
                    context = mask[statement:stop + 1]
                    block = innermost(pairs, position)
                    if block > end:
                        context += " " + header(mask, pairs, block)
                    maps = [n for n in read_names(context, 0, len(context)) if n != variable and is_map(mask, n)]
                    query.maps += [(n, position, path(mask, pairs, position)) for n in maps]
                    query.dynamic |= not maps
        for name in dict.fromkeys(name for name, _, _ in query.maps):
            for put in re.finditer(r"(?<![\w$.])" + re.escape(name) + r"\s*\.\s*put\s*\(", mask):
                put_close = closing(mask, put.end() - 1)
                comma = split_comma(mask[put.end():put_close])
                literal = LITERAL.fullmatch(code[put.end():put.end() + comma].strip()) if comma is not None else None
                if not literal:
                    query.dynamic = True
                    continue
                statement = statement_start(mask, put.start())
                stop = statement_end(mask, statement)
                bare = (skip_space(mask, controls(mask, statement, put.start())[1]) == put.start()
                        and not mask[put_close + 1:stop].strip())
                value_start = skip_space(mask, put.end() + comma + 1)
                value_end = len(mask[:put_close].rstrip())
                query.binds.append(Bind(literal.group(1), code[value_start:value_end], "put",
                                        removal(code, mask, pairs, statement, stop) if bare else None, put.start(),
                                        path(mask, pairs, put.start()), value_span=(value_start, value_end), target=name))
        found.append(query)
    return found


# --- binding ------------------------------------------------------------------------------------------------------

def declared(mask: str, name: str) -> list[int]:
    return [m.start() for m in re.finditer(r"(?<![\w$.])(?:final\s+)?" + TYPE + r"\s+" + re.escape(name)
                                           + r"\s*(?=[=;:,)])", mask)]


def in_scope(mask: str, pairs: dict, expression: str, position: int) -> bool:
    """Every local variable the expression reads is declared before the position, in a block around it."""
    for name in set(read_names(java_mask(expression), 0, len(expression))):
        if not name[0].islower():
            continue
        found = declared(mask, name)
        if found and not any(d < position <= pairs.get(innermost(pairs, d), len(mask)) for d in found):
            return False
    return True


def candidates(name: str, query: Query, mask: str, ctx, preferred: list) -> list[str]:
    """Where a :name's value can come from, best first: the model's own binds, the variable of that name (or of the
    same name in another spelling), a method parameter, the java-variables.json entry."""
    parameters = set(getattr(ctx, "parameters", None) or {})
    found = list(preferred) + [b.value for b in query.binds if b.name == name]
    if declared(mask, name) or name in parameters:
        found.append(name)
    else:
        plain = name.replace("_", "").lower()
        names = {m.group(1) for m in re.finditer(r"(?<![\w$.])(?:final\s+)?" + TYPE + r"\s+(" + NAME + r")\s*(?=[=;:,)])", mask)}
        same = [n for n in names | parameters if n.replace("_", "").lower() == plain]
        if len(same) == 1:
            found.append(same[0])
    entry = (getattr(ctx, "variables", None) or {}).get(name)
    if entry and entry.get("value"):
        found.append(entry["value"])
    return list(dict.fromkeys(v for v in found if v))


def wrapped(value: str, value_type: str) -> str:
    from .answer_checks import LITERAL, NEVER_NULL
    if value_type != "Object" or NEVER_NULL.fullmatch(value) or LITERAL.fullmatch(value):
        return value
    return "SqlValues.bind(" + value + ")"


def free_name(mask: str, wanted: str) -> str:
    name, number = wanted, 2
    while re.search(r"(?<![\w$.])" + re.escape(name) + r"(?![\w$])", mask):
        name, number = wanted + str(number), number + 1
    return name


def present(piece: Piece) -> bool:
    """In the statement whenever the query runs."""
    return piece.parent is None or (present(piece.parent) and not piece.parent.gated and prefix(piece.path, piece.parent.path))


def exact(piece: Piece) -> bool:
    """In the statement exactly when its own statement runs: a value put next to it is bound exactly then."""
    if piece.parent is None:
        return True
    return not piece.parent.gated and (present(piece.parent) or (prefix(piece.parent.path, piece.path) and exact(piece.parent)))


def effective(bind_: Bind, query: Query) -> bool:
    """A bind that has run whenever the query runs."""
    if bind_.kind == "chain":
        return True
    if bind_.kind == "put":
        return prefix(bind_.path, query.path) and any(prefix(bind_.path, where) for _, _, where in query.maps)
    if not query.executions:
        return prefix(bind_.path, query.path)
    return all(bind_.position < at and prefix(bind_.path, where) for at, where in query.executions)


def covered(binds: list, query: Query) -> bool:
    """Binds on every branch of an if / else if / else chain (ending in else): together they always run."""
    reach = [where for _, where in query.executions] or [query.path]
    paths = {b.path for b in binds if b.kind == "call" and all(b.position < at for at, _ in query.executions)}
    for found in paths:
        if not found or not all(prefix(found[:-1], where) for where in reach):
            continue
        base, last = found[:-1], found[-1]
        if last == "else" and any(p[:-1] == base and p[-1].startswith("if ") for p in paths):
            return True  # brace-less: if (...) q.setParameter(...); else q.setParameter(...);
        parts = last.split(" of ")
        if parts[0] == "else" and len(parts) > 1 and all(base + (" of ".join(parts[i:]),) in paths for i in range(len(parts))):
            return True
    return False


def bind(code: str, ctx, notes: list, warnings: list) -> str:
    """Every :name of every native query bound where its piece is (see the module); one query at a time."""
    constants = {c["name"]: c["sql"] for c in getattr(ctx, "sql_constants", None) or []}
    index, fixed, moved, typed = 0, [], [], False
    while True:
        found = queries(code, constants)
        if index >= len(found):
            break
        mask = java_mask(code)
        edits, done, shifted, unsure = plan(code, mask, braces(mask), found[index], ctx)
        index += 1
        for name in unsure:
            warnings.append(f"A(z) :{name} SQL-paraméter kötése nem biztos, hogy minden ágon helyes (feltételes "
                            "SQL-darab vagy feltételes setParameter); ellenőrizd.")
        ordered = sorted(edits, key=lambda e: (e[0], e[1]))
        if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])):
            warnings.append("Egy natív lekérdezés paramétereinek kötését nem lehetett biztonságosan rendezni; "
                            "ellenőrizd a setParameter-hívásokat.")
            continue
        for start, end, text in reversed(ordered):
            code = code[:start] + text + code[end:]
        if any(start != end and not text for start, end, text in edits):
            code = drop_empty_ifs(code)
        typed |= any(text.startswith("SqlValues.bind(") and start != end for start, end, text in edits)
        fixed += done
        moved += shifted
    if fixed:
        notes.append("Helyben kötve az SQL paraméterei ott, ahol a darabjuk az SQL-be kerül: "
                     + ", ".join(":" + n for n in dict.fromkeys(fixed)) + " (setParameter hiányzott).")
    if moved:
        notes.append("Helyben áthelyezve a kötés oda, ahol a paraméter SQL-darabja az SQL-be kerül: "
                     + ", ".join(":" + n for n in dict.fromkeys(moved))
                     + " (feltételes darab mindig kötött értékkel vagy fordítva: futáskor hibát adott volna).")
    if typed:
        notes.append("Helyben javítva: a paramétertérkép értékei SqlValues.bind(...)-on át kerülnek a lekérdezésbe "
                     "(a null érték így típusos NULL az Oracle-nek).")
    return code


def plan(code: str, mask: str, pairs: dict, query: Query, ctx) -> tuple[list, list, list, list]:
    """The edits (start, end, text) that bind the query's :names right; the names fixed, moved and left unsure."""
    if not query.followed or not query.pieces:
        return [], [], [], []
    names = list(dict.fromkeys(n for piece in query.pieces for n in piece.params))
    fixed, moved, unsure, chain, puts, removed = [], [], [], [], {}, []
    target_map = next((name for name, _, _ in query.maps if map_value_type(mask, name)), None)
    new_map = None
    for name in names:
        pieces = [p for p in query.pieces if name in p.params]
        binds = [b for b in query.binds if b.name == name]
        movable = all(b.remove is not None or b.group is not None for b in binds)
        if any(present(p) for p in pieces):
            if any(effective(b, query) for b in binds) or covered(binds, query):
                continue
            values = list(dict.fromkeys(" ".join(b.value.split()) for b in binds))
            value = next((v for v in candidates(name, query, mask, ctx, values)
                          if in_scope(mask, pairs, v, query.statement[0])), None)
            if len(values) > 1 or value is None or not movable:
                # bound in if/else branches with other values, or no value in reach: left as written
                if binds:
                    unsure.append(name)
                continue
            removed += binds
            chain.append('.setParameter("' + name + '", ' + value + ')')
        else:
            if binds and {b.path for b in binds} == {p.path for p in pieces} and all(b.kind != "chain" for b in binds) \
                    and all(b.kind == "put" or all(b.position < at for at, _ in query.executions) for b in binds):
                continue
            if not all(exact(p) for p in pieces) or not movable:
                if binds:
                    unsure.append(name)
                continue
            plans = []
            for piece in pieces:
                own = [b.value for b in binds if b.path == piece.path]
                value = next((v for v in candidates(name, query, mask, ctx, own)
                              if in_scope(mask, pairs, v, piece.end)), None)
                if value is None:
                    break
                plans.append((piece, value))
            if len(plans) < len(pieces):
                if binds:
                    unsure.append(name)
                continue
            removed += binds
            if target_map and not all(in_scope(mask, pairs, target_map, p.end) for p in pieces):
                target_map = None  # the model's map is declared after the piece: a map of our own
            if target_map is None and new_map is None:
                new_map = free_name(mask, "sqlParams")
            map_name = target_map or new_map
            value_type = map_value_type(mask, target_map) if target_map else "Object"
            for piece, value in plans:
                puts.setdefault(piece, []).append(map_name + '.put("' + name + '", ' + wrapped(value, value_type) + ');')
        (moved if binds else fixed).append(name)
    edits = []
    # a removed bind: its whole statement when all of its setParameter calls go, else the call alone
    gone = {}
    for b in removed:
        if b.group is not None:
            gone.setdefault(b.group, []).append(b)
    whole = {g for g, members in gone.items() if len(members) == sum(1 for x in query.binds if x.group == g)}
    edits += [(g[0], g[1], "") for g in whole]
    for b in removed:
        if b.group not in whole:
            if b.remove is None:
                return [], [], [], names  # a branch of an if/else that cannot go alone
            edits.append((b.remove[0], b.remove[1], ""))
    # the model's own map values: a null is sent as a typed NULL, like setParameter's (answer_checks.bind)
    for b in query.binds:
        if b.kind == "put" and b not in removed and b.value_span and map_value_type(mask, b.target) == "Object":
            text = wrapped(b.value, "Object")
            if text != b.value:
                edits.append((b.value_span[0], b.value_span[1], text))
    if chain:
        edits.append((query.call[1] + 1, query.call[1] + 1, "".join(chain)))
    for piece, lines in puts.items():
        head = CONTROL.match(mask, piece.start)
        if head and head.start(1) < piece.proper:
            # a brace-less if (...) piece: braces around the piece and its value
            begin = head.start(1)
            indent = indent_at(code, begin)
            inner = indent + "    "
            stop = skip_space(mask, piece.end + 1)
            follows = re.match(r"else\b", mask[stop:])  # } else on one line (Checkstyle RightCurly)
            edits.append((begin, stop if follows else piece.end + 1, code[begin:piece.proper].rstrip() + " {\n" + inner
                          + code[piece.proper:piece.end + 1] + "".join("\n" + inner + line for line in lines)
                          + "\n" + indent + "}" + (" " if follows else "")))
        else:
            indent = indent_at(code, piece.proper)
            edits.append((piece.end + 1, piece.end + 1, "".join("\n" + indent + line for line in lines)))
    if new_map:
        place = declaration_place(code, mask, pairs, query, list(puts))
        if place is None:
            return [], [], [], names
        position, indent = place
        edits.append((position, position, "Map<String, Object> " + new_map + " = new LinkedHashMap<>();\n" + indent))
        start = skip_space(mask, query.statement[0])
        if query.variable:
            end = query.statement[1]
            edits.append((end + 1, end + 1, "\n" + indent_at(code, start) + new_map + ".forEach("
                          + query.variable + "::setParameter);"))
        else:
            if "->" in mask[start:query.call[0]] or controls(mask, query.statement[0], query.call[0])[0]:
                return [], [], [], names  # in a lambda expression or under a brace-less if: not split here
            variable = free_name(mask, "sqlQuery")
            indent = indent_at(code, start)
            edits.append((start, start, "Query " + variable + " = " + code[query.call[0]:query.call[1] + 1] + ";\n"
                          + indent + new_map + ".forEach(" + variable + "::setParameter);\n" + indent))
            edits.append((query.call[0], query.call[1] + 1, variable))
    return edits, fixed, moved, unsure


def declaration_place(code: str, mask: str, pairs: dict, query: Query, pieces: list) -> tuple[int, str] | None:
    """Where a new parameter map is declared: in the block holding every piece that puts into it and the query, right
    after the builder's declaration when that is there and before them, else first in that block."""
    positions = [skip_space(mask, p.start) for p in pieces] + [skip_space(mask, query.statement[0])]
    blocks = [o for o in sorted(pairs, reverse=True) if all(o < p <= pairs[o] for p in positions)]
    block = blocks[0] if blocks else -1
    first = min(positions)
    for piece in query.pieces:
        start = skip_space(mask, piece.start)
        if piece is query.argument or piece.parent is not query.argument or piece.end >= first \
                or innermost(pairs, start) != block or not re.match(r"(?:final\s+)?(?:" + BUILDERS + r")\s", mask[start:]):
            continue
        line_end = code.find("\n", piece.end)
        if line_end != -1 and not code[piece.end + 1:line_end].strip():
            return skip_space(code, line_end + 1), indent_at(code, start)
    if block < 0:
        return None
    start = skip_space(code, block + 1)
    return start, indent_at(code, start)


def problems(code: str, ctx) -> list[tuple[str, str]]:
    """(Hungarian, English) of every :name of a statement built at run time that nothing binds after the repairs."""
    from .answer_checks import native_queries
    constants = {c["name"]: c["sql"] for c in getattr(ctx, "sql_constants", None) or []}
    written = {q.span[0] for q in native_queries(code, constants) if q.sql is not None}
    found = []
    for query in queries(code, constants):
        if query.dynamic or not query.followed or query.opening + 1 in written:
            continue  # bound elsewhere, not followed, or a written statement (answer_checks names its parameters)
        for name in dict.fromkeys(n for piece in query.pieces for n in piece.params):
            if any(b.name == name for b in query.binds):
                continue
            piece = next(p for p in query.pieces if name in p.params)
            text = " ".join(code[piece.proper:piece.end].split())[:120]
            found.append((f"A futáskor összerakott SQL :{name} paramétere nem kap értéket (`{text}`).",
                          f"The SQL piece `{text}` has :{name}, but nothing gives it a value. Declare the value as a "
                          f"variable named {name} before that piece (it is then bound), or bind it where the piece "
                          "is appended."))
    return found


# --- unused locals ------------------------------------------------------------------------------------------------

PURE = re.compile(r"\s*(?:\"(?:\\.|[^\"\\])*\"|-?\d[\w.]*|null|true|false|" + NAME + r"|"
                  r"(?:PlsqlValues\s*\.\s*(?:text|number|datetime|date)|SqlValues\s*\.\s*(?:text|number|dateTime|bind))"
                  r"\s*\([^;{}]*\)|request\s*\.\s*(?:get)?(?:[Bb]locks|[Pp]arameters)\s*\(\s*\)(?:\s*\.\s*get\s*\([^;{}]*\))*)\s*")


def unused(code: str, notes: list, ctx=None) -> str:
    """A local variable nothing reads, whose value only reads the screen or a literal: PMD reports it and it changes
    nothing, so it goes (a note names it). Not one named like a parameter of the SQL: that one is missing its bind
    (problems asks for it)."""
    constants = {c["name"]: c["sql"] for c in getattr(ctx, "sql_constants", None) or []}
    wanted = {n for q in queries(code, constants) for p in q.pieces for n in p.params}
    removed = []
    for _ in range(20):
        mask = java_mask(code)
        pairs = braces(mask)
        target = None
        for match in re.finditer(r"(?m)^[ \t]*(?:final\s+)?" + TYPE + r"\s+(" + NAME + r")\s*=(?!=)", mask):
            name = match.group(1)
            if name in KEYWORDS or name in wanted:
                continue
            end = statement_end(mask, match.end())
            if re.search(r"(?<![\w$.])" + re.escape(name) + r"(?![\w$])", mask[:match.start()] + " " + mask[end:]) \
                    or not PURE.fullmatch(code[match.end():end]):
                continue
            span = removal(code, mask, pairs, statement_start(mask, match.start()), end)
            if span:
                target = (name, span)
                break
        if target is None:
            break
        name, (start, stop) = target
        code = code[:start] + code[stop:]
        removed.append(name)
    if removed:
        notes.append("Helyben törölve a használatlan változó (PMD UnusedLocalVariable): " + ", ".join(removed)
                     + "; csak képernyőértéket vagy állandót olvasott.")
    return code

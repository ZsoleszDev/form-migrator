"""What javac cannot see in an AI-written service method: its native queries (data access "service").

The model runs the Forms SQL through the EntityManager. A method that compiles can still be wrong without anybody
noticing until a user presses the button: a parameter of the SQL that is never set, a setParameter for a name the SQL
does not have, a statement of the task that was left out or altered, a trigger hook that is not called. These are
checked here against the method's own SQL and - for a data endpoint or a LOV - against the task's contract (the SQL and
the hooks its hints name), so the model gets a precise correction instead of the developer a runtime error.

One slip is repaired instead of reported (bind): a null bound to a native query has no SQL type, and Hibernate 5 sends
it as binary - Oracle then refuses it for a NUMBER or DATE column (ORA-00932). SqlValues.bind sends '' instead, which
Oracle reads as NULL of a type it converts. Stored procedure parameters are typed and stay as written.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from .java_scan import java_mask
from .reply_repair import closing_paren

LITERAL = re.compile(r'"((?:\\.|[^"\\\n])*)"')
CODE_TOKEN = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'')
CREATE = re.compile(r"\bcreateNativeQuery\s*\(")
SET = re.compile(r"\.\s*setParameter\s*\(")
ASSIGNED = re.compile(r"([A-Za-z_$][\w$]*)\s*=\s*(?:\(\s*[\w.<>\[\], ]+\)\s*)?(?:this\s*\.\s*)?[A-Za-z_$][\w$]*\s*\.\s*$")
SQL_PARAMETER = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")
SQL_STRING = re.compile(r"'(?:[^']|'')*'")
TERMINAL = re.compile(r"\.\s*(?:getResultList|getSingleResult|executeUpdate|getResultStream)\s*\(")
# Values that are never null: no bind() around them.
NEVER_NULL = re.compile("|".join((
    r"-?\d[\w.]*", "true", "false", "offset", "limit",                    # a number, a flag, the paging arguments
    r"[\w.()\s]+[+\-*]\s*\d+",                                          # limit + 1
    r"[\w.]+\s*\.\s*(?:offset|limit|getOffset|getLimit)\s*\(\s*\)",       # request.limit(), the company's getLimit()
    r"LovQuery\s*\.\s*limit\s*\(.*\)", r"SqlValues\s*\.\s*bind\s*\(.*\)")), re.S)


@dataclass
class Parameter:
    name: str
    start: int   # the value expression in the code
    end: int


@dataclass
class NativeQuery:
    sql: str | None                 # the statement when it is written as string literals only; None: built at run time
    parameters: list = field(default_factory=list)
    complete: bool = True           # False: the query object leaves the statement (returned, passed on): not judged
    span: tuple | None = None       # the createNativeQuery argument in the code
    constant: str | None = None     # the argument is one of the task's SQL constants (frame_sql)
    variable: str | None = None     # the argument is a local String holding string literals: String sql = "...";
    literal: bool = False           # the statement is written in the code (string literals), not taken from elsewhere


def text_of(literal: str) -> str:
    """The value of a Java string literal body, as far as SQL needs it."""
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", "r": "\r"}.get(m.group(1), m.group(1)), literal)


def statement_end(mask: str, start: int) -> int:
    depth = 0
    for pos in range(start, len(mask)):
        char = mask[pos]
        if char in "({[":
            depth += 1
        elif char in ")}]":
            depth -= 1
            if depth < 0:
                return pos
        elif char == ";" and depth == 0:
            return pos
    return len(mask)


def set_calls(code: str, mask: str, start: int, end: int) -> list[Parameter]:
    """The setParameter("name", value) calls between two positions."""
    found = []
    for match in SET.finditer(mask, start, end):
        opening = match.end() - 1
        closing = closing_paren(mask, opening)
        if closing < 0 or closing > end:
            continue
        depth, comma = 0, -1
        for pos in range(opening + 1, closing):
            if mask[pos] in "({[":
                depth += 1
            elif mask[pos] in ")}]":
                depth -= 1
            elif mask[pos] == "," and depth == 0:
                comma = pos
                break
        name = LITERAL.fullmatch(code[opening + 1:comma].strip()) if comma > 0 else None
        if name:
            found.append(Parameter(text_of(name.group(1)), comma + 1, closing))
    return found


def literal_text(code: str, mask: str, start: int, end: int) -> str | None:
    """The value of an expression made of string literals only ("..." + "..."), else None."""
    pieces = [text_of(m.group(1)) for m in LITERAL.finditer(code, start, end)]
    return "".join(pieces) if pieces and not mask[start:end].replace("+", "").strip() else None


def native_queries(code: str, constants: dict | None = None) -> list[NativeQuery]:
    """The createNativeQuery calls of the code with their SQL and the parameters set on them. constants: the task's
    SQL constants by name (frame_sql), whose statement the argument may name; a local String sql = "..."; counts too."""
    mask = java_mask(code)
    calls = [(m, ASSIGNED.search(mask[:m.start()])) for m in CREATE.finditer(mask)]
    queries = []
    for index, (match, variable) in enumerate(calls):
        opening = match.end() - 1
        closing = closing_paren(mask, opening)
        if closing < 0:
            continue
        # string literals only (the mask blanks them): the statement is known here; anything else is built at run time
        sql = literal_text(code, mask, opening + 1, closing)
        argument = mask[opening + 1:closing].strip()
        constant = local = None
        if sql is None and re.fullmatch(r"(?:this\s*\.\s*)?[A-Za-z_$][\w$]*", argument):
            name = argument.split(".")[-1].strip()
            if constants and name in constants:
                sql, constant = constants[name], name
            else:
                declared = list(re.finditer(r"(?<![\w$.])(?:final\s+)?(?:String|var)\s+" + re.escape(name) + r"\s*=", mask[:match.start()]))
                # assigned again (sql += " AND ..."): the declaration is not the statement that runs
                changed = len(re.findall(r"(?<![\w$.])" + re.escape(name) + r"\s*(?:\+=|=(?!=))", mask)) > 1
                if declared and not changed:
                    value_end = statement_end(mask, declared[-1].end())
                    sql = literal_text(code, mask, declared[-1].end(), value_end)
                    local = name if sql is not None else None
        end = statement_end(mask, closing + 1)
        query = NativeQuery(sql, set_calls(code, mask, closing + 1, end), span=(opening + 1, closing),
                            constant=constant, variable=local, literal=sql is not None and constant is None)
        if not TERMINAL.search(mask, closing + 1, end):
            if variable:
                # Query q = entityManager.createNativeQuery(...); q.setParameter(...); ... q.getResultList();
                name = variable.group(1)
                until = next((m.start() for m, v in calls[index + 1:] if v and v.group(1) == name), len(mask))
                for use in re.finditer(r"(?<![\w$.])" + re.escape(name) + r"\s*\.", mask[:until]):
                    if use.start() > end:
                        query.parameters += set_calls(code, mask, use.start(), statement_end(mask, use.start()))
            else:
                query.complete = False  # the query object is handed on, not run here
        queries.append(query)
    return queries


def sql_parameters(sql: str) -> list[str]:
    """The :names Oracle sees: not in string literals, not in comments."""
    from .frame_sql import strip_comments
    return list(dict.fromkeys(SQL_PARAMETER.findall(SQL_STRING.sub("''", strip_comments(sql)))))


def bind(code: str, notes: list) -> str:
    """setParameter("name", value) of a native query -> setParameter("name", SqlValues.bind(value)), except values
    that cannot be null (numbers, offset/limit)."""
    edits = []
    for query in native_queries(code):
        for parameter in query.parameters:
            value = code[parameter.start:parameter.end]
            if not NEVER_NULL.fullmatch(value.strip()) and not LITERAL.fullmatch(value.strip()):
                lead = len(value) - len(value.lstrip())
                edits.append((parameter.start + lead, parameter.end, "SqlValues.bind(" + value.strip() + ")"))
    for start, end, text in sorted(set(edits), reverse=True):
        code = code[:start] + text + code[end:]
    if edits:
        notes.append("Helyben javítva: a natív lekérdezések paraméterei SqlValues.bind(...)-on át kötődnek "
                     "(a null érték így típusos NULL az Oracle-nek).")
    return code


def literal_runs(code: str, mask: str) -> list[str]:
    """Every expression of string literals joined by + ("SELECT ..." + "FROM ..."), and all of them in order: a
    statement assembled in parts (StringBuilder.append) is found too."""
    runs, current, last = [], [], None
    for match in CODE_TOKEN.finditer(code):
        if not match.group().startswith('"'):
            continue  # a comment or a char literal
        if last is not None and mask[last:match.start()].replace("+", "").strip():
            runs.append("".join(current)); current = []
        current.append(text_of(match.group()[1:-1]))
        last = match.end()
    if current:
        runs.append("".join(current))
    return runs + (["".join(runs)] if len(runs) > 1 else [])


def problems(code: str, contract: dict | None = None, constants: list | None = None) -> list[tuple[str, str]]:
    """(Hungarian for the developer, English for the model) of everything that would fail or differ at run time.

    constants: the task's SQL constants (frame_sql.statements): a statement of the contract is there when the code
    runs its constant, or when Oracle reads the code's SQL as that statement (token by token, comments removed)."""
    from . import frame_sql
    found = []
    constants = constants or []
    queries = native_queries(code, {c["name"]: c["sql"] for c in constants})
    for query in queries:
        if query.sql is None:
            continue
        lost = frame_sql.swallowed(query.sql) if query.literal else None
        if lost:
            found.append(("Az SQL egysoros szövegében a -- megjegyzés az utána következő SQL-t is kikommentezi: "
                          + lost[:90],
                          "The SQL string has a -- comment with SQL after it on the same line (" + lost[:90] + "): Oracle "
                          "ignores everything after --. Remove the comment, or end it with \\n."))
        if not query.complete:
            continue
        expected, given = sql_parameters(query.sql), [p.name for p in query.parameters]
        short = " ".join(query.sql.split())[:90]
        missing = [name for name in expected if name not in given]
        extra = [name for name in dict.fromkeys(given) if name not in expected]
        if missing:
            found.append(("Az SQL paramétere nem kap értéket (" + ", ".join(":" + n for n in missing) + "): " + short,
                          "The query `" + short + "` has no setParameter for " + ", ".join('"' + n + '"' for n in missing)
                          + ". Set every :name of the SQL."))
        if extra:
            found.append(("A setParameter olyan nevet állít be, amely nincs az SQL-ben (" + ", ".join(extra) + "): " + short,
                          "The query `" + short + "` has no parameter " + ", ".join(":" + n for n in extra)
                          + ", but setParameter is called for it. Use the parameter names of the SQL."))
    if not contract:
        return found
    mask = java_mask(code)
    runs = literal_runs(code, mask)
    by_required = {c["required"]: c for c in constants}
    for statement in contract.get("sql", []):
        constant = by_required.get(statement)
        if constant and re.search(r"(?<![\w$.])" + re.escape(constant["name"]) + r"(?![\w$])", mask):
            continue
        wanted = frame_sql.tokens(statement)
        executed = [frame_sql.tokens(q.sql) for q in queries if q.sql is not None] + [frame_sql.tokens(run) for run in runs]
        if any(frame_sql.contains(have, wanted) for have in executed):
            continue
        short = " ".join(statement.split())[:160]
        if constant:
            found.append(("A feladat SQL-utasítása hiányzik vagy megváltozott (a " + constant["name"] + " konstanst kell "
                          "futtatni): " + short,
                          "Run the SQL of the comment through its constant, do not write that SQL yourself: "
                          "entityManager.createNativeQuery(" + constant["name"] + ")."))
        else:
            found.append(("A feladat SQL-utasítása hiányzik vagy megváltozott: " + short,
                          "Run this SQL of the comment exactly as written (same columns, conditions and parameter "
                          "names): " + " ".join(statement.split())))
    position = -1
    for call in contract.get("calls", []):
        match = re.search(r"(?<![\w$.])" + re.escape(call) + r"\s*\(", mask)
        if not match:
            found.append(("A metódus nem hívja meg a(z) " + call + " triggert.",
                          "Call " + call + "(...) as the comment says; the method exists."))
        elif match.start() < position:
            found.append(("A triggerek hívási sorrendje eltér a Formsétól (" + call + ").",
                          "Call the trigger hooks in the order of the comment; " + call + " comes later."))
        else:
            position = match.start()
    return found

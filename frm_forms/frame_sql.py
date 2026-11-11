"""The SQL of a data endpoint or a LOV as constants of the service, and SQL compared as Oracle reads it.

The SQL of these methods is the migrator's own (ai_frames: the block query, the FOR UPDATE NOWAIT lock, the
INSERT/UPDATE/DELETE, the record group query of a LOV with its filter and limit), and the written method must run it
as it is (answer_checks: the endpoint's contract). A small model had to retype it from the comment, and a long
statement rarely came back letter by letter: the comment of a record group dropped (correct SQL, rejected), a line
break lost after a "--" comment (the rest of the statement commented out, but accepted: whitespace was ignored), an AS
before an alias, two lines joined without a space. Twice in a row, the task ended as a draft.

Now the statements are constants of the service (LOV_INPTIP_SQL, UPDATE_ORDERS_LOCK_SQL ...): the prompt names them and
the model runs them by name (fewer output tokens, nothing to mistype); the migrator declares the ones the method uses.
The comparison is token by token, with the comments removed as Oracle removes them: a statement Oracle reads as the
comment's passes however it is written; one that differs but is meant as it (the same parameters, nearly the same
tokens) runs the constant instead, without another model call.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import re

from .java_scan import java_mask

TOKEN = re.compile(r"'(?:[^']|'')*'?|\"[^\"]*\"?|:[^\W\d]\w*|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|[^\W\d][\w$#]*"
                   r"|<>|!=|\^=|<=|>=|\|\||=>|\S")
# AS before a type, not an alias: CAST(x AS NUMBER).
TYPED = {"CAST", "TREAT", "XMLCAST", "MULTISET", "XMLSERIALIZE"}
SIMILAR = 0.75  # retyped statements below this token similarity are not taken for the constant's


def strip_comments(sql: str) -> str:
    """The SQL without "--" (to the end of the line) and /* */ comments; string literals and identifiers stay."""
    out, i, n = [], 0, len(sql)
    while i < n:
        char = sql[i]
        if char in "'\"":
            end = sql.find(char, i + 1)
            while char == "'" and end != -1 and sql[end + 1:end + 2] == "'":
                end = sql.find("'", end + 2)
            end = n if end == -1 else end + 1
            out.append(sql[i:end]); i = end
        elif sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end == -1 else end
            out.append(" ")
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
        else:
            out.append(char); i += 1
    return "".join(out)


def tokens(sql: str, comments: bool = False) -> list[str]:
    """The statement as Oracle reads it (comments: False) or as written: words upper-case, literals as they are,
    an optional AS before an alias left out, != and ^= as <>."""
    text = sql if comments else strip_comments(sql)
    found, stack, previous = [], [], ""
    raw = TOKEN.findall(text)
    for index, token in enumerate(raw):
        word = token if token[0] in "'\"" else token.upper()
        if word in {"!=", "^="}:
            word = "<>"
        if word == "(":
            stack.append(previous)
        elif word == ")" and stack:
            stack.pop()
        following = raw[index + 1] if index + 1 < len(raw) else ""
        if (word == "AS" and following[:1] not in {"(", ""} and re.match(r"[^\W\d]|\"", following)
                and not (stack and stack[-1] in TYPED)):
            previous = word
            continue
        found.append(word)
        previous = word
    return found


def parameters(sql: str, comments: bool = False) -> list[str]:
    """The :names the statement really has (not in its comments - unless asked - or string literals), as written:
    setParameter names are case-sensitive."""
    text = sql if comments else strip_comments(sql)
    text = re.sub(r"'(?:[^']|'')*'", "''", text)
    return list(dict.fromkeys(re.findall(r"(?<![:\w]):([^\W\d]\w*)", text)))


def contains(haystack: list[str], needle: list[str]) -> bool:
    if not needle:
        return True
    first, size = needle[0], len(needle)
    return any(haystack[i] == first and haystack[i:i + size] == needle for i in range(len(haystack) - size + 1))


def similarity(a: list[str], b: list[str]) -> float:
    return SequenceMatcher(None, a, b, autojunk=False).ratio() if a or b else 1.0


def swallowed(sql: str) -> str | None:
    """A "--" comment on the last line of a statement written on one line that holds SQL: Oracle ignores it all
    ("... -- name FROM t WHERE x = :id"). The text of the swallowed part, else None."""
    text = sql.rstrip()
    stripped = strip_comments(text)
    if stripped == text or "\n" in text[text.rfind("--"):]:
        return None
    rest = text[text.rfind("--") + 2:]
    words = re.findall(r"\b(FROM|WHERE|AND|OR|ORDER|GROUP|SELECT|UNION|FETCH|JOIN|SET|VALUES|INTO)\b", rest, re.I)
    return rest.strip() if len(words) >= 2 or re.search(r"(?<![:\w]):[^\W\d]\w*", rest) else None


# --- the constants of a task --------------------------------------------------------------------------------------

def snake(method: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", method).upper()


def statements(packet: dict) -> list[dict]:
    """The SQL constants of a data endpoint or LOV task: {"name", "sql" (what the comment shows and the constant
    holds), "required" (the contract's statement, contained in it), "what"}; none for other tasks."""
    endpoint = packet.get("endpoint") or {}
    contract = endpoint.get("contract") or {}
    sql = [s for s in contract.get("sql") or [] if isinstance(s, str) and s.strip()]
    op, method = endpoint.get("operation"), packet.get("method") or endpoint.get("method")
    if not sql or not method or not re.fullmatch(r"[A-Za-z_$][\w$]*", method):
        return []
    base = snake(method)
    if op == "lov":
        # The record group query with the typed-text filter and the limit, as the comment shows it (backend_lovs).
        region = packet.get("existing_method") or ""
        if ") lov" not in region or "FETCH FIRST :limit ROWS ONLY" not in region:
            return []
        display = re.search(r"UPPER\(lov\.(\"[^\"]+\"|[^\W\d][\w$#]*)\) LIKE UPPER\(:term\)", region)
        from .backend_lovs import wrap
        return [{"name": base + "_SQL", "sql": wrap(sql[0], display.group(1) if display else None), "required": sql[0],
                 "what": "the record group query of the comment, with its filter and limit"}]
    if op in {"list", "search"}:
        return [{"name": base + "_SQL", "sql": sql[0] + " OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY",
                 "required": sql[0], "what": "the block query with its paging (:limit = limit + 1)"}]
    if op not in {"create", "update", "delete"}:
        return []
    result = []
    for statement in sql:
        lock = re.search(r"\bFOR\s+UPDATE\s+NOWAIT\s*$", statement, re.I) is not None
        kind = "the lock (SELECT ... FOR UPDATE NOWAIT)" if lock else "the " + statement.split(None, 1)[0].upper()
        result.append({"name": base + ("_LOCK" if lock else "") + "_SQL", "sql": statement, "required": statement,
                       "what": kind})
    return result


def prompt_lines(constants: list[dict]) -> list[str]:
    return ["- " + c["name"] + ": " + c["what"] + ("; parameters " + ", ".join(":" + p for p in parameters(c["sql"]))
                                                    if parameters(c["sql"]) else "") for c in constants]


# --- in the service class -----------------------------------------------------------------------------------------

def java_literal(sql: str, indent: str = "    ") -> str:
    """The statement as a Java string expression, line by line ("...\\n" + "..."), operator first on the next line."""
    lines = sql.split("\n")
    pieces = []
    for number, line in enumerate(lines):
        text = line.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t")
        pieces.append('"' + text + ("\\n" if number < len(lines) - 1 else "") + '"')
    return ("\n" + indent + "        + ").join(pieces)


MARKER = "// AI-feladat SQL"


def declare(source: str, constants: list[dict]) -> str:
    """Declare (or bring up to date) the constants the class uses: private static final String NAME = "...";
    right at the start of the class body, the migrator's comment above each."""
    for constant in constants:
        name = constant["name"]
        mask = java_mask(source)
        if not re.search(r"(?<![\w$.])" + re.escape(name) + r"(?![\w$])", mask):
            continue
        declared = re.search(r"\bstatic\s+final\s+String\s+" + re.escape(name) + r"\s*=", mask)
        value = java_literal(constant["sql"])
        if declared:
            end = mask.find(";", declared.end())
            if end < 0:
                continue
            source = source[:declared.end()] + " " + value + source[end:]
            continue
        body = re.search(r"\bclass\s+[A-Za-z_$][\w$]*[^{;]*\{", mask)
        if not body:
            continue
        at = body.end()
        # after the constants declared before (ours), else first in the class
        for previous in re.finditer(r"private\s+static\s+final\s+String\s+[A-Z][A-Z0-9_]*_SQL\s*=", mask):
            end = mask.find(";", previous.end())
            if end > at:
                at = end + 1
        line = ("\n    " + MARKER + " (" + name + "): a megjegyzés SQL-je; a migrátor tartja karban.\n"
                "    private static final String " + name + " = " + value + ";\n")
        source = source[:at] + line + source[at:]
    return source


def use_constants(code: str, constants: list[dict], notes: list, warnings: list, used: set | None = None) -> str:
    """A statement the model retyped wrong: its createNativeQuery runs the constant instead (the comment's SQL
    exactly), when it is meant as that statement - the same parameters, nearly the same tokens. A statement Oracle
    reads as the comment's (other line breaks, no comment, AS before an alias) stays as the model wrote it."""
    from .answer_checks import native_queries
    used = used if used is not None else set()
    mask = java_mask(code)
    for constant in constants:
        name = constant["name"]
        if name in used or re.search(r"(?<![\w$.])" + re.escape(name) + r"(?![\w$])", mask):
            used.add(name)
            continue
        want_executed, want_written = tokens(constant["sql"]), tokens(constant["sql"], comments=True)
        want_parameters = set(parameters(constant["sql"]))
        keyword = want_executed[:1]
        queries = native_queries(code, {c["name"]: c["sql"] for c in constants})
        required = tokens(constant["required"])
        if any(q.sql is not None and contains(tokens(q.sql), required) for q in queries):
            continue  # Oracle reads the model's SQL as the comment's statement: it stays as written
        best = None
        for query in queries:
            if query.sql is None or query.constant or query.span is None:
                continue
            executed = tokens(query.sql)
            # the written statement's parameters: a "--" on one line may have swallowed some of them
            written_parameters = set(parameters(query.sql, comments=True))
            if executed[:1] != keyword and tokens(query.sql, comments=True)[:1] != keyword:
                continue
            if written_parameters != want_parameters:
                continue
            score = max(similarity(executed, want_executed), similarity(tokens(query.sql, comments=True), want_written))
            if score >= SIMILAR and (best is None or score > best[0]):
                best = (score, query)
        if best is None:
            continue
        _, query = best
        start, end = query.span
        code = code[:start] + name + code[end:]
        if query.variable:
            code = drop_unused(code, query.variable)
        used.add(name)
        mask = java_mask(code)
        notes.append(f"Helyben javítva: a(z) {name} konstans fut a modell újragépelt SQL-je helyett, amely eltért a "
                     "megjegyzésétől (például egy sorba írt -- megjegyzés vagy két összeragadt szó); a megjegyzés SQL-je "
                     "betű szerint fut, ugyanazokkal a paraméterekkel.")
        warnings.append(f"A(z) {name} SQL-jét a migrátor cserélte a megjegyzés pontos SQL-jére (a modellé eltért); "
                        "átvétel előtt nézd meg a diffet.")
    return code


def drop_unused(code: str, variable: str) -> str:
    """String sql = "..."; whose only use was the replaced createNativeQuery argument: the declaration goes too."""
    mask = java_mask(code)
    uses = list(re.finditer(r"(?<![\w$.])" + re.escape(variable) + r"(?![\w$])", mask))
    declaration = re.search(r"(?m)^[ \t]*(?:final\s+)?(?:String|var)\s+" + re.escape(variable) + r"\s*=[^;]*;[ \t]*\n?", mask)
    if declaration and len(uses) == 1 and declaration.start() <= uses[0].start() < declaration.end():
        code = code[:declaration.start()] + code[declaration.end():]
    return code

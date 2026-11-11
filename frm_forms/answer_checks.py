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


def native_queries(code: str) -> list[NativeQuery]:
    """The createNativeQuery calls of the code with their SQL and the parameters set on them."""
    mask = java_mask(code)
    calls = [(m, ASSIGNED.search(mask[:m.start()])) for m in CREATE.finditer(mask)]
    queries = []
    for index, (match, variable) in enumerate(calls):
        opening = match.end() - 1
        closing = closing_paren(mask, opening)
        if closing < 0:
            continue
        pieces = [text_of(m.group(1)) for m in LITERAL.finditer(code, opening + 1, closing)]
        # string literals only (the mask blanks them): the statement is known here; anything else is built at run time
        sql = "".join(pieces) if pieces and not mask[opening + 1:closing].replace("+", "").strip() else None
        end = statement_end(mask, closing + 1)
        query = NativeQuery(sql, set_calls(code, mask, closing + 1, end))
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
    return list(dict.fromkeys(SQL_PARAMETER.findall(SQL_STRING.sub("''", sql))))


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


def squeeze(sql: str) -> str:
    """SQL as compared: without whitespace and letter case (the names of the parameters stay)."""
    return re.sub(r"\s+", "", sql).upper()


def problems(code: str, contract: dict | None = None) -> list[tuple[str, str]]:
    """(Hungarian for the developer, English for the model) of everything that would fail or differ at run time."""
    found = []
    queries = native_queries(code)
    for query in queries:
        if query.sql is None or not query.complete:
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
    literals = squeeze("".join(text_of(m.group(1)) for m in LITERAL.finditer(code)))
    mask = java_mask(code)
    for statement in contract.get("sql", []):
        if squeeze(statement) not in literals:
            found.append(("A feladat SQL-utasítása hiányzik vagy megváltozott: " + " ".join(statement.split())[:160],
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

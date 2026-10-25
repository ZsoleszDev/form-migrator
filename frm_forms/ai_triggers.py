"""backend_trigger_mode = "ai": the generator never translates Forms trigger code into the DPS ServiceImpl.

Every trigger that runs on the server gets a method with the right signature (the "frame": DTOs,
endpoint, transaction, log1x, call site in the CRUD flow) whose body is only the original Oracle
Forms code as a comment (//region Eredeti Forms-kód ... //endregion) and a HTTP 501 placeholder.
The Java assistant (local AI) rewrites exactly that body; nothing half-translated confuses it.

What stays deterministic: the frontend, the CRUD SQL of the blocks, LOVs, the COMMIT_FORM order,
item properties (lengths, ranges, defaults), the routes and DTOs. Only the decision *which* contract a
button gets (a query button returns the target block's rows) is made here, from the code's shape.
"""
from __future__ import annotations

import re

from .common import decode_line_escapes, name

# The region title the Java assistant looks for (backend_assistant.ORIGINAL_CODE).
REGION_TITLE = "Eredeti Forms-kód"
PLACEHOLDER = "A Forms-trigger még nincs átírva (Java asszisztens): "

SET_WHERE = re.compile(r"\bSET_BLOCK_PROPERTY\s*\(\s*'([A-Za-z_][\w$#]*)'\s*,\s*(?:DEFAULT_WHERE|ONETIME_WHERE)\b", re.I)
GO_BLOCK = re.compile(r"\bGO_BLOCK\s*\(\s*'([A-Za-z_][\w$#]*)'\s*\)", re.I)
EXECUTE_QUERY = re.compile(r"\bEXECUTE_QUERY\b|\bDO_KEY\s*\(\s*'EXECUTE_QUERY'\s*\)", re.I)
COMMENT = re.compile(r"--[^\n\r]*|/\*[\s\S]*?\*/")
WORD = re.compile(r"[A-Za-z_][\w$#]*")


def code_text(source: str) -> str:
    """The PL/SQL without comments (string literals stay: block names are literals)."""
    text = decode_line_escapes(source or "")
    try:
        from .plsql_passthrough import scan
        return "".join(t[1] if t[0] != "comment" else " " for t in scan(text))
    except Exception:  # a lexically broken source still gets a best-effort answer
        return COMMENT.sub(" ", text)


def reachable_units(source: str, units: dict, limit: int = 40) -> list[str]:
    """Local program units the code calls, directly or through other local units (by name, lexically)."""
    found, pending = [], [code_text(source)]
    while pending and len(found) < limit:
        text = pending.pop()
        words = {w.upper() for w in WORD.findall(text)}
        for unit in units:
            if unit in words and unit not in found:
                found.append(unit)
                pending.append(code_text(units[unit].get("text", "")))
    return found


def query_target(source: str, model: dict) -> str | None:
    """The database block a button queries with its own filter (SET_BLOCK_PROPERTY DEFAULT_WHERE + EXECUTE_QUERY).

    Such a button keeps the query contract (QueryActionRequest -> PageResult<TargetRow>): the screen shows the
    returned rows. Only the shape is checked; the filter itself is written by the AI from the commented code.
    """
    from .plsql_passthrough import local_units
    units = local_units(model)
    texts = [code_text(source)] + [code_text(units[u].get("text", "")) for u in reachable_units(source, units)]
    joined = "\n".join(texts)
    if not EXECUTE_QUERY.search(joined):
        return None
    targets = {m.group(1).upper() for m in SET_WHERE.finditer(joined)}
    if len(targets) != 1:
        gone = {m.group(1).upper() for m in GO_BLOCK.finditer(joined)}
        targets = targets & gone if len(targets) > 1 else targets
    if len(targets) != 1:
        return None
    target = next(iter(targets))
    block = next((b for b in model["blocks"] if b["name"] == target), None)
    if not block or not block["database"] or not block.get("query_allowed", True) or not block["db_items"] or block.get("backend_skip"):
        return None
    return target


def writes_database_item(source: str, block: dict) -> bool:
    """True when the code assigns (or SELECT ... INTO) a database item of its block (POST-CHANGE on fetch)."""
    text = code_text(source).upper()
    names = {i["name"].upper() for i in block["db_items"]}
    prefix = re.escape(block["name"].upper())
    for match in re.finditer(r":\s*(?:" + prefix + r"\s*\.\s*)?([A-Z_][\w$#]*)\s*:=", text):
        if match.group(1) in names:
            return True
    for match in re.finditer(r"\bINTO\b([^;]*?)(?:\bFROM\b|;)", text):
        for ref in re.finditer(r":\s*(?:" + prefix + r"\s*\.\s*)?([A-Z_][\w$#]*)", match.group(1)):
            if ref.group(1) in names:
                return True
    return False


def mark(trigger: dict, block: dict | None, source: str, catalog: dict | None = None) -> None:
    """A record/item trigger of a database block: generated as an AI hook, never translated here."""
    trigger.update(status="converted", target="backend", ai=True)
    trigger.pop("reason", None)
    trigger.pop("java", None)
    trigger.pop("passthrough", None)
    calls = []
    if catalog:
        try:
            from .rules import framework_statements
            calls = framework_statements(source, catalog)
        except Exception:
            calls = []
    if calls:
        trigger["framework_calls"] = calls
    if trigger["event"] == "POST-CHANGE" and block is not None:
        # Forms fires POST-CHANGE on fetch as well: the same hook runs for fetched rows when it writes no
        # queried column (a derived description field, like the earlier POST-QUERY variant did).
        trigger["ai_on_query"] = not writes_database_item(source, block)


def hook_name(trigger: dict, taken: set) -> str:
    """trigger<Event>[<Item>][Form]: readable for the developer and the model (the block suffix comes later)."""
    event = name(trigger["event"].lower().replace("-", "_"), "pascal")
    item = name(trigger["item"].lower(), "pascal") if trigger.get("item") else ""
    base = "trigger" + event + item + ("Form" if trigger.get("form_level") else "")
    result, n = base, 2
    while result in taken:
        result, n = base + str(n), n + 1
    taken.add(result)
    return result


def region(title: str, sections: list[tuple[str, str]], indent: str, notes: list[str] = ()) -> str:
    """The original code as a foldable comment; the Java assistant offers every method that has one."""
    lines = [indent + "//region " + REGION_TITLE + ": " + title]
    for note in notes:
        lines.append(indent + "// " + note)
    for heading, code in sections:
        lines.append(indent + "// --- " + heading)
        for line in decode_line_escapes(code or "").replace("\r\n", "\n").replace("\r", "\n").rstrip().split("\n"):
            lines.append(indent + ("// " + line.rstrip().replace("\\", "[backslash]") if line.strip() else "//"))
    lines.append(indent + "//endregion")
    return "\n".join(lines) + "\n"


def placeholder(label: str, indent: str) -> str:
    from .common import jstr
    return (indent + "// TODO(AI): a fenti Forms-kód Java-változata ide kerül (Java asszisztens).\n"
            + indent + "throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, " + jstr(PLACEHOLDER + label) + ");")


def unit_sections(source: str, model: dict) -> list[tuple[str, str]]:
    """The local procedures/functions/packages the trigger calls, so the model sees what they do."""
    from .plsql_passthrough import local_units
    units = local_units(model)
    sections = []
    for unit in reachable_units(source, units):
        info = units[unit]
        label = info.get("kind", "procedure") + " " + unit + (" (csatolt könyvtár: " + info["library"] + ")" if info.get("library") else "")
        sections.append(("Hívott helyi " + label, info.get("text", "")))
    return sections


def framework_note(source: str, catalog: dict | None, calls: list[str] | None = None) -> list[str]:
    """Catalogued framework plumbing (Headstart qms$..., cgte$...) the model should leave out."""
    if calls is None and catalog:
        try:
            from .rules import framework_statements
            calls = framework_statements(source, catalog)
        except Exception:
            calls = []
    calls = [" ".join(c.split()) for c in (calls or [])]
    return ["Keretrendszer-hívás (Headstart/Designer), Java-ban kihagyandó: " + "; ".join(calls)] if calls else []


def hook_body(trigger: dict, model: dict, catalog: dict | None = None, indent: str = "        ") -> str:
    """The body of a record/item trigger hook: the region and the placeholder."""
    source = (trigger.get("replacement") or {}).get("source", trigger["source"])
    title = trigger["owner"] + " / " + trigger["event"] + (" (form szintű, " + trigger["block"] + " blokkra)" if trigger.get("form_level") else "")
    notes = ["AI-ra vár. Bemenet/kimenet: a rekord (row.<mező>), üzenet: context.message(...), hiba: context.abort() vagy kivétel."]
    notes += framework_note(source, catalog, trigger.get("framework_calls"))
    sections = [(trigger["owner"] + " / " + trigger["event"], source)] + unit_sections(source, model)
    return region(title, sections, indent, notes) + placeholder(trigger["owner"] + " / " + trigger["event"], indent)

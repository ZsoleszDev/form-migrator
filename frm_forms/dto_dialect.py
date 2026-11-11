"""The module's DTO dialect for the Java assistant: the names of its DTOs and how their values are read.

The two backend formats write different DTOs for the same contract:

  - the default format: nested classes of <Module>Dtos (ActionResult, PageResult<T> ...) with public fields and
    record-style accessors: row.name, request.blocks();
  - the company (AWU) format: one Lombok bean per DTO, <Module><Name>Dto (RtActionResultDto, RtPageResultDto<T> ...),
    with private fields, getters and setters: row.getName(), row.setName(value), request.getBlocks().

The assistant's prompt used to name only the generic contract ("Return new ActionResult(...)", "row.field",
"request.blocks()"), and with the company DTOs a small model copied it literally: return new ActionResult(...)
in a method that returns RtActionResultDto. The prompt now speaks the module's dialect (assistant_context.build),
and an answer's leftovers of the other dialect are repaired in it (reply_repair).

Everything is read from the task packet: the CL DTO sources (packet["contracts"]) and the method's own signature,
which is authoritative for the method's return and request types.
"""
from __future__ import annotations

import re

from .java_scan import java_mask, matching_brace, strip_annotations

# The generic contract names the prompt and the migrator's hints use; the longest first (QueryActionRequest
# before ActionRequest), so that a suffix match finds the right one.
WRAPPERS = ("QueryActionRequest", "ActionRequest", "ActionResult", "CommitRequest", "CommitResult", "SearchRequest",
            "UpdateRequest", "BlockChanges", "PageResult", "RowResult", "LovRequest", "LovResult")
CLASS = re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)\s*(<[^{]*?>)?[^{;]*\{")
FIELD = re.compile(r"^[ \t]*(?:@[\w.$]+(?:\s*\([^()]*\))?\s+)*(?:public|private|protected)\s+"
                   r"(?!static\b|final\b|class\b|abstract\b)"
                   r"([A-Za-z_$][\w$.]*(?:<[^;=(){}]*>)?(?:\[\])*)\s+([A-Za-z_$][\w$]*)\s*;", re.M)
SIGNATURE = re.compile(r"^\s*(?:(?:public|protected|private|static|final|synchronized)\s+)*"
                       r"(?:<[^>]*>\s+)?([A-Za-z_$][\w$.]*(?:<[^()]*>)?(?:\[\])*)\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)")


def plain() -> dict:
    """The default format: the generic names themselves, public fields, record-style accessors."""
    return {"beans": False, "types": {}, "classes": {}}


def raw_type(type_name: str) -> str:
    """List<RtBDto> -> List, java.math.BigDecimal -> BigDecimal."""
    return type_name.split("<", 1)[0].strip().rsplit(".", 1)[-1]


def type_arguments(type_name: str) -> list[str]:
    """RtPageResultDto<RtBDto> -> [RtBDto]; Map<String, Map<String, String>> -> [String, Map<String, String>]."""
    if "<" not in type_name:
        return []
    inner = type_name[type_name.index("<") + 1:type_name.rindex(">")]
    parts, depth, start = [], 0, 0
    for index, char in enumerate(inner):
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(inner[start:index].strip())
            start = index + 1
    parts.append(inner[start:].strip())
    return [p for p in parts if p]


def classes_of(source: str) -> dict:
    """{class: {"generic": [T ...], "fields": {field: type}, "lombok": bool}} of every class of a CL source."""
    mask = java_mask(source)
    result = {}
    for match in CLASS.finditer(mask):
        try:
            end = matching_brace(mask, match.end() - 1)
        except Exception:  # an unbalanced source is no contract to read
            continue
        body_mask = mask[match.end():end]
        # Only the class's own fields: blank its nested classes' bodies first.
        own = list(body_mask)
        for nested in CLASS.finditer(body_mask):
            try:
                close = matching_brace(body_mask, nested.end() - 1)
            except Exception:
                continue
            own[nested.end():close] = [" " if c != "\n" else "\n" for c in body_mask[nested.end():close]]
        own = "".join(own)
        fields = {m.group(2): " ".join(m.group(1).split()) for m in FIELD.finditer(own)}
        generic = [g.split()[0] for g in type_arguments(match.group(2))] if match.group(2) else []
        head = mask[max(0, mask.rfind("}", 0, match.start()) + 1):match.start()]
        lombok = bool(re.search(r"@(?:lombok\.)?(?:Data|Getter|Setter|Value)\b", head))
        result[match.group(1)] = {"generic": generic, "fields": fields, "lombok": lombok}
    return result


def generic_name(name: str, module_class: str) -> str | None:
    """RtActionResultDto -> ActionResult, RtQueryActionRequestDto -> QueryActionRequest; a row DTO -> None."""
    stem = name[:-3] if name.endswith(("Dto", "DTO")) else name
    if module_class and stem.startswith(module_class) and stem[len(module_class):] in WRAPPERS:
        return stem[len(module_class):]
    return next((w for w in WRAPPERS if stem.endswith(w)), None)


def from_contracts(contracts: dict, module_class: str = "") -> dict:
    """The dialect of the module's CL DTO sources (file name -> source), as the task packet keeps them."""
    classes, beans = {}, False
    for file, source in sorted((contracts or {}).items()):
        if not isinstance(source, str) or not file.endswith(".java") or file == "CommonMigrateTools.java":
            continue
        found = classes_of(source)
        for name, info in found.items():
            if name.endswith(("Constants", "RestClient", "RestClientImpl")) or name == module_class + "Dtos":
                continue
            # A Lombok bean with private fields: the company DTO (getters/setters, no public field).
            if info["lombok"] and info["fields"] and re.search(r"^[ \t]*private\s", source, re.M):
                info["bean"] = True
                beans = True
            classes[name] = info
    types = {}
    if beans:
        for name in classes:
            generic = generic_name(name, module_class)
            if generic and generic != name:
                types.setdefault(generic, name)
    return {"beans": beans, "types": types,
            "classes": {name: {"generic": info["generic"], "fields": info["fields"]} for name, info in classes.items()
                        if not beans or info.get("bean")}}


def of_packet(packet: dict) -> dict:
    """The dialect of a task: its module's CL DTOs, the method's own signature on top (authoritative)."""
    target = str(packet.get("target") or "")
    module_class = target.rsplit("/", 1)[-1].removesuffix(".java").removesuffix("ServiceImpl")
    dialect = from_contracts(packet.get("contracts") or {}, module_class)
    if dialect["beans"]:
        # The generator's own names (analysis/cl-contract.json): OrdersRow -> OrdersOrdersDto too.
        dialect["types"] = {**{k: v for k, v in (packet.get("dto_types") or {}).items() if v in dialect["classes"]},
                            **dialect["types"]}
    return with_signature(dialect, str(packet.get("signature") or packet.get("existing_method") or ""))


def signature(header: str) -> tuple[str, str, dict]:
    """(return type, method name, {parameter: type}) of a method header."""
    match = SIGNATURE.search(strip_annotations(java_mask(header.split("{", 1)[0])))
    if not match:
        return "", "", {}
    return " ".join(match.group(1).split()), match.group(2), parameters(match.group(3))


def parameters(text: str) -> dict:
    """'UserDto user, RtSearchRequestDto<RtTFilterDto> request' -> {user: UserDto, request: RtSearchRequestDto<...>}."""
    result, depth, start = {}, 0, 0
    for index, char in enumerate(text + ","):
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        elif char == "," and depth == 0:
            part = re.sub(r"@\w+(?:\([^)]*\))?\s*|\bfinal\s+", "", text[start:index]).strip()
            if " " in part:
                typ, name = part.rsplit(None, 1)
                result[name] = " ".join(typ.split())
            start = index + 1
    return result


def with_signature(dialect: dict, header: str) -> dict:
    """The method's return and parameter types name the module's wrappers, whatever the CL sources say: a method
    returning MyModulActionResultDto makes ActionResult -> MyModulActionResultDto."""
    returns, _, params = signature(header)
    types = dict(dialect.get("types") or {})
    for type_name in [returns, *params.values()]:
        name = raw_type(type_name)
        generic = generic_name(name, "")
        if generic and name != generic and name.endswith(("Dto", "DTO")):
            types[generic] = name
    return {**dialect, "types": types}


def capitalized(field: str) -> str:
    return field[:1].upper() + field[1:]


def getter(field: str, type_name: str = "") -> str:
    """Lombok's getter: isX() for a primitive boolean, else getX()."""
    return ("is" if type_name == "boolean" else "get") + capitalized(field)


def setter(field: str) -> str:
    return "set" + capitalized(field)


# --- the prompt in the module's dialect --------------------------------------------------------------------------

# The prompt's generic phrases about reading the DTOs, and their wording for the company's Lombok beans. Each plain
# phrase occurs in the rules of assistant_context (tests check it), so a reworded rule cannot drift silently.
BEAN_PHRASES = (
    ("Row DTOs have public fields (row.code); requests have accessors (request.limit()).",
     "The DTOs are Lombok beans with private fields: row.getCode(), row.setCode(value), request.getLimit()."),
    ("read and\n   assign row.field directly.",
     "read it as\n   row.getField(), assign it with row.setField(value) (a Lombok bean, no public field)."),
    ("(public, set them directly)", "(a Lombok bean: set them with their setters, row.setCode(value))"),
)
ACCESSOR = re.compile(r"\b(request|result)\s*\.\s*(blocks|parameters|offset|limit|term|criteria|original|value)\(\)")


def prompt_text(text: str, dialect: dict) -> str:
    """A rule or hint of the prompt in the module's dialect: its DTO names, and getters/setters for beans."""
    types = {k: v for k, v in (dialect.get("types") or {}).items() if k != v}
    if types:
        names = "|".join(re.escape(k) for k in sorted(types, key=len, reverse=True))
        text = re.sub(r"(?<![\w$])(" + names + r")(?![\w$])", lambda m: types[m.group(1)], text)
    if dialect.get("beans"):
        for old, new in BEAN_PHRASES:
            text = text.replace(old, new)
        text = ACCESSOR.sub(lambda m: m.group(1) + "." + getter(m.group(2)) + "()", text)
        text = re.sub(r"(?<![\w$.])row\.field\b(?!\s*\()", "row.getField()", text)
    return text


# The migrator's notes in a method's //region (before the first "// --- <heading>" line, after which the original
# Forms source follows) were written in the generic dialect: "request.criteria().rendelesId", ":id = row.id",
# "eredmény -> row.vevoNev". They are sent in the module's dialect too; the file keeps them as they are (the model does
# not copy the region, the migrator keeps the file's own), so modules generated earlier get the same prompt.
REGION = re.compile(r"^[ \t]*//[ \t]*region\b[^\n]*\n(?:[^\n]*\n)*?[ \t]*//[ \t]*endregion\b", re.M)
RECEIVERS = r"(?:row|value|original|current|saved|key|request\.getCriteria\(\))"


def bean_fields(dialect: dict) -> dict:
    """field -> type of the module's row and criteria DTOs (not the wrappers)."""
    result = {}
    for name, info in (dialect.get("classes") or {}).items():
        if generic_name(name, "") is None:
            for field, type_name in info["fields"].items():
                result.setdefault(field, type_name)
    return result


def note_line(line: str, dialect: dict, fields: dict) -> str:
    line = prompt_text(line, {**dialect, "beans": False})  # the DTO names only
    if not dialect.get("beans"):
        return line
    line = ACCESSOR.sub(lambda m: m.group(1) + "." + getter(m.group(2)) + "()", line)

    def access(match):
        receiver, field = match.group(2), match.group(3)
        if field not in fields:
            return match.group(0)
        if match.group(1):  # "eredmény -> row.vevoNev": the value is written into the field
            return match.group(1) + receiver + "." + setter(field) + "(..)"
        return receiver + "." + getter(field, fields[field]) + "()"
    return re.sub(r"(->\s*)?(?<![\w$.])(" + RECEIVERS + r")\.([A-Za-z_$][\w$]*)(?![\w$(])", access, line)


def method_notes(method: str, dialect: dict) -> str:
    """The method as the prompt shows it: its regions' migrator notes in the module's dialect."""
    if not dialect.get("beans") and not dialect.get("types"):
        return method
    fields = bean_fields(dialect)

    def notes(match):
        lines, result, forms = match.group(0).split("\n"), [], False
        for line in lines:
            forms = forms or bool(re.match(r"[ \t]*//[ \t]*--- ", line))
            result.append(line if forms else note_line(line, dialect, fields))
        return "\n".join(result)
    return REGION.sub(notes, method)

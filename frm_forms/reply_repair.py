"""The local model's usual slips, repaired here instead of asking the model again (assistant_reply.finish).

A correction round costs a whole model call (minutes on a laptop CPU), and a small model often makes the same slip
twice. The slips below have exactly one meaning, so the migrator fixes them in the answer and says so (a note in the
proposal); anything with more than one reading stays a problem that goes back to the model.

  - the request DTO read as a JavaBean: request.getBlocks() -> request.blocks() (the DTOs have record accessors);
  - the record of a hook read as a JavaBean: row.getVevo() -> row.vevo, row.setId(x); -> row.id = x; (public fields);
  - a repository or an entity named without the module prefix: rendelesRepository / RendelesEntity ->
    rendelesRendelesRepository / RendelesRendelesEntity, when exactly one repository / entity ends that way;
  - the Spring Data methods models reach for (findById, save, delete ...) on a repository that has no generated
    method for the task: the migrator writes them (find/merge/remove, flushed like an SQL statement), with a warning
    that save is an INSERT or UPDATE (merge).

With the company's DTOs (dto_dialect: one Lombok bean per DTO, RtActionResultDto, private fields) the same slips have
the other meaning, and the repairs go the other way:
  - a generic contract name the module has no class for: new ActionResult(...) -> new RtActionResultDto(...),
    new PageResult<>(...) -> new RtPageResultDto<>(...) (the model copied the name of the rules);
  - a DTO read like the default format's: request.blocks() -> request.getBlocks(), row.vevo -> row.getVevo(),
    row.vevo = x; -> row.setVevo(x); for the variables whose DTO type is known (parameters, declarations).
And in both formats the Java 11 / PMD slips with one meaning (java_lint): stream.toList(), x.equals("A") ...

Nothing here touches comments or strings: java_mask keeps them out of the patterns.
"""
from __future__ import annotations

import re

from .dto_dialect import getter, raw_type, setter, type_arguments
from .java_scan import java_mask, replace_identifier

REQUEST_GETTERS = {"getBlocks": "blocks", "getParameters": "parameters", "getOffset": "offset", "getLimit": "limit"}
# What every repository inherits from JpaRepository: the service may call these, nobody writes them.
STANDARD = {"findById", "existsById", "findAll", "findAllById", "count", "save", "saveAll", "saveAndFlush", "flush",
            "delete", "deleteById", "deleteAll", "deleteAllById", "getById", "getOne", "getReferenceById"}


def free(name: str) -> str:
    """A free name, also after `this.`: not a member of something else."""
    return r"(?:(?<=this\.)|(?<![\w$.]))" + re.escape(name) + r"(?![\w$])"


def request_accessors(code: str, notes: list) -> str:
    mask = java_mask(code)
    spans = [(m.start(1), m.end(1), m.group(1))
             for m in re.finditer(r"(?<![\w$.])request\s*\.\s*(" + "|".join(REQUEST_GETTERS) + r")(?=\s*\(\s*\))", mask)]
    for start, end, getter in reversed(spans):
        code = code[:start] + REQUEST_GETTERS[getter] + code[end:]
    if spans:
        found = sorted({s[2] for s in spans})
        notes.append("Helyben javítva: " + ", ".join("request." + g + "() -> request." + REQUEST_GETTERS[g] + "()"
                                                     for g in found) + " (a DTO record-stílusú elérője).")
    return code


def closing_paren(mask: str, opening: int) -> int:
    depth = 0
    for pos in range(opening, len(mask)):
        if mask[pos] == "(":
            depth += 1
        elif mask[pos] == ")":
            depth -= 1
            if depth == 0:
                return pos
    return -1


def row_fields(code: str, fields: set, notes: list) -> str:
    """row.getX() -> row.x and row.setX(value); -> row.x = value; for the public fields of the hook's record."""
    if not fields:
        return code
    by_suffix = {f[:1].upper() + f[1:]: f for f in fields}
    changed = set()
    while True:
        mask = java_mask(code)
        found = None
        for match in re.finditer(r"(?<![\w$.])row\s*\.\s*(get|set|is)([A-Z][\w$]*)\s*\(", mask):
            field = by_suffix.get(match.group(2))
            if field is None:
                continue
            close = closing_paren(mask, match.end() - 1)
            if close < 0:
                continue
            argument = code[match.end():close].strip()
            if match.group(1) in {"get", "is"}:
                if argument:
                    continue
                found = (match.start(), close + 1, "row." + field)
            else:
                tail = re.match(r"\s*;", mask[close + 1:])
                if not argument or not tail or not re.search(r"(?:^|[;{}])\s*$", mask[:match.start()]):
                    continue  # only a statement of its own: row.setX(v);
                found = (match.start(), close + 1 + tail.end(), "row." + field + " = " + argument + ";")
            changed.add(field)
            break
        if found is None:
            break
        code = code[:found[0]] + found[2] + code[found[1]:]
    if changed:
        notes.append("Helyben javítva: a rekord mezői (" + ", ".join(sorted(changed)) + ") getter/setter helyett "
                     "közvetlenül (row.mező), mert a Row DTO-nak nyilvános mezői vannak.")
    return code


def suffix_match(name: str, known: list[str]) -> str | None:
    """The one known name that ends with the given one (module prefix left out), case-insensitively."""
    lower = name.lower()
    found = [k for k in known if k.lower().endswith(lower) and k.lower() != lower]
    return found[0] if len(found) == 1 else None


def declared(mask: str, variable: str) -> bool:
    return bool(re.search(r"\b[A-Z][\w$<>?, ]*\s+" + re.escape(variable) + r"\s*[=;,)]", mask))


def repository_names(code: str, ctx, notes: list) -> str:
    """A repository or entity name the model guessed: RendelesEntity -> RendelesDomain, a unique suffix (tetelRepository ->
    rendelesTetelRepository)."""
    variables = [r.variable for r in ctx.repositories]
    classes = [r.name for r in ctx.repositories] + [r.entity for r in ctx.repositories]
    mask = java_mask(code)
    renamed = []
    for unknown in sorted(set(re.findall(r"(?:(?<=this\.)|(?<![\w$.]))([a-z][\w$]*Repository)(?![\w$])", mask)) - set(variables)):
        target = suffix_match(unknown, variables)
        if target and not declared(mask, unknown) and not re.search(free(target), mask):
            code = replace_identifier(code, unknown, target)
            renamed.append(unknown + " -> " + target)
    mask = java_mask(code)
    domains = {r.entity[:-len("Domain")] + "Entity": r.entity for r in ctx.repositories if r.entity.endswith("Domain")}
    for unknown in sorted(set(re.findall(r"(?<![\w$.])([A-Z][\w$]*(?:Repository|Entity|Domain))(?![\w$])", mask)) - set(classes)):
        # RendelesEntity: the Domain of the table (the company names the entity <Table>Domain)
        target = domains.get(unknown) or suffix_match(unknown, classes)
        if target and not re.search(r"\b(?:class|interface|record|enum)\s+" + re.escape(unknown) + r"\b", mask):
            code = replace_identifier(code, unknown, target, allow_this=False)
            renamed.append(unknown + " -> " + target)
    if renamed:
        notes.append("Helyben javítva (a repository vagy a Domain neve): " + ", ".join(renamed) + ".")
    return code


def dto_names(code: str, ctx, notes: list) -> str:
    """new ActionResult(...) -> new RtActionResultDto(...): a generic contract name the module has no class for."""
    service = ctx.service or ""
    known = set(re.findall(r"^\s*import\s+(?:static\s+)?[\w.$]*\.([A-Za-z_$][\w$]*)\s*;", service, re.M))
    known |= set(re.findall(r"\b(?:class|interface|enum|record)\s+([A-Za-z_$][\w$]*)", java_mask(service)))
    renamed = []
    for generic, actual in sorted((getattr(ctx, "dto_types", None) or {}).items(), key=lambda kv: -len(kv[0])):
        if generic == actual or generic in known:
            continue
        if re.search(r"(?<![\w$.])" + re.escape(generic) + r"(?![\w$])", java_mask(code)):
            code = replace_identifier(code, generic, actual, allow_this=False)
            renamed.append(generic + " -> " + actual)
    if renamed:
        notes.append("Helyben javítva (a modul DTO-osztálya, nem a szabályok általános neve): " + ", ".join(renamed) + ".")
    return code


def property_type(type_name: str | None, prop: str, classes: dict) -> str | None:
    """The DTO type of a bean property (request.getCriteria() of RtSearchRequestDto<RtTFilterDto> -> RtTFilterDto)."""
    if not type_name:
        return None
    info = classes.get(raw_type(type_name))
    if not info:
        return None
    field = prop if prop in info["fields"] else prop[:1].lower() + prop[1:]
    found = info["fields"].get(field)
    if found is None:
        return None
    arguments = dict(zip(info["generic"], type_arguments(type_name)))
    found = arguments.get(found, found)
    return found if raw_type(found) in classes else None


def bean_types(code: str, ctx) -> dict:
    """variable -> DTO type of the code: the method's parameters and the code's own declarations."""
    classes = ctx.dto_classes
    found = {name: typ for name, typ in (ctx.parameters or {}).items() if raw_type(typ) in classes}
    if not classes:
        return found
    mask = java_mask(code)
    names = "|".join(re.escape(c) for c in sorted(classes, key=len, reverse=True))
    generic = r"(?:\s*<[^;(){}=]*?>)?"
    for m in re.finditer(r"(?<![\w$.<])((?:" + names + r")" + generic + r")\s+([a-z_$][\w$]*)\s*(?=[=;,):])", mask):
        found[m.group(2)] = " ".join(m.group(1).split())
    for m in re.finditer(r"\bvar\s+([a-z_$][\w$]*)\s*=\s*new\s+((?:" + names + r")" + generic + r")\s*\(", mask):
        found[m.group(1)] = " ".join(m.group(2).split()).replace("<>", "")
    # Lists of DTOs, for `for (var r : rows)`.
    elements = {}
    for m in re.finditer(r"\b(?:List|ArrayList|Collection|Iterable)\s*<\s*((?:" + names + r")" + generic
                         + r")\s*>\s+([a-z_$][\w$]*)\s*(?=[=;,):])", mask):
        elements[m.group(2)] = " ".join(m.group(1).split())
    for m in re.finditer(r"\bvar\s+([a-z_$][\w$]*)\s*=\s*new\s+ArrayList\s*<\s*((?:" + names + r")" + generic + r")\s*>", mask):
        elements[m.group(1)] = " ".join(m.group(2).split())
    for m in re.finditer(r"\bfor\s*\(\s*(?:final\s+)?var\s+([a-z_$][\w$]*)\s*:\s*([a-z_$][\w$]*)\s*\)", mask):
        if m.group(2) in elements:
            found.setdefault(m.group(1), elements[m.group(2)])
    for _ in range(2):  # var criteria = request.getCriteria(); var row = result.getRow(); var row = value;
        for m in re.finditer(r"\bvar\s+([a-z_$][\w$]*)\s*=\s*([a-z_$][\w$]*)\s*\.\s*(?:get|is)?([A-Za-z_$][\w$]*)\s*\(\s*\)\s*;", mask):
            if m.group(1) not in found:
                typ = property_type(found.get(m.group(2)), m.group(3), classes)
                if typ:
                    found[m.group(1)] = typ
        for m in re.finditer(r"\bvar\s+([a-z_$][\w$]*)\s*=\s*([a-z_$][\w$]*)\s*;", mask):
            if m.group(1) not in found and m.group(2) in found:
                found[m.group(1)] = found[m.group(2)]
    return found


def bean_member(code: str, mask: str, types: dict, classes: dict):
    """The bean accesses of the code: (match, the receiver's DTO type, its fields) for every known receiver."""
    if not types:
        return
    receivers = "|".join(re.escape(v) for v in sorted(types, key=len, reverse=True))
    pattern = (r"(?<![\w$.])(?P<recv>" + receivers + r")(?P<chain>\s*\.\s*(?:get|is)(?P<prop>[A-Z][\w$]*)\s*\(\s*\))?"
               r"\s*\.\s*(?P<name>[A-Za-z_$][\w$]*)(?![\w$])")
    for match in re.finditer(pattern, mask):
        typ = types[match.group("recv")]
        if match.group("chain"):
            typ = property_type(typ, match.group("prop"), classes)
        info = classes.get(raw_type(typ)) if typ else None
        if info and match.group("name") in info["fields"]:
            yield match, typ, info


def bean_access(code: str, ctx, notes: list) -> str:
    """request.blocks() -> request.getBlocks(), row.vevo -> row.getVevo(), row.vevo = x; -> row.setVevo(x);"""
    classes = ctx.dto_classes
    types = bean_types(code, ctx)
    changed = set()
    for _ in range(500):
        mask = java_mask(code)
        edit = None
        for match, typ, info in bean_member(code, mask, types, classes):
            name = match.group("name")
            field_type = info["fields"][name]
            after = mask[match.end():]
            head = code[match.start():match.start("name")]
            call = re.match(r"\s*\(\s*\)", after)
            if call:  # a record-style accessor: request.blocks()
                edit = (match.start(), match.end() + call.end(), head + getter(name, field_type) + "()")
            elif re.match(r"\s*\(", after) or re.match(r"\s*(?:\+\+|--|[-+*/%&|^]=|<<=|>>>?=)", after):
                continue  # a call with arguments, or a compound write: no single rewrite
            elif re.match(r"\s*=(?!=)", after):
                if not re.search(r"(?:^|[;{})]|->|\belse)\s*$", mask[:match.start()]):
                    continue  # only a statement of its own: row.x = v;
                start = match.end() + re.match(r"\s*=", after).end()
                end, depth = start, 0
                while end < len(mask) and not (mask[end] == ";" and depth == 0):
                    depth += mask[end] in "([{"
                    depth -= mask[end] in ")]}"
                    if depth < 0:
                        break
                    end += 1
                if end >= len(mask) or mask[end] != ";":
                    continue
                edit = (match.start(), end, head + setter(name) + "(" + code[start:end].strip() + ")")
            else:
                edit = (match.start(), match.end(), head + getter(name, field_type) + "()")
            changed.add(raw_type(typ) + "." + name)
            break
        if edit is None:
            break
        code = code[:edit[0]] + edit[2] + code[edit[1]:]
    if changed:
        notes.append("Helyben javítva: a DTO-k Lombok beanek (privát mezők), ezért getter/setter: "
                     + ", ".join(sorted(changed)) + ".")
    return code


def bean_fields_left(code: str, ctx) -> list[str]:
    """The bean fields the code still reads or writes directly (what bean_access could not rewrite)."""
    mask = java_mask(code)
    left = []
    for match, typ, _ in bean_member(code, mask, bean_types(code, ctx), ctx.dto_classes):
        if not re.match(r"\s*\(", mask[match.end():]):
            left.append(match.group("recv") + "." + match.group("name"))
    return sorted(set(left))


def variables(code: str, ctx, notes: list) -> str:
    """java-variables.json in the written method: a variable of the map gets the map's value (String igkod =
    formsCommonsRepository.getIgKod(user);), also when it is set straight into a query: setParameter("igkod", ...).
    The value comes from the developer's map, afterwards; the model is not told about it."""
    entries = getattr(ctx, "variables", None) or {}
    if not entries:
        return code
    from .answer_checks import statement_end
    changed = []
    for name, entry in entries.items():
        targets = [name]
        mask = java_mask(code)
        for call in re.finditer(r"\.\s*setParameter\s*\(\s*\"" + re.escape(name) + r"\"\s*,", mask):
            end = closing_paren(mask, call.end() - mask[:call.end()][::-1].index("(") - 1)
            value = code[call.end():end].strip() if end > 0 else ""
            if re.fullmatch(r"[A-Za-z_$][\w$]*", value):
                targets.append(value)  # set from another local: that one gets the value
            elif value and value != entry["value"] and end > 0:
                start = call.end() + (len(code[call.end():end]) - len(code[call.end():end].lstrip()))
                code = code[:start] + entry["value"] + code[end:]
                changed.append(name)
                mask = java_mask(code)
        for target in dict.fromkeys(targets):
            mask = java_mask(code)
            declared = re.search(r"(?<![\w$.])(?:final\s+)?[A-Za-z_$][\w$.<>\[\]]*\s+" + re.escape(target) + r"\s*=(?!=)", mask)
            if not declared:
                continue
            end = statement_end(mask, declared.end())
            if code[declared.end():end].strip() != entry["value"]:
                code = code[:declared.end()] + " " + entry["value"] + code[end:]
                changed.append(name)
    if changed:
        notes.append("Helyben kicserélve a java-variables.json alapján: " + ", ".join(dict.fromkeys(changed))
                     + " (a változó értéke a térkép kifejezése).")
    return code


def service(code: str, ctx, notes: list) -> str:
    code = variables(code, ctx, notes)
    code = dto_names(code, ctx, notes)
    if getattr(ctx, "beans", False):
        code = bean_access(code, ctx, notes)
    else:
        code = request_accessors(code, notes)
        code = row_fields(code, set(ctx.row_fields), notes)
    if ctx.mode == "jpa" and ctx.repositories:
        code = repository_names(code, ctx, notes)
    from . import java_lint
    return java_lint.repair(code, notes)


def response_dialect(response: dict, ctx) -> dict:
    """A deterministic proposal (hook_java, action_java) in the module's dialect: the same repairs, no notes."""
    if not getattr(ctx, "beans", False) and not getattr(ctx, "dto_types", None):
        return response
    notes = []
    fix = lambda text: (bean_access(dto_names(text, ctx, notes), ctx, notes) if ctx.beans else dto_names(text, ctx, notes))
    return {**response, "body": fix(response.get("body", "")), "private_methods": fix(response.get("private_methods", ""))}


def standard_methods(code: str, ctx, available: dict, warnings: list) -> dict:
    """Nothing to write any more: the repository is an interface extending JpaRepository, which has findById, save,
    delete ... (STANDARD). Kept for the callers; always {}."""
    return {}

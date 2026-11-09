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

Nothing here touches comments or strings: java_mask keeps them out of the patterns.
"""
from __future__ import annotations

import re

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


def service(code: str, ctx, notes: list) -> str:
    code = request_accessors(code, notes)
    code = row_fields(code, set(ctx.row_fields), notes)
    if ctx.mode == "jpa" and ctx.repositories:
        code = repository_names(code, ctx, notes)
    return code


def standard_methods(code: str, ctx, available: dict, warnings: list) -> dict:
    """Nothing to write any more: the repository is an interface extending JpaRepository, which has findById, save,
    delete ... (STANDARD). Kept for the callers; always {}."""
    return {}

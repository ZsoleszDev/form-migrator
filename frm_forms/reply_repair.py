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
# Spring Data CrudRepository names -> the migrator's implementation (E: the entity, I: the id type).
STANDARD = {
    "findById": """    public Optional<E> findById(I id) {
        return Optional.ofNullable(entityManager.find(E.class, id));
    }""",
    "existsById": """    public boolean existsById(I id) {
        return entityManager.find(E.class, id) != null;
    }""",
    "save": """    public E save(E entity) {
        // INSERT or UPDATE (merge), flushed at once like the SQL statement
        E saved = entityManager.merge(entity);
        entityManager.flush();
        return saved;
    }""",
    "delete": """    public void delete(E entity) {
        entityManager.remove(entityManager.contains(entity) ? entity : entityManager.merge(entity));
        entityManager.flush();
    }""",
    "deleteById": """    public void deleteById(I id) {
        E found = entityManager.find(E.class, id);
        if (found != null) {
            entityManager.remove(found);
            entityManager.flush();
        }
    }""",
    "findAll": """    public List<E> findAll() {
        CriteriaBuilder cb = entityManager.getCriteriaBuilder();
        CriteriaQuery<E> query = cb.createQuery(E.class);
        query.select(query.from(E.class));
        return entityManager.createQuery(query).getResultList();
    }""",
    "count": """    public long count() {
        CriteriaBuilder cb = entityManager.getCriteriaBuilder();
        CriteriaQuery<Long> query = cb.createQuery(Long.class);
        query.select(cb.count(query.from(E.class)));
        return entityManager.createQuery(query).getSingleResult();
    }""",
}
STANDARD_IMPORTS = {"findById": ["java.util.Optional"], "findAll": ["java.util.List"]}


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
    """rendelesRepository -> rendelesRendelesRepository, RendelesEntity -> RendelesRendelesEntity (unique suffix)."""
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
    for unknown in sorted(set(re.findall(r"(?<![\w$.])([A-Z][\w$]*(?:Repository|Entity))(?![\w$])", mask)) - set(classes)):
        target = suffix_match(unknown, classes)
        if target and not re.search(r"\b(?:class|interface|record|enum)\s+" + re.escape(unknown) + r"\b", mask):
            code = replace_identifier(code, unknown, target, allow_this=False)
            renamed.append(unknown + " -> " + target)
    if renamed:
        notes.append("Helyben javítva (a modul előtagja hiányzott): " + ", ".join(renamed) + ".")
    return code


def service(code: str, ctx, notes: list) -> str:
    code = request_accessors(code, notes)
    code = row_fields(code, set(ctx.row_fields), notes)
    if ctx.mode == "jpa" and ctx.repositories:
        code = repository_names(code, ctx, notes)
    return code


def standard_methods(code: str, ctx, available: dict, warnings: list) -> dict:
    """{repository: [(source, imports)]}: the Spring Data methods the service calls on a repository without them.

    Only on a repository with no generated method for this task (its SQL is not the migrator's): there a call of
    findById/save beside the generated methods means the model ignored them, which goes back to the model.
    """
    mask = java_mask(code)
    result = {}
    for repo in ctx.repositories:
        if ctx.generated.get(repo.name) or not repo.id_type:
            continue
        calls = re.finditer(free(repo.variable) + r"\s*\.\s*([A-Za-z_$][\w$]*)\s*\(", mask)
        names = [n for n in dict.fromkeys(c.group(1) for c in calls) if n in STANDARD and n not in available.get(repo.name, set())]
        if not names:
            continue
        result[repo.name] = [(STANDARD[n].replace("E.class", repo.entity + ".class")
                              .replace("<E>", "<" + repo.entity + ">")
                              .replace("public E ", "public " + repo.entity + " ")
                              .replace("(E entity)", "(" + repo.entity + " entity)")
                              .replace("E saved", repo.entity + " saved").replace("E found", repo.entity + " found")
                              .replace("(I id)", "(" + repo.id_type + " id)"),
                              STANDARD_IMPORTS.get(n, [])) for n in names]
        warnings.append(f"A(z) {repo.name} {', '.join(n + '()' for n in names)} metódusát a migrátor írta (Spring "
                        "Data-szerű alapmetódus, find/merge/remove). Ellenőrizd, hogy az eredeti SQL-lel azonos-e: "
                        "a save() INSERT vagy UPDATE (merge), létező kulcsra nem ad ORA-00001-et."
                        if "save" in names else
                        f"A(z) {repo.name} {', '.join(n + '()' for n in names)} metódusát a migrátor írta (Spring "
                        "Data-szerű alapmetódus, find/remove).")
    return result

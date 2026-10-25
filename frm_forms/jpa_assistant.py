"""Entity-based persistence for the simple trigger-to-Java assistant.

Own filenames, entity mappings and service injection locally. The model adds
repository methods and optional small DTOs, never controllers or endpoint APIs.
"""
from __future__ import annotations

from pathlib import Path
import re

from .common import MigrationError, RESERVED, name
from . import backend_assistant as engine
from .java_scan import java_mask, scan_members, strip_annotations, without_sequence_queries

# JPA names a repository method may use without writing the import (javax or jakarta, per config).
PERSISTENCE_NAMES = ("EntityManager", "PersistenceContext", "TypedQuery", "Query", "NoResultException",
                     "NonUniqueResultException", "EntityNotFoundException", "LockModeType", "Tuple",
                     "PersistenceException", "OptimisticLockException", "FlushModeType")
CRITERIA_NAMES = ("CriteriaBuilder", "CriteriaQuery", "CriteriaUpdate", "CriteriaDelete", "Root", "Predicate", "Path",
                  "Expression", "Order", "Subquery", "Join", "JoinType", "ParameterExpression", "Selection", "From")


def persistence_imports(namespace: str) -> dict:
    mapping = {simple: namespace + ".persistence." + simple for simple in PERSISTENCE_NAMES}
    mapping.update({simple: namespace + ".persistence.criteria." + simple for simple in CRITERIA_NAMES})
    return mapping


def package_of(source: str) -> str:
    match = re.search(r"^\s*package\s+([\w.]+)\s*;", source, re.M)
    return match[1] if match else ""


def preamble(package: str, imports=()) -> str:
    return ("package " + package + ";\n\n" if package else "") + "".join(
        "import " + value + ";\n" for value in sorted(set(imports))) + ("\n" if imports else "")


def java_type(spec: dict) -> str:
    db_type = re.sub(r"TIMESTAMP\(\d+\)", "TIMESTAMP", str(spec.get("data_type", "")).upper())
    if db_type in {"VARCHAR", "VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR", "CLOB", "NCLOB"}:
        return "String"
    if db_type in {"NUMBER", "DECIMAL", "NUMERIC", "INTEGER", "FLOAT"}:
        return "java.math.BigDecimal"
    if db_type in {"DATE", "TIMESTAMP"}: return "java.time.LocalDateTime"
    if db_type == "TIMESTAMP WITH LOCAL TIME ZONE":
        raise MigrationError("TIMESTAMP WITH LOCAL TIME ZONE: a céges időzóna-leképezést add meg a projektmintában.")
    if db_type == "TIMESTAMP WITH TIME ZONE": return "java.time.OffsetDateTime"
    if db_type in {"BLOB", "RAW"}: return "byte[]"
    if db_type == "BINARY_FLOAT": return "Float"
    if db_type == "BINARY_DOUBLE": return "Double"
    raise MigrationError("Nem ismert JPA-leképezés: " + db_type)


def accessors(fields: dict[str, str]) -> str:
    result = []
    for field, typ in fields.items():
        suffix = field[0].upper() + field[1:]
        result += [f"    public {typ} get{suffix}() {{ return {field}; }}",
                   f"    public void set{suffix}({typ} value) {{ this.{field} = value; }}"]
    return "\n\n".join(result)


def id_source(package: str, cls: str, fields: dict[str, str]) -> str:
    declarations = "\n".join("    public " + typ + " " + field + ";" for field, typ in fields.items())
    equal, hashed = [], []
    for field, typ in fields.items():
        if typ == "java.math.BigDecimal":
            equal.append(f"({field} == null ? that.{field} == null : that.{field} != null && {field}.compareTo(that.{field}) == 0)")
            hashed.append(f"{field} == null ? null : {field}.stripTrailingZeros()")
        else:
            equal.append(f"java.util.Objects.equals({field}, that.{field})")
            hashed.append(field)
    return preamble(package) + f"""// Forms migrator JPA composite key
public class {cls} implements java.io.Serializable {{
    private static final long serialVersionUID = 1L;
{declarations}

    public {cls}() {{}}

    @Override
    public boolean equals(Object other) {{
        if (this == other) return true;
        if (!(other instanceof {cls})) return false;
        {cls} that = ({cls}) other;
        return {" && ".join(equal)};
    }}

    @Override
    public int hashCode() {{
        return java.util.Objects.hash({", ".join(hashed)});
    }}
}}
"""


def plan(packet: dict, output: Path) -> list[dict]:
    namespace = packet.get("config", {}).get("jpa_namespace") or "javax"
    if namespace not in {"javax", "jakarta"}:
        raise MigrationError("jpa_namespace: javax vagy jakarta szükséges.")
    package = package_of(packet.get("service_package_source", ""))
    tables = {str(b.get("table") or "").upper() for b in packet.get("blocks", []) if b.get("database")}
    tables |= {t["table"] for t in packet.get("table_requirements", [])}
    from .schema_workbench import is_oracle_dual
    result, classes = [], set()
    for table in sorted(t for t in tables if t and not is_oracle_dual(t)):
        spec = packet.get("schema", {}).get("tables", {}).get(table)
        if not isinstance(spec, dict) or not spec.get("columns"):
            raise MigrationError(table + ": előbb add meg a táblaszerkezetet.")
        columns, keys = spec["columns"], spec.get("primary_key", [])
        if not keys or not all(k in columns for k in keys):
            raise MigrationError(table + ": a JPA entityhez jelöld az adatbázis valódi elsődleges kulcsát (PK).")
        fields = {c: name(c) for c in columns}
        if (not engine.DB_NAME.fullmatch(table) or any(not engine.DB_NAME.fullmatch(c) or "." in c for c in columns)
                or len(set(fields.values())) != len(fields)):
            raise MigrationError(table + ": ütköző vagy nem támogatott oszlopnév.")
        types = {c: java_type(v) for c, v in columns.items()}
        if any(types[k] == "byte[]" for k in keys):
            raise MigrationError(table + ": a bináris elsődleges kulcshoz céges JPA-leképezés szükséges.")
        cls = name(packet["module"], "pascal") + name(table, "pascal")
        entity, repository = cls + "Entity", cls + "Repository"
        if entity in classes: raise MigrationError("Ütköző JPA-osztálynév: " + entity)
        classes.add(entity)
        imports = {namespace + ".persistence." + a for a in ("Entity", "Table", "Id", "Column")}
        table_name = table.rsplit(".", 1)[-1]
        table_annotation = f'@Table(name = "{table_name}"' + (f', schema = "{table.split(".")[0]}"' if "." in table else "") + ")"
        annotations = f'@Entity(name = "{entity}")\n{table_annotation}'
        extra = {}
        if len(keys) > 1:
            imports.add(namespace + ".persistence.IdClass")
            annotations += f"\n@IdClass({entity}Id.class)"
            extra["backend/DPS/" + entity + "Id.java"] = id_source(package, entity + "Id", {fields[k]: types[k] for k in keys})
        declarations = []
        for column, field in fields.items():
            data = columns[column]
            attrs = [f'name = "{column}"', "nullable = " + str(data.get("nullable", True)).lower()]
            length = data.get("length", data.get("char_length", data.get("data_length")))
            if types[column] == "String" and type(length) is int and length > 0:
                attrs.append("length = " + str(length))
            if data.get("data_type") == "NUMBER" and type(data.get("precision")) is int and data["precision"] <= 38:
                attrs.append("precision = " + str(data["precision"]))
                if type(data.get("scale")) is int: attrs.append("scale = " + str(data["scale"]))
            if str(data.get("data_type", "")).upper() in {"BLOB", "CLOB", "NCLOB"}:
                imports.add(namespace + ".persistence.Lob")
                declarations.append("    @Lob")
            if column in keys: declarations.append("    @Id")
            declarations += ["    @Column(" + ", ".join(attrs) + ")", f"    private {types[column]} {field};", ""]
        entity_source = preamble(package, imports) + "// Forms migrator JPA entity\n" + annotations + f"""
public class {entity} {{
{chr(10).join(declarations)}
    public {entity}() {{}}

{accessors({fields[c]: types[c] for c in columns})}
}}
"""
        repo_source = preamble(package, [namespace + ".persistence.EntityManager", namespace + ".persistence.PersistenceContext",
                                         "org.springframework.stereotype.Repository"]) + f"""// Forms migrator JPA repository
@Repository
public class {repository} {{
    @PersistenceContext
    private EntityManager entityManager;
}}
"""
        files = {"backend/DPS/" + entity + ".java": entity_source, "backend/DPS/" + repository + ".java": repo_source, **extra}
        for path in files:
            target = engine.safe_file(output, path)
            if target.is_file():
                existing = target.read_text(encoding="utf-8")
                marker = "Forms migrator JPA " + ("repository" if path.endswith("Repository.java") else "composite key" if path.endswith("Id.java") else "entity")
                if marker not in existing:
                    raise MigrationError("Eltérő, már létező osztályt nem írunk felül: " + path)
                files[path] = existing
        result.append({"table": table, "entity": entity, "repository": repository,
            "variable": name(repository), "package": package, "namespace": namespace,
            "fields": {fields[c]: types[c] for c in columns}, "primary_key": [fields[k] for k in keys],
            "columns": {c: fields[c] for c in columns},
            "entity_api": files["backend/DPS/" + entity + ".java"],
            "existing_repository": files["backend/DPS/" + repository + ".java"], "files": files})
    return result


def validate_members(source: str):
    """Repository additions: complete methods and static final constants only."""
    mask = strip_annotations(java_mask(source))
    if re.search(r"\b(?:class|interface|enum|record|package|import)\s+", mask):
        raise MigrationError("A repository nem hozhat létre osztályt vagy Controllert.")
    rest = mask.strip()
    while rest:
        constant = re.match(r"(?:(?:public|private|protected)\s+)?(?:static\s+final|final\s+static)\s+[\w.$<>?, \[\]]+\s+\w+\s*=[^;{}]*;", rest)
        if constant:
            rest = rest[constant.end():].strip()
            continue
        match = re.match(r"(?:public|private|protected)\s+(?:(?:static|final|synchronized)\s+)*(?:<[^;{}()]*>\s*)?[\w.$<>?, \[\]]+\s+\w+\s*\([^{};]*\)\s*(?:throws\s+[\w., ]+)?\s*\{", rest)
        if not match: raise MigrationError("A repository csak teljes Java-metódusokat adhat, osztályt vagy mezőt nem.")
        rest = rest[engine.matching_brace(rest, match.end() - 1) + 1:].strip()


def check_code(source: str):
    """Security and data-access rules on code (comments and strings do not count)."""
    mask = java_mask(without_sequence_queries(source))
    if (r"\u" in source or chr(96) * 3 in source
            or re.search(r"\b(Runtime|ProcessBuilder|DriverManager|ScriptEngineManager|ClassLoader)\b|java\s*\.\s*(?:net|io|nio|lang\s*\.\s*reflect)\b", mask)):
        raise MigrationError("A kód nem megengedett Java API-t vagy kódformát tartalmaz.")
    if re.search(r"\b(?:jdbcTemplate|NamedParameterJdbcTemplate|JdbcTemplate|createNativeQuery|createStoredProcedureQuery|prepareStatement|prepareCall|DriverManager|DbCalls)\b|(?<![\w$.])jdbc\s*\.", mask):
        raise MigrationError("Az adatbázis-műveletek entity-alapú repositoryba tartoznak, natív SQL/JDBC helyett.")


def validate_extras(response: dict):
    repositories, dtos = response.get("repositories", []), response.get("dtos", [])
    if not isinstance(repositories, list) or len(repositories) > 50 or not isinstance(dtos, list) or len(dtos) > 30:
        raise MigrationError("Hibás repository/DTO lista.")
    names = set()
    for entry in repositories:
        if (not isinstance(entry, dict) or set(entry) != {"name", "methods", "imports"}
                or not isinstance(entry["name"], str) or not engine.JAVA_NAME.fullmatch(entry["name"])
                or entry["name"] in names or not isinstance(entry["methods"], str)
                or len(entry["methods"]) > 80000):
            raise MigrationError("Hibás repository-javaslat.")
        names.add(entry["name"])
        if not isinstance(entry["imports"], list) or len(entry["imports"]) > 100:
            raise MigrationError("Hibás repository-importok.")
        for imp in entry["imports"]:
            if not isinstance(imp, str) or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_$][\w$]*)+", imp):
                raise MigrationError("Hibás repository-import.")
        check_code(entry["methods"] + "\n" + "\n".join(entry["imports"]))
        validate_members(entry["methods"])
    names = set()
    for dto in dtos:
        if (not isinstance(dto, dict) or set(dto) != {"name", "fields"} or not isinstance(dto["name"], str)
                or not engine.JAVA_NAME.fullmatch(dto["name"]) or not dto["name"].endswith("Dto")
                or dto["name"] in names or not isinstance(dto["fields"], list) or not 1 <= len(dto["fields"]) <= 100):
            raise MigrationError("Hibás DTO-javaslat.")
        names.add(dto["name"])
        used = set()
        for field in dto["fields"]:
            if (not isinstance(field, dict) or set(field) != {"name", "type"}
                    or not isinstance(field["name"], str) or not engine.JAVA_NAME.fullmatch(field["name"])
                    or field["name"] in RESERVED or field["name"] in used or not valid_dto_type(field["type"])):
                raise MigrationError("Hibás DTO-mező vagy típus.")
            used.add(field["name"])


def valid_dto_type(value):
    return isinstance(value, str) and bool(re.fullmatch(
        r"(?:String|Boolean|Integer|Long|Double|Float|java\.math\.BigDecimal|java\.time\.(?:LocalDateTime|LocalDate|OffsetDateTime)|byte\[\])", value))


def members_of(source: str) -> set[str]:
    """Method and field names declared in the classes of a Java source."""
    mask = java_mask(source)
    names = set()
    for member in scan_members(source, mask):
        if member.kind == "type" and member.body:
            names |= {m.name for m in scan_members(source, mask, member.body[0] + 1, member.body[1]) if m.kind in {"method", "field"}}
    return names


def add_imports(source: str, imports) -> str:
    for imp in sorted(set(imports)):
        if "import " + imp + ";" in source: continue
        match = re.search(r"^\s*package\s+[\w.]+\s*;\s*\n", source, re.M)
        at = match.end() if match else 0
        source = source[:at] + "import " + imp + ";\n" + source[at:]
    return source


def render_files(output: Path, packet: dict, response: dict, service: str, persistence: list[dict]) -> dict:
    check_code(response["body"] + "\n" + response["private_methods"] + "\n" + "\n".join(response["imports"]))
    if re.search(r"\b(?:entityManager|EntityManager|Session|SessionFactory)\b", engine.java_mask(response["body"] + response["private_methods"])):
        raise MigrationError("A service a repositoryt hívja; az EntityManager csak a repositoryban használható.")
    by_name = {p["repository"]: p for p in persistence}
    additions = {r["name"]: r for r in response.get("repositories", [])}
    if set(additions) - set(by_name): raise MigrationError("A modell ismeretlen repositoryt kért.")
    imports_map = dict(engine.java_tidy_known())
    imports_map.update(packet.get("formatting_imports") or {})
    files = {}
    for descriptor in persistence:
        files.update(descriptor["files"])
        entry = additions.get(descriptor["repository"])
        path = "backend/DPS/" + descriptor["repository"] + ".java"
        if entry and entry["methods"].strip():
            check_code(entry["methods"] + "\n" + "\n".join(entry["imports"]))
            source = files[path]
            old_names = members_of(source)
            new_members = [m for m in scan_members(entry["methods"], java_mask(entry["methods"])) if m.kind in {"method", "field"}]
            headers = [" ".join(java_mask(entry["methods"])[m.start:m.body[0] if m.body else m.end].split()) for m in new_members]
            if len(set(headers)) != len(headers) or old_names.intersection(m.name for m in new_members):
                raise MigrationError("A repository metódusneve ütközik; használd a meglévőt, vagy adj egyedi nevet.")
            at = engine.java_mask(source).rfind("}")
            source = source[:at] + "\n" + "\n".join("    " + line if line else "" for line in entry["methods"].strip().splitlines()) + "\n" + source[at:]
            source = add_imports(source, entry["imports"])
            files[path] = engine.ensure_imports(source, {**imports_map, **persistence_imports(descriptor["namespace"])})
    dto_imports = []
    module_class = name(packet["module"], "pascal")
    contract_sources = [s for s in packet["contracts"].values() if isinstance(s, str)]
    cl_package = next((package_of(s) for s in contract_sources if package_of(s)), "")
    for dto in response.get("dtos", []):
        cls = dto["name"]
        if not cls.startswith(module_class): raise MigrationError("Az új DTO neve a modul nevével kezdődjön: " + module_class)
        fields = {f["name"]: f["type"] for f in dto["fields"]}
        declarations = "\n".join(f"    private {typ} {field};" for field, typ in fields.items())
        args = ", ".join(typ + " " + field for field, typ in fields.items())
        assign = "\n".join(f"        this.{field} = {field};" for field in fields)
        source = preamble(cl_package) + f"""// Forms migrator supplemental DTO
public class {cls} {{
{declarations}

    public {cls}() {{}}

    public {cls}({args}) {{
{assign}
    }}

{accessors(fields)}
}}
"""
        path = "backend/CL/" + cls + ".java"
        if engine.safe_file(output, path).exists():
            raise MigrationError("Már létező DTO-t nem írunk felül: " + cls)
        files[path] = source
        if cl_package: dto_imports.append(cl_package + "." + cls)
    for path in list(files):
        if path.endswith("Repository.java"):
            files[path] = add_imports(files[path], dto_imports)
    rendered = engine.render_candidate(service, packet, response)
    rendered = rendered.replace("SQL/PLSQL and private helpers are kept here; no domain/repository layer.",
                                "AI-converted triggers use entity-based repositories; other methods are preserved.")
    rendered = add_imports(rendered, dto_imports)
    body_mask = engine.java_mask(response["body"] + "\n" + response["private_methods"])
    injections = []
    for descriptor in persistence:
        variable, repository = descriptor["variable"], descriptor["repository"]
        if not re.search(r"\b" + re.escape(variable) + r"\b", body_mask): continue
        mask = engine.java_mask(rendered)
        field = re.search(r"\b(?:private|protected|public)\s+(?:final\s+)?" + re.escape(repository) + r"\s+" + re.escape(variable) + r"\b", mask)
        if not field:
            if re.search(r"\b" + re.escape(variable) + r"\s*(?:[;=,])", engine.java_mask(service)):
                raise MigrationError("A repository mezőneve ütközik: " + variable)
            injections.append("    @org.springframework.beans.factory.annotation.Autowired\n    private " + repository + " " + variable + ";\n")
    if injections:
        match = re.search(r"\bpublic\s+class\s+\w+[^{]*\{", engine.java_mask(rendered))
        if not match: raise MigrationError("A DPS ServiceImpl osztálya nem található.")
        rendered = rendered[:match.end()] + "\n" + "\n".join(injections) + rendered[match.end():]
    files[packet["target"]] = rendered
    return files


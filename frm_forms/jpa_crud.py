"""Per table a Domain entity and an empty JpaRepository (backend_trigger_mode=ai), for the developer.

The generation writes the Domain of every block table (its columns, or the table's reviewed structure) and an empty
Repository (`interface RendelesRepository extends JpaRepository<RendelesDomain, BigDecimal> {}`, the company pattern):
the Java assistant writes the service's SQL itself (ai_frames), the developer may move a query into the repository
later. A structure saved on the Adatbázistáblák panel gives its table the same pair (write_schema_entity).
A table without a primary key gets no Domain (rules.NO_KEY): the block has no data endpoint until the schema names one.
One Domain and one Repository per table, named after the table (RENDELES_TETEL -> RendelesTetelDomain,
RendelesTetelRepository); the blocks of one table share them.
"""
from __future__ import annotations

import re

from .backend_queries import domain_field
from .common import MigrationError, name

JAVA_TYPES = {"text": "String", "number": "BigDecimal", "datetime": "LocalDateTime", "unsupported": "String"}
MARKER = "Forms migrator JPA"  # the first comment line of a generated Domain / Repository (project_deploy)


def table_class(table: str) -> str:
    return name(table.rsplit(".", 1)[-1], "pascal")


def accessor(field: str) -> str:
    return field[0].upper() + field[1:]


def ai_mode(model: dict) -> bool:
    return (model.get("options") or {}).get("backend_trigger_mode") == "ai"


def plan(blocks: list[dict], schema_tables: dict | None = None) -> dict[str, dict]:
    """table -> Domain/Repository plan of the served blocks (the union of their columns, the shared key).

    With the table's reviewed structure (schema.json tables: data dictionary, Adatbázistáblák panel) the Domain maps
    every column of the table, typed by the database; without it, the columns of the blocks."""
    tables: dict[str, dict] = {}
    for b in blocks:
        table = b["table"].upper()
        if not b["pk"]:
            raise MigrationError(b["name"] + ": JPA Domain kulcs nélkül nem készül.")
        entry = tables.setdefault(table, {"table": table, "class": table_class(table), "columns": {}, "key": None,
                                          "blocks": []})
        for item in b["db_items"]:
            column = item["column"].upper()
            typ = JAVA_TYPES[item["type"]]
            known = entry["columns"].setdefault(column, {"field": domain_field(column), "type": typ})
            if known["type"] != typ:
                raise MigrationError(f"{table}.{column}: a blokkok eltérő típussal használják ({known['type']}, {typ}).")
        key = [i["column"].upper() for i in b["pk"]]
        if entry["key"] not in (None, key):
            raise MigrationError(table + ": a blokkok eltérő elsődleges kulcsot adnak meg.")
        entry["key"] = key
        entry["blocks"].append(b["name"])
        add_schema_columns(entry, schema_tables or {})
    classes: dict[str, str] = {}
    for table, entry in tables.items():
        fields = [c["field"] for c in entry["columns"].values()]
        if len(set(fields)) != len(fields):
            raise MigrationError(table + ": két oszlop ugyanarra a Java-mezőnévre képeződik.")
        if entry["class"] in classes:  # same table name in two schemas
            entry["class"] = name(table.replace(".", "_"), "pascal")
        classes[entry["class"]] = table
        entry["domain"] = entry["class"] + "Domain"
        entry["repository"] = entry["class"] + "Repository"
        entry["variable"] = name(entry["class"]) + "Repository"
        key_types = [entry["columns"][k]["type"] for k in entry["key"]]
        entry["id_type"] = key_types[0] if len(key_types) == 1 else entry["domain"] + ".Key"
    return tables


def add_schema_columns(entry: dict, schema_tables: dict) -> None:
    """The table's columns the blocks do not show, from its reviewed structure (a type JPA cannot map is left out)."""
    from .rules import dictionary_table
    spec = dictionary_table(schema_tables, entry["table"])
    if not isinstance(spec, dict) or not isinstance(spec.get("columns"), dict):
        return
    from .jpa_assistant import java_type
    taken = {c["field"] for c in entry["columns"].values()}
    for column, definition in spec["columns"].items():
        column = str(column).upper()
        if column in entry["columns"] or not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", column):
            continue
        try:
            typ = java_type(definition if isinstance(definition, dict) else {})
        except MigrationError:
            continue
        typ = {"java.math.BigDecimal": "BigDecimal", "java.time.LocalDateTime": "LocalDateTime",
               "java.time.OffsetDateTime": "OffsetDateTime"}.get(typ, typ)
        if typ not in {"String", "BigDecimal", "LocalDateTime"} or domain_field(column) in taken:
            continue
        lob = str((definition or {}).get("data_type", "")).upper() in {"CLOB", "NCLOB"}
        entry["columns"][column] = {"field": domain_field(column), "type": typ, **({"lob": True} if lob else {})}
        taken.add(domain_field(column))


def bind_names(text: str, binds: list[dict]) -> str:
    """:q0, :q1 ... (backend_queries) -> the readable parameter names (the criteria fields); never inside a literal."""
    names = {":" + b["parameter"]: ":" + b["field"] for b in binds}
    return re.sub(r"'(?:[^']|'')*'|:q\d+\b", lambda m: names.get(m[0], m[0]), text)


def domain_source(entry: dict, package: str, namespace: str) -> str:
    keys = entry["key"]
    composite = len(keys) > 1
    types = {c["type"] for c in entry["columns"].values()}
    imports = [namespace + ".persistence." + n for n in ("Column", "Entity", "Id", "Table")]
    imports += ["java.math.BigDecimal"] if "BigDecimal" in types else []
    imports += ["java.time.LocalDateTime"] if "LocalDateTime" in types else []
    if composite:
        imports += [namespace + ".persistence.IdClass", "java.io.Serializable", "java.util.Objects"]
    if any(c.get("lob") for c in entry["columns"].values()):
        imports.append(namespace + ".persistence.Lob")
    table = entry["table"]
    annotation = f'@Table(name = "{table.rsplit(".", 1)[-1]}"' + (f', schema = "{table.split(".")[0]}"' if "." in table else "") + ")"
    lines = [f"// {MARKER} domain: {table} (Forms-blokkok: {', '.join(entry['blocks'])}).", "@Entity", annotation]
    if composite:
        lines.append(f"@IdClass({entry['domain']}.Key.class)")
    lines.append(f"public class {entry['domain']} {{")
    for column, spec in entry["columns"].items():
        lines += [""] + (["    @Id"] if column in keys else []) + (["    @Lob"] if spec.get("lob") else []) + [
            f'    @Column(name = "{column}")', f"    private {spec['type']} {spec['field']};"]
    for spec in entry["columns"].values():
        field, typ = spec["field"], spec["type"]
        lines += ["", f"    public {typ} get{accessor(field)}() {{", f"        return {field};", "    }",
                  "", f"    public void set{accessor(field)}({typ} {field}) {{", f"        this.{field} = {field};", "    }"]
    if composite:
        key_fields = [(entry["columns"][k]["type"], entry["columns"][k]["field"]) for k in keys]
        lines += ["", "    /** The composite primary key (JpaRepository id type). */",
                  "    public static class Key implements Serializable {", "        private static final long serialVersionUID = 1L;"]
        lines += [f"        private {t} {f};" for t, f in key_fields]
        lines += ["", "        public Key() {", "        }", "",
                  "        public Key(" + ", ".join(f"{t} {f}" for t, f in key_fields) + ") {"]
        lines += [f"            this.{f} = {f};" for _, f in key_fields]
        lines += ["        }", "", "        @Override", "        public boolean equals(Object other) {",
                  "            if (this == other) {", "                return true;", "            }",
                  "            if (!(other instanceof Key)) {", "                return false;", "            }",
                  "            Key key = (Key) other;",
                  # a NUMBER key: 1 and 1.0 are the same row (BigDecimal.equals would tell them apart)
                  "            return " + " && ".join(
                      f"({f} == null ? key.{f} == null : key.{f} != null && {f}.compareTo(key.{f}) == 0)" if t == "BigDecimal"
                      else f"Objects.equals({f}, key.{f})" for t, f in key_fields) + ";",
                  "        }", "", "        @Override", "        public int hashCode() {",
                  "            return Objects.hash(" + ", ".join(f"{f} == null ? null : {f}.stripTrailingZeros()" if t == "BigDecimal"
                                                                else f for t, f in key_fields) + ");", "        }", "    }"]
    lines.append("}")
    return preamble(package, imports) + "\n".join(lines) + "\n"


def empty_repository_source(entry: dict, package: str, note: bool = True) -> str:
    """The table's repository without methods: the service runs its own SQL (written by the Java assistant); the
    developer may move a query here as a @Query method (nativeQuery = true keeps the Oracle SQL as written).
    note=False: the jpa data access of the assistant adds its methods to it (jpa_assistant)."""
    imports = ["org.springframework.data.jpa.repository.JpaRepository"]
    imports += ["java.math.BigDecimal"] if entry["id_type"] == "BigDecimal" else []
    imports += ["java.time.LocalDateTime"] if entry["id_type"] == "LocalDateTime" else []
    lines = [f"// {MARKER} repository: {entry['table']}."]
    if note:
        lines += ["// Üres: a service maga futtatja az SQL-jét (EntityManager). Ide tehető át egy lekérdezés @Query metódusként,",
                  '// például @Query(value = "<az eredeti SQL>", nativeQuery = true) List<Object[]> findX(@Param("x") String x);']
    lines += [f"public interface {entry['repository']} extends JpaRepository<{entry['domain']}, {entry['id_type']}> {{", "}"]
    return preamble(package, imports) + "\n".join(lines) + "\n"


def preamble(package: str, imports) -> str:
    return ("package " + package + ";\n\n" if package else "") + "".join(
        "import " + value + ";\n" for value in sorted(set(imports))) + "\n"


def write_files(tables: dict, output, package: str, namespace: str) -> list[str]:
    """backend/DPS/<Table>Domain.java and <Table>Repository.java of every table."""
    from .java_style import layout
    from .java_tidy import tidy, wrap_strings
    written = []
    for entry in tables.values():
        for cls, source in ((entry["domain"], domain_source(entry, package, namespace)),
                            (entry["repository"], empty_repository_source(entry, package))):
            filename = cls + ".java"
            # The javax/jakarta imports stay: jpa_namespace says which one the project has.
            text = wrap_strings(layout(tidy(source, filename, preserve_explicit=True), filename))
            path = output / "backend" / "DPS" / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            written.append(filename)
    # For the Java assistant (jpa_assistant.plan): which tables have a Domain, and the columns it maps.
    from .common import write_json
    write_json(output / "analysis" / "jpa-domains.json", {"version": 1, "tables": {
        table: {k: entry[k] for k in ("domain", "repository", "variable", "key", "id_type")}
        | {"columns": {c: dict(spec) for c, spec in entry["columns"].items()}} for table, entry in tables.items()}})
    return written


def write_schema_entity(output, table: str, definition: dict) -> list[str]:
    """A saved table structure (Adatbázistáblák panel) as its Domain entity and an empty Repository (AI mode).

    Deterministic, for the developer: the Domain maps every column of the structure with its key; the Repository is
    written only when there is none yet (the developer may have moved queries into it). A table without a primary key
    gets nothing (an entity needs its @Id); a project class of the same name (no generator marker) is never touched."""
    from .common import read_json, write_json
    from .java_imports import layer_package
    from .jpa_assistant import java_type
    effective = read_json(output / "analysis" / "effective-config.json")
    config = effective.get("config", {})
    if config.get("backend_trigger_mode", "ai") != "ai":
        return []
    columns = definition.get("columns") or {}
    keys = [str(k).upper() for k in definition.get("primary_key") or []]
    if not columns or not keys or not all(k in {str(c).upper() for c in columns} for k in keys):
        return []
    simple = {"java.math.BigDecimal": "BigDecimal", "java.time.LocalDateTime": "LocalDateTime"}
    entry = {"table": table, "class": table_class(table), "key": keys, "columns": {}}
    for column, spec in columns.items():
        column = str(column).upper()
        try:
            typ = simple.get(java_type(spec), java_type(spec))
        except MigrationError:
            if column in keys:
                return []
            continue
        if typ not in {"String", "BigDecimal", "LocalDateTime"}:
            if column in keys:
                return []
            continue
        lob = str(spec.get("data_type", "")).upper() in {"CLOB", "NCLOB"}
        entry["columns"][column] = {"field": domain_field(column), "type": typ, **({"lob": True} if lob else {})}
    if any(k not in entry["columns"] for k in keys) or len({c["field"] for c in entry["columns"].values()}) != len(entry["columns"]):
        return []
    ir = output / "analysis" / "form.ir.json"
    blocks = [b["name"] for b in read_json(ir).get("blocks", []) if b.get("database")
              and str(b.get("table") or "").upper() == table] if ir.is_file() else []
    entry["blocks"] = blocks or ["(trigger)"]
    entry["domain"], entry["repository"] = entry["class"] + "Domain", entry["class"] + "Repository"
    entry["variable"] = name(entry["class"]) + "Repository"
    key_types = [entry["columns"][k]["type"] for k in keys]
    entry["id_type"] = key_types[0] if len(key_types) == 1 else entry["domain"] + ".Key"
    package = layer_package(config, effective.get("package", ""), "DPS")
    namespace = config.get("jpa_namespace") or "javax"
    from .java_style import layout
    from .java_tidy import tidy, wrap_strings
    written = []
    for cls, source, keep in ((entry["domain"], domain_source(entry, package, namespace), False),
                              (entry["repository"], empty_repository_source(entry, package), True)):
        path = output / "backend" / "DPS" / (cls + ".java")
        if path.is_file() and (keep or MARKER not in path.read_text(encoding="utf-8")):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(wrap_strings(layout(tidy(source, path.name, preserve_explicit=True), path.name)),
                        encoding="utf-8", newline="\n")
        written.append(path.name)
    known_file = output / "analysis" / "jpa-domains.json"
    known = read_json(known_file) if known_file.is_file() else {"version": 1, "tables": {}}
    known.setdefault("tables", {})[table] = ({k: entry[k] for k in ("domain", "repository", "variable", "key", "id_type")}
                                            | {"columns": {c: dict(spec) for c, spec in entry["columns"].items()}})
    write_json(known_file, known)
    return written



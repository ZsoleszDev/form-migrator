"""Block CRUD over Spring Data JPA (backend_trigger_mode=ai): per table a Domain entity and a JpaRepository.

The company pattern: `interface RendelesRepository extends JpaRepository<RendelesDomain, BigDecimal>` with @Query
methods, called by the DPS ServiceImpl through an @Autowired field. The service holds no SQL and no JDBC:
- the block query (Forms DEFAULT_WHERE + ORDER BY) is a JPQL @Query over the Domain (backend_queries compiles it); a
  clause outside the JPQL subset runs as written, as a native @Query of the same repository;
- the record lock before an update or delete is @Lock(PESSIMISTIC_WRITE) with lock timeout 0, the Forms
  FOR UPDATE NOWAIT: a record another session holds is HTTP 409 at once (FormsErrors);
- insert, update and delete are save/delete of the Domain; a sequence key is a native NEXTVAL query.
A table without a primary key gets no Domain (rules.NO_KEY): the block has no data endpoint until the schema names one.
One Domain and one Repository per table, named after the table (RENDELES_TETEL -> RendelesTetelDomain,
RendelesTetelRepository); the blocks of one table share them, each with its own query method.
"""
from __future__ import annotations

import re

from .backend_queries import domain_field
from .common import MigrationError, jstr, name

JAVA_TYPES = {"text": "String", "number": "BigDecimal", "datetime": "LocalDateTime", "unsupported": "String"}
MARKER = "Forms migrator JPA"  # the first comment line of a generated Domain / Repository (project_deploy)


def table_class(table: str) -> str:
    return name(table.rsplit(".", 1)[-1], "pascal")


def accessor(field: str) -> str:
    return field[0].upper() + field[1:]


def ai_mode(model: dict) -> bool:
    return (model.get("options") or {}).get("backend_trigger_mode") == "ai"


def plan(blocks: list[dict]) -> dict[str, dict]:
    """table -> Domain/Repository plan of the served blocks (the union of their columns, the shared key)."""
    tables: dict[str, dict] = {}
    for b in blocks:
        table = b["table"].upper()
        if not b["pk"]:
            raise MigrationError(b["name"] + ": JPA Domain kulcs nélkül nem készül.")
        entry = tables.setdefault(table, {"table": table, "class": table_class(table), "columns": {}, "key": None,
                                          "blocks": [], "sequences": {}})
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
        if b.get("sequence"):
            entry["sequences"][b["sequence"].upper()] = "next" + name(b["sequence"].rsplit(".", 1)[-1], "pascal")
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
        entry["queries"] = []
    return tables


def bind_names(text: str, binds: list[dict]) -> str:
    """:q0, :q1 ... (backend_queries) -> the readable parameter names (the criteria fields); never inside a literal."""
    names = {":" + b["parameter"]: ":" + b["field"] for b in binds}
    return re.sub(r"'(?:[^']|'')*'|:q\d+\b", lambda m: names.get(m[0], m[0]), text)


def block_query(b: dict, entry: dict) -> dict:
    """The block's list or search method of the repository: JPQL when the clause compiles to it, else native SQL."""
    query = b.get("query_plan") or {}
    binds = query.get("binds", []) if query.get("status") == "compiled" else []
    search = bool(binds)
    method = ("search" if search else "list") + b["class"]
    alias = "d"
    key_order = ", ".join(f"{alias}.{entry['columns'][k]['field']}" for k in entry["key"])
    # backend_queries sets where_jpql / order_jpql together, only when both compile to JPQL
    native = query.get("status") == "compiled" and "where_jpql" not in query and bool(query.get("where_sql") or query.get("order_sql"))
    if native:
        from .generate import table_alias
        sql = "SELECT * FROM " + b["table"] + table_alias(b, b["table"])
        if query["where_sql"]:
            sql += " WHERE " + query["where_sql"]
        sql += " ORDER BY " + (query["order_sql"] or ", ".join(entry["key"]))
        text, kind = bind_names(sql, binds), "native"
    else:
        jpql = f"SELECT {alias} FROM {entry['domain']} {alias}"
        if query.get("where_jpql"):
            jpql += " WHERE " + query["where_jpql"]
        jpql += " ORDER BY " + (query.get("order_jpql") or key_order)
        text, kind = bind_names(jpql, binds), "jpql"
    params = [(JAVA_TYPES[v["item"]["type"]], v["field"]) for v in binds]
    reason = query.get("jpql_reason") or query.get("closed_reason") or ""
    return {"block": b["name"], "method": method, "kind": kind, "text": text, "params": params, "binds": binds,
            "search": search, "reason": reason}


def domain_source(entry: dict, package: str, namespace: str) -> str:
    keys = entry["key"]
    composite = len(keys) > 1
    types = {c["type"] for c in entry["columns"].values()}
    imports = [namespace + ".persistence." + n for n in ("Column", "Entity", "Id", "Table")]
    imports += ["java.math.BigDecimal"] if "BigDecimal" in types else []
    imports += ["java.time.LocalDateTime"] if "LocalDateTime" in types else []
    if composite:
        imports += [namespace + ".persistence.IdClass", "java.io.Serializable", "java.util.Objects"]
    table = entry["table"]
    annotation = f'@Table(name = "{table.rsplit(".", 1)[-1]}"' + (f', schema = "{table.split(".")[0]}"' if "." in table else "") + ")"
    lines = [f"// {MARKER} domain: {table} (Forms-blokkok: {', '.join(entry['blocks'])}).", "@Entity", annotation]
    if composite:
        lines.append(f"@IdClass({entry['domain']}.Key.class)")
    lines.append(f"public class {entry['domain']} {{")
    for column, spec in entry["columns"].items():
        lines += [""] + (["    @Id"] if column in keys else []) + [f'    @Column(name = "{column}")',
                                                                     f"    private {spec['type']} {spec['field']};"]
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
                  "            return " + " && ".join(f"Objects.equals({f}, key.{f})" for _, f in key_fields) + ";",
                  "        }", "", "        @Override", "        public int hashCode() {",
                  "            return Objects.hash(" + ", ".join(f for _, f in key_fields) + ");", "        }", "    }"]
    lines.append("}")
    return preamble(package, imports) + "\n".join(lines) + "\n"


def repository_source(entry: dict, package: str, namespace: str) -> str:
    key_fields = [(entry["columns"][k]["type"], entry["columns"][k]["field"]) for k in entry["key"]]
    types = {t for t, _ in key_fields} | {t for q in entry["queries"] for t, _ in q["params"]}
    imports = ["java.util.Optional", namespace + ".persistence.LockModeType", namespace + ".persistence.QueryHint",
               "org.springframework.data.jpa.repository.JpaRepository", "org.springframework.data.jpa.repository.Lock",
               "org.springframework.data.jpa.repository.Query", "org.springframework.data.jpa.repository.QueryHints",
               "org.springframework.data.repository.query.Param"]
    imports += ["java.math.BigDecimal"] if "BigDecimal" in types or entry["sequences"] else []
    imports += ["java.time.LocalDateTime"] if "LocalDateTime" in types else []
    if entry["queries"]:
        imports += ["org.springframework.data.domain.Pageable", "org.springframework.data.domain.Slice"]
    domain = entry["domain"]
    lines = [f"// {MARKER} repository: {entry['table']}.",
             f"public interface {entry['repository']} extends JpaRepository<{domain}, {entry['id_type']}> {{"]
    for q in entry["queries"]:
        what = "keresése (a Forms-lekérdezés WHERE és ORDER BY része)" if q["search"] else "listája (Forms ORDER BY / DEFAULT_WHERE)"
        lines += ["", f"    // {q['block']} blokk {what}."]
        if q["kind"] == "native":
            lines.append(f"    // Natív SQL: JPQL-ben nem írható le ({q['reason'] or 'Oracle-specifikus feltétel'}).")
            lines.append(f"    @Query(value = {jstr(q['text'])}, nativeQuery = true)")
        else:
            lines.append(f"    @Query({jstr(q['text'])})")
        params = [f'@Param("{f}") {t} {f}' for t, f in q["params"]] + ["Pageable pageable"]
        lines.append(f"    Slice<{domain}> {q['method']}({', '.join(params)});")
    hint = ("javax" if namespace == "javax" else "jakarta") + ".persistence.lock.timeout"
    condition = " AND ".join(f"d.{f} = :{f}" for _, f in key_fields)
    lines += ["", "    // A rekord zárolása módosítás és törlés előtt; ha más tartja, azonnal hiba (Forms: FOR UPDATE NOWAIT).",
              "    @Lock(LockModeType.PESSIMISTIC_WRITE)",
              f'    @QueryHints(@QueryHint(name = "{hint}", value = "0"))',
              f"    @Query({jstr(f'SELECT d FROM {domain} d WHERE {condition}')})",
              f"    Optional<{domain}> lockById(" + ", ".join(f'@Param("{f}") {t} {f}' for t, f in key_fields) + ");"]
    for sequence, method in entry["sequences"].items():
        lines += ["", f"    // Az új rekord kulcsa (Forms: {sequence}.NEXTVAL).",
                  f"    @Query(value = {jstr('SELECT ' + sequence + '.NEXTVAL FROM DUAL')}, nativeQuery = true)",
                  f"    BigDecimal {method}();"]
    lines.append("}")
    return preamble(package, imports) + "\n".join(lines) + "\n"


def preamble(package: str, imports) -> str:
    return ("package " + package + ";\n\n" if package else "") + "".join(
        "import " + value + ";\n" for value in sorted(set(imports))) + "\n"


def block_values(b: dict, entry: dict, query: dict | None) -> dict:
    """The JPA parts of CompactBlockJpa.java.tpl for one block (generate.block_values gives the rest)."""
    columns = entry["columns"]
    by_item = [(i, columns[i["column"].upper()]) for i in b["db_items"]]
    keys = [(i, columns[i["column"].upper()]) for i in b["pk"]]

    def key_of(var):
        values = ", ".join(f"{var}.{i['field']}" for i, _ in keys)
        return values if len(keys) == 1 else f"new {entry['domain']}.Key({values})"

    def check(var):
        return ("if (" + " || ".join(f"SqlValues.isNull({var}.{i['field']})" for i, _ in keys)
                + ') throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "Hiányzó elsődleges kulcs.");')
    inserts = [(i, c) for i, c in by_item if i["insert_allowed"] or i["primary_key"]]
    updates = [(i, c) for i, c in by_item if i["update_allowed"] and not i["primary_key"]]
    values = {"DOMAIN": entry["domain"], "REPOSITORY_CLASS": entry["repository"], "REPOSITORY": entry["variable"],
              "LOCK_METHOD": "lockById", "LOCK_ARGS": ", ".join(f"key.{i['field']}" for i, _ in keys),
              "ID_OF_KEY": key_of("key"), "ID_OF_ROW": key_of("row"), "KEY_CHECK": check("key"), "ROW_KEY_CHECK": check("row"),
              "MAPPING": "\n".join(f"        row.{i['field']} = domain.get{accessor(c['field'])}();" for i, c in by_item),
              "APPLY_INSERT": "\n".join(f"        domain.set{accessor(c['field'])}(row.{i['field']});" for i, c in inserts),
              "APPLY_UPDATE": "\n".join(f"        domain.set{accessor(c['field'])}(row.{i['field']});" for i, c in updates),
              "LIST_METHOD": query["method"] if query and not query["search"] else "listUnavailable",
              "SEQUENCE_METHOD": (f"public BigDecimal nextId() {{ return {entry['variable']}."
                                  f"{entry['sequences'][b['sequence'].upper()]}(); }}") if b.get("sequence") else ""}
    return values


def search_method(b: dict, query: dict, criteria_checks: str, repository: str) -> str:
    args = ", ".join(("SqlValues.normalize(criteria." + v["field"] + ")") if v["item"]["type"] == "text" else "criteria." + v["field"]
                     for v in query["binds"])
    return f'''        public PageResult<{b["class"]}Row> search(SearchRequest<{b["class"]}Criteria> request) {{
            guard("search");
            if (request == null || request.criteria() == null) throw bad("Hiányzó keresési feltételek.");
            if (request.offset() < 0 || request.offset() > 1000000 || request.limit() < 1 || request.limit() > 200
                    || request.offset() % request.limit() != 0)
                throw bad("offset: 0..1000000 (a limit többszöröse), limit: 1..200 szükséges.");
            var criteria = request.criteria();
            var errors = new ArrayList<String>();
{criteria_checks}
            if (!errors.isEmpty()) throw bad("Hibás keresési feltételek: " + String.join("; ", errors));
            var page = {repository}.{query["method"]}({args}{", " if args else ""}PageRequest.of(request.offset() / request.limit(), request.limit()));
            var rows = mapAll(page);
            var context = new RuleContext();
            for (var row : rows) postQuery(row, context);
            return new PageResult<>(rows, context.messages(), page.hasNext());  // hasNext: the page left rows out
        }}'''


def write_files(tables: dict, output, package: str, namespace: str) -> list[str]:
    """backend/DPS/<Table>Domain.java and <Table>Repository.java of every table."""
    from .java_style import layout
    from .java_tidy import tidy, wrap_strings
    written = []
    for entry in tables.values():
        for cls, source in ((entry["domain"], domain_source(entry, package, namespace)),
                            (entry["repository"], repository_source(entry, package, namespace))):
            filename = cls + ".java"
            # The javax/jakarta imports stay: jpa_namespace says which one the project has.
            text = wrap_strings(layout(tidy(source, filename, preserve_explicit=True), filename))
            path = output / "backend" / "DPS" / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            written.append(filename)
    return written


def service_fields(tables: dict) -> list[str]:
    return [f"    @Autowired\n    private {e['repository']} {e['variable']};" for e in tables.values()]

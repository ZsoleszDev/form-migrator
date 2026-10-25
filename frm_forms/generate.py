from __future__ import annotations

import json
from pathlib import Path
import re
from .common import MigrationError, jstr, name, write_json
from .plsql import parse, Unsupported

TEMPLATES = Path(__file__).parent / "templates"
JAVA_TYPES = {"text": "String", "number": "BigDecimal", "datetime": "LocalDateTime", "unsupported": "String"}
SQL_TYPES = {"text": "Types.VARCHAR", "number": "Types.NUMERIC", "datetime": "Types.TIMESTAMP", "unsupported": "Types.VARCHAR"}


def template(filename: str, values: dict, config: dict, sections: set | None = None) -> str:
    custom = Path(config["template_dir"]) / filename if config.get("template_dir") else None
    text = (custom if custom and custom.exists() else TEMPLATES / filename).read_text(encoding="utf-8")
    # Optional regions: @@BEGIN_X@@ ... @@END_X@@ lines. With sections given, only
    # the named regions stay (e.g. no create() for InsertAllowed=false blocks).
    def region(match):
        return match.group(2) if sections is None or match.group(1) in sections else ""
    text = re.sub(r"^[ \t]*@@BEGIN_([A-Z]+)@@[ \t]*\n(.*?)^[ \t]*@@END_\1@@[ \t]*\n", region, text, flags=re.S | re.M)
    def replace(match):
        key = match.group(1)
        if key not in values:
            raise MigrationError(f"Ismeretlen template-helyőrző: {filename}: {key}")
        return str(values[key])
    return re.sub(r"@@([A-Z_]+)@@", replace, text)


def write(path: Path, text: str):
    if path.suffix == ".java" and "backend" in path.parts:
        from .java_tidy import tidy
        from .java_style import layout
        text = tidy(text, path.name)  # simple type names; JDK/Spring/Jackson and java-imports.json imports
        text = layout(text, path.name)  # Checkstyle: braces, blank lines, import groups, UTF-8 literals
        from .java_tidy import wrap_strings
        text = wrap_strings(text)  # PMD line length: no SQL literal line reaches 120 characters
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def initial_values(model: dict) -> None:
    for b in model["blocks"]:
        for item in b["items"]:
            item["initial"] = None
            value = item["initial_value"]
            if not value:
                continue
            try:
                if item["type"] == "text" and not value.startswith(("'", ":")) and "$$" not in value:
                    item["initial"] = value
                else:
                    expr = parse(":" + b["name"] + "." + item["name"] + " := " + value + ";")[0]["value"]
                    # Keep defaults literal only: no server clock or arbitrary functions.
                    if expr["op"] == "unary" and expr["operator"] in {"+", "-"} and expr["value"].get("type") == "number":
                        expr = {"op": "literal", "type": "number", "value": ("-" if expr["operator"] == "-" else "") + expr["value"]["value"]}
                    if expr["op"] != "literal" or expr["type"] not in {item["type"], "null"}:
                        raise Unsupported("Nem literál alapérték.")
                    item["initial"] = expr["value"]
            except (Unsupported, KeyError):
                model["issues"].append({"code": "INITIAL_VALUE", "owner": b["name"], "scope": "write", "detail": f"{b['name']}.{item['name']}: dinamikus/nem támogatott InitialValue={value}"})


def row_declarations(fields):
    # Field rules (Required, MaximumLength, NUMBER precision) are checked in the service with the shared
    # FormsChecks (field_checks): no bean validation dependency. Jackson belongs to Spring web.
    declarations = []
    for item in fields:
        annotations = []
        if item["type"] == "number":
            annotations.append("@JsonFormat(shape = JsonFormat.Shape.STRING)")
        if item["type"] == "datetime":
            annotations.append('@JsonFormat(pattern = "yyyy-MM-dd\'T\'HH:mm:ss")')
        declarations.extend("    " + a for a in annotations)
        declarations.append("    public " + JAVA_TYPES[item["type"]] + " " + item["field"] + ";")

    return "\n".join(declarations)

def check_lines(entries) -> list[str]:
    """(item, accessor) pairs -> FormsChecks lines: Required, MaximumLength, NUMBER(p, s)."""
    lines = []
    for item, access in entries:
        label = json.dumps(item["name"])
        if item["required"]:
            lines.append(f"FormsChecks.required(errors, {label}, {access});")
        if item["type"] == "text" and item["max_length"]:
            lines.append(f"FormsChecks.maxLength(errors, {label}, {access}, {item['max_length']});")
        if item["type"] == "number" and item.get("precision"):
            lines.append(f"FormsChecks.digits(errors, {label}, {access}, {item['precision'] - item.get('scale', 0)}, {item.get('scale', 0)});")
    return lines


def field_checks(fields) -> str:
    """The DTO field rules of a record, checked with CommonMigrateTools.FormsChecks."""
    lines = check_lines((item, "row." + item["field"]) for item in fields)
    # The template line already indents the first check (like @@DOMAIN_CHECKS@@).
    return ("        " + "\n            ".join(lines)) if lines else ""


def converted_backend_triggers(model, b):
    return [t for t in model["triggers"] if t["block"] == b["name"] and t["status"] == "converted" and t["target"] == "backend"]


def rule_methods(model, b):
    cls = b["class"]
    trigger_methods = []
    # Forms order inside one record: item validation (POST-CHANGE, WHEN-VALIDATE-ITEM), then the record.
    event_methods = {"validateRules": ["POST-CHANGE", "WHEN-VALIDATE-ITEM", "WHEN-VALIDATE-RECORD"], "preInsert": ["PRE-INSERT"], "preUpdate": ["PRE-UPDATE"], "preDelete": ["PRE-DELETE"], "postQuery": ["POST-QUERY"],
                     "postInsert": ["POST-INSERT"], "postUpdate": ["POST-UPDATE"], "postDelete": ["POST-DELETE"]}
    relevant = converted_backend_triggers(model, b)
    # backend_trigger_mode=ai: a readable hook per trigger (triggerPostQuery, triggerWhenValidateItemMennyiseg ...),
    # its body the commented Forms code for the Java assistant (ai_triggers); the call sites are the same.
    from . import ai_triggers
    taken = set()
    callee = {id(tr): ai_triggers.hook_name(tr, taken) if tr.get("ai") else "trigger" + str(model["triggers"].index(tr))
              for tr in relevant}
    for method, events in event_methods.items():
        calls = []
        for event in events:
            for tr in relevant:
                if tr["event"] == event:
                    calls.append("        " + callee[id(tr)] + "(row, context);")
        if method == "postQuery":
            # POST-CHANGE also fires on fetch (Forms): only its variant that writes no queried item.
            calls[:0] = ["        trigger" + str(model["triggers"].index(tr)) + "Query(row, context);"
                         for tr in relevant if tr["event"] == "POST-CHANGE" and tr.get("java_query")]
            calls[:0] = ["        " + callee[id(tr)] + "(row, context);"
                         for tr in relevant if tr["event"] == "POST-CHANGE" and tr.get("ai") and tr.get("ai_on_query")]
        trigger_methods.append(f"    public void {method}({cls}Row row, RuleContext context) {{\n" + "\n".join(calls) + "\n    }")
    # ON-INSERT / ON-UPDATE / ON-DELETE replace the generated DML; ON-CHECK-DELETE-MASTER runs before
    # a delete. Generated only when the form has them, so the default DML stays otherwise.
    for event, method in [("ON-INSERT", "onInsert"), ("ON-UPDATE", "onUpdate"), ("ON-DELETE", "onDelete"),
                          ("ON-CHECK-DELETE-MASTER", "checkDeleteMaster")]:
        calls = ["        " + callee[id(tr)] + "(row, context);" for tr in relevant if tr["event"] == event]
        if calls:
            trigger_methods.append(f"    public void {method}({cls}Row row, RuleContext context) {{\n" + "\n".join(calls) + "\n    }")
    for tr in relevant:
        index = model["triggers"].index(tr)
        if tr.get("ai"):
            trigger_methods.append(f"    private void {callee[id(tr)]}({cls}Row row, RuleContext context) {{\n"
                                   + ai_triggers.hook_body(tr, model).rstrip("\n") + "\n    }")
            continue
        trigger_methods.append(f"    private void trigger{index}({cls}Row row, RuleContext context) {{\n{tr['java']}\n    }}")
        if tr.get("java_query"):
            trigger_methods.append(f"    private void trigger{index}Query({cls}Row row, RuleContext context) {{\n{tr['java_query']}\n    }}")

    return "\n\n".join(trigger_methods)


def dml_calls(model, b) -> dict:
    """The DML steps of the block operations: the generated SQL, or the form's own ON-* trigger."""
    events = {t["event"] for t in converted_backend_triggers(model, b)}
    return {"INSERT_CALL": "onInsert(row, context);" if "ON-INSERT" in events else "insertRow(row);",
            "UPDATE_CALL": "onUpdate(row, context);" if "ON-UPDATE" in events else "updateRow(row);",
            "DELETE_CALL": "onDelete(current, context);" if "ON-DELETE" in events else "deleteRow(original);",
            "DELETE_CHECKS": "checkDeleteMaster(current, context);\n            " if "ON-CHECK-DELETE-MASTER" in events else "",
            # Re-read after INSERT (database defaults and triggers); a ROWID-keyed block whose own ON-INSERT did
            # the DML has no ROWID to read it back by: the record stays as the trigger left it.
            "CREATED_ROW": "row" if "ON-INSERT" in events and b.get("rowid_item") else "loadRow(row, false)"}


def column_sql(item) -> str:
    """The select-list expression of a database item; the ROWID key travels as text."""
    return "ROWIDTOCHAR(ROWID)" if item.get("rowid") else item["column"]


def key_sql(item) -> str:
    """One key condition of the generated SELECT/UPDATE/DELETE (named parameter = DTO field)."""
    if item.get("rowid"):
        return "ROWID = CHARTOROWID(:" + item["field"] + ")"
    return item["column"] + " = :" + item["field"]


def table_alias(b, table) -> str:
    """Forms queries FROM table alias when the block has an Alias: its WHERE/ORDER BY may use it."""
    from .xmlmodel import get
    alias = get(b.get("properties", {}), "Alias").strip().upper()
    return " " + alias if alias and re.fullmatch(r"[A-Z][A-Z0-9_$#]*", alias) and alias != table.rsplit(".", 1)[-1] else ""


def insert_body(b, table, inserts, insert_sql, valid_sql) -> str:
    """insertRow: a plain INSERT, or for a ROWID-keyed block an INSERT that returns the new ROWID."""
    rowid = b.get("rowid_item")
    if not rowid or not inserts or not valid_sql:
        return ("int count = jdbc.update(" + jstr(insert_sql) + ', params(row));\n'
                '        if (count != 1) throw new ResponseStatusException(HttpStatus.CONFLICT, "Sikertelen beszúrás.");')
    sql = ("BEGIN INSERT INTO " + table + " (" + ", ".join(i["column"] for i in inserts) + ") VALUES ("
           + ", ".join("?" for _ in inserts) + ") RETURNING ROWIDTOCHAR(ROWID) INTO ?; END;")
    params = [f"DbCalls.in(row.{i['field']}, {SQL_TYPES[i['type']]})" for i in inserts] + ["DbCalls.out(Types.VARCHAR)"]
    return ("// Kulcs nélküli blokk: a Forms-hoz hasonlóan a ROWID azonosítja az új rekordot.\n"
            "        Object[] out = DbCalls.call(jdbc, " + jstr(sql) + ",\n            " + ",\n            ".join(params) + ");\n"
            f"        row.{rowid['field']} = (String) out[{len(inserts)}];")


def normalize_line(item) -> str:
    """Empty text is NULL (VARCHAR2); CaseRestriction=Upper/Lower converts it like the Forms item did."""
    field = item["field"]
    case = item.get("case_restriction")
    if case:
        return f"        row.{field} = SqlValues.{case}(row.{field});"
    return f"        row.{field} = SqlValues.normalize(row.{field});"


def block_values(model, b):
    cls = b["class"]
    fields = [i for i in b["items"] if i["kind"] != "button"]
    db = b["db_items"]
    pk = b["pk"]
    # Invalid mappings produce compilable but permanently guarded SQL placeholders.
    valid_sql = not any(i["code"] in {"SQL_MAPPING", "NO_COLUMNS"} and i["owner"] == b["name"] for i in model["issues"])
    table = b["table"] if valid_sql else "FRM_UNMAPPED_BLOCK"
    select = "SELECT " + (", ".join(column_sql(i) for i in db) if valid_sql else "1") + " FROM " + table + table_alias(b, table)
    where = " AND ".join(key_sql(i) for i in pk) if valid_sql and pk else "1 = 0"
    order = ", ".join(i["column"] for i in pk) if pk and valid_sql else (db[0]["column"] if db and valid_sql else "1")
    query = b.get('query_plan', {})
    filtered_select = select
    if query.get('status') == 'compiled':
        if query['where_sql']:
            filtered_select += ' WHERE ' + query['where_sql']
        order = query['order_sql'] or order
    inserts = [i for i in db if (i["insert_allowed"] or i["primary_key"]) and not i.get("rowid")]
    updates = [i for i in db if i["update_allowed"] and not i["primary_key"]]
    insert_sql = "INSERT INTO " + table + " (" + ", ".join(i["column"] for i in inserts) + ") VALUES (" + ", ".join(":" + i["field"] for i in inserts) + ")" if inserts and valid_sql else "INSERT INTO FRM_UNMAPPED_BLOCK (X) VALUES (NULL)"
    update_sql = "UPDATE " + table + " SET " + ", ".join(i["column"] + " = :" + i["field"] for i in updates) + " WHERE " + where
    mappings, parameters = [], []
    for n, item in enumerate(db, 1):
        field, t = item["field"], item["type"]
        parameters.append(f'        p.addValue("{field}", row.{field}, {SQL_TYPES[t]});')
        if t == "datetime":
            mappings.append(f"        var date{n} = rs.getTimestamp({n});\n        row.{field} = date{n} == null ? null : date{n}.toLocalDateTime();")
        else:
            mappings.append(f"        row.{field} = rs.{'getBigDecimal' if t == 'number' else 'getString'}({n});")
    create_checks = []
    if b["sequence"]:
        key_field = pk[0]["field"]
        create_checks.append(f'        if (row.{key_field} != null) throw bad("Az azonosítót a szerver generálja.");')
        create_checks.append(f"        row.{key_field} = nextId();")
    for item in db:
        if not item["insert_allowed"] and not item["primary_key"]:
            create_checks.append(f'        if (!SqlValues.isNull(row.{item["field"]})) throw bad({jstr(item["field"] + ": beszúráskor nem adható meg.")});')
    for item in fields:
        if item.get("initial") is not None and item["insert_allowed"] and not (b["sequence"] and item["primary_key"]):
            literal = "new java.math.BigDecimal(" + jstr(item["initial"]) + ")" if item["type"] == "number" else jstr(item["initial"])
            create_checks.append(f"        if (SqlValues.isNull(row.{item['field']})) row.{item['field']} = {literal};")
    immutable = [i for i in db if i["primary_key"] or not i["update_allowed"]]
    def change_checks(items):
        return "\n".join(f'        if (!SqlValues.same(current.{i["field"]}, row.{i["field"]})) throw bad({jstr(i["field"] + ": nem módosítható.")});' for i in items)
    domains = []
    for item in fields:
        for bound, value in item.get('numeric_bounds', {}).items():
            comparison = '<' if bound=='minimum' else '>'
            domains.append(f"        if (row.{item['field']} != null && row.{item['field']}.compareTo(new java.math.BigDecimal({jstr(value)})) {comparison} 0) throw bad({jstr(item['field']+': '+bound+' '+value)});")
        options = [o["value"] for o in item["options"]]
        if item["kind"] == "checkbox":
            options = [item["checked_value"], item["unchecked_value"]]
        if options:
            checks = []
            for value in options:
                if item["type"] == "number":
                    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value):
                        raise MigrationError(f"Nem numerikus listaérték a numerikus mezőhöz: {b['name']}.{item['name']}: {value}")
                    literal = "new java.math.BigDecimal(" + jstr(value) + ")"
                elif item["type"] == "text":
                    literal = jstr(value)
                else:
                    raise MigrationError("Dátum típusú statikus lista adaptert igényel.")
                checks.append(f"!SqlValues.same(row.{item['field']}, {literal})")
            domains.append(f"        if (row.{item['field']} != null && " + " && ".join(checks) + f") throw bad({jstr(item['field'] + ': nem megengedett érték.')});")
    vals = {"CLASS": cls, "BLOCK_NAME": jstr(b["name"]),
            "SELECT_PAGE": jstr(filtered_select + " ORDER BY " + order + " OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY"), "SELECT_KEY": jstr(select + " WHERE " + where),
            "KEY_CHECK": "if (" + (" || ".join("SqlValues.isNull(key." + i["field"] + ")" for i in pk) or "true") + ') throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "Hiányzó elsődleges kulcs.");',
            "INSERT_SQL": jstr(insert_sql), "INSERT_BODY": insert_body(b, table, inserts, insert_sql, valid_sql), "UPDATE_BODY": ("int count = jdbc.update(" + jstr(update_sql) + ', params(row));\n        if (count != 1) throw new ResponseStatusException(HttpStatus.CONFLICT, "Sikertelen módosítás.");') if updates and valid_sql else 'throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, "Nincs módosítható oszlop.");',
            "DELETE_SQL": jstr("DELETE FROM " + table + " WHERE " + where), "PARAMETERS": "\n".join(parameters), "MAPPING": "\n".join(mappings),
            "SEQUENCE_METHOD": 'public java.math.BigDecimal nextId() { return jdbc.queryForObject(' + jstr("SELECT " + b["sequence"] + ".NEXTVAL FROM DUAL") + ', new MapSqlParameterSource(), java.math.BigDecimal.class); }' if b["sequence"] else "",
            "CREATE_CHECKS": "\n".join(create_checks), "UPDATE_CHECKS": change_checks(immutable), "KEY_IMMUTABLE": change_checks(pk),
            "NORMALIZE": "\n".join(normalize_line(i) for i in fields if i["type"] == "text"),
            "FIELD_CHECKS": field_checks([i for i in b["items"] if i["kind"] != "button"]),
            "DOMAIN_CHECKS": "\n".join(domains), "SNAPSHOT_DIFF": " || ".join(f"!SqlValues.same(original.{i['field']}, current.{i['field']})" for i in db) or "true"}
    vals.update(dml_calls(model, b))
    gated = model.get("module_gate", {}).get("required")
    def flag(op):
        # A clean operation of a module under review follows the single MODULE_REVIEWED switch
        # (backend_live only sets its initial value to true).
        if not b.get("ready_" + op, b.get("can_" + op)):
            return "false"
        return "MODULE_REVIEWED" if gated else "true"
    vals.update({"CAN_" + op.upper(): flag(op) for op in ["read", "create", "update", "delete", "search"]})
    # The guard: the operations that run (one Set instead of a switch over all five).
    always = [op for op in ["read", "search", "create", "update", "delete"] if flag(op) == "true"]
    reviewed = [op for op in ["read", "search", "create", "update", "delete"] if flag(op) == "MODULE_REVIEWED"]
    def operation_set(names):
        return "java.util.Set.of(" + ", ".join(jstr(n) for n in names) + ")"
    vals["ENABLED_OPERATIONS"] = (f"(MODULE_REVIEWED ? {operation_set(always + reviewed)} : {operation_set(always)})"
                                  if reviewed else operation_set(always))
    vals['SEARCH_METHOD'] = ''
    if query.get('status') == 'compiled' and query['binds'] and b.get('endpoint_plan', {}).get('search', True):
        bind_params = '\n'.join(f'            p.addValue({jstr(v["parameter"])}, '
                                + (f'SqlValues.normalize(criteria.{v["field"]})' if v['item']['type']=='text' else f'criteria.{v["field"]}')
                                + f', {SQL_TYPES[v["item"]["type"]]});' for v in query['binds'])
        criteria_checks = "\n".join("            " + line for line in check_lines((v["item"], "criteria." + v["field"]) for v in query["binds"]))
        vals['SEARCH_METHOD'] = f'''        public PageResult<{cls}Row> search(SearchRequest<{cls}Criteria> request) {{
            guard("search");
            if (request == null || request.criteria() == null) throw bad("Hiányzó keresési feltételek.");
            if (request.offset() < 0 || request.offset() > 1000000 || request.limit() < 1 || request.limit() > 200)
                throw bad("offset: 0..1000000, limit: 1..200 szükséges.");
            var criteria = request.criteria();
            var errors = new ArrayList<String>();
{criteria_checks}
            if (!errors.isEmpty()) throw bad("Hibás keresési feltételek: " + String.join("; ", errors));
            var p = new MapSqlParameterSource().addValue("offset", request.offset()).addValue("limit", request.limit());
{bind_params}
            var rows = jdbc.query({vals['SELECT_PAGE']}, p, (rs, rowNum) -> map(rs));
            var context = new RuleContext();
            for (var row : rows) postQuery(row, context);
            return new PageResult<>(rows, context.messages());
        }}'''

    return vals

def generate_java(model: dict, output: Path, config: dict, module: str, package: str, *, discovery=None, actions=None):
    from .compact_backend import generate
    from . import java_imports, java_style, java_tidy, java_variables
    # java-imports.json is read at every generation: an edited path applies to the next module.
    java_tidy.configure(java_imports.load(config), omit_package=bool(config.get("java_empty_package")))
    java_style.configure(config)
    try:
        generate(model, output, config, module, package, discovery or {"code": [], "objects": []}, actions or [])
        write_json(output / "analysis" / "java-imports.json", java_tidy.report(output))
        if java_variables.ACTIVE:  # java-variables.json (cli.migration): which entry got where
            write_json(output / "analysis" / "java-variables.json", java_variables.report())
        if java_style.FAILED:  # files the Checkstyle layout left as generated (normally none)
            write_json(output / "analysis" / "java-style.json", {"unformatted": list(java_style.FAILED)})
    finally:
        java_tidy.configure({})  # the map belongs to this generation only
        java_style.configure({})


def generate_angular(model: dict, output: Path, config: dict, module: str):
    from .angular_single import generate_single
    generate_single(model, output, config, module)

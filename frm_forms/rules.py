from __future__ import annotations

import json
import re
from decimal import Decimal
from .common import MigrationError, identifier, jstr, model_cache, nls_decimal
from .plsql import Unsupported, adapter_error, parse, flatten
from .forms_context import NORMAL_MODE, normal_mode_expression
from . import framework, forms_runtime


BACKEND_EVENTS = {"WHEN-VALIDATE-ITEM", "WHEN-VALIDATE-RECORD", "PRE-INSERT", "PRE-UPDATE", "PRE-DELETE", "POST-QUERY",
                  "POST-INSERT", "POST-UPDATE", "POST-DELETE"}
# Data events whose original PL/SQL runs in Oracle at its Forms moment of the generated operation:
# ON-INSERT/ON-UPDATE/ON-DELETE replace the generated DML, ON-CHECK-DELETE-MASTER runs before a
# delete, POST-CHANGE (the pre-WHEN-VALIDATE validation event) runs with the record validation.
REPLACING_EVENTS = {"ON-INSERT": "onInsert", "ON-UPDATE": "onUpdate", "ON-DELETE": "onDelete"}
PASSTHROUGH_EVENTS = BACKEND_EVENTS | set(REPLACING_EVENTS) | {"ON-CHECK-DELETE-MASTER", "POST-CHANGE"}
UI_EVENTS = {"WHEN-NEW-FORM-INSTANCE", "WHEN-BUTTON-PRESSED"}
# Record-level data events a form-level trigger runs for in every database block without its own (Execution
# Hierarchy). The replacing ON-INSERT/UPDATE/DELETE and the item-level events stay with the form.
FORM_LEVEL_EVENTS = {"POST-QUERY", "WHEN-VALIDATE-RECORD", "PRE-INSERT", "PRE-UPDATE", "PRE-DELETE", "POST-INSERT",
                     "POST-UPDATE", "POST-DELETE", "ON-CHECK-DELETE-MASTER"}

# Which generated operations an untranslated trigger can change. Every event not
# listed here is screen behaviour (keys, navigation, windows, messages, timers):
# frontend work that never disables a data endpoint.
READ_EVENTS = {"PRE-QUERY", "POST-QUERY", "ON-SELECT", "ON-FETCH", "ON-COUNT", "PRE-SELECT", "POST-SELECT"}
CREATE_EVENTS = {"PRE-INSERT", "ON-INSERT", "POST-INSERT", "ON-SEQUENCE-NUMBER", "WHEN-CREATE-RECORD"}
UPDATE_EVENTS = {"PRE-UPDATE", "ON-UPDATE", "POST-UPDATE"}
DELETE_EVENTS = {"PRE-DELETE", "ON-DELETE", "POST-DELETE", "ON-CHECK-DELETE-MASTER"}
WRITE_EVENTS = {"WHEN-VALIDATE-ITEM", "WHEN-VALIDATE-RECORD", "POST-CHANGE", "ON-CHECK-UNIQUE", "ON-LOCK",
                "ON-COLUMN-SECURITY", "PRE-COMMIT", "POST-FORMS-COMMIT", "ON-COMMIT", "POST-DATABASE-COMMIT",
                "ON-ROLLBACK", "ON-SAVEPOINT"}
DATA_EVENTS = READ_EVENTS | CREATE_EVENTS | UPDATE_EVENTS | DELETE_EVENTS | WRITE_EVENTS
# Startup and session code may set up authorization or query context.
STARTUP_EVENTS = {"PRE-FORM", "WHEN-NEW-FORM-INSTANCE", "PRE-LOGON", "ON-LOGON", "POST-LOGON"}
# Keys whose trigger replaces a default data operation: (operation scope, default built-in step).
DATA_KEYS = {"KEY-EXEQRY": ("read", "executeQuery"), "KEY-CREREC": ("create", "createRecord"),
             "KEY-DUPREC": ("create", None), "KEY-UPDREC": ("update", None),
             "KEY-DELREC": ("delete", "deleteRecord"), "KEY-COMMIT": ("write", "commit")}
# Generic, module-wide review requirements. They are listed once and gate every
# otherwise clean operation behind one switch instead of repeating per method.
POLICY_CODES = {"SCAFFOLD_REVIEW_REQUIRED", "INHERITANCE_BACKEND_REVIEW", "INHERITANCE"}
OPERATION_SCOPES = {"read": {"all", "read"}, "create": {"all", "write", "create"},
                    "update": {"all", "write", "update"}, "delete": {"all", "write", "delete"}}
BLOCKING_SCOPES = {"all", "read", "write", "create", "update", "delete", "button"}
WRITE_APPROVAL = "Írás nincs engedélyezve a schema.json-ban (writable: true)."
# SET_BLOCK_PROPERTY properties that change what a generated endpoint must do.
RUNTIME_READ_PROPERTIES = {"DEFAULT_WHERE", "ONETIME_WHERE", "ORDER_BY", "QUERY_DATA_SOURCE_NAME",
                           "QUERY_DATA_SOURCE_TYPE", "QUERY_DATA_SOURCE_COLUMNS", "QUERY_DATA_SOURCE_ARGUMENTS"}
RUNTIME_WRITE_PROPERTIES = {"DML_DATA_TARGET_NAME", "DML_DATA_TARGET_TYPE", "DML_ARGUMENTS"}
RUNTIME_ALLOWED_PROPERTIES = {"QUERY_ALLOWED": "read", "INSERT_ALLOWED": "create",
                              "UPDATE_ALLOWED": "update", "DELETE_ALLOWED": "delete"}


class Compiler:
    def __init__(self, block: dict, procedures: dict | None = None, local_packages=()):
        self.block = block
        self.fields = {block["name"] + "." + i["name"]: i for i in block["items"] if i["kind"] != "button"}
        self.procedures = procedures or {}
        self.local_packages = set(local_packages)
        self.locals = {}  # DECLAREd variables (4.24): NAME -> {"java": local name, "type": text/number/datetime}

    def declare(self, node: dict, indent: str) -> str:
        """A DECLARE variable as a Java local: its type from the declaration, %TYPE from the block's column."""
        from .query_java import camel, declared_type
        if node["type"] == "EXCEPTION":
            return ""  # declared only: a RAISE of it is refused
        typ = declared_type(node["type"])
        if typ is None:
            table, _, column = node["type"].upper()[:-len("%TYPE")].rpartition(".")
            match = [i for i in self.block["db_items"] if i["column"] == column and table in {"", self.block["table"].upper().split(".")[-1]}]
            typ = match[0]["type"] if match else None
        if typ not in DB_TYPES:
            raise Unsupported(f"Nem támogatott változótípus a Java-fordítóban: {node['name']} {node['type']}")
        expr, t = self.expression(node["value"]) if node["value"] is not None else ("null", "null")
        if t not in {typ, "null"}:
            raise Unsupported(f"Implicit konverzió a deklarációban: {node['name']} ({t} -> {typ})")
        known = self.locals.get(node["name"])
        if known:
            if known["type"] != typ:
                raise Unsupported("Azonos nevű, eltérő típusú helyi változók: " + node["name"])
        elif node["value"] is None:
            field = camel(node["name"])
            while field in {v["field"] for v in self.locals.values()}:
                field += "_"
            self.locals[node["name"]] = {"java": "local." + field, "field": field, "type": typ}
            return ""  # a new field is null already
        else:
            field = camel(node["name"])
            while field in {v["field"] for v in self.locals.values()}:
                field += "_"
            # fields of one holder object: the lambdas of SqlValues.and/or may read them although they change
            known = self.locals[node["name"]] = {"java": "local." + field, "field": field, "type": typ}
        return indent + known["java"] + " = " + expr + ";"

    def holder(self, indent: str) -> str:
        fields = " ".join(JAVA_CASTS[v["type"]] + " " + v["field"] + ";" for v in self.locals.values())
        return indent + "var local = new Object() { " + fields + " };"

    def routine(self, name: str, kind: str) -> dict:
        """A reviewed DB routine signature, or a refusal that says what is missing."""
        from .discovery import FORMS_BUILTINS
        signature = self.procedures.get(name)
        if signature is None:
            if name in FORMS_BUILTINS or forms_runtime.builtin(name):
                raise Unsupported(f"Forms beépített hívás adatműveleti triggerben: {name}; képernyő- vagy eseményadapter feladata.")
            if name.split(".")[0] in self.local_packages:
                raise Unsupported(f"Helyi csomag hívása: {name}; a csomagtörzs átültetése külön feladat.")
            raise Unsupported(f"DB-rutin aláírás nélkül: {name}; vedd fel a schema.json procedures szakaszába (dictionary-sql, dictionary-import).")
        if signature["kind"] != kind:
            raise Unsupported(f"{name}: a schema.json szerint {signature['kind']}, itt {'eljárásként' if kind == 'procedure' else 'függvényként'} hívódik.")
        return signature

    def db_arguments(self, name: str, signature: dict, args: list[dict]) -> tuple[list[str], list[tuple[int, dict]]]:
        """Positional arguments against the signature; OUT/IN OUT only into this block's fields."""
        params = signature["arguments"]
        if len(args) > len(params) or any(not p["default"] for p in params[len(args):]):
            raise Unsupported(f"{name}: az argumentumok száma nem egyezik az aláírással ({len(args)} / {len(params)}).")
        java, outs = [], []
        for index, (arg, param) in enumerate(zip(args, params)):
            label = param["name"] or str(index + 1)
            if param["type"] not in DB_TYPES:
                raise Unsupported(f"{name}: nem támogatott paramétertípus: {label} ({param['type']}).")
            jdbc = JDBC_TYPES[param["type"]]
            if "OUT" in param["mode"]:
                if arg["op"] != "ref":
                    raise Unsupported(f"{name}: a(z) {label} {param['mode']} paraméter csak blokkmező lehet.")
                field = self.ref(arg["name"])
                if field["type"] != param["type"]:
                    raise Unsupported(f"{name}: {label} típuseltérés: {param['type']} -> {field['type']}")
                outs.append((index, field))
                java.append(f"DbCalls.inOut(row.{field['field']}, {jdbc})" if param["mode"] == "IN OUT" else f"DbCalls.out({jdbc})")
            else:
                expr, t = self.expression(arg)
                if t not in {param["type"], "null"}:
                    raise Unsupported(f"{name}: {label} típuseltérés: {t} -> {param['type']}")
                java.append(f"DbCalls.in({expr}, {jdbc})")
        return java, outs

    def db_function(self, name: str, args: list[dict]) -> tuple[str, str]:
        signature = self.routine(name, "function")
        returns = signature["returns"]
        if returns not in DB_TYPES:
            raise Unsupported(f"{name}: nem támogatott visszatérési típus: {returns}")
        java, outs = self.db_arguments(name, signature, args)
        if outs:
            raise Unsupported(f"{name}: OUT paraméteres függvény kifejezésben nem fordítható.")
        sql = "{? = call " + name + ("(" + ", ".join("?" for _ in args) + ")" if args else "") + "}"
        call = f"DbCalls.function(jdbc, {jstr(sql)}, {JDBC_TYPES[returns]}" + "".join(", " + j for j in java) + ")"
        return f"(({JAVA_CASTS[returns]}) {call})", returns

    def ref(self, value: str) -> dict:
        if value not in self.fields:
            raise Unsupported(f"Másik blokk, SYSTEM/GLOBAL/PARAMETER vagy ismeretlen mező: {value}")
        return self.fields[value]

    def expression(self, node: dict) -> tuple[str, str]:
        if normal_mode_expression(node):
            return jstr(NORMAL_MODE), 'text'
        op = node["op"]
        if op == "literal":
            t, value = node["type"], node["value"]
            if value is None:
                return "null", "null"
            if t == "number":
                return "new java.math.BigDecimal(" + jstr(value) + ")", t
            return jstr(value) if t == "text" else str(value).lower(), t
        if op == "ref":
            field = self.ref(node["name"])
            return "row." + field["field"], field["type"]
        if op == "is_null":
            expr, _ = self.expression(node["value"])
            return ("!" if node["negated"] else "") + "SqlValues.isNull(" + expr + ")", "boolean"
        if op == "unary":
            expr, t = self.expression(node["value"])
            operator = node["operator"]
            if operator == "NOT" and t in {"boolean", "null"}:
                return "SqlValues.not(" + expr + ")", "boolean"
            if operator in {"+", "-"} and t in {"number", "null"}:
                return (expr if operator == "+" else "SqlValues.neg(" + expr + ")"), "number"
            raise Unsupported("Nem támogatott egyoperandusú típuskonverzió.")
        if op == "binary":
            left, lt = self.expression(node["left"])
            right, rt = self.expression(node["right"])
            operator = node["operator"]
            if operator in {"AND", "OR"} and lt in {"boolean", "null"} and rt in {"boolean", "null"}:
                return f"SqlValues.{operator.lower()}(() -> {left}, () -> {right})", "boolean"
            if operator in {"=", "<>", "!=", "<", ">", "<=", ">="} and (lt == rt or "null" in {lt, rt}) and lt != "boolean" and rt != "boolean":
                return f"SqlValues.compare({left}, {right}, {jstr(operator)})", "boolean"
            if operator in {"+", "-", "*", "/"} and lt in {"number", "null"} and rt in {"number", "null"}:
                return f"SqlValues.math({left}, {right}, {jstr(operator)})", "number"
            if operator == "||" and lt in {"text", "null"} and rt in {"text", "null"}:
                return f"SqlValues.concat({left}, {right})", "text"
            raise Unsupported(f"Nem támogatott operandustípusok / implicit NLS-konverzió: {lt} {operator} {rt}")
        if op == "symbol" and node["name"] in self.locals:
            return self.locals[node["name"]]["java"], self.locals[node["name"]]["type"]
        if op in {"in", "between"}:
            value = node["value"]
            if op == "in":
                tests = [{"op": "binary", "operator": "=", "left": value, "right": item} for item in node["items"]]
            else:
                tests = [{"op": "binary", "operator": "AND", "left": {"op": "binary", "operator": ">=", "left": value, "right": node["low"]},
                          "right": {"op": "binary", "operator": "<=", "left": value, "right": node["high"]}}]
            combined = tests[0]
            for test in tests[1:]:
                combined = {"op": "binary", "operator": "OR", "left": combined, "right": test}
            return self.expression({"op": "unary", "operator": "NOT", "value": combined} if node["negated"] else combined)
        if op == "symbol" and self.procedures.get(node["name"], {}).get("kind") == "function":
            return self.db_function(node["name"], [])  # parameterless: PKG.GET_DEFAULT
        if op == "function" and (node["name"] in self.procedures or "." in node["name"]):
            return self.db_function(node["name"], node["args"])
        if op == "function":
            args = [self.expression(arg) for arg in node["args"]]
            fname = node["name"]
            if fname == "NVL" and len(args) == 2 and (args[0][1] == args[1][1] or "null" in {a[1] for a in args}):
                return f"SqlValues.nvl({args[0][0]}, {args[1][0]})", args[1][1] if args[0][1] == "null" else args[0][1]
            if fname in {"UPPER", "LOWER", "TRIM", "LENGTH"} and len(args) == 1 and args[0][1] in {"text", "null"}:
                return f"SqlValues.{fname.lower()}({args[0][0]})", "number" if fname == "LENGTH" else "text"
            if fname == "ABS" and len(args) == 1 and args[0][1] in {"number", "null"}:
                return f"SqlValues.abs({args[0][0]})", "number"
        raise Unsupported(f"Nem támogatott kifejezés / függvény: {node.get('name', op)}")

    def statements(self, nodes: list[dict], indent: str = "        ") -> str:
        lines = []
        for node in flatten(nodes):
            op = node["op"]
            if op in {"noop", "pragma"}:
                continue
            if op == "declare":
                lines.append(self.declare(node, indent))
            elif op == "local_assign":
                local = self.locals.get(node["variable"])
                if local is None:
                    raise Unsupported(f"Nem deklarált változó vagy csomagváltozó: {node['variable']}")
                expr, t = self.expression(node["value"])
                if t not in {local["type"], "null"}:
                    raise Unsupported(f"Implicit hozzárendelési konverzió: {t} -> {local['type']}")
                lines.append(indent + local["java"] + " = " + expr + ";")
            elif op == "assign":
                field = self.ref(node["target"])
                expr, t = self.expression(node["value"])
                if t not in {field["type"], "null"}:
                    raise Unsupported(f"Implicit hozzárendelési konverzió: {t} -> {field['type']}")
                lines.append(indent + "row." + field["field"] + " = " + expr + ";")
            elif op == "if":
                for n, branch in enumerate(node["branches"]):
                    expr, t = self.expression(branch["condition"])
                    if t not in {"boolean", "null"}:
                        raise Unsupported("Az IF feltétel nem logikai típusú.")
                    lines.append(indent + ("if" if n == 0 else "else if") + " (SqlValues.truth(" + expr + ")) {")
                    lines.append(self.statements(branch["body"], indent + "    "))
                    lines.append(indent + "}")
                if node["else"]:
                    lines.append(indent + "else {")
                    lines.append(self.statements(node["else"], indent + "    "))
                    lines.append(indent + "}")
            elif op == "call" and node["name"] == "MESSAGE" and len(node["args"]) == 1:
                expr, t = self.expression(node["args"][0])
                if t not in {"text", "null"}:
                    raise Unsupported("MESSAGE csak szöveget fogad ebben a részhalmazban.")
                lines.append(indent + "context.message(" + expr + ");")
            elif op == "abort":
                lines.append(indent + "context.abort();")
            elif op == "call":
                # A reviewed DB procedure runs in the service transaction, like the trigger did.
                name = node["name"]
                java, outs = self.db_arguments(name, self.routine(name, "procedure"), node["args"])
                sql = "{call " + name + ("(" + ", ".join("?" for _ in node["args"]) + ")" if node["args"] else "") + "}"
                call = f"DbCalls.call(jdbc, {jstr(sql)}" + "".join(", " + j for j in java) + ")"
                if not outs:
                    lines.append(indent + call + ";")
                else:
                    lines.append(indent + "{")
                    lines.append(indent + "    Object[] out = " + call + ";")
                    lines += [indent + f"    row.{field['field']} = ({JAVA_CASTS[field['type']]}) out[{index}];" for index, field in outs]
                    lines.append(indent + "}")
            else:
                raise Unsupported(f"Nem támogatott backend utasítás: {node.get('name', op)}")
        if indent == "        " and self.locals:  # the trigger's own level: the holder of its DECLAREd variables first
            lines.insert(0, self.holder(indent))
        return "\n".join(line for line in lines if line)


def ui_actions(nodes: list[dict], model: dict) -> list[dict]:
    actions = []
    all_fields = {b["name"] + "." + i["name"] for b in model["blocks"] for i in b["items"]}
    all_blocks = {b["name"] for b in model["blocks"]}
    for node in flatten(nodes):
        if node["op"] == "noop":
            continue
        if node["op"] != "call":
            raise Unsupported("UI trigger: csak feltétel nélküli támogatott built-in hívások fordíthatók.")
        fname, args = node["name"], node["args"]
        if fname in {"MESSAGE", "GO_ITEM", "GO_BLOCK"} and len(args) == 1 and args[0]["op"] == "literal" and args[0]["type"] == "text":
            value = args[0]["value"] or ""
            if fname == "GO_ITEM" and value.upper() not in all_fields:
                raise Unsupported("GO_ITEM célelem nem található.")
            if fname == "GO_BLOCK" and value.upper() not in all_blocks:
                raise Unsupported("GO_BLOCK célblokk nem található.")
            actions.append({"op": fname.lower(), "value": value if fname == "MESSAGE" else value.upper()})
        elif fname == "SET_ITEM_PROPERTY" and len(args) == 3 and args[0]["op"] == "literal" and args[0]["type"] == "text" and args[1]["op"] == "symbol" and args[2]["op"] == "symbol":
            target = (args[0]["value"] or "").upper()
            prop = args[1]["name"]
            val = args[2]["name"]
            if target not in all_fields or prop not in {"ENABLED", "DISPLAYED", "VISIBLE"} or val not in {"PROPERTY_TRUE", "PROPERTY_FALSE"}:
                raise Unsupported("SET_ITEM_PROPERTY: nem támogatott cél/property/érték.")
            actions.append({"op": "property", "target": target, "property": prop, "value": val == "PROPERTY_TRUE"})
        else:
            if fname == 'DO_KEY':
                from .forms_keys import KEY_EVENTS
                key = args[0]['value'].upper() if (len(args) == 1 and args[0]['op'] == 'literal'
                      and args[0]['type'] == 'text' and args[0]['value']) else None
                event = KEY_EVENTS.get(key, 'dinamikus/ismeretlen KEY esemény')
                raise Unsupported('Nem támogatott UI eseménylánc: DO_KEY('
                                  + (repr(key) if key else 'dinamikus argumentum') + ') → ' + event + '.')
            raise Unsupported(f"Nem támogatott UI hívás: {fname}; külön felületi adapter szükséges.")
    return actions


ROUTINE_NAME = re.compile(r"[A-Z][A-Z0-9_$#]{0,127}(?:\.[A-Z][A-Z0-9_$#]{0,127}){0,2}")
DB_TYPES = {"text", "number", "datetime"}
JDBC_TYPES = {"text": "java.sql.Types.VARCHAR", "number": "java.sql.Types.NUMERIC", "datetime": "java.sql.Types.TIMESTAMP"}
JAVA_CASTS = {"text": "String", "number": "java.math.BigDecimal", "datetime": "java.time.LocalDateTime"}


def procedure_signatures(value) -> dict:
    """schema.json "procedures": reviewed DB routine signatures (see dictionary-import).

    {"PKG.PROC": {"kind": "procedure"|"function", "returns": "number",
                  "arguments": [{"name": "P_ID", "mode": "IN"|"OUT"|"IN OUT", "type": "number", "default": false}]}}
    Only these routines may be called from generated Java; the name is emitted
    into SQL text, so it is validated as a plain (OWNER.)(PACKAGE.)NAME.
    """
    if not isinstance(value, dict):
        raise MigrationError("schema.json: procedures objektum szükséges.")
    result = {}
    for key, entry in value.items():
        name = str(key).strip().upper()
        if not ROUTINE_NAME.fullmatch(name) or not isinstance(entry, dict) or set(entry) - {"kind", "returns", "arguments", "source"}:
            raise MigrationError("schema.json procedures: hibás eljárás: " + str(key))
        kind = entry.get("kind", "procedure")
        arguments = entry.get("arguments", [])
        if kind not in {"procedure", "function"} or not isinstance(arguments, list):
            raise MigrationError("schema.json procedures: kind=procedure/function és arguments lista szükséges: " + name)
        if (kind == "function") != ("returns" in entry):
            raise MigrationError("schema.json procedures: csak függvénynek (és annak kötelezően) van returns típusa: " + name)
        clean = []
        for argument in arguments:
            if not isinstance(argument, dict) or set(argument) - {"name", "mode", "type", "default"}:
                raise MigrationError("schema.json procedures: hibás argumentum: " + name)
            mode = str(argument.get("mode", "IN")).upper().replace("/", " ")
            if mode not in {"IN", "OUT", "IN OUT"} or type(argument.get("default", False)) is not bool:
                raise MigrationError("schema.json procedures: mode=IN/OUT/IN OUT, default boolean: " + name)
            clean.append({"name": str(argument.get("name", "")), "mode": mode, "type": str(argument.get("type", "unsupported")),
                          "default": argument.get("default", False)})
        result[name] = {"kind": kind, "returns": entry.get("returns"), "arguments": clean}
    return result


def dictionary_table(tables: dict, name: str) -> dict | None:
    """The data-dictionary entry for a block table: exact OWNER.TABLE, else a unique bare TABLE."""
    name = str(name or "").strip().upper()
    if name in tables:
        return tables[name]
    matches = [v for k, v in tables.items() if k.rsplit(".", 1)[-1] == name]
    return matches[0] if len(matches) == 1 else None


def apply_metadata(model: dict, metadata: dict) -> None:
    entries = metadata.get("blocks", {})
    if not isinstance(entries, dict):
        raise MigrationError("schema.json: blocks objektum szükséges.")
    model["procedures"] = procedure_signatures(metadata.get("procedures", {}))
    tables = metadata.get("tables", {})
    if not isinstance(tables, dict) or not all(isinstance(v, dict) for v in tables.values()):
        raise MigrationError("schema.json: tables objektum szükséges (dictionary-import kimenete).")
    tables = {str(k).upper(): v for k, v in tables.items()}
    unknown = set(entries) - {b["name"] for b in model["blocks"]}
    if unknown:
        raise MigrationError(f"Ismeretlen block a schema.json-ban: {sorted(unknown)}")
    for b in model["blocks"]:
        entry = entries.get(b["name"], {})
        if not isinstance(entry, dict):
            raise MigrationError("A block metaadata JSON objektum kell legyen.")
        unknown_keys = set(entry) - {"table", "primary_key", "sequence", "writable", "columns"}
        if unknown_keys:
            raise MigrationError(f"Ismeretlen schema beállítás: {unknown_keys}")
        b["table"] = entry.get("table", b["table"])
        if b["database"] and not str(b["table"] or "").strip():
            # Headstart/Designer helper blocks (CALENDAR, QMS$TRANS_ERRORS...) are often
            # DatabaseDataBlock=true without QueryDataSourceName/DMLDataTargetName. There
            # is nothing to query or save, so no CRUD endpoint is generated for them.
            b["database"] = False
            b["backend_skip"] = ("DatabaseDataBlock=true, de nincs QueryDataSourceName/DMLDataTargetName: nincs mit "
                                 "lekérdezni vagy menteni, ezért nem készül backend-végpont. Valódi táblánál add meg "
                                 "a schema.json blocks." + b["name"] + ".table értékét.")
        # backend_live: generated DML is usable at once; schema.json writable:false still forbids it.
        b["writable"] = entry.get("writable", bool(model.get("options", {}).get("backend_live")))
        if not isinstance(b["writable"], bool):
            raise MigrationError("writable: true vagy false kell.")
        b["sequence"] = entry.get("sequence")
        if b["sequence"]:
            identifier(b["sequence"], True)
        b["db_items"] = [i for i in b["items"] if b["database"] and i["database"] and i["kind"] != "button"]
        # Blank columns are missing mappings; SQL_MAPPING keeps them closed.
        named = [i for i in b["db_items"] if i["column"].strip()]
        columns = {i["column"]: i for i in named}
        if len(columns) != len(named):
            duplicates = {c: [i["name"] for i in named if i["column"] == c]
                          for c in columns if sum(i["column"] == c for i in named) > 1}
            detail = "; ".join(c + " ← " + ", ".join(items) for c, items in sorted(duplicates.items()))
            raise MigrationError(f"Több adatbázis-item ugyanarra az oszlopra mutat: {b['name']}: {detail}; előbb rendezd a mappinget.")
        overrides = entry.get("columns", {})
        if not isinstance(overrides, dict):
            raise MigrationError("A columns metaadata JSON objektum kell legyen.")
        if set(overrides) - set(columns):
            raise MigrationError(f"Ismeretlen oszlop metaadat: {set(overrides) - set(columns)}")
        for col, overrides_for_col in overrides.items():
            if not isinstance(overrides_for_col, dict):
                raise MigrationError(f"Az oszlop metaadata JSON objektum kell legyen: {col}")
            if set(overrides_for_col) - {"type", "max_length", "required", "precision", "scale"}:
                raise MigrationError(f"Ismeretlen oszlopproperty: {col}")
            item = columns[col]
            item.update(overrides_for_col)
            if item["type"] not in {"text", "number", "datetime"}:
                raise MigrationError(f"Nem támogatott schema típus: {item['type']}")
            if type(item["required"]) is not bool or (item["max_length"] is not None and (type(item["max_length"]) is not int or not 1 <= item["max_length"] <= 1000000)):
                raise MigrationError("required: boolean; max_length: pozitív egész vagy null szükséges.")
            if "precision" in item and (not isinstance(item["precision"], int) or not 1 <= item["precision"] <= 38 or not isinstance(item.get("scale", 0), int) or not 0 <= item.get("scale", 0) <= item["precision"]):
                raise MigrationError("precision: 1..38; scale: 0..precision szükséges.")
        dictionary = dictionary_table(tables, b["table"]) if b["database"] else None
        declared = [i["column"] for i in named if i["primary_key"]]
        if "primary_key" not in entry and not declared and dictionary and dictionary.get("primary_key"):
            # The form has no PrimaryKey flags: the reviewed data dictionary decides,
            # and only when every key column is mapped by an item of this block.
            dictionary_key = [str(c).upper() for c in dictionary["primary_key"]]
            if set(dictionary_key) <= set(columns):
                declared = dictionary_key
                b["primary_key_source"] = "dictionary"
            else:
                model["issues"].append({"code": "DICTIONARY_KEY_UNMAPPED", "owner": b["name"], "scope": "review",
                                        "detail": "Az adatszótár kulcsa (" + ", ".join(dictionary_key) + ") nincs teljesen leképezve a blokk mezőire."})
        if dictionary and isinstance(dictionary.get("columns"), dict):
            known = {str(c).upper() for c in dictionary["columns"]}
            missing = sorted(c for c in columns if c not in known)
            if missing:
                model["issues"].append({"code": "SQL_MAPPING", "owner": b["name"], "scope": "all",
                                        "detail": "Az adatszótár szerint nem létező oszlop(ok) a(z) " + b["table"] + " táblában: " + ", ".join(missing)})
        primary_key = entry.get("primary_key", declared)
        if not isinstance(primary_key, list) or not all(isinstance(k, str) for k in primary_key) or len(set(primary_key)) != len(primary_key) or set(primary_key) - set(columns):
            raise MigrationError(f"Hibás primary_key: {b['name']}; az XML-ben szereplő adatbázisoszlopok listája kell.")
        b["pk"] = [columns[c] for c in primary_key]
        for item in b["items"]:
            item["primary_key"] = item in b["pk"]
        if b["sequence"] and (len(b["pk"]) != 1 or b["pk"][0]["type"] != "number"):
            raise MigrationError("A sequence-hez egyetlen numerikus elsődleges kulcs szükséges.")
        if b["database"]:
            try:
                identifier(b["table"], True)
                for item in b["db_items"]:
                    identifier(item["column"])
            except MigrationError as exc:
                model["issues"].append({"code": "SQL_MAPPING", "owner": b["name"], "scope": "all", "detail": str(exc)})
            if not b["db_items"]:
                model["issues"].append({"code": "NO_COLUMNS", "owner": b["name"], "scope": "all", "detail": "Nincs leképezhető adatbázismező."})
            if not b["pk"] and b["db_items"]:
                # Forms itself identifies a row of a block without a primary key by its ROWID (Key Mode
                # Automatic/Unique): the generated operations do the same, nothing is guessed from names.
                fields = {i["field"] for i in b["items"]}
                field = next(f for f in ("rowid", "rowidKey", "frm_rowid") if f not in fields)
                rowid = {"name": "ROWID", "column": "ROWID", "field": field, "kind": "text", "type": "text", "label": "ROWID",
                         "required": False, "max_length": None, "visible": False, "enabled": False, "concealed": False,
                         "insert_allowed": False, "update_allowed": False, "database": True, "primary_key": True, "rowid": True,
                         "checked_value": "", "unchecked_value": "", "options": [], "initial_value": "", "lov": "", "properties": {}}
                b["db_items"].append(rowid)
                b["pk"] = [rowid]
                b["rowid_item"] = rowid
                model["issues"].append({"code": "ROWID_KEY", "owner": b["name"], "scope": "review",
                                        "detail": "Nincs elsődleges kulcs: a rekordot a Forms-hoz hasonlóan a ROWID azonosítja "
                                                  "(ROWIDTOCHAR/CHARTOROWID). Valódi kulcshoz add meg a schema.json primary_key értékét."})
            elif not b["pk"]:
                model["issues"].append({"code": "NO_PRIMARY_KEY", "owner": b["name"], "scope": "write", "detail": "Hiányzó kulcs: primary_key beállítás szükséges; nem találgatunk ID alapján."})


def decimal_literal(raw: str) -> str:
    """A Forms number bound as a plain decimal: '12.5', and the decimal comma of a Hungarian NLS ('12,5')."""
    raw = nls_decimal(raw or '')
    return raw if re.fullmatch(r'[+-]?\d+(?:\.\d+)?', raw) else ''


def item_semantics(model: dict, catalog: dict, live: bool) -> None:
    """Item properties the generated code handles, or that only need a look: not endpoint blockers.

    - LOV: the generated LOV endpoint serves the list; the database constraint still guards the value.
      A framework LOV (Headstart QMS$..., e.g. the list lamp) is no business list at all.
    - CopyValueFromItem: the screen copies the master value into a new record; the save chain sets
      the relation key from the master.
    - CaseRestriction: the generated normalize() converts the text like Forms did.
    """
    keep = []
    for issue in model["issues"]:
        code, detail = issue["code"], issue["detail"]
        if code == "DYNAMIC_LOV":
            lov = detail.split("LOV=", 1)[-1].split(";", 1)[0].strip()
            if framework.framework_name(lov, catalog):
                continue
            if live:
                issue["scope"] = "review"
                issue["detail"] = detail.split(";")[0] + "; a LOV-végpont generált, a választott érték érvényességét az adatbázis-kényszer védi."
        elif code == "ITEM_SEMANTICS" and ": CopyValueFromItem=" in detail and live:
            issue["scope"] = "review"
            issue["detail"] = detail + "; új rekordnál a képernyő és a mentés a forrásmező értékét veszi át."
        elif code == "ITEM_SEMANTICS" and "AllowedValue=" in detail and live and not decimal_literal(detail.split("=", 1)[1]):
            # A bound that is no number (format mask, NLS group separators): the database still checks.
            issue["scope"] = "review"
        elif code == "CASE_RESTRICTION":
            issue["scope"] = "review"
            issue["detail"] = detail.replace("normalizáló adapter szükséges", "a mentés előtti normalizálás nagy/kisbetűsít")
        keep.append(issue)
    model["issues"][:] = keep
    for block in model["blocks"]:
        for item in block["items"]:
            from .xmlmodel import get, canonical
            case = canonical(get(item.get("properties", {}), "CaseRestriction", default="Mixed"))
            if case in {"upper", "lower"} and item["type"] == "text":
                item["case_restriction"] = case


def plsql_library(model: dict, catalog: dict, ui: bool = False) -> dict:
    """The form's program units, rewritten once for the database (cached on the model).

    ui: the variant for buttons and start-up code, with the Forms runtime emulation (forms_emulation).
    """
    from .plsql_passthrough import block_statics, items_by_block, local_units, unit_library
    key, summary = ("_plsql_library_ui", "plsql_units_ui") if ui else ("_plsql_library", "plsql_units")
    if key not in model:
        model[key] = unit_library(local_units(model), items_by_block(model), catalog["call_prefixes"], model.get("procedures", {}),
                                  catalog.get("runtime_calls", ()), ui=ui, form=model["name"], messages=catalog.get("messages"),
                                  block_info=block_statics(model))
        model[summary] = {name: {"kind": u["kind"], "source": u["source"], "text": u["text"], "items": u.get("items", ""),
                                 "error": u["error"], "binds": list(u["binds"]), "calls": u["calls"], "unresolved": u["unresolved"],
                                 "members": u.get("members", []), "commands": u.get("commands", []),
                                 **({"library": u["library"]} if u.get("library") else {})}
                          for name, u in model[key].items()}
    return model[key]


def action_passthrough(trigger: dict, source: str, model: dict, catalog: dict) -> dict:
    """The anonymous block of a button trigger: values of every block, one transaction, the Forms
    built-ins emulated as screen commands (forms_emulation)."""
    from .plsql_passthrough import block_statics, items_by_block, local_units, prepare
    from .xmlmodel import get
    item = (trigger["block"] + "." + trigger["item"]) if trigger.get("block") and trigger.get("item") else None
    # A button with Mouse Navigate = No leaves the cursor where it was: its own KEY triggers may not apply.
    button = next((i for b in model["blocks"] if b["name"] == trigger.get("block") for i in b["items"] if i["name"] == trigger.get("item")), None)
    navigates = button is None or get(button.get("properties", {}), "MouseNavigate", default="true").strip().lower() not in {"false", "no", "0"}
    return prepare(source, block=trigger["block"] or None, items=items_by_block(model), units=local_units(model),
                   prefixes=catalog["call_prefixes"], other_blocks=True, parameters=True, transaction=True,
                   procedures=model.get("procedures", {}), library=plsql_library(model, catalog, ui=True),
                   runtime_calls=catalog.get("runtime_calls", ()), messages=catalog.get("messages"),
                   block_info=block_statics(model), ui=True, form=model["name"], trigger_item=item,
                   key_overrides=key_overrides(model, catalog), key_triggers=key_triggers(model, catalog),
                   commit_points=True, cursor_on_item=navigates)


def key_overrides(model: dict, catalog: dict) -> set:
    """Built-ins whose DO_KEY would run an own KEY-* trigger (not Headstart dispatch): embedded or refused."""
    return model_cache(model, "key_overrides", lambda: _key_overrides(model, catalog))


def _key_overrides(model: dict, catalog: dict) -> set:
    from .forms_keys import KEY_EVENTS
    events = {t["event"] for t in model["triggers"] if framework.classify(t["source"], catalog)[0] not in {"framework", "empty"}}
    return {builtin for builtin, event in KEY_EVENTS.items() if event in events}


def key_triggers(model: dict, catalog: dict) -> dict:
    """KEY event -> the own KEY-* triggers DO_KEY may run: owner, scope, code and Execution Hierarchy."""
    return model_cache(model, "key_triggers", lambda: _key_triggers(model, catalog))


def _key_triggers(model: dict, catalog: dict) -> dict:
    from .xmlmodel import get
    result = {}
    for t in model["triggers"]:
        if not t["event"].startswith("KEY-") or framework.classify(t["source"], catalog)[0] in {"framework", "empty"}:
            continue
        hierarchy = get(t.get("properties", {}), "ExecutionHierarchy", "ExecuteHierarchy", default="Override").strip().upper()
        result.setdefault(t["event"], []).append({
            "id": t["id"], "event": t["event"], "block": t["block"], "item": t["item"], "hierarchy": hierarchy,
            "source": (t.get("replacement") or {}).get("source", t["source"])})
    return result


COMMIT_EVENTS = ("PRE-COMMIT", "POST-FORMS-COMMIT")


def commit_plan(model: dict, catalog: dict) -> None:
    """PRE-COMMIT and POST-FORMS-COMMIT run in the commit endpoint, before and after the DML of the blocks.

    Like a button: the original PL/SQL in Oracle with every block's current values, the Forms
    built-ins as screen commands. Once attached, they no longer block the write endpoints.
    """
    from .plsql_passthrough import block_statics, items_by_block, local_units, prepare
    plans = {}
    for trigger in model["triggers"]:
        if trigger["event"] not in COMMIT_EVENTS or trigger["block"] or trigger["status"] == "framework" \
                or trigger.get("target") == "noop":
            continue
        source = (trigger.get("replacement") or {}).get("source", trigger["source"])
        if model.get('options', {}).get('backend_trigger_mode') == 'ai':
            # A hook in commitForm (before / after the blocks' DML); its body: the Java assistant.
            plans[trigger["event"]] = {"trigger": trigger["id"], "ai": True}
            trigger.update(status="converted", target="commit", ai=True)
            trigger.pop("reason", None)
            model["issues"][:] = [i for i in model["issues"]
                                  if not (i["code"] == "UNSUPPORTED_TRIGGER" and i["detail"].startswith(trigger["id"] + ": "))]
            continue
        try:
            plan = prepare(source, block=None, items=items_by_block(model), units=local_units(model),
                           prefixes=catalog["call_prefixes"], other_blocks=True, parameters=True, transaction=True,
                           procedures=model.get("procedures", {}), library=plsql_library(model, catalog, ui=True),
                           runtime_calls=catalog.get("runtime_calls", ()), messages=catalog.get("messages"),
                           block_info=block_statics(model), ui=True, form=model["name"],
                           key_overrides=key_overrides(model, catalog), key_triggers=key_triggers(model, catalog))
        except Exception as exc:
            if isinstance(exc, MigrationError):
                raise
            trigger["commit_reason"] = str(adapter_error(exc))
            continue
        if "SHOW_ALERT" in plan["commands"]:
            trigger["commit_reason"] = "SHOW_ALERT a mentési láncban: a párbeszéd és a mentés sorrendje kézi átültetést igényel."
            continue
        plans[trigger["event"]] = {"trigger": trigger["id"], "plan": plan,
                                   "passthrough": {"binds": [b["source"] for b in plan["binds"]],
                                                   "written": [b["source"] for b in plan["outs"]],
                                                   "commands": plan["commands"], "notes": plan["notes"], "units": plan["units"]}}
        trigger.update(status="converted", target="commit")
        trigger.pop("reason", None)
        model["issues"][:] = [i for i in model["issues"]
                              if not (i["code"] == "UNSUPPORTED_TRIGGER" and i["detail"].startswith(trigger["id"] + ": "))]
    model["commit_plan"] = plans


INIT_EVENTS = ("PRE-FORM", "WHEN-NEW-FORM-INSTANCE")


def startup_plan(model: dict, catalog: dict) -> None:
    """PRE-FORM and WHEN-NEW-FORM-INSTANCE of the form as one init endpoint the screen calls on opening.

    Their PL/SQL runs in Oracle in Forms order, the Forms built-ins become screen commands: the
    screen gets the item values, the :GLOBAL values and the commands (item states, navigation,
    record groups, messages). Only own code: framework-only and NULL triggers stay out.
    """
    from .common import decode_line_escapes
    from .plsql_passthrough import block_statics, items_by_block, local_units, prepare
    chosen = [t for event in INIT_EVENTS for t in model["triggers"]
              if t["event"] == event and not t["block"] and t["status"] != "framework" and t.get("target") not in {"noop", "frontend"}]
    if not chosen:
        return
    parts = []
    for trigger in chosen:
        source = (trigger.get("replacement") or {}).get("source", trigger["source"])
        parts.append("-- " + trigger["event"] + "\nBEGIN\n" + decode_line_escapes(source).strip().rstrip("/").strip() + "\nEND;")
    ids = [t["id"] for t in chosen]
    if model.get('options', {}).get('backend_trigger_mode') == 'ai':
        # The init endpoint's frame (onFormInit, called when the screen opens); its body: the Java assistant.
        model["init_plan"] = {"status": "ai", "triggers": ids}
        for trigger in chosen:
            trigger.update(status="converted", target="init", ai=True)
            trigger.pop("reason", None)
        model["issues"][:] = [i for i in model["issues"]
                              if not (i["code"] == "UNSUPPORTED_TRIGGER" and any(i["detail"].startswith(t + ": ") for t in ids))]
        return
    try:
        plan = prepare("\n".join(parts), block=None, items=items_by_block(model), units=local_units(model),
                       prefixes=catalog["call_prefixes"], other_blocks=True, parameters=True, transaction=True,
                       procedures=model.get("procedures", {}), library=plsql_library(model, catalog, ui=True),
                       runtime_calls=catalog.get("runtime_calls", ()), messages=catalog.get("messages"),
                       block_info=block_statics(model), ui=True, form=model["name"],
                       key_overrides=key_overrides(model, catalog), key_triggers=key_triggers(model, catalog))
    except Exception as error:
        if isinstance(error, MigrationError):
            raise
        exc = adapter_error(error)
        model["init_plan"] = {"status": "manual", "triggers": ids, "reason": str(exc)}
        for issue in model["issues"]:
            if issue["code"] == "UNSUPPORTED_TRIGGER" and any(issue["detail"].startswith(i + ": ") for i in ids):
                issue["detail"] += " Az indítási végpont sem generálható: " + str(exc)
        return
    model["init_plan"] = {"status": "generated", "triggers": ids, "plan": plan,
                          "passthrough": {"binds": [b["source"] for b in plan["binds"]], "written": [b["source"] for b in plan["outs"]],
                                          "globals": [b["source"] for b in plan["globals"]], "commands": plan["commands"],
                                          "notes": plan["notes"], "units": plan["units"], "unresolved": plan["unresolved"]}}
    for trigger in chosen:
        trigger.update(status="converted", target="init")
        trigger.pop("reason", None)
    model["issues"][:] = [i for i in model["issues"]
                          if not (i["code"] == "UNSUPPORTED_TRIGGER" and any(i["detail"].startswith(t + ": ") for t in ids))]


def passthrough(trigger: dict, block: dict, source: str, model: dict, catalog: dict, event: str | None = None) -> tuple[str, dict]:
    """Java for a data trigger that runs its own PL/SQL as an anonymous Oracle block.

    event: the Forms moment whose write-back rules apply (POST-CHANGE also runs as POST-QUERY).
    """
    from .plsql_passthrough import block_statics, input_declarations, input_variable, items_by_block, local_units, prepare, sql_expression
    items = items_by_block(model)
    event = event or trigger["event"]
    def writable(bind):
        info = items[bind["block"]][bind["item"]]
        if event == "POST-QUERY" and info["database"]:
            return False  # the queried snapshot must stay what the database returned
        if info["primary_key"] and event not in {"PRE-INSERT", "ON-INSERT"}:
            return False  # ON-INSERT, like PRE-INSERT, may take the key from a sequence
        return not (info["database"] and not info["update_allowed"]
                    and event in {"WHEN-VALIDATE-ITEM", "WHEN-VALIDATE-RECORD", "POST-CHANGE", "PRE-UPDATE", "ON-UPDATE"})
    prepared = prepare(source, block=block["name"], items=items, units=local_units(model), prefixes=catalog["call_prefixes"],
                       writable=writable, procedures=model.get("procedures", {}), library=plsql_library(model, catalog),
                       runtime_calls=catalog.get("runtime_calls", ()), messages=catalog.get("messages"),
                       block_info=block_statics(model))
    field = lambda b: items[b["block"]][b["item"]]["field"]
    params = [f"DbCalls.in({input_variable(prepared, b) or 'row.' + field(b)}, {JDBC_TYPES[b['type']]})" for b in prepared["binds"]]
    params += [f"DbCalls.out({JDBC_TYPES[b['type']]})" for b in prepared["outs"]] + ["DbCalls.out(java.sql.Types.VARCHAR)"]
    offset = len(prepared["binds"])
    lines = ["        // Az eredeti PL/SQL fut az adatbázisban (névtelen blokk); a mezők kötött változók."]
    lines += input_declarations(prepared, "        ").splitlines()
    lines += ["        Object[] out = DbCalls.call(jdbc, " + sql_expression(prepared) + ",",
             "            " + ",\n            ".join(params) + ");"]
    lines += [f"        row.{field(b)} = ({JAVA_CASTS[b['type']]}) out[{offset + k}];" for k, b in enumerate(prepared["outs"])]
    lines.append(f"        if (out[{offset + len(prepared['outs'])}] instanceof String) for (String line : ((String) out[{offset + len(prepared['outs'])}]).split(\"\\n\")) if (!line.isBlank()) context.message(line);")
    info = {"binds": [b["source"] for b in prepared["binds"]], "written": [b["source"] for b in prepared["outs"]],
            "inputs": prepared["inputs"],
            "notes": prepared["notes"], "units": prepared["units"], "unresolved": prepared["unresolved"], "guarded": prepared["guarded"],
            "sql": prepared["sql"]}  # analysis/db-statements.json: verify-db compiles it in the target database
    return "\n".join(lines), info


def statement_routine(statement: str) -> str:
    """The routine name of a framework statement ('exception when others then cgte$x' included)."""
    text = re.sub(r"^exception\s+when\s+[\w$#,\s]+?\s+then\s+", "", statement, flags=re.I)
    match = re.match(r"\s*([A-Za-z][\w$#]*(?:\.[A-Za-z][\w$#]*)*)", text)
    return match.group(1) if match else ""


def framework_statements(source: str, catalog: dict) -> list[str]:
    return [s for s in framework.statements(source) if framework.framework_call(s, catalog)]


def strip_framework(nodes: list[dict], catalog: dict) -> tuple[list[dict], list[str]]:
    """Drop catalogued framework calls from a parsed trigger: plumbing, not business rules.

    An EXCEPTION handler that only reports and re-raises (framework calls and/or
    RAISE FORM_TRIGGER_FAILURE, e.g. Designer's cgte$other_exceptions) goes too:
    without it the error propagates and the request fails, as the trigger did.
    A handler that swallows or recovers (NULL, assignments) stays and is refused.
    """
    removed = []
    def walk(body):
        result = []
        for node in body:
            if framework.safe_framework_node(node, catalog):
                removed.append(node["name"].lower())
                continue
            if node["op"] == "block":
                kept = []
                for handler in node.get("handlers", []):
                    rest = walk(handler["body"])
                    plumbing = len(rest) < len(handler["body"]) or any(n["op"] == "abort" for n in rest)
                    if plumbing and all(n["op"] == "abort" for n in rest):
                        removed.append("exception when " + " or ".join(handler["names"]).lower() + " (hibajelzés és továbbdobás)")
                        continue
                    kept.append({**handler, "body": rest})
                node = {**node, "body": walk(node["body"]), "handlers": kept}
            elif node["op"] == "if":
                node = {**node, "branches": [{**branch, "body": walk(branch["body"])} for branch in node["branches"]],
                        "else": walk(node["else"])}
            result.append(node)
        return result
    return walk(nodes), sorted(set(removed))


def trigger_scope(trigger: dict, block: dict | None, source: str, catalog: dict, model=None) -> str:
    """The generated operations an untranslated trigger can change; 'frontend' changes none."""
    event = trigger["event"]
    if event == "WHEN-BUTTON-PRESSED":
        return "button"
    if block is not None and not block["database"]:
        return "frontend"  # No generated data endpoint ever runs control-block code.
    if event in DATA_KEYS or event in STARTUP_EVENTS:
        # A key trigger only runs when the Forms key is pressed: the web screen's own controls call
        # the generated endpoints. Start-up code prepares the screen. Neither changes what an endpoint
        # does: SET_BLOCK_PROPERTY (query, DML target, *_ALLOWED) is checked separately for every trigger
        # and program unit (runtime_block_properties), access to the module is the host's permission.
        return "frontend"
    if event in READ_EVENTS:
        return "all"  # POST-QUERY also runs on the row re-read after every write.
    for events, scope in ((CREATE_EVENTS, "create"), (UPDATE_EVENTS, "update"), (DELETE_EVENTS, "delete"), (WRITE_EVENTS, "write")):
        if event in events:
            return scope
    return "frontend"


def event_reason(event: str) -> str:
    # The issue detail already starts with the trigger id (OWNER:EVENT).
    if event in STARTUP_EVENTS:
        return ("indítási kód: a képernyő előkészítése (mezőállapot, globális változók, üzenetek); a végpontokat nem tiltja. "
                "A futásidejű blokkbeállítást (SET_BLOCK_PROPERTY) külön ellenőrizzük.")
    if event in DATA_KEYS:
        return ("billentyű-trigger: a webes képernyő saját vezérlője hívja a generált végpontot; a végpontot nem tiltja, "
                "a többletlogikát a képernyőn kell átnézni.")
    if event in DATA_EVENTS:
        return "Nem támogatott adatesemény: " + event
    return "képernyő-esemény: a frontend feladata, a backend-végpontokat nem tiltja."


ACCESS_STOPS = re.compile(r"\b(EXIT_FORM|FORM_TRIGGER_FAILURE|RAISE_APPLICATION_ERROR)\b", re.I)


def access_checks(source: str) -> list[str]:
    """The statements in start-up code that can refuse the form (outside strings and comments)."""
    from .plsql_passthrough import scan, significant
    from .common import decode_line_escapes
    try:
        words = [t[1].upper() for t in significant(scan(decode_line_escapes(source))) if t[0] == "ident"]
    except Unsupported:
        words = [m.group(1).upper() for m in ACCESS_STOPS.finditer(source)]
    return sorted({w for w in words if ACCESS_STOPS.fullmatch(w)})


# The built-ins by which a key trigger still performs its Forms default operation.
DATA_KEY_BUILTINS = {"KEY-EXEQRY": {"EXECUTE_QUERY"}, "KEY-CREREC": {"CREATE_RECORD"},
                     "KEY-DUPREC": {"DUPLICATE_RECORD", "CREATE_RECORD"}, "KEY-UPDREC": {"LOCK_RECORD"},
                     "KEY-DELREC": {"DELETE_RECORD"}, "KEY-COMMIT": {"COMMIT_FORM", "COMMIT", "POST"}}
KEY_OPERATION_NAMES = {"read": "lekérdezés", "create": "új rekord", "update": "módosítás", "delete": "törlés", "write": "mentés"}


def code_words(source: str) -> tuple[list[str], list[str]]:
    """Identifiers outside strings and comments, and the literal arguments of DO_KEY('...')."""
    from .plsql_passthrough import scan, significant
    from .common import decode_line_escapes
    sig = significant(scan(decode_line_escapes(source)))
    words = [t[1].upper() for t in sig if t[0] == "ident"]
    keys = [sig[i + 2][1][1:-1].upper() for i, t in enumerate(sig[:-3])
            if t[1].upper() == "DO_KEY" and sig[i + 1][1] == "(" and sig[i + 2][0] == "string"]
    return words, keys


def key_operation(trigger: dict, source: str, model: dict, catalog: dict) -> str:
    """Does a key trigger still do its default operation? 'yes', 'no' or 'unknown' (own/library routine).

    Local program units are followed (they are in the form); a routine Forms resolved elsewhere
    (attached library, database) may or may not perform it, so it is 'unknown'.
    """
    from .xmlmodel import get
    from .discovery import FORMS_BUILTINS
    units = {get(u, "Name").upper(): get(u, "ProgramUnitText") for u in model["program_units"]}
    wanted = DATA_KEY_BUILTINS.get(trigger["event"], set())
    seen, pending, unknown = set(), [source], set()
    while pending:
        text = pending.pop()
        try:
            words, keys = code_words(text)
        except Unsupported:
            return "unknown"
        if wanted & (set(words) | set(keys)):
            return "yes"
        for name in framework.called(text):
            upper = name.upper()
            head = upper.split(".")[0]
            if upper in units or head in units:
                key = upper if upper in units else head
                if key not in seen:
                    seen.add(key)
                    pending.append(units[key])
            elif not (framework.framework_name(name, catalog) or upper in FORMS_BUILTINS or forms_runtime.builtin(upper)
                      or upper in {"MESSAGE", "RAISE_APPLICATION_ERROR"}):
                unknown.add(upper)
    return "unknown" if unknown else "no"


def unconditional_item_properties(source: str) -> list[tuple[str, str, str, bool]]:
    """SET_ITEM_PROPERTY('B.I', PROP, PROPERTY_TRUE/FALSE) calls: (item, property, value, unconditional)."""
    from .plsql_passthrough import scan, significant
    from .common import decode_line_escapes
    try:
        sig = significant(scan(decode_line_escapes(source)))
    except Unsupported:
        return []
    found, stack, skip = [], [], -1
    for index, token in enumerate(sig):
        word = token[1].upper() if token[0] == "ident" else ""
        if index == skip:
            continue
        if word in {"IF", "CASE", "LOOP", "BEGIN"}:
            stack.append(word)
        elif word == "END":
            following = sig[index + 1][1].upper() if index + 1 < len(sig) else ""
            if stack:
                stack.pop()
            if following in {"IF", "CASE", "LOOP"}:
                skip = index + 1
        elif word == "EXCEPTION" and stack and stack[-1] == "BEGIN":
            stack[-1] = "EXCEPTION"
        elif word == "SET_ITEM_PROPERTY" and index + 7 < len(sig):
            args = sig[index + 1:index + 8]
            if ([a[1] for a in args[0::2]] == ["(", ",", ",", ")"] and args[1][0] == "string"
                    and args[3][0] == "ident" and args[5][0] == "ident"):
                conditional = any(s in {"IF", "CASE", "LOOP", "EXCEPTION"} for s in stack)
                found.append((args[1][1][1:-1].upper(), args[3][1].upper(), args[5][1].upper(), not conditional))
    return found


def startup_item_permissions(trigger: dict, source: str, model: dict) -> None:
    """INSERT_ALLOWED/UPDATE_ALLOWED switched off at start-up: the generated write endpoints honour it.

    Unconditional: the database item becomes read-only for that operation, as in Forms. Conditional
    (role, mode, parameter): the screen applies it; the endpoint keeps the static rule - a review note.
    """
    items = {b["name"] + "." + i["name"]: (b, i) for b in model["blocks"] for i in b["items"]}
    for target, prop, value, unconditional in unconditional_item_properties(source):
        if prop not in {"INSERT_ALLOWED", "UPDATE_ALLOWED"} or value not in {"PROPERTY_FALSE", "PROPERTY_OFF"}:
            continue
        found = items.get(target)
        if not found or not found[1]["database"]:
            continue
        block, item = found
        key = "insert_allowed" if prop == "INSERT_ALLOWED" else "update_allowed"
        if unconditional:
            item[key] = False
            model["issues"].append({"code": "STARTUP_ITEM_PROPERTY", "owner": block["name"], "scope": "review",
                                    "detail": trigger["id"] + ": " + target + " " + prop + "=FALSE indításkor: a generált "
                                              + ("beszúrás" if key == "insert_allowed" else "módosítás") + " sem írja ezt a mezőt."})
        else:
            model["issues"].append({"code": "STARTUP_ITEM_PROPERTY", "owner": block["name"],
                                    "scope": "review" if model.get("options", {}).get("backend_live") else "write",
                                    "detail": trigger["id"] + ": " + target + " " + prop + "=FALSE feltételesen (indításkor): "
                                              "a képernyő alkalmazza; ha jogosultsághoz kötött, a ServiceImpl-ben is érvényesítsd."})


def key_trigger_effect(trigger: dict, source: str, model: dict, catalog: dict) -> None:
    """A key trigger that no longer does its default operation disables that control on the screen."""
    blocks = {b["name"]: b for b in model["blocks"]}
    operation = DATA_KEYS[trigger["event"]][0]
    result = key_operation(trigger, source, model, catalog)
    owner = trigger["block"] or "@FORM:" + model["name"]
    targets = [blocks[trigger["block"]]] if trigger["block"] in blocks else [b for b in model["blocks"] if b["database"]]
    if result == "no":
        for b in targets:
            b.setdefault("ui_disabled_operations", {})[operation] = trigger["id"]
        model["issues"].append({"code": "KEY_DISABLES_OPERATION", "owner": owner, "scope": "review",
                                "detail": trigger["id"] + ": a Forms-felületen a(z) " + KEY_OPERATION_NAMES[operation]
                                          + " nem érhető el (a trigger nem hívja: " + ", ".join(sorted(DATA_KEY_BUILTINS[trigger["event"]]))
                                          + "); a generált képernyő sem kínálja fel."})
    elif result == "unknown":
        model["issues"].append({"code": "KEY_TRIGGER_WRAPPER", "owner": owner, "scope": "review",
                                "detail": trigger["id"] + ": saját/könyvtári rutint hív; ellenőrizd, hogy a(z) "
                                          + KEY_OPERATION_NAMES[operation] + " a Formsban is így futott-e."})


FIND_BLOCK = re.compile(r"([A-Za-z][\w$#]*)\s*:=\s*find_block\s*\(\s*'([^']+)'\s*\)", re.I)


def runtime_block_properties(model: dict) -> list[dict]:
    """SET_BLOCK_PROPERTY calls that change a block's query, DML target or permissions at runtime.

    Lexical evidence from every trigger and local program unit. A literal block
    name, a FIND_BLOCK handle or :SYSTEM.TRIGGER_BLOCK identifies the block; any
    other target is unknown and therefore concerns every block.
    """
    return model_cache(model, "runtime_block_properties", lambda: _runtime_block_properties(model))


def _runtime_block_properties(model: dict) -> list[dict]:
    from .discovery import scan, source_view
    from .xmlmodel import get
    names = {b["name"] for b in model["blocks"]}
    watched = RUNTIME_READ_PROPERTIES | RUNTIME_WRITE_PROPERTIES | set(RUNTIME_ALLOWED_PROPERTIES)
    sources = [(t["id"], t["block"], t["source"]) for t in model["triggers"] if not t.get("form_level")]  # copies: same code
    sources += [("PROGRAM UNIT " + get(u, "Name"), None, get(u, "ProgramUnitText")) for u in model["program_units"]]
    findings = []
    for origin, owner_block, source in sources:
        if "SET_BLOCK_PROPERTY" not in str(source).upper():
            continue
        handles = {m.group(1).upper(): m.group(2).strip().upper() for m in FIND_BLOCK.finditer(source_view(source)[0])}
        for call in scan(source)["calls"]:
            if call["name"] != "SET_BLOCK_PROPERTY" or len(call["arguments"]) < 2:
                continue
            prop = call["arguments"][1].strip().upper()
            if prop not in watched:
                continue
            target = call["arguments"][0].strip()
            literal = re.fullmatch(r"'([^']*)'", target)
            if literal:
                block = literal.group(1).strip().upper()
            elif target.upper() in handles:
                block = handles[target.upper()]
            elif target.upper() == ":SYSTEM.TRIGGER_BLOCK" and owner_block:
                block = owner_block
            else:
                block = None
            if block is not None and block not in names:
                continue  # Another module's block cannot change this one.
            text = " ".join(call["text"].split())
            findings.append({"origin": origin, "block": block, "property": prop,
                             "call": text if len(text) <= 160 else text[:157] + "..."})
    return findings


def java_query_origin(model: dict, finding: dict) -> str | None:
    """The Java query button (owner) whose own code makes this SET_BLOCK_PROPERTY: the trigger or a procedure it inlines."""
    for trigger in model["triggers"]:
        java = (trigger.get("query_action") or {}).get("java")
        if not java or java["target"] != finding["block"]:
            continue
        if finding["origin"] == trigger["id"] or finding["origin"].upper() in {"PROGRAM UNIT " + u for u in java.get("expanded", [])}:
            return trigger["owner"]
    return None


def form_level_copies(model: dict) -> None:
    """A form-level record trigger (POST-QUERY, WHEN-VALIDATE-RECORD, PRE-INSERT ...) as Forms fires it: for every
    database block without its own trigger of the event (Execution Hierarchy Override), and besides the block's own
    when that one says Before (block first) or After (form first). Each block gets a copy (id FORM:EVENT@BLOCK) that
    is translated and attached like the block's own trigger; the form-level original only lists the copies."""
    from .xmlmodel import get
    copies = []
    for trigger in list(model["triggers"]):
        if trigger["block"] or trigger["event"] not in FORM_LEVEL_EVENTS or trigger.get("form_level"):
            continue
        blocks = []
        for b in model["blocks"]:
            if not b["database"] or b.get("backend_skip"):
                continue
            own = [t for t in model["triggers"] if t["block"] == b["name"] and not t["item"] and t["event"] == trigger["event"]]
            hierarchy = {get(t.get("properties", {}), "ExecutionHierarchy", "ExecuteHierarchy", default="Override").strip().upper()
                         for t in own}
            if own and hierarchy <= {"OVERRIDE"}:
                continue
            copy = {**trigger, "id": trigger["id"] + "@" + b["name"], "block": b["name"], "form_level": trigger["id"]}
            copies.append((copy, own[0] if own and "AFTER" in hierarchy else None))
            blocks.append(b["name"])
        trigger["form_level_blocks"] = blocks
    for copy, before in copies:
        # After: the form-level code runs first, so its copy comes before the block's own trigger
        model["triggers"].insert(model["triggers"].index(before), copy) if before else model["triggers"].append(copy)


def analyze(model: dict, metadata: dict, replacements: dict, catalog: dict | None = None) -> None:
    catalog = catalog or framework.load({})
    apply_metadata(model, metadata)
    for b in model["blocks"]:
        role = catalog["blocks"].get(b["name"].upper())
        if b.get("backend_skip") and role:
            b["backend_skip"] = "Keretrendszer-blokk (" + catalog["source"] + "): " + role + " " + b["backend_skip"]
    from .backend_queries import prepare_queries, prepare_relations
    from .backend_units import LocalProcedures
    prepare_relations(model)
    prepare_queries(model)
    procedures = LocalProcedures(model)
    live = bool(model.get('options', {}).get('backend_live'))
    item_semantics(model, catalog, live)
    for block in model['blocks']:
        for item in block['items']:
            if item['type'] != 'number': continue
            bounds = {}
            for property_name, bound in [('LowestAllowedValue','minimum'), ('HighestAllowedValue','maximum')]:
                raw = decimal_literal(item['properties'].get(property_name.lower(),''))
                if raw:
                    bounds[bound] = raw
                    detail = f"{block['name']}.{item['name']}: {property_name}="+item['properties'][property_name.lower()]
                    for issue in model['issues']:
                        if issue['code']=='ITEM_SEMANTICS' and issue['owner']==block['name'] and issue['detail']==detail:
                            issue['scope']='review'; issue['detail'] += '; pontos BigDecimal tartományellenőrzés generálva.'
            if 'minimum' in bounds and 'maximum' in bounds and Decimal(bounds['minimum'])>Decimal(bounds['maximum']):
                raise MigrationError('NUMERIC_RANGE: az alsó határ nagyobb a felsőnél: '+block['name']+'.'+item['name'])
            item['numeric_bounds'] = bounds
    blocks = {b["name"]: b for b in model["blocks"]}
    form_level_copies(model)
    replacements = replacements.get("replacements", {})
    form_owner = "@FORM:" + model["name"]
    from .xmlmodel import get
    local_packages = {get(u, "Name").upper() for u in model["program_units"] if "PACKAGE" in get(u, "ProgramUnitType").upper()}
    # 4.35 default: no deterministic translation of server-side trigger code (ai_triggers): the method
    # frame is generated, its body is the commented original code for the Java assistant.
    ai_mode = model.get('options', {}).get('backend_trigger_mode') == 'ai'
    from . import ai_triggers

    def run_in_database(trigger, block, source):
        trigger["java"], trigger["passthrough"] = passthrough(trigger, block, source, model, catalog)
        if trigger["event"] == "POST-CHANGE":
            # Forms also fires POST-CHANGE when a fetched value lands in the item, so a derived
            # description shows in the list as on the Forms screen - when it writes nothing queried.
            try:
                trigger["java_query"], _ = passthrough(trigger, block, source, model, catalog, "POST-QUERY")
            except Unsupported:
                pass
        trigger.update(status="converted", target="backend")

    for trigger in model["triggers"]:
        block = blocks.get(trigger["block"]) if trigger["block"] else None
        source = trigger["source"]
        if trigger.get("form_level_blocks") is not None:
            # its copies run in the blocks (form_level_copies); none: no database block fires it
            trigger.update(status="converted", target="per-block" if trigger["form_level_blocks"] else "noop")
            continue
        try:
            if not source.strip():
                # Empty source may indicate an incomplete export, not a NULL trigger.
                raise Unsupported("Üres TriggerText; exportteljesség ellenőrzése szükséges.")
            replacement = replacements.get(trigger["sha256"])
            if replacement:
                if not isinstance(replacement, dict) or not replacement.get("reason") or not isinstance(replacement.get("source"), str):
                    raise MigrationError("A replacement 'source' és 'reason' mezőt igényel.")
                source = replacement["source"]
                trigger["replacement"] = replacement
            runtime = framework.runtime_calls(source, catalog)
            if runtime:
                trigger["forms_runtime"] = runtime  # catalogued Forms-only calls (calendar.event): never in the database
            if framework.classify(source, catalog)[0] == "framework":
                # Catalogued Headstart/Designer dispatch (qms$event_form, cgte$...) or catalogued
                # Forms-runtime routines (calendar.event): not a task, never a database call.
                calls = framework_statements(source, catalog)
                trigger.update(status="framework", target="framework", framework_calls=calls)
                # Only dispatch plumbing may stand for database-side logic (Table API, DB trigger).
                dispatch = [c for c in calls if not framework.runtime_call(statement_routine(c), catalog)]
                if dispatch and trigger["event"] in DATA_EVENTS and (block is None or block["database"]):
                    model["issues"].append({"code": "FRAMEWORK_DATA_TRIGGER", "owner": trigger["block"] or form_owner, "scope": "review",
                                            "detail": trigger["id"] + ": csak keretrendszer-hívás (" + "; ".join(calls) + "); a végpontot nem tiltja. "
                                                      "Ellenőrizd, hogy az adatbázisoldali logika (Table API, DB-trigger) lefedi-e."})
                continue
            if framework.explicit_noop(source):
                trigger.update(status="converted", target="noop")
                continue
            if ai_mode and trigger['event'] in PASSTHROUGH_EVENTS and block is not None and block['database']:
                # The record's own trigger: a hook in the CRUD flow, its body written by the Java assistant.
                ai_triggers.mark(trigger, block, source, catalog)
                continue
            if (model.get('options', {}).get('backend_trigger_mode') == 'plsql'
                    and trigger['event'] in PASSTHROUGH_EVENTS and block is not None and block['database']):
                # The original Oracle code is authoritative, even when the Java compiler
                # knows this expression. Never split a mixed UI/DB trigger into partial work.
                run_in_database(trigger, block, source)
                continue
            # 4.24: the Java compiler knows DECLAREd local variables (the record events only; the rest stays strict)
            ast = parse(source, extended=trigger['event'] in BACKEND_EVENTS)
            if not ast:
                raise Unsupported('A triggerben nincs végrehajtható forrás; az export ellenőrzése szükséges.')
            if trigger['event'] in BACKEND_EVENTS:
                ast, inlined = procedures.expand(ast)
                if inlined: trigger['inlined_program_units'] = inlined
            # After inlining, so local procedure bodies lose their plumbing too.
            ast, plumbing = strip_framework(ast, catalog)
            if plumbing:
                trigger["framework_calls"] = plumbing
            trigger["ast"] = ast
            if ast and not any(n['op'] != 'noop' for n in flatten(ast)):
                trigger.update(status="converted", target="noop")
                continue
            if trigger["event"] in BACKEND_EVENTS and block is not None:
                if not block["database"]:
                    raise Unsupported("Vezérlőblokk eseménye: nincs generált adatvégpont, amely futtatná; a frontend vagy egy külön akció-adapter feladata.")
                def assignments(nodes):
                    for node in flatten(nodes):
                        if node["op"] == "assign":
                            yield node["target"]
                        elif node["op"] == "call" and node["name"] in model["procedures"]:
                            # OUT / IN OUT arguments write the item just like an assignment.
                            for arg, param in zip(node["args"], model["procedures"][node["name"]]["arguments"]):
                                if "OUT" in param["mode"] and arg["op"] == "ref":
                                    yield arg["name"]
                        elif node["op"] == "if":
                            for branch in node["branches"]:
                                yield from assignments(branch["body"])
                            yield from assignments(node["else"])
                writes = set(assignments(ast))
                if trigger["event"] == "POST-QUERY" and writes & {block["name"] + "." + i["name"] for i in block["db_items"]}:
                    raise Unsupported("POST-QUERY nem írhat adatbázismezőt ebben a snapshot-alapú adapterben.")
                if trigger["event"] != "PRE-INSERT" and writes & {block["name"] + "." + i["name"] for i in block["pk"]}:
                    raise Unsupported("Kulcsmódosító trigger külön adaptert igényel.")
                if trigger["event"] in {"WHEN-VALIDATE-ITEM", "WHEN-VALIDATE-RECORD", "PRE-UPDATE"} and writes & {block["name"] + "." + i["name"] for i in block["db_items"] if not i["update_allowed"]}:
                    raise Unsupported("Nem módosítható adatbázismezőt író trigger külön adaptert igényel.")
                trigger["java"] = Compiler(block, model["procedures"], local_packages).statements(ast)
                trigger["target"] = "backend"
            elif trigger["event"] in UI_EVENTS:
                trigger["actions"] = ui_actions(ast, model)
                trigger["target"] = "frontend"
            elif not list(n for n in flatten(ast) if n["op"] != "noop"):
                trigger["target"] = "noop"
            else:
                raise Unsupported(event_reason(trigger["event"]))
            trigger["status"] = "converted"
        except MigrationError:
            raise
        except Exception as error:
            exc = adapter_error(error)
            if ai_mode and trigger["event"] == "WHEN-BUTTON-PRESSED" and source.strip():
                # Not a pure screen step: a backend action whose body the Java assistant writes. A query button
                # (own DEFAULT_WHERE + EXECUTE_QUERY) keeps the query contract: the screen shows the returned rows.
                target = ai_triggers.query_target(source, model)
                if target:
                    trigger['query_action'] = {'target': target, 'unit': trigger.get('owner') or '', 'context': None,
                                               'variants': [], 'message_units': {}, 'java': None, 'ai': True,
                                               'prepared': {'binds': [], 'outs': [], 'inputs': []}}
                trigger.update(status='converted', target='action', ai=True, ai_reason=str(exc))
                trigger.pop('reason', None)
                continue
            # Not translatable to Java: run the original PL/SQL in Oracle instead, if it only needs the database.
            if trigger["event"] == "WHEN-BUTTON-PRESSED" and source.strip():
                # Forms DEFAULT_WHERE builders need an explicit query adapter:
                # the original PL/SQL builds the filter, JDBC queries the block.
                from .query_actions import query_action
                if model.get('options', {}).get('query_action_mode', 'java') == 'java':
                    # 4.23: only the SQL request, in readable Java (query_java); the PL/SQL adapter is the fallback.
                    from .query_java import plan as java_query
                    try:
                        java = java_query(trigger, source, model, catalog)
                        trigger['query_action'] = {'target': java['target'], 'unit': java['unit'], 'context': None,
                                                   'variants': [], 'message_units': {}, 'java': java,
                                                   'prepared': {'binds': [], 'outs': [], 'inputs': java['inputs']}}
                        trigger.update(status='converted', target='action')
                        trigger.pop('reason', None)
                        continue
                    except Exception as java_error:
                        trigger['query_java_reason'] = str(adapter_error(java_error))
                try:
                    trigger['query_action'] = query_action(trigger, source, model, catalog)
                    trigger.update(status='converted', target='action')
                    trigger.pop('reason', None)
                    continue
                except Exception as query_error:
                    query_reason = str(adapter_error(query_error))
                # A button that only needs the database: its action endpoint runs the PL/SQL as written.
                try:
                    trigger["passthrough_plan"] = action_passthrough(trigger, source, model, catalog)
                    plan = trigger["passthrough_plan"]
                    trigger["passthrough"] = {"binds": [b["source"] for b in plan["binds"]], "written": [b["source"] for b in plan["outs"]],
                                              "inputs": plan["inputs"],
                                              "globals": [b["source"] for b in plan["globals"]], "commands": plan["commands"],
                                              "notes": plan["notes"], "units": plan["units"], "unresolved": plan["unresolved"], "guarded": plan["guarded"]}
                    trigger.update(status="converted", target="action")  # runs in the button's action endpoint
                    trigger.pop("reason", None)
                    continue
                except Exception as failure:
                    second = adapter_error(failure)
                    trigger['adapter_diagnostics'] = {'frontend': str(exc), 'query': query_reason, 'plsql': str(second)}
                    exc = Unsupported(str(exc) + " Átfuttatás az adatbázisban sem lehetséges: " + str(second))
                    if 'SET_BLOCK_PROPERTY' in str(second).upper():
                        trigger['query_action_reason'] = query_reason
                        # Keep the specific recognition failure visible before
                        # the generic UI / native-PLSQL refusals.
                        exc = Unsupported('Lekérdezés-adapter: ' + query_reason + ' ' + str(exc))
            if trigger["event"] in PASSTHROUGH_EVENTS and block is not None and block["database"] and source.strip():
                try:
                    run_in_database(trigger, block, source)
                    trigger.pop("reason", None)
                    continue
                except Exception as failure:
                    exc = Unsupported(str(exc) + " Átfuttatás az adatbázisban sem lehetséges: " + str(adapter_error(failure)))
            scope = trigger_scope(trigger, block, source, catalog, model)
            if trigger["event"] in STARTUP_EVENTS or trigger["event"] in DATA_KEYS:
                # The parser message says nothing about the screen task; the event reason does.
                exc = Unsupported(event_reason(trigger["event"]))
                access = access_checks(source)
                if trigger["event"] in STARTUP_EVENTS and access:
                    model["issues"].append({"code": "STARTUP_ACCESS", "owner": trigger["block"] or form_owner, "scope": "review",
                                            "detail": trigger["id"] + ": az indítási kód leállíthatja a formot (" + ", ".join(access)
                                                      + "); ha jogosultságot ellenőriz, a host menü-/szerepkör-jogosultsága váltja ki."})
            trigger["status"], trigger["target"], trigger["reason"] = "review", "manual", str(exc)
            model["issues"].append({"code": "UNSUPPORTED_TRIGGER", "owner": trigger["block"] or form_owner, "scope": scope, "detail": trigger["id"] + ": " + str(exc)})
    for trigger in model["triggers"]:
        # Screen-side triggers that still decide what an endpoint may do (never blockers):
        # a key override that drops the default operation, start-up item write permissions.
        if trigger["status"] == "framework" or trigger.get("target") == "noop":
            continue
        source = (trigger.get("replacement") or {}).get("source", trigger["source"])
        if trigger["event"] in DATA_KEYS:
            key_trigger_effect(trigger, source, model, catalog)
        elif trigger["event"] in STARTUP_EVENTS:
            startup_item_permissions(trigger, source, model)
    for finding in runtime_block_properties(model):
        prop = finding["property"]
        targets = [blocks[finding["block"]]] if finding["block"] else list(model["blocks"])
        if prop in RUNTIME_ALLOWED_PROPERTIES:
            operation = RUNTIME_ALLOWED_PROPERTIES[prop]
            for b in targets:
                b["runtime_operations"] = sorted(set(b.get("runtime_operations", [])) | {operation})
            scope = operation
            detail = (finding["origin"] + ": futásidőben állított " + prop + " (" + finding["call"] + "); "
                      "ha jogosultsághoz kötött, a projekt jogosultságkezelése kezeli; egyéb feltételt a ServiceImpl-ben kell megvalósítani.")
        else:
            # The WHERE and the order concern the queries only: the DML works on the record's key.
            scope = "read" if prop in {"DEFAULT_WHERE", "ONETIME_WHERE", "ORDER_BY"} else "all" if prop in RUNTIME_READ_PROPERTIES else "write"
            detail = (finding["origin"] + ": futásidőben módosított " + prop + " (" + finding["call"] + "); "
                      "a ServiceImpl lekérdezését/DML-jét ennek megfelelően kell átvenni.")
            button = java_query_origin(model, finding)
            if button and prop in {"DEFAULT_WHERE", "ONETIME_WHERE", "ORDER_BY"}:
                scope = "review"  # the button's Java query applies it; the block's own query keeps the form's WHERE
                detail += (" A(z) " + button + " Java-lekérdezése ezt alkalmazza; a blokk saját keresése/listája az eredeti "
                           "WHERE-rel fut (a Formsban a gomb után a következő lekérdezés is ezt használná).")
        issue = {"code": "RUNTIME_BLOCK_PROPERTY", "owner": finding["block"] or form_owner, "scope": scope, "detail": detail}
        if issue not in model["issues"]:
            model["issues"].append(issue)
    startup_plan(model, catalog)
    commit_plan(model, catalog)
    plsql_library(model, catalog)  # every program unit, even if no trigger uses it
    plsql_library(model, catalog, ui=True)
    model.pop("_plsql_library", None)  # working cache only; plsql_units is the serialisable summary
    model.pop("_plsql_library_ui", None)
    finalize_capabilities(model)


def finalize_capabilities(model: dict) -> None:
    """Per-operation blockers plus one module-level gate.

    Module-wide review requirements (screen/scaffold mode, OLB inheritance) are
    not repeated per method: they gate every otherwise clean operation behind the
    generated MODULE_REVIEWED switch. can_* is true only without any of them.
    """
    from .backend_queries import query_capabilities
    form_owner = "@FORM:" + model["name"]
    policy = [i for i in model["issues"] if i["owner"] == form_owner and i["code"] in POLICY_CODES and i["scope"] == "all"]
    gate = list(dict.fromkeys(i["detail"] for i in policy))
    model["module_gate"] = {"required": bool(gate), "switch": "MODULE_REVIEWED", "reasons": gate,
                            "codes": sorted({i["code"] for i in policy})}
    policy_ids = {id(i) for i in policy}
    for b in model["blocks"]:
        relevant = [i for i in model["issues"] if i["owner"] in {form_owner, b["name"]} and id(i) not in policy_ids]
        blockers = {op: list(dict.fromkeys(i["detail"] for i in relevant if i["scope"] in scopes))
                    for op, scopes in OPERATION_SCOPES.items()}
        if not b["writable"]:
            for op in ("create", "update", "delete"):
                blockers[op].append(WRITE_APPROVAL)
        b["blockers"] = blockers
        # Complete lists, module gate included, for reports and --strict.
        b["read_blockers"] = gate + blockers["read"]
        b["write_blockers"] = list(dict.fromkeys(gate + blockers["create"] + blockers["update"] + blockers["delete"]))
        updatable = any(i["update_allowed"] and not i["primary_key"] for i in b["db_items"])
        ready = {"read": b["database"] and b["query_allowed"] and not blockers["read"],
                 "create": b["database"] and b["insert_allowed"] and not blockers["create"],
                 "update": b["database"] and b["update_allowed"] and not blockers["update"] and updatable,
                 "delete": b["database"] and b["delete_allowed"] and not blockers["delete"]}
        live = bool(model.get("options", {}).get("backend_live"))
        for op, value in ready.items():
            b["ready_" + op] = bool(value)
            b["can_" + op] = bool(value) and (not gate or live)  # backend_live: MODULE_REVIEWED starts as true
        query_capabilities(model, b)
        b["endpoint_plan"], b["skipped_operations"] = endpoint_plan(b)


def endpoint_plan(block: dict) -> tuple[dict, list[dict]]:
    """Only operations the Forms block itself allows become endpoints.

    A statically forbidden operation is generated only when code enables it at
    runtime (SET_BLOCK_PROPERTY ..._ALLOWED); such an endpoint stays blocked
    until reviewed. A bound WHERE replaces the parameterless list with search.
    """
    if not block["database"]:
        return {}, []
    runtime = set(block.get("runtime_operations", []))
    plan = block.get("query_plan", {})
    search = plan.get("status") == "compiled" and bool(plan.get("binds"))
    readable = block["query_allowed"] or "read" in runtime
    changeable = block["update_allowed"] or "update" in runtime
    updatable = any(i["update_allowed"] and not i["primary_key"] for i in block["db_items"])
    wanted = {"list": readable and not search, "search": readable and search,
              "create": block["insert_allowed"] or "create" in runtime,
              "update": changeable and updatable,
              "delete": block["delete_allowed"] or "delete" in runtime}
    not_readable = "QueryAllowed=false: a Forms-blokk nem kérdezhető le."
    reasons = {"list": not_readable if not readable else
                       "A WHERE Forms-mezőre hivatkozik: paraméter nélküli lista nem készül, a search" + block["class"] + " váltja ki.",
               "search": not_readable if search and not readable else "",
               "create": "InsertAllowed=false: a Forms-blokk nem enged beszúrást.",
               "update": "UpdateAllowed=false: a Forms-blokk nem enged módosítást." if not changeable else
                         "Nincs módosítható, nem kulcs adatbázisoszlop.",
               "delete": "DeleteAllowed=false: a Forms-blokk nem enged törlést."}
    skipped = [{"operation": op, "reason": reasons[op]} for op, keep in wanted.items() if not keep and reasons[op]]
    return wanted, skipped

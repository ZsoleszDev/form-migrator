"""backend_trigger_mode=ai: every DPS method is written by the Java assistant, from a short hint region.

A block's data endpoints (list, search, create, update, delete) and the LOVs get the same frame as a trigger: the
signature, @Transactional and log1x with an empty body, and a //region AI-feladat above it. The region holds only what
the method needs to work like the Forms block: the SQL Forms runs for the operation (the block query, the
INSERT/UPDATE/DELETE, the FOR UPDATE NOWAIT lock), which Row field each column fills, the record's trigger hooks in
the Forms order and what the method returns. The model writes it in the service with native SQL through the
EntityManager; no CRUD machinery, repository or JDBC is generated around it. The Domain entity and the empty
Repository of the table are there for the developer, who may move the SQL into the repository later.
"""
from __future__ import annotations

import re

from .common import decode_line_escapes

# The region title the Java assistant offers a method by (backend_assistant.ORIGINAL_CODE), like "Eredeti Forms-kód".
TITLE = "AI-feladat"
JAVA_TYPES = {"text": "String", "number": "BigDecimal", "datetime": "LocalDateTime", "unsupported": "String"}
OPERATION_TITLES = {"list": "lista", "search": "keresés", "create": "új rekord", "update": "módosítás", "delete": "törlés"}
VALIDATION_EVENTS = ("POST-CHANGE", "WHEN-VALIDATE-ITEM", "WHEN-VALIDATE-RECORD")


WIDTH = 116  # a comment line of the hints stays within the 120-character Checkstyle limit


def region(title: str, lines: list[str], indent: str) -> str:
    """The hints as a foldable comment: the Java assistant offers every method that has one; it stays on apply.
    A long hint goes on to indented lines (an SQL statement too: it is a comment, not code)."""
    result = [indent + "//region " + TITLE + ": " + title]
    for line in lines:
        result += comment(line, indent)
    result.append(indent + "//endregion")
    return "\n".join(result) + "\n"


def comment(line: str, indent: str) -> list[str]:
    """One hint as // lines: a long one goes on to indented lines (an SQL statement too: it is a comment)."""
    import textwrap
    result = []
    width = max(60, WIDTH - len(indent) - 3)
    for part in line.split("\n"):
        part = part.rstrip().replace("\\", "[backslash]")
        if not part.strip():
            result.append(indent + "//")
            continue
        lead = re.match(r"\s*", part).group(0)
        wrapped = textwrap.wrap(part, width, initial_indent="", subsequent_indent=lead + "   ",
                                break_long_words=False, break_on_hyphens=False) or [part]
        result += [indent + "// " + (lead if n == 0 else "") + piece for n, piece in enumerate(wrapped)]
    return result


def hooks(model: dict, b: dict) -> tuple[list[str], dict[str, list[str]]]:
    """The block's trigger hooks (private, the commented Forms code as their body) and their names by Forms event.

    trigger<Event>[<Item>]<Block> (triggerPostQueryOrders, triggerWhenValidateItemCodeOrders): one per trigger; the
    CRUD hints call them by name, in the Forms order.
    """
    from . import ai_triggers
    from .generate import converted_backend_triggers
    taken, sources, calls = set(), [], {}
    on_query = []
    for trigger in converted_backend_triggers(model, b):
        if not trigger.get("ai"):
            continue
        method = ai_triggers.hook_name(trigger, taken) + b["class"]
        calls.setdefault(trigger["event"], []).append(method)
        if trigger["event"] == "POST-CHANGE" and trigger.get("ai_on_query"):
            on_query.append(method)  # Forms fires POST-CHANGE on fetch too (it writes no queried column)
        sources.append(f"    private void {method}({b['class']}Row row, RuleContext context) {{\n"
                       + ai_triggers.hook_body(trigger, model).rstrip("\n") + "\n    }")
    if on_query:
        calls["FETCH"] = on_query + calls.get("POST-QUERY", [])
    elif "POST-QUERY" in calls:
        calls["FETCH"] = list(calls["POST-QUERY"])
    return sources, calls


def call_line(calls: dict, events, row: str = "row") -> str:
    """'triggerA(row, context) (EVENT); ...' of the hooks of the given events, in order."""
    parts = [f"{method}({row}, context) ({event})" for event in events for method in calls.get(event, [])]
    return "; ".join(parts)


def block_sql(model: dict, b: dict) -> dict:
    """The SQL Forms runs for the block, with :field binds (the Row field of the same name)."""
    from .generate import column_sql, key_sql, table_alias
    db, pk = b["db_items"], b["pk"]
    table = b["table"]
    select = "SELECT " + ", ".join(column_sql(i) for i in db) + " FROM " + table + table_alias(b, table)
    key = " AND ".join(key_sql(i) for i in pk)
    order = ", ".join(i["column"] for i in pk) or (db[0]["column"] if db else "1")
    query = b.get("query_plan") or {}
    where = ""
    binds = []
    if query.get("status") == "compiled":
        from .jpa_crud import bind_names
        binds = query.get("binds") or []
        where = bind_names(query.get("where_sql") or "", binds)
        order = bind_names(query.get("order_sql") or "", binds) or order
    inserts = [i for i in db if (i["insert_allowed"] or i["primary_key"]) and not i.get("rowid")]
    updates = [i for i in db if i["update_allowed"] and not i["primary_key"]]
    return {
        "query": select + (" WHERE " + where if where else "") + " ORDER BY " + order,
        "binds": binds,
        "row": select + " WHERE " + key,
        "lock": select + " WHERE " + key + " FOR UPDATE NOWAIT",
        "insert": ("INSERT INTO " + table + " (" + ", ".join(i["column"] for i in inserts) + ") VALUES ("
                   + ", ".join(":" + i["field"] for i in inserts) + ")") if inserts else "",
        "update": ("UPDATE " + table + " SET " + ", ".join(i["column"] + " = :" + i["field"] for i in updates)
                   + " WHERE " + key) if updates else "",
        "delete": "DELETE FROM " + table + " WHERE " + key,
    }


def mapping_line(b: dict) -> str:
    """Column -> Row field (type) in select-list order: the Object[] of a native query row. A query of one column
    returns the values themselves (List<Object>): there is no array to index."""
    if len(b["db_items"]) == 1:
        item = b["db_items"][0]
        return (f"a lekérdezés egy oszlopa maga a sor (Object, nem Object[]): {item['column']} -> {item['field']} "
                f"({JAVA_TYPES[item['type']]})")
    return ", ".join(f"[{n}] {i['column'] if not i.get('rowid') else 'ROWID'} -> {i['field']} ({JAVA_TYPES[i['type']]})"
                     for n, i in enumerate(b["db_items"]))


def check_line(b: dict) -> str:
    """The item properties Forms checks itself (Required, MaximumLength, NUMBER precision): one line."""
    fields = [i for i in b["items"] if i["kind"] != "button" and not i.get("rowid")]
    required = [i["name"] for i in fields if i["required"]]
    lengths = [f"{i['name']} {i['max_length']}" for i in fields if i["type"] == "text" and i["max_length"]]
    digits = [f"{i['name']} {i['precision'] - i.get('scale', 0)} egész, {i.get('scale', 0)} tizedes" for i in fields
              if i["type"] == "number" and i.get("precision")]
    parts = (["kötelező: " + ", ".join(required)] if required else []) + (["max. hossz: " + ", ".join(lengths)] if lengths else []) \
        + (["számjegyek: " + "; ".join(digits)] if digits else [])
    return "Mezőellenőrzés (HTTP 422, FormsChecks): " + "; ".join(parts) + "." if parts else ""


def list_lines(model: dict, b: dict, op: str, calls: dict) -> list[str]:
    sql = block_sql(model, b)
    lines = ["SQL (a Forms-blokk lekérdezése): " + sql["query"] + " OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY"]
    if op == "search":
        lines.append("Feltételek: " + ", ".join(f":{v['field']} = request.criteria().{v['field']}" for v in sql["binds"])
                     + "; lapozás: request.offset(), request.limit().")
    lines.append("Lapozás: offset >= 0, limit 1..200 (különben 422); :limit = limit + 1, a plusz sor jelzi: hasMore = true"
                 " (azt a sort ne add vissza).")
    lines.append(f"Sor -> new {b['class']}Row(): " + mapping_line(b) + ".")
    fetch = call_line(calls, ["FETCH"]) if calls.get("FETCH") else ""
    if fetch:
        lines.append("Minden sorra (POST-QUERY): " + fetch.replace(" (FETCH)", "") + ".")
    lines.append("Vissza: new PageResult<>(rows, context.messages(), hasMore); var context = new RuleContext().")
    return lines


def write_lines(model: dict, b: dict, op: str, calls: dict) -> list[str]:
    sql = block_sql(model, b)
    events = {e for e in calls}
    lines = ["Lépések Forms-sorrendben (var context = new RuleContext()):"]
    step = 0

    def add(text: str):
        nonlocal step
        if text:
            step += 1
            lines.append(f"{step}. {text}")
    key = ", ".join(i["field"] for i in b["pk"])
    if op in {"update", "delete"}:
        record = "value" if op == "update" else "original"
        if op == "update":
            add(f"A kulcs ({key}) nem változhat: original és value kulcsa egyezik, különben 422.")
        add("Zárolás: " + sql["lock"] + f" (:mező = original.mező). Nincs sor: 404; ha egy oszlop eltér az original-tól: 409 "
            "\"A rekord közben megváltozott.\"")
    else:
        record = "row"
    if op in {"create", "update"}:
        # Forms converts the text of a CaseRestriction item as it is typed; the screen does not, so the save does.
        cases = [f"{record}.{i['field']} = SqlValues.{i['case_restriction']}({record}.{i['field']})"
                 for i in b["items"] if i["kind"] != "button" and i.get("case_restriction")]
        if cases:
            add("Kis-/nagybetűs mezők (Forms CaseRestriction): " + "; ".join(cases) + ".")
        add(check_line(b))
    if op == "create":
        if b.get("sequence"):
            add(f"Kulcs a szekvenciából: SELECT {b['sequence']}.NEXTVAL FROM DUAL -> row.{b['pk'][0]['field']}"
                " (a képernyő nem adja meg).")
    validation = [e for e in VALIDATION_EVENTS if e in events] if op in {"create", "update"} else []
    before = {"create": ["PRE-INSERT"], "update": ["PRE-UPDATE"], "delete": ["ON-CHECK-DELETE-MASTER", "PRE-DELETE"]}[op]
    triggers = call_line(calls, validation + before, record)
    if triggers:
        add("Triggerek: " + triggers + ".")
    replacing = {"create": "ON-INSERT", "update": "ON-UPDATE", "delete": "ON-DELETE"}[op]
    statement = {"create": sql["insert"], "update": sql["update"], "delete": sql["delete"]}[op]
    if calls.get(replacing):
        add(call_line(calls, [replacing], record) + ": ez végzi a mentést (a Forms saját " + statement.split(" ", 1)[0]
            + "-ja helyett).")
    elif statement:
        add("SQL: " + statement + f" (:mező = {record}.mező).")
    after = call_line(calls, [{"create": "POST-INSERT", "update": "POST-UPDATE", "delete": "POST-DELETE"}[op]], record)
    if after:
        add("Utána: " + after + ".")
    lines.append({"create": "Vissza: new RowResult<>(row, context.messages()).",
                  "update": "Vissza: new RowResult<>(value, context.messages()).",
                  "delete": "Vissza: context.messages()."}[op])
    return lines


def contract(o: dict, model: dict, calls: dict) -> dict:
    """What the written method must contain, checked on the model's answer (answer_checks): the statements of the
    hints as written, and the hooks in their order. The paging of a list may be written either way (OFFSET/FETCH in
    the SQL or setFirstResult/setMaxResults), so only the block query itself is required."""
    if o["op"] == "lov":
        plan = o["lov"]
        inner = plan["sql"].split("SELECT * FROM (\n", 1)[-1].split("\n) lov", 1)[0]
        return {"sql": [inner], "calls": []} if not plan["blockers"] else {}
    b, op = o["block"], o["op"]
    sql = block_sql(model, b)
    if op in {"list", "search"}:
        return {"sql": [sql["query"]], "calls": list(calls.get("FETCH", []))}
    events = [e for e in VALIDATION_EVENTS] if op in {"create", "update"} else []
    events += {"create": ["PRE-INSERT", "ON-INSERT", "POST-INSERT"], "update": ["PRE-UPDATE", "ON-UPDATE", "POST-UPDATE"],
               "delete": ["ON-CHECK-DELETE-MASTER", "PRE-DELETE", "ON-DELETE", "POST-DELETE"]}[op]
    replacing = {"create": "ON-INSERT", "update": "ON-UPDATE", "delete": "ON-DELETE"}[op]
    statement = {"create": sql["insert"], "update": sql["update"], "delete": sql["delete"]}[op]
    statements = ([sql["lock"]] if op in {"update", "delete"} else []) + ([statement] if statement and not calls.get(replacing) else [])
    return {"sql": statements, "calls": [method for event in events for method in calls.get(event, [])]}


def statements(o: dict, model: dict, calls: dict) -> list[tuple[str, str]]:
    """(label, SQL) of a data endpoint for verify-db: what the hints tell the model to run, compiled in the database
    before anybody writes the method."""
    if o["op"] == "lov":
        return []  # the LOV statement is listed by its plan (compact_backend.db_statements)
    b, op = o["block"], o["op"]
    found = contract(o, model, calls)["sql"]
    if op in {"list", "search"}:
        found = [found[0] + " OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY"]
    return [(b["name"] + " " + OPERATION_TITLES[op], statement) for statement in found]


def crud_frame(o: dict, model: dict, calls: dict, log1x, user_type: str, transaction: str, info: str) -> str:
    """One data endpoint of a block: the empty log1x frame under its hint region."""
    b, op = o["block"], o["op"]
    lines = list_lines(model, b, op, calls) if op in {"list", "search"} else write_lines(model, b, op, calls)
    title = b["name"] + " / " + OPERATION_TITLES[op]
    return (f"    // {info}\n"
            f"    {transaction}\n"
            "    @Override\n"
            f"    public {o['returns']} {o['method']}({user_type} user, {o['args']}) throws Exception {{\n"
            f"        {log1x(o, '() -> {')}\n"
            + region(title, lines, "            ") +
            "        });\n"
            "    }")


def lov_frame(o: dict, log1x, user_type: str) -> str:
    """A LOV endpoint: the record group's query (with the typed-text filter) for the assistant; a LOV without one
    (static or missing record group) answers HTTP 501 and is no task."""
    plan = o["lov"]
    users = ", ".join(plan["users"]) or "—"
    info = f"    // {plan['name']} LOV ({users}; rekordcsoport: {plan['record_group'] or '—'}).\n"
    head = ("    @Transactional(readOnly = true)\n    @Override\n"
            f"    public LovResult {o['method']}({user_type} user, LovRequest request) throws Exception {{\n"
            f"        {log1x(o, '() -> {')}\n")
    if plan["blockers"]:
        reason = "; ".join(plan["blockers"])
        return (info + f"    // Nem futtatható: {reason}\n" + head
                + '            throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, "Ez a LOV ebben a modulban még nem érhető el.");\n'
                + "        });\n    }")
    labels = lov_labels(plan)
    # :term: the text typed into the field (a null is bound as '' - "every row" in the SQL); :limit: the helper gives
    # the default and the cap (the screen sends no limit: a null would fetch no row)
    params = [":term = request.term() (a mezőbe gépelt szöveg: az így kezdődő sorok; üres: mind)",
              ":limit = LovQuery.limit(request.limit()) (hiányzó: 50, legfeljebb 500)"]
    params += [f":{b['parameter']} = LovQuery.{b['type']}(request.parameters(), {quote(b['source'])})" for b in plan["binds"]]
    lines = ["SQL (a rekordcsoport lekérdezése, a beírt szövegre szűrve):", *plan["sql"].split("\n"),
             "Paraméterek: " + "; ".join(params) + ".",
             ("Vissza: new LovResult(LovQuery.rows(rows, " + ", ".join(quote(label) for label in labels) + "))"
              " (a sorok oszlopnév szerint, a select-lista sorrendjében)." if labels else
              "Vissza: new LovResult(sorok oszlopnév szerint: Map<String, Object>, a select-lista nevei nagybetűvel).")]
    return info + head + region(plan["name"], lines, "            ") + "        });\n    }"


def lov_labels(plan: dict) -> list[str]:
    """The column names Oracle gives the select list (the LOV's screen contract); none when they need an alias."""
    from .jpa_lov import select_labels
    from .plsql import Unsupported
    from .plsql_passthrough import normal_mode_sql
    try:
        return select_labels(normal_mode_sql(decode_line_escapes(plan["query"])), [c["column"] for c in plan["columns"]])
    except Unsupported:
        return []


def quote(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def evidence(o: dict, model: dict) -> str:
    """backend-evidence.md: the same hints, for the developer."""
    b, op = o["block"], o["op"]
    _, calls = hooks(model, b)
    lines = list_lines(model, b, op, calls) if op in {"list", "search"} else write_lines(model, b, op, calls)
    return "AI-RA VÁR: " + b["name"] + " / " + OPERATION_TITLES[op] + "\n\n" + "\n".join(lines)


def sql_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


MAX_SQL_HINTS = 8


def sql_hints(sources: list[str], model: dict, block: dict | None = None) -> list[str]:
    """The SQL of the Forms code as native SQL with named parameters, one line per statement: where each parameter
    comes from and where a SELECT INTO's values go. block: a record hook's block, whose items are fields of `row`.

    Only a restatement of statements that are in the commented code anyway (the same text, binds renamed): the model
    runs them through the EntityManager as written."""
    from .ai_triggers import code_text
    from .jpa_sql import find
    from .common import name
    fields = {(b["name"].upper(), i["name"].upper()): i["field"] for b in model.get("blocks", []) for i in b["items"]}
    own = block["name"].upper() if block else None
    lines, total = [], 0
    for source in sources:
        text = code_text(source)
        for found in find(text):
            total += 1
            if len(lines) >= MAX_SQL_HINTS:
                continue
            names: dict[str, str] = {}

            def describe(reference: str) -> tuple[str, str]:
                """A bind -> (parameter name, its Java value or screen source)."""
                parts = [p.strip().upper() for p in reference.lstrip(":").split(".")]
                if len(parts) == 1 and own:
                    parts = [own, parts[0]]
                if len(parts) == 2 and (parts[0], parts[1]) in fields:
                    field = fields[(parts[0], parts[1])]
                    value = "row." + field if parts[0] == own else parts[0] + "." + parts[1]
                    return field, value
                if len(parts) == 2:  # :GLOBAL.X, :PARAMETER.X, :SYSTEM.X or an unknown item
                    return name((parts[0] if parts[0] not in {"GLOBAL", "PARAMETER", "SYSTEM"} else parts[0].lower())
                                + "_" + parts[1]), parts[0] + "." + parts[1]
                return name(parts[0]), parts[0]

            tokens = found.tokens
            skip, targets = range(0), []
            if found.kind == "select":  # SELECT ... INTO targets FROM ...: the INTO part is the Java side
                depth, start = 0, None
                for k, token in enumerate(tokens):
                    depth += {"(": 1, ")": -1}.get(token[1], 0)
                    if depth == 0 and token[1].upper() == "INTO" and start is None:
                        start = k
                    elif depth == 0 and token[1].upper() == "FROM" and start is not None:
                        skip, targets = range(start, k), tokens[start + 1:k]
                        break
            pieces, params, previous = [], [], None
            for k, token in enumerate(tokens):
                if k in skip:
                    previous = None
                    continue
                value = token[1]
                if token[0] == "bind":
                    parameter, source = describe(value)
                    if names.get(parameter, source) != source:
                        parameter = name(source.replace(".", "_"))
                    names[parameter] = source
                    if (parameter, source) not in params:
                        params.append((parameter, source))
                    value = ":" + parameter
                spaced = previous is None or token[2] > previous
                pieces.append((" " if spaced and pieces else "") + value)
                previous = token[3]
            statement = sql_text("".join(pieces))
            line = ("SQL (sorok): " if found.kind in {"cursor", "loop"} else "SQL: ") + statement
            if params:
                line += " — " + ", ".join(f":{p} = {v}" for p, v in params)
            if targets:
                outs = []
                for target in "".join(t[1] if t[0] != "bind" else " " + t[1] + " " for t in targets).split(","):
                    target = target.strip()
                    if not target:
                        continue
                    outs.append(describe(target)[1] if target.startswith(":") else target)
                line += "; eredmény -> " + ", ".join(outs)
            lines.append(line)
    if total > len(lines):
        lines.append(f"(+{total - len(lines)} további SQL-utasítás a kódban.)")
    return lines

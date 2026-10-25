"""Local, endpoint-scoped Java implementation proposals, separate from generation.

The generator supplies one complete method, its entities and the wiring.
The model supplies business logic, repository methods and supplemental DTO fields.
It cannot choose output paths, enable a module, execute commands, or replace the
existing controller/endpoint DTO APIs. Applying a draft preserves logging and
transactions and checks that all affected files still match their source hashes.
"""
from __future__ import annotations

from dataclasses import dataclass
import difflib
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile

from .common import MigrationError, RESERVED, name, read_json, write_json
from .java_scan import java_mask, matching_brace  # noqa: F401  (re-exported: engine.java_mask)
from . import ollama

VERSION = "backend-assistant-7"
AREA = "analysis/backend-assistant"
ID = re.compile(r"[a-f0-9]{20}")
JAVA_NAME = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
DB_NAME = re.compile(r"[A-Z][A-Z0-9_$#]{0,127}(?:\.[A-Z][A-Z0-9_$#]{0,127})?")
MAX_RESPONSE_BYTES = 1_000_000
# A method the assistant offers: it still carries the original Forms source as a comment.
ORIGINAL_CODE = re.compile(r"^[ \t]*//[ \t]*region[ \t]+Eredeti Forms-k[óo]d", re.M | re.I)
# The original Forms source the generator put into the method as a comment; kept on rewrite.
ORIGINAL_REGION =re.compile(r"^[ \t]*//[ \t]*region\b[^\n]*\n(?:[^\n]*\n)*?[ \t]*//[ \t]*endregion\b[^\n]*", re.M)

# The model answers in Markdown with ```java blocks (assistant_context builds the request,
# assistant_reply reads the answer). The normalized answer below is what proposals store;
# apply_proposal re-validates it and rebuilds every file from it.
RESPONSE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["status", "summary", "body", "imports", "private_methods", "assumptions", "missing_context", "test_cases"],
    "properties": {
        "status": {"type": "string", "enum": ["proposal", "needs_context"]},
        "summary": {"type": "string"}, "body": {"type": "string"},
        "private_methods": {"type": "string"},
        **{key: {"type": "array", "items": {"type": "string"}}
           for key in ("imports", "assumptions", "missing_context", "test_cases")},
    },
}

def digest(value: bytes | str) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def safe_file(root: Path, relative: str) -> Path:
    """No absolute paths, symlinks or escapes, including Windows path spellings."""
    rel = Path(relative)
    if not relative or "\\" in relative or ":" in relative or rel.is_absolute() or ".." in rel.parts:
        raise MigrationError("BACKEND_ASSISTANT: nem biztonságos útvonal.")
    root = root.resolve()
    path = root / rel
    for parent in [path, *path.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            raise MigrationError("BACKEND_ASSISTANT: symlink nem engedélyezett.")
    if not path.resolve().is_relative_to(root):
        raise MigrationError("BACKEND_ASSISTANT: az útvonal kilép a modulból.")
    return path


def method_span(source: str, method: str) -> tuple[int, int, int]:
    if not JAVA_NAME.fullmatch(method):
        raise MigrationError("BACKEND_ASSISTANT: hibás Java metódusnév.")
    mask = java_mask(source)
    # public: an endpoint; private: an AI hook of a CRUD flow or of commitForm (backend_trigger_mode=ai).
    pattern = r"\b(?:public|private)\s+[\w.$<>?, \[\]]+\s+" + re.escape(method) + r"\s*\([^{};]*\)\s*(?:throws\s+[\w., ]+)?\s*\{"
    matches = list(re.finditer(pattern, mask))
    if len(matches) != 1:
        raise MigrationError("BACKEND_ASSISTANT: a célmetódus nem egyértelmű: " + method)
    start, opening = matches[0].start(), matches[0].end() - 1
    return start, opening, matching_brace(mask, opening)


def contract_excerpt(source: str) -> dict:
    mask = java_mask(source)
    declarations = re.findall(r"(?:public|private|protected)\s+[^;{}\n]+(?:[;{])", mask)
    return {"imports": re.findall(r"^import .+;", source, re.M),
            "declarations": declarations}


INIT_EVENTS = ("PRE-FORM", "WHEN-NEW-FORM-INSTANCE")
HOOK = re.compile(r"\bprivate\s+void\s+([A-Za-z_$][\w$]*)\s*\(([^{};]*)\)\s*\{")
REGION_TITLE = re.compile(r"^[ \t]*//[ \t]*region[ \t]+Eredeti Forms-k[óo]d:[ \t]*([^\n]*)$", re.M)


def ai_hooks(service: str, ir: dict, endpoint_methods: set) -> list[dict]:
    """The private methods that still carry the commented Forms code: CRUD and commit trigger hooks (ai mode)."""
    mask = java_mask(service)
    classes = {b["class"] + "Row": b for b in ir.get("blocks", [])}
    result = []
    for match in HOOK.finditer(mask):
        method = match.group(1)
        if method in endpoint_methods:
            continue
        end = matching_brace(mask, match.end() - 1)
        title = REGION_TITLE.search(service, match.end(), end)
        if not title:
            continue
        params = " ".join(service[match.start(2):match.end(2)].split())
        label = title.group(1).strip()
        owner, _, event = label.partition(" / ")
        event = event.split(" (")[0].strip()
        row = re.match(r"(\w+Row)\s+row\s*,\s*RuleContext\s+context$", params)
        if row and row.group(1) in classes:
            block = classes[row.group(1)]
            result.append({"method": method, "operation": "hook", "hook_kind": "record", "owner": owner.strip(),
                           "event": event, "block": block["name"], "row_class": row.group(1), "blockers": []})
        elif re.search(r"Map<String,\s*(?:java\.util\.)?Map<String,\s*String>>\s+values\b", params):
            trigger_id = label.split(" (")[0].strip()
            result.append({"method": method, "operation": "hook", "hook_kind": "commit", "owner": trigger_id.split(":", 1)[0],
                           "event": trigger_id.split(":", 1)[-1], "trigger_id": trigger_id, "block": "@FORM", "blockers": []})
    return result


def hook_code(discovery: dict, hook: dict) -> list[str]:
    """The discovery code ids of a hook's trigger (the region holds the same source)."""
    found = []
    for code in discovery.get("code", []):
        if code["kind"] != "trigger" or code.get("name") != hook.get("event"):
            continue
        owner = code.get("owner") or ""
        if hook["hook_kind"] == "record" and (owner == hook["owner"] or (code.get("block") == hook["block"] and owner == hook["owner"])):
            found.append(code["id"])
        elif hook["hook_kind"] == "commit" and not code.get("block"):
            found.append(code["id"])
    if not found and hook["hook_kind"] == "record":
        # a form-level trigger copied to the block (FORM:EVENT@BLOCK): the form's own code
        found = [c["id"] for c in discovery.get("code", []) if c["kind"] == "trigger" and not c.get("block")
                 and c.get("name") == hook.get("event")]
    return found


def hook_contract(hook: dict, ir: dict) -> dict:
    """What a hook's body may use: the record's fields (Forms item -> row.field) or the screen maps of commitForm."""
    if hook["hook_kind"] != "record":
        return {"kind": hook["hook_kind"], "event": hook["event"]}
    block = next(b for b in ir["blocks"] if b["name"] == hook["block"])
    db = {i["name"] for i in block.get("db_items", [])}
    fields = [{"item": block["name"] + "." + i["name"], "field": i["field"], "type": i["type"],
               "column": next((d["column"] for d in block.get("db_items", []) if d["name"] == i["name"]), None),
               "database": i["name"] in db, "primary_key": any(p["name"] == i["name"] for p in block.get("pk", []))}
              for i in block["items"] if i.get("kind") != "button"]
    return {"kind": "record", "event": hook["event"], "block": block["name"], "table": block.get("table"),
            "row_class": hook["row_class"], "fields": fields}


def prepare_workspace(output: Path) -> dict:
    """Index unfinished endpoints without network access or backend modifications."""
    output = output.resolve()
    plan_path = safe_file(output, "analysis/backend-plan.json")
    if not plan_path.is_file():
        return {"version": VERSION, "tasks": [], "notice": "Nincs generált backend."}
    plan = read_json(plan_path)
    effective = read_json(safe_file(output, "analysis/effective-config.json"))
    ir = read_json(safe_file(output, "analysis/form.ir.json"))
    discovery = read_json(safe_file(output, "analysis/discovery/form-map.json"))
    actions = read_json(safe_file(output, "analysis/action-plan.json"))["actions"]
    codes = {c["id"]: c for c in discovery["code"]}
    cls = plan["module_class"]
    target = "backend/DPS/" + cls + "ServiceImpl.java"
    service_path = safe_file(output, target)
    if not service_path.is_file():
        return {"version": VERSION, "tasks": []}
    service = service_path.read_text(encoding="utf-8")
    from .schema_workbench import load_schema, schema_sheet
    schema_bundle = load_schema(output)
    schema = schema_bundle[0]
    from . import java_imports
    formatting_imports = java_imports.load(effective["config"])
    from .input_bindings import sheet as input_sheet
    input_contract = input_sheet(output)
    tasks = []
    endpoints = list(plan["endpoints"])
    # backend_trigger_mode=ai: the record/item triggers of the CRUD flow and the commit triggers are private
    # hooks of the ServiceImpl (triggerOnInsertRendeles ...); each is a task of its own, like an endpoint.
    applied_hooks = {h["method"] for h in plan.get("ai_hooks_applied", [])}
    endpoints += [h for h in ai_hooks(service, ir, {e["method"] for e in endpoints}) if h["method"] not in applied_hooks]
    for endpoint in endpoints:
        if endpoint.get("assistant", {}).get("applied"):
            continue
        # Offered: exactly the methods that still carry the commented original Forms code
        # (//region Eredeti Forms-kód ...): manual buttons, Java/PL-SQL query buttons, gated CRUD.
        method = endpoint["method"]
        try:
            start, opening, end = method_span(service, method)
        except MigrationError:
            continue
        if not ORIGINAL_CODE.search(service[start:end + 1]): continue
        hook = endpoint.get("operation") == "hook"
        manual = endpoint.get("operation") == "action" and endpoint.get("runs") in {"manual", "ai"}
        rewrite = endpoint.get("operation") == "action" and not manual
        blocked = endpoint.get("operation") in {"create", "update", "delete", "list", "search"}
        action = next((a for a in actions if a.get("method_name") == method), None)
        if hook:
            action = {"trigger_id": None, "reachable_code": hook_code(discovery, endpoint), "event_code_candidates": []}
        elif action is None and endpoint.get("owner") == "@INIT":
            action = {"trigger_id": None, "reachable_code": [c["id"] for c in discovery["code"] if c["kind"] == "trigger"
                                                             and not c.get("block") and c.get("name") in INIT_EVENTS],
                      "event_code_candidates": []}
        reachable = list(dict.fromkeys((action or {}).get("reachable_code", [])))
        reachable += [cid for cid in (action or {}).get("event_code_candidates", []) if cid not in reachable]
        if action and action.get("trigger_id") and action.get("trigger_id") not in reachable:
            reachable.insert(0, action["trigger_id"])
        for cid in list(reachable):  # the local units the code calls (packages, procedures): their tables count too
            for call in codes.get(cid, {}).get("calls", []):
                for dependency in call.get("target_ids", []):
                    if dependency not in reachable: reachable.append(dependency)
        if not action:
            reachable += [c["id"] for c in discovery["code"] if c["kind"] == "trigger"
                          and (c.get("block") is None or c.get("owner") == endpoint.get("block") or c.get("block") == endpoint.get("block"))]
            # Follow complete local dependency edges for blocked CRUD triggers.
            for cid in reachable:
                for call in codes.get(cid, {}).get("calls", []):
                    for dependency in call.get("target_ids", []):
                        if dependency not in reachable: reachable.append(dependency)
        sources = [{"id": cid, "owner": codes[cid]["owner"], "name": codes[cid]["name"],
                    "kind": codes[cid]["kind"], "sha256": codes[cid]["sha256"],
                    "source": codes[cid].get("source_view", codes[cid]["source"]),
                    "calls": codes[cid].get("calls", [])} for cid in reachable if cid in codes]
        task_id = digest(target + ":" + method)[:20]
        # A query button's target block (DEFAULT_WHERE + EXECUTE_QUERY) needs its table too.
        relevant_blocks = {endpoint.get("block"), endpoint.get("query_block"),
                           *[(codes[cid].get("block")) for cid in reachable if cid in codes]}
        relevant_binds = {b["name"].split(".", 1)[0] for cid in reachable if cid in codes for b in codes[cid].get("binds", [])}
        relevant_blocks |= relevant_binds
        if hook or endpoint.get("runs") == "ai":
            # backend_trigger_mode=ai: only the tables the code really works on need a structure (its SQL names
            # them); a screen value it reads (a bind) needs no entity. A replacing ON-INSERT/UPDATE/DELETE writes
            # its own block's table, a query button reads its target's.
            relevant_blocks = {endpoint.get("query_block")}
            sequence = any(re.search(r"\.\s*NEXTVAL\b", codes[cid].get("source_view", ""), re.I) for cid in reachable if cid in codes)
            if hook and (endpoint.get("event") in {"ON-INSERT", "ON-UPDATE", "ON-DELETE"} or sequence):
                # its own table (and the repository that hosts the sequence query of a key)
                relevant_blocks.add(endpoint.get("block"))
        relevant_blocks.discard(None)
        blocks = [{k: b.get(k) for k in ("name", "table", "database", "writable", "pk", "db_items", "items", "query_plan")}
                  for b in ir["blocks"] if b["name"] in relevant_blocks]
        contracts = {}
        for file in sorted((output / "backend/CL").glob("*.java")):
            file = safe_file(output, file.relative_to(output).as_posix())
            # Keep contracts locally for apply-time change detection.
            # They are not attached to the default AI request.
            if file.name == "CommonMigrateTools.java":
                contracts[file.name] = contract_excerpt(file.read_text(encoding="utf-8"))
            elif file.name.startswith(cls) and "RestClient" not in file.name:
                contracts[file.name] = file.read_text(encoding="utf-8")
        sheet = schema_sheet(output, sources, blocks, schema_bundle)
        packet = {"version": VERSION, "id": task_id, "module": effective["module"],
                  "target": target, "method": method, "endpoint": endpoint,
                  "kind": "hook" if hook else "implement" if manual or blocked else "rewrite_java",
                  "hook": hook_contract(endpoint, ir) if hook else None,
                  "apply_allowed": bool(manual or rewrite or hook),
                  "source_file_sha256": digest(service), "method_sha256": digest(service[start:end + 1]),
                  "signature": service[start:opening].strip(),
                  "existing_method": service[start:end + 1], "service_contract": contract_excerpt(service),
                  "contracts": contracts, "sources": sources, "blocks": blocks,
                  "schema": schema, "config": {key: effective["config"].get(key) for key in
                    ("java_user_type", "java_empty_package", "dps_package", "java_package", "backend_trigger_mode",
                     "java_checkstyle_format", "java_import_order", "jpa_namespace")},
                  "formatting_imports": formatting_imports,
                  "frontend_inputs": {"revision": input_contract.get("revision"),
                      "rows": [row for row in input_contract["rows"]
                               if row["owner"] == endpoint.get("owner", endpoint.get("block"))],
                      "warnings": input_contract["warnings"]},
                  "source_module_sha256": ir.get("source_sha256"),
                  "external_calls": (action or {}).get("external_calls", []),
                  "module_gate": plan.get("module_gate", {}),
                  "table_requirements": sheet["tables"], "pending_tables": sheet["pending"], "schema_warnings": sheet["warnings"],
                  "notice": "A tiltás oka és a forrás megmarad; az AI-javaslat nem működésigazolás."}
        packet["support_file_sha256"] = {f.relative_to(output).as_posix(): digest(f.read_bytes())
            for f in sorted((output / "backend/DPS").glob("*.java")) if f != service_path}
        packet["fingerprint"] = digest(json.dumps(packet, ensure_ascii=False, sort_keys=True))
        write_json(safe_file(output, AREA + "/tasks/" + task_id + ".json"), packet)
        tasks.append({k: packet[k] for k in ("id", "method", "target", "kind", "fingerprint")}
                     | {"owner": endpoint.get("owner", endpoint.get("block")), "blockers": endpoint.get("blockers", []),
                        "event": endpoint.get("event") or (action or {}).get("event") or "", "block": endpoint.get("block"),
                        "tables": [t["table"] for t in sheet["tables"]], "pending_tables": sheet["pending"]})
    full_sheet = schema_sheet(output, bundle=schema_bundle)
    write_json(safe_file(output, "analysis/schema.json"), schema)
    from .assistant_context import SYSTEM as system_prompt, rules
    index = {"version": VERSION, "module": effective["module"], "tasks": tasks,
             "schema": full_sheet, "input_bindings": input_contract,
             "default_instructions": system_prompt + "\n\n" + rules(),
             "notice": "A javaslatok külön készülnek; a generált végpontokat csak ellenőrzött átvétel módosítja."}
    write_json(safe_file(output, AREA + "/index.json"), index)
    guide = ("# Trigger javásítása\n\n"
             "1. A Java asszisztens fülön válassz modult és végpontot. A felismert táblák oszlopait "
             "importáld CSV-ből, jelöld az adatbázis valódi PK-ját, majd mentsd.\n"
             "2. Kattints a **Trigger javásítása** gombra. Az AI csak a kiválasztott DPS ServiceImpl metódust kapja "
             "(benne a kikommentezett Oracle-forrással), és a metódusban ténylegesen használt táblák "
             "repositoryjának néhány soros leírását. A küldött promptot és az AI nyers válaszát az "
             "**AI-kommunikáció** rész és a /ai-log oldal mutatja.\n"
             "3. Nézd át a kódot, majd **Beillesztés a fájlokba**. A DPS ServiceImpl metódusa, a JPA entityk, "
             "a saját repositoryk és a szükséges kiegészítő DTO-k együtt frissülnek. A meglévő Controller és "
             "végponti szerződés megmarad. A friss modul ZIP-je letölthető.\n\n"
             "Az adatbázis-műveletek entitykkel, a repositoryban futnak; a service üzleti logikát és "
             "repositoryhívásokat tartalmaz. Nem kell promptot írni. Hiányzó külső rutint vagy céges API-t "
             "csak akkor kell pótolni a kiegészítő forrásban, ha a működéshez szükséges.\n\n"
             "A képernyőértékek külön bekötése opcionális haladó eszköz; nem feltétele a Java-átírásnak. "
             "A meglévő log1x és tranzakciós keret megmarad. A céges projektben JPA/Hibernate és a megfelelő "
             "entity/repository csomagok felismerése szükséges. Java 11 mellett az alap névtér javax.persistence; "
             "jakarta.persistence projekthez a generálási configban jpa_namespace=jakarta adható meg.\n\n"
             "A generált kódot a céges projektben le kell fordítani és az eredeti működéssel tesztelni.\n")
    safe_file(output, "JAVA_ASSISTANT.md").write_text(guide, encoding="utf-8")
    return index


@dataclass
class Options:
    url: str = "http://localhost:11434"
    model: str = "qwen3-coder:30b"
    # A laptop CPU: a 16k context and a 4k answer budget keep the memory and the run time in check; a
    # trigger that needs more says so (budget_message) instead of the laptop swapping.
    num_ctx: int = 16384
    num_predict: int = 4096
    timeout: int = 900
    attempts: int = 2
    think: str = "default"
    data_access: str = "jpa"
    instructions: str = ""
    project_context: str = ""
    cached_only: bool = False

    def validate(self):
        self.url = ollama.base_url(self.url)
        if not re.fullmatch(r"[A-Za-z0-9_./:@-]{1,120}", self.model):
            raise MigrationError("Hibás Ollama modellnév.")
        for key, lo, hi in (("num_ctx", 4096, 65536), ("num_predict", 512, 16384),
                            ("timeout", 1, 3600), ("attempts", 1, 3)):
            if type(getattr(self, key)) is not int or not lo <= getattr(self, key) <= hi:
                raise MigrationError(f"{key}: {lo}..{hi} közötti egész szükséges.")
        if self.think not in {"default", "disabled", "low", "medium", "high"}:
            raise MigrationError("Hibás gondolkodási mód.")
        if self.data_access not in {"jpa", "inline", "jdbc"}:
            raise MigrationError("data_access: jpa, inline vagy jdbc szükséges.")
        if len(self.instructions) > 8000 or len(self.project_context) > 24000:
            raise MigrationError("Túl hosszú feladat vagy projektminta.")


def data_models(packet: dict) -> tuple[dict[str, str], list[str]]:
    """Generate read-only JDBC helpers from verified dictionary columns, no JPA."""
    from .schema_workbench import is_oracle_dual
    files, notes, used = {}, [], set()
    tables = packet.get("schema", {}).get("tables", {})
    candidates = list(packet["blocks"]) + [{"database": True, "table": ref["table"]}
                                          for ref in packet.get("table_requirements", []) if ref["status"] == "ready"]
    for block in candidates:
        if not block.get("database"): continue
        table = str(block.get("table") or "").upper()
        if table in used or is_oracle_dual(table): continue
        used.add(table)
        spec = tables.get(table)
        # Never resolve an unqualified name to one of several schema owners.
        if not spec or not DB_NAME.fullmatch(table) or not spec.get("columns"):
            notes.append(table + ": entity/repository kihagyva; hiányzik a pontos tables-metaadat.")
            continue
        columns = spec["columns"]
        keys = spec.get("primary_key", [])
        if (not keys or not all(k in columns for k in keys)
                or not all(DB_NAME.fullmatch(c) and "." not in c for c in columns)):
            notes.append(table + ": entity/repository kihagyva; hiányzó kulcs vagy hibás oszlopnév.")
            continue
        java_types = {"text": "String", "number": "java.math.BigDecimal", "datetime": "java.time.LocalDateTime"}
        # LOB/timezone/RAW values need an explicit mapping; broad normalized
        # categories from the dictionary are not sufficient for those types.
        db_types = {"CHAR", "VARCHAR", "VARCHAR2", "NCHAR", "NVARCHAR2", "NUMBER", "DECIMAL", "NUMERIC", "INTEGER", "FLOAT", "DATE", "TIMESTAMP"}
        if any(not isinstance(v, dict) or v.get("type") not in java_types or str(v.get("data_type", "")).upper() not in db_types
               for v in columns.values()):
            notes.append(table + ": entity/repository kihagyva; nem támogatott vagy nem igazolt Oracle-típus.")
            continue
        fields = {c: name(c) for c in columns}
        if len(set(fields.values())) != len(fields) or any(f in RESERVED for f in fields.values()):
            notes.append(table + ": entity/repository kihagyva; ütköző Java mezőnév.")
            continue
        cls = name(packet["module"], "pascal") + name(table, "pascal")
        entity, repository = cls + "Entity", cls + "Repository"
        if any(p.endswith('/' + entity + '.java') for p in files):
            raise MigrationError("Ütköző entity-osztálynév: " + entity)
        package_match = re.search(r"^package\s+([\w.]+);", packet.get("service_package_source", ""), re.M)
        package = "package " + package_match[1] + ";\n\n" if package_match else ""
        declarations = "\n".join("    public " + java_types[v["type"]] + " " + fields[c] + ";" for c, v in columns.items())
        files["backend/DPS/" + entity + ".java"] = package + f"/** JDBC adatmodell: {table}. Nem JPA entity; nincs automatikus mentés. */\npublic class {entity} {{\n{declarations}\n}}\n"
        params = "\n".join('        parameters.addValue("' + fields[k] + '", key.' + fields[k] + ');' for k in keys)
        nonnull = "\n".join('        java.util.Objects.requireNonNull(key.' + fields[k] + ', "' + k + '");' for k in keys)
        reads = []
        for c, v in columns.items():
            if v["type"] == "datetime":
                reads += [f'            java.sql.Timestamp {fields[c]}Value = rs.getTimestamp("{c}");',
                          f'            row.{fields[c]} = {fields[c]}Value == null ? null : {fields[c]}Value.toLocalDateTime();']
            else:
                reader = "getBigDecimal" if v["type"] == "number" else "getString"
                reads.append(f'            row.{fields[c]} = rs.{reader}("{c}");')
        sql = "SELECT " + ", ".join(columns) + " FROM " + table + " WHERE " + " AND ".join(k + " = :" + fields[k] for k in keys)
        files["backend/DPS/" + repository + ".java"] = package + f'''/** Igazolt kulcs szerinti JDBC-lekérdezés; jogosultság/tenant-szűrés a service feladata. */
public final class {repository} {{
    private final org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate jdbc;

    public {repository}(org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate jdbc) {{
        this.jdbc = java.util.Objects.requireNonNull(jdbc);
    }}

    public java.util.List<{entity}> findByKey({entity} key) {{
        java.util.Objects.requireNonNull(key);
{nonnull}
        var parameters = new org.springframework.jdbc.core.namedparam.MapSqlParameterSource();
{params}
        return jdbc.query({json.dumps(sql)}, parameters, (rs, rowNumber) -> {{
            var row = new {entity}();
{chr(10).join(reads)}
            return row;
        }});
    }}
}}
'''
    return files, notes


def validate_response(value: object) -> dict:
    """The normalized answer a proposal stores (see assistant_reply); re-checked before apply."""
    keys = set(RESPONSE_SCHEMA["required"])
    if not isinstance(value, dict) or not keys <= set(value) or set(value) - keys - {"repositories", "dtos"} or value["status"] not in {"proposal", "needs_context"}:
        raise MigrationError("Az AI-válasz nem követi a Java-javaslat sémáját.")
    for key, limit in (("summary", 4000), ("body", 60000), ("private_methods", 40000)):
        if not isinstance(value[key], str) or len(value[key]) > limit:
            raise MigrationError("Hibás vagy túl hosszú AI-mező: " + key)
    for key in ("imports", "assumptions", "missing_context", "test_cases"):
        if not isinstance(value[key], list) or len(value[key]) > 100 or not all(isinstance(s, str) and len(s) <= 3000 for s in value[key]):
            raise MigrationError("Hibás AI-lista: " + key)
    from .jpa_assistant import validate_extras
    validate_extras(value)
    if value["status"] == "needs_context":
        # A draft waiting for the missing source: shown and downloadable, never applied.
        if not value["missing_context"]:
            raise MigrationError("Hiányzó kontextus esetén meg kell nevezni, mi hiányzik.")
        return value
    if not value["body"].strip() or value["missing_context"]:
        raise MigrationError("A teljes javaslathoz kódtörzs kell, megnevezett hiányzó függőség nélkül.")
    if re.fullmatch(r"\s*return\s+null\s*;\s*", java_mask(value["body"])):
        raise MigrationError("Az üres null-visszatérés nem teljes végpont-implementáció.")
    for imp in value["imports"]:
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_$][\w$]*)+", imp):
            raise MigrationError("Csak explicit Java-osztályimport engedélyezett.")
    code = value["body"] + "\n" + value["private_methods"]
    mask = java_mask(code)
    # Structural checks are deliberately NOT described as compilation/security proof.
    if "\\u" in code or "```" in code or re.search(r"\b(NOT_IMPLEMENTED|UnsupportedOperationException)\b", mask):
        raise MigrationError("A javaslat helyőrzőt vagy nem támogatott kódformát tartalmaz.")
    if re.search(r"\b(package|import|class|interface|enum|record)\s+", mask):
        raise MigrationError("A modell csak metódustörzset és privát segédmetódusokat adhat.")
    if re.search(r"\b(Runtime|ProcessBuilder|System\s*\.\s*(?:exit|setProperty)|ScriptEngineManager|ClassLoader|DriverManager)\b|java\s*\.\s*(?:net|io|nio|lang\s*\.\s*reflect)\b", mask + "\n" + "\n".join(value["imports"])):
        raise MigrationError("A javaslat nem megengedett rendszer-/hálózati API-t használ.")
    for text in (value["body"], value["private_methods"]):
        depth = 0
        for char in java_mask(text):
            if char == "{": depth += 1
            elif char == "}": depth -= 1
            if depth < 0: raise MigrationError("A javaslat kilép a Java blokkból.")
        if depth: raise MigrationError("Lezáratlan blokk a Java-javaslatban.")
    from .java_scan import strip_annotations
    helpers = strip_annotations(java_mask(value["private_methods"])).strip()
    while helpers:
        match = re.match(r"private\s+(?:(?:static|final|synchronized)\s+)*(?:<[^;{}()]*>\s*)?[\w.$<>?, \[\]]+\s+\w+\s*\([^{};]*\)\s*(?:throws\s+[\w., ]+)?\s*\{", helpers)
        if not match: raise MigrationError("Csak privát segédmetódus adható meg, mező és inicializáló nem.")
        helpers = helpers[matching_brace(helpers, match.end() - 1) + 1:].strip()
    return value


def common_tool_imports(output: Path) -> dict[str, str]:
    """Simple name -> import of the CommonMigrateTools helpers (SqlValues, DbCalls ...) the module has."""
    path = safe_file(output, "backend/CL/CommonMigrateTools.java")
    if not path.is_file(): return {}
    text = path.read_text(encoding="utf-8")
    package = re.search(r"^\s*package\s+([\w.]+)\s*;", text, re.M)
    if not package: return {}
    return {name: package.group(1) + ".CommonMigrateTools." + name
            for name in re.findall(r"^    public static final class (\w+)", text, re.M)}


def render_candidate(service: str, packet: dict, response: dict) -> str:
    """Own the wrapper ourselves: model code is inside the original logging lambda."""
    start, opening, end = method_span(service, packet["method"])
    if digest(service[start:end + 1]) != packet["method_sha256"]:
        raise MigrationError("A célmetódus megváltozott; friss javaslat szükséges.")
    existing = service[opening + 1:end]
    mask = java_mask(existing)
    match = re.search(r"return\s+log1x\s*\([^;{}]*?\(\s*\)\s*->\s*\{", mask)
    if packet.get("kind") == "hook":
        # A private trigger hook (backend_trigger_mode=ai): the region stays on top, the model's code replaces the rest.
        original = ORIGINAL_REGION.search(existing)
        region = original.group(0).rstrip() + "\n" if original else ""
        body = "\n".join("        " + line if line else "" for line in response["body"].strip().splitlines())
        result = service[:opening + 1] + "\n" + region + body + "\n    }" + service[end + 1:]
        return finish_candidate(result, service, packet, response)
    if not match:
        raise MigrationError("A célmetódus log1x szerkezete nem támogatott; nincs automatikus csere.")
    lambda_open = match.end() - 1
    lambda_end = matching_brace(mask, lambda_open)
    # The commented original Forms source stays in the method: the model need not repeat it.
    original = ORIGINAL_REGION.search(existing, lambda_open + 1, lambda_end)
    region = original.group(0).rstrip() + "\n" if original else ""
    body = "\n".join("            " + line if line else "" for line in response["body"].strip().splitlines())
    # The gate is generator-owned, never delegated to the model.
    guard = ('            if (!MODULE_REVIEWED) {\n'
             '                throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, "A modul ellenőrzése szükséges.");\n'
             '            }\n') if packet.get("module_gate", {}).get("required") else ""
    new_method = service[start:opening + 1] + existing[:lambda_open + 1] + "\n" + region + guard + body + "\n        " + existing[lambda_end:] + "}"
    # Put @Transactional on this public service method if it had no transaction.
    before = service[:start]
    line_start = before.rfind("\n") + 1
    # start points after indentation; replace at the beginning of its line.
    annotation_region = before[max(0, before.rfind("}") + 1):]
    if "@Transactional" not in annotation_region and "@org.springframework.transaction.annotation.Transactional" not in annotation_region:
        before = before[:line_start] + "    @org.springframework.transaction.annotation.Transactional(rollbackFor = Exception.class)\n" + before[line_start:]
    result = before + new_method + service[end + 1:]
    return finish_candidate(result, service, packet, response)


def finish_candidate(result: str, service: str, packet: dict, response: dict) -> str:
    """The model's private helpers, its imports and the known JDK/Spring imports of the rewritten service."""
    from .java_scan import method_names, scan_members
    helper_names = [m.name for m in scan_members(response["private_methods"], java_mask(response["private_methods"]))
                    if m.kind == "method"]
    if len(set(helper_names)) != len(helper_names) or set(helper_names) & method_names(service):
        raise MigrationError("A javaslat segédmetódusneve ütközik; használj egyedi neveket.")
    imports = list(response["imports"])
    for imp in imports:
        if "import " + imp + ";" in result: continue
        package = re.search(r"^package\s+[\w.]+;\s*\n", result, re.M)
        pos = package.end() if package else 0
        result = result[:pos] + "import " + imp + ";\n" + result[pos:]
    if response["private_methods"].strip():
        last = java_mask(result).rfind("}")
        helpers = "\n".join("    " + line if line else "" for line in response["private_methods"].strip().splitlines())
        result = result[:last] + "\n    // Java asszisztens: " + packet["id"] + "\n" + helpers + "\n" + result[last:]
    mapping = dict(java_tidy_known())
    mapping.update(packet.get("formatting_imports") or {})
    # SqlValues.concat / nvl (the prompt's Oracle semantics), DbCalls of the database routine methods ...
    mapping.update(packet.get("common_tool_imports") or {})
    if packet.get("plsql_values_import"):
        mapping["PlsqlValues"] = packet["plsql_values_import"]
    return ensure_imports(result, mapping)


# JDK types model-written code often uses without writing the import (in addition to the generator's list).
ASSISTANT_IMPORTS = {name.rsplit(".", 1)[1]: name for name in (
    "java.util.HashMap", "java.util.HashSet", "java.util.Set", "java.util.LinkedHashSet", "java.util.LinkedList",
    "java.util.TreeMap", "java.util.Collection", "java.util.Collections", "java.util.Iterator", "java.util.Optional",
    "java.util.StringJoiner", "java.util.stream.Stream", "java.util.stream.IntStream", "java.util.function.Function",
    "java.util.function.Consumer", "java.util.function.BiFunction", "java.util.function.Predicate",
    "java.math.RoundingMode", "java.math.BigInteger", "java.time.LocalTime", "java.time.Duration", "java.time.Instant",
    "java.time.ZoneId", "java.time.temporal.ChronoUnit", "java.time.format.DateTimeFormatter")}


def java_tidy_known() -> dict:
    from .java_tidy import KNOWN
    return {**KNOWN, **ASSISTANT_IMPORTS}


def ensure_imports(source: str, mapping: dict) -> str:
    """`import x.y.Name;` for every simple name the code uses without an import (names of the mapping only)."""
    from .java_tidy import unimported_names
    package = re.search(r"^\s*package\s+([\w.]+)\s*;", source, re.M)
    own = package.group(1) if package else ""
    missing = sorted({mapping[name] for name in unimported_names(source) if isinstance(mapping.get(name), str)
                      and FQN_PATTERN.fullmatch(mapping[name]) and mapping[name].rsplit(".", 1)[0] != own})
    for fqn in missing:
        if re.search(r"^import\s+" + re.escape(fqn) + r"\s*;", source, re.M): continue
        at = package.end() + 1 if package else 0
        source = source[:at] + "import " + fqn + ";\n" + source[at:]
        package = re.search(r"^\s*package\s+([\w.]+)\s*;", source, re.M)
    return source


FQN_PATTERN = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_$][\w$]*)+")


def java_parse(source: str, filename: str) -> str:
    """Use javac's parser when a JDK is installed, without resolving or running code."""
    java = shutil.which("java")
    if not java: return "not_available"
    checker = '''import java.util.Arrays;
import javax.tools.Diagnostic;
import javax.tools.DiagnosticCollector;
import javax.tools.JavaFileObject;
import javax.tools.ToolProvider;
import com.sun.source.util.JavacTask;
class SyntaxCheck {
    public static void main(String[] args) throws Exception {
        var compiler = ToolProvider.getSystemJavaCompiler();
        if (compiler == null) { System.exit(42); }
        var diagnostics = new DiagnosticCollector<JavaFileObject>();
        try (var files = compiler.getStandardFileManager(diagnostics, null, java.nio.charset.StandardCharsets.UTF_8)) {
            var task = (JavacTask) compiler.getTask(null, files, diagnostics,
                Arrays.asList("-proc:none", "--release", "11"), null, files.getJavaFileObjects(args));
            task.parse();
            boolean failed = false;
            for (var diagnostic : diagnostics.getDiagnostics()) {
                if (diagnostic.getKind() == Diagnostic.Kind.ERROR) {
                    failed = true;
                    System.out.println("line " + diagnostic.getLineNumber() + ": " + diagnostic.getMessage(java.util.Locale.ROOT));
                }
            }
            if (failed) { System.exit(43); }
        }
    }
}
'''
    with tempfile.TemporaryDirectory(prefix="frm-java-parse-") as temp:
        root = Path(temp)
        (root / "SyntaxCheck.java").write_text(checker, encoding="utf-8")
        target = root / filename
        target.write_text(source, encoding="utf-8")
        try:
            run = subprocess.run([java, str(root / "SyntaxCheck.java"), str(target)],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return "not_available"
    if run.returncode == 43:
        lines = source.split("\n")
        details = []
        for found in re.finditer(r"line (\d+): ([^\n]*)", run.stdout):
            number = int(found.group(1))
            text = lines[number - 1].strip() if 0 < number <= len(lines) else ""
            details.append(f"{filename} {number}. sor: {found.group(2)}" + (f" — `{text[:160]}`" if text else ""))
        raise MigrationError("Java 11 szintaktikai hiba: " + ("; ".join(details[:5]) if details else run.stdout[:2500]))
    return "passed" if run.returncode == 0 else "not_available"


def format_candidates(packet: dict, files: dict) -> tuple[dict, dict]:
    """Formatting failure never retries inference; keep the syntax-checked raw draft."""
    from .assistant_format import format_java
    result, reports = {}, {}
    for target, original in sorted(files.items()):
        text, report = format_java(original, target, packet.get("config", {}), packet.get("formatting_imports", {}))
        if text != original:
            try:
                java_parse(text, Path(target).name)
            except MigrationError as exc:
                text, report = original, {"status": "manual", "reason": str(exc)[:500]}
        result[target], reports[target] = text, report
    return result, reports


def estimate_tokens(*texts: str) -> int:
    """Deliberately conservative (about 3 bytes per token); Ollama reports the real count afterwards."""
    return (sum(len(text.encode("utf-8")) for text in texts) + 2) // 3


def budget_message(required: int, num_ctx: int) -> str:
    recommended = ((required + 4095) // 4096) * 4096
    next_step = (f"A Memória és futtatás résznél a Kontextus (num_ctx) értéke legalább {recommended} legyen."
                 if recommended <= 65536 else "Ehhez a forráshoz a jelenlegi 65536 tokennél nagyobb kontextust támogató futtatás szükséges.")
    return (f"A teljes kontextus és a kimeneti keret becsült igénye {required} token, "
            f"a beállított num_ctx {num_ctx}. {next_step} A trigger és a szükséges források teljesek maradtak.")


def prepare(output: Path, task_id: str, options: Options) -> dict:
    """Everything one request needs, built locally without network access."""
    options.validate()
    if not ID.fullmatch(task_id): raise MigrationError("Hibás asszisztensfeladat-azonosító.")
    output = output.resolve()
    # Re-index actual preserved/edited code immediately before each request.
    index = prepare_workspace(output)
    if not any(t["id"] == task_id for t in index["tasks"]):
        raise MigrationError("A végpont már nem szerepel az asszisztens feladatai között.")
    packet = read_json(safe_file(output, AREA + "/tasks/" + task_id + ".json"))
    if packet.get("pending_tables"):
        raise MigrationError("Előbb add meg a hiányzó táblaszerkezetet az Adatbázistáblák panelen: " + ", ".join(packet["pending_tables"]))
    service = safe_file(output, packet["target"]).read_text(encoding="utf-8")
    packet["service_package_source"] = service[:service.find("public class") if "public class" in service else 1000]
    packet["common_tool_imports"] = common_tool_imports(output)
    packet["plsql_values_import"] = packet["common_tool_imports"].get("PlsqlValues")
    from . import assistant_context, jpa_assistant
    persistence = jpa_assistant.plan(packet, output) if options.data_access == "jpa" else []
    helpers, notes = data_models(packet) if options.data_access == "jdbc" else ({}, [])
    prompt = assistant_context.build(packet, persistence, helpers, options,
                                     plsql_values=bool(packet["plsql_values_import"]))
    # What the former JSON request carried (whole task packet): kept as a size comparison.
    legacy = {"task": packet, "generated_jdbc_helpers": helpers, "helper_notes": notes,
              "additional_requirements": options.instructions, "project_context": options.project_context}
    input_tokens = estimate_tokens(prompt["system"], prompt["user"])
    return {"output": output, "packet": packet, "service": service, "persistence": persistence, "helpers": helpers,
            "notes": notes, "prompt": prompt, "input_tokens": input_tokens,
            "required_tokens": input_tokens + options.num_predict + 1000,
            "original_bytes": len(json.dumps(legacy, ensure_ascii=False).encode("utf-8"))}


def preview(output: Path, task_id: str, options: Options) -> dict:
    """The exact request the next run would send, without calling the model."""
    prepared = prepare(output, task_id, options)
    prompt = prepared["prompt"]
    warnings = []
    if prepared["required_tokens"] > options.num_ctx:
        warnings.append(budget_message(prepared["required_tokens"], options.num_ctx))
    return {"task_id": task_id, "method": prepared["packet"]["method"], "system": prompt["system"], "user": prompt["user"],
            "bytes": prompt["bytes"], "estimated_input_tokens": prepared["input_tokens"],
            "required_tokens": prepared["required_tokens"], "num_ctx": options.num_ctx, "num_predict": options.num_predict,
            "repositories": prompt["repositories"], "entities": prompt["entities"],
            "omitted_tables": prompt["omitted_tables"], "original_bytes": prepared["original_bytes"], "warnings": warnings}


class Cancelled(MigrationError):
    """The developer stopped the run (Megszakítás): the model's connection is closed, Ollama stops generating."""


class Live:
    """What the running request is doing right now, for the UI (elapsed time, phase, tokens, speed, the last text).

    Ollama streams one JSON line per token; the counts below are those lines (≈ tokens). The final line carries the
    exact prompt/answer token counts and durations. on_change gets a snapshot at most twice a second.
    """

    def __init__(self, on_change=None, attempt: int = 1):
        import time
        self.clock = time.monotonic
        self.started = self.clock()
        self.on_change = on_change
        self.attempt = attempt
        self.first_token = None
        self.phase = "connecting"
        self.thinking_tokens = self.content_tokens = 0
        self.content, self.thinking = [], []
        self.last_emit = 0.0
        self.final = {}

    def snapshot(self) -> dict:
        now = self.clock()
        generated = self.thinking_tokens + self.content_tokens
        writing = (now - self.first_token) if self.first_token else 0
        text = "".join(self.content)
        thought = "".join(self.thinking)
        return {"attempt": self.attempt, "phase": self.phase, "elapsed": round(now - self.started, 1),
                "prompt_seconds": round((self.first_token or now) - self.started, 1),
                "first_token_seconds": round(self.first_token - self.started, 1) if self.first_token else None,
                "thinking_tokens": self.thinking_tokens, "content_tokens": self.content_tokens,
                "tokens_per_second": round(generated / writing, 2) if writing > 0.5 and generated else None,
                "prompt_tokens": self.final.get("prompt_eval_count"), "output_tokens": self.final.get("eval_count"),
                "tail": text[-1600:], "thinking_tail": thought[-800:]}

    def emit(self, force: bool = False) -> None:
        if self.on_change is None:
            return
        now = self.clock()
        if force or now - self.last_emit >= 0.5:
            self.last_emit = now
            try:
                self.on_change(self.snapshot())
            except Exception:  # the UI's state is advisory; the request goes on
                pass

    def chunk(self, value: dict) -> None:
        message = value.get("message") if isinstance(value.get("message"), dict) else {}
        thought, text = message.get("thinking"), message.get("content")
        if (thought or text) and self.first_token is None:
            self.first_token = self.clock()
        if isinstance(thought, str) and thought:
            self.thinking.append(thought); self.thinking_tokens += 1; self.phase = "thinking"
        if isinstance(text, str) and text:
            self.content.append(text); self.content_tokens += 1; self.phase = "writing"
        if value.get("done") is True:
            self.final = value
            self.phase = "done"
        self.emit(force=value.get("done") is True)


def call_model(options: Options, body: dict, progress=None, cancel=None, attempt: int = 1) -> dict:
    """One streamed /api/chat call; network and server errors are never retried here.

    progress(snapshot) shows the live state (Live), cancel() -> True stops the run. The result is the same
    envelope as a non-streamed answer: the last line with the whole message (content and thinking) in it.
    A server that answers with one JSON document (no stream) is read the same way.
    """
    import socket
    import threading
    import time
    url = options.url.rstrip("/") + "/api/chat"
    req = urllib.request.Request(url, data=json.dumps({**body, "stream": True}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    live = Live(progress, attempt)
    live.emit(force=True)
    deadline = time.monotonic() + options.timeout
    stop = threading.Event()
    holder = {}

    def watchdog():
        # The socket waits silently while the CPU reads the prompt: a cancel or the time limit closes it.
        while not stop.wait(0.5):
            if (cancel and cancel()) or time.monotonic() > deadline:
                holder["reason"] = "cancel" if cancel and cancel() else "timeout"
                stream = holder.get("stream")
                sock = getattr(getattr(getattr(stream, "fp", None), "raw", None), "_sock", None)
                try:
                    if sock is not None: sock.shutdown(socket.SHUT_RDWR)
                    elif stream is not None: stream.close()
                except OSError:
                    pass
                return
            live.emit()

    guard = threading.Thread(target=watchdog, daemon=True, name="frm-ollama-watchdog")
    guard.start()
    raw_size, loose = 0, []
    try:
        live.phase = "connecting"
        with ollama.opener().open(req, timeout=options.timeout) as stream:
            holder["stream"] = stream
            live.phase = "prompt"
            live.emit(force=True)
            while True:
                line = stream.readline()
                if not line:
                    break
                raw_size += len(line)
                if raw_size > MAX_RESPONSE_BYTES:
                    raise MigrationError("Túl nagy Ollama-válasz.")
                text = line.strip()
                if not text:
                    continue
                try:
                    value = json.loads(text)
                except ValueError:
                    loose.append(line)  # a pretty-printed (non-streamed) JSON document: parsed at the end
                    continue
                if not isinstance(value, dict):
                    raise MigrationError("Az Ollama válasza nem JSON-objektum.")
                if isinstance(value.get("error"), str) and value["error"].strip():
                    raise MigrationError("Az Ollama hibát adott: " + value["error"][:2000])
                live.chunk(value)
    except urllib.error.HTTPError as exc:
        raise MigrationError(ollama.http_error(exc, "POST", req.full_url)) from exc
    except MigrationError as exc:
        exc.partial = "".join(live.content)
        raise
    except (OSError, ValueError) as exc:
        if holder.get("reason") == "cancel":
            failure = Cancelled("A futást leállítottad; az Ollama abbahagyta a választ.")
        elif holder.get("reason") == "timeout":
            failure = MigrationError(f"Az Ollama-kérés túllépte az időkorlátot ({options.timeout} mp). "
                                     "Növeld az Időkorlátot a Memória és futtatás résznél, vagy csökkentsd a Kimeneti keretet.")
        else:
            failure = MigrationError("Az Ollama-kérés nem sikerült: " + (str(exc) or type(exc).__name__))
        failure.partial = "".join(live.content)  # what the model wrote so far: kept in the AI log
        raise failure from exc
    finally:
        stop.set()
    if holder.get("reason") == "cancel":
        failure = Cancelled("A futást leállítottad; az Ollama abbahagyta a választ.")
        failure.partial = "".join(live.content)
        raise failure
    if loose and not live.final:
        try:
            outer = json.loads(b"".join(loose))
        except ValueError as exc:
            raise MigrationError("Az Ollama válasza nem JSON: " + b"".join(loose)[:300].decode("utf-8", "replace")) from exc
        if not isinstance(outer, dict):
            raise MigrationError("Az Ollama válasza nem JSON-objektum.")
        if isinstance(outer.get("error"), str) and outer["error"].strip():
            raise MigrationError("Az Ollama hibát adott: " + outer["error"][:2000])
        live.chunk({**outer, "done": True} if "done" not in outer else outer)
        return outer
    if not live.final and not live.content and not live.thinking:
        if holder.get("reason") == "timeout":
            raise MigrationError(f"Az Ollama-kérés túllépte az időkorlátot ({options.timeout} mp).")
        raise MigrationError("Az Ollama üres választ adott.")
    outer = dict(live.final) if live.final else {"done": False, "done_reason": "length" if holder.get("reason") == "timeout" else None}
    message = dict(outer.get("message") or {})
    message["role"] = message.get("role") or "assistant"
    message["content"] = "".join(live.content)
    if live.thinking:
        message["thinking"] = "".join(live.thinking)
    outer["message"] = message
    outer["live"] = {k: v for k, v in live.snapshot().items() if k not in {"tail", "thinking_tail"}}
    return outer


def reply_context(prepared: dict, options: Options):
    from . import assistant_reply
    packet, service = prepared["packet"], prepared["service"]
    used = [p for p in prepared["persistence"] if p["repository"] in prepared["prompt"]["repositories"]]
    allowed = set(re.findall(r"^\s*import\s+([\w.$]+)\s*;", service, re.M))
    allowed |= set(re.findall(r"\b(?:[a-z_][\w]*\.)+[A-Z][\w$]*", options.project_context))
    return assistant_reply.Context(
        method=packet["method"], service=service, module_class=name(packet["module"], "pascal"),
        repositories=[assistant_reply.Repository(p["repository"], p["variable"], p["entity"], p["existing_repository"])
                      for p in used],
        namespace=(packet.get("config") or {}).get("jpa_namespace") or "javax", mode=options.data_access,
        allowed_imports=frozenset(allowed))


def propose(output: Path, task_id: str, options: Options, cache: Path | None = None,
            exchange_id: str | None = None, progress=None, cancel=None) -> dict:
    """Ask the local model once (plus bounded corrections) and store a reviewable proposal.

    Every request and raw answer is written to the AI log first (assistant_log), also when the
    answer is rejected, so the developer can always see what was sent and what came back.
    """
    from . import assistant_log, assistant_reply, jpa_assistant
    prepared = prepare(output, task_id, options)
    output, packet, service = prepared["output"], prepared["packet"], prepared["service"]
    persistence, helpers, notes, prompt = prepared["persistence"], prepared["helpers"], prepared["notes"], prepared["prompt"]
    request = {"model": options.model, "stream": False, "keep_alive": "5m",
               "messages": [{"role": "system", "content": prompt["system"]}, {"role": "user", "content": prompt["user"]}],
               "options": {"temperature": 0, "seed": 0, "num_ctx": options.num_ctx, "num_predict": options.num_predict}}
    if options.think != "default": request["think"] = False if options.think == "disabled" else options.think
    log = assistant_log.Exchange(
        output, exchange_id or assistant_log.new_id(), task_id=task_id, method=packet["method"], target=packet["target"],
        model=options.model, url=options.url.rstrip("/") + "/api/chat",
        options={"num_ctx": options.num_ctx, "num_predict": options.num_predict, "timeout": options.timeout,
                 "attempts": options.attempts, "think": options.think, "data_access": options.data_access,
                 "temperature": 0},
        prompt={"system": prompt["system"], "user": prompt["user"], "bytes": prompt["bytes"],
                "estimated_input_tokens": prepared["input_tokens"], "repositories": prompt["repositories"],
                "entities": prompt["entities"], "omitted_tables": prompt["omitted_tables"],
                "original_bytes": prepared["original_bytes"]})
    try:
        result = _propose(prepared, options, cache, request, log, assistant_reply, jpa_assistant, progress, cancel)
    except BaseException as exc:
        message = str(exc).strip() or type(exc).__name__
        if isinstance(exc, Cancelled):
            log.finish("cancelled", error=message)
            raise
        log.finish("failed", error=message)
        if isinstance(exc, MigrationError) and log.data["attempts"]:
            raise MigrationError(message + f" A teljes AI-kérés és -válasz az AI-naplóban látható (azonosító: {log.data['id']}).") from exc
        raise
    log.finish("completed", result={"proposal_id": result["id"], "status": result["status"]})
    return result


def _propose(prepared, options, cache, request, log, assistant_reply, jpa_assistant, progress=None, cancel=None) -> dict:
    output, packet, service = prepared["output"], prepared["packet"], prepared["service"]
    persistence, helpers, notes, prompt = prepared["persistence"], prepared["helpers"], prepared["notes"], prepared["prompt"]
    task_id = packet["id"]
    if prepared["required_tokens"] > options.num_ctx:
        raise MigrationError(budget_message(prepared["required_tokens"], options.num_ctx))
    ctx = reply_context(prepared, options)

    def render_files(response):
        if options.data_access == "jpa":
            return jpa_assistant.render_files(output, packet, response, service, persistence)
        if response.get("repositories") or response.get("dtos"):
            raise MigrationError("Repository/DTO-javaslathoz JPA mód szükséges.")
        return {packet["target"]: render_candidate(service, packet, response), **helpers}

    def parse_files(files):
        statuses = [java_parse(source, Path(path).name) for path, source in files.items()]
        return "passed" if statuses and all(s == "passed" for s in statuses) else "not_available"

    key = digest(json.dumps({"version": VERSION, "url": options.url.rstrip("/"), "request": request}, sort_keys=True, ensure_ascii=False))
    cache = cache or output.parent / ".frm-assistant-cache"
    cache_file = safe_file(cache, key + ".json")
    response, errors, cached, extraction, usage = None, [], False, None, {}
    if cache_file.is_file():
        try:
            entry = read_json(cache_file)
            if entry.get("key") == key:
                response = validate_response(entry["response"])
                extraction = entry.get("extraction")
                cached = True
                log.cached(entry)
        except (MigrationError, KeyError, TypeError, ValueError): pass
    if response is None and options.cached_only:
        raise MigrationError("Ehhez a pontos forráshoz/beállításhoz nincs érvényes cache; hálózati hívás nem történt.")
    calls, parse_status, raw = 0, "not_run", ""
    verified_procedures = {name.upper() for name in packet.get("schema", {}).get("procedures", {})}
    unresolved = [call for call in packet.get("external_calls", []) if call.upper() not in verified_procedures]
    messages, correction = list(request["messages"]), None
    while response is None and calls < options.attempts:
        calls += 1
        attempt = log.begin(calls, correction)
        try:
            outer = call_model(options, {**request, "messages": messages}, progress=progress, cancel=cancel, attempt=calls)
        except MigrationError as exc:
            log.end(attempt, raw=getattr(exc, "partial", None) or None, error=str(exc))
            raise
        message = outer.get("message") if isinstance(outer.get("message"), dict) else {}
        raw = message.get("content") if isinstance(message.get("content"), str) else ""
        thinking = message.get("thinking") if isinstance(message.get("thinking"), str) else ""
        truncated = outer.get("done") is not True or outer.get("done_reason") == "length"
        reply = assistant_reply.extract(raw, ctx, truncated=truncated)
        if not reply.problems and reply.response["status"] == "proposal":
            if options.data_access != "jpa" and unresolved and not options.project_context.strip():
                reply.problem("A hívott üzleti rutinokhoz nincs teljes forrás vagy igazolt szignatúra: " + ", ".join(unresolved) + ".",
                              "These called routines have no source: " + ", ".join(unresolved) + ". List them under MISSING:.")
            else:
                try:
                    validate_response(reply.response)
                    parse_status = parse_files(render_files(reply.response))
                except MigrationError as exc:
                    details = str(exc)
                    reply.problem(details, ("The generated Java does not parse: " + details.split(":", 1)[1].strip())
                                  if "szintaktikai" in details else "The migrator could not insert your code: " + details)
        elif not reply.problems:
            try:
                validate_response(reply.response)
            except MigrationError as exc:
                reply.problem(str(exc), "The answer is incomplete: " + str(exc))
        log.end(attempt, raw=raw, thinking=thinking or reply.thinking, outer=outer, extraction=reply.report())
        usage = {"prompt_tokens": outer.get("prompt_eval_count"), "output_tokens": outer.get("eval_count"),
                 "seconds": attempt.get("seconds"),
                 "tokens_per_second": (outer.get("live") or {}).get("tokens_per_second"),
                 "first_token_seconds": (outer.get("live") or {}).get("first_token_seconds")}
        if not reply.problems:
            response, extraction = reply.response, reply.report()
            break
        errors.append("; ".join(p.hu for p in reply.problems)[:2000])
        if reply.truncated:
            errors.append(f"A válasz a kimeneti keretnél (num_predict={options.num_predict}) megszakadt; "
                          "növeld a Kimeneti keretet a Memória és futtatás résznél.")
            break
        correction = assistant_reply.correction(reply, ctx)
        messages = request["messages"][:2] + [{"role": "assistant", "content": raw}, {"role": "user", "content": correction}]
        needed = estimate_tokens(*(m["content"] for m in messages)) + options.num_predict + 1000
        if calls < options.attempts and needed > options.num_ctx:
            errors.append("A javítási kérés (előző válasz + hibalista) nem fér a beállított kontextusba; nem küldtük el.")
            break
    if response is None:
        raise MigrationError(f"Nem érkezett használható Java-javaslat ({calls} AI-hívás): " + " | ".join(errors))
    if not cached:
        write_json(cache_file, {"key": key, "response": response, "raw": raw, "extraction": extraction})
    proposal_id = key[:20]
    folder = safe_file(output, AREA + "/proposals/" + proposal_id)
    files, draft_note = {}, None
    if response["status"] == "proposal":
        files = render_files(response)
        if cached: parse_status = parse_files(files)
    elif response["body"].strip():
        # needs_context: show what the model could write, never apply it.
        try:
            files = render_files(response)
        except MigrationError as exc:
            draft_note = "A hiányos vázlatból nem készült fájl: " + str(exc)
    files, formatting = format_candidates(packet, files)
    manifest = []
    for relative, text in sorted(files.items()):
        target = safe_file(output, relative)
        if options.data_access != "jpa" and relative != packet["target"] and target.exists():
            raise MigrationError("A JDBC segéd már létezik; nem írjuk felül: " + relative)
        draft_rel = "files/" + relative + ".txt"
        draft = safe_file(folder, draft_rel)
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text(text, encoding="utf-8", newline="\n")
        old = target.read_text(encoding="utf-8") if target.is_file() else ""
        patch = "".join(difflib.unified_diff(old.splitlines(True), text.splitlines(True), fromfile=relative, tofile=relative))
        diff_path = safe_file(folder, draft_rel + ".patch")
        diff_path.write_text(patch, encoding="utf-8")
        manifest.append({"target": relative, "candidate": draft_rel, "sha256": digest(text),
                         "expected_sha256": digest(old) if target.is_file() else None})
    extraction = dict(extraction or {})
    if draft_note:
        extraction["warnings"] = list(extraction.get("warnings", [])) + [draft_note]
    result = {"version": VERSION, "id": proposal_id, "task_id": task_id, "method": packet["method"],
              "status": response["status"], "model": options.model, "cached": cached, "calls": calls,
              "data_access": options.data_access, "exchange_id": log.data["id"],
              "context": {"original_bytes": prepared["original_bytes"], "sent_bytes": prompt["bytes"],
                          "estimated_input_tokens": prepared["input_tokens"], "method_count": 1,
                          "entity_count": len(prompt["entities"]), **{k: v for k, v in usage.items() if v is not None}},
              "source_fingerprint": packet["fingerprint"], "response": response, "files": manifest,
              "apply_allowed": packet["apply_allowed"], "extraction": extraction,
              "helper_notes": notes, "format_errors": errors, "applied": False,
              "validation": {"schema": "passed", "structural_checks": "passed", "java_parse": parse_status,
                             "java_compile": "not_run", "oracle_equivalence": "not_run",
                             "deterministic_format": formatting}}
    write_json(folder / "proposal.json", result)
    lines = ["# Java-javaslat: " + packet["method"], "", response["summary"], "",
             "Állapot: " + response["status"] + ". A Java-fordítás és a működési tesztek még nem futottak.",
             "AI-napló: " + log.data["id"] + " (a küldött prompt és az AI nyers válasza).", ""]
    for label, values in (("Hiányzó kontextus", response["missing_context"]), ("Feltételezések", response["assumptions"]),
                           ("Feldolgozási figyelmeztetések", extraction.get("warnings", [])),
                           ("Ellenőrizendő tesztesetek", response["test_cases"]), ("JDBC adatmodellek", notes),
                           ("Determinisztikus formázás", [path + ": " + info["status"] + (" — " + info["reason"] if info.get("reason") else "")
                                                        for path, info in formatting.items()])):
        if values:
            lines += ["## " + label, "", *["- " + v for v in values], ""]
    lines += ["A files mappában teljes Java-fájljavaslatok (.java.txt) és diffek vannak.",
              "Átnézés után: `python -m frm_forms backend-apply MODULMAPPA --proposal " + proposal_id + " --reviewed`.", ""]
    (folder / "REVIEW.md").write_text("\n".join(lines), encoding="utf-8")
    return result


def proposal_archive(output: Path, proposal_id: str, archive: Path) -> None:
    if not ID.fullmatch(proposal_id): raise MigrationError("Hibás javaslatazonosító.")
    folder = safe_file(output, AREA + "/proposals/" + proposal_id)
    if not (folder / "proposal.json").is_file(): raise MigrationError("Nincs ilyen Java-javaslat.")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for file in sorted(folder.rglob("*")):
            if file.is_symlink(): raise MigrationError("Symlink a Java-javaslatban.")
            if file.is_file(): z.write(file, file.relative_to(folder).as_posix())


def apply_proposal(output: Path, proposal_id: str, reviewed: bool = False) -> dict:
    """Explicit reviewed application; stage/validate all bytes before writing any."""
    if not reviewed: raise MigrationError("Átnézés után --reviewed szükséges; az AI-kód nem kerül automatikusan a backendbe.")
    if not ID.fullmatch(proposal_id): raise MigrationError("Hibás javaslatazonosító.")
    output = output.resolve()
    folder = safe_file(output, AREA + "/proposals/" + proposal_id)
    record = read_json(safe_file(folder, "proposal.json"))
    if record["status"] != "proposal" or record.get("applied"):
        raise MigrationError("A javaslat nem alkalmazható vagy már átvették.")
    if not record.get("apply_allowed"):
        raise MigrationError("CRUD-végpontnál kézi összefésülés szükséges a meglévő validációs, írási és jogosultsági feltételekkel. A teljes fájlt és diffet a javaslat tartalmazza.")
    # Whole-module input contracts must still match, not just the edited method.
    index = prepare_workspace(output)
    current = next((t for t in index["tasks"] if t["id"] == record["task_id"]), None)
    if not current or current["fingerprint"] != record["source_fingerprint"]:
        raise MigrationError("A forrás, a séma vagy a Java-szerződés megváltozott; új javaslat szükséges.")
    packet = read_json(safe_file(output, AREA + "/tasks/" + record["task_id"] + ".json"))
    if not packet.get("apply_allowed"):
        raise MigrationError("Ehhez a végponthoz kézi összefésülés szükséges.")
    response = validate_response(record["response"])
    source = safe_file(output, packet["target"]).read_text(encoding="utf-8")
    packet["service_package_source"] = source[:source.find("public class") if "public class" in source else 1000]
    packet["common_tool_imports"] = common_tool_imports(output)
    packet["plsql_values_import"] = packet["common_tool_imports"].get("PlsqlValues")
    # Reconstruct rather than trusting paths or editable files from a model ZIP.
    expected_files = {packet["target"]: render_candidate(source, packet, response)}
    if record.get("data_access") == "jpa":
        from . import jpa_assistant
        expected_files = jpa_assistant.render_files(output, packet, response, source, jpa_assistant.plan(packet, output))
    elif any(f["target"] != packet["target"] for f in record["files"]):
        expected_files.update(data_models(packet)[0])
    expected_files, _ = format_candidates(packet, expected_files)
    if set(expected_files) != {f["target"] for f in record["files"]}:
        raise MigrationError("A javaslat fájllistája megváltozott.")
    staged = []
    for entry in record["files"]:
        target = safe_file(output, entry["target"])
        draft = safe_file(folder, entry["candidate"])
        text = draft.read_text(encoding="utf-8")
        old = target.read_bytes() if target.is_file() else None
        if (digest(text) != entry["sha256"] or text != expected_files[entry["target"]]
                or (digest(old) if old is not None else None) != entry["expected_sha256"]):
            raise MigrationError("A cél vagy a javaslat módosult; nem írjuk felül: " + entry["target"])
        staged.append((target, old, text.encode("utf-8")))
    backups = folder / "originals"
    for target, old, _ in staged:
        if old is not None:
            backup = safe_file(backups, target.relative_to(output).as_posix() + ".txt")
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(old)
    applied = []
    try:
        for target, old, new in staged:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(new)
            try: temporary.replace(target)
            finally: temporary.unlink(missing_ok=True)
            applied.append((target, old))
    except OSError:
        for target, old in reversed(applied):
            if old is None: target.unlink(missing_ok=True)
            else: target.write_bytes(old)
        raise
    record["applied"] = True
    write_json(folder / "proposal.json", record)
    plan_path = safe_file(output, "analysis/backend-plan.json")
    plan = read_json(plan_path)
    for endpoint in plan["endpoints"]:
        if endpoint["method"] == record["method"]:
            endpoint["assistant"] = {"proposal": proposal_id, "applied": True, "functional_verification": "required"}
    if packet.get("kind") == "hook":
        # A private trigger hook is no endpoint of the plan: the applied ones are listed beside them.
        plan.setdefault("ai_hooks_applied", []).append({"method": record["method"], "proposal": proposal_id,
                                                         "functional_verification": "required"})
    write_json(plan_path, plan)
    from .ui_report import update_manifest
    update_manifest(output)
    prepare_workspace(output)  # the task list without the applied method (index.json is read by the UI)
    return {"applied": True, "proposal": proposal_id, "files": [f["target"] for f in record["files"]],
            "notice": "A fájlok átvéve. Fordítsd és teszteld a céges projektben; a korábbi tiltási metaadatok megmaradtak."}


def refresh_archives(output: Path) -> None:
    """Refresh manifest and any existing CLI/web downloads after assistant writes."""
    from .ui_report import update_manifest
    from .cli import package_bundle
    update_manifest(output)
    full = output.with_name(output.name + ".zip")
    if full.is_file():
        for path in output.rglob("*"):
            if path.is_symlink(): raise MigrationError("Symlink a generált modulban.")
        with tempfile.TemporaryDirectory(prefix=".frm-assistant-zip-", dir=output.parent) as temp:
            staged = Path(temp) / "module.zip"
            package_bundle(output, staged, output.name)
            staged.replace(full)
    # Web workers put the layer downloads next to their module directory.
    if output.name == "module":
        for layer in ("frontend", "backend"):
            archive = output.parent / (layer + ".zip")
            if not archive.is_file(): continue
            with tempfile.TemporaryDirectory(prefix=".frm-assistant-zip-", dir=output.parent) as temp:
                staged = Path(temp) / archive.name
                with zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as z:
                    for file in sorted((output / layer).rglob("*")):
                        if file.is_symlink(): raise MigrationError("Symlink a generált modulban.")
                        if file.is_file(): z.write(file, file.relative_to(output).as_posix())
                staged.replace(archive)

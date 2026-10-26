"""4.35 backend_trigger_mode=ai: the generator writes the method frames only, the Java assistant the bodies.

Covers the generated frames (endpoints, CRUD hooks, query buttons) with their commented Forms code, the
assistant tasks of the hooks and their apply, the streamed Ollama call (live progress, cancel, time limit)
and the automatic, one-at-a-time AI queue of the web backend (auto-run after migration, auto-apply).
"""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from frm_forms import backend_assistant as engine
from frm_forms.cli import main
from frm_forms.common import MigrationError, read_json
from java_support import COMPANY_IMPORTS, write_stubs
from screen_support import ANGULAR_CLI_OPTIONS, with_company_imports
from test_backend_assistant import Client

ROOT = Path(__file__).resolve().parents[1]

SERVICE = "backend/DPS/OrdersServiceImpl.java"
HOOKS = ("triggerPostQueryOrders", "triggerPreInsertOrders", "triggerWhenValidateItemCodeOrders")
ENDPOINTS = ("onCtrlCountBtn", "onCtrlSearchBtn")


def form() -> str:
    """A database block with record triggers, a control block with a SQL button and a query button."""
    root = ET.Element("FormModule", Name="ORDERS_FORM", Title="Rendelések")
    ET.SubElement(root, "Canvas", Name="MAIN", CanvasType="Content", Width="400", Height="200")
    ET.SubElement(root, "Window", Name="WINDOW1")
    orders = ET.SubElement(root, "Block", Name="ORDERS", DatabaseDataBlock="true", QueryDataSourceType="Table",
                           QueryDataSourceName="APP_ORDERS", QueryAllowed="true", InsertAllowed="true",
                           UpdateAllowed="true", DeleteAllowed="true")
    ET.SubElement(orders, "Item", Name="ID", DataType="Number", ItemType="Text Item", PrimaryKey="true",
                  ColumnName="ID", CanvasName="MAIN", MaximumLength="10")
    code = ET.SubElement(orders, "Item", Name="CODE", DataType="Char", ItemType="Text Item", ColumnName="CODE",
                         CanvasName="MAIN", MaximumLength="20", Required="true")
    ET.SubElement(orders, "Item", Name="NAME", DataType="Char", ItemType="Text Item", ColumnName="NAME",
                  CanvasName="MAIN", MaximumLength="80")
    ET.SubElement(orders, "Item", Name="PARTNER_NAME", DataType="Char", ItemType="Text Item", DatabaseItem="false",
                  CanvasName="MAIN", MaximumLength="80")
    ET.SubElement(orders, "Trigger", Name="POST-QUERY", TriggerText=(
        "BEGIN\n  SELECT p.name INTO :ORDERS.PARTNER_NAME FROM app_partners p WHERE p.code = :ORDERS.CODE;\n"
        "EXCEPTION WHEN NO_DATA_FOUND THEN :ORDERS.PARTNER_NAME := NULL;\nEND;"))
    ET.SubElement(orders, "Trigger", Name="PRE-INSERT", TriggerText=(
        "BEGIN\n  SELECT app_orders_seq.NEXTVAL INTO :ORDERS.ID FROM dual;\nEND;"))
    ET.SubElement(code, "Trigger", Name="WHEN-VALIDATE-ITEM", TriggerText=(
        "BEGIN\n  IF length(:ORDERS.CODE) < 3 THEN\n    message('Túl rövid kód');\n"
        "    RAISE form_trigger_failure;\n  END IF;\nEND;"))
    ctrl = ET.SubElement(root, "Block", Name="CTRL", DatabaseDataBlock="false")
    ET.SubElement(ctrl, "Item", Name="FILTER", DataType="Char", ItemType="Text Item", CanvasName="MAIN", MaximumLength="20")
    ET.SubElement(ctrl, "Item", Name="TOTAL", DataType="Number", ItemType="Text Item", CanvasName="MAIN", MaximumLength="10")
    count = ET.SubElement(ctrl, "Item", Name="COUNT_BTN", ItemType="Push Button", CanvasName="MAIN", Label="Számol")
    ET.SubElement(count, "Trigger", Name="WHEN-BUTTON-PRESSED", TriggerText=(
        "BEGIN\n  SELECT count(*) INTO :CTRL.TOTAL FROM app_orders WHERE code LIKE :CTRL.FILTER || '%';\n"
        "  message('Összesen: ' || :CTRL.TOTAL);\nEND;"))
    search = ET.SubElement(ctrl, "Item", Name="SEARCH_BTN", ItemType="Push Button", CanvasName="MAIN", Label="Keres")
    ET.SubElement(search, "Trigger", Name="WHEN-BUTTON-PRESSED", TriggerText=(
        "BEGIN\n  set_block_property('ORDERS', DEFAULT_WHERE, 'code like ''' || :CTRL.FILTER || '%''');\n"
        "  go_block('ORDERS');\n  execute_query;\nEND;"))
    return ET.tostring(root, encoding="unicode")


def migrate(root: Path, output: Path, mode: str = "ai") -> Path:
    source, config = root / "form.xml", root / ("config-" + mode + ".json")
    source.write_text(form(), encoding="utf-8")
    config.write_text(json.dumps({"java_company_imports": COMPANY_IMPORTS, "backend_trigger_mode": mode}), encoding="utf-8")
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        code = main(["migrate", str(source), "--screen", "--module", "orders", "--config", str(config), "--out", str(output)])
    assert code == 0, err.getvalue()
    return output


def method_text(service: str, method: str) -> str:
    start, _, end = engine.method_span(service, method)
    return service[start:end + 1]


def code_lines(text: str) -> str:
    """The Java lines of a method without its comments."""
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("//"))


VALIDATE_ANSWER = '''```java
// SERVICE
private void triggerWhenValidateItemCodeOrders(OrdersRow row, RuleContext context) {
    if (row.code != null && row.code.length() < 3) {
        context.message("Túl rövid kód");
        context.abort();
    }
}
```
'''


JPA_STUBS = {
    "Entity": 'public @interface Entity { String name() default ""; }',
    "Table": 'public @interface Table { String name(); String schema() default ""; }',
    "Column": "public @interface Column { String name(); boolean nullable() default true; int length() default 255; "
              "int precision() default 0; int scale() default 0; }",
    "Id": "public @interface Id {}", "IdClass": "public @interface IdClass { Class<?> value(); }",
    "Lob": "public @interface Lob {}", "PersistenceContext": "public @interface PersistenceContext {}",
    "Query": "public interface Query { Object getSingleResult(); java.util.List getResultList(); "
             "Query setParameter(String name, Object value); }",
    "EntityManager": "public interface EntityManager { <T> T find(Class<T> type, Object id); Query createNativeQuery(String sql); "
                     "void persist(Object entity); <T> T merge(T entity); void remove(Object entity); }"}


def compile_module(test: unittest.TestCase, output: Path, work: Path) -> None:
    sources = list((output / "backend").rglob("*.java")) + write_stubs(work / "stubs")
    for name, body in JPA_STUBS.items():  # the JPA entity/repository of an applied proposal
        path = work / "stubs/javax/persistence" / (name + ".java")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("package javax.persistence;\n" + body, encoding="utf-8")
        sources.append(path)
    argsfile = work / "sources.args"
    argsfile.write_text("\n".join('"' + str(p).replace("\\", "/") + '"' for p in sources))
    build = subprocess.run(["java", "com.sun.tools.javac.Main", "--release", "11", "-encoding", "UTF-8",
                            "-d", str(work / "classes"), "@" + str(argsfile)], capture_output=True, text=True, timeout=120)
    test.assertEqual(build.returncode, 0, build.stdout + build.stderr)


class AiFramesTests(unittest.TestCase):
    """One AI-mode and one legacy migration of the same form, shared by the read-only checks."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.output = migrate(cls.root, cls.root / "ai")
        cls.service = (cls.output / SERVICE).read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_every_server_trigger_is_a_frame_with_its_commented_original_code(self):
        for method, line in (("onCtrlCountBtn", "SELECT count(*) INTO :CTRL.TOTAL FROM app_orders"),
                             ("onCtrlSearchBtn", "set_block_property('ORDERS', DEFAULT_WHERE"),
                             ("triggerPostQueryOrders", "SELECT p.name INTO :ORDERS.PARTNER_NAME"),
                             ("triggerPreInsertOrders", "SELECT app_orders_seq.NEXTVAL INTO :ORDERS.ID FROM dual;"),
                             ("triggerWhenValidateItemCodeOrders", "IF length(:ORDERS.CODE) < 3 THEN")):
            with self.subTest(method=method):
                text = method_text(self.service, method)
                self.assertIn("//region Eredeti Forms-kód: ", text)
                self.assertIn("//endregion", text)
                self.assertRegex(text, r"//\s+" + re.escape(line))
                self.assertIn("// TODO(AI):", text)
                self.assertIn("HttpStatus.NOT_IMPLEMENTED", text)
                # Nothing of the trigger is translated: the frame only throws until the assistant writes it.
                body = code_lines(text)
                self.assertNotIn("app_", body.lower())
                self.assertNotIn("DbCalls", body)
        self.assertNotIn("DbCalls.call(", self.service)

    def test_frames_keep_the_final_contracts(self):
        self.assertIn("public ActionResult onCtrlCountBtn(UserDto user, ActionRequest request)", self.service)
        # The query button keeps the query contract: the screen shows the returned rows of ORDERS.
        self.assertIn("public PageResult<OrdersRow> onCtrlSearchBtn(UserDto user, QueryActionRequest request)", self.service)
        for hook in HOOKS:
            self.assertIn("private void " + hook + "(OrdersRow row, RuleContext context)", self.service)
        # The CRUD flow calls the hooks at the Forms points (validation, before insert, after fetch).
        self.assertIn("triggerWhenValidateItemCodeOrders(row, context);", method_text(self.service, "validateRulesOrders"))
        self.assertIn("triggerPreInsertOrders(row, context);", method_text(self.service, "preInsertOrders"))
        self.assertIn("triggerPostQueryOrders(row, context);", method_text(self.service, "postQueryOrders"))
        self.assertIn("preInsertOrders(row, context);", method_text(self.service, "createOrders"))
        self.assertIn("postQueryOrders(row, context);", method_text(self.service, "listOrders"))

    def test_plan_report_and_screen_follow_the_ai_frames(self):
        plan = read_json(self.output / "analysis/backend-plan.json")
        endpoints = {e["method"]: e for e in plan["endpoints"]}
        for method in ENDPOINTS:
            self.assertEqual(endpoints[method]["runs"], "ai")
            self.assertTrue(endpoints[method]["blockers"][0].startswith("AI-ra vár"))
        self.assertEqual(endpoints["onCtrlSearchBtn"]["query_block"], "ORDERS")
        self.assertEqual(plan["skipped_actions"], [])
        report = (self.output / "migration-report.md").read_text(encoding="utf-8")
        self.assertRegex(report, r"AI-ra váró triggerek \(Java asszisztens\) \| 5 \|")
        screen = (self.output / "frontend/orders/orders.component.ts").read_text(encoding="utf-8")
        # A plain action: its answer reaches the screen (written items, messages, GO_BLOCK/EXECUTE_QUERY).
        self.assertIn(".subscribe(res => this.applyActionResult(res));", screen)
        self.assertIn("protected applyActionResult(res: any", screen)
        self.assertIn("protected migrationQuery(block: string)", screen)
        # The query button sends its filter and shows the rows of its target block.
        click = screen[screen.index("protected onSearchBtnClick()"):]
        click = click[:click.index("\n  }\n")]
        self.assertIn("this.actionOnctrlsearchbtn(", click)
        self.assertIn("FILTER:", click)
        self.assertIn("limit: 1,", click)  # the form shows one record; hasMore says when more matched
        self.assertIn("this.migrationLoaded['ORDERS'] = row;\n      this.forms['orders']?.reset(row ?? {});", click)

    def test_screen_carries_out_the_commands_of_an_answer(self):
        screen = (self.output / "frontend/orders/orders.component.ts").read_text(encoding="utf-8")
        handler = screen[screen.index("protected applyActionResult("):]
        handler = handler[:handler.index("\n  }\n")]
        self.assertIn("operation === 'SET_ITEM_PROPERTY' && args[1] === 'ENABLED'", handler)
        self.assertIn("control?.disable();", handler)
        self.assertIn("} else if (operation === 'COMMIT_FORM') {\n        this.save();", handler)
        self.assertIn("answer(window.confirm(", handler)

    def test_screen_typescript_strict(self):
        tsc = os.environ.get("FRM_TSC") or shutil.which("tsc")
        if not tsc:
            self.skipTest("tsc szükséges (FRM_TSC=<tsc útvonala>)")
        work = Path(tempfile.mkdtemp(dir=self.root))
        shutil.copytree(self.output / "frontend", work / "frontend")
        shutil.copy(ROOT / "tests/ts_stubs/company.ts", work / "company.ts")
        files = with_company_imports(work / "frontend")
        (work / "tsconfig.json").write_text(json.dumps({"compilerOptions": {
            **ANGULAR_CLI_OPTIONS, "noUnusedLocals": True, "useDefineForClassFields": True, "noEmit": True},
            "files": [str(ROOT / "tests/ts_stubs/host.d.ts")] + files}))
        run = subprocess.run([tsc, "-p", str(work / "tsconfig.json")], capture_output=True, text=True, timeout=180)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_assistant_tasks_ask_only_for_the_tables_the_code_uses(self):
        index = engine.prepare_workspace(self.output)
        tasks = {t["method"]: t for t in index["tasks"]}
        self.assertEqual(set(tasks), set(HOOKS) | set(ENDPOINTS))
        self.assertEqual(tasks["triggerWhenValidateItemCodeOrders"]["pending_tables"], [])
        self.assertEqual(tasks["triggerPostQueryOrders"]["pending_tables"], ["APP_PARTNERS"])
        self.assertEqual(tasks["triggerPreInsertOrders"]["pending_tables"], ["APP_ORDERS"])  # the key's sequence
        self.assertEqual(tasks["onCtrlSearchBtn"]["pending_tables"], ["APP_ORDERS"])
        self.assertEqual({t["kind"] for m, t in tasks.items() if m in HOOKS}, {"hook"})
        self.assertEqual(tasks["onCtrlCountBtn"]["event"], "WHEN-BUTTON-PRESSED")
        self.assertEqual(tasks["triggerPreInsertOrders"]["event"], "PRE-INSERT")
        packet = read_json(self.output / engine.AREA / "tasks" / (tasks["triggerWhenValidateItemCodeOrders"]["id"] + ".json"))
        self.assertTrue(packet["apply_allowed"])
        fields = {f["item"]: f for f in packet["hook"]["fields"]}
        self.assertEqual((fields["ORDERS.CODE"]["field"], fields["ORDERS.CODE"]["column"]), ("code", "CODE"))
        self.assertTrue(fields["ORDERS.ID"]["primary_key"])
        self.assertFalse(fields["ORDERS.PARTNER_NAME"]["database"])

    def test_legacy_modes_still_translate_on_request(self):
        output = migrate(self.root, self.root / "legacy", mode="plsql")
        service = (output / SERVICE).read_text(encoding="utf-8")
        self.assertIn("DbCalls.call(", method_text(service, "onCtrlCountBtn"))
        self.assertNotIn("//region Eredeti Forms-kód: ", service)
        self.assertNotIn("triggerWhenValidateItemCodeOrders", service)

    def test_generated_ai_module_compiles_as_java_11(self):
        if not shutil.which("java"):
            self.skipTest("Java 11+ fordító szükséges")
        work = Path(tempfile.mkdtemp(dir=self.root))
        compile_module(self, self.output, work)


class HookAssistantTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = migrate(self.root, self.root / "out")
        self.tasks = {t["method"]: t for t in engine.prepare_workspace(self.output)["tasks"]}
        self.options = engine.Options(num_ctx=16384)

    def test_hook_prompt_answer_and_apply_keep_the_original_code(self):
        task = self.tasks["triggerWhenValidateItemCodeOrders"]
        client = Client(VALIDATE_ANSWER)
        with patch("frm_forms.ollama.opener", return_value=client):
            result = engine.propose(self.output, task["id"], self.options)
        self.assertEqual(result["status"], "proposal", result)
        self.assertTrue(client.requests[0]["stream"])
        user = client.requests[0]["messages"][1]["content"]
        self.assertIn("triggerWhenValidateItemCodeOrders", user)
        self.assertIn("IF length(:ORDERS.CODE) < 3 THEN", user)
        self.assertIn("RuleContext", user)
        engine.apply_proposal(self.output, result["id"], reviewed=True)
        service = (self.output / SERVICE).read_text(encoding="utf-8")
        hook = method_text(service, "triggerWhenValidateItemCodeOrders")
        self.assertIn("//region Eredeti Forms-kód: ORDERS.CODE / WHEN-VALIDATE-ITEM", hook)
        self.assertIn('context.message("Túl rövid kód");', hook)
        self.assertNotIn("NOT_IMPLEMENTED", hook)
        # The other hooks still wait; the done one leaves the task list.
        self.assertIn("NOT_IMPLEMENTED", method_text(service, "triggerPostQueryOrders"))
        remaining = {t["method"] for t in read_json(self.output / engine.AREA / "index.json")["tasks"]}
        self.assertNotIn("triggerWhenValidateItemCodeOrders", remaining)
        self.assertIn("triggerPostQueryOrders", remaining)
        if shutil.which("java"):
            compile_module(self, self.output, Path(tempfile.mkdtemp(dir=self.root)))

    def test_hook_hints_come_from_the_trigger_code_only(self):
        # The region notes of every hook say "context.message(...)": that is no MESSAGE of the Forms code.
        from frm_forms import schema_workbench as schema
        text = {"data_type": "VARCHAR2", "length": 80, "length_semantics": "CHAR"}
        for table, key, columns in (("APP_ORDERS", "ID", ("CODE", "NAME")), ("APP_PARTNERS", "CODE", ("NAME",))):
            first = {"name": "ID", "data_type": "NUMBER"} if key == "ID" else {"name": key, **text}
            rows = [{**first, "nullable": False}] + [{"name": c, **text, "nullable": True} for c in columns]
            schema.save_table(self.output, table, rows, [key], schema.schema_sheet(self.output)["revision"])
        self.tasks = {t["method"]: t for t in engine.prepare_workspace(self.output)["tasks"]}
        hint = "- MESSAGE(x): context.message(x)."
        validate = engine.preview(self.output, self.tasks["triggerWhenValidateItemCodeOrders"]["id"], self.options)["user"]
        self.assertIn(hint, validate)
        for method in ("triggerPostQueryOrders", "triggerPreInsertOrders"):
            self.assertNotIn(hint, engine.preview(self.output, self.tasks[method]["id"], self.options)["user"], method)

    def test_sequence_key_hook_gets_its_own_table_repository_and_compiles(self):
        from frm_forms import schema_workbench as schema
        sheet = schema.schema_sheet(self.output)
        schema.save_table(self.output, "APP_ORDERS", [
            {"name": "ID", "data_type": "NUMBER", "nullable": False, "precision": 10, "scale": 0},
            {"name": "CODE", "data_type": "VARCHAR2", "nullable": False, "length": 20, "length_semantics": "CHAR"},
            {"name": "NAME", "data_type": "VARCHAR2", "nullable": True, "length": 80, "length_semantics": "CHAR"}], ["ID"], sheet["revision"])
        task = next(t for t in engine.prepare_workspace(self.output)["tasks"] if t["method"] == "triggerPreInsertOrders")
        self.assertEqual(task["pending_tables"], [])
        answer = """```java
// SERVICE
private void triggerPreInsertOrders(OrdersRow row, RuleContext context) {
    row.id = ordersAppOrdersRepository.nextOrderId();
}
```
```java
// REPOSITORY OrdersAppOrdersRepository
public java.math.BigDecimal nextOrderId() {
    return (java.math.BigDecimal) entityManager.createNativeQuery("SELECT APP_ORDERS_SEQ.NEXTVAL FROM DUAL").getSingleResult();
}
```
"""
        client = Client(answer)
        with patch("frm_forms.ollama.opener", return_value=client):
            result = engine.propose(self.output, task["id"], self.options)
        user = client.requests[0]["messages"][1]["content"]
        # The sequence needs no table name in the code: the hook's own table hosts the query.
        self.assertIn("Repositories:\n- ordersAppOrdersRepository (OrdersAppOrdersRepository)", user)
        self.assertNotIn("No database table is available", user)
        self.assertEqual(result["status"], "proposal", result)
        engine.apply_proposal(self.output, result["id"], reviewed=True)
        service = (self.output / SERVICE).read_text(encoding="utf-8")
        self.assertIn("row.id = ordersAppOrdersRepository.nextOrderId();", method_text(service, "triggerPreInsertOrders"))
        self.assertIn("nextOrderId()", (self.output / "backend/DPS/OrdersAppOrdersRepository.java").read_text(encoding="utf-8"))
        if shutil.which("java"):
            compile_module(self, self.output, Path(tempfile.mkdtemp(dir=self.root)))

    def test_answer_without_the_hook_is_rejected_and_nothing_changes(self):
        task = self.tasks["triggerWhenValidateItemCodeOrders"]
        before = (self.output / SERVICE).read_bytes()
        with patch("frm_forms.ollama.opener", return_value=Client("Nem tudom.", "Most sem.")):
            with self.assertRaises(MigrationError):
                engine.propose(self.output, task["id"], engine.Options(num_ctx=16384, attempts=2))
        self.assertEqual((self.output / SERVICE).read_bytes(), before)


class Stream:
    """A streamed HTTP answer: NDJSON lines, optionally waiting (silently, like a CPU reading the prompt)."""

    def __init__(self, lines=(), hold: float = 0.0, block: bool = False):
        self.lines = [json.dumps(line).encode() + b"\n" for line in lines]
        self.hold, self.block = hold, block
        self.closed = threading.Event()

    def __enter__(self): return self
    def __exit__(self, *exc): self.close()
    def close(self): self.closed.set()

    def readline(self):
        if self.block:
            self.closed.wait(10)
            if self.closed.is_set():
                raise OSError("connection closed")
        if self.hold:
            time.sleep(self.hold)
        return self.lines.pop(0) if self.lines else b""


class Opener:
    def __init__(self, stream): self.stream, self.requests = stream, []
    def open(self, request, timeout):
        self.requests.append(json.loads(request.data))
        return self.stream


class StreamingTests(unittest.TestCase):
    def options(self, **changes):
        options = engine.Options(url="http://localhost:11434", model="qwen-test", num_ctx=16384)
        for key, value in changes.items(): setattr(options, key, value)
        return options

    def test_tokens_arrive_with_live_progress_and_make_one_answer(self):
        lines = [{"message": {"role": "assistant", "content": "", "thinking": "gondolkodom"}, "done": False},
                 {"message": {"role": "assistant", "content": "return "}, "done": False},
                 {"message": {"role": "assistant", "content": "null;"}, "done": False},
                 {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
                  "prompt_eval_count": 120, "eval_count": 3, "eval_duration": 1_500_000_000}]
        snapshots = []
        opener = Opener(Stream(lines, hold=0.05))
        with patch("frm_forms.ollama.opener", return_value=opener):
            outer = engine.call_model(self.options(), {"model": "qwen-test", "messages": []}, progress=snapshots.append)
        self.assertTrue(opener.requests[0]["stream"])
        self.assertEqual(outer["message"]["content"], "return null;")
        self.assertEqual(outer["message"]["thinking"], "gondolkodom")
        self.assertEqual(outer["prompt_eval_count"], 120)
        self.assertIsNotNone(outer["live"]["first_token_seconds"])
        phases = [s["phase"] for s in snapshots]
        self.assertEqual(phases[0], "connecting")
        self.assertEqual(phases[-1], "done")
        last = snapshots[-1]
        self.assertEqual((last["content_tokens"], last["thinking_tokens"], last["output_tokens"]), (2, 1, 3))
        self.assertIn("return null;", last["tail"])

    def test_cancel_stops_a_request_that_still_reads_the_prompt(self):
        stream = Stream(block=True)
        flag = threading.Event()
        threading.Timer(0.3, flag.set).start()
        started = time.monotonic()
        with patch("frm_forms.ollama.opener", return_value=Opener(stream)):
            with self.assertRaises(engine.Cancelled):
                engine.call_model(self.options(), {"model": "qwen-test", "messages": []}, cancel=flag.is_set)
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(stream.closed.is_set())

    def test_time_limit_closes_a_silent_request(self):
        stream = Stream(block=True)
        with patch("frm_forms.ollama.opener", return_value=Opener(stream)):
            with self.assertRaisesRegex(MigrationError, "időkorlát"):
                engine.call_model(self.options(timeout=1), {"model": "qwen-test", "messages": []})
        self.assertTrue(stream.closed.is_set())

    def test_partial_answer_survives_a_broken_connection(self):
        class Broken(Stream):
            def readline(self):
                if self.lines: return self.lines.pop(0)
                raise OSError("reset by peer")
        stream = Broken([{"message": {"content": "public "}, "done": False}])
        with patch("frm_forms.ollama.opener", return_value=Opener(stream)):
            with self.assertRaises(MigrationError) as raised:
                engine.call_model(self.options(), {"model": "qwen-test", "messages": []})
        self.assertEqual(raised.exception.partial, "public ")

    def test_server_error_line_is_reported(self):
        with patch("frm_forms.ollama.opener", return_value=Opener(Stream([{"error": "model qwen-test not found"}]))):
            with self.assertRaisesRegex(MigrationError, "model qwen-test not found"):
                engine.call_model(self.options(), {"model": "qwen-test", "messages": []})


class FakeJobs:
    """The parts of JobManager the assistant queue uses."""

    def __init__(self, root: Path):
        self.root = root
        self.settings = SimpleNamespace(data_dir=root / "data")
        self.on_completed = None

    def get(self, job_id):
        from frm_forms.web.jobs import JobError
        if job_id != "job1":
            raise JobError("Nincs ilyen feladat.", 404)
        return {"id": job_id, "status": "completed"}


class QueueTests(unittest.TestCase):
    def setUp(self):
        try:
            from frm_forms.web.assistant import AssistantManager
        except ImportError as exc:  # the web extras (pydantic) are optional for the engine tests
            self.skipTest("webes függőség hiányzik: " + str(exc))
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "data").mkdir()
        migrate(self.root, self.root / "job1" / "module")
        self.jobs = FakeJobs(self.root)
        self.manager = AssistantManager(self.jobs)
        self.addCleanup(self.manager.close)
        self.manager.save_settings({"url": "http://localhost:11434", "model": "qwen-test"})

    def idle(self):
        for _ in range(750):
            state = self.manager.queue_state("job1")
            if state["active"] is None and not state["queued"]:
                return state
            time.sleep(0.02)
        self.fail("Az AI-sor nem ürült ki.")

    def test_migration_completion_queues_ready_triggers_and_applies_the_answer(self):
        self.assertEqual(self.jobs.on_completed, self.manager.migration_completed)
        client = Client(VALIDATE_ANSWER)
        with patch("frm_forms.ollama.opener", return_value=client):
            self.jobs.on_completed("job1")
            state = self.idle()
        # Only the trigger whose tables are known (none needed) ran; the rest waits for the table structures.
        self.assertEqual(len(client.requests), 1)
        finished = state["finished"]
        self.assertEqual([(r["method"], r["status"], r["auto"], r["applied"]) for r in finished],
                         [("triggerWhenValidateItemCodeOrders", "completed", True, True)])
        self.assertEqual(finished[0]["proposal_status"], "proposal")
        service = (self.root / "job1/module" / SERVICE).read_text(encoding="utf-8")
        self.assertIn('context.message("Túl rövid kód");', method_text(service, "triggerWhenValidateItemCodeOrders"))
        index = self.manager.index("job1")
        self.assertEqual([t["method"] for t in index["done_tasks"]], ["triggerWhenValidateItemCodeOrders"])
        self.assertEqual(index["auto"]["done"], 1)
        self.assertEqual(index["auto"]["waiting_for_tables"], 4)
        # Nothing ready is left: a second automatic start queues nothing.
        with patch("frm_forms.ollama.opener", side_effect=AssertionError("no new request")):
            self.assertEqual(self.manager.auto_start("job1", reason="migration")["queued"], 0)

    def test_switched_off_automation_neither_starts_nor_applies(self):
        self.manager.save_settings({"auto_run": False, "auto_apply": False})
        with patch("frm_forms.ollama.opener", side_effect=AssertionError("no automatic request")):
            self.jobs.on_completed("job1")
            self.assertEqual(self.idle()["finished"], [])
        before = (self.root / "job1/module" / SERVICE).read_bytes()
        with patch("frm_forms.ollama.opener", return_value=Client(VALIDATE_ANSWER)):
            queued = self.manager.auto_start("job1")  # the developer's own "start all"
            self.assertEqual(queued["methods"], ["triggerWhenValidateItemCodeOrders"])
            state = self.idle()
        self.assertEqual(state["finished"][0]["status"], "completed")
        self.assertFalse(state["finished"][0]["applied"])
        self.assertEqual((self.root / "job1/module" / SERVICE).read_bytes(), before)

    def test_a_failed_automatic_run_is_not_repeated_for_the_same_input(self):
        with patch("frm_forms.ollama.opener", return_value=Client("Nem tudom.", "Most sem.")):
            self.manager.auto_start("job1", reason="migration")
            state = self.idle()
        self.assertEqual(state["finished"][0]["status"], "failed")
        self.assertIn("/ai-log?job=job1&exchange=", state["finished"][0]["error"])
        with patch("frm_forms.ollama.opener", side_effect=AssertionError("no retry")):
            self.assertEqual(self.manager.auto_start("job1", reason="schema")["skipped"], ["triggerWhenValidateItemCodeOrders"])
        with patch("frm_forms.ollama.opener", return_value=Client(VALIDATE_ANSWER)):
            self.assertEqual(self.manager.auto_start("job1", force=True)["queued"], 1)
            self.assertTrue(self.idle()["finished"][0]["applied"])

    def test_runs_go_one_at_a_time_and_can_be_cancelled(self):
        from frm_forms.web.assistant import AssistantRequest, CancelRequest
        stream = Stream(block=True)
        tasks = {t["method"]: t for t in self.manager.index("job1")["tasks"]}
        with patch("frm_forms.ollama.opener", return_value=Opener(stream)):
            first = self.manager.start("job1", AssistantRequest(task_id=tasks["triggerWhenValidateItemCodeOrders"]["id"],
                                                                url="http://localhost:11434", model="qwen-test"))
            self.assertEqual(first["status"], "running")
            for _ in range(250):
                state = self.manager.queue_state("job1")
                if state["active"] and state["active"].get("progress"):
                    break
                time.sleep(0.02)
            self.assertEqual(state["active"]["id"], first["id"])
            self.assertIn(state["active"]["progress"]["phase"], {"connecting", "prompt"})
            stopped = self.manager.stop("job1", CancelRequest(clear_queue=True))
            self.assertEqual(stopped["cancelled"], first["id"])
            state = self.idle()
        self.assertEqual(state["finished"][0]["status"], "cancelled")
        self.assertTrue(stream.closed.is_set())


if __name__ == "__main__":
    unittest.main()

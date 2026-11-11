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
from java_support import COMPANY_IMPORTS, compile_copies, write_stubs
from screen_support import ANGULAR_CLI_OPTIONS, with_company_imports
from test_backend_assistant import Client

ROOT = Path(__file__).resolve().parents[1]

SERVICE = "backend/DPS/OrdersServiceImpl.java"
HOOKS = ("triggerPostQueryOrders", "triggerPreInsertOrders", "triggerWhenValidateItemCodeOrders")
ENDPOINTS = ("onCtrlCountBtn", "onCtrlSearchBtn")
CRUD = ("listOrders", "createOrders", "updateOrders", "deleteOrders")


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


def model_path():
    """The model's way even for a trigger the migrator translates itself (hook_java): the tests of the AI path."""
    return patch("frm_forms.hook_java.response", return_value=(None, "a modell útját teszteljük"))


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


# A model answer for every task of the form, in the service style the prompt asks for: the Forms SQL as native SQL
# through the service's EntityManager, no repository, no JDBC.
ROW = """            var row = new OrdersRow();
            row.id = SqlValues.number(r[0]);
            row.code = SqlValues.text(r[1]);
            row.name = SqlValues.text(r[2]);"""
LOCK = """        List<Object[]> locked;
        try {
            locked = entityManager.createNativeQuery("SELECT ID, CODE, NAME FROM APP_ORDERS WHERE ID = :id FOR UPDATE NOWAIT")
                    .setParameter("id", original.id).getResultList();
        } catch (PersistenceException e) {
            throw FormsErrors.translate(e);
        }
        if (locked.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "A rekord nem található.");
        }
        Object[] current = locked.get(0);
        if (!SqlValues.same(SqlValues.number(current[0]), original.id) || !SqlValues.same(SqlValues.text(current[1]), original.code)
                || !SqlValues.same(SqlValues.text(current[2]), original.name)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "A rekord közben megváltozott.");
        }"""
CHECKS = """        var errors = new ArrayList<String>();
        FormsChecks.required(errors, "CODE", row.code);
        FormsChecks.maxLength(errors, "CODE", row.code, 20);
        FormsChecks.maxLength(errors, "NAME", row.name, 80);
        FormsChecks.throwIfAny(errors);"""
ANSWERS = {
    "listOrders": """```java
// SERVICE
public PageResult<OrdersRow> listOrders(UserDto user, int offset, int limit) throws Exception {
    return log1x(log, OrdersConstants.LIST_ORDERS_NAME, user, null, () -> {
        if (offset < 0 || limit < 1 || limit > 200) {
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "offset/limit");
        }
        var context = new RuleContext();
        List<Object[]> result = entityManager.createNativeQuery(
                "SELECT ID, CODE, NAME FROM APP_ORDERS ORDER BY ID OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY")
                .setParameter("offset", offset).setParameter("limit", limit + 1).getResultList();
        var rows = new ArrayList<OrdersRow>();
        for (Object[] r : result) {
            if (rows.size() == limit) {
                break;
            }
""" + ROW + """
            triggerPostQueryOrders(row, context);
            rows.add(row);
        }
        return new PageResult<>(rows, context.messages(), result.size() > limit);
    });
}
```""",
    "createOrders": """```java
// SERVICE
public RowResult<OrdersRow> createOrders(UserDto user, OrdersRow row) throws Exception {
    return log1x(log, OrdersConstants.CREATE_ORDERS_NAME, user, null, () -> {
        var context = new RuleContext();
""" + CHECKS + """
        triggerWhenValidateItemCodeOrders(row, context);
        triggerPreInsertOrders(row, context);
        try {
            entityManager.createNativeQuery("INSERT INTO APP_ORDERS (ID, CODE, NAME) VALUES (:id, :code, :name)")
                    .setParameter("id", row.id).setParameter("code", row.code).setParameter("name", row.name).executeUpdate();
        } catch (PersistenceException e) {
            throw FormsErrors.translate(e);
        }
        return new RowResult<>(row, context.messages());
    });
}
```""",
    "updateOrders": """```java
// SERVICE
public RowResult<OrdersRow> updateOrders(UserDto user, OrdersRow original, OrdersRow value) throws Exception {
    return log1x(log, OrdersConstants.UPDATE_ORDERS_NAME, user, null, () -> {
        var context = new RuleContext();
        if (!SqlValues.same(original.id, value.id)) {
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "id: nem módosítható.");
        }
""" + LOCK + """
        var row = value;
""" + CHECKS + """
        triggerWhenValidateItemCodeOrders(row, context);
        try {
            entityManager.createNativeQuery("UPDATE APP_ORDERS SET CODE = :code, NAME = :name WHERE ID = :id")
                    .setParameter("code", row.code).setParameter("name", row.name).setParameter("id", row.id).executeUpdate();
        } catch (PersistenceException e) {
            throw FormsErrors.translate(e);
        }
        return new RowResult<>(value, context.messages());
    });
}
```""",
    "deleteOrders": """```java
// SERVICE
public List<String> deleteOrders(UserDto user, OrdersRow original) throws Exception {
    return log1x(log, OrdersConstants.DELETE_ORDERS_NAME, user, null, () -> {
        var context = new RuleContext();
""" + LOCK + """
        try {
            entityManager.createNativeQuery("DELETE FROM APP_ORDERS WHERE ID = :id").setParameter("id", original.id).executeUpdate();
        } catch (PersistenceException e) {
            throw FormsErrors.translate(e);
        }
        return context.messages();
    });
}
```""",
    "onCtrlCountBtn": """```java
// SERVICE
public ActionResult onCtrlCountBtn(UserDto user, ActionRequest request) throws Exception {
    return log1x(log, OrdersConstants.ON_CTRL_COUNT_BTN_NAME, user, null, () -> {
        var values = request.blocks() == null ? Map.<String, Map<String, String>>of() : request.blocks();
        String filter = PlsqlValues.text(values, "CTRL", "FILTER");
        BigDecimal total = SqlValues.number(entityManager.createNativeQuery(
                "SELECT count(*) FROM app_orders WHERE code LIKE :filter || '%'").setParameter("filter", filter).getSingleResult());
        List<String> messages = new ArrayList<>();
        messages.add(SqlValues.concat("Összesen: ", total == null ? null : total.toPlainString()));
        Map<String, Map<String, String>> blocks = new LinkedHashMap<>();
        PlsqlValues.put(blocks, "CTRL", "TOTAL", total);
        return new ActionResult(blocks, messages, new ArrayList<>(), Map.of());
    });
}
```""",
    "onCtrlSearchBtn": """```java
// SERVICE
public PageResult<OrdersRow> onCtrlSearchBtn(UserDto user, QueryActionRequest request) throws Exception {
    return log1x(log, OrdersConstants.ON_CTRL_SEARCH_BTN_NAME, user, null, () -> {
        var values = request.blocks() == null ? Map.<String, Map<String, String>>of() : request.blocks();
        String filter = PlsqlValues.text(values, "CTRL", "FILTER");
        var context = new RuleContext();
        List<Object[]> result = entityManager.createNativeQuery("SELECT ID, CODE, NAME FROM APP_ORDERS WHERE code like :filter "
                + "|| '%' ORDER BY ID OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY").setParameter("filter", filter)
                .setParameter("offset", request.offset()).setParameter("limit", request.limit() + 1).getResultList();
        var rows = new ArrayList<OrdersRow>();
        for (Object[] r : result) {
            if (rows.size() == request.limit()) {
                break;
            }
""" + ROW + """
            triggerPostQueryOrders(row, context);
            rows.add(row);
        }
        return new PageResult<>(rows, context.messages(), result.size() > request.limit());
    });
}
```""",
    "triggerPostQueryOrders": """```java
// SERVICE
private void triggerPostQueryOrders(OrdersRow row, RuleContext context) {
    List<Object> names = entityManager.createNativeQuery("SELECT p.name FROM app_partners p WHERE p.code = :code")
            .setParameter("code", row.code).getResultList();
    row.partnerName = names.isEmpty() ? null : SqlValues.text(names.get(0));
}
```""",
    "triggerPreInsertOrders": """```java
// SERVICE
private void triggerPreInsertOrders(OrdersRow row, RuleContext context) {
    row.id = SqlValues.number(entityManager.createNativeQuery("SELECT app_orders_seq.NEXTVAL FROM dual").getSingleResult());
}
```""",
    "triggerWhenValidateItemCodeOrders": VALIDATE_ANSWER,
}


class MethodClient:
    """Ollama double that answers each task by the method it is about (the method under "Method:")."""

    def __init__(self, answers, fallback="Nem tudom."):
        self.answers, self.fallback, self.requests = answers, fallback, []

    def open(self, request, timeout):
        body = json.loads(request.data)
        self.requests.append(body)
        user = body["messages"][1]["content"]
        method = re.search(r"Method:\n```java\n[^\n(]*?(\w+)\(", user)
        content = self.answers.get(method.group(1) if method else "", self.fallback)
        return io.BytesIO(json.dumps({"done": True, "done_reason": "stop",
                                      "message": {"role": "assistant", "content": content}}).encode())


def compile_module(test: unittest.TestCase, output: Path, work: Path) -> None:
    # The frames the assistant has not written yet are empty (they do not compile until then): written here.
    sources = compile_copies(list((output / "backend").rglob("*.java")), work) + write_stubs(work / "stubs")
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
                # Nothing is started: the body under the commented Forms code is empty until the assistant writes
                # it (no TODO, no NOT_IMPLEMENTED throw, no half-translated code that would confuse the model).
                self.assertNotIn("TODO(AI)", text)
                self.assertNotIn("NOT_IMPLEMENTED", text)
                self.assertNotIn("AI-ra vár", text)
                self.assertRegex(text, r"//endregion\n\s*(?:\}\);\n\s*)?\}$")
                body = code_lines(text)
                self.assertNotIn("app_", body.lower())
                self.assertNotIn("DbCalls", body)
        self.assertIn("        return log1x(log, OrdersConstants.ON_CTRL_COUNT_BTN_NAME, user, null, () -> {\n", self.service)
        self.assertNotIn("MODULE_REVIEWED", self.service)
        self.assertNotIn("DbCalls.call(", self.service)

    def test_frames_keep_the_final_contracts(self):
        self.assertIn("public ActionResult onCtrlCountBtn(UserDto user, ActionRequest request)", self.service)
        # The query button keeps the query contract: the screen shows the returned rows of ORDERS.
        self.assertIn("public PageResult<OrdersRow> onCtrlSearchBtn(UserDto user, QueryActionRequest request)", self.service)
        for hook in HOOKS:
            self.assertIn("private void " + hook + "(OrdersRow row, RuleContext context)", self.service)
        # The data endpoints name the hooks at the Forms points (validation, before insert, after fetch); no
        # dispatcher or CRUD helper is generated around them.
        create = method_text(self.service, "createOrders")
        self.assertLess(create.index("triggerWhenValidateItemCodeOrders(row, context)"), create.index("triggerPreInsertOrders(row, context)"))
        self.assertLess(create.index("triggerPreInsertOrders(row, context)"), create.index("INSERT INTO APP_ORDERS"))
        self.assertIn("triggerPostQueryOrders(row, context)", method_text(self.service, "listOrders"))
        self.assertIn("triggerPostQueryOrders(row, context)", method_text(self.service, "onCtrlSearchBtn"))
        for helper in ("validateRulesOrders", "preInsertOrders", "postQueryOrders", "guardOrders", "loadRowOrders"):
            self.assertNotIn(" " + helper + "(", self.service)

    def test_data_endpoints_are_frames_with_the_forms_sql(self):
        # Every data endpoint is the assistant's too: an empty frame under its hints (the SQL Forms runs, the row
        # mapping, the hooks in the Forms order, the result). The service runs SQL through its EntityManager only.
        hints = {"listOrders": ["SELECT ID, CODE, NAME FROM APP_ORDERS ORDER BY ID OFFSET :offset ROWS FETCH NEXT",
                                "[0] ID -> id (BigDecimal)", "new PageResult<>(rows, context.messages(), hasMore)"],
                 "createOrders": ["kötelező: CODE", "INSERT INTO APP_ORDERS (ID, CODE, NAME) VALUES (:id, :code, :name)",
                                  "new RowResult<>(row, context.messages())"],
                 "updateOrders": ["SELECT ID, CODE, NAME FROM APP_ORDERS WHERE ID = :id FOR UPDATE NOWAIT",
                                  "UPDATE APP_ORDERS SET CODE = :code, NAME = :name WHERE ID = :id",
                                  "triggerWhenValidateItemCodeOrders(value, context)"],
                 "deleteOrders": ["FOR UPDATE NOWAIT", "DELETE FROM APP_ORDERS WHERE ID = :id", "Vissza: context.messages()."]}
        for method, expected in hints.items():
            with self.subTest(method=method):
                text = method_text(self.service, method)
                self.assertIn("//region AI-feladat: ORDERS / ", text)
                self.assertRegex(text, r"//endregion\n\s*\}\);\n\s*\}$")  # nothing started under the hints
                flat = " ".join(line.strip().lstrip("/").strip() for line in text.splitlines())
                for line in expected:
                    self.assertIn(line, flat)
                self.assertEqual(code_lines(text).count(";"), 1, text)  # only the log1x frame
        self.assertIn("@PersistenceContext\n    private EntityManager entityManager;", self.service)
        self.assertIn("import javax.persistence.EntityManager;", self.service)
        for word in ("Repository", "jdbc", "@Autowired", "FormsChecks.required("):
            self.assertNotIn(word, code_lines(self.service))
        # The SQL of a trigger as native SQL with named parameters, beside the Forms code.
        self.assertIn("SQL: SELECT p.name FROM app_partners p WHERE p.code = :code — :code = row.code; eredmény ->",
                      method_text(self.service, "triggerPostQueryOrders"))
        self.assertIn("SQL: SELECT count(*) FROM app_orders WHERE code LIKE :filter || '%' — :filter = CTRL.FILTER",
                      method_text(self.service, "onCtrlCountBtn"))
        # The table's entity and an empty repository: the developer's, the service does not use them.
        domain = (self.output / "backend/DPS/AppOrdersDomain.java").read_text(encoding="utf-8")
        self.assertIn('@Table(name = "APP_ORDERS")', domain)
        repository = (self.output / "backend/DPS/AppOrdersRepository.java").read_text(encoding="utf-8")
        self.assertIn("public interface AppOrdersRepository extends JpaRepository<AppOrdersDomain, BigDecimal> {\n}", repository)

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
        self.assertEqual(set(tasks), set(HOOKS) | set(ENDPOINTS) | set(CRUD))
        # The model runs the Forms SQL as written: no table structure holds a task back. The tables the code uses
        # are still listed (Adatbázistáblák): their structure gives the Domain entity.
        self.assertEqual({m for m, t in tasks.items() if t["pending_tables"]}, set())
        self.assertEqual(tasks["triggerPostQueryOrders"]["tables"], ["APP_PARTNERS"])
        self.assertEqual(tasks["onCtrlSearchBtn"]["tables"], ["APP_ORDERS"])
        self.assertEqual(tasks["createOrders"]["tables"], [])
        self.assertEqual((tasks["createOrders"]["event"], tasks["createOrders"]["kind"]), ("ÚJ REKORD", "implement"))
        self.assertTrue(read_json(self.output / engine.AREA / "tasks" / (tasks["listOrders"]["id"] + ".json"))["apply_allowed"])
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

    def test_a_query_button_of_a_block_without_endpoints_is_an_ordinary_action(self):
        # The query button's block has no primary key: no Domain, no data endpoint, no <Block>Row to return. The
        # generation stops on nothing (it was a StopIteration); the button is an ordinary AI action that says why.
        source, config = self.root / "nokey.xml", self.root / "config-nokey.json"
        source.write_text(form().replace(' PrimaryKey="true"', ""), encoding="utf-8")
        config.write_text(json.dumps({"java_company_imports": COMPANY_IMPORTS}), encoding="utf-8")
        output = self.root / "nokey"
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = main(["migrate", str(source), "--screen", "--module", "orders", "--config", str(config), "--out", str(output)])
        self.assertEqual(code, 0, err.getvalue())
        service = (output / SERVICE).read_text(encoding="utf-8")
        frame = method_text(service, "onCtrlSearchBtn")
        self.assertIn("public ActionResult onCtrlSearchBtn(UserDto user, ActionRequest request)", frame)
        flat = " ".join(line.strip().lstrip("/").strip() for line in frame.splitlines())  # long notes wrap
        self.assertIn("Lekérdezőgomb lenne, de a(z) ORDERS blokknak nincs adatvégpontja", flat)
        self.assertIn("Nincs elsődleges kulcs", flat)
        endpoints = {e["method"]: e for e in json.loads((output / "analysis/backend-plan.json").read_text())["endpoints"]}
        self.assertNotIn("query_block", endpoints["onCtrlSearchBtn"])
        self.assertFalse(any(e.get("block") == "ORDERS" and e.get("operation") in {"list", "search"} for e in endpoints.values()))
        if shutil.which("java"):
            compile_module(self, output, Path(tempfile.mkdtemp(dir=self.root)))
        # The table that gives the block its key is offered with every task (Adatbázistáblák), and says why.
        from frm_forms import schema_workbench as schema
        index = engine.prepare_workspace(output)
        self.assertTrue(index["tasks"])
        for task in index["tasks"]:
            self.assertIn("APP_ORDERS", task["tables"], task["method"])
            self.assertNotIn("APP_ORDERS", task["pending_tables"] if task["method"] == "onCtrlSearchBtn" else [])
        row = next(t for t in index["schema"]["tables"] if t["table"] == "APP_ORDERS")
        self.assertEqual((row["status"], row["key_for_blocks"]), ("needs_schema", ["ORDERS"]))
        self.assertTrue(any("Elsődleges kulcs kell a(z) ORDERS blokk adatvégpontjaihoz" in r for r in row["reasons"]))
        # Saved with its key, then generated again: the block has its endpoints, the button is a query button.
        saved = self.root / "nokey-schema.json"
        saved.write_text("{}", encoding="utf-8")
        sheet = schema.save_table(output, "APP_ORDERS", [
            {"name": "ID", "data_type": "NUMBER", "nullable": False, "precision": 10, "scale": 0},
            {"name": "CODE", "data_type": "VARCHAR2", "nullable": False, "length": 20, "length_semantics": "CHAR"},
            {"name": "NAME", "data_type": "VARCHAR2", "nullable": True, "length": 80, "length_semantics": "CHAR"}],
            ["ID"], schema.schema_sheet(output)["revision"], saved)
        self.assertTrue(any("futtasd újra a migrálást" in w for w in sheet["warnings"]), sheet["warnings"])
        again = self.root / "nokey-again"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = main(["migrate", str(source), "--screen", "--module", "orders", "--config", str(config),
                         "--schema", str(saved), "--out", str(again)])
        self.assertEqual(code, 0, err.getvalue())
        endpoints = {e["method"]: e for e in json.loads((again / "analysis/backend-plan.json").read_text())["endpoints"]}
        self.assertEqual(endpoints["onCtrlSearchBtn"]["query_block"], "ORDERS")
        self.assertTrue((again / "backend/DPS/AppOrdersRepository.java").is_file())


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
        with patch("frm_forms.ollama.opener", return_value=client), model_path():
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
        # The other hooks still wait (empty under their Forms code); the done one leaves the task list.
        self.assertRegex(method_text(service, "triggerPostQueryOrders"), r"//endregion\n\s*\}$")
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

    def test_sequence_key_hook_runs_its_sql_through_the_entity_manager(self):
        # No table structure needed, no repository, no deterministic shortcut: the model writes the hook in the
        # service, the sequence's SQL through the EntityManager.
        task = self.tasks["triggerPreInsertOrders"]
        self.assertEqual(task["pending_tables"], [])
        preview = engine.preview(self.output, task["id"], self.options)
        self.assertFalse(preview["deterministic"])
        self.assertEqual(preview["repositories"], [])
        self.assertIn("the service's `entityManager` field", preview["user"])
        self.assertIn('SEQ.NEXTVAL: SqlValues.number(entityManager.createNativeQuery("SELECT SEQ.NEXTVAL FROM DUAL")', preview["user"])
        self.assertIn("SQL: SELECT app_orders_seq.NEXTVAL FROM dual; eredmény -> row.id", preview["user"])
        self.assertNotIn("Repositories:", preview["user"])
        self.assertNotIn("// REPOSITORY", preview["user"])
        client = MethodClient(ANSWERS)
        with patch("frm_forms.ollama.opener", return_value=client):
            result = engine.propose(self.output, task["id"], self.options)
        self.assertEqual((result["status"], result["calls"], result["data_access"]), ("proposal", 1, "service"), result)
        self.assertEqual([f["target"] for f in result["files"]], [SERVICE])
        engine.apply_proposal(self.output, result["id"], reviewed=True)
        service = (self.output / SERVICE).read_text(encoding="utf-8")
        hook = method_text(service, "triggerPreInsertOrders")
        self.assertIn('row.id = SqlValues.number(entityManager.createNativeQuery("SELECT app_orders_seq.NEXTVAL FROM "', hook)
        self.assertIn("//region Eredeti Forms-kód: ORDERS / PRE-INSERT", hook)
        self.assertIn("import hu.company.features.cl.CommonMigrateTools.SqlValues;", service)
        if shutil.which("java"):
            compile_module(self, self.output, Path(tempfile.mkdtemp(dir=self.root)))

    def test_every_method_is_written_by_the_model_applied_and_compiles(self):
        # The whole module from the model: the data endpoints, the buttons and the hooks, each from its own
        # frame, through the EntityManager. Applied one by one, the service compiles as Java 11.
        client = MethodClient(ANSWERS)
        for method in CRUD + ENDPOINTS + HOOKS:
            with self.subTest(method=method):
                task = next(t for t in engine.prepare_workspace(self.output)["tasks"] if t["method"] == method)
                with patch("frm_forms.ollama.opener", return_value=client):
                    result = engine.propose(self.output, task["id"], self.options)
                self.assertEqual(result["status"], "proposal", result)
                engine.apply_proposal(self.output, result["id"], reviewed=True)
        self.assertEqual(len(client.requests), 9)
        crud = client.requests[0]["messages"][1]["content"]
        self.assertIn("Write the body of this Java method from the steps of its AI-feladat comment", crud)
        self.assertIn("FormsChecks.required(errors", crud)
        self.assertEqual(engine.prepare_workspace(self.output)["tasks"], [])
        service = (self.output / SERVICE).read_text(encoding="utf-8")
        create = method_text(service, "createOrders")
        self.assertIn("//region AI-feladat: ORDERS / új rekord", create)  # the hints stay above the written body
        self.assertIn('"INSERT INTO APP_ORDERS (ID, CODE, NAME) VALUES (:id, :code, :name)"', create)
        self.assertIn('.setParameter("code", SqlValues.bind(row.code))', create)  # null-safe, repaired locally
        self.assertIn("import javax.persistence.PersistenceException;", service)
        self.assertNotIn("Repository", code_lines(service))
        if shutil.which("java"):
            compile_module(self, self.output, Path(tempfile.mkdtemp(dir=self.root)))

    def test_an_answer_that_would_fail_at_run_time_goes_back_with_the_exact_fault(self):
        # It compiles, but a parameter of the INSERT is never set and PRE-INSERT is not called: the migrator names
        # both faults to the model instead of applying the method.
        task = self.tasks["createOrders"]
        bad = ANSWERS["createOrders"].replace("        triggerPreInsertOrders(row, context);\n", "").replace(
            '.setParameter("name", row.name)', "")
        self.assertNotEqual(bad, ANSWERS["createOrders"])
        client = Client(bad, ANSWERS["createOrders"])
        with patch("frm_forms.ollama.opener", return_value=client):
            result = engine.propose(self.output, task["id"], self.options)
        self.assertEqual((result["status"], result["calls"]), ("proposal", 2), result)
        correction = client.requests[1]["messages"][-1]["content"]
        self.assertIn('has no setParameter for "name"', correction)
        self.assertIn("Call triggerPreInsertOrders(...) as the comment says", correction)
        # A null bound to a native query: sent through SqlValues.bind, repaired here (no model call for it).
        candidate = (self.output / engine.AREA / "proposals" / result["id"] / result["files"][0]["candidate"]).read_text(encoding="utf-8")
        self.assertIn('.setParameter("name", SqlValues.bind(row.name))', candidate)
        self.assertTrue(any("SqlValues.bind" in note for note in result["extraction"]["notes"]), result["extraction"])

    def test_the_sql_of_the_hints_is_the_contract_and_goes_to_verify_db(self):
        plan = read_json(self.output / "analysis/backend-plan.json")
        endpoints = {e["method"]: e for e in plan["endpoints"]}
        self.assertEqual(endpoints["createOrders"]["contract"],
                         {"sql": ["INSERT INTO APP_ORDERS (ID, CODE, NAME) VALUES (:id, :code, :name)"],
                          "calls": ["triggerWhenValidateItemCodeOrders", "triggerPreInsertOrders"]})
        self.assertEqual(endpoints["listOrders"]["contract"],
                         {"sql": ["SELECT ID, CODE, NAME FROM APP_ORDERS ORDER BY ID"], "calls": ["triggerPostQueryOrders"]})
        self.assertEqual(len(endpoints["updateOrders"]["contract"]["sql"]), 2)  # the lock and the UPDATE
        statements = [(s["source"], s["sql"]) for s in read_json(self.output / "analysis/db-statements.json")["statements"]]
        self.assertIn(("ORDERS új rekord", "INSERT INTO APP_ORDERS (ID, CODE, NAME) VALUES (:id, :code, :name)"), statements)
        self.assertIn(("ORDERS törlés", "SELECT ID, CODE, NAME FROM APP_ORDERS WHERE ID = :id FOR UPDATE NOWAIT"), statements)
        self.assertIn(("ORDERS lista", "SELECT ID, CODE, NAME FROM APP_ORDERS ORDER BY ID OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY"),
                      statements)

    def test_answer_without_the_hook_is_rejected_and_nothing_changes(self):
        task = self.tasks["triggerWhenValidateItemCodeOrders"]
        before = (self.output / SERVICE).read_bytes()
        with patch("frm_forms.ollama.opener", return_value=Client("Nem tudom.", "Most sem.")), model_path():
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
        # A whole module (9 tasks, each with a javac syntax check) on a busy CI machine: up to a minute.
        for _ in range(3000):
            state = self.manager.queue_state("job1")
            if state["active"] is None and not state["queued"]:
                return state
            time.sleep(0.02)
        self.fail("Az AI-sor nem ürült ki.")

    def test_migration_completion_queues_every_task_and_applies_the_answers(self):
        self.assertEqual(self.jobs.on_completed, self.manager.migration_completed)
        client = MethodClient(ANSWERS)
        with patch("frm_forms.ollama.opener", return_value=client):
            self.jobs.on_completed("job1")
            state = self.idle()
        # No task waits for a table structure: every method of the module ran, one at a time, and was applied.
        self.assertEqual(len(client.requests), 9)
        finished = state["finished"]
        self.assertEqual(sorted(r["method"] for r in finished), sorted(CRUD + ENDPOINTS + HOOKS))
        self.assertEqual({(r["status"], r["auto"], r["applied"], r["proposal_status"]) for r in finished},
                         {("completed", True, True, "proposal")})
        service = (self.root / "job1/module" / SERVICE).read_text(encoding="utf-8")
        self.assertIn('context.message("Túl rövid kód");', method_text(service, "triggerWhenValidateItemCodeOrders"))
        self.assertIn(".executeUpdate();", method_text(service, "createOrders"))
        index = self.manager.index("job1")
        self.assertEqual(len(index["done_tasks"]), 9)
        self.assertEqual((index["auto"]["done"], index["auto"]["waiting_for_tables"]), (9, 0))
        # Nothing ready is left: a second automatic start queues nothing.
        with patch("frm_forms.ollama.opener", side_effect=AssertionError("no new request")):
            self.assertEqual(self.manager.auto_start("job1", reason="migration")["queued"], 0)

    def test_switched_off_automation_neither_starts_nor_applies(self):
        self.manager.save_settings({"auto_run": False, "auto_apply": False})
        with patch("frm_forms.ollama.opener", side_effect=AssertionError("no automatic request")):
            self.jobs.on_completed("job1")
            self.assertEqual(self.idle()["finished"], [])
        before = (self.root / "job1/module" / SERVICE).read_bytes()
        with patch("frm_forms.ollama.opener", return_value=MethodClient(ANSWERS)):
            queued = self.manager.auto_start("job1")  # the developer's own "start all"
            self.assertEqual(sorted(queued["methods"]), sorted(CRUD + ENDPOINTS + HOOKS))
            state = self.idle()
        self.assertEqual({r["status"] for r in state["finished"]}, {"completed"})
        self.assertFalse(any(r["applied"] for r in state["finished"]))
        self.assertEqual((self.root / "job1/module" / SERVICE).read_bytes(), before)

    def test_a_failed_automatic_run_is_not_repeated_for_the_same_input(self):
        with patch("frm_forms.ollama.opener", return_value=MethodClient({}, "Nem tudom.")):
            self.manager.auto_start("job1", reason="migration")
            state = self.idle()
        self.assertEqual({r["status"] for r in state["finished"]}, {"failed"})
        self.assertIn("/ai-log?job=job1&exchange=", state["finished"][0]["error"])
        with patch("frm_forms.ollama.opener", side_effect=AssertionError("no retry")):
            skipped = self.manager.auto_start("job1", reason="schema")["skipped"]
        self.assertEqual(sorted(skipped), sorted(CRUD + ENDPOINTS + HOOKS))
        with patch("frm_forms.ollama.opener", return_value=MethodClient(ANSWERS)):
            self.assertEqual(self.manager.auto_start("job1", force=True)["queued"], 9)
            self.assertTrue(all(r["applied"] for r in self.idle()["finished"][:9]))

    def test_runs_go_one_at_a_time_and_can_be_cancelled(self):
        from frm_forms.web.assistant import AssistantRequest, CancelRequest
        self.enterContext(model_path())
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

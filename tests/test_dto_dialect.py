"""The Java assistant in the module's DTO dialect: the company's Lombok beans (OrdersActionResultDto, getters).

The rules of the prompt name the generic contract (new ActionResult(...), request.blocks(), row.field). With the
company (AWU) DTOs a small model copied that literally: return new ActionResult(...) in a method that returns
OrdersActionResultDto, row.code on a bean with private fields. The prompt now speaks the module's dialect, the
answer's leftovers of the other dialect are repaired without a model call, and the record hooks of a company module
are tasks at all (their record is OrdersOrdersDto, not OrdersRow).
"""
import contextlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from frm_forms import assistant_context, dto_dialect, reply_repair
from frm_forms import assistant_reply as reply
from frm_forms import backend_assistant as engine
from frm_forms.cli import main
from java_support import trigger_mode, write_stubs
from test_company_cl import CL_IMPORTS
import test_ai_triggers as ai

setUpModule, tearDownModule = trigger_mode('ai')

SERVICE = "backend/DPS/OrdersServiceImpl.java"

# The company surface the DPS side of a module needs besides java_support's stubs (Lombok does not run in the tests:
# the DTO copies get their getters, setters and no-arg constructor written out, see delombok).
COMPANY_STUBS = {
    "lombok.Data": "public @interface Data {}",
    "lombok.Getter": "public @interface Getter {}",
    "lombok.AccessLevel": "public enum AccessLevel { PUBLIC, PRIVATE }",
    "lombok.NoArgsConstructor": "public @interface NoArgsConstructor { AccessLevel access() default AccessLevel.PUBLIC; }",
    "lombok.EqualsAndHashCode": "public @interface EqualsAndHashCode { boolean onlyExplicitlyIncluded() default false; }",
    "hu.company.common.ModuleService": "public interface ModuleService { String getModuleName(); }",
    "hu.company.common.ConstantsBase": ('public class ConstantsBase { public static final String AUTH_HAS_ACCESS_PREFIX = ""; '
                                        'public static final String AUTH_HAS_ACCESS_SUFFIX = ""; public static final String PD = "/"; }'),
    "hu.company.common.WebMenuLeaf": ('public class WebMenuLeaf { public static class Path { public static final String AWU_00123 = "awu"; } '
                                      'public static class FullPath { public static final String AWU_00123 = "/awu"; } }'),
}


def migrate_company(root: Path, output: Path) -> Path:
    source, config = root / "form.xml", root / "company.json"
    source.write_text(ai.form(), encoding="utf-8")
    config.write_text(json.dumps({"AWU_AZON": "00123", "java_company_imports": CL_IMPORTS, "backend_trigger_mode": "ai"}),
                      encoding="utf-8")
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        code = main(["migrate", str(source), "--screen", "--module", "orders", "--config", str(config), "--out", str(output)])
    assert code == 0, err.getvalue()
    return output


def delombok(source: str) -> str:
    """What Lombok writes for @Data/@NoArgsConstructor: a no-arg constructor, the getters and the setters."""
    for name, info in dto_dialect.classes_of(source).items():
        if not info["fields"]:
            continue
        members = ["    public " + name + "() {\n    }"]
        for field, typ in info["fields"].items():
            members.append(f"    public {typ} {dto_dialect.getter(field, typ)}() {{\n        return {field};\n    }}")
            members.append(f"    public void {dto_dialect.setter(field)}({typ} {field}) {{\n        this.{field} = {field};\n    }}")
        last = source.rstrip().rfind("}")
        source = source[:last] + "\n" + "\n\n".join(members) + "\n}\n"
    return source


def compile_dps(test: unittest.TestCase, output: Path, work: Path, cls: str = "Orders") -> None:
    """The company module's DPS service with its CL DTOs and helpers, as Java 11 (the WBS/REST layers need more host)."""
    sources = []
    for path in sorted((output / "backend/CL").glob("*.java")):
        if "RestClient" in path.name:
            continue
        copy = work / "src" / path.name
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_text(delombok(path.read_text(encoding="utf-8")), encoding="utf-8")
        sources.append(copy)
    from java_support import written_frames
    for name in ("ServiceImpl.java", "ServiceBase.java", "Service.java"):
        # the frames the assistant has not written yet are empty (they compile once written): `return null;`
        copy = work / "src" / (cls + name)
        copy.write_text(written_frames((output / "backend/DPS" / (cls + name)).read_text(encoding="utf-8")), encoding="utf-8")
        sources.append(copy)
    sources += write_stubs(work / "stubs")
    for fullname, body in COMPANY_STUBS.items():
        package, _ = fullname.rsplit(".", 1)
        path = (work / "stubs").joinpath(*fullname.split(".")).with_suffix(".java")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("package " + package + ";\n" + body + "\n", encoding="utf-8")
        sources.append(path)
    argsfile = work / "sources.args"
    argsfile.write_text("\n".join('"' + str(p).replace("\\", "/") + '"' for p in sources))
    build = subprocess.run(["java", "com.sun.tools.javac.Main", "--release", "11", "-encoding", "UTF-8", "-proc:none",
                            "-d", str(work / "classes"), "@" + str(argsfile)], capture_output=True, text=True, timeout=120)
    test.assertEqual(build.returncode, 0, build.stdout + build.stderr)


class CompanyDialectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.company = migrate_company(cls.root, cls.root / "company")
        cls.plain = ai.migrate(cls.root, cls.root / "plain")
        cls.options = engine.Options()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def preview(self, output: Path, method: str) -> dict:
        task = next(t for t in engine.prepare_workspace(output)["tasks"] if t["method"] == method)
        return engine.preview(output, task["id"], self.options)

    def test_the_record_hooks_of_a_company_module_are_tasks(self):
        tasks = {t["method"]: t for t in engine.prepare_workspace(self.company)["tasks"]}
        for hook in ai.HOOKS:
            self.assertIn(hook, tasks)
        self.assertIn("private void triggerPostQueryOrders(OrdersOrdersDto row, RuleContext context)",
                      self.preview(self.company, "triggerPostQueryOrders")["user"])

    def test_the_button_rules_name_the_modules_result_class(self):
        user = self.preview(self.company, "onCtrlCountBtn")["user"]
        self.assertIn("4. Return new OrdersActionResultDto(blocks, messages, commands, globals)", user)
        self.assertIn("return new OrdersActionResultDto(blocks, messages, commands, Map.of());", user)  # the skeleton
        self.assertNotRegex(user, r"(?<![\w$])ActionResult(?![\w$])")
        self.assertIn('PlsqlValues.text(request.getBlocks(), "BLOCK", "ITEM")', user)  # null-safe screen values
        self.assertNotIn("request.blocks()", user)
        self.assertIn("public OrdersActionResultDto onCtrlCountBtn(UserDto user, OrdersActionRequestDto request)", user)

    def test_the_default_format_keeps_its_rules(self):
        user = self.preview(self.plain, "onCtrlCountBtn")["user"]
        self.assertIn("4. Return new ActionResult(blocks, messages, commands, globals)", user)
        self.assertIn('PlsqlValues.text(request.blocks(), "BLOCK", "ITEM")', user)
        self.assertNotIn("Lombok", user)
        hook = self.preview(self.plain, "triggerPostQueryOrders")["user"]
        self.assertIn("assign row.field directly.", hook)
        self.assertIn(":ORDERS.CODE -> row.code (String, column CODE)", hook)

    def test_a_hook_reads_and_writes_the_bean_with_getters_and_setters(self):
        user = self.preview(self.company, "triggerPostQueryOrders")["user"]
        self.assertIn("read it as\n   row.getField(), assign it with row.setField(value)", user)
        self.assertIn(":ORDERS.CODE -> row.getCode() / row.setCode(..) (String, column CODE)", user)
        self.assertNotIn("assign row.field directly", user)
        # The migrator's notes of the region too (the original Forms code below them stays as written).
        self.assertIn(":code = row.getCode()", user)
        self.assertIn(":ORDERS.CODE", user)

    def test_a_frame_names_the_beans_accessors_and_its_notes_follow(self):
        user = self.preview(self.company, "createOrders")["user"]
        self.assertIn("The DTOs are Lombok beans with private fields: row.getCode(), row.setCode(value), request.getLimit().", user)
        self.assertIn('FormsChecks.required(errors, "ITEM", row.getField());', user)
        self.assertIn("new OrdersRowResultDto<>(row, context.messages())", user)
        search = self.preview(self.company, "onCtrlSearchBtn")["user"]
        self.assertIn("request.getOffset(), request.getLimit()", search)
        self.assertIn("new OrdersOrdersDto and return new OrdersPageResultDto<>(rows, messages, hasMore)", search)

    def test_answers_in_the_generic_dialect_are_repaired_applied_and_compile(self):
        # The answers of the default format (new ActionResult, request.blocks(), row.code = ..., OrdersRow): what a
        # small model writes from habit. Repaired locally, without a second call, the company module compiles.
        output = Path(tempfile.mkdtemp(dir=self.root)) / "module"
        shutil.copytree(self.company, output)
        client = ai.MethodClient(ai.ANSWERS)
        notes = []
        for method in ai.CRUD + ai.ENDPOINTS + ai.HOOKS:
            with self.subTest(method=method):
                task = next(t for t in engine.prepare_workspace(output)["tasks"] if t["method"] == method)
                with ai.model_path(), patch("frm_forms.ollama.opener", return_value=client):
                    result = engine.propose(output, task["id"], self.options)
                self.assertEqual(result["status"], "proposal", result["extraction"])
                notes += result["extraction"]["notes"]
                engine.apply_proposal(output, result["id"], reviewed=True)
        self.assertEqual(len(client.requests), 9)  # one call each: no correction round
        service = (output / SERVICE).read_text(encoding="utf-8")
        code = ai.code_lines(service)
        self.assertIn("return new OrdersActionResultDto(blocks, messages, new ArrayList<>(), Map.of());", code)
        self.assertNotRegex(code, r"new (?:ActionResult|PageResult|RowResult)\b|\bOrdersRow\b")
        self.assertIn("var values = request.getBlocks() == null", code)
        self.assertIn("row.setPartnerName(names.isEmpty() ? null : SqlValues.text(names.get(0)));", code)
        self.assertIn('FormsChecks.required(errors, "CODE", row.getCode());', code)
        self.assertIn('.setParameter("offset", request.getOffset())', code)  # paging values are never null
        self.assertTrue(any("ActionResult -> OrdersActionResultDto" in n for n in notes), notes)
        self.assertTrue(any("Lombok beanek" in n for n in notes), notes)
        if shutil.which("java"):
            compile_dps(self, output, Path(tempfile.mkdtemp(dir=self.root)))

    def test_a_field_left_after_the_repair_goes_back_to_the_model(self):
        ctx = reply.Context(method="triggerX", service="", mode="service", beans=True,
                            dto_classes={"OrdersOrdersDto": {"generic": [], "fields": {"total": "BigDecimal", "code": "String"}}},
                            parameters={"row": "OrdersOrdersDto", "context": "RuleContext"})
        answer = "```java\n// SERVICE\nprivate void triggerX(OrdersOrdersDto row, RuleContext context) {\n    row.total += 1;\n}\n```"
        result = reply.extract(answer, ctx)
        self.assertTrue(any("Lombok beanek" in p.hu and "row.total" in p.hu for p in result.problems), result.problems)
        self.assertIn("never row.total", " ".join(p.en for p in result.problems))


class DialectUnitTests(unittest.TestCase):
    def test_the_bean_phrases_are_the_rules_own(self):
        rules = assistant_context.FRAME_RULES + assistant_context.HOOK_RULES + assistant_context.QUERY_RESULT
        rules += "Fields of X (public, set them directly):"  # assistant_context.build's query field heading
        for plain, _ in dto_dialect.BEAN_PHRASES:
            self.assertIn(plain, rules)

    def test_the_signature_names_the_wrappers_whatever_the_sources(self):
        dialect = dto_dialect.with_signature(dto_dialect.plain(),
                                             "@Override\npublic MyModulActionResultDto onX(UserDto user, MyModulActionRequestDto request) throws Exception")
        self.assertEqual(dialect["types"], {"ActionResult": "MyModulActionResultDto", "ActionRequest": "MyModulActionRequestDto"})
        text = dto_dialect.prompt_text("5. Return new ActionResult(blocks, messages, commands, globals): ...", dialect)
        self.assertEqual(text, "5. Return new MyModulActionResultDto(blocks, messages, commands, globals): ...")

    def test_generic_names_and_record_accessors_are_repaired_in_code_only(self):
        ctx = reply.Context(method="onX", service="import a.b.OrdersActionResultDto;\n", beans=True,
                            dto_types={"ActionResult": "OrdersActionResultDto"},
                            dto_classes={"OrdersActionRequestDto": {"generic": [], "fields": {"blocks": "Map<String, Map<String, String>>"}},
                                         "OrdersActionResultDto": {"generic": [], "fields": {"messages": "List<String>"}}},
                            parameters={"request": "OrdersActionRequestDto"})
        notes = []
        code = reply_repair.service('var v = request.blocks(); // new ActionResult( stays in a comment\n'
                                    'String s = "ActionResult";\nreturn new ActionResult(v, List.of(), List.of(), Map.of());', ctx, notes)
        self.assertIn("var v = request.getBlocks(); // new ActionResult( stays in a comment", code)
        self.assertIn('String s = "ActionResult";', code)
        self.assertIn("return new OrdersActionResultDto(v, List.of(), List.of(), Map.of());", code)

    def test_a_criteria_chain_and_a_list_of_beans(self):
        classes = {"OSearchRequestDto": {"generic": ["T"], "fields": {"criteria": "T", "limit": "int"}},
                   "OFilterDto": {"generic": [], "fields": {"code": "String"}},
                   "OPageResultDto": {"generic": ["T"], "fields": {"rows": "List<T>", "hasMore": "boolean"}},
                   "ORowDto": {"generic": [], "fields": {"id": "BigDecimal"}}}
        ctx = reply.Context(method="search", beans=True, dto_classes=classes,
                            parameters={"request": "OSearchRequestDto<OFilterDto>"})
        notes = []
        code = reply_repair.service("String c = request.criteria().code;\nvar f = request.getCriteria();\nString d = f.code;\n"
                                    "List<ORowDto> rows = load();\nfor (var r : rows) {\n    r.id = null;\n}\n"
                                    "var page = new OPageResultDto<ORowDto>();\nboolean more = page.hasMore;", ctx, notes)
        self.assertIn("String c = request.getCriteria().getCode();", code)
        self.assertIn("String d = f.getCode();", code)
        self.assertIn("r.setId(null);", code)
        self.assertIn("boolean more = page.isHasMore();", code)  # Lombok: isX() for a primitive boolean


if __name__ == "__main__":
    unittest.main()

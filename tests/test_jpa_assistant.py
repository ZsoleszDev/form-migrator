"""Simple trigger -> JPA repository -> existing service, including Java execution."""
import contextlib
import copy
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from frm_forms import backend_assistant as engine, jpa_assistant as jpa, schema_workbench as schema
from frm_forms.cli import main
from frm_forms.common import MigrationError, read_json
from java_support import COMPANY_IMPORTS, trigger_mode, write_jpa_stubs, write_stubs
from test_backend_assistant import Client, answer
from test_schema_workbench import FORM, columns

setUpModule, tearDownModule = trigger_mode('plsql')


ENTITY = "AccountDomain"
REPOSITORY = "AccountRepository"


def implementation():
    return answer(
        body="""if (request == null || request.blocks() == null || request.blocks().get("CTRL") == null) {
    throw new IllegalArgumentException("Hiányzó kérés.");
}
String raw = request.blocks().get("CTRL").get("ID");
java.math.BigDecimal id = raw == null || raw.isEmpty() ? null : new java.math.BigDecimal(raw);
AccountDomain row = accountRepository.findAccount(id);
if (row == null) {
    throw new IllegalStateException("NO_DATA_FOUND");
}
LookupAccountDto dto = new LookupAccountDto(row.getId(), row.getName());
var values = new java.util.LinkedHashMap<String, String>();
values.put("NAME", dto.getName());
return new ActionResult(java.util.Map.of("CTRL", values), java.util.List.of(), java.util.List.of(), java.util.Map.of());""",
        repositories=[{"name": REPOSITORY, "imports": [], "methods":
            "@Query(\"SELECT d FROM AccountDomain d WHERE d.id = :id\")\n"
            "AccountDomain findAccount(@Param(\"id\") java.math.BigDecimal id);"}],
        dtos=[{"name": "LookupAccountDto", "fields": [
            {"name": "id", "type": "java.math.BigDecimal"}, {"name": "name", "type": "String"}]}],
        test_cases=["ID=1 → CTRL.NAME az entity neve; hiányzó ID → NO_DATA_FOUND; NULL név megmarad."])


def mark_original(out):
    """The assistant offers methods that still carry the original Forms code as a comment (as generated)."""
    service = next((out / "backend/DPS").glob("*ServiceImpl.java"))
    text = service.read_text(encoding="utf-8")
    for method in re.findall(r"public ActionResult (on\w+)\(", text):
        _, opening, _ = engine.method_span(text, method)
        text = (text[:opening + 1] + "\n        //region Eredeti Forms-kód: CTRL.RUN\n"
                "        // SELECT a.NAME INTO :CTRL.NAME FROM APP.ACCOUNT a WHERE a.ID = :CTRL.ID;\n"
                "        //endregion" + text[opening + 1:])
    service.write_text(text, encoding="utf-8")


class JpaAssistantTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name); self.out = self.root / "out"
        self.source = self.root / "form.xml"
        self.source.write_text(FORM, encoding="utf-8")
        config = self.root / "config.json"
        config.write_text(json.dumps({"java_company_imports": COMPANY_IMPORTS, "backend_live": True}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(["migrate", str(self.source), "--screen", "--module", "lookup",
                         "--config", str(config), "--out", str(self.out)])
        self.assertEqual(code, 0)
        self.assertEqual(engine.prepare_workspace(self.out)["tasks"], [])  # finished PL/SQL: nothing to offer
        mark_original(self.out)
        self.task = engine.prepare_workspace(self.out)["tasks"][0]
        self.service = self.out / self.task["target"]
        sheet = schema.schema_sheet(self.out)
        schema.save_table(self.out, "APP.ACCOUNT", columns(), ["ID"], sheet["revision"])

    def propose(self, response=None):
        client = Client(response or implementation())
        with patch("frm_forms.ollama.opener", return_value=client):
            result = engine.propose(self.out, self.task["id"], engine.Options(attempts=1))
        return result, client

    def test_entity_repository_dto_and_existing_service_apply_together(self):
        protected = {p: p.read_bytes() for p in (self.out / "backend").rglob("*.java")
                     if "Controller" in p.name or p.name == "LookupDtos.java"}
        before = self.service.read_bytes()
        result, client = self.propose()
        self.assertEqual(result["data_access"], "jpa")
        self.assertEqual(self.service.read_bytes(), before)
        sent = client.requests[0]["messages"][1]["content"]
        packet = read_json(self.out / engine.AREA / "tasks" / (self.task["id"] + ".json"))
        self.assertIn("```java\n" + packet["existing_method"] + "\n```", sent)
        self.assertIn("SELECT a.NAME", sent)
        # The entity is described in two lines, not sent as Java source.
        self.assertIn("- accountRepository (AccountRepository) for entity AccountDomain, "
                      "table APP.ACCOUNT", sent)
        self.assertIn("fields: BigDecimal id (ID, primary key), String name (NAME)", sent)
        # The SQL has a generated repository method (jpa_sql): the answer is the service only.
        self.assertIn("- accountRepository.findNameById(BigDecimal id) returns String\n"
                      "  SQL: SELECT a.NAME INTO :CTRL.NAME FROM APP.ACCOUNT a WHERE a.ID = :CTRL.ID", sent)
        self.assertNotIn("// REPOSITORY", sent)
        self.assertNotIn("@Entity", sent)
        self.assertNotIn("getName()", sent)
        self.assertEqual(result["context"]["entity_count"], 1)
        self.assertEqual(len(result["files"]), 4)
        engine.apply_proposal(self.out, result["id"], reviewed=True)
        entity = (self.out / ("backend/DPS/" + ENTITY + ".java")).read_text()
        repository = (self.out / ("backend/DPS/" + REPOSITORY + ".java")).read_text()
        self.assertIn("@Entity", entity)
        self.assertIn("@Id", entity)
        self.assertIn("schema = \"APP\"", entity)
        self.assertIn("public interface AccountRepository extends JpaRepository<AccountDomain, BigDecimal> {", repository)
        self.assertIn("AccountDomain findAccount(@Param(\"id\") BigDecimal id);", repository)  # the model's
        self.assertIn("default String findNameById(BigDecimal id) {", repository)  # generated: JPQL @Query + default method
        self.assertNotIn("EntityManager", repository)
        self.assertIn('@Query("SELECT d.name FROM AccountDomain d WHERE d.id = :id")', repository)  # JPQL over the Domain
        source = self.service.read_text()
        self.assertIn("private AccountRepository accountRepository", source)
        start, _, end = engine.method_span(source, self.task["method"])
        self.assertIn("accountRepository.findAccount", source[start:end])
        self.assertNotIn("DbCalls.call", source[start:end])
        self.assertIn("log1x(", source[start:end])
        self.assertEqual({p: p.read_bytes() for p in protected}, protected)

    def test_missing_real_primary_key_stops_before_ai(self):
        sheet = schema.schema_sheet(self.out)
        schema.save_table(self.out, "APP.ACCOUNT", columns(), [], sheet["revision"])
        with patch("frm_forms.ollama.opener", side_effect=AssertionError("No invented key")), self.assertRaisesRegex(MigrationError, "PK"):
            engine.propose(self.out, self.task["id"], engine.Options())

    def test_repository_code_and_dto_shapes_are_checked(self):
        for methods in ("public class WrongController {}", "private String field;",
                        "public String bad() { return entityManager.createNativeQuery(\"SELECT 1\").toString(); }"):
            value = implementation()
            value["repositories"][0]["methods"] = methods
            with self.subTest(methods=methods), self.assertRaises(MigrationError):
                engine.validate_response(value)
        value = implementation(); value["repositories"][0]["name"] = "InventedRepository"
        packet = read_json(self.out / engine.AREA / "tasks" / (self.task["id"] + ".json"))
        engine.prepare_workspace(self.out)
        packet = read_json(self.out / engine.AREA / "tasks" / (self.task["id"] + ".json"))
        packet["service_package_source"] = self.service.read_text()
        with self.assertRaisesRegex(MigrationError, "ismeretlen repository"):
            jpa.render_files(self.out, packet, value, self.service.read_text(), jpa.plan(packet, self.out))
        value = implementation(); value["dtos"][0]["fields"][0]["type"] = "UnknownCompanyClass"
        with self.assertRaisesRegex(MigrationError, "DTO"):
            engine.validate_response(value)

    def test_existing_repository_is_preserved_and_its_change_invalidates_apply(self):
        packet = read_json(self.out / engine.AREA / "tasks" / (self.task["id"] + ".json"))
        engine.prepare_workspace(self.out)
        packet = read_json(self.out / engine.AREA / "tasks" / (self.task["id"] + ".json"))
        packet["service_package_source"] = self.service.read_text()
        descriptor = jpa.plan(packet, self.out)[0]
        path = self.out / ("backend/DPS/" + REPOSITORY + ".java")
        path.write_text(descriptor["files"][path.relative_to(self.out).as_posix()].replace(
            "\n}", "\n    default String existingMethod() { return \"kept\"; }\n}"), encoding="utf-8")
        result, _ = self.propose()
        candidate = next(f for f in result["files"] if f["target"].endswith("Repository.java"))
        self.assertIn("existingMethod", (self.out / engine.AREA / "proposals" / result["id"] / candidate["candidate"]).read_text())
        path.write_text(path.read_text() + "\n// developer change\n")
        before = self.service.read_bytes()
        with self.assertRaisesRegex(MigrationError, "megváltozott"):
            engine.apply_proposal(self.out, result["id"], reviewed=True)
        self.assertEqual(self.service.read_bytes(), before)

    def test_composite_keys_namespace_and_lobs_are_mapped_without_sql(self):
        packet = {"module": "sample", "blocks": [{"database": True, "table": "APP.MASTER"}],
                  "schema": {"tables": {"APP.MASTER": {"primary_key": ["ID", "TENANT"], "columns": {
                      "ID": {"data_type": "NUMBER", "nullable": False},
                      "TENANT": {"data_type": "VARCHAR2", "nullable": False, "length": 30},
                      "TEXT": {"data_type": "CLOB", "nullable": True},
                      "BINARY": {"data_type": "BLOB", "nullable": True}}}}},
                  "config": {"jpa_namespace": "jakarta"}, "service_package_source": "package sample.dps;"}
        descriptor = jpa.plan(packet, self.out)[0]
        entity = descriptor["files"]["backend/DPS/MasterDomain.java"]
        self.assertEqual(descriptor["repository"], "MasterRepository")
        self.assertIn("extends JpaRepository<MasterDomain, MasterDomain.Key>",
                      descriptor["files"]["backend/DPS/MasterRepository.java"])
        self.assertIn("import jakarta.persistence.IdClass;", entity)
        self.assertIn("@IdClass(MasterDomain.Key.class)", entity)
        self.assertIn("@Lob", entity)
        self.assertIn("byte[] binary", entity)
        # the composite key: a NUMBER part compares by value (1 and 1.0 are the same row)
        self.assertIn("id.compareTo(key.id) == 0", entity)
        self.assertIn("stripTrailingZeros()", entity)

    def test_two_triggers_reuse_one_repository_and_dto(self):
        tree = ET.fromstring(FORM)
        block = tree.find(".//Block")
        second = copy.deepcopy(block.find("Item[@Name='RUN']")); second.set("Name", "RUN2")
        block.append(second)
        self.source.write_text(ET.tostring(tree, encoding="unicode"))
        self.out = self.root / "two-triggers"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            status = main(["migrate", str(self.source), "--screen", "--module", "lookup",
                           "--config", str(self.root / "config.json"), "--out", str(self.out)])
        self.assertEqual(status, 0)
        mark_original(self.out)
        self.task = engine.prepare_workspace(self.out)["tasks"][0]
        self.service = self.out / self.task["target"]
        sheet = schema.schema_sheet(self.out)
        schema.save_table(self.out, "APP.ACCOUNT", columns(), ["ID"], sheet["revision"])
        first, _ = self.propose()
        engine.apply_proposal(self.out, first["id"], reviewed=True)
        repository = self.out / ("backend/DPS/" + REPOSITORY + ".java")
        original = repository.read_bytes()
        self.task = engine.prepare_workspace(self.out)["tasks"][0]
        response = implementation(); response["repositories"] = []; response["dtos"] = []
        second, client = self.propose(response)
        context = client.requests[0]["messages"][1]["content"]
        # Only the signature of the earlier trigger's method is offered for reuse, not the class.
        self.assertIn("existing methods (reuse them): AccountDomain findAccount(BigDecimal id)", context)
        self.assertNotIn("@PersistenceContext", context)
        engine.apply_proposal(self.out, second["id"], reviewed=True)
        self.assertEqual(repository.read_bytes(), original)
        self.assertEqual(self.service.read_text().count("private AccountRepository accountRepository;"), 1)
        self.assertEqual(engine.prepare_workspace(self.out)["tasks"], [])

    def test_reported_needs_context_with_repository_is_now_a_visible_draft(self):
        # 4.33.3: "Nem érkezett érvényes Java-javaslat: Hiányzó adat esetén nincs részleges repository/DTO." twice.
        answer = implementation()
        answer.update(status="needs_context", missing_context=["A PKG_ACCOUNT.CHECK_LIMIT függvény forrása"], test_cases=[])
        result, client = self.propose(answer)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(result["status"], "needs_context")
        self.assertEqual(result["response"]["missing_context"], ["A PKG_ACCOUNT.CHECK_LIMIT függvény forrása"])
        targets = {f["target"] for f in result["files"]}
        self.assertIn("backend/DPS/" + REPOSITORY + ".java", targets)
        folder = self.out / engine.AREA / "proposals" / result["id"]
        repository = next(f for f in result["files"] if f["target"].endswith(REPOSITORY + ".java"))
        self.assertIn("findAccount", (folder / repository["candidate"]).read_text())
        with self.assertRaisesRegex(MigrationError, "nem alkalmazható"):
            engine.apply_proposal(self.out, result["id"], reviewed=True)

    def test_markdown_whole_classes_compile_and_execute_as_java_11(self):
        if not shutil.which("java"): self.skipTest("JDK szükséges")
        markdown = """A kód:

```java
// SERVICE
@Override
public ActionResult onCtrlRun(UserDto user, ActionRequest request) throws Exception {
    return log1x(log, LookupConstants.ON_CTRL_RUN_NAME, user, null, () -> {
        BigDecimal id = PlsqlValues.number(request.blocks(), "CTRL", "ID");
        AccountDomain row = accountRepository.loadAccount(id);
        if (row == null) {
            throw new IllegalStateException("NO_DATA_FOUND");
        }
        Map<String, Map<String, String>> blocks = new LinkedHashMap<>();
        PlsqlValues.put(blocks, "CTRL", "NAME", row.getName());
        return new ActionResult(blocks, List.of(), List.of(), Map.of());
    });
}
```

```java
package hu.company.features.lookup.dps;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface AccountRepository extends JpaRepository<AccountDomain, BigDecimal> {
    @Query("SELECT d FROM AccountDomain d WHERE d.id = :id")
    AccountDomain loadAccount(@Param("id") BigDecimal id);
}
```
"""
        result, _ = self.propose(markdown)
        self.assertEqual(result["status"], "proposal")
        self.assertEqual(result["extraction"]["problems"], [])
        self.compile_and_run(result, "loadAccount")

    def test_reviewed_service_repository_and_dto_compile_and_execute_as_java_11(self):
        if not shutil.which("java"): self.skipTest("JDK szükséges")
        result, _ = self.propose()
        self.compile_and_run(result)

    def compile_and_run(self, result, method="findAccount"):
        engine.apply_proposal(self.out, result["id"], reviewed=True)
        stubs = write_stubs(self.root / "stubs") + write_jpa_stubs(self.root / "stubs")
        runner = self.root / "Runner.java"
        runner.write_text("""import hu.company.features.lookup.dps.*;
import hu.company.features.lookup.cl.LookupDtos.*;
import hu.company.common.UserDto;
import java.util.Map;
public class Runner {
    public static void main(String[] args) throws Exception {
        AccountDomain entity = new AccountDomain();
        entity.setId(new java.math.BigDecimal("1"));
        entity.setName("Teszt ügyfél");
        // the repository interface as Spring Data would run its @Query method: one row by id, or none
        AccountRepository repository = (AccountRepository) java.lang.reflect.Proxy.newProxyInstance(
            AccountRepository.class.getClassLoader(), new Class<?>[] {AccountRepository.class}, (proxy, call, arguments) -> {
                if (!call.getName().equals("METHOD")) throw new UnsupportedOperationException(call.getName());
                return java.math.BigDecimal.ONE.equals(arguments[0]) ? entity : null;
            });
        LookupServiceImpl service = new LookupServiceImpl(new org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate());
        java.lang.reflect.Field dep = LookupServiceImpl.class.getDeclaredField("accountRepository");
        dep.setAccessible(true); dep.set(service, repository);
        ActionResult result = service.onCtrlRun(new UserDto(), new ActionRequest(Map.of("CTRL", Map.of("ID", "1")), Map.of()));
        if (!"Teszt ügyfél".equals(result.blocks().get("CTRL").get("NAME"))) throw new AssertionError("Repository result was not returned");
        entity.setName(null);
        result = service.onCtrlRun(new UserDto(), new ActionRequest(Map.of("CTRL", Map.of("ID", "1")), Map.of()));
        if (!result.blocks().get("CTRL").containsKey("NAME") || result.blocks().get("CTRL").get("NAME") != null) throw new AssertionError("NULL was lost");
        try {
            service.onCtrlRun(new UserDto(), new ActionRequest(Map.of("CTRL", Map.of("ID", "2")), Map.of()));
            throw new AssertionError("Missing row was reported as success");
        } catch (IllegalStateException expected) {
            if (!"NO_DATA_FOUND".equals(expected.getMessage())) throw expected;
        }
        System.out.println("SERVICE_REPOSITORY_RUNTIME_OK");
    }
}""".replace("METHOD", method))
        paths = list((self.out / "backend").rglob("*.java")) + stubs + [runner]
        args = self.root / "sources.args"
        args.write_text("\n".join('"' + str(p) + '"' for p in paths))
        classes = self.root / "classes"; classes.mkdir()
        built = subprocess.run(["java", "com.sun.tools.javac.Main", "--release", "11", "-d", str(classes), "@" + str(args)],
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        executed = subprocess.run(["java", "-cp", str(classes), "Runner"], capture_output=True, text=True, timeout=30)
        self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)
        self.assertIn("SERVICE_REPOSITORY_RUNTIME_OK", executed.stdout)


if __name__ == "__main__":
    unittest.main()


"""What the AI-written method needs from outside, filled in afterwards without a model call.

- java-variables.json: a variable the model creates (String igkod = ...) gets the map's value, also when it is set
  straight into a query; the service gets the entry's import and @Autowired field.
- A parameter of SQL built at run time (sql.append(") and X = :igkod")) that no setParameter sets: bound from the
  variable of that name where its piece gets into the statement (sql_binding; the variants are in test_sql_binding).
"""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from frm_forms import backend_assistant as engine
from frm_forms import sql_binding
from java_support import trigger_mode
import test_ai_triggers as ai

setUpModule, tearDownModule = trigger_mode('ai')

BUILT = '''String igkod = PlsqlValues.text(values, "YX", "XY");
StringBuilder sql = new StringBuilder("SELECT A FROM T WHERE (1 = 1");
if (kod != null) {
    sql.append(" and KOD = :kod");
}
sql.append(") and AIT_ILLIGAZ_KOD = :igkod");
Query q = entityManager.createNativeQuery(sql.toString());
if (kod != null) {
    q.setParameter("kod", kod);
}
List<Object[]> rows = q.getResultList();'''


CTX = SimpleNamespace(parameters={"kod": "String"}, variables={}, sql_constants=[])


class BuiltSqlTests(unittest.TestCase):
    def test_a_parameter_always_in_the_statement_is_bound_from_its_variable(self):
        notes, warnings = [], []
        fixed = sql_binding.bind(BUILT, CTX, notes, warnings)
        self.assertIn('createNativeQuery(sql.toString()).setParameter("igkod", igkod);', fixed)
        self.assertEqual(fixed.count('setParameter("kod"'), 1)  # set under its own if: right, left as written
        self.assertEqual(warnings, [])
        self.assertIn(":igkod", notes[0])

    def test_a_parameter_under_an_if_is_bound_in_that_if(self):
        conditional = BUILT.replace('if (kod != null) {\n    q.setParameter("kod", kod);\n}\n', "")
        notes, warnings = [], []
        fixed = sql_binding.bind(conditional, CTX, notes, warnings)
        self.assertIn('    sql.append(" and KOD = :kod");\n    sqlParams.put("kod", SqlValues.bind(kod));\n}', fixed)
        self.assertIn("Query q = entityManager.createNativeQuery(sql.toString())", fixed)
        self.assertIn("sqlParams.forEach(q::setParameter);", fixed)
        self.assertEqual(warnings, [])

    def test_a_parameter_without_a_value_is_asked_from_the_model(self):
        unknown = BUILT.replace('String igkod = PlsqlValues.text(values, "YX", "XY");\n', "")
        notes, warnings = [], []
        self.assertEqual(sql_binding.bind(unknown, CTX, notes, warnings), unknown)
        self.assertTrue(any(":igkod" in hu and "variable named igkod" in en for hu, en in sql_binding.problems(unknown, CTX)))

    def test_written_sql_is_bound_from_the_variable_of_its_name(self):
        code = 'String x = "1";\nvar rows = entityManager.createNativeQuery("SELECT 1 FROM T WHERE X = :x").getResultList();'
        self.assertIn('.setParameter("x", x).getResultList()', sql_binding.bind(code, CTX, [], []))


class VariableMapTests(unittest.TestCase):
    def test_a_variable_of_the_map_gets_its_value_its_field_and_its_import(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            variables = root / "java-variables.json"
            variables.write_text(json.dumps({"variables": [{
                "variableName": "filter", "variableValue": 'commonService.Details("igkod")',
                "autowired": "commonService", "import": "hu.company.pelda.CommonService"}]}), encoding="utf-8")
            with patch.dict(os.environ, {"FRM_JAVA_VARIABLE_MAP": str(variables)}):
                output = ai.migrate(root, root / "module")
                task = next(t for t in engine.prepare_workspace(output)["tasks"] if t["method"] == "onCtrlCountBtn")
                client = ai.MethodClient(ai.ANSWERS)
                with patch("frm_forms.ollama.opener", return_value=client):
                    result = engine.propose(output, task["id"], engine.Options())
                self.assertEqual(result["status"], "proposal", result["extraction"])
                # the model is not told about the map: the value comes in afterwards
                self.assertNotIn("commonService", client.requests[0]["messages"][1]["content"])
                self.assertTrue(any("java-variables.json" in n for n in result["extraction"]["notes"]))
                engine.apply_proposal(output, result["id"], reviewed=True)
            service = (output / ai.SERVICE).read_text(encoding="utf-8")
            method = ai.method_text(service, "onCtrlCountBtn")
            self.assertIn('String filter = commonService.Details("igkod");', method)
            self.assertIn('.setParameter("filter", SqlValues.bind(filter))', method)
            self.assertIn("import hu.company.pelda.CommonService;", service)
            self.assertIn("@Autowired\n    private CommonService commonService;", service)
            self.assertEqual(service.count("private CommonService commonService;"), 1)
            if shutil.which("java"):
                ai.compile_module(self, output, Path(tempfile.mkdtemp(dir=root)))

    def test_a_repository_field_of_the_map_is_not_an_unknown_repository(self):
        # formsCommonsRepository.getIgKod(user) is the migrator's own repair: the checks must know the field
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            variables = root / "java-variables.json"
            variables.write_text(json.dumps({"variables": [{
                "variableName": "filter", "variableValue": "formsCommonsRepository.getIgKod(user)",
                "autowired": "formsCommonsRepository", "import": "hu.company.pelda.FormsCommonsRepository"}]}),
                encoding="utf-8")
            with patch.dict(os.environ, {"FRM_JAVA_VARIABLE_MAP": str(variables)}):
                output = ai.migrate(root, root / "module")
                task = next(t for t in engine.prepare_workspace(output)["tasks"] if t["method"] == "onCtrlCountBtn")
                client = ai.MethodClient(ai.ANSWERS)
                with patch("frm_forms.ollama.opener", return_value=client):
                    result = engine.propose(output, task["id"], engine.Options())
            self.assertEqual(result["status"], "proposal", result["extraction"])
            self.assertEqual(len(client.requests), 1)  # no correction round for the migrator's own code
            self.assertFalse(any("repository" in p.lower() for p in result["extraction"]["problems"]))


class ProjectHelperTests(unittest.TestCase):
    def test_an_older_common_migrate_tools_in_the_project_is_named_in_every_proposal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = ai.migrate(root, root / "module")
            folder = root / "project/cl/src/main/java/hu/acme/cl"
            folder.mkdir(parents=True)
            copy = folder / "CommonMigrateTools.java"
            copy.write_text('package hu.acme.cl;\npublic final class CommonMigrateTools {\n'
                            '    public static final String VERSION = "6";\n}\n', encoding="utf-8")
            effective = output / "analysis/effective-config.json"
            data = json.loads(effective.read_text(encoding="utf-8"))
            data["config"]["project_layout"] = {"CL": str(folder)}
            effective.write_text(json.dumps(data), encoding="utf-8")
            task = next(t for t in engine.prepare_workspace(output)["tasks"] if t["method"] == "onCtrlCountBtn")
            warnings = engine.preview(output, task["id"], engine.Options())["warnings"]
            self.assertTrue(any("6. változat" in w and "backend/CL/CommonMigrateTools.java" in w for w in warnings), warnings)
            with patch("frm_forms.ollama.opener", return_value=ai.MethodClient(ai.ANSWERS)):
                result = engine.propose(output, task["id"], engine.Options())
            self.assertIn("CommonMigrateTools", result["extraction"]["warnings"][0])
            # the project has the module's version: nothing to say
            copy.write_text((output / "backend/CL/CommonMigrateTools.java").read_text(encoding="utf-8"), encoding="utf-8")
            self.assertEqual(engine.preview(output, task["id"], engine.Options())["warnings"], [])


if __name__ == "__main__":
    unittest.main()

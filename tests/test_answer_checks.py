"""What javac cannot see in an AI-written service method (answer_checks): the parameters of its native queries, the
statements and hooks its task names, and the null-safe binding the migrator repairs itself."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from frm_forms import answer_checks as checks
from frm_forms.compact_backend import common_tools_source
from java_support import write_stubs

CREATE = '''var context = new RuleContext();
triggerWhenValidateItemCodeOrders(row, context);
triggerPreInsertOrders(row, context);
entityManager.createNativeQuery("INSERT INTO APP_ORDERS (ID, CODE, NAME) "
        + "VALUES (:id, :code, :name)")
        .setParameter("id", row.id).setParameter("code", row.code).setParameter("name", row.name).executeUpdate();
return new RowResult<>(row, context.messages());'''
CONTRACT = {"sql": ["INSERT INTO APP_ORDERS (ID, CODE, NAME) VALUES (:id, :code, :name)"],
            "calls": ["triggerWhenValidateItemCodeOrders", "triggerPreInsertOrders"]}


class NativeQueryTests(unittest.TestCase):
    def english(self, code, contract=None):
        return [en for _, en in checks.problems(code, contract)]

    def test_a_complete_method_has_no_problem(self):
        self.assertEqual(checks.problems(CREATE, CONTRACT), [])
        # letter case and line breaks of the SQL do not matter, its text does
        self.assertEqual(checks.problems(CREATE.replace("INSERT INTO APP_ORDERS", "insert into\\n app_orders"), CONTRACT), [])

    def test_a_parameter_of_the_sql_that_is_never_set(self):
        problems = self.english(CREATE.replace('.setParameter("name", row.name)', ""))
        self.assertEqual(len(problems), 1)
        self.assertIn('has no setParameter for "name"', problems[0])

    def test_a_parameter_the_sql_does_not_have(self):
        problems = self.english(CREATE.replace('"name", row.name', '"nev", row.name'))
        self.assertTrue(any('has no setParameter for "name"' in p for p in problems))
        self.assertTrue(any("has no parameter :nev" in p for p in problems))

    def test_a_query_object_in_a_variable(self):
        code = ('Query q = entityManager.createNativeQuery("UPDATE T SET A = :a WHERE T = \'HH:mi\' AND B = :b");\n'
                'q.setParameter("a", value.a);\nq.executeUpdate();\n'
                'Query q2 = entityManager.createNativeQuery("DELETE FROM T WHERE B = :b");\nq2.setParameter("b", value.b);\n'
                'q2.executeUpdate();')
        problems = self.english(code)
        self.assertEqual(len(problems), 1)  # 'HH:mi' is a literal of the SQL, :b of the first query is not set
        self.assertIn("UPDATE T SET A", problems[0])

    def test_sql_built_at_run_time_or_handed_on_is_not_judged(self):
        self.assertEqual(checks.problems('String sql = "SELECT 1 FROM T WHERE X = :x";\n'
                                         'var rows = entityManager.createNativeQuery(sql + where).getResultList();'), [])
        self.assertEqual(checks.problems('return page(entityManager.createNativeQuery("SELECT 1 FROM T WHERE X = :x"));'), [])

    def test_the_statement_and_the_hooks_of_the_task(self):
        altered = self.english(CREATE.replace("(:id, :code, :name)", "(:id, :code, NULL)").replace('.setParameter("name", row.name)', ""),
                               CONTRACT)
        self.assertEqual(len(altered), 1)
        self.assertIn("exactly as written", altered[0])
        self.assertIn("VALUES (:id, :code, :name)", altered[0])
        missing = self.english(CREATE.replace("triggerPreInsertOrders(row, context);\n", ""), CONTRACT)
        self.assertEqual(missing, ["Call triggerPreInsertOrders(...) as the comment says; the method exists."])
        swapped = CREATE.replace("triggerWhenValidateItemCodeOrders(row, context);\n", "").replace(
            "triggerPreInsertOrders(row, context);\n", "triggerPreInsertOrders(row, context);\ntriggerWhenValidateItemCodeOrders(row, context);\n")
        # another order is shown to the developer, not a correction round (a loop or a helper can reorder the text)
        self.assertEqual(self.english(swapped, CONTRACT), [])
        self.assertIn("triggerPreInsertOrders korábban fut", checks.order_warnings(swapped, CONTRACT)[0])
        self.assertEqual(checks.order_warnings(CREATE, CONTRACT), [])
        # a hook named only in a comment is not a call
        self.assertTrue(self.english(CREATE.replace("triggerPreInsertOrders(row, context);", "// triggerPreInsertOrders(row, context);"),
                                     CONTRACT))


class BindTests(unittest.TestCase):
    def test_values_that_may_be_null_are_bound_through_sqlvalues(self):
        notes = []
        code = checks.bind(CREATE, notes)
        self.assertIn('.setParameter("id", SqlValues.bind(row.id)).setParameter("code", SqlValues.bind(row.code))', code)
        self.assertEqual(len(notes), 1)
        self.assertEqual(checks.bind(code, notes), code)  # once
        self.assertEqual(checks.problems(code, CONTRACT), [])

    def test_values_that_cannot_be_null_and_typed_procedure_parameters_stay(self):
        code = ('var rows = entityManager.createNativeQuery("SELECT A FROM T WHERE B = :b OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY")\n'
                '        .setParameter("b", nvl(request.blocks().get("B").get("X"), "x")).setParameter("offset", request.offset())\n'
                '        .setParameter("limit", limit + 1).getResultList();\n'
                'StoredProcedureQuery call = entityManager.createStoredProcedureQuery("PKG.P");\n'
                'call.setParameter("P_ID", row.id);\n')
        fixed = checks.bind(code, [])
        self.assertIn('.setParameter("b", SqlValues.bind(nvl(request.blocks().get("B").get("X"), "x")))', fixed)
        self.assertIn('.setParameter("offset", request.offset())', fixed)
        self.assertIn('.setParameter("limit", limit + 1)', fixed)
        self.assertIn('call.setParameter("P_ID", row.id);', fixed)


HELPERS = r'''
import java.math.BigDecimal;
import java.util.Arrays;
import java.util.Collections;
import javax.sql.rowset.serial.SerialClob;
import hu.ceg.cl.CommonMigrateTools.LovQuery;
import hu.ceg.cl.CommonMigrateTools.SqlValues;

public class HelperRun {
    static void check(boolean ok, String what) {
        if (!ok) throw new AssertionError(what);
    }

    public static void main(String[] args) throws Exception {
        check("".equals(SqlValues.bind(null)), "a null is bound as an empty text");
        check(SqlValues.bind(BigDecimal.ONE) == BigDecimal.ONE, "a value is bound as it is");
        check("hosszú szöveg".equals(SqlValues.text(new SerialClob("hosszú szöveg".toCharArray()))), "a CLOB column is its text");
        check(SqlValues.text("") == null, "an empty text is null");
        // a query of one column returns the values themselves, not arrays
        var one = LovQuery.rows(Arrays.asList("A", "B"), "KOD");
        check(one.size() == 2 && "B".equals(one.get(1).get("KOD")), "one column");
        var two = LovQuery.rows(Collections.singletonList(new Object[] {"A", new BigDecimal("1.50")}), "KOD", "AR");
        check("A".equals(two.get(0).get("KOD")) && "1.50".equals(two.get(0).get("AR")), "two columns");
        System.out.println("helpers passed");
    }
}
'''


class HelperRuntimeTests(unittest.TestCase):
    def test_bind_clob_text_and_one_column_rows_at_run_time(self):
        if not shutil.which("java"):
            self.skipTest("Java 11+ required")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tools = root / "hu/ceg/cl/CommonMigrateTools.java"
            tools.parent.mkdir(parents=True)
            tools.write_text(common_tools_source({"backend_trigger_mode": "ai", "common_migrate_tools_package": "hu.ceg.cl",
                                                  "java_package": "hu.ceg"}), encoding="utf-8")
            main = root / "HelperRun.java"
            main.write_text(HELPERS, encoding="utf-8")
            sources = [str(tools), str(main)] + [str(p) for p in write_stubs(root / "stubs")]
            build = subprocess.run(["java", "com.sun.tools.javac.Main", "--release", "11", "-encoding", "UTF-8", "-d",
                                    str(root / "classes")] + sources, capture_output=True, text=True, timeout=120)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run(["java", "-cp", str(root / "classes"), "HelperRun"], capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("helpers passed", run.stdout)


if __name__ == "__main__":
    unittest.main()

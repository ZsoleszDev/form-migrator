"""The :names of the native SQL the model writes are bound where their piece gets into the statement (sql_binding).

The corpus is what a small model writes for a Forms query with a DEFAULT_WHERE: SQL built from pieces, some under an
if, with the binds forgotten, put under the wrong if or in the wrong place. Every repaired variant must compile
(javac --release 11), must not change on a second pass, and must bind each :name exactly when its piece is in the SQL.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from frm_forms import backend_assistant as engine
from frm_forms import sql_binding
from java_support import trigger_mode
import test_ai_triggers as ai

setUpModule, tearDownModule = trigger_mode('ai')

CTX = SimpleNamespace(parameters={"request": "ActionRequest"}, sql_constants=[],
                      variables={"igkod": {"name": "igkod", "value": "formsCommonsRepository.getIgKod(user)"}})

# name: (method parameters, body, in the result, not in the result)
CASES = {
    "unconditional_piece_without_bind": ("String kod", '''
        String igkod = PlsqlValues.text(values, "YX", "XY");
        StringBuilder sql = new StringBuilder("SELECT A FROM T WHERE (1 = 1");
        if (kod != null) {
            sql.append(" and KOD = :kod");
        }
        sql.append(") and AIT_ILLIGAZ_KOD = :igkod");
        Query q = entityManager.createNativeQuery(sql.toString());
        if (kod != null) {
            q.setParameter("kod", kod);
        }
        List<Object[]> rows = q.getResultList();
        return rows;''', ['createNativeQuery(sql.toString()).setParameter("igkod", igkod);',
                          'if (kod != null) {\n            q.setParameter("kod", kod);'], ["sqlParams"]),
    "conditional_piece_bound_always": ("", '''
        String kod = PlsqlValues.text(values, "B", "KOD");
        StringBuilder sql = new StringBuilder("SELECT A FROM T WHERE 1 = 1");
        if (kod != null) {
            sql.append(" AND KOD = :kod");
        }
        Query q = entityManager.createNativeQuery(sql.toString());
        q.setParameter("kod", kod);
        return q.getResultList();''', ['StringBuilder sql = new StringBuilder("SELECT A FROM T WHERE 1 = 1");\n'
                                       '        Map<String, Object> sqlParams = new LinkedHashMap<>();',
                                       '            sql.append(" AND KOD = :kod");\n'
                                       '            sqlParams.put("kod", SqlValues.bind(kod));\n        }',
                                       'Query q = entityManager.createNativeQuery(sql.toString());\n'
                                       '        sqlParams.forEach(q::setParameter);'], ['q.setParameter("kod"']),
    "braceless_piece_chain_split": ("", '''
        String kod = PlsqlValues.text(values, "B", "KOD");
        StringBuilder sql = new StringBuilder("SELECT A FROM T WHERE 1 = 1");
        if (kod != null) sql.append(" AND KOD = :kod");
        List<Object[]> rows = entityManager.createNativeQuery(sql.toString()).getResultList();
        return rows;''', ['if (kod != null) {\n            sql.append(" AND KOD = :kod");\n'
                          '            sqlParams.put("kod", SqlValues.bind(kod));\n        }',
                          'Query sqlQuery = entityManager.createNativeQuery(sql.toString());\n'
                          '        sqlParams.forEach(sqlQuery::setParameter);\n'
                          '        List<Object[]> rows = sqlQuery.getResultList();'], []),
    "unconditional_piece_bound_under_if": ("", '''
        String nev = PlsqlValues.text(values, "B", "NEV");
        StringBuilder sql = new StringBuilder("SELECT A FROM T WHERE NEV = :nev");
        Query q = entityManager.createNativeQuery(sql.toString());
        if (nev != null) {
            q.setParameter("nev", nev);
        }
        return q.getResultList();''', ['createNativeQuery(sql.toString()).setParameter("nev", nev);'], ["if (nev != null)"]),
    "right_conditional_bind": ("", '''
        String kod = PlsqlValues.text(values, "B", "KOD");
        StringBuilder sql = new StringBuilder("SELECT A FROM T WHERE 1 = 1");
        if (kod != null) sql.append(" AND KOD = :kod");
        Query q = entityManager.createNativeQuery(sql.toString());
        if (kod != null) q.setParameter("kod", kod);
        return q.getResultList();''', None, []),
    "map_idiom_value_typed": ("", '''
        String kod = PlsqlValues.text(values, "B", "KOD");
        String nev = PlsqlValues.text(values, "B", "NEV");
        Map<String, Object> params = new HashMap<>();
        StringBuilder sql = new StringBuilder("SELECT A FROM T WHERE 1 = 1");
        if (kod != null) {
            sql.append(" AND KOD = :kod");
            params.put("kod", kod);
        }
        if (nev != null) {
            sql.append(" AND NEV LIKE :nev");
        }
        Query q = entityManager.createNativeQuery(sql.toString());
        params.forEach(q::setParameter);
        return q.getResultList();''', ['params.put("kod", SqlValues.bind(kod));',
                                       '            sql.append(" AND NEV LIKE :nev");\n'
                                       '            params.put("nev", SqlValues.bind(nev));'], ["sqlParams"]),
    "string_plus_assign": ("", '''
        String kod = PlsqlValues.text(values, "B", "KOD");
        String sql = "SELECT A FROM T WHERE 1 = 1";
        if (kod != null) {
            sql += " AND KOD = :kod";
        }
        Query q = entityManager.createNativeQuery(sql);
        q.setParameter("kod", kod);
        return q.getResultList();''', ['sql += " AND KOD = :kod";\n            sqlParams.put("kod", SqlValues.bind(kod));'],
                           ['q.setParameter("kod"']),
    "where_builder_read_by_the_statement": ("", '''
        String kod = PlsqlValues.text(values, "B", "KOD");
        StringBuilder where = new StringBuilder();
        if (kod != null) {
            where.append(" AND KOD = :kod");
        }
        String sql = "SELECT A FROM T WHERE 1 = 1" + where;
        Query q = entityManager.createNativeQuery(sql);
        q.setParameter("kod", kod);
        return q.getResultList();''', ['where.append(" AND KOD = :kod");\n            sqlParams.put("kod", SqlValues.bind(kod));',
                                       'sqlParams.forEach(q::setParameter);'], ['q.setParameter("kod"']),
    "conditions_joined_from_a_list": ("", '''
        String kod = PlsqlValues.text(values, "B", "KOD");
        List<String> conditions = new ArrayList<>();
        if (kod != null) {
            conditions.add("KOD = :kod");
        }
        String sql = "SELECT A FROM T" + (conditions.isEmpty() ? "" : " WHERE " + String.join(" AND ", conditions));
        return entityManager.createNativeQuery(sql).setParameter("kod", kod).getResultList();''',
                                      ['conditions.add("KOD = :kod");\n            sqlParams.put("kod", SqlValues.bind(kod));',
                                       'return sqlQuery.getResultList();'], ['.setParameter("kod"']),
    "else_if_branches_with_one_name": ("", '''
        String kod = PlsqlValues.text(values, "B", "KOD");
        StringBuilder sql = new StringBuilder("SELECT A FROM T WHERE 1 = 1");
        if (kod != null && kod.contains("%"))
            sql.append(" AND KOD LIKE :kod");
        else if (kod != null)
            sql.append(" AND KOD = :kod");
        Query q = entityManager.createNativeQuery(sql.toString());
        q.setParameter("kod", kod);
        return q.getResultList();''', ['    } else if (kod != null) {\n            sql.append(" AND KOD = :kod");\n'
                                       '            sqlParams.put("kod", SqlValues.bind(kod));\n        }'], ['q.setParameter("kod"']),
    "piece_in_a_loop": ("", '''
        String kod = PlsqlValues.text(values, "B", "KOD");
        StringBuilder sql = new StringBuilder("SELECT A FROM T WHERE 1 = 0");
        for (String col : columns) {
            sql.append(" OR " + col + " = :kod");
        }
        Query q = entityManager.createNativeQuery(sql.toString());
        q.setParameter("kod", kod);
        return q.getResultList();''', ['sql.append(" OR " + col + " = :kod");\n            sqlParams.put("kod", SqlValues.bind(kod));'],
                        ['q.setParameter("kod"']),
    "two_chained_binds_both_conditional": ("", '''
        String a = PlsqlValues.text(values, "B", "A");
        String b = PlsqlValues.text(values, "B", "B");
        StringBuilder sql = new StringBuilder("SELECT A FROM T WHERE 1 = 1");
        if (a != null) sql.append(" AND A = :a");
        if (b != null) sql.append(" AND B = :b");
        Query q = entityManager.createNativeQuery(sql.toString());
        q.setParameter("a", a).setParameter("b", b);
        return q.getResultList();''', ['sqlParams.put("a", SqlValues.bind(a));', 'sqlParams.put("b", SqlValues.bind(b));'],
                                           ["setParameter(\"a\"", "setParameter(\"b\""]),
    "inside_the_log_wrapper": ("", '''
        return log1x("m", () -> {
            String kod = PlsqlValues.text(request.blocks(), "B", "KOD");
            StringBuilder sql = new StringBuilder("SELECT A FROM T WHERE 1 = 1");
            if (kod != null) {
                sql.append(" AND KOD = :kod");
            }
            sql.append(" AND IGKOD = :igkod");
            List<Object[]> rows = entityManager.createNativeQuery(sql.toString()).setParameter("kod", kod).getResultList();
            return rows;
        });''', ['sqlQuery.setParameter("igkod", formsCommonsRepository.getIgKod(user)).getResultList();',
                 'sqlParams.put("kod", SqlValues.bind(kod));'], ['.setParameter("kod"']),
    "update_with_an_optional_column": ("OrdersRow row", '''
        StringBuilder sql = new StringBuilder("UPDATE APP_ORDERS SET CODE = :code");
        if (row.name != null) {
            sql.append(", NAME = :name");
        }
        sql.append(" WHERE ID = :id");
        Query q = entityManager.createNativeQuery(sql.toString());
        q.setParameter("code", row.code);
        q.setParameter("name", row.name);
        q.setParameter("id", row.id);
        return q.executeUpdate();''', ['sql.append(", NAME = :name");\n            sqlParams.put("name", SqlValues.bind(row.name));',
                                       'q.setParameter("code", row.code);', 'q.setParameter("id", row.id);'],
                                       ['q.setParameter("name"']),
    "written_sql_missing_its_bind": ("", '''
        String kod = PlsqlValues.text(values, "B", "KOD");
        return entityManager.createNativeQuery("SELECT A FROM T WHERE KOD = :kod").getResultList();''',
                                     ['createNativeQuery("SELECT A FROM T WHERE KOD = :kod").setParameter("kod", kod)'], []),
    "names_built_at_run_time": ("", '''
        StringBuilder sql = new StringBuilder("SELECT A FROM T WHERE 1 = 0");
        for (int i = 0; i < ids.size(); i++) {
            sql.append(" OR ID = :p" + i);
        }
        Query q = entityManager.createNativeQuery(sql.toString());
        for (int i = 0; i < ids.size(); i++) {
            q.setParameter("p" + i, ids.get(i));
        }
        return q.getResultList();''', None, []),
    "if_else_binds_with_their_own_values": ("", '''
        String kod = PlsqlValues.text(values, "B", "KOD");
        Query q = entityManager.createNativeQuery("SELECT A FROM T WHERE KOD LIKE :kod");
        if (kod == null) {
            q.setParameter("kod", "%");
        } else {
            q.setParameter("kod", kod);
        }
        return q.getResultList();''', None, []),
}


def repaired(name):
    parameters, body, _, _ = CASES[name]
    code = "public Object " + name + "(" + ", ".join(p for p in ("ActionRequest request", parameters) if p) + ") {" + body + "\n}"
    notes, warnings = [], []
    return code, sql_binding.bind(code, CTX, notes, warnings), notes, warnings


class VariantTests(unittest.TestCase):
    def test_every_variant_is_bound_where_its_pieces_are(self):
        for name, (_, _, present, absent) in CASES.items():
            with self.subTest(name):
                code, fixed, notes, warnings = repaired(name)
                if present is None:
                    self.assertEqual(fixed, code)  # right as written, or not followed: left alone
                    self.assertEqual(notes, [])
                    continue
                for text in present:
                    self.assertIn(text, fixed)
                for text in absent:
                    self.assertNotIn(text, fixed)
                self.assertTrue(notes)
                self.assertEqual(warnings, [])

    def test_a_second_pass_changes_nothing(self):
        for name in CASES:
            with self.subTest(name):
                _, fixed, _, _ = repaired(name)
                notes, warnings = [], []
                self.assertEqual(sql_binding.bind(fixed, CTX, notes, warnings), fixed)
                self.assertEqual(notes, [])

    def test_every_repaired_variant_compiles_with_java_11(self):
        if not shutil.which("javac"):
            self.skipTest("javac is not available")
        methods = "\n".join(repaired(name)[1] for name in CASES)
        source = JAVA.replace("// METHODS", methods)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "Variants.java"
            path.write_text(source, encoding="utf-8")
            build = subprocess.run(["javac", "--release", "11", "-encoding", "UTF-8", "-nowarn", "-d", temp, str(path)],
                                   capture_output=True, text=True, timeout=120)
        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)

    def test_a_value_out_of_reach_is_a_warning_and_the_code_stays(self):
        code = '''public Object m(ActionRequest request) {
    StringBuilder sql = new StringBuilder("SELECT A FROM T WHERE 1 = 1");
    if (request.blocks() != null) {
        sql.append(" AND KOD = :kod");
    }
    String kod = PlsqlValues.text(values, "B", "KOD");
    Query q = entityManager.createNativeQuery(sql.toString());
    q.setParameter("kod", kod);
    return q.getResultList();
}'''
        notes, warnings = [], []
        self.assertEqual(sql_binding.bind(code, CTX, notes, warnings), code)
        self.assertTrue(any(":kod" in w for w in warnings), warnings)

    def test_a_piece_chosen_by_a_condition_of_its_own_is_not_followed(self):
        code = '''public Object m(ActionRequest request) {
    String kod = PlsqlValues.text(values, "B", "KOD");
    StringBuilder where = new StringBuilder();
    if (kod != null) {
        where.append(" AND KOD = :kod");
    }
    String sql = "SELECT A FROM T WHERE 1 = 1" + (flag ? where.toString() : "");
    Query q = entityManager.createNativeQuery(sql);
    q.setParameter("kod", kod);
    return q.getResultList();
}'''
        notes, warnings = [], []
        self.assertEqual(sql_binding.bind(code, CTX, notes, warnings), code)
        self.assertTrue(any(":kod" in w for w in warnings), warnings)


class ProblemTests(unittest.TestCase):
    def test_a_name_nothing_can_give_a_value_is_asked_from_the_model(self):
        code = '''public Object m(ActionRequest request) {
    StringBuilder sql = new StringBuilder("SELECT A FROM T WHERE 1 = 1");
    sql.append(" AND NEV = :nev");
    return entityManager.createNativeQuery(sql.toString()).getResultList();
}'''
        notes, warnings = [], []
        self.assertEqual(sql_binding.bind(code, CTX, notes, warnings), code)
        found = sql_binding.problems(code, CTX)
        self.assertEqual(len(found), 1)
        self.assertIn(":nev", found[0][0])
        self.assertIn("variable named nev", found[0][1])

    def test_written_sql_and_names_built_at_run_time_are_not_reported_here(self):
        written = 'var r = entityManager.createNativeQuery("SELECT 1 FROM T WHERE X = :x").getResultList();'
        self.assertEqual(sql_binding.problems(written, CTX), [])  # answer_checks names its parameters
        _, fixed, _, _ = repaired("names_built_at_run_time")
        self.assertEqual(sql_binding.problems(fixed, CTX), [])


class UnusedTests(unittest.TestCase):
    def test_a_screen_value_nothing_reads_goes_and_one_named_like_a_parameter_stays(self):
        code = '''public Object m(ActionRequest request) {
    String kod = PlsqlValues.text(values, "B", "KOD");
    String unusedOne = PlsqlValues.text(values, "B", "X");
    String nev = PlsqlValues.text(values, "B", "NEV");
    String called = service.load(kod);
    return entityManager.createNativeQuery("SELECT A FROM T WHERE KOD = :kod AND NEV = :nev").setParameter("kod", kod)
            .getResultList();
}'''
        notes = []
        fixed = sql_binding.unused(code, notes, CTX)
        self.assertNotIn("unusedOne", fixed)
        self.assertIn('String nev = PlsqlValues.text(values, "B", "NEV");', fixed)  # its bind is missing, not the variable
        self.assertIn("String called = service.load(kod);", fixed)  # a call may do something: stays
        self.assertIn("unusedOne", notes[0])


# The search button of the test form, written the way the model writes a DEFAULT_WHERE: the filter's piece under an
# if, its value bound always; :limit's variable made but not bound.
SEARCH = """```java
// SERVICE
public PageResult<OrdersRow> onCtrlSearchBtn(UserDto user, QueryActionRequest request) throws Exception {
    return log1x(log, OrdersConstants.ON_CTRL_SEARCH_BTN_NAME, user, null, () -> {
        var values = request.blocks() == null ? Map.<String, Map<String, String>>of() : request.blocks();
        String filter = PlsqlValues.text(values, "CTRL", "FILTER");
        int limit = request.limit() + 1;
        var context = new RuleContext();
        StringBuilder sql = new StringBuilder("SELECT ID, CODE, NAME FROM APP_ORDERS WHERE 1 = 1");
        if (filter != null) {
            sql.append(" AND code like :filter || '%'");
        }
        sql.append(" ORDER BY ID OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY");
        List<Object[]> result = entityManager.createNativeQuery(sql.toString()).setParameter("filter", filter)
                .setParameter("offset", request.offset()).getResultList();
        var rows = new ArrayList<OrdersRow>();
        for (Object[] r : result) {
            if (rows.size() == request.limit()) {
                break;
            }
""" + ai.ROW + """
            triggerPostQueryOrders(row, context);
            rows.add(row);
        }
        return new PageResult<>(rows, context.messages(), result.size() > request.limit());
    });
}
```"""


class QueryButtonTests(unittest.TestCase):
    def test_the_query_built_from_pieces_is_bound_in_place_without_asking_the_model_again(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = ai.migrate(root, root / "module")
            task = next(t for t in engine.prepare_workspace(output)["tasks"] if t["method"] == "onCtrlSearchBtn")
            client = ai.MethodClient({**ai.ANSWERS, "onCtrlSearchBtn": SEARCH})
            with patch("frm_forms.ollama.opener", return_value=client):
                result = engine.propose(output, task["id"], engine.Options())
            self.assertEqual(result["status"], "proposal", result["extraction"])
            self.assertEqual(len(client.requests), 1)  # repaired here, not by a second model call
            notes = " ".join(result["extraction"]["notes"])
            self.assertIn(":filter", notes)
            self.assertIn(":limit", notes)
            engine.apply_proposal(output, result["id"], reviewed=True)
            service = (output / ai.SERVICE).read_text(encoding="utf-8")
            method = ai.method_text(service, "onCtrlSearchBtn")
            body = re.sub(r"\s+", " ", method)
            self.assertIn('if (filter != null) { sql.append(" AND code like :filter || \'%\'"); '
                          'sqlParams.put("filter", SqlValues.bind(filter)); }', body)
            self.assertIn("sqlParams.forEach(sqlQuery::setParameter);", body)
            self.assertIn('.setParameter("limit", limit)', body)
            self.assertNotIn('.setParameter("filter"', body)
            self.assertIn("//region Eredeti Forms-kód", method)  # the regions stay
            if shutil.which("java"):
                ai.compile_module(self, output, Path(tempfile.mkdtemp(dir=root)))


JAVA = """import java.util.*;
import java.util.function.Supplier;

public class Variants {
    interface Parameter<T> {}
    interface Query {
        // the two-argument overloads of javax.persistence.Query: q::setParameter must still pick (String, Object)
        Query setParameter(String name, Object value);
        Query setParameter(int position, Object value);
        <T> Query setParameter(Parameter<T> parameter, T value);
        List getResultList();
        Object getSingleResult();
        int executeUpdate();
    }
    interface EntityManager { Query createNativeQuery(String sql); }
    interface Repository { String getIgKod(Object user); }
    static final class PlsqlValues {
        static String text(Map<String, Map<String, String>> values, String block, String item) { return null; }
    }
    static final class SqlValues {
        static Object bind(Object value) { return value == null ? "" : value; }
    }
    static final class OrdersRow {
        String code;
        String name;
        Object id;
    }
    static final class ActionRequest {
        Map<String, Map<String, String>> blocks() { return null; }
    }
    EntityManager entityManager;
    Repository formsCommonsRepository;
    Object user;
    Map<String, Map<String, String>> values;
    List<String> columns;
    List<Object> ids;
    boolean flag;

    Object log1x(String name, Supplier<Object> body) { return body.get(); }

// METHODS
}
"""


if __name__ == "__main__":
    unittest.main()

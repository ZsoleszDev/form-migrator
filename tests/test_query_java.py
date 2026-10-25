"""4.23: query buttons as plain Java - only the SQL request, the Forms plumbing left out, the original code in a region.

The anonymised query button of the real form: LEKERDEZESI_FELTETELEK builds the WHERE from the form's inputs (T1 here,
XY_LAP there) with seven flag IFs and commented-out old variants, WUZENET shows an alert, and the form has a form-level
POST-QUERY with a DECLARE section the migrator cannot translate. The button runs in Java: the trigger's and the
builder's IFs are Java ifs, the SQL pieces get their quotes at generation time, :T1.X references are JDBC binds.
"""
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from java_support import COMPANY_IMPORTS, write_stubs
from frm_forms.cli import main
import test_query_actions as qa

BUILDER = """PROCEDURE lekerdezesi_feltetelek IS
 lek_sql varchar2(1000);
BEGIN

 lek_sql:='COL6=;00; and ((:T1.COL1 = ;XY_42_LAP; and COL7 in (;XY_34;,;XY_340;,;XY_341;)) or
            (:T1.COL1 = ;Y901; and COL7 = ;06109;)) and
             COL8 = :T1.col5';
"""
for flags, add in [((1, 0, 0), "' and COL9=;B56;'"), ((0, 1, 0), "' and COL9=;K70;'"), ((1, 1, 1), "' and (COL9=;B56; or COL9=;K70;)'")]:
    BUILDER += (" IF :T1.COL2=%d AND\n    :T1.COL3=%d AND\n    :T1.COL4=%d THEN\n    lek_sql:= lek_sql || %s;\n"
                "    --lek_sql:='COL6=;00; and nvl(COL8,;%%;) like decode(:T1.col5,;;,;%%;,:T1.col5)';\n END IF;\n" % (*flags, add))
BUILDER += """ select replace(lek_sql,';',chr(39)) into lek_sql from dual;
 Set_Block_Property('BLK',DEFAULT_WHERE,lek_sql);
 go_block('BLK');
 execute_query();
END;"""

WUZENET = """PROCEDURE wuzenet(vv_uzenet varchar2) IS
  m_alertdialog  CONSTANT VARCHAR2(15) := 'QMS$INFORMATION';
  m_alertid      ALERT;
  m_alertbutton  NUMBER;
BEGIN
      m_alertid := FIND_ALERT ( m_alertdialog );
      SET_ALERT_PROPERTY( m_alertid, ALERT_MESSAGE_TEXT, vv_uzenet);
      m_alertbutton := SHOW_ALERT( m_alertid );
END;"""

FORM_POST_QUERY = "DECLARE\n  vn_hossz NUMBER;\nBEGIN\n  vn_hossz := LENGTH(:SYSTEM.TRIGGER_BLOCK);\n  NULL;\nEND;"


def form(builder=BUILDER, button=qa.BUTTON) -> bytes:
    root = ET.fromstring(qa.fixture(builder, button))
    module = root.find('FormModule')
    ET.SubElement(module, 'ProgramUnit', Name='WUZENET', ProgramUnitType='Procedure', ProgramUnitText=WUZENET)
    module.insert(2, ET.Element('Trigger', Name='POST-QUERY', TriggerText=FORM_POST_QUERY))
    return ET.tostring(root)


def generate(root: Path, xml: bytes, label='out') -> Path:
    (root / (label + '.xml')).write_bytes(xml)
    (root / 'config.json').write_text(json.dumps({'backend_live': True, 'java_company_imports': COMPANY_IMPORTS,
                                                  'java_import_map': '-'}))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert main(['migrate', str(root / (label + '.xml')), '--module', 'query', '--screen', '--config',
                     str(root / 'config.json'), '--out', str(root / label)]) == 0
    return root / label


def endpoint(out: Path, owner='T1.PB_LEKERDEZES') -> dict:
    plan = json.loads((out / 'analysis/backend-plan.json').read_text(encoding='utf-8'))
    return next(e for e in plan['endpoints'] if e.get('owner') == owner)


class JavaQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.out = generate(cls.root, form())
        service = (cls.out / 'backend/DPS/QueryServiceImpl.java').read_text(encoding='utf-8')
        start = service.index('public PageResult<BlkRow> onT1PbLekerdezes(')
        cls.method = service[service.rindex('\n\n', 0, start):service.index('\n    }\n', start)]
        start = service.index('static QueryText onT1PbLekerdezesQuery(')
        cls.query = service[start:service.index('\n    }\n', start)]

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_the_button_runs_and_the_form_level_post_query_runs_on_its_rows(self):
        action = endpoint(self.out)
        self.assertEqual((action['runs'], action['implemented'], action['blockers']), ('java-query', True, []))
        self.assertNotIn('todo', action)  # 4.24: the form-level POST-QUERY runs for the block (Execution Hierarchy)
        self.assertNotIn('// TODO: a Formsban ez is fut', self.method)
        self.assertNotIn('NOT_IMPLEMENTED, "A lekérdezés további átültetést igényel', self.method)

    def test_only_the_sql_request_in_readable_java(self):
        for line in ('String col1 = PlsqlValues.text(values, "T1", "COL1");',
                     'BigDecimal col2 = PlsqlValues.number(values, "T1", "COL2");',
                     'var q = onT1PbLekerdezesQuery(col1, col5, col2, col3, col4);',
                     'if (!q.run) {\n                return new PageResult<>(null, q.messages);',
                     'var rows = jdbc.query("SELECT COL6, COL7, COL8, COL9 FROM T_BLK WHERE " + whereText(q.where) + " ORDER BY COL6"',
                     'postQueryBlk(row, context);'):
            self.assertIn(line, self.method)
        for line in ('static QueryText onT1PbLekerdezesQuery(String col1, String col5, BigDecimal col2, BigDecimal col3, BigDecimal col4) {',
                     'if (col1 != null) {',
                     """lekSql = "COL6='00' and ((:col1 = 'XY_42_LAP' and COL7 in ('XY_34','XY_340','XY_341')) or\\n\"""",
                     'if (BigDecimal.ONE.equals(col2) && BigDecimal.ZERO.equals(col3) && BigDecimal.ZERO.equals(col4)) {',
                     """lekSql += " and COL9='B56'";""",
                     'q.where = lekSql;\n            q.run = true;',
                     'q.params.addValue("col1", col1);', 'q.params.addValue("col5", col5);',
                     '} else {\n            q.messages.add("Adatlap kiválasztása nem történt meg!");'):
            self.assertIn(line, self.query)
        for forms in ('DECLARE', 'DbCalls.call', 'FRM_QUERY_CONTEXT', 'whereText.equals'):
            self.assertNotIn(forms, self.method.split('//endregion')[1] + self.query)  # no PL/SQL emulation in the code
        self.assertIn('// Kimaradt Forms-hívások (a webes lekérdezésnek nem kellenek): GO_BLOCK.', self.method)
        self.assertIn('// Egyezés-ellenőrzés: ', self.method)

    def test_the_original_code_is_in_a_foldable_region(self):
        region = self.method[self.method.index('        //region '):self.method.index('        //endregion') + 20]
        self.assertTrue(region.startswith('        //region Eredeti Forms-kód: T1.PB_LEKERDEZES, LEKERDEZESI_FELTETELEK, WUZENET\n'))
        for original in ("//  Set_Block_Property('BLK',DEFAULT_WHERE,lek_sql);", "//     --lek_sql:='COL6=;00; and nvl(COL8",
                         '// WUZENET (helyi eljárás, üzenetként jelenik meg):', '//       m_alertid := FIND_ALERT ( m_alertdialog );'):
            self.assertIn(original, region)

    def test_the_generated_query_runs(self):
        if not shutil.which('java'):
            self.skipTest('Java 11+ required')
        smoke = self.root / 'JavaQuerySmoke.java'
        smoke.write_text(SMOKE, encoding='utf-8')
        sources = list(self.out.glob('backend/**/*.java')) + write_stubs(self.root / 'stubs') + [smoke]
        (self.root / 'sources.args').write_text('\n'.join('"' + str(p).replace(chr(92), '/') + '"' for p in sources))
        build = subprocess.run(['java', 'com.sun.tools.javac.Main', '--release', '11', '-encoding', 'UTF-8', '-d',
                                str(self.root / 'classes'), '@' + str(self.root / 'sources.args')], capture_output=True, text=True, timeout=300)
        if 'Could not find or load main class' in build.stderr:
            self.skipTest('JDK (javac) required')
        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
        run = subprocess.run(['java', '-cp', str(self.root / 'classes'), 'JavaQuerySmoke'], capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('java-query OK', run.stdout)


class JavaQueryEdgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_a_value_with_no_source_in_the_where_is_a_developer_input(self):
        out = generate(self.root, form(BUILDER.replace('COL8 = :T1.col5', 'COL8 = :NINCS.MEZO')))
        service = (out / 'backend/DPS/QueryServiceImpl.java').read_text(encoding='utf-8')
        self.assertIn('// TODO: :NINCS.MEZO (ismeretlen mező, nincs a formban): add át ennek a változónak a megfelelő értéket.\n'
                      '            String nincsMezo = null;', service)
        self.assertIn('COL8 = :nincsMezo', service)
        self.assertIn('.addValue("nincsMezo", nincsMezo)', service)

    def test_the_query_may_run_in_the_trigger_after_the_builder(self):
        builder = BUILDER.replace(" go_block('BLK');\n execute_query();\n", '')
        button = qa.BUTTON.replace('    lekerdezesi_feltetelek;', "    lekerdezesi_feltetelek;\n    go_block('BLK');\n    execute_query;")
        out = generate(self.root, form(builder, button))
        self.assertEqual(endpoint(out)['runs'], 'java-query')

    def test_a_condition_java_cannot_express_falls_back_to_the_plsql_adapter(self):
        button = qa.BUTTON.replace('If :T1.COL1 is not null then', 'If substr(:T1.COL1, 1, 2) = :T1.COL5 then')
        out = generate(self.root, form(button=button))
        model = json.loads((out / 'analysis/form.ir.json').read_text(encoding='utf-8'))
        trigger = next(t for t in model['triggers'] if t['event'] == 'WHEN-BUTTON-PRESSED')
        self.assertIn('nem fordítható egyszerű Java-kifejezésre: SUBSTR', trigger['query_java_reason'])
        self.assertNotEqual(endpoint(out)['runs'], 'java-query')

    def test_a_manual_button_keeps_its_original_code_in_a_region(self):
        root = ET.fromstring(form())
        controls = next(b for b in root.find('FormModule').findall('Block') if b.get('Name') == 'T1')
        item = ET.SubElement(controls, 'Item', Name='PB_KEZI', ItemType='Push Button', DatabaseItem='false', CanvasName='C',
                             XPosition='240', YPosition='60', Width='100', Height='30', Label='Kézi')
        ET.SubElement(item, 'Trigger', Name='WHEN-BUTTON-PRESSED', TriggerText="HOST('dir');")
        out = generate(self.root, ET.tostring(root))
        service = (out / 'backend/DPS/QueryServiceImpl.java').read_text(encoding='utf-8')
        method = service[service.index('public ActionResult onT1PbKezi('):]
        method = method[:method.index('\n    }\n')]
        self.assertIn('            //region Eredeti Forms-kód kiindulásnak (nem fut; a trigger és az általa hívott helyi eljárások)\n', method)
        self.assertIn('            //endregion\n', method)


SMOKE = r'''import java.lang.reflect.Proxy;
import java.sql.CallableStatement;
import java.sql.ResultSet;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import hu.company.features.query.dps.QueryServiceImpl;
import hu.company.features.query.cl.QueryDtos.QueryActionRequest;
import hu.company.common.UserDto;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.CallableStatementCallback;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.jdbc.core.namedparam.SqlParameterSource;

public class JavaQuerySmoke {
    static class CaptureJdbc extends NamedParameterJdbcTemplate {
        String sql;
        SqlParameterSource params;
        int queries;

        @Override
        public JdbcTemplate getJdbcTemplate() {  // the block's own POST-QUERY (PL/SQL in the database)
            return new JdbcTemplate() {
                @Override
                public <T> T execute(String sql, CallableStatementCallback<T> callback) {
                    var statement = (CallableStatement) Proxy.newProxyInstance(
                        getClass().getClassLoader(), new Class<?>[] {CallableStatement.class}, (proxy, method, args) -> {
                            switch (method.getName()) {
                                case "setObject": case "setNull": case "registerOutParameter": return null;
                                case "getString": return Integer.valueOf(2).equals(args[0]) ? "Betöltve" : null;
                                case "execute": return true;
                                default: throw new AssertionError(method.getName());
                            }
                        });
                    try {
                        return callback.doInCallableStatement(statement);
                    } catch (java.sql.SQLException e) {
                        throw new AssertionError(e);
                    }
                }
            };
        }

        @Override
        public <T> List<T> query(String sql, SqlParameterSource params, RowMapper<T> mapper) {
            queries++;
            this.sql = sql;
            this.params = params;
            var row = (ResultSet) Proxy.newProxyInstance(getClass().getClassLoader(), new Class<?>[] {ResultSet.class},
                (proxy, method, args) -> new String[] {"00", "XY_34", "U1", "B56"}[(Integer) args[0] - 1]);
            try {
                return List.of(mapper.mapRow(row, 0));
            } catch (java.sql.SQLException e) {
                throw new AssertionError(e);
            }
        }
    }

    static void check(boolean ok, String what) {
        if (!ok) throw new AssertionError(what);
    }

    public static void main(String[] args) throws Exception {
        var jdbc = new CaptureJdbc();
        var service = new QueryServiceImpl(jdbc);
        var flags = Map.of("COL1", "XY_42_LAP", "COL2", "1", "COL3", "0", "COL4", "0", "COL5", "U1");
        var result = service.onT1PbLekerdezes(new UserDto(), new QueryActionRequest(Map.of("T1", flags), Map.of(), 0, 200));
        check(jdbc.sql.contains("WHERE (COL6='00' and ((:col1 = 'XY_42_LAP'") && jdbc.sql.endsWith(
            "COL8 = :col5 and COL9='B56') ORDER BY COL6 OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY"), jdbc.sql);
        check("XY_42_LAP".equals(jdbc.params.getValue("col1")) && "U1".equals(jdbc.params.getValue("col5"))
              && Integer.valueOf(200).equals(jdbc.params.getValue("limit")), "binds");
        check(result.rows().size() == 1 && "XY_34".equals(result.rows().get(0).col7) && "Betöltve".equals(result.rows().get(0).info), "rows");
        var all = Map.of("COL1", "Y901", "COL2", "1", "COL3", "1", "COL4", "1", "COL5", "U1");
        service.onT1PbLekerdezes(new UserDto(), new QueryActionRequest(Map.of("T1", all), Map.of(), 0, 200));
        check(jdbc.sql.contains(":col5 and (COL9='B56' or COL9='K70')) ORDER BY"), jdbc.sql);
        result = service.onT1PbLekerdezes(new UserDto(), new QueryActionRequest(Map.of("T1", Map.of("COL2", "1")), Map.of(), 0, 200));
        check(result.rows() == null && result.messages().equals(List.of("Adatlap kiválasztása nem történt meg!")) && jdbc.queries == 2,
              "no selection: the message, no query, the rows on the screen stay");
        System.out.println("java-query OK");
    }
}
'''


if __name__ == '__main__':
    unittest.main()

"""The Angular adapter runs Forms mode-dependent logic with a fixed NORMAL mode."""
import contextlib
import io
import json
import re
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from java_support import COMPANY_IMPORTS, write_stubs
from frm_forms.backend_lovs import rewrite
from frm_forms.cli import main
from frm_forms.plsql import Unsupported, parse
from frm_forms.plsql_passthrough import normal_mode_sql, prepare
from frm_forms.rules import Compiler


def fixture():
    root = ET.Element('Module')
    form = ET.SubElement(root, 'FormModule', Name='F')
    ET.SubElement(form, 'Coordinate', CoordinateSystem='Real', RealUnit='Pixel')
    ET.SubElement(form, 'Window', Name='W')
    ET.SubElement(form, 'Canvas', Name='C', CanvasType='Content', WindowName='W', Width='400', Height='200')
    block = ET.SubElement(form, 'Block', Name='B', DatabaseDataBlock='true', QueryDataSourceName='T_B',
                          WhereClause=":SYSTEM.MODE = 'NORMAL'")
    ET.SubElement(block, 'Item', Name='ID', ItemType='Text Item', DataType='Number', PrimaryKey='true')
    ET.SubElement(block, 'Item', Name='KOD', ItemType='Text Item', DataType='Char', MaximumLength='20',
                  CanvasName='C', XPosition='10', YPosition='10', Width='100', Height='20', LOVName='INPTIP')
    ET.SubElement(block, 'Item', Name='INFO', ItemType='Text Item', DataType='Char', DatabaseItem='false')
    ET.SubElement(block, 'Trigger', Name='POST-QUERY', TriggerText=":B.INFO := NAME_IN('SYSTEM.MODE');")
    ET.SubElement(form, 'Trigger', Name='WHEN-NEW-FORM-INSTANCE', TriggerText=
                  "IF :SYSTEM.MODE = 'NORMAL' THEN set_item_property('B.KOD', ENABLED, PROPERTY_TRUE); "
                  "ELSE set_item_property('B.KOD', ENABLED, PROPERTY_FALSE); END IF;")
    lov = ET.SubElement(form, 'LOV', Name='INPTIP', RecordGroupName='RG')
    ET.SubElement(lov, 'LOVColumnMapping', ColumnName='KOD', DisplayWidth='100', ReturnItem='B.KOD')
    ET.SubElement(form, 'RecordGroup', Name='RG', RecordGroupQuery=
                  "SELECT KOD FROM T WHERE :SYSTEM.MODE = 'ENTER-QUERY' OR AKTIV = 'Y'")
    return ET.tostring(root)


class NormalModeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def generate(self, mode='plsql', live=True):
        source = self.root / (mode + '.xml')
        source.write_bytes(fixture())
        config = self.root / (mode + '.json')
        config.write_text(json.dumps({'backend_live': live, 'backend_trigger_mode': mode,
                                     'java_company_imports': COMPANY_IMPORTS, 'java_import_map': '-'}))
        output = self.root / mode
        errors = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
            result = main(['migrate', str(source), '--screen', '--module', 'normal',
                           '--config', str(config), '--out', str(output)])
        self.assertEqual(result, 0, errors.getvalue())
        return output

    def test_lov_results_use_normal_even_if_the_request_claims_query_mode(self):
        query = ("SELECT KOD FROM T WHERE (:SYSTEM.MODE = 'ENTER-QUERY' OR AKTIV = 'Y') "
                 "AND KOD = :B.KOD")
        sql, binds = rewrite(query, {'B.KOD': {'type': 'text'}}, 'KOD')
        self.assertEqual([b['source'] for b in binds], ['B.KOD'])
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE T (KOD TEXT, AKTIV TEXT)')
            db.executemany('INSERT INTO T VALUES (?, ?)', [('X', 'Y'), ('X', 'N')])
            # Only the Oracle-specific row-limit clause is omitted for this predicate check.
            rows = db.execute(sql.split('\n FETCH FIRST')[0],
                              {'p0': 'X', 'term': None, 'SYSTEM.MODE': 'ENTER-QUERY'}).fetchall()
        self.assertEqual(rows, [('X',)])

    def test_literals_comments_and_other_names_are_preserved(self):
        source = ("SELECT ':SYSTEM.MODE', q'[:SYSTEM.MODE]', N':SYSTEM.MODE' FROM DUAL "
                  "-- :SYSTEM.MODE\nWHERE :system.mode = NAME_IN('System.Mode') /* :SYSTEM.MODE */")
        self.assertEqual(normal_mode_sql(source),
                         source.replace(':system.mode', "'NORMAL'").replace("NAME_IN('System.Mode')", "'NORMAL'"))
        self.assertEqual(normal_mode_sql(':SYSTEM.MODE_EXTRA'), ':SYSTEM.MODE_EXTRA')
        self.assertEqual(normal_mode_sql("pkg.NAME_IN('SYSTEM.MODE')"), "pkg.NAME_IN('SYSTEM.MODE')")
        self.assertEqual(normal_mode_sql("NAME_IN('SYSTEM.MODE')", {'NAME_IN'}), "NAME_IN('SYSTEM.MODE')")
        self.assertEqual(normal_mode_sql("nv_0000000000 := :SYSTEM.MODE;"), "nv_0000000000 := 'NORMAL';")
        commented = normal_mode_sql("SELECT NAME_IN(-- mode\n'SYSTEM.MODE') FROM DUAL")
        self.assertIn("'NORMAL' -- mode\n", commented)
        self.assertIn('FROM DUAL', commented)

    def test_other_context_binds_are_still_blocked(self):
        for reference in ('SYSTEM.RECORD_STATUS', 'SYSTEM.MODE_EXTRA', 'GLOBAL.CEG', 'PARAMETER.P'):
            with self.subTest(reference=reference), self.assertRaisesRegex(Unsupported, reference):
                rewrite('SELECT KOD FROM T WHERE X = :' + reference, {}, '')

    def test_plsql_and_local_program_units_read_normal_without_context_parameters(self):
        units = {'GET_MODE': {'kind': 'function', 'text':
                            "FUNCTION get_mode RETURN VARCHAR2 IS BEGIN RETURN NAME_IN('SYSTEM.MODE'); END;"}}
        prepared = prepare(":B.INFO := get_mode; IF :system.mode = 'NORMAL' THEN :B.INFO := 'OK'; END IF;",
                           block='B', items={'B': {'INFO': {'type': 'text'}}}, units=units, prefixes=())
        self.assertIn("RETURN 'NORMAL';", prepared['sql'])
        self.assertIn("IF 'NORMAL' = 'NORMAL' THEN", prepared['sql'])
        self.assertEqual([b['source'] for b in prepared['binds']], ['B.INFO'])
        self.assertEqual(prepared['assigned'], ['B.INFO'])

    def test_mode_remains_read_only(self):
        for source in (":SYSTEM.MODE := 'QUERY';", 'SELECT X INTO :SYSTEM.MODE FROM T;',
                       'FETCH c INTO :SYSTEM.MODE;'):
            with self.subTest(source=source), self.assertRaisesRegex(Unsupported, 'csak olvasható'):
                normal_mode_sql(source)

    def test_java_conditions_use_the_normal_mode(self):
        compiler = Compiler({'name': 'B', 'items': []})
        for reference in (':system.mode', "NAME_IN('System.Mode')"):
            node = parse('IF ' + reference + " = 'NORMAL' THEN NULL; END IF;")[0]['branches'][0]['condition']
            java, kind = compiler.expression(node)
            self.assertEqual(kind, 'boolean')
            self.assertIn('SqlValues.compare("NORMAL", "NORMAL", "=")', java)

    def test_complete_generation_has_a_live_lov_and_compiled_block_query(self):
        for mode in ('plsql', 'java'):
            with self.subTest(mode=mode):
                out = self.generate(mode)
                plan = json.loads((out / 'analysis/backend-plan.json').read_text())
                lov = next(e for e in plan['endpoints'] if e['method'] == 'lovInptip')
                self.assertTrue(lov['implemented'])
                self.assertEqual(lov['blockers'], [])
                model = json.loads((out / 'analysis/form.ir.json').read_text())
                query = model['blocks'][0]['query_plan']
                self.assertEqual(query['status'], 'compiled')
                self.assertEqual(query['binds'], [])
                self.assertIn("'NORMAL'", query['where_sql'])
                service = (out / 'backend/DPS/NormalServiceImpl.java').read_text()
                method = service[service.index('public LovResult lovInptip('):]
                self.assertIn('static final boolean MODULE_REVIEWED = true;', service)
                self.assertNotIn('if (!false)', method)
                self.assertNotIn('SELECT NULL FROM DUAL WHERE 1 = 0', method)
                self.assertIn("'NORMAL' = 'ENTER-QUERY'", re.sub(r'"\s*\+\s*"', '', method))  # SQL wrapped below 120 characters
                self.assertTrue(all(t['status'] == 'converted' for t in model['triggers'] if t['event'] == 'POST-QUERY'))
                # Evidence retains the original query; it is not overwritten with the adaptation.
                self.assertIn(':SYSTEM.MODE', (out / 'analysis/backend-evidence.md').read_text())

    def test_review_switch_still_controls_an_otherwise_ready_lov(self):
        out = self.generate(live=False)
        plan = json.loads((out / 'analysis/backend-plan.json').read_text())
        lov = next(e for e in plan['endpoints'] if e['method'] == 'lovInptip')
        self.assertFalse(lov['implemented'])
        self.assertTrue(lov['ready_after_module_review'])
        self.assertEqual(lov['blockers'], [])

    def test_generated_java_lov_compiles_and_reaches_jdbc_without_a_mode_parameter(self):
        if not shutil.which('java'):
            self.skipTest('Java 11+ compiler required')
        out = self.generate()
        sources = list(out.glob('backend/**/*.java')) + write_stubs(self.root / 'stubs')
        smoke = self.root / 'NormalModeSmoke.java'
        smoke.write_text('''import java.util.List;
import java.util.Map;
import hu.company.features.normal.dps.NormalServiceImpl;
import hu.company.features.normal.cl.NormalDtos.LovRequest;
import hu.company.common.UserDto;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.jdbc.core.namedparam.SqlParameterSource;

public class NormalModeSmoke {
    static class CaptureJdbc extends NamedParameterJdbcTemplate {
        int calls;

        @Override
        public List<Map<String, Object>> queryForList(String sql, SqlParameterSource params) {
            if (!sql.contains("'NORMAL' = 'ENTER-QUERY'") || sql.contains(":SYSTEM.MODE")
                    || params.getValue("SYSTEM.MODE") != null) {
                throw new AssertionError("The LOV must use the fixed mode, not request data.");
            }
            calls++;
            return List.of(Map.of("KOD", "X"));
        }
    }

    public static void main(String[] args) throws Exception {
        CaptureJdbc jdbc = new CaptureJdbc();
        NormalServiceImpl service = new NormalServiceImpl(jdbc);
        var result = service.lovInptip(new UserDto(),
                new LovRequest(null, Map.of("SYSTEM.MODE", "ENTER-QUERY"), 10));
        if (jdbc.calls != 1 || !"X".equals(result.rows().get(0).get("KOD"))) {
            throw new AssertionError("The generated endpoint must return the JDBC result.");
        }
        System.out.println("normal mode runtime passed");
    }
}
''')
        sources.append(smoke)
        args = self.root / 'sources.args'
        args.write_text('\n'.join('"' + str(p).replace(chr(92), '/') + '"' for p in sources))
        result = subprocess.run(['java', 'com.sun.tools.javac.Main', '--release', '11', '-encoding', 'UTF-8',
                                 '-d', str(self.root / 'classes'), '@' + str(args)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        run = subprocess.run(['java', '-cp', str(self.root / 'classes'), 'NormalModeSmoke'],
                             capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('normal mode runtime passed', run.stdout)


if __name__ == '__main__':
    unittest.main()

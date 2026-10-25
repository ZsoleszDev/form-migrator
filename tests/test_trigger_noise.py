"""Self-contained regressions for action noise and executable ServiceImpl SQL."""
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
from frm_forms.framework import classify, load
from frm_forms.plsql import Unsupported
from frm_forms.plsql_passthrough import prepare, Rewriter
from screen_support import screen_source


ACTION_SQL = """BEGIN
  qms$calendar.ok;
UPDATE T_B SET D = :B.D WHERE ID = :B.ID;
SELECT q'[árvíz 😀 "quoted" \\path; -- literal
trailing spaces   ]' INTO :B.NOTE FROM DUAL;
local_audit;
COMMIT;
END;"""


def fixture(buttons=None, calendar_only=False):
    form = ET.Element('FormModule', Name='NOISE', Title='Noise')
    ET.SubElement(form, 'Canvas', Name='MAIN', CanvasType='Content')
    if not calendar_only:
        block = ET.SubElement(form, 'Block', Name='B', DatabaseDataBlock='true',
                              QueryDataSourceName='T_B')
        ET.SubElement(block, 'Item', Name='ID', DataType='Number', PrimaryKey='true', CanvasName='MAIN')
        date = ET.SubElement(block, 'Item', Name='D', ItemType='Text Item', DataType='Date', CanvasName='MAIN')
        ET.SubElement(date, 'Trigger', Name='WHEN-VALIDATE-ITEM',
                      TriggerText='BEGIN SELECT label INTO :B.NOTE FROM dates WHERE d = :B.D; END;')
        ET.SubElement(block, 'Item', Name='NOTE', DataType='Char', MaximumLength='100',
                      DatabaseItem='false', CanvasName='MAIN')
        ET.SubElement(block, 'Trigger', Name='POST-QUERY', TriggerText='BEGIN NULL; END;')
        ET.SubElement(form, 'ProgramUnit', Name='LOCAL_AUDIT', ProgramUnitType='Procedure',
                      ProgramUnitText='PROCEDURE local_audit IS BEGIN INSERT INTO audit_log VALUES (:B.ID); END; -- tail')
        ET.SubElement(form, 'ProgramUnit', Name='UNUSED_UI', ProgramUnitType='Procedure',
                      ProgramUnitText="PROCEDURE unused_ui IS BEGIN go_block('CALENDAR'); END;")
    calendar = ET.SubElement(form, 'Block', Name='CALENDAR', DatabaseDataBlock='false')
    entries = buttons if buttons is not None else {
        'CANCEL': "BEGIN qms$calendar.cancel; END;",
        'OK': "qms$calendar.ok;",
        'EMPTY': 'BEGIN BEGIN NULL; END; END;',
        'QUERY': "go_block('B'); execute_query;",
        'MESSAGE': "message('Text; -- not SQL, /* not comment */');",
        'SAVE_DATE': ACTION_SQL,
        'CUSTOM': "go_block('B'); pkg.changed(:B.D);",
        'NESTED': 'qms$calendar.ok(pkg.mutate(:B.ID));',
        'MISSING': '',
        'COMMENT_ONLY': '-- source missing',
    }
    for item_name, body in entries.items():
        button = ET.SubElement(calendar, 'Item', Name=item_name, ItemType='Push Button', CanvasName='MAIN')
        ET.SubElement(button, 'Trigger', Name='WHEN-BUTTON-PRESSED', TriggerText=body)
    return ET.tostring(form)


class TriggerNoiseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def generate(self, raw=None, config=None, label='out'):
        source = self.root / (label + '.xml')
        source.write_bytes(raw if raw is not None else fixture())
        settings = self.root / (label + '.json')
        settings.write_text(json.dumps({'java_company_imports': COMPANY_IMPORTS, **(config or {})}))
        output = self.root / label
        errors = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
            status = main(['migrate', str(source), '--screen', '--module', 'noise', '--config', str(settings), '--out', str(output)])
        self.assertEqual(status, 0, errors.getvalue())
        return output

    def test_only_proven_framework_noop_and_ui_actions_are_removed(self):
        out = self.generate()
        plan = json.loads((out / 'analysis/backend-plan.json').read_text())
        kept = {e['owner'] for e in plan['endpoints'] if e['operation'] == 'action'}
        skipped = {e['owner'] for e in plan['skipped_actions']}
        self.assertEqual(kept, {'CALENDAR.SAVE_DATE', 'CALENDAR.CUSTOM', 'CALENDAR.NESTED',
                                'CALENDAR.MISSING', 'CALENDAR.COMMENT_ONLY'})
        self.assertEqual(skipped, {'CALENDAR.CANCEL', 'CALENDAR.OK', 'CALENDAR.EMPTY',
                                   'CALENDAR.QUERY', 'CALENDAR.MESSAGE'})
        actions = json.loads((out / 'analysis/action-plan.json').read_text())
        self.assertEqual(kept, {a['owner'] for a in actions['actions']})
        for action in actions['skipped_actions']:
            self.assertTrue((out / action['source_file']).is_file())
            self.assertEqual(len(action['source_sha256']), 64)
        source = '\n'.join(p.read_text() for p in out.glob('backend/**/*.java'))
        for item in ('Cancel', 'Ok', 'Empty', 'Query', 'Message'):
            self.assertNotIn('calendar' + item + 'Action', source)
        api = plan['api']['actions']
        self.assertEqual(set(api), kept)
        frontend = screen_source(out)  # the component (4.26)
        for entry in api.values():  # this.url(...) with the endpoint as the CL names it
            self.assertIn("this.url('" + plan['api']['paths'][entry['constant']].lstrip('/') + "')", frontend)
        handoff = json.loads((out / 'analysis/backend-handoff.json').read_text())
        self.assertFalse(any(t['scope'] == 'button' and 'CALENDAR.QUERY:' in t['detail'] for t in handoff['tasks']))

    def test_date_change_sql_and_mixed_calendar_business_logic_survive(self):
        out = self.generate()
        model = json.loads((out / 'analysis/form.ir.json').read_text())
        date = next(t for t in model['triggers'] if t['owner'] == 'B.D')
        self.assertEqual((date['status'], date['target']), ('converted', 'backend'))
        self.assertEqual(date['passthrough']['written'], ['B.NOTE', 'B.D'])
        mixed = next(t for t in model['triggers'] if t['owner'] == 'CALENDAR.SAVE_DATE')
        self.assertEqual((mixed['status'], mixed['target']), ('converted', 'action'))
        self.assertIn('UPDATE T_B', mixed['passthrough_plan']['sql'])
        self.assertEqual(mixed['passthrough']['units'], ['LOCAL_AUDIT'])
        source = (out / 'backend/DPS/NoiseServiceImpl.java').read_text()
        self.assertIn('SELECT label INTO', source)
        self.assertIn('UPDATE T_B', source)
        self.assertIn('DbCalls.call(jdbc, "DECLARE\\n"', source)  # Java 11: concatenated literal, no text block
        self.assertNotIn('class BData', source)
        self.assertNotIn('UNUSED_UI', source)
        self.assertNotIn('postQueryB(', source)
        self.assertFalse(list(out.glob('backend/**/*Data.java')))
        self.assertEqual(len(list(out.glob('backend/**/*.java'))), 13)  # 12 module files + CL/CommonMigrateTools
        screen = json.loads((out / 'analysis/screen-plan.json').read_text())
        self.assertTrue(any(n['code'] == 'FRAMEWORK_BLOCK_KEPT' and n['owner'] == 'CALENDAR' for n in screen['notices']))

    def test_same_calendar_names_with_real_logic_are_kept(self):
        for body in ('UPDATE t SET x = 1;', 'pkg.changed(:B.D);',
                     "qms$calendar.cancel; pkg.changed(:B.D);", "raise FORM_TRIGGER_FAILURE;",
                     "IF pkg.changed(:B.D) = 1 THEN NULL; END IF;"):
            with self.subTest(body=body):
                out = self.generate(fixture({'CANCEL': body}), label='case' + str(len(list(self.root.glob('case*.xml')))))
                plan = json.loads((out / 'analysis/backend-plan.json').read_text())
                self.assertEqual([a['owner'] for a in plan['endpoints'] if a['operation'] == 'action'], ['CALENDAR.CANCEL'])

    def test_pure_calendar_creates_no_action_contract_or_jdbc_plumbing(self):
        out = self.generate(fixture({'OK': 'qms$calendar.ok;', 'CANCEL': 'BEGIN NULL; END;'}, calendar_only=True))
        source = (out / 'backend/DPS/NoiseServiceImpl.java').read_text()
        self.assertNotIn('ActionRequest', (out / 'backend/CL/NoiseDtos.java').read_text())
        self.assertNotIn('JdbcTemplate', source)
        self.assertNotIn('DbCalls', source)
        self.assertNotIn('Reviewed', source)
        self.assertEqual(json.loads((out / 'analysis/backend-plan.json').read_text())['endpoints'], [])

    def test_framework_classifier_never_swallows_nested_functions_or_bad_syntax(self):
        catalog = load({})
        for body in ("qms$event(pkg.mutate());", "qms$event(pkg.mutate);",
                     "qms$event('x') qms$event('y');", "BEGIN qms$event('x');"):
            with self.subTest(body=body):
                self.assertNotEqual(classify(body, catalog)[0], 'framework')
                with self.assertRaises(Unsupported):
                    prepare(body, block=None, items={}, units={}, prefixes=catalog['call_prefixes'])
        self.assertEqual(classify("BEGIN qms$event('a; -- b /* c */'); END;", catalog)[0], 'framework')

    def test_skipped_framework_arguments_do_not_leave_overlapping_bind_rewrites(self):
        result = prepare('qms$event(:SYSTEM.CURSOR_ITEM); UPDATE t SET a = 1;',
                         block=None, items={}, units={}, prefixes=('qms$',))
        self.assertEqual(result['binds'], [])
        self.assertNotIn(':SYSTEM', result['sql'])
        self.assertIn('BEGIN\n UPDATE t SET a = 1;', result['sql'])  # the NULL left for qms$event is pruned (4.14)
        self.assertNotIn('qms$', result['sql'])

    def test_plsql_structure_check_accepts_nested_loops_cases_and_exception_handlers(self):
        body = '''DECLARE n NUMBER; BEGIN
            SELECT CASE WHEN 1 = 1 THEN 2 ELSE 3 END INTO n FROM dual;
            FOR i IN 1..2 LOOP
                IF i = 1 THEN CASE n WHEN 2 THEN NULL; ELSE NULL; END CASE; END IF;
            END LOOP;
            EXCEPTION WHEN OTHERS THEN cgte$other_exceptions;
        END;'''
        result = prepare(body, block=None, items={}, units={}, prefixes=('cgte$',))
        self.assertIn('END CASE; END IF;', result['sql'])
        self.assertIn('EXCEPTION WHEN OTHERS THEN RAISE;', result['sql'])

    def test_known_named_out_arguments_and_nested_unit_writes_are_checked(self):
        items = {'B': {n: {'type': 'text'} for n in ('ID', 'NOTE')}}
        procedures = {'PKG.FILL': {'kind': 'procedure', 'arguments': [
            {'name': 'P_IN', 'mode': 'IN'}, {'name': 'P_OUT', 'mode': 'OUT'}]}}
        kwargs = dict(block='B', items=items, prefixes=(), procedures=procedures,
                      writable=lambda b: b['item'] != 'ID')
        for source, units in [
            ('pkg.fill(p_out => :B.ID, p_in => :B.NOTE);', {}),
            ('local_fill;', {'LOCAL_FILL': {'kind': 'procedure', 'text':
                'PROCEDURE local_fill IS BEGIN pkg.fill(p_out => :B.ID, p_in => :B.NOTE); END;'}}),
        ]:
            with self.subTest(source=source), self.assertRaisesRegex(Unsupported, 'nem visszaírható mezőt ír: B.ID'):
                prepare(source, units=units, **kwargs)
        good = prepare('pkg.fill(p_out => :B.NOTE, p_in => :B.ID);', units={}, **kwargs)
        self.assertIn('B.NOTE', good['assigned'])

    def test_oracle_input_strings_are_untouched_and_empty_sources_are_manual(self):
        result = prepare("SELECT q'[x; -- text]' INTO :B.NOTE FROM dual;", block='B',
                         items={'B': {'NOTE': {'type': 'text'}}}, units={}, prefixes=())
        self.assertIn("q'[x; -- text]'", result['sql'])
        for body in ('', '-- comment', '/* comment */'):
            with self.subTest(body=body), self.assertRaises(Unsupported):
                prepare(body, block='B', items={}, units={}, prefixes=())

    def test_generation_is_deterministic_and_module_review_still_blocks_execution(self):
        one = self.generate(label='one')
        two = self.generate(label='two')
        for path in one.glob('backend/**/*.java'):
            self.assertEqual(path.read_bytes(), (two / path.relative_to(one)).read_bytes())
        plan = json.loads((one / 'analysis/backend-plan.json').read_text())
        self.assertFalse(any(e['implemented'] for e in plan['endpoints']))
        action = next(e for e in plan['endpoints'] if e.get('owner') == 'CALENDAR.SAVE_DATE')
        self.assertTrue(action['ready_after_module_review'])

    def test_generated_java_compiles_and_executes_typed_jdbc_binds_and_results(self):
        if not shutil.which('java'):
            self.skipTest('Java 11+ compiler required')
        out = self.generate(config={'backend_live': True})
        model = json.loads((out / 'analysis/form.ir.json').read_text())
        plan = json.loads((out / 'analysis/backend-plan.json').read_text())
        action = next(e for e in plan['endpoints'] if e.get('owner') == 'CALENDAR.SAVE_DATE')
        prepared = next(t for t in model['triggers'] if t['owner'] == action['owner'])['passthrough_plan']
        expected_sql = self.root / 'expected.sql'
        expected_sql.write_bytes(prepared['sql'].encode('utf-8'))  # exact bytes: no CRLF on Windows
        input_keys = [b['source'] for b in prepared['binds']]
        self.assertEqual(input_keys, ['B.D', 'B.ID', 'B.NOTE'])
        smoke = self.root / 'Smoke.java'
        smoke.write_text(SMOKE.replace('__METHOD__', action['method']), encoding='utf-8')
        sources = list(out.glob('backend/**/*.java')) + write_stubs(self.root / 'stubs') + [smoke]
        args = self.root / 'sources.args'
        args.write_text('\n'.join('"' + str(p).replace(chr(92), '/') + '"' for p in sources))
        result = subprocess.run(['java', 'com.sun.tools.javac.Main', '--release', '11', '-encoding', 'UTF-8',
                                 '-d', str(self.root / 'classes'), '@' + str(args)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        run = subprocess.run(['java', '-cp', str(self.root / 'classes'), 'Smoke', str(expected_sql)],
                             capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('typed binds, exact SQL, result mapping and validation passed', run.stdout)


SMOKE = r'''
import java.util.*;
import java.math.BigDecimal;
import java.sql.*;
import java.time.LocalDateTime;
import hu.company.features.noise.dps.NoiseServiceImpl;
import hu.company.features.noise.cl.NoiseDtos.*;
import hu.company.common.UserDto;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.CallableStatementCallback;

public class Smoke {
    static int executions;
    static String expectedSql;
    static void require(boolean ok, String message) {
        if (!ok) throw new AssertionError(message);
    }
    static final class Database extends NamedParameterJdbcTemplate {
        @Override public JdbcTemplate getJdbcTemplate() {
            return new JdbcTemplate() {
                @Override public <T> T execute(String sql, CallableStatementCallback<T> callback) {
                    require(sql.equals(expectedSql), "Generated SQL differs from the prepared block");
                    var ins = new LinkedHashMap<Integer, Object>();
                    var outs = new LinkedHashMap<Integer, Integer>();
                    CallableStatement cs = (CallableStatement) java.lang.reflect.Proxy.newProxyInstance(
                        Smoke.class.getClassLoader(), new Class<?>[] {CallableStatement.class}, (proxy, method, args) -> {
                            switch (method.getName()) {
                                case "setObject": ins.put((Integer) args[0], args[1]); return null;
                                case "setNull": ins.put((Integer) args[0], null); return null;
                                case "registerOutParameter": outs.put((Integer) args[0], (Integer) args[1]); return null;
                                case "execute":
                                    executions++;
                                    require(ins.get(1) instanceof LocalDateTime, "DATE was not typed");
                                    require(new BigDecimal("42").equals(ins.get(2)), "NUMBER was not typed");
                                    require(ins.containsKey(3) && ins.get(3) == null, "NULL text was not bound");
                                    require(outs.equals(Map.of(4, Types.TIMESTAMP, 5, Types.NUMERIC,
                                                               6, Types.VARCHAR, 7, Types.VARCHAR,
                                                               8, Types.VARCHAR)), "Wrong OUT registration"); // 8: screen commands
                                    return false;
                                case "getObject": return LocalDateTime.of(2026, 9, 28, 12, 30);
                                case "getBigDecimal": return new BigDecimal("43");
                                case "getString": return (Integer) args[0] == 7 ? "Finished\n" : (Integer) args[0] == 8 ? null : "Changed";
                                default: throw new AssertionError("Unexpected JDBC call: " + method.getName());
                            }
                        });
                    try { return callback.doInCallableStatement(cs); }
                    catch (SQLException error) { throw new RuntimeException(error); }
                }
            };
        }
    }
    public static void main(String[] args) throws Exception {
        expectedSql = java.nio.file.Files.readString(java.nio.file.Path.of(args[0]));
        var service = new NoiseServiceImpl(new Database());
        var values = new LinkedHashMap<String, String>();
        values.put("ID", "42"); values.put("D", "2026-09-28T12:30:00"); values.put("NOTE", null);
        var result = service.__METHOD__(new UserDto(), new ActionRequest(Map.of("B", values), Map.of()));
        require(executions == 1, "One action must execute one PL/SQL block");
        require(result.blocks().get("B").get("ID").equals("43"), "NUMBER OUT lost");
        require(result.blocks().get("B").get("D").startsWith("2026-09-28T12:30"), "DATE OUT lost");
        require(result.blocks().get("B").get("NOTE").equals("Changed"), "Text OUT lost");
        require(result.messages().equals(List.of("Finished")), "Message output lost");
        values.put("ID", "42 OR 1=1");
        try {
            service.__METHOD__(new UserDto(), new ActionRequest(Map.of("B", values), Map.of()));
            throw new AssertionError("Invalid numeric input reached JDBC");
        } catch (org.springframework.web.server.ResponseStatusException expected) {
            require(executions == 1, "Invalid input executed SQL");
        }
        System.out.println("typed binds, exact SQL, result mapping and validation passed");
    }
}
'''


if __name__ == '__main__':
    unittest.main()

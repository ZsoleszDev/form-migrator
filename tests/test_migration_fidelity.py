"""End-to-end regressions for native calendars and Oracle trigger fidelity."""
import json
import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET

import test_forms_runtime as runtime
from java_support import write_stubs
from frm_forms.plsql import Unsupported
from frm_forms.plsql_passthrough import prepare

fixture = runtime.fixture


def calendar_screen(*, shared=False, business=False, same_window=False):
    form = ET.fromstring(fixture({}))
    form.find('Canvas').set('WindowName', 'WORK')
    ET.SubElement(form, 'Window', Name='WORK', WindowStyle='Document', PrimaryCanvas='MAIN')
    window = 'WORK' if same_window else 'PICKER_WINDOW'
    if not same_window:
        ET.SubElement(form, 'Window', Name=window, WindowStyle='Dialog', PrimaryCanvas='DAY_GRID')
    canvas = ET.SubElement(form, 'Canvas', Name='DAY_GRID', CanvasType='Content', WindowName=window)
    group = ET.SubElement(canvas, 'Graphics', Name='LABELS', GraphicsType='Group')
    for n, day in enumerate(('Hétfő', 'Kedd', 'Szerda', 'Csütörtök', 'Péntek', 'Szombat', 'Vasárnap')):
        ET.SubElement(group, 'Graphics', Name='DAY_' + str(n), GraphicsType='Text', Text=day)
    calendar = next(b for b in form.findall('Block') if b.get('Name') == 'CALENDAR')
    for item in calendar.findall('Item'):
        item.set('CanvasName', 'DAY_GRID')
    if shared:
        b = ET.SubElement(form, 'Block', Name='BUSINESS', DatabaseDataBlock='false')
        ET.SubElement(b, 'Item', Name='VALUE', ItemType='Text Item', CanvasName='DAY_GRID')
    if business:
        calendar.find('Item/Trigger').set('TriggerText', 'UPDATE T_B SET D = :B.D WHERE ID = :B.ID;')
    return ET.tostring(form)


class MigrationFidelityTests(unittest.TestCase):
    setUp = runtime.FormsRuntimeTests.setUp
    generate = runtime.FormsRuntimeTests.generate
    read = runtime.FormsRuntimeTests.read

    def test_calendar_labels_do_not_resurrect_a_screen(self):
        out = self.generate(calendar_screen())
        plan = self.read(out, 'screen-plan.json')
        self.assertEqual([s['name'] for s in plan['surfaces']], ['MAIN'])
        self.assertEqual(plan['framework_canvases'][0]['canvas'], 'DAY_GRID')
        self.assertEqual(next(w for w in plan['windows'] if w['name'] == 'PICKER_WINDOW')['role'], 'unused')
        self.assertTrue(all(g['status'] == 'unused' for g in plan['graphics']))
        source = (out / 'frontend/rt/rt.component.ts').read_text(encoding='utf-8')
        self.assertNotIn('<p-dialog', source)
        self.assertNotIn('Hétfő', source)
        self.assertIn(next(i['widget'] for s in plan['sections'] for i in s['items'] if i['owner'] == 'B.D'), {'date', 'datetime'})
        self.assertIn('showIcon: true', source)

    def test_calendar_content_page_is_omitted_in_a_shared_window(self):
        out = self.generate(calendar_screen(same_window=True))
        plan = self.read(out, 'screen-plan.json')
        self.assertEqual([s['name'] for s in plan['surfaces']], ['MAIN'])
        self.assertEqual(plan['windows'][0]['content_canvases'], ['MAIN'])

    def test_skipped_primary_canvas_does_not_reappear_as_an_empty_page(self):
        form = ET.fromstring(calendar_screen(same_window=True))
        form.find('Window').set('PrimaryCanvas', 'DAY_GRID')
        out = self.generate(ET.tostring(form))
        plan = self.read(out, 'screen-plan.json')
        self.assertEqual([s['name'] for s in plan['surfaces']], ['MAIN'])
        self.assertEqual(plan['windows'][0]['initial_canvas'], 'MAIN')

    def test_text_only_business_canvas_is_not_removed_by_its_name(self):
        form = ET.fromstring(fixture({}))
        canvas = ET.SubElement(form, 'Canvas', Name='CALENDAR', CanvasType='Content')
        ET.SubElement(canvas, 'Graphics', Name='HELP', GraphicsType='Text', Text='Üzleti tájékoztató')
        out = self.generate(ET.tostring(form))
        plan = self.read(out, 'screen-plan.json')
        self.assertIn('CALENDAR', [s['name'] for s in plan['surfaces']])
        self.assertEqual(plan['framework_canvases'], [])

    def test_calendar_sharing_canvas_or_containing_business_logic_is_kept(self):
        for label, raw in [('shared', calendar_screen(shared=True)), ('business', calendar_screen(business=True))]:
            with self.subTest(label=label):
                out = self.generate(raw, label=label)
                plan = self.read(out, 'screen-plan.json')
                self.assertIn('DAY_GRID', [s['name'] for s in plan['surfaces']])
                self.assertEqual(plan['framework_canvases'], [])
                if label == 'business':
                    backend = self.read(out, 'backend-plan.json')
                    self.assertTrue(any(e.get('owner') == 'CALENDAR.OK' and e.get('runs') == 'plsql' for e in backend['endpoints']))

    def test_date_business_trigger_keeps_original_plsql_by_default(self):
        form = ET.fromstring(fixture({}))
        date = next(i for i in form.findall('Block/Item') if i.get('Name') == 'D')
        date.find('Trigger').set('TriggerText', "IF :B.D IS NULL THEN MESSAGE('Dátum szükséges'); RAISE FORM_TRIGGER_FAILURE; END IF;")
        out = self.generate(ET.tostring(form), config={'backend_live': True})
        trigger = next(t for t in self.read(out, 'form.ir.json')['triggers'] if t['owner'] == 'B.D')
        self.assertEqual(trigger['target'], 'backend')
        self.assertIn('passthrough', trigger)
        service = (out / 'backend/DPS/RtServiceImpl.java').read_text(encoding='utf-8')
        self.assertIn('FORM_TRIGGER_FAILURE EXCEPTION', service)
        self.assertIn("frm_msg('Dátum szükséges')", service)  # UTF-8 source: no Unicode escapes (Checkstyle)
        self.assertNotIn('onCalendarOk(', service)

    def test_legacy_java_mode_remains_available(self):
        form = ET.fromstring(fixture({}))
        next(i for i in form.findall('Block/Item') if i.get('Name') == 'D').find('Trigger').set(
            'TriggerText', "IF :B.D IS NULL THEN MESSAGE('Required'); RAISE FORM_TRIGGER_FAILURE; END IF;")
        out = self.generate(ET.tostring(form), config={'backend_trigger_mode': 'java'})
        trigger = next(t for t in self.read(out, 'form.ir.json')['triggers'] if t['owner'] == 'B.D')
        self.assertNotIn('passthrough', trigger)
        self.assertIn('context.abort()', trigger['java'])

    def test_calendar_notifications_preserve_exception_recovery(self):
        body = "BEGIN UPDATE t SET a = 1; EXCEPTION WHEN NO_DATA_FOUND THEN calendar.event('X'); END;"
        sql = runtime.prepared(body)['sql']
        self.assertIn('WHEN NO_DATA_FOUND THEN NULL;', sql)
        self.assertNotIn('THEN RAISE;', sql)

    def test_case_branches_are_not_exception_handlers(self):
        body = 'DECLARE yes BOOLEAN := TRUE; BEGIN CASE WHEN yes THEN qms$notify; ELSE NULL; END CASE; END;'
        sql = runtime.prepared(body)['sql']
        self.assertIn('WHEN yes THEN NULL;', sql)
        self.assertNotIn('THEN RAISE;', sql)

    def test_error_dispatch_propagates_after_other_handler_statements(self):
        body = "BEGIN UPDATE t SET a = 1; EXCEPTION WHEN OTHERS THEN MESSAGE('Failure'); qms$error; END;"
        sql = runtime.prepared(body)['sql']
        self.assertIn("frm_msg('Failure'); RAISE;", sql)
        # Recovery in a finished nested handler does not leak into the outer body.
        nested = 'BEGIN BEGIN NULL; EXCEPTION WHEN OTHERS THEN NULL; END; qms$notify; END;'
        sql = runtime.prepared(nested)['sql']
        # 4.14: the NULL left for qms$notify is pruned; it never became a RAISE.
        self.assertIn('EXCEPTION WHEN OTHERS THEN NULL; END; END;', sql)
        self.assertNotIn('RAISE;', sql)
        self.assertNotIn('qms$', sql)

    def test_unknown_function_out_arguments_cannot_silently_change_protected_items(self):
        options = dict(block='B', items={'B': {'ID': {'type': 'number'}, 'NAME': {'type': 'text'}}},
                       units={}, prefixes=(), writable=lambda b: b['item'] != 'ID')
        sql = prepare(':B.NAME := pkg.fill(:B.ID);', **options)
        self.assertEqual(sql['guarded'], ['B.ID'])
        self.assertEqual(sql['unresolved'], ['PKG.FILL'])
        signature = {'PKG.FILL': {'kind': 'function', 'arguments': [{'name': 'P_ID', 'mode': 'IN OUT'}]}}
        with self.assertRaisesRegex(Unsupported, 'nem visszaírható mezőt ír: B.ID'):
            prepare(':B.NAME := pkg.fill(:B.ID);', procedures=signature, **options)

    def test_coverage_distinguishes_conversion_from_actual_wiring(self):
        form = ET.fromstring(fixture({'SAVE': 'UPDATE t SET a = :B.ID;', 'FOCUS': "go_item('B.D');"},
                                     startup="go_block('B'); execute_query;"))
        date = next(i for i in form.findall('Block/Item') if i.get('Name') == 'D')
        date.find('Trigger').set('TriggerText', "IF :B.D IS NULL THEN RAISE FORM_TRIGGER_FAILURE; END IF;")
        out = self.generate(ET.tostring(form), config={'backend_live': True})
        data = self.read(out, 'runtime-coverage.json')
        self.assertFalse(data['equivalence_verified'])
        rows = {r['id']: r for r in data['triggers']}
        validation = rows['B.D:WHEN-VALIDATE-ITEM']
        self.assertEqual((validation['status'], validation['engine']), ('backend', 'plsql'))
        self.assertIn('nincs szerverhívás mezőelhagyáskor', validation['execution'])
        self.assertEqual({e['operation'] for e in validation['endpoints']}, {'create', 'update'})
        self.assertEqual(rows['B.SAVE:WHEN-BUTTON-PRESSED']['status'], 'backend')
        self.assertIn('Angular HTTP', rows['B.SAVE:WHEN-BUTTON-PRESSED']['execution'])
        self.assertEqual(rows['B.FOCUS:WHEN-BUTTON-PRESSED']['status'], 'manual')
        self.assertEqual(rows['RT:WHEN-NEW-FORM-INSTANCE']['status'], 'manual')
        self.assertEqual(rows['CALENDAR.OK:WHEN-BUTTON-PRESSED']['status'], 'omitted')
        self.assertEqual(sum(data['counts'].values()), len(self.read(out, 'form.ir.json')['triggers']))
        self.assertTrue(all((out / r['source']).is_file() for r in data['triggers']))
        self.assertTrue((out / 'RUNTIME_COVERAGE.md').is_file())

    def test_coverage_reports_disabled_and_absent_backend(self):
        from frm_forms.runtime_coverage import coverage
        out = self.generate(fixture({'SAVE': 'UPDATE t SET a = 1;'}))
        rows = {r['owner']: r for r in self.read(out, 'runtime-coverage.json')['triggers']}
        self.assertEqual(rows['B.SAVE']['status'], 'blocked')
        self.assertTrue(rows['B.SAVE']['endpoints'][0]['ready_after_module_review'])
        without_backend = coverage(self.read(out, 'form.ir.json'))
        self.assertEqual(next(r for r in without_backend['triggers'] if r['owner'] == 'B.SAVE')['status'], 'manual')

    def test_native_validation_chain_compiles_and_passes_changed_values_to_next_trigger(self):
        if not shutil.which('java'):
            self.skipTest('Java 11+ compiler required')
        form = ET.fromstring(fixture({}))
        block = next(b for b in form.findall('Block') if b.get('Name') == 'B')
        field = next(i for i in block.findall('Item') if i.get('Name') == 'NAME')
        ET.SubElement(field, 'Trigger', Name='WHEN-VALIDATE-ITEM',
                      TriggerText="MESSAGE('WVI'); :B.NAME := UPPER(:B.NAME);")
        ET.SubElement(block, 'Trigger', Name='WHEN-VALIDATE-RECORD',
                      TriggerText="MESSAGE('WVR'); IF LENGTH(:B.NAME) > 40 THEN RAISE FORM_TRIGGER_FAILURE; END IF;")
        out = self.generate(ET.tostring(form), config={'backend_live': True})
        smoke = self.root / 'ValidationSmoke.java'
        smoke.write_text(VALIDATION_SMOKE, encoding='utf-8')
        sources = list(out.glob('backend/**/*.java')) + write_stubs(self.root / 'stubs') + [smoke]
        args = self.root / 'sources.args'
        args.write_text('\n'.join('"' + str(p).replace(chr(92), '/') + '"' for p in sources), encoding='utf-8')
        compiled = subprocess.run(['java', 'com.sun.tools.javac.Main', '--release', '11', '-encoding', 'UTF-8',
                                   '-d', str(self.root / 'classes'), '@' + str(args)], capture_output=True, text=True, timeout=60)
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        result = subprocess.run(['java', '-cp', str(self.root / 'classes'), 'ValidationSmoke'],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('validation order and OUT values passed', result.stdout)


VALIDATION_SMOKE = r'''
import java.util.*;
import java.sql.*;
import hu.company.features.rt.dps.RtServiceImpl;
import hu.company.features.rt.cl.RtDtos.BRow;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.CallableStatementCallback;

public class ValidationSmoke {
    static int executions;
    static void check(boolean ok, String message) { if (!ok) throw new AssertionError(message); }
    static class Database extends NamedParameterJdbcTemplate {
        @Override public JdbcTemplate getJdbcTemplate() {
            return new JdbcTemplate() {
                @Override public <T> T execute(String sql, CallableStatementCallback<T> callback) {
                    String event = executions == 0 ? "WVI" : "WVR";
                    check(sql.contains("frm_msg('" + event + "')"), "Wrong trigger order or not original PL/SQL");
                    check(sql.contains(executions == 0 ? "UPPER(" : "LENGTH("), "Oracle expression was lost");
                    var outs = new LinkedHashMap<Integer, Integer>();
                    CallableStatement cs = (CallableStatement) java.lang.reflect.Proxy.newProxyInstance(
                        ValidationSmoke.class.getClassLoader(), new Class<?>[] {CallableStatement.class}, (proxy, method, args) -> {
                            switch (method.getName()) {
                                case "setObject":
                                    check(args[0].equals(1) && args[1].equals(executions == 0 ? "before" : "AFTER"), "OUT not passed to next trigger");
                                    check(args[2].equals(Types.VARCHAR), "Wrong IN type"); return null;
                                case "registerOutParameter": outs.put((Integer) args[0], (Integer) args[1]); return null;
                                case "execute":
                                    check(outs.equals(Map.of(2, Types.VARCHAR, 3, Types.VARCHAR)), "Wrong OUT positions");
                                    executions++; return false;
                                case "getString": return (Integer) args[0] == 2 ? "AFTER" : event + "\n";
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
        var service = new RtServiceImpl(new Database());
        var row = new BRow(); row.name = "before";
        var validate = Arrays.stream(RtServiceImpl.class.getDeclaredMethods()).filter(m -> m.getName().equals("validateRulesB")).findFirst().orElseThrow();
        var constructor = validate.getParameterTypes()[1].getDeclaredConstructor(); constructor.setAccessible(true);
        Object context = constructor.newInstance(); validate.setAccessible(true); validate.invoke(service, row, context);
        check(executions == 2 && row.name.equals("AFTER"), "Missing trigger or missing OUT assignment");
        var messages = context.getClass().getDeclaredMethod("messages"); messages.setAccessible(true);
        check(messages.invoke(context).equals(List.of("WVI", "WVR")), "Messages not kept in order");
        System.out.println("validation order and OUT values passed");
    }
}
'''

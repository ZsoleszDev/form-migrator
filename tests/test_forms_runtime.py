"""4.9: calls that exist only in the Oracle Forms runtime never become database calls."""
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
from frm_forms.common import MigrationError
from frm_forms.dictionary import references
from frm_forms.discovery import build_map
from frm_forms.framework import classify, forms_calls, load, runtime_call
from frm_forms.plsql import Unsupported
from frm_forms.plsql_passthrough import prepare, unit_library

CALENDAR = load({})['runtime_calls']


def fixture(buttons=None, startup="calendar.event('WHEN-NEW-FORM-INSTANCE');", units=()):
    form = ET.Element('FormModule', Name='RT', Title='Runtime')
    ET.SubElement(form, 'Canvas', Name='MAIN', CanvasType='Content')
    if startup:
        ET.SubElement(form, 'Trigger', Name='WHEN-NEW-FORM-INSTANCE', TriggerText=startup)
    block = ET.SubElement(form, 'Block', Name='B', DatabaseDataBlock='true', QueryDataSourceName='T_B')
    ET.SubElement(block, 'Item', Name='ID', DataType='Number', PrimaryKey='true', CanvasName='MAIN')
    date = ET.SubElement(block, 'Item', Name='D', ItemType='Text Item', DataType='Date', CanvasName='MAIN')
    ET.SubElement(date, 'Trigger', Name='WHEN-VALIDATE-ITEM', TriggerText="calendar.event('WHEN-VALIDATE-ITEM');")
    ET.SubElement(block, 'Item', Name='NAME', DataType='Char', MaximumLength='40', CanvasName='MAIN')
    ET.SubElement(block, 'Trigger', Name='POST-QUERY', TriggerText="BEGIN calendar.event('POST-QUERY'); END;")
    entries = buttons if buttons is not None else {
        'SAVE': "BEGIN calendar.event('WHEN-BUTTON-PRESSED'); UPDATE T_B SET NAME = :B.NAME WHERE ID = :B.ID; END;",
        'LINK': "web.show_document('https://example.invalid', '_blank');",
        'MARK': "set_item_instance_property(:SYSTEM.CURSOR_ITEM, CURRENT_RECORD, VISUAL_ATTRIBUTE, 'VA'); synchronize;",
        'SHELL': "host('dir');",
    }
    for item_name, body in entries.items():
        button = ET.SubElement(block, 'Item', Name=item_name, ItemType='Push Button', CanvasName='MAIN')
        ET.SubElement(button, 'Trigger', Name='WHEN-BUTTON-PRESSED', TriggerText=body)
    calendar = ET.SubElement(form, 'Block', Name='CALENDAR', DatabaseDataBlock='false')
    for item_name in ('OK', 'CANCEL'):
        button = ET.SubElement(calendar, 'Item', Name=item_name, ItemType='Push Button', CanvasName='MAIN')
        ET.SubElement(button, 'Trigger', Name='WHEN-BUTTON-PRESSED', TriggerText="calendar.event('WHEN-BUTTON-PRESSED');")
    ET.SubElement(calendar, 'Trigger', Name='WHEN-NEW-BLOCK-INSTANCE', TriggerText="calendar.event('WHEN-NEW-BLOCK-INSTANCE');")
    for name, kind, text in units:
        ET.SubElement(form, 'ProgramUnit', Name=name, ProgramUnitType=kind, ProgramUnitText=text)
    return ET.tostring(form)


def prepared(body, units=None, **kwargs):
    return prepare(body, block=None, items={}, units=units or {}, prefixes=('qms$',), runtime_calls=CALENDAR, **kwargs)


class FormsRuntimeTests(unittest.TestCase):
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
            status = main(['migrate', str(source), '--screen', '--module', 'rt', '--config', str(settings), '--out', str(output)])
        self.assertEqual(status, 0, errors.getvalue())
        return output

    def read(self, out, name):
        return json.loads((out / 'analysis' / name).read_text(encoding='utf-8'))

    def test_calendar_only_triggers_get_no_database_call_endpoint_or_task(self):
        out = self.generate()
        model, plan = self.read(out, 'form.ir.json'), self.read(out, 'backend-plan.json')
        status = {t['id']: t['status'] for t in model['triggers']}
        for trigger in ('RT:WHEN-NEW-FORM-INSTANCE', 'B:POST-QUERY', 'B.D:WHEN-VALIDATE-ITEM', 'CALENDAR:WHEN-NEW-BLOCK-INSTANCE',
                        'CALENDAR.OK:WHEN-BUTTON-PRESSED', 'CALENDAR.CANCEL:WHEN-BUTTON-PRESSED'):
            self.assertEqual(status[trigger], 'framework', trigger)
        backend = '\n'.join(p.read_text(encoding='utf-8') for p in out.glob('backend/**/*.java'))
        self.assertNotIn('calendar', backend.lower())
        skipped = {a['owner']: a for a in plan['skipped_actions']}
        for owner in ('CALENDAR.OK', 'CALENDAR.CANCEL'):
            self.assertEqual(skipped[owner]['category'], 'framework')
            self.assertIn('CALENDAR.*', skipped[owner]['reason'])
        # No blocker from the calendar calls: the start-up trigger no longer disables the whole backend.
        issues = [i for i in model['issues'] if i['code'] in {'UNSUPPORTED_TRIGGER', 'FRAMEWORK_DATA_TRIGGER'}]
        self.assertFalse([i for i in issues if 'CALENDAR' in i['detail'].upper()])
        listing = next(e for e in plan['endpoints'] if e['operation'] == 'list')
        self.assertEqual(listing['blockers'], [])
        self.assertIn('CALENDAR', [b['block'] for b in self.read(out, 'screen-plan.json')['framework_blocks']])
        self.assertIn('Forms-futtatókörnyezet, kihagyva', (out / 'backend/DPS/RtServiceImpl.java').read_text(encoding='utf-8'))

    def test_mixed_trigger_runs_its_sql_without_the_forms_call(self):
        out = self.generate()
        model = self.read(out, 'form.ir.json')
        save = next(t for t in model['triggers'] if t['owner'] == 'B.SAVE')
        self.assertEqual((save['status'], save['target']), ('converted', 'action'))
        self.assertIn('UPDATE T_B', save['passthrough_plan']['sql'])  # the NULL left for the call is pruned (4.14)
        self.assertNotIn('calendar', save['passthrough_plan']['sql'].lower())
        self.assertEqual(save['passthrough']['notes'], ['CALENDAR.EVENT -> NULL (Forms-futtatókörnyezet: CALENDAR.*)'])
        self.assertEqual(save['passthrough']['unresolved'], [])

    def test_buttons_with_only_forms_builtins_get_no_endpoint_but_server_work_stays(self):
        out = self.generate()
        plan = self.read(out, 'backend-plan.json')
        # Forms-only built-ins of a button are screen commands of its endpoint (forms_emulation);
        # server work (HOST) stays manual.
        kept = {e['owner']: e['runs'] for e in plan['endpoints'] if e['operation'] == 'action'}
        self.assertEqual(kept, {'B.SAVE': 'plsql', 'B.SHELL': 'manual', 'B.LINK': 'plsql', 'B.MARK': 'plsql'})
        self.assertFalse({a['owner'] for a in plan['skipped_actions']} & {'B.LINK', 'B.MARK'})
        source = (out / 'backend/DPS/RtServiceImpl.java').read_text(encoding='utf-8')
        self.assertEqual(source.count('DbCalls.call(jdbc'), 3)
        self.assertIn("frm_cmd('WEB.SHOW_DOCUMENT', 'https://example.invalid', '_blank')", source)
        self.assertIn("frm_cmd('SET_ITEM_INSTANCE_PROPERTY', nv_", source)  # :SYSTEM.CURSOR_ITEM from the screen
        model = self.read(out, 'form.ir.json')
        link = next(t for t in model['triggers'] if t['owner'] == 'B.LINK')
        self.assertEqual(link['passthrough']['commands'], ['WEB.SHOW_DOCUMENT'])
        mark = next(t for t in model['triggers'] if t['owner'] == 'B.MARK')
        self.assertIn('SYSTEM.CURSOR_ITEM', mark['passthrough']['binds'])
        scopes = {i['detail'].split(':')[0]: i['scope'] for i in model['issues'] if i['code'] == 'UNSUPPORTED_TRIGGER'}
        self.assertEqual(scopes, {'B.SHELL': 'button'})

    def test_forms_builtins_and_forms_only_calls_are_refused_for_the_database(self):
        for body in ('delete_record;', 'bell;', "set_item_instance_property('B.X', CURRENT_RECORD, VISUAL_ATTRIBUTE, 'VA');",
                     "web.show_document('u', '_blank');", "text_io.put_line(f, 'x');", 'UPDATE t SET a = 1; clear_item;',
                     "IF get_item_instance_property('B.X', 1, VISIBLE) = 'TRUE' THEN NULL; END IF;"):
            with self.subTest(body=body), self.assertRaisesRegex(Unsupported, 'az adatbázisban nem futtatható'):
                prepared(body)
        for body in ("UPDATE t SET a = calendar.today;", "IF calendar.is_open THEN NULL; END IF;"):
            with self.subTest(body=body), self.assertRaisesRegex(Unsupported, 'kifejezésben: CALENDAR'):
                prepared(body)
        # Not Forms-only: a column named HELP, an owner-qualified database package, a similar package name.
        self.assertIn('help = 1', prepared('UPDATE t SET help = 1;')['sql'])
        self.assertEqual(prepared('app.calendar.event(1);')['unresolved'], ['APP.CALENDAR.EVENT'])
        self.assertEqual(prepared('calendar_util.run;')['unresolved'], ['CALENDAR_UTIL.RUN'])
        with self.assertRaisesRegex(Unsupported, 'nem elhagyható argumentummal'):
            prepared("calendar.event(pkg.mutate(1)); UPDATE t SET a = 1;")

    def test_local_units_that_cannot_run_in_oracle_are_never_embedded(self):
        units = {'EMPTY_UNIT': {'kind': 'procedure', 'text': ''},
                 'MARK': {'kind': 'procedure', 'text': "PROCEDURE mark IS BEGIN set_item_instance_property('B.X', 1, VISIBLE, PROPERTY_TRUE); END;"},
                 'CAL': {'kind': 'function', 'text': 'FUNCTION cal RETURN NUMBER IS BEGIN RETURN 1; END;'}}
        library = unit_library(units, {}, ('qms$',), runtime_calls=CALENDAR)
        self.assertIn('forrása üres', library['EMPTY_UNIT']['error'])
        self.assertIn('SET_ITEM_INSTANCE_PROPERTY', library['MARK']['error'])
        for body in ('empty_unit; UPDATE t SET a = 1;', 'mark; UPDATE t SET a = 1;'):
            with self.subTest(body=body), self.assertRaisesRegex(Unsupported, 'helyi eljárás nem futtatható'):
                prepared(body, units=units)
        with self.assertRaisesRegex(Unsupported, 'saját programegysége'):
            prepared('cal.event(1);', units=units)  # a package exported without its type
        # A discarded calendar call needs no local CALENDAR package in the block.
        package = {'CALENDAR': {'kind': 'package', 'text': 'PACKAGE calendar IS PROCEDURE event(e VARCHAR2); END;'}}
        self.assertIn('UPDATE t', prepared("calendar.event('X'); UPDATE t SET a = 1;", units=package)['sql'])
        # A local package is embedded in the block, but only with its body in the export.
        with self.assertRaisesRegex(Unsupported, 'helyi csomag nem futtatható: A csomag törzse'):
            prepared("calendar2.run; UPDATE t SET a = 1;", units={'CALENDAR2': package['CALENDAR']})

    def test_startup_presentation_no_longer_blocks_but_item_properties_still_do(self):
        # Start-up code becomes the init endpoint; its Forms built-ins are screen commands.
        shown = self.generate(fixture({}, startup="set_window_property(forms_mdi_window, window_state, maximize); "
                                                  "go_block('B'); execute_query;"), label='shown')
        model = self.read(shown, 'form.ir.json')
        self.assertFalse([i for i in model['issues'] if i['code'] == 'UNSUPPORTED_TRIGGER'])
        self.assertEqual(model['init_plan']['passthrough']['commands'], ['SET_WINDOW_PROPERTY', 'GO_BLOCK', 'EXECUTE_QUERY'])
        endpoints = self.read(shown, 'backend-plan.json')['endpoints']
        self.assertEqual(next(e for e in endpoints if e['operation'] == 'list')['blockers'], [])
        init = next(e for e in endpoints if e.get('owner') == '@INIT')
        self.assertTrue(init['implemented'] or init['ready_after_module_review'])
        self.assertEqual(init['blockers'], [])
        # An item made read-only at start-up stays read-only in the generated write endpoints too.
        guarded = self.generate(fixture({}, startup="set_item_property('B.NAME', UPDATE_ALLOWED, PROPERTY_FALSE);"), label='guarded')
        model = self.read(guarded, 'form.ir.json')
        self.assertFalse(next(i for b in model['blocks'] for i in b['items'] if i['name'] == 'NAME')['update_allowed'])
        self.assertTrue(any(i['code'] == 'STARTUP_ITEM_PROPERTY' for i in model['issues']))

    def test_catalog_defaults_switch_off_and_validation(self):
        custom = self.root / 'catalog.json'
        custom.write_text(json.dumps({'version': 1, 'call_prefixes': ['qms$']}), encoding='utf-8')
        catalog = load({'framework_catalog': str(custom)})
        self.assertEqual(classify("calendar.event('X');", catalog)[0], 'framework')
        custom.write_text(json.dumps({'version': 1, 'call_prefixes': ['qms$'], 'forms_runtime_calls': {}}), encoding='utf-8')
        catalog = load({'framework_catalog': str(custom)})
        self.assertEqual(classify("calendar.event('X');", catalog), ('own', ['calendar.event']))
        for pattern in ('*.EVENT', 'A.B.C', 'CAL ENDAR.*', ''):
            with self.subTest(pattern=pattern), self.assertRaises(MigrationError):
                custom.write_text(json.dumps({'version': 1, 'forms_runtime_calls': {pattern: 'x'}}), encoding='utf-8')
                load({'framework_catalog': str(custom)})
        default = load({})
        self.assertEqual(runtime_call('Calendar.Event', default)[0], 'CALENDAR.*')
        self.assertIsNone(runtime_call('APP.CALENDAR.EVENT', default))
        self.assertEqual([c['kind'] for c in forms_calls("calendar.show; web.show_document('u'); host('x');", default)],
                         ['catalog', 'ui', 'server'])
        self.assertIsNone(forms_calls("IF :B.X = 1 THEN go_item('B.Y'); END IF;", default))
        self.assertIsNone(forms_calls("go_item(pkg.target);", default))

    def test_discovery_and_dictionary_skip_forms_runtime_calls(self):
        out = self.generate()
        data = build_map([('rt.xml', ET.fromstring(fixture()))], load({}))
        kinds = {c['name']: c['kind'] for code in data['code'] for c in code['calls']}
        self.assertEqual(kinds['CALENDAR.EVENT'], 'forms_runtime')
        self.assertEqual((kinds['WEB.SHOW_DOCUMENT'], kinds['SET_ITEM_INSTANCE_PROPERTY']), ('forms_builtin', 'forms_builtin'))
        _, routines = references([out], load({})['call_prefixes'])
        self.assertFalse(routines & {'CALENDAR.EVENT', 'WEB.SHOW_DOCUMENT', 'SET_ITEM_INSTANCE_PROPERTY', 'HOST'})

    def test_generated_java_compiles(self):
        if not shutil.which('java'):
            self.skipTest('Java 11+ compiler required')
        out = self.generate(config={'backend_live': True})
        sources = list(out.glob('backend/**/*.java')) + write_stubs(self.root / 'stubs')
        args = self.root / 'sources.args'
        args.write_text('\n'.join('"' + str(p).replace(chr(92), '/') + '"' for p in sources))
        result = subprocess.run(['java', 'com.sun.tools.javac.Main', '--release', '11', '-encoding', 'UTF-8',
                                 '-d', str(self.root / 'classes'), '@' + str(args)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()

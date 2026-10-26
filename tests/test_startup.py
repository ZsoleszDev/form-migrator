"""The form's start-up code in the web application: only its database work reaches the server and the Java assistant.

PRE-FORM / WHEN-NEW-FORM-INSTANCE are Forms events; a web screen opens in its initial state. Constants and item
properties become the screen's opening state, Forms plumbing (Headstart dispatch, cursor style, session NLS)
goes, Forms-only state (record groups, package variables) is listed for the developer, and only SQL, database
routines and what cannot be decided statically stay for onFormInit (backend_trigger_mode=ai).
"""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from frm_forms import backend_assistant as engine, framework, startup
from frm_forms.cli import main
from java_support import COMPANY_IMPORTS, trigger_mode

setUpModule, tearDownModule = trigger_mode('ai')

ROOT = Path(__file__).resolve().parents[1]
REPLICA = ROOT / 'tests' / 'fixtures' / 'felmeres_replika_fmb.xml'
CATALOG = framework.load({})


def triage(source, units=()):
    return startup.triage(source, CATALOG, units)


class TriageTests(unittest.TestCase):
    def test_plumbing_and_screen_state_leave_nothing_for_the_server(self):
        result = triage("BEGIN qms$event_form('PRE-FORM'); END;\n"
                        "SET_APPLICATION_PROPERTY(CURSOR_STYLE, 'DEFAULT');\n"
                        "FORMS_DDL('ALTER SESSION SET NLS_DATE_FORMAT = ''YYYY.MM.DD''');\n"
                        ":CTRL.MODUS := 'U';\n:GLOBAL.APP := 'RENDELES';\nDEFAULT_VALUE('2', 'GLOBAL.SZINT');\n"
                        "SET_ITEM_PROPERTY('CTRL.EV', ENABLED, PROPERTY_FALSE);\nGO_BLOCK('PARTNER');\nEXECUTE_QUERY;")
        self.assertEqual(result['backend'], '')
        self.assertEqual(result['values'], {'CTRL.MODUS': 'U'})
        self.assertEqual(result['globals'], {'GLOBAL.APP': 'RENDELES', 'GLOBAL.SZINT': '2'})
        self.assertEqual(result['properties'], [{'item': 'CTRL.EV', 'property': 'ENABLED', 'value': 'PROPERTY_FALSE'}])
        self.assertEqual(result['queries'], ['PARTNER'])
        self.assertEqual([d['reason'] for d in result['dropped']],
                         [startup.DROP_FRAMEWORK, startup.DROP_PRESENTATION, startup.DROP_SESSION, startup.DROP_PRESENTATION])

    def test_database_work_stays_with_what_it_reads(self):
        result = triage("BEGIN\n  :CTRL.MODUS := 'U';\n  :CTRL.EV := 2024;\n"
                        "  SELECT ertek INTO :CTRL.NEV FROM param WHERE kod = :CTRL.MODUS;\n"
                        "  SET_ITEM_PROPERTY('CTRL.NEV', UPDATE_ALLOWED, PROPERTY_FALSE);\nEND;")
        # MODUS is read by the query: its constant stays there too; EV is only the screen's.
        self.assertIn(":CTRL.MODUS := 'U';", result['backend'])
        self.assertIn('SELECT ertek INTO :CTRL.NEV', result['backend'])
        self.assertNotIn('2024', result['backend'])
        self.assertNotIn('SET_ITEM_PROPERTY', result['backend'])
        self.assertEqual(result['values'], {'CTRL.MODUS': 'U', 'CTRL.EV': '2024'})

    def test_conditions_loops_and_unknown_calls_stay_whole(self):
        code = ("IF :GLOBAL.X IS NULL THEN\n  :GLOBAL.X := 'A';\nEND IF;\n"
                "FOR r IN (SELECT 1 FROM dual) LOOP NULL; END LOOP;\nnaplo_pkg.init('APP');")
        result = triage(code)
        self.assertEqual(result['backend'], code)
        self.assertEqual(result['globals'], {})  # a conditional value is not the screen's constant

    def test_an_if_of_plumbing_only_goes(self):
        result = triage("IF :SYSTEM.MODE = 'NORMAL' THEN\n  SYNCHRONIZE;\n  qms$event_form('X');\nEND IF;")
        self.assertEqual(result['backend'], '')
        self.assertEqual(len(result['dropped']), 2)

    def test_a_computed_declaration_keeps_its_block(self):
        code = "DECLARE\n  v NUMBER := aktualis_ev();\nBEGIN\n  :CTRL.MODUS := 'U';\nEND;"
        self.assertEqual(triage(code)['backend'], "DECLARE\n  v NUMBER := aktualis_ev();\nBEGIN\n  NULL;\nEND;")

    def test_record_groups_and_package_state_are_notes(self):
        result = triage("DECLARE\n  rg RECORDGROUP;\n  c GROUPCOLUMN;\nBEGIN\n  app_pkg.aktiv := FALSE;\n"
                        "  rg := CREATE_GROUP('RG');\n  c := ADD_GROUP_COLUMN(rg, 'KOD', CHAR_COLUMN, 10);\n"
                        "  ADD_GROUP_ROW(rg, 1);\n  SET_GROUP_CHAR_CELL(c, 1, 'U');\nEND;")
        self.assertEqual(result['backend'], '')
        self.assertEqual([n['reason'] for n in result['notes']], [startup.NOTE_PACKAGE] + [startup.NOTE_GROUP] * 4)

    def test_what_is_not_certain_stays(self):
        # FORMS_DDL with DDL, the form's own unit shadowing a built-in, a non-constant property: all the server's.
        for code in ("FORMS_DDL('CREATE TABLE t (x NUMBER)');", 'message(\'kezdés\');',
                     "SET_ITEM_PROPERTY('CTRL.EV', ENABLED, v_flag);", ':CTRL.EV := aktualis_ev();'):
            with self.subTest(code=code):
                units = {'MESSAGE'} if code.startswith('message') else ()
                self.assertEqual(triage(code, units)['backend'], code)
        self.assertEqual(triage('BEGIN this is not ( PL/SQL; END')['backend'], 'BEGIN this is not ( PL/SQL; END')


def migrate(root: Path, source: Path, module: str) -> Path:
    config = root / (module + '.json')
    config.write_text(json.dumps({'java_company_imports': COMPANY_IMPORTS, 'backend_trigger_mode': 'ai', 'backend_live': True,
                                  'screen_window_selection': 'all', 'screen_primary_window_auto': True}))
    out = root / module
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert main(['migrate', str(source), '--out', str(out), '--screen', '--module', module, '--config', str(config)]) == 0
    return out


SCREEN_ONLY = '''<Module><FormModule Name="KEZDO"><Coordinate CoordinateSystem="Real" RealUnit="Pixel"/>
 <Trigger Name="PRE-FORM" TriggerText="qms$event_form('PRE-FORM');&#10;SET_APPLICATION_PROPERTY(CURSOR_STYLE, 'DEFAULT');"/>
 <Trigger Name="WHEN-NEW-FORM-INSTANCE" TriggerText=":CTRL.MODUS := 'U';&#10;SET_ITEM_PROPERTY('CTRL.MODUS', ENABLED, PROPERTY_FALSE);&#10;:GLOBAL.APP := 'KEZDO';"/>
 <Block Name="CTRL" DatabaseDataBlock="false">
  <Item ItemType="Text Item" Name="MODUS" DataType="Char" MaximumLength="1" CanvasName="C" XPosition="10" YPosition="10" Width="40" Height="20"/>
  <Item ItemType="Text Item" Name="NEV" DataType="Char" MaximumLength="30" CanvasName="C" XPosition="60" YPosition="10" Width="100" Height="20"/>
 </Block><Canvas Name="C" CanvasType="Content" WindowName="W"/><Window Name="W"/>
</FormModule></Module>'''


class StartupMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('FRM_JAVA_IMPORT_MAP', '-')
        os.environ.setdefault('FRM_JAVA_VARIABLE_MAP', '-')
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.replica = migrate(cls.root, REPLICA, 'rendeles')
        form = cls.root / 'kezdo_fmb.xml'
        form.write_text(SCREEN_ONLY, encoding='utf-8')
        cls.kezdo = migrate(cls.root, form, 'kezdo')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_start_up_code_without_database_work_has_no_endpoint(self):
        plan = json.loads((self.kezdo / 'analysis/backend-plan.json').read_text(encoding='utf-8'))
        self.assertNotIn('init', plan.get('api', {}))
        self.assertFalse(any(e.get('method') == 'onFormInit' for e in plan['endpoints']))
        self.assertNotIn('onFormInit', [t['method'] for t in engine.prepare_workspace(self.kezdo)['tasks']])
        screen = next((self.kezdo / 'frontend').rglob('*.component.ts')).read_text(encoding='utf-8')
        self.assertIn("formControlName: 'modus', labelText: 'MODUS', col: '3', disabled: true, maxLenght: 1, startValue: 'U' }", screen)
        self.assertNotIn('actionOnforminit', screen)
        notes = (self.kezdo / 'frontend/kezdo/MIGRATION_NOTES.md').read_text(encoding='utf-8')
        self.assertIn('nincs indítási végpont: az indítókódban nincs adatbázismunka.', notes)
        self.assertIn("| `qms$event_form('PRE-FORM');` | keretrendszer-hívás", notes)

    def test_the_java_assistant_gets_only_the_database_work(self):
        model = json.loads((self.replica / 'analysis/form.ir.json').read_text(encoding='utf-8'))
        self.assertEqual(model['init_plan']['triggers'], ['RENDELES:WHEN-NEW-FORM-INSTANCE'])  # PRE-FORM: nothing for the server
        task = next(t for t in engine.prepare_workspace(self.replica)['tasks'] if t['method'] == 'onFormInit')
        packet = json.loads((self.replica / engine.AREA / 'tasks' / (task['id'] + '.json')).read_text(encoding='utf-8'))
        code = '\n'.join(s['source'] for s in packet['sources'])
        self.assertIn('FROM param_tabla', code)
        for gone in ('qms$event_form', 'SET_APPLICATION_PROPERTY', 'ALTER SESSION', 'CREATE_GROUP', 'SET_ITEM_PROPERTY', "MODUS := 'U'",
                     'ank_ctrl', 'ank_menu'):  # the company's Forms-only UI libraries are catalogued (forms_runtime_calls)
            self.assertNotIn(gone, code)
        self.assertIn('naplo_init( :GLOBAL.CG$APP );', code)  # the log entry in the database stays, with the global it reads
        self.assertEqual(packet['pending_tables'], ['PARAM_TABLA'])

    def test_the_screen_opens_in_the_state_the_start_up_code_left(self):
        screen = next((self.replica / 'frontend').rglob('*.component.ts')).read_text(encoding='utf-8')
        self.assertIn("formControlName: 'modus', labelText: 'Mód', col: '2', maxLenght: 1, startValue: 'U' }", screen)
        self.assertIn("formControlName: 'ev', labelText: 'Év', col: '3', readonly: true }", screen)
        # :GLOBAL.CG$APP is the start-up code's constant: the PARTNER search sends it (Forms: its WHERE reads it).
        self.assertIn("globalCgApp: /* frm-input:", screen)
        self.assertIn("('RENDELES') /* /frm-input:", screen)
        notes = (self.replica / 'frontend/rendeles/MIGRATION_NOTES.md').read_text(encoding='utf-8')
        self.assertIn("| `rg_id := CREATE_GROUP('RG_ALLAPOT'); gc_kod := ADD_GROUP_COLUMN(", notes)  # one row per record group


if __name__ == '__main__':
    unittest.main()

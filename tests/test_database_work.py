"""Only database work becomes Java (backend_trigger_mode=ai): a button that works only on the screen is the screen's.

database_work.evidence says why a trigger needs the database - SQL, a database routine or package element, the
block's SQL (DEFAULT_WHERE), a Forms built-in that reaches the server - looking into the form's own program units
and the key trigger a DO_KEY starts. A button without such work gets no Java endpoint and no task for the Java
assistant: the screen runs its recognised steps (field checks, constant values, queries), the rest is the
developer's on the screen.
"""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from frm_forms import backend_assistant as engine, database_work, framework
from frm_forms.cli import main
from java_support import COMPANY_IMPORTS, trigger_mode
from screen_support import screen_method

setUpModule, tearDownModule = trigger_mode('ai')

ROOT = Path(__file__).resolve().parents[1]
REPLICA = ROOT / 'tests' / 'fixtures' / 'felmeres_replika_fmb.xml'
CATALOG = framework.load({})


def unit(name, kind, text):
    return {'name': name, 'programunittype': kind, 'programunittext': text}  # as xmlmodel.props keys them


MODEL = {'triggers': [], 'program_units': [
    unit('FRISSIT', 'Procedure', "PROCEDURE frissit IS\nBEGIN\n  :CTRL.UTOLSO := :CTRL.MODUS;\n  GO_BLOCK('RENDELES');\nEND;"),
    unit('APP_PKG', 'Package Spec', 'PACKAGE app_pkg IS\n  g_aktiv BOOLEAN;\n  FUNCTION osszeg RETURN NUMBER;\nEND;'),
    unit('APP_PKG', 'Package Body', 'PACKAGE BODY app_pkg IS\n  FUNCTION osszeg RETURN NUMBER IS v NUMBER;\n  BEGIN\n'
                                    '    SELECT SUM(x) INTO v FROM t;\n    RETURN v;\n  END;\nEND;')]}
KEY_COMMIT = {'id': 'F:KEY-COMMIT', 'event': 'KEY-COMMIT', 'block': None, 'item': None,
              'source': "IF ank_jog.irhat THEN\n  COMMIT_FORM;\n  ank_naplo.mentes(:RENDELES.ID);\nEND IF;"}


def evidence(code, model=MODEL):
    return database_work.evidence(code, model, CATALOG, 'CTRL', 'PB')


class EvidenceTests(unittest.TestCase):
    def test_screen_work_needs_no_database(self):
        code = ("BEGIN qms$event_item('WHEN-BUTTON-PRESSED'); END;\nBEGIN\n  VALIDATE(ITEM_SCOPE);\n"
                "  IF NOT FORM_SUCCESS THEN RAISE FORM_TRIGGER_FAILURE; END IF;\n  :CTRL.MODUS := 'K';\n"
                "  DEFAULT_VALUE('2024', 'GLOBAL.EV');\n  :CTRL.EV := TO_NUMBER(:GLOBAL.EV);\n  MESSAGE('Kész');\n"
                "  GO_BLOCK('RENDELES');\n  EXECUTE_QUERY;\nEND;")
        self.assertEqual(evidence(code), [])
        # The form's own procedure and package variables are Forms state, not the database.
        self.assertEqual(evidence('frissit;\napp_pkg.g_aktiv := TRUE;\nIF app_pkg.g_aktiv THEN NULL; END IF;'), [])
        # DO_KEY without a key trigger: the built-in itself, the screen's save.
        self.assertEqual(evidence("DO_KEY('COMMIT_FORM');"), [])

    def test_sql_routines_and_the_blocks_sql_are_database_work(self):
        self.assertEqual(evidence('SELECT nev INTO :CTRL.NEV FROM vevo WHERE kod = :CTRL.KOD;'), ['SQL: SELECT … INTO'])
        # The parser does not take DML: what the code names is read lexically.
        self.assertEqual(evidence("UPDATE rendeles SET statusz = 'L' WHERE id = :RENDELES.ID;"), ['SQL: UPDATE'])
        self.assertEqual(evidence('naplo_pkg.ir(:CTRL.KOD);'), ['adatbázis-rutin: NAPLO_PKG.IR'])
        # A parameterless database function in a condition is no call in the lexical map: the parser finds it.
        self.assertEqual(evidence('IF jog_pkg.irhat THEN :CTRL.X := 1; END IF;'), ['adatbázis-függvény vagy csomagelem: JOG_PKG.IRHAT'])
        self.assertEqual(evidence(':CTRL.X := app_pkg.osszeg;'), ['SQL: SELECT'])  # the form's package function: its SQL
        self.assertEqual(evidence("SET_BLOCK_PROPERTY('RENDELES', DEFAULT_WHERE, 'statusz = ''U''');\nGO_BLOCK('RENDELES');\n"
                                  'EXECUTE_QUERY;'), ['a blokk lekérdezésének SQL-je: SET_BLOCK_PROPERTY DEFAULT_WHERE'])
        self.assertEqual(evidence("FORMS_DDL('DELETE FROM t');"), ['Forms szerveroldali beépített hívás: FORMS_DDL'])

    def test_a_do_key_runs_its_key_trigger(self):
        model = dict(MODEL, triggers=[KEY_COMMIT])
        self.assertEqual(evidence("DO_KEY('COMMIT_FORM');", model),
                         ['adatbázis-függvény vagy csomagelem: ANK_JOG.IRHAT', 'adatbázis-rutin: ANK_NAPLO.MENTES'])

    def test_a_doubt_keeps_the_code_on_the_server(self):
        self.assertEqual(evidence(':CTRL.X := aktualis_ev;'), ['ismeretlen név (adatbázis-függvény lehet): AKTUALIS_EV'])
        found = evidence('BEGIN this is not ( PL/SQL; END;')
        self.assertTrue(found and found[0].startswith('a kód nem elemezhető teljesen'), found)


def migrate(root: Path, source: Path, module: str) -> Path:
    config = root / (module + '.json')
    config.write_text(json.dumps({'java_company_imports': COMPANY_IMPORTS, 'backend_trigger_mode': 'ai', 'backend_live': True,
                                  'screen_window_selection': 'all', 'screen_primary_window_auto': True}))
    out = root / module
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert main(['migrate', str(source), '--out', str(out), '--screen', '--module', module, '--config', str(config)]) == 0
    return out


class ScreenButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.out = migrate(Path(cls.temp.name), REPLICA, 'rendeles')
        cls.model = json.loads((cls.out / 'analysis/form.ir.json').read_text(encoding='utf-8'))
        cls.plan = json.loads((cls.out / 'analysis/backend-plan.json').read_text(encoding='utf-8'))
        cls.tasks = [t['method'] for t in engine.prepare_workspace(cls.out)['tasks']]

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def trigger(self, owner):
        return next(t for t in self.model['triggers'] if t['owner'] == owner and t['event'] == 'WHEN-BUTTON-PRESSED')

    def test_buttons_without_database_work_have_no_java(self):
        methods = {e['method'] for e in self.plan['endpoints']}
        for owner, method in (('CTRL.PB_KERES', 'onCtrlPbKeres'), ('CTRL.PB_ALAP', 'onCtrlPbAlap'), ('CTRL.PB_FRISSIT', 'onCtrlPbFrissit')):
            with self.subTest(owner=owner):
                self.assertTrue(self.trigger(owner).get('screen_only'))
                self.assertNotIn(method, methods)
                self.assertNotIn(method, self.tasks)
        service = (self.out / 'backend/DPS/RendelesServiceImpl.java').read_text(encoding='utf-8')
        self.assertNotIn('onCtrlPbKeres', service)
        # The buttons with database work stay the Java assistant's, with the reason.
        for owner, method in (('CTRL.PB_LEZAR', 'onCtrlPbLezar'), ('CTRL.PB_UJRASZAMOL', 'onCtrlPbUjraszamol'),
                              ('CTRL.PB_MENT', 'onCtrlPbMent')):
            with self.subTest(owner=owner):
                self.assertIn(method, self.tasks)
                self.assertTrue(self.trigger(owner)['database_work'])
        self.assertEqual(self.trigger('CTRL.PB_UJRASZAMOL')['database_work'], ['SQL: SELECT'])

    def test_the_screen_runs_the_recognised_steps(self):
        keres = screen_method(self.out, 'onPbKeresClick')
        self.assertIn("if (this.migrationInvalid('ctrl')) {", keres)
        self.assertIn("this.forms['ctrl']?.get('modus')?.setValue('K');", keres)
        self.assertIn('this.rendelesList(0, 1).subscribe(res => {', keres)
        self.assertEqual(self.trigger('CTRL.PB_KERES')['status'], 'converted')
        # Not recognised (a :GLOBAL default, a condition on the cursor block): the developer's, on the screen.
        alap = self.trigger('CTRL.PB_ALAP')
        self.assertEqual((alap['status'], alap['target']), ('review', 'frontend'))
        self.assertTrue(alap['reason'].startswith('Nincs benne adatbázismunka, ezért nincs Java-végpontja: a képernyőn kell megírni'))
        self.assertIn("DEFAULT_VALUE('2024', 'GLOBAL.EV');", screen_method(self.out, 'onPbAlapClick'))
        # The code it runs is there too, where the developer ports it: the form's own procedure.
        frissit = screen_method(self.out, 'onPbFrissitClick')
        self.assertIn('// -- A form saját programegysége: BLOKK_FRISSIT', frissit)
        self.assertIn("//   IF :SYSTEM.CURSOR_BLOCK = 'RENDELES' THEN", frissit)
        issue = next(i for i in self.model['issues'] if i['detail'].startswith('CTRL.PB_ALAP:WHEN-BUTTON-PRESSED: '))
        self.assertEqual(issue['scope'], 'frontend')

    def test_the_coverage_says_where_the_code_runs(self):
        rows = {r['id']: r for r in json.loads((self.out / 'analysis/runtime-coverage.json').read_text(encoding='utf-8'))['triggers']}
        keres = rows['CTRL.PB_KERES:WHEN-BUTTON-PRESSED']
        self.assertEqual((keres['status'], keres['execution']), ('frontend', 'Gombnyomás → validate, setValue, executeQuery: RENDELES.'))
        self.assertEqual(rows['CTRL.PB_ALAP:WHEN-BUTTON-PRESSED']['status'], 'manual')
        for trigger in ('RENDELES:PRE-FORM', 'RENDELES:WHEN-NEW-FORM-INSTANCE'):
            self.assertEqual(rows[trigger]['status'], 'frontend')
        self.assertEqual(rows['RENDELES:WHEN-NEW-FORM-INSTANCE']['gaps'],
                         ['Nem migrált adatbázismunka (Forms-indítás): a képernyő MIGRATION_NOTES.md-je listázza.'])


if __name__ == '__main__':
    unittest.main()

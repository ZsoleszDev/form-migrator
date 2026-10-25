"""4.15: a screen step in the middle of a button's code (EXECUTE_QUERY, CLEAR_BLOCK ...) as a screen point.

Before, the web screen could carry out EXECUTE_QUERY and the like only as the last step, so a button
that read the queried values afterwards stayed manual work. Now the request stops at the step
(FRM_RESUME command) and the button can be called again with FRM.RESUME: the statements before the point are
skipped, the code continues with the new values. Since 4.26 the screen does not run the commands itself: the
button's method lists them (TODO) with the query method to call.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from java_support import COMPANY_IMPORTS, write_stubs
from screen_support import component
from frm_forms.cli import main
from frm_forms import commit_points as cp
from frm_forms.plsql import Unsupported
from frm_forms.plsql_passthrough import prepare
from frm_forms.plsql_structure import parse
from frm_forms.survey import mid_code_steps

ROOT = Path(__file__).resolve().parents[1]
REPLICA = ROOT / 'tests' / 'fixtures' / 'felmeres_replika_fmb.xml'
ITEMS = {'B': {'ID': {'type': 'number'}, 'NAME': {'type': 'text'}}, 'CTRL': {'X': {'type': 'text'}}}


def button(source, points=True, units=None):
    return prepare(source, block='CTRL', items=ITEMS, units=units or {}, prefixes=('qms$',), other_blocks=True,
                   parameters=True, transaction=True, ui=True, form='F', trigger_item='CTRL.PB', commit_points=points)


class BackendTests(unittest.TestCase):
    def test_the_request_stops_at_the_step_and_resumes_after_it(self):
        r = button("go_block('B'); execute_query(NO_VALIDATE); :CTRL.X := :B.NAME;")
        self.assertEqual((r['screen_points'], r['commit_points']), (1, 0))
        self.assertEqual(r['commands'], ['GO_BLOCK', 'EXECUTE_QUERY'])
        self.assertIn("IF frm_resume NOT IN (1) THEN\nfrm_cmd('GO_BLOCK', 'B');\nEND IF;", r['tail'])
        self.assertIn("frm_screen_point(1, 'EXECUTE_QUERY');", r['tail'])
        self.assertIn("frm_cmd('FRM_RESUME', TO_CHAR(frm_commit_at));", r['tail'])
        self.assertNotIn('ROLLBACK', r['tail'])  # the work before the point stays: no save follows
        sources = [b['source'] for b in r['binds']]
        self.assertIn('FRM.RESUME', sources)
        self.assertNotIn('FRM.COMMIT', sources)
        self.assertIn('PROCEDURE frm_screen_point(p_point PLS_INTEGER, p_step VARCHAR2)', r['sql'])
        self.assertNotIn('frm_commit_form', r['sql'])
        self.assertTrue(any('képernyőpont (1)' in n for n in r['notes']))
        parse(r['sql'])

    def test_screen_and_commit_points_are_numbered_together(self):
        r = button("create_record; :B.NAME := 'új'; commit_form; :CTRL.X := 'kész';")
        self.assertEqual((r['screen_points'], r['commit_points']), (1, 1))
        self.assertIn("frm_screen_point(1, 'CREATE_RECORD');", r['tail'])
        self.assertIn('frm_commit_form(2);', r['tail'])
        self.assertIn("IF frm_point_kind = 'STEP' THEN", r['tail'])
        self.assertIn('FRM.COMMIT', [b['source'] for b in r['binds']])
        parse(r['sql'])

    def test_what_cannot_resume_stays_manual_with_the_reason(self):
        with self.assertRaisesRegex(Unsupported, 'EXECUTE_QUERY a kód közepén: a\\(z\\) V helyi változó'):
            button("DECLARE v VARCHAR2(10); BEGIN v := :CTRL.X; execute_query; :CTRL.X := v; END;")
        with self.assertRaisesRegex(Unsupported, 'EXECUTE_QUERY a kód közepén: ciklusban'):
            button("FOR i IN 1 .. 2 LOOP execute_query; END LOOP; :CTRL.X := 'x';")
        with self.assertRaisesRegex(Unsupported, 'DELETE_RECORD után további'):
            button("delete_record; :CTRL.X := 'x';")  # where the cursor goes after it is not certain
        with self.assertRaisesRegex(Unsupported, 'CALL_FORM után további'):
            button("call_form('MASIK'); :CTRL.X := 'x';")  # navigates away
        with self.assertRaisesRegex(Unsupported, 'EXECUTE_QUERY után további'):
            button("execute_query; :CTRL.X := :B.NAME;", points=False)  # start-up and save-chain code

    def test_the_survey_counts_the_remaining_refusals_by_step(self):
        self.assertEqual(mid_code_steps(['Átfuttatás az adatbázisban sem lehetséges: EXECUTE_QUERY a kód közepén: ciklusban']),
                         {'EXECUTE_QUERY'})

    def test_placeholders(self):
        self.assertEqual(cp.screen_placeholder('CLEAR_BLOCK'), "frm_screen_point(FRM_POINT, 'CLEAR_BLOCK')")
        self.assertEqual(cp.purpose("frm_commit_form(FRM_POINT); frm_screen_point(FRM_POINT, 'CLEAR_BLOCK');"),
                         'COMMIT_FORM, CLEAR_BLOCK a kód közepén')


def replica_variant() -> str:
    """The survey replica with the refusals of the real survey: a query in the middle of a button, a local
    package with an initialisation part and a nested function."""
    xml = REPLICA.read_text(encoding='utf-8')
    xml = xml.replace('TriggerText="blokk_frissit;"', 'TriggerText="GO_BLOCK(&apos;TETEL&apos;);&amp;#10;EXECUTE_QUERY;'
                      '&amp;#10;:CTRL.UTOLSO := :TETEL.CIKK;"', 1)
    body = xml[xml.index('ProgramUnitText="PACKAGE BODY rendeles_pkg'):]
    body = body[:body.index('"', len('ProgramUnitText="')) + 1]
    lines = ['PACKAGE BODY rendeles_pkg IS', '  PROCEDURE ujraszamol IS', '    v_sum NUMBER;',
             '    FUNCTION kerekit(p NUMBER) RETURN NUMBER IS BEGIN RETURN ROUND(p, 2); END;', '  BEGIN',
             '    SELECT SUM(mennyiseg) INTO v_sum FROM rendeles_tetel WHERE rendeles_id = :RENDELES.ID;',
             '    :RENDELES.OSSZEG := kerekit(v_sum);', '    g_utolso := v_sum;',
             '    MESSAGE(&apos;Újraszámolva: &apos; || v_sum);', '  END ujraszamol;', 'BEGIN', '  g_utolso := 0;',
             'END rendeles_pkg;']
    return xml.replace(body, 'ProgramUnitText="' + '&amp;#10;'.join(lines) + '"', 1)


class ReplicaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('FRM_JAVA_IMPORT_MAP', '-')
        os.environ.setdefault('FRM_JAVA_VARIABLE_MAP', '-')  # nor a developer's own java-variables.json
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        form = cls.root / 'valtozat_fmb.xml'
        form.write_text(replica_variant(), encoding='utf-8')
        config = cls.root / 'config.json'
        config.write_text(json.dumps({'java_company_imports': COMPANY_IMPORTS, 'backend_live': True,
                                      'screen_window_selection': 'all', 'screen_primary_window_auto': True}))
        cls.out = cls.root / 'out'
        with contextlib.redirect_stdout(io.StringIO()):
            assert main(['migrate', str(form), '--out', str(cls.out), '--screen', '--module', 'rendeles',
                         '--config', str(config)]) == 0
        cls.plan = json.loads((cls.out / 'analysis/backend-plan.json').read_text(encoding='utf-8'))
        cls.service = (cls.out / 'backend/DPS/RendelesServiceImpl.java').read_text(encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def endpoint(self, owner):
        return next(e for e in self.plan['endpoints'] if e.get('operation') == 'action' and owner in str(e))

    def test_the_survey_refusals_became_working_endpoints(self):
        self.assertTrue(self.endpoint('PB_FRISSIT')['implemented'], self.endpoint('PB_FRISSIT'))
        self.assertTrue(self.endpoint('PB_UJRASZAMOL')['implemented'], self.endpoint('PB_UJRASZAMOL'))
        self.assertIn("frm_screen_point(1, 'EXECUTE_QUERY');", self.service)
        self.assertIn('-- RENDELES_PKG inicializálása', self.service)
        self.assertIn('FUNCTION kerekit(p NUMBER) RETURN NUMBER', self.service)

    def test_the_screen_method_sends_the_request(self):
        # 4.27: the button sends its request; carrying out FRM_RESUME (the screen step, then FRM.RESUME) is the developer's.
        screen = component(self.out)
        method = screen[screen.index('  protected onPbFrissitClick(): void {'):]
        method = method[:method.index('\n  }\n')]
        self.assertIn('this.actionOnctrlpbfrissit({', method)
        self.assertIn("TETEL: { CIKK: this.tetelSelection?.['cikk'] }", method)  # a table's value: its selected row
        self.assertNotIn('RESUME', method)

    def test_generated_java_compiles(self):
        if not shutil.which('java'):
            self.skipTest('Java 11+ compiler required')
        sources = list(self.out.glob('backend/**/*.java')) + write_stubs(self.root / 'stubs')
        args = self.root / 'sources.args'
        args.write_text('\n'.join('"' + str(p).replace(chr(92), '/') + '"' for p in sources))
        build = subprocess.run(['java', 'com.sun.tools.javac.Main', '--release', '11', '-encoding', 'UTF-8',
                                '-d', str(self.root / 'classes'), '@' + str(args)], capture_output=True, text=True, timeout=120)
        if 'Could not find or load main class' in build.stderr:
            self.skipTest('JDK (javac) required')
        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)


if __name__ == '__main__':
    unittest.main()

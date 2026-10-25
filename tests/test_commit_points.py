"""4.14: DO_KEY runs the form's own KEY trigger; COMMIT_FORM followed by more code is a commit point.

The button stops at the commit point (its work rolled back), the screen saves - the commit endpoint
runs the code up to the point again in the commit's transaction and checks the state - and the
button resumes after the point (FRM.RESUME).
"""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from java_support import COMPANY_IMPORTS, write_stubs
from frm_forms.cli import main
from frm_forms.commit_points import transform
from frm_forms.plsql import Unsupported
from frm_forms.plsql_passthrough import prepare
from screen_support import screen_source

ROOT = Path(__file__).resolve().parents[1]
REPLICA = ROOT / 'tests' / 'fixtures' / 'felmeres_replika_fmb.xml'
ITEMS = {'B': {'ID': {'type': 'number'}, 'NAME': {'type': 'text'}}, 'CTRL': {'X': {'type': 'text'}}}
KEY_COMMIT = {'id': 'F:KEY-COMMIT', 'event': 'KEY-COMMIT', 'block': None, 'item': None, 'hierarchy': 'OVERRIDE',
              'source': "BEGIN IF :B.ID IS NOT NULL THEN COMMIT_FORM; log_save(:B.ID); :CTRL.X := 'MENTVE'; END IF; END;"}


def button(source, keys=(KEY_COMMIT,), **options):
    triggers = {}
    for key in keys:
        triggers.setdefault(key['event'], []).append(key)
    overrides = {'KEY-COMMIT': 'COMMIT_FORM', 'KEY-EXEQRY': 'EXECUTE_QUERY', 'KEY-LISTVAL': 'LIST_VALUES'}
    options.setdefault('trigger_item', 'CTRL.PB')
    return prepare(source, block='CTRL', items=ITEMS, units=options.pop('units', {}), prefixes=('qms$',), other_blocks=True,
                   parameters=True, transaction=True, ui=True, form='F', commit_points=True, key_triggers=triggers,
                   key_overrides={overrides[k['event']] for k in keys}, **options)


class TransformTests(unittest.TestCase):
    def test_branches_to_the_point_are_taken_again_and_earlier_statements_skipped(self):
        body, count = transform("BEGIN\n  :a := 1;\n  IF x THEN\n    frm_commit_form(FRM_POINT);\n    y := 2;\n  END IF;\nEND;")
        self.assertEqual(count, 1)
        self.assertIn('IF frm_resume NOT IN (1) THEN\n  :a := 1;\n  END IF;', body)
        self.assertIn('IF (frm_resume IN (1) OR (frm_resume NOT IN (1) AND (x))) THEN', body)
        self.assertIn('frm_commit_form(1);\n    y := 2;', body)

    def test_points_in_sequence_and_in_else_branches_get_their_own_numbers(self):
        body, count = transform('BEGIN IF a THEN frm_commit_form(FRM_POINT); ELSIF b THEN NULL; ELSE frm_commit_form(FRM_POINT);'
                                ' END IF; z := 1; frm_commit_form(FRM_POINT); w := 2; END;')
        self.assertEqual(count, 3)
        self.assertIn('ELSIF (frm_resume NOT IN (1, 2) AND (b)) THEN', body)
        self.assertIn('ELSE frm_commit_form(2);', body)
        self.assertIn('IF frm_resume NOT IN (3) THEN\nz := 1;\nEND IF;', body)

    def test_enclosing_handlers_let_the_point_through(self):
        body, _ = transform('BEGIN BEGIN frm_commit_form(FRM_POINT); EXCEPTION WHEN OTHERS THEN NULL; END; END;')
        self.assertIn('EXCEPTION\n  WHEN frm_commit_pending THEN\n    RAISE; WHEN OTHERS', body)

    def test_refused_where_the_continuation_cannot_be_followed(self):
        for source, reason in [
            ('BEGIN FOR r IN (SELECT 1 FROM dual) LOOP frm_commit_form(FRM_POINT); END LOOP; END;', 'ciklusban'),
            ('BEGIN NULL; EXCEPTION WHEN OTHERS THEN frm_commit_form(FRM_POINT); END;', 'kivételkezelőben'),
            ('DECLARE v NUMBER; BEGIN v := 1; frm_commit_form(FRM_POINT); log(v); END;', 'V helyi változó'),
            ('BEGIN GOTO x; frm_commit_form(FRM_POINT); END;', 'GOTO'),
        ]:
            with self.subTest(reason=reason), self.assertRaisesRegex(Unsupported, reason):
                transform(source)
        # A constant and a variable first set after the point are fine.
        transform('DECLARE c CONSTANT NUMBER := 1; v NUMBER; BEGIN frm_commit_form(FRM_POINT); v := c; log(v); END;')


class PrepareTests(unittest.TestCase):
    def test_do_key_embeds_the_key_trigger_and_its_commit_form_is_a_commit_point(self):
        r = button("do_key('COMMIT_FORM');")
        self.assertEqual(r['commit_points'], 1)
        self.assertIn("IF (frm_resume IN (1) OR (frm_resume NOT IN (1) AND (nv_", r['sql'])
        self.assertIn('frm_commit_form(1);', r['sql'])
        self.assertIn("FRM.RESUME", [b['source'] for b in r['binds']])
        self.assertIn("frm_cmd('FRM_COMMIT', TO_CHAR(frm_commit_at), frm_commit_state);", r['sql'])
        self.assertIn('IF frm_commit_mode IS NULL THEN\n      ROLLBACK TO SAVEPOINT frm_start;', r['sql'])
        self.assertTrue(any('F:KEY-COMMIT saját kódja beágyazva' in n for n in r['notes']))
        # The state compares the item values (numbers in a fixed format), never the request context.
        state = re.search(r'frm_commit_state := SUBSTR\((.*), 1, 32000\);', r['sql'])[1]
        self.assertIn("TO_CHAR(", state)
        self.assertNotIn(re.search(r'(nv_\w+) VARCHAR2\(32767\) := \?; -- FRM.RESUME', r['sql'])[1], state)

    def test_commit_form_last_stays_a_screen_command(self):
        r = button("do_key('COMMIT_FORM');", keys=[{**KEY_COMMIT, 'source': "message('Mentés'); commit_form;"}])
        self.assertEqual(r['commit_points'], 0)
        self.assertIn("frm_cmd('COMMIT_FORM')", r['sql'])
        # The same KEY trigger with code after DO_KEY: its COMMIT_FORM is no longer last.
        r = button("do_key('COMMIT_FORM'); :CTRL.X := 'kész';", keys=[{**KEY_COMMIT, 'source': "commit_form;"}])
        self.assertEqual(r['commit_points'], 1)

    def test_plain_commit_form_in_the_middle(self):
        r = button("commit_form; :CTRL.X := 'ok';", keys=())
        self.assertEqual(r['commit_points'], 1)
        r = button("commit_form;", keys=())
        self.assertEqual((r['commit_points'], r['commands']), (0, ['COMMIT_FORM']))

    def test_key_trigger_scope_hierarchy_and_recursion(self):
        block_key = {**KEY_COMMIT, 'id': 'B:KEY-COMMIT', 'block': 'B', 'source': 'NULL;'}
        with self.assertRaisesRegex(Unsupported, 'kurzor helyétől'):
            button("do_key('COMMIT_FORM');", keys=[block_key], trigger_item=None)
        own_block = {**KEY_COMMIT, 'id': 'CTRL:KEY-COMMIT', 'block': 'CTRL', 'source': "message('blokk');"}
        r = button("do_key('COMMIT_FORM');", keys=[KEY_COMMIT, own_block])
        self.assertTrue(any('CTRL:KEY-COMMIT' in n for n in r['notes']))  # the button's block wins over the form
        with self.assertRaisesRegex(Unsupported, 'Execution Hierarchy'):
            button("do_key('COMMIT_FORM');", keys=[{**KEY_COMMIT, 'hierarchy': 'BEFORE'}])
        with self.assertRaisesRegex(Unsupported, 'végtelen'):
            button("do_key('COMMIT_FORM');", keys=[{**KEY_COMMIT, 'source': "do_key('COMMIT_FORM');"}])

    def test_navigation_before_do_key_leaves_only_the_form_level_trigger_certain(self):
        block_key = {**KEY_COMMIT, 'id': 'B:KEY-COMMIT', 'block': 'B', 'source': "message('B');"}
        with self.assertRaisesRegex(Unsupported, 'kurzor helyétől'):
            button("go_block('B'); do_key('COMMIT_FORM');", keys=[KEY_COMMIT, block_key])
        r = button("go_block('B'); do_key('COMMIT_FORM');")
        self.assertTrue(any('F:KEY-COMMIT saját kódja beágyazva' in n for n in r['notes']))
        with self.assertRaisesRegex(Unsupported, 'LIST_VALUES lépést'):
            button("do_key('LIST_VALUES');", keys=[{**KEY_COMMIT, 'id': 'F:KEY-LISTVAL', 'event': 'KEY-LISTVAL', 'source': 'list_values;'}])

    def test_local_package_state_cannot_cross_the_point(self):
        units = {'PKG': {'kind': 'package', 'text': '', 'spec': 'PACKAGE pkg IS g NUMBER; PROCEDURE run; END pkg;',
                         'body': 'PACKAGE BODY pkg IS PROCEDURE run IS BEGIN g := 1; END run; END pkg;'}}
        with self.assertRaisesRegex(Unsupported, 'PKG helyi csomag'):
            button("pkg.run; commit_form; pkg.run;", keys=(), units=units)


def java_available():
    if not shutil.which('java'):
        return False
    probe = subprocess.run(['java', 'com.sun.tools.javac.Main', '-version'], capture_output=True, text=True)
    return 'Could not find or load main class' not in probe.stderr


class PreludeJavaTests(unittest.TestCase):
    def test_prelude_accepts_the_same_state_and_refuses_any_other(self):
        if not java_available():
            self.skipTest('JDK (javac) required')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tools = root / 'src' / 'CommonMigrateTools.java'
            tools.parent.mkdir()
            template = (ROOT / 'frm_forms/templates/CommonMigrateTools.java.tpl').read_text(encoding='utf-8')
            tools.write_text(template.replace('@@PACKAGE@@', 'hu.test.cl'), encoding='utf-8')
            check = root / 'src' / 'PreludeCheck.java'
            check.write_text('''import hu.test.cl.CommonMigrateTools.PlsqlValues;
import java.util.List;
import java.util.Map;
public class PreludeCheck {
    public static void main(String[] args) {
        var commands = List.of(List.of("GO_BLOCK", "B"), List.of("FRM_COMMIT", "1", "7\\u001dL"));
        var kept = PlsqlValues.prelude(commands, Map.of("FRM.COMMIT_POINT", "1", "FRM.COMMIT_STATE", "7\\u001dL"));
        if (!kept.equals(List.of(List.of("GO_BLOCK", "B")))) throw new AssertionError(kept.toString());
        for (var parameters : List.of(Map.of("FRM.COMMIT_POINT", "1", "FRM.COMMIT_STATE", "8\\u001dL"),
                                      Map.of("FRM.COMMIT_POINT", "2", "FRM.COMMIT_STATE", "7\\u001dL"))) {
            try {
                PlsqlValues.prelude(commands, parameters);
                throw new AssertionError("an other state must be refused: " + parameters);
            } catch (org.springframework.web.server.ResponseStatusException expected) {
                // HTTP 409: the commit's transaction rolls back
            }
        }
        try {
            PlsqlValues.prelude(List.of(List.of("GO_BLOCK", "B")), Map.of("FRM.COMMIT_POINT", "1"));
            throw new AssertionError("a run that never reached the point must be refused");
        } catch (org.springframework.web.server.ResponseStatusException expected) {
            // the code did not arrive at the commit point this time
        }
        System.out.println("prelude OK");
    }
}
''', encoding='utf-8')
            sources = [tools, check] + write_stubs(root / 'stubs')
            build = subprocess.run(['java', 'com.sun.tools.javac.Main', '--release', '11', '-encoding', 'UTF-8', '-d',
                                    str(root / 'classes'), *map(str, sources)], capture_output=True, text=True, timeout=120)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run(['java', '-cp', str(root / 'classes'), 'PreludeCheck'], capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn('prelude OK', run.stdout)


class ReplicaFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('FRM_JAVA_IMPORT_MAP', '-')
        os.environ.setdefault('FRM_JAVA_VARIABLE_MAP', '-')  # nor a developer's own java-variables.json
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        config = cls.root / 'config.json'
        config.write_text(json.dumps({'java_company_imports': COMPANY_IMPORTS, 'backend_live': True,
                                      'screen_window_selection': 'all', 'screen_primary_window_auto': True}))
        cls.out = cls.root / 'out'
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(['migrate', str(REPLICA), '--out', str(cls.out), '--screen', '--module', 'rendeles', '--config', str(config)])
        assert code == 0, code
        cls.service = (cls.out / 'backend/DPS/RendelesServiceImpl.java').read_text(encoding='utf-8')
        cls.screen = screen_source(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_commit_endpoint_runs_the_button_first(self):
        commit = self.service[self.service.index('public CommitResult commitForm'):]
        prelude = commit.index('runOnCtrlPbMent(actionValues, actionParameters')
        self.assertLess(prelude, commit.index('changesRendeles()'))
        self.assertIn('actionParameters.put("FRM.COMMIT", "POST");', commit)
        self.assertIn('commands.addAll(PlsqlValues.prelude(actionCommands, actionParameters));', commit)
        self.assertIn('public String action;', (self.out / 'backend/CL/RendelesDtos.java').read_text(encoding='utf-8'))

    def test_the_screen_leaves_the_save_point_to_the_developer(self):
        # 4.27: the button sends its request; the backend's FRM_COMMIT (save, then FRM.RESUME) is the developer's.
        # 4.35: the answer's written items, messages and Forms commands (COMMIT_FORM: save()) go to applyActionResult.
        method = self.screen[self.screen.index('  protected onPbMentClick(): void {'):]
        method = method[:method.index('\n  }\n')]
        self.assertIn('this.actionOnctrlpbment({', method)
        self.assertIn('.subscribe(res => this.applyActionResult(res));', method)
        self.assertIn("} else if (operation === 'COMMIT_FORM') {\n        this.save();", self.screen)
        self.assertNotIn('FRM', method.split('{', 1)[1])  # the backend's own protocol values are not sent
        self.assertIn('  protected save(): void {', self.screen)  # the Forms save of the screen is there to call

if __name__ == '__main__':
    unittest.main()

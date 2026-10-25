"""A working application from the survey patterns (tests/fixtures/felmeres_replika_fmb.xml).

The replica carries the constructs that kept 99% of the endpoints disabled in the survey:
Headstart start-up code, ON-INSERT/ON-UPDATE, WHERE/ORDER BY with subqueries and :GLOBAL,
a block without primary key, master-detail, alerts, a local package, :SYSTEM values.
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
from screen_support import screen_source
from frm_forms.cli import main
from frm_forms.plsql import Unsupported
from frm_forms.plsql_passthrough import prepare

ROOT = Path(__file__).resolve().parents[1]
REPLICA = ROOT / 'tests' / 'fixtures' / 'felmeres_replika_fmb.xml'
ITEMS = {'B': {'ID': {'type': 'number'}, 'NAME': {'type': 'text'}}, 'CTRL': {'X': {'type': 'text'}}}


def emulated(source, **options):
    options.setdefault('block', 'CTRL')
    return prepare(source, items=ITEMS, units=options.pop('units', {}), prefixes=('qms$',), other_blocks=True, parameters=True,
                   transaction=True, ui=True, form='F', **options)


class EmulationTests(unittest.TestCase):
    def test_builtins_become_commands_and_context(self):
        r = emulated("IF :SYSTEM.CURSOR_BLOCK = 'B' THEN set_item_property('B.NAME', ENABLED, PROPERTY_FALSE); END IF;"
                     " :GLOBAL.LAST := NAME_IN('B.NAME'); go_block('B'); execute_query;", trigger_item='CTRL.BTN')
        self.assertEqual(r['commands'], ['SET_ITEM_PROPERTY', 'GO_BLOCK', 'EXECUTE_QUERY'])
        self.assertIn("frm_cmd('SET_ITEM_PROPERTY', 'B.NAME', 'ENABLED', 'PROPERTY_FALSE')", r['sql'])
        self.assertEqual([b['source'] for b in r['globals']], ['GLOBAL.LAST'])
        self.assertIn('SYSTEM.CURSOR_BLOCK', [b['source'] for b in r['binds'] if b['parameter']])
        self.assertTrue(r['sql'].rstrip().endswith('? := frm_ui;\nEND;'))

    def test_steps_that_the_code_could_observe_must_be_last(self):
        with self.assertRaisesRegex(Unsupported, 'EXECUTE_QUERY után további'):
            emulated("go_block('B'); execute_query; :CTRL.X := :B.NAME;")
        # A sibling branch never runs after the command.
        self.assertEqual(emulated("IF :CTRL.X IS NULL THEN go_block('B'); execute_query; ELSE :CTRL.X := 'A'; END IF;")['commands'],
                         ['GO_BLOCK', 'EXECUTE_QUERY'])

    def test_alert_answers_rerun_the_code_and_pending_work_rolls_back(self):
        r = emulated("DECLARE a ALERT; n NUMBER; BEGIN a := FIND_ALERT('Q'); n := SHOW_ALERT(a);"
                     " IF n = ALERT_BUTTON1 THEN UPDATE t SET x = 1; END IF; END;")
        self.assertIn('a VARCHAR2(4000);', r['sql'])
        self.assertIn('SAVEPOINT frm_start', r['sql'])
        self.assertIn('ROLLBACK TO SAVEPOINT frm_start', r['sql'])
        self.assertIn('FRM.ALERTS', [b['source'] for b in r['binds']])

    def test_overridden_do_key_and_query_properties_stay_manual(self):
        with self.assertRaisesRegex(Unsupported, 'KEY-COMMIT'):
            emulated("do_key('COMMIT_FORM');", key_overrides={'COMMIT_FORM'})
        self.assertEqual(emulated("do_key('COMMIT_FORM');")['commands'], ['DO_KEY'])
        with self.assertRaisesRegex(Unsupported, 'DEFAULT_WHERE'):
            emulated("set_block_property('B', DEFAULT_WHERE, 'X = 1');")

    def test_keep_query_status_idiom_needs_no_runtime(self):
        r = prepare("SELECT name INTO :B.NAME FROM t WHERE id = :B.ID;"
                    " set_record_property(:SYSTEM.TRIGGER_RECORD, :SYSTEM.TRIGGER_BLOCK, STATUS, QUERY_STATUS);",
                    block='B', items=ITEMS, units={}, prefixes=('qms$',))
        self.assertEqual([b['source'] for b in r['binds']], ['B.NAME', 'B.ID'])
        with self.assertRaisesRegex(Unsupported, 'SET_RECORD_PROPERTY'):
            prepare("set_record_property(1, 'B', STATUS, CHANGED_STATUS);", block='B', items=ITEMS, units={}, prefixes=('qms$',))

    def test_local_package_is_embedded(self):
        units = {'PKG': {'kind': 'package', 'text': '', 'spec': 'PACKAGE pkg IS g NUMBER; PROCEDURE run; END pkg;',
                         'body': 'PACKAGE BODY pkg IS PROCEDURE run IS BEGIN g := 1; :B.NAME := \'x\'; helper; END run;'
                                 ' PROCEDURE helper IS BEGIN NULL; END; END pkg;'}}
        r = emulated('pkg.run;', units=units)
        self.assertEqual(r['units'], ['PKG'])
        self.assertIn('g NUMBER;', r['head'])  # package state before every subprogram of the block
        self.assertNotIn('PKG.RUN', r['tail'].upper())
        self.assertFalse(r['unresolved'])  # helper is the package's own member


class ReplicaTests(unittest.TestCase):
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
        cls.plan = json.loads((cls.out / 'analysis/backend-plan.json').read_text(encoding='utf-8'))
        cls.model = json.loads((cls.out / 'analysis/form.ir.json').read_text(encoding='utf-8'))
        cls.service = (cls.out / 'backend/DPS/RendelesServiceImpl.java').read_text(encoding='utf-8')
        cls.screen = screen_source(cls.out)  # the component and frm-forms-screen.ts (4.14)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_every_endpoint_runs_the_overridden_save_key_too(self):
        # 4.14: DO_KEY('COMMIT_FORM') embeds the form's own KEY-COMMIT, whose COMMIT_FORM is a commit point.
        off = [e['method'] for e in self.plan['endpoints'] if not e['implemented']]
        self.assertEqual(off, [])
        self.assertEqual(len(self.plan['endpoints']), 21)
        self.assertTrue(self.plan['api']['actions']['CTRL.PB_MENT']['commit_point'])
        self.assertIn('runOnCtrlPbMent(actionValues, actionParameters, blocks, globals, messages, actionCommands);', self.service)
        self.assertIn('this.actionOnctrlpbment({', self.screen)  # 4.27: the button sends its request; the save point is the developer's

    def test_where_order_by_and_rowid_run_like_forms(self):
        sql = ' '.join(self.service.replace('"\n', '').replace('+ "', '').split())  # the wrapped SQL literals joined
        self.assertIn('FROM RENDELES WHERE ( RENDELES.STATUSZ IN ( SELECT KOD FROM STATUSZ_KODTAR ) ) ORDER BY RENDELES.DATUM DESC', sql)
        self.assertIn('ROWIDTOCHAR(ROWID)', self.service)
        self.assertIn('ROWID = CHARTOROWID(:rowid)', sql)
        # Its own ON-INSERT does the DML: no ROWID to re-read the record by.
        self.assertIn('var saved = row;', self.service)
        criteria = self.plan['api']['blocks']['PARTNER']['operations']['search']['criteria']
        self.assertEqual([c['source'] for c in criteria], ['CTRL.SZURO', 'GLOBAL.CG$APP'])

    def test_on_triggers_replace_the_dml(self):
        self.assertIn('onInsertRendeles(row, context);', self.service)
        self.assertIn('onUpdateRendeles(row, context);', self.service)
        self.assertIn('checkDeleteMasterRendeles(current, context);', self.service)
        self.assertNotIn('INSERT INTO RENDELES (', self.service)  # the trigger does the INSERT

    def test_start_up_code_is_the_init_endpoint(self):
        self.assertEqual(self.model['init_plan']['status'], 'generated')
        self.assertEqual(self.plan['api']['init'], '@INIT')
        self.assertIn("    this.actionOnforminit({ blocks: {}, parameters: { 'GLOBAL.CG$APP': null } }).subscribe();\n  }",
                      self.screen)  # the constructor calls it
        self.assertIn("frm_group('CREATE_GROUP', 'RG_ALLAPOT')", self.service)

    def test_commit_chain_in_forms_order_with_the_master_key(self):
        commit = self.service[self.service.index('public CommitResult commitForm'):]
        order = [commit.index('changesRendeles()'), commit.index('changesTetel()'), commit.index('changesPartner()')]
        self.assertEqual(order, sorted(order))
        self.assertIn('row.rendelesId = saved.id;', commit)
        self.assertIn('PlsqlValues.number(values, "RENDELES", "ID")', commit)
        self.assertIn('<button pButton type="button" (click)="save()">Mentés</button>', self.screen)
        # 4.27: the request of the save; its changes are the developer's (TODO)
        self.assertIn('this.commitForm({ changesRendeles: { inserted: [], updated: [], deleted: [] } }).subscribe();', self.screen)

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

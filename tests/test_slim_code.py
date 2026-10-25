"""4.14: a slimmer generated module with the same behaviour.

The fixed PL/SQL helpers of the emulation live once in CommonMigrateTools.FormsPlsql, the empty
statements the emulation leaves behind are pruned, the block guard is one Set, and a button method
declares only the request maps it binds.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from java_support import COMPANY_IMPORTS
from frm_forms import forms_emulation as emu
from frm_forms.cli import main
from frm_forms.plsql_passthrough import Rewriter, prepare, sql_expression
from frm_forms.plsql_structure import prune

ROOT = Path(__file__).resolve().parents[1]
REPLICA = ROOT / 'tests' / 'fixtures' / 'felmeres_replika_fmb.xml'
TEMPLATE = ROOT / 'frm_forms' / 'templates' / 'CommonMigrateTools.java.tpl'


class HelperTests(unittest.TestCase):
    def test_template_constants_are_the_helpers(self):
        self.assertIn(emu.java_class(), TEMPLATE.read_text(encoding='utf-8'))
        self.assertEqual(emu.answers_var(), Rewriter.var(emu.ALERT_PARAMETER))

    def test_the_block_names_its_helpers_and_the_sql_text_stays_complete(self):
        r = prepare("go_block('B'); :CTRL.X := 'A';", block='CTRL', items={'CTRL': {'X': {'type': 'text'}}}, units={},
                    prefixes=(), other_blocks=True, parameters=True, transaction=True, ui=True, form='F')
        java = sql_expression(r)
        self.assertIn('FormsPlsql.MSG + FormsPlsql.CMD', java)
        self.assertNotIn('PROCEDURE frm_cmd', java)
        self.assertNotIn('+ ""', java)  # no empty literal between the parts
        self.assertIn(emu.HELPERS['CMD'], r['sql'])  # the evidence and the database get the whole block


class PruneTests(unittest.TestCase):
    def test_empty_statements_of_the_emulation_go(self):
        source = ("BEGIN\n  NULL;\nEND;\nBEGIN\n  NULL;\n  IF NOT TRUE THEN\n    RAISE FORM_TRIGGER_FAILURE;\n  END IF;\n"
                  "  x := 'K';\nEND;")
        self.assertEqual(prune(source), "BEGIN\n  x := 'K';\nEND;")
        self.assertEqual(prune("-- PRE-FORM\nBEGIN\nBEGIN\n  NULL;\nEND;\nEND;"), '-- PRE-FORM\nNULL;')

    def test_whatever_could_matter_stays(self):
        kept = ('IF a THEN NULL; ELSE x := 1; END IF;\n'
                'BEGIN NULL; EXCEPTION WHEN OTHERS THEN NULL; END;\n'
                '<<l>> BEGIN NULL; END;\n'
                'DECLARE v NUMBER; BEGIN NULL; END;\n'
                'IF NOT TRUE THEN x := 1; ELSE x := 2; END IF;')
        self.assertEqual(prune(kept), kept)
        self.assertEqual(prune('not PL/SQL ((('), 'not PL/SQL (((')  # unparsable: unchanged

    def test_unused_failure_declaration_goes_with_its_last_use(self):
        r = prepare('IF NOT form_success THEN RAISE form_trigger_failure; END IF; UPDATE t SET a = 1;', block=None,
                    items={}, units={}, prefixes=(), other_blocks=True, parameters=True, transaction=True, ui=True, form='F')
        self.assertNotIn('FORM_TRIGGER_FAILURE', r['sql'].upper())
        self.assertIn('UPDATE t SET a = 1;', r['sql'])


class ReplicaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('FRM_JAVA_IMPORT_MAP', '-')
        os.environ.setdefault('FRM_JAVA_VARIABLE_MAP', '-')  # nor a developer's own java-variables.json
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        config = root / 'config.json'
        config.write_text(json.dumps({'java_company_imports': COMPANY_IMPORTS, 'backend_live': True,
                                      'screen_window_selection': 'all', 'screen_primary_window_auto': True}))
        with contextlib.redirect_stdout(io.StringIO()):
            assert main(['migrate', str(REPLICA), '--out', str(root / 'out'), '--screen', '--module', 'rendeles',
                         '--config', str(config)]) == 0
        cls.service = (root / 'out/backend/DPS/RendelesServiceImpl.java').read_text(encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_helpers_are_not_repeated(self):
        self.assertNotIn('PROCEDURE frm_cmd(', self.service)
        self.assertNotIn('PROCEDURE frm_msg(', self.service)
        self.assertIn('import hu.company.features.cl.CommonMigrateTools.FormsPlsql;', self.service)

    def test_guard_is_one_set_and_unused_request_maps_are_not_declared(self):
        self.assertNotIn('switch (operation)', self.service)
        self.assertIn('Set.of("read"', self.service)
        keres = self.service[self.service.index('public ActionResult onCtrlPbKeres'):]
        keres = keres[:keres.index('\n    }\n')]
        self.assertIn('var values =', keres)
        self.assertNotIn('var parameters =', keres)  # PB_KERES binds no :GLOBAL / :SYSTEM value

    def test_start_up_code_lost_its_empty_framework_blocks(self):
        init = self.service[self.service.index('public ActionResult onFormInit'):]
        init = init[:init.index('\n    }\n')]
        self.assertNotIn('"  NULL;\\n"\n                    + "  NULL;\\n"', init)
        self.assertIn('-- PRE-FORM', init)


if __name__ == '__main__':
    unittest.main()

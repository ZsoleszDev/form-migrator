"""4.22: data the migrated code cannot get anywhere is a developer input, not a reason to leave the trigger untranslated.

An item that is not in the form, another block's item in a record trigger, a :GLOBAL in a data trigger, a Forms
system variable the screen does not send: the trigger's PL/SQL still runs, and each such value is a local variable
of the generated Java method (null, with a TODO) that the developer fills. Writing such a value stays refused.
"""
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
import test_query_actions as qa


def form() -> bytes:
    root = ET.fromstring(qa.fixture())
    module = root.find('FormModule')
    controls = next(b for b in module.findall('Block') if b.get('Name') == 'T1')
    button = ET.SubElement(controls, 'Item', Name='PB_MENT', ItemType='Push Button', DatabaseItem='false', CanvasName='C',
                           XPosition='120', YPosition='60', Width='100', Height='30', Label='Mentés')
    ET.SubElement(button, 'Trigger', Name='WHEN-BUTTON-PRESSED',
                  TriggerText="BEGIN\n  UPDATE t_blk SET col9 = :XX.YY WHERE col7 = :T1.COL1;\n  :T1.COL5 := :SYSTEM.LAST_QUERY;\nEND;")
    rows = next(b for b in module.findall('Block') if b.get('Name') == 'BLK')
    next(t for t in rows.findall('Trigger') if t.get('Name') == 'POST-QUERY').set(
        'TriggerText', ":BLK.INFO := :GLOBAL.FELHASZNALO || ' ' || :T1.COL1;")
    return ET.tostring(root)


class DeveloperInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        source = cls.root / 'inputs.xml'
        source.write_bytes(form())
        config = cls.root / 'config.json'
        config.write_text(json.dumps({'backend_live': True, 'backend_trigger_mode': 'plsql',
                                      'java_company_imports': COMPANY_IMPORTS, 'java_import_map': '-'}))
        cls.out = cls.root / 'out'
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            assert main(['migrate', str(source), '--module', 'query', '--screen', '--config', str(config), '--out', str(cls.out)]) == 0
        cls.service = (cls.out / 'backend/DPS/QueryServiceImpl.java').read_text(encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_the_button_runs_and_its_unknown_values_are_variables_of_the_method(self):
        plan = json.loads((self.out / 'analysis/backend-plan.json').read_text(encoding='utf-8'))
        endpoint = next(e for e in plan['endpoints'] if e.get('owner') == 'T1.PB_MENT')
        self.assertEqual((endpoint['runs'], endpoint['implemented'], endpoint['blockers']), ('plsql', True, []))
        self.assertEqual(endpoint['developer_inputs'], [
            {'source': 'XX.YY', 'variable': 'xxYy', 'reason': 'ismeretlen mező, nincs a formban'},
            {'source': 'SYSTEM.LAST_QUERY', 'variable': 'systemLastQuery', 'reason': 'Forms rendszerváltozó, a webes képernyő nem adja át'}])
        method = self.service[self.service.index('public ActionResult onT1PbMent('):]
        self.assertIn('            // TODO: :XX.YY (ismeretlen mező, nincs a formban): add át ennek a változónak a megfelelő '
                      'értéket.\n            String xxYy = null;\n', method)
        self.assertIn('DbCalls.in(xxYy, Types.VARCHAR)', method)
        self.assertIn('DbCalls.in(systemLastQuery, Types.VARCHAR)', method)
        self.assertIn('Fejlesztői bemenet (a metódus elején, null; TODO): :XX.YY -> xxYy, :SYSTEM.LAST_QUERY -> systemLastQuery.',
                      self.service)

    def test_a_record_trigger_gets_other_blocks_and_globals_as_variables(self):
        method = self.service[self.service.index('private void trigger'):]
        method = method[:method.index('\n    }\n')]
        for declaration in ('String globalFelhasznalo = null;', 'String t1Col1 = null;'):
            self.assertIn(declaration, method)
        self.assertIn('DbCalls.in(row.info, Types.VARCHAR)', method)  # the record's own item: from the row
        self.assertIn('DbCalls.in(t1Col1, Types.VARCHAR)', method)

    def test_the_inputs_are_listed_for_the_developer(self):
        tasks = (self.out / 'BACKEND_TASKS.md').read_text(encoding='utf-8')
        self.assertIn('## Fejlesztői bemenetek', tasks)
        for row in ('| T1.PB_MENT / WHEN-BUTTON-PRESSED | :XX.YY | xxYy | ismeretlen mező, nincs a formban |',
                    '| BLK / POST-QUERY | :GLOBAL.FELHASZNALO | globalFelhasznalo | szerveroldali kontextus, adatműveleti '
                    'triggerben nem érhető el |',
                    '| BLK / POST-QUERY | :T1.COL1 | t1Col1 | másik blokk mezője, a rekordban nem érhető el |'):
            self.assertIn(row, tasks)
        coverage = json.loads((self.out / 'analysis/runtime-coverage.json').read_text(encoding='utf-8'))
        row = next(r for r in coverage['triggers'] if r['owner'] == 'T1.PB_MENT')
        self.assertTrue(any(g.startswith('Fejlesztői bemenet: :XX.YY, :SYSTEM.LAST_QUERY') for g in row['gaps']), row['gaps'])

    def test_the_generated_java_compiles(self):
        if not shutil.which('java'):
            self.skipTest('Java 11+ compiler required')
        sources = list(self.out.glob('backend/**/*.java')) + write_stubs(self.root / 'stubs')
        args = self.root / 'sources.args'
        args.write_text('\n'.join('"' + str(p).replace(chr(92), '/') + '"' for p in sources))
        run = subprocess.run(['java', 'com.sun.tools.javac.Main', '--release', '11', '-encoding', 'UTF-8',
                              '-d', str(self.root / 'classes'), '@' + str(args)], capture_output=True, text=True, timeout=300)
        if 'Could not find or load main class' in run.stderr:
            self.skipTest('JDK (javac) required')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


class SaveChainInputTests(unittest.TestCase):
    """A button with a commit point and a form-level PRE-COMMIT (the survey replica): their runners get the variables."""

    def test_commit_point_and_pre_commit_inputs(self):
        source = (Path(__file__).parent / 'fixtures/felmeres_replika_fmb.xml').read_text(encoding='utf-8')
        source = source.replace('<Item Name="PB_ALAP" ItemType="Push Button"', (
            '<Item Name="PB_PONT" ItemType="Push Button" Label="Pont" CanvasName="C" XPosition="460" YPosition="40" Width="80" '
            'Height="22"><Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="UPDATE rendeles SET statusz = :NINCS.STATUSZ WHERE id = '
            ":RENDELES.ID;&amp;#10;COMMIT_FORM;&amp;#10;:CTRL.MODUS := 'K';\"/></Item>\n"
            '  <Item Name="PB_ALAP" ItemType="Push Button"'), 1)
        source = source.replace('<Trigger Name="PRE-FORM"', '<Trigger Name="PRE-COMMIT" TriggerText="UPDATE rendeles SET '
                                'megjegyzes = :NINCS.MEGJ WHERE id = :RENDELES.ID;"/>\n <Trigger Name="PRE-FORM"', 1)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'replika.xml').write_text(source, encoding='utf-8')
            (root / 'config.json').write_text(json.dumps({'backend_live': True, 'java_company_imports': COMPANY_IMPORTS,
                                                          'java_import_map': '-'}))
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['migrate', str(root / 'replika.xml'), '--module', 'rendeles', '--screen',
                                       '--config', str(root / 'config.json'), '--out', str(root / 'out')]), 0)
            service = (root / 'out/backend/DPS/RendelesServiceImpl.java').read_text(encoding='utf-8')
            runner = service[service.index('private void runOnCtrlPbPont('):]
            self.assertIn('        String nincsStatusz = null;\n        Object[] out = DbCalls.call(', runner)  # the commit runs it again
            self.assertIn('            // PRE-COMMIT: az eredeti PL/SQL az adatbázisban, a képernyő értékeivel.\n            {\n'
                          '                // TODO: :NINCS.MEGJ (ismeretlen mező, nincs a formban)', service)
            tasks = (root / 'out/BACKEND_TASKS.md').read_text(encoding='utf-8')
            self.assertIn('| FORM / PRE-COMMIT (mentés) | :NINCS.MEGJ | nincsMegj | ismeretlen mező, nincs a formban |', tasks)
            if shutil.which('java'):
                sources = list((root / 'out').glob('backend/**/*.java')) + write_stubs(root / 'stubs')
                (root / 'sources.args').write_text('\n'.join('"' + str(p).replace(chr(92), '/') + '"' for p in sources))
                run = subprocess.run(['java', 'com.sun.tools.javac.Main', '--release', '11', '-encoding', 'UTF-8', '-d',
                                      str(root / 'classes'), '@' + str(root / 'sources.args')], capture_output=True, text=True, timeout=300)
                if 'Could not find or load main class' not in run.stderr:
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == '__main__':
    unittest.main()

"""screen_windows: the developer picks the windows to generate; the web asks once, with a preview."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

import frm_forms.cli as cli
from frm_forms.screen_windows import WindowSelectionRequired
from test_screen_windows import simple_windows

os.environ.setdefault('FRM_JAVA_IMPORT_MAP', '-')  # tests never read a developer's own java-imports.json
os.environ.setdefault('FRM_JAVA_VARIABLE_MAP', '-')  # nor a developer's own java-variables.json

HEADSTART = Path(__file__).with_name('golden') / 'headstart' / 'input.xml'


class WindowSelectionTests(unittest.TestCase):
    def run_migration(self, source, **config):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / 'form_fmb.xml').write_text(source, encoding='utf-8')
        (root / 'config.json').write_text(json.dumps(config), encoding='utf-8')
        asked, original = {}, cli.migration
        def capture(args, on_progress=None):
            try:
                return original(args, on_progress)
            except WindowSelectionRequired as exc:
                asked['question'] = exc
                raise
        cli.migration = capture
        self.addCleanup(setattr, cli, 'migration', original)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = cli.main(['migrate', str(root / 'form_fmb.xml'), '--screen', '--module', 'windows',
                             '--config', str(root / 'config.json'), '--out', str(root / 'out')])
        return code, err.getvalue(), asked.get('question'), root / 'out'

    def test_ask_lists_every_window_with_a_script_free_preview(self):
        code, err, question, _ = self.run_migration(simple_windows(), screen_window_selection='ask')
        self.assertEqual(code, 1)
        self.assertIn('SCREEN_WINDOWS_REQUIRED', err)
        self.assertEqual([c['name'] for c in question.candidates], ['LEFT', 'RIGHT'])
        for choice in question.candidates:
            self.assertGreater(choice['items'], 0)
            self.assertIn('class="window"', choice['preview'])
            self.assertNotIn('<script', choice['preview'].lower())

    def test_chosen_windows_are_generated_and_the_others_are_left_out(self):
        code, err, question, out = self.run_migration(simple_windows(), screen_window_selection='ask', screen_windows=['RIGHT'])
        self.assertEqual(code, 0, err)
        self.assertIsNone(question)
        plan = json.loads((out / 'analysis/screen-plan.json').read_text(encoding='utf-8'))
        self.assertEqual({w['name']: w['role'] for w in plan['windows']}, {'RIGHT': 'main'})  # as if LEFT never existed
        scope = json.loads((out / 'analysis/window-scope.json').read_text(encoding='utf-8'))
        self.assertEqual((scope['windows'], scope['blocks']), (['LEFT'], ['B1']))
        model = json.loads((out / 'analysis/form.ir.json').read_text(encoding='utf-8'))
        self.assertEqual([b['name'] for b in model['blocks']], ['B2'])

    def test_default_keeps_the_previous_behaviour_and_bad_names_are_refused(self):
        code, err, question, _ = self.run_migration(simple_windows())
        self.assertIsNone(question)  # 'all': no windows question (the main window question may follow)
        self.assertNotIn('SCREEN_WINDOWS_REQUIRED', err)
        code, err, _, _ = self.run_migration(simple_windows(), screen_windows=['NOPE'])
        self.assertEqual(code, 1)
        self.assertIn('SCREEN_WINDOWS: nem létező ablak: NOPE', err)

    def test_a_left_out_window_has_no_endpoint_action_or_lov(self):
        form = """<Module><FormModule Name="KET" Title="Két ablak"><Coordinate CoordinateSystem="Real" RealUnit="Pixel"/>
 <Block Name="FO" DatabaseDataBlock="true" QueryDataSourceName="FO_T">
  <Item Name="ID" ItemType="Text Item" DataType="Number" PrimaryKey="true" ColumnName="ID" Prompt="Azonosító" CanvasName="C_FO" XPosition="10" YPosition="10" Width="80" Height="20"/>
 </Block>
 <Block Name="EXTRA" DatabaseDataBlock="true" QueryDataSourceName="EXTRA_T">
  <Item Name="ID" ItemType="Text Item" DataType="Number" PrimaryKey="true" ColumnName="ID" Prompt="Azonosító" CanvasName="C_EXTRA" XPosition="10" YPosition="10" Width="80" Height="20"/>
  <Item Name="KOD" ItemType="Text Item" DataType="Char" MaximumLength="10" ColumnName="KOD" Prompt="Kód" LovName="KOD_LOV" CanvasName="C_EXTRA" XPosition="100" YPosition="10" Width="80" Height="20"/>
  <Item Name="PB_SZAMOL" ItemType="Push Button" Label="Számol" CanvasName="C_EXTRA" XPosition="190" YPosition="10" Width="80" Height="22">
   <Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="UPDATE extra_t SET kod = 'X' WHERE id = :EXTRA.ID;"/></Item>
 </Block>
 <LOV Name="KOD_LOV" RecordGroupName="KOD_RG"/>
 <RecordGroup Name="KOD_RG" RecordGroupQuery="SELECT kod FROM kodok"/>
 <Canvas Name="C_FO" CanvasType="Content" WindowName="FO_W"/><Window Name="FO_W" Title="Fő"/>
 <Canvas Name="C_EXTRA" CanvasType="Content" WindowName="EXTRA_W"/><Window Name="EXTRA_W" Title="Extra"/>
</FormModule></Module>"""
        code, err, _, out = self.run_migration(form, screen_windows=['FO_W'], backend_live=True)
        self.assertEqual(code, 0, err)
        plan = json.loads((out / 'analysis/backend-plan.json').read_text(encoding='utf-8'))
        text = json.dumps(plan)
        self.assertNotIn('EXTRA', text)  # no endpoint, action or LOV of the window left out
        self.assertNotIn('KOD_LOV', text)
        scope = json.loads((out / 'analysis/window-scope.json').read_text(encoding='utf-8'))
        self.assertEqual((scope['blocks'], scope['lovs'], scope['record_groups']), (['EXTRA'], ['KOD_LOV'], ['KOD_RG']))
        for path in (out / 'backend').rglob('*.java'):
            self.assertNotIn('EXTRA', path.read_text(encoding='utf-8'), path.name)

    def test_a_single_window_form_is_never_asked(self):
        code, err, question, _ = self.run_migration(HEADSTART.read_text(encoding='utf-8'), screen_window_selection='ask')
        self.assertEqual(code, 0, err)
        self.assertIsNone(question)


if __name__ == '__main__':
    unittest.main()

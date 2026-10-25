"""FMB export location, main-window question, screen preview, CL paths in the frontend.

Runs without the web dependencies: the worker and the generators are plain Python.
"""
import contextlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

from frm_forms.cli import main
from frm_forms.screen_windows import PrimaryWindowRequired
from test_screen_windows import simple_windows

HEADSTART = Path(__file__).with_name('golden') / 'headstart' / 'input.xml'


def multi_screen():
    """Two Document windows (ambiguous main), a tab canvas, a stacked region and a modal dialog."""
    def item(name, prompt, x, y, canvas, tab='', extra='DataType="Char" MaximumLength="30"'):
        page = f' TabPageName="{tab}"' if tab else ''
        return (f'<Item Name="{name}" ItemType="Text Item" {extra} Prompt="{prompt}" CanvasName="{canvas}"{page} '
                f'XPosition="{x}" YPosition="{y}" Width="120" Height="20"/>')
    return f'''<Module><FormModule Name="MULTI_SCREEN" Title="Ügyfél &lt;kezelés&gt;">
 <Coordinate CoordinateSystem="Real" RealUnit="Pixel"/>
 <Block Name="HEAD" DatabaseDataBlock="false">{item('KOD', 'Kód', 10, 10, 'MAIN_C')}</Block>
 <Block Name="TETEL" DatabaseDataBlock="true" QueryDataSourceName="UGYFEL_TETEL" RecordsDisplayCount="5">
  <Item Name="TETEL_ID" ItemType="Text Item" DataType="Number" PrimaryKey="true" Prompt="Azonosító" CanvasName="TABS" TabPageName="TP_A" XPosition="10" YPosition="100" Width="80" Height="20"/>
 </Block>
 <Block Name="MEGJ" DatabaseDataBlock="false">{item('SZOVEG', 'Megjegyzés', 10, 100, 'TABS', 'TP_B')}</Block>
 <Block Name="SZURO" DatabaseDataBlock="false">{item('NEV', 'Név', 10, 10, 'SEARCH_C')}</Block>
 <Block Name="SZERK" DatabaseDataBlock="false">{item('UJ_NEV', 'Új név', 10, 10, 'EDIT_C')}</Block>
 <Block Name="INFO" DatabaseDataBlock="false">{item('INFO_TXT', 'Tájékoztatás', 10, 200, 'STACK')}</Block>
 <Canvas Name="MAIN_C" CanvasType="Content" WindowName="MAIN_WIN"/>
 <Canvas Name="TABS" CanvasType="Tab" WindowName="MAIN_WIN"><TabPage Name="TP_A" Label="Tételek"/><TabPage Name="TP_B" Label="Megjegyzés"/></Canvas>
 <Canvas Name="STACK" CanvasType="Stacked" WindowName="MAIN_WIN"/>
 <Canvas Name="SEARCH_C" CanvasType="Content" WindowName="SEARCH_WIN"/>
 <Canvas Name="EDIT_C" CanvasType="Content" WindowName="EDIT_DLG"/>
 <Window Name="MAIN_WIN" Title="Ügyfelek" WindowStyle="Document"/>
 <Window Name="SEARCH_WIN" Title="Részletes keresés" WindowStyle="Document"/>
 <Window Name="EDIT_DLG" Title="Név módosítása" WindowStyle="Dialog" Modal="true"/>
</FormModule></Module>'''


class WebFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def migrate(self, source, *extra, name='form_fmb.xml', out='out'):
        path = self.root / name
        if isinstance(source, bytes): path.write_bytes(source)
        else: path.write_text(source, encoding='utf-8')
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = main(['migrate', str(path), '--out', str(self.root / out), *extra])
        return code, err.getvalue(), self.root / out

    # .fmb: Forms2XML with an absolute input path writes next to the input
    def test_fmb_export_is_found_next_to_the_input(self):
        exporter = self.root / 'export.py'
        exporter.write_text('import sys\nfrom pathlib import Path\nsource = Path(sys.argv[-1])\n'
                            'assert source.read_bytes() == b"synthetic-fmb"\n'
                            '(source.parent / (source.stem + "_fmb.xml")).write_bytes(Path(sys.argv[1]).read_bytes())\n')
        config = self.root / 'config.json'
        config.write_text(json.dumps({'export_command': [sys.executable, str(exporter), str(HEADSTART), '{input}']}))
        code, err, out = self.migrate(b'synthetic-fmb', '--screen', '--module', 'ank', '--config', str(config), name='ANK.fmb')
        self.assertEqual(code, 0, err)
        self.assertIn('a bemenet mellől átvéve', (out / 'analysis/export.log').read_text(encoding='utf-8'))

    def test_stale_export_next_to_the_input_is_never_used(self):
        (self.root / 'ANK_fmb.xml').write_text(HEADSTART.read_text(encoding='utf-8'), encoding='utf-8')
        import os, time
        old = time.time() - 3600
        os.utime(self.root / 'ANK_fmb.xml', (old, old))
        config = self.root / 'config.json'
        config.write_text(json.dumps({'export_command': [sys.executable, '-c', 'pass', '{input}']}))
        code, err, _ = self.migrate(b'synthetic-fmb', '--screen', '--config', str(config), name='ANK.fmb')
        self.assertEqual(code, 1)
        self.assertIn('Várt fájl: ANK_fmb.xml', err)

    # Several possible main windows: a question with the candidates
    def test_ambiguous_main_window_carries_candidates_and_the_worker_asks(self):
        code, err, _ = self.migrate(multi_screen(), '--screen')
        self.assertEqual(code, 1)
        self.assertIn('SCREEN_PRIMARY_WINDOW_REQUIRED', err)
        from frm_forms.web import worker
        job = self.root / 'job'; inputs = job / 'inputs'; inputs.mkdir(parents=True)
        (inputs / 'input.xml').write_text(multi_screen(), encoding='utf-8')
        (inputs / 'config.json').write_text('{}')
        (job / 'job.json').write_text(json.dumps({'source_type': '.xml', 'options': {
            'module': None, 'ai_mode': 'off', 'strict': False, 'generation_mode': 'screen'}}))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(worker.run(job), worker.NEEDS_INPUT)
        question = json.loads((job / 'question.json').read_text(encoding='utf-8'))
        self.assertEqual(question['kind'], 'primary_window')
        self.assertEqual([(c['name'], c['title'], c['blocks']) for c in question['choices']],
                         [('MAIN_WIN', 'Ügyfelek', ['HEAD', 'INFO', 'MEGJ', 'TETEL']), ('SEARCH_WIN', 'Részletes keresés', ['SZURO'])])
        self.assertTrue(issubclass(PrimaryWindowRequired, Exception))

    # Preview: every screen, tabs, no script, escaped text
    def test_preview_shows_every_screen_without_scripts(self):
        config = self.root / 'config.json'; config.write_text(json.dumps({'screen_primary_window': 'MAIN_WIN'}))
        code, err, out = self.migrate(multi_screen(), '--screen', '--config', str(config))
        self.assertEqual(code, 0, err)
        html = (out / 'analysis/screen-preview.html').read_text(encoding='utf-8')
        self.assertNotIn('<script', html.lower())
        self.assertEqual(html.count('class="window"'), 3)
        self.assertLess(html.index('Ügyfelek'), html.index('Részletes keresés'))  # the main screen first
        for text in ['Fő képernyő', 'Párbeszédablak (modális)', 'Tételek', 'Megjegyzés', 'Rétegzett régió: STACK', 'Ügyfél &lt;kezelés&gt;']:
            self.assertIn(text, html)
        self.assertIn('screen-preview.html', (out / 'frontend/multiScreen/MIGRATION_NOTES.md').read_text(encoding='utf-8'))

    def test_spacers_and_tables_in_the_preview(self):
        code, err, out = self.migrate(HEADSTART.read_text(encoding='utf-8'), '--screen', '--module', 'teszt')
        self.assertEqual(code, 0, err)
        html = (out / 'analysis/screen-preview.html').read_text(encoding='utf-8')
        self.assertIn('<th title="AIT.AIT_KULCS">Kulcs</th>', html)
        self.assertNotIn('AIT.L_URES_3', html.split('<table>')[1].split('</table>')[0])  # a spacer is no column
        self.assertNotIn('nav class="screens"', html)  # one screen: no switcher

    # Generated frontend: the company pattern - ServiceBase.url with the endpoint as the CL names it
    def test_every_backend_call_uses_the_cl_endpoint_through_service_base(self):
        code, err, out = self.migrate(HEADSTART.read_text(encoding='utf-8'), '--screen', '--module', 'teszt')
        self.assertEqual(code, 0, err)
        java = (out / 'backend/CL/TesztConstants.java').read_text(encoding='utf-8')
        component = (out / 'frontend/teszt/teszt.component.ts').read_text(encoding='utf-8')
        # Paths only: the <METHOD>_NAME constants name log1x operations, not endpoints.
        paths = {k: v for k, v in re.findall(r'public static final String (\w+) = "([^"]*)";', java) if not k.endswith('_NAME') and k != 'BASE_PATH'}
        self.assertEqual(set(re.findall(r"this\.url\('([^']*)'\)", component)), {v.lstrip('/') for v in paths.values()})
        self.assertNotIn('ACTION_CGNVW011PBRESZLETEKACTIONA6B26D41_PATH', java)
        self.assertNotIn("'/api/", component)  # the server and module path come from ServiceBase
        self.assertIn('export class TesztComponent extends ServiceBase {', component)  # 4.26: no shared runtime
        self.assertIn("  aitSearch(body: unknown) {\n    return this.http.post<any>(this.url('ait/query/search'), body).pipe(\n"
                      "      tap(res => WFF.debug(this.modName + '.aitSearch', res)),\n"
                      "      catchError(error => {\n        WFF.err('Hiba', error);\n        return EMPTY;\n      }),\n    );\n  }", component)
        self.assertNotIn('@Input', component)
        self.assertNotIn('@Output', component)  # a routed component, not a child
        self.assertIn('  lovInptip(body: unknown) {', component)  # 4.27: the LOV endpoint is there; its suggestions are the developer's
        self.assertIn("this.aitSearch({ criteria: { vElekAdlapUbiInptipKod: vElekAdlap.ubiInptipKod }, offset: 0, limit: 200 })", component)
        self.assertIn('## Backend-hívások', (out / 'frontend/teszt/MIGRATION_NOTES.md').read_text(encoding='utf-8'))
        plan = json.loads((out / 'analysis/screen-plan.json').read_text(encoding='utf-8'))
        self.assertEqual(plan['backend_calls']['queries'], {'AIT': 'aitSearch'})

    def test_frontend_only_calls_no_backend(self):
        code, err, out = self.migrate(HEADSTART.read_text(encoding='utf-8'), '--screen', '--module', 'teszt', '--frontend-only')
        self.assertEqual(code, 0, err)
        component = (out / 'frontend/teszt/teszt.component.ts').read_text(encoding='utf-8')
        self.assertNotIn('HttpClient', component)
        self.assertNotIn('@Output', component)
        self.assertIn('extends ServiceBase', component)
        self.assertNotIn('completeMethod', component)  # no LOV endpoint: the field offers no choices
        self.assertNotIn('WFF', component)

if __name__ == '__main__':
    unittest.main()

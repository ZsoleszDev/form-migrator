import contextlib
import io
import json
from pathlib import Path
import unittest

from frm_forms.cli import main
import test_screen

ROOT = test_screen.ROOT


def simple_windows(first=''):
    return f'''<FormModule Name="ANY_MODULE" {first} CoordinateSystem="Real" RealUnit="Pixel">
      <Window Name="LEFT" WindowStyle="Document"/><Window Name="RIGHT" WindowStyle="Document"/>
      <Canvas Name="C1" CanvasType="Content" WindowName="LEFT"/><Canvas Name="C2" CanvasType="Content" WindowName="RIGHT"/>
      <Block Name="B1"><Item Name="A" ItemType="Text Item" CanvasName="C1"/></Block>
      <Block Name="B2"><Item Name="B" ItemType="Text Item" CanvasName="C2"/></Block></FormModule>'''


class WindowTests(unittest.TestCase):
    setUp = test_screen.ScreenTests.setUp
    generate = test_screen.ScreenTests.generate

    def rejected(self, xml, code, config=None):
        src = self.root / 'input.xml'; src.write_text(xml)
        path = self.root / 'config.json'; path.write_text(json.dumps(config or {}))
        out = self.root / 'rejected'; err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            status = main(['migrate', str(src), '--screen', '--frontend-only', '--config', str(path), '--out', str(out)])
        self.assertEqual(status, 1, err.getvalue()); self.assertIn(code, err.getvalue()); self.assertFalse(out.exists())

    def test_window_owns_all_content_tabs_and_stacked_regions(self):
        out, p, source = self.generate(ROOT / 'examples/screen-dialogs_fmb.xml')
        windows = {w['name']: w for w in p['windows']}
        self.assertEqual(windows['WORK_AREA']['role'], 'main')
        self.assertEqual(windows['UNUSED_HELPER']['role'], 'unused')
        self.assertEqual(source.count('<p-dialog'), 3)
        self.assertEqual(set(windows['EDIT_WINDOW']['canvases']), {'DETAIL_CONTENT', 'DETAIL_TABS', 'DETAIL_EXTRA'})
        dialog = next(part.split('</p-dialog>')[0] for part in source.split('<p-dialog')[1:] if "windowVisible['EDIT_WINDOW']" in part)
        self.assertIn('<p-tabs', dialog)
        for canvas in windows['EDIT_WINDOW']['canvases']: self.assertIn("canvasVisible['" + canvas + "']", dialog)
        self.assertIn('[modal]="false"', dialog); self.assertIn('[focusTrap]="false"', dialog)
        confirm = next(part.split('</p-dialog>')[0] for part in source.split('<p-dialog')[1:] if "windowVisible['CONFIRM_WINDOW']" in part)
        self.assertIn('[modal]="true"', confirm); self.assertIn('[closable]="false"', confirm)
        self.assertFalse(p['window_controls']['windows']['EDIT_WINDOW'])
        self.assertFalse(p['window_controls']['canvases']['DETAIL_EXTRA'])
        self.assertIn('protected readonly windowVisible: Record<string, boolean> = {', source)  # 4.26: plain fields
        self.assertIn("[(visible)]=\"windowVisible['EDIT_WINDOW']\"", source)
        self.assertTrue((out / 'analysis/ui-model-strict.json').is_file())

    def test_dialog_accordion_keeps_pages_in_one_window(self):
        _, p, source = self.generate(ROOT / 'examples/screen-dialogs_fmb.xml', {'screen_tab_layout': 'accordion'})
        dialog = next(part.split('</p-dialog>')[0] for part in source.split('<p-dialog')[1:] if "windowVisible['EDIT_WINDOW']" in part)
        self.assertIn('<p-accordion', dialog); self.assertNotIn('<p-tabs', dialog)
        self.assertEqual(source.count('<p-dialog'), 3)

    def test_document_main_selection_requires_evidence(self):
        self.rejected(simple_windows(), 'SCREEN_PRIMARY_WINDOW_REQUIRED')
        _, p, source = self.generate(simple_windows(), {'screen_primary_window': 'RIGHT'})
        self.assertEqual(next(w['name'] for w in p['windows'] if w['role'] == 'main'), 'RIGHT')
        self.assertEqual(source.count('<p-dialog'), 1)
        self.assertIn('[modal]="false"', source)
        self.rejected(simple_windows(), 'SCREEN_PRIMARY_WINDOW', {'screen_primary_window': 'MISSING'})

    def test_first_navigation_block_and_renaming_are_module_independent(self):
        xml = simple_windows('FirstNavigationBlock="B2"')
        _, first, _ = self.generate(xml)
        renamed = xml.replace('ANY_MODULE', 'DIFFERENT').replace('LEFT', 'QXZ').replace('RIGHT', 'ZYX').replace('B1', 'QUERY_INPUT').replace('B2', 'RESULT_DATA')
        _, second, _ = self.generate(renamed, label='renamed')
        self.assertEqual([w['role'] for w in first['windows']], ['dialog', 'main'])
        self.assertEqual([w['role'] for w in second['windows']], ['dialog', 'main'])
        self.assertEqual([s['mode'] for s in first['sections']], [s['mode'] for s in second['sections']])

    def test_alternative_content_canvases_share_window_state(self):
        _, p, source = self.generate(ROOT / 'examples/screen-pages_fmb.xml')
        self.assertNotIn('<p-dialog', source)
        self.assertEqual(p['window_controls']['content'], {'WORKSPACE': 'STEP_A'})
        for canvas in ['STEP_A', 'STEP_B']:
            self.assertIn("activeContentCanvas['WORKSPACE'] === '" + canvas + "'", source)
        self.assertFalse(p['window_controls']['canvases']['STEP_B'])

    def test_primary_canvas_supplies_missing_window_name(self):
        xml = simple_windows('FirstNavigationBlock="B1"').replace('Name="LEFT" WindowStyle', 'Name="LEFT" PrimaryCanvas="C1" WindowStyle').replace(' WindowName="LEFT"', '')
        _, p, _ = self.generate(xml)
        canvas = next(s for s in p['surfaces'] if s['name'] == 'C1')
        self.assertEqual((canvas['window'], canvas['window_source']), ('LEFT', 'Window.PrimaryCanvas'))

    def test_single_window_inference_is_reported(self):
        xml = '<FormModule Name="F" CoordinateSystem="Real" RealUnit="Pixel"><Window Name="W"/><Canvas Name="C"/><Block Name="B"><Item Name="I" ItemType="Text Item" CanvasName="C"/></Block></FormModule>'
        _, p, _ = self.generate(xml)
        self.assertEqual(p['surfaces'][0]['window'], 'W')
        self.assertIn('SCREEN_INFERRED_CANVAS_WINDOW', [n['code'] for n in p['notices']])

    def test_blank_primary_canvas_is_retained_as_content_host(self):
        xml = '<FormModule Name="F" CoordinateSystem="Real" RealUnit="Pixel"><Window Name="W" WindowStyle="Dialog" PrimaryCanvas="EMPTY"/><Canvas Name="EMPTY" WindowName="W"/><Canvas Name="STACK" CanvasType="Stacked" WindowName="W"/><Block Name="B"><Item Name="I" ItemType="Text Item" CanvasName="STACK"/></Block></FormModule>'
        _, p, source = self.generate(xml)
        self.assertEqual(set(p['windows'][0]['canvases']), {'EMPTY', 'STACK'})
        self.assertEqual(source.count('<p-dialog'), 1)

    def test_invalid_or_conflicting_window_references_reject_output(self):
        xml = simple_windows('FirstNavigationBlock="B1"')
        self.rejected(xml.replace('WindowName="LEFT"', 'WindowName="MISSING"'), 'SCREEN_UNKNOWN_WINDOW')
        self.rejected(xml.replace(' WindowName="LEFT"', ''), 'SCREEN_CANVAS_WINDOW_AMBIGUOUS')
        self.rejected(xml.replace('Name="LEFT" WindowStyle', 'Name="LEFT" PrimaryCanvas="MISSING" WindowStyle'), 'SCREEN_UNKNOWN_PRIMARY_CANVAS')
        self.rejected(xml.replace('Name="LEFT" WindowStyle="Document"', 'Name="LEFT" WindowStyle="Unknown"'), 'SCREEN_WINDOW_STYLE')
        self.rejected(xml.replace('Name="RIGHT" WindowStyle', 'Name="LEFT" WindowStyle'), 'DUPLICATE_CHILD')

    def test_a_primary_canvas_that_is_not_the_window_s_content_canvas_is_ignored(self):
        """4.25.2: Designer forms (CG$POPUP_n) may name a stacked canvas, a blank one or another window's canvas as the
        window's PrimaryCanvas: a notice, not the end of the migration; Canvas.WindowName decides."""
        main_window = ('<Window Name="MAIN" WindowStyle="Document"/><Canvas Name="PAGE" CanvasType="Content" WindowName="MAIN"/>'
                       '<Block Name="B"><Item Name="A" ItemType="Text Item" CanvasName="PAGE"/>')
        cases = {
            'stacked': ('<Window Name="POPW" WindowStyle="Dialog" PrimaryCanvas="CG$POPUP_17"/>'
                        '<Canvas Name="CG$POPUP_17" CanvasType="Stacked" WindowName="POPW"/>',
                        '<Item Name="P" ItemType="Text Item" CanvasName="CG$POPUP_17"/>', 'Stacked típusú'),
            'blank stacked': ('<Window Name="POPW" WindowStyle="Dialog" PrimaryCanvas="CG$POPUP_17"/>'
                              '<Canvas Name="CG$POPUP_17" CanvasType="Stacked" WindowName="POPW"/>'
                              '<Canvas Name="POPC" CanvasType="Content" WindowName="POPW"/>',
                              '<Item Name="P" ItemType="Text Item" CanvasName="POPC"/>', 'üres, Stacked típusú'),
            'another window': ('<Window Name="POPW" WindowStyle="Dialog" PrimaryCanvas="PAGE"/>'
                               '<Canvas Name="POPC" CanvasType="Content" WindowName="POPW"/>',
                               '<Item Name="P" ItemType="Text Item" CanvasName="POPC"/>', 'a(z) MAIN ablakhoz tartozik'),
        }
        for label, (window, item, problem) in cases.items():
            with self.subTest(label):
                xml = ('<FormModule Name="F" FirstNavigationBlock="B" CoordinateSystem="Real" RealUnit="Pixel">' + main_window.split('<Block')[0]
                       + window + '<Block' + main_window.split('<Block')[1] + item + '</Block></FormModule>')
                _, p, source = self.generate(xml, label=label.replace(' ', '-'))
                notice = next(n for n in p['notices'] if n['code'] == 'SCREEN_PRIMARY_CANVAS_IGNORED')
                self.assertEqual(notice['owner'], 'POPW')
                self.assertIn(problem, notice['detail'])
                windows = {w['name']: w for w in p['windows']}
                self.assertEqual((windows['MAIN']['role'], windows['POPW']['role']), ('main', 'dialog'))
                self.assertIn('PAGE', windows['MAIN']['canvases'])
                self.assertIn("windowVisible['POPW']", source)

    def test_source_references_are_audited_without_automatic_execution(self):
        xml = (ROOT / 'examples/screen-dialogs_fmb.xml').read_text().replace("SHOW_WINDOW('EDIT_WINDOW');", "IF :SEARCH.IDENTIFIER IS NOT NULL THEN SHOW_WINDOW('EDIT_WINDOW'); END IF; SHOW_WINDOW(:GLOBAL.TARGET); -- HIDE_WINDOW('HISTORY_WINDOW');")
        _, p, source = self.generate(xml)
        refs = [r for r in p['window_references'] if r['owner'] == 'SEARCH.OPEN_DETAILS']
        self.assertEqual([r['status'] for r in refs], ['resolved', 'dynamic'])
        self.assertTrue(all(r['execution'] == 'review-only' and r['source_file'].endswith('.sql') for r in refs))
        action_method = source.split('protected onAction(', 1)[1].split('\n  }', 1)[0]
        self.assertNotIn('setWindowVisible(', action_method)

    def test_hidden_window_and_windowless_stacked_canvas(self):
        xml = '<FormModule Name="F" CoordinateSystem="Real" RealUnit="Pixel"><Window Name="W" Visible="false"/><Canvas Name="C" WindowName="W"/><Block Name="B"><Item Name="I" ItemType="Text Item" CanvasName="C"/></Block></FormModule>'
        _, p, source = self.generate(xml)
        self.assertFalse(p['window_controls']['windows']['W'])
        self.assertIn("@if (windowVisible['W'])", source)
        _, p, source = self.generate(xml.replace('<Window Name="W" Visible="false"/>', '').replace('WindowName="W"', 'CanvasType="Stacked" Visible="false"'), label='no-window')
        self.assertNotIn('<p-dialog', source); self.assertNotIn('windowVisible', source)
        self.assertIn('protected readonly canvasVisible: Record<string, boolean> = {', source)
        self.assertIn('C: false,', source); self.assertFalse(p['window_controls']['canvases']['C'])

    def test_inherited_window_properties_are_effective(self):
        library = self.root / 'base_olb.xml'
        library.write_text('<ObjectLibrary Name="BASE"><ObjectLibraryTab Name="T"><Window Name="DIALOG_BASE" WindowStyle="Dialog" Modal="true" Title="Örökölt ablak"/></ObjectLibraryTab></ObjectLibrary>')
        xml = '<FormModule Name="F" CoordinateSystem="Real" RealUnit="Pixel"><Window Name="W" ParentModule="BASE" ParentFilename="base.olb" ParentName="DIALOG_BASE"/><Canvas Name="C" WindowName="W"/><Block Name="B"><Item Name="I" ItemType="Text Item" CanvasName="C"/></Block></FormModule>'
        _, p, source = self.generate(xml, extra=['--olb', str(library)])
        self.assertEqual(p['windows'][0]['role'], 'dialog'); self.assertTrue(p['windows'][0]['modal'])
        self.assertEqual(p['windows'][0]['property_sources']['modal'], 'inherited')
        self.assertIn('Örökölt ablak', source)


if __name__ == '__main__': unittest.main()

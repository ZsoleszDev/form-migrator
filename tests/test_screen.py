import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from frm_forms.cli import main
from frm_forms.screen_model import display_text

ROOT = Path(__file__).resolve().parents[1]


class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def generate(self, source, config=None, extra=(), label='out'):
        src = source if isinstance(source, Path) else self.root / (label + '.xml')
        if not isinstance(source, Path): src.write_text(source, encoding='utf-8')
        out = self.root / label
        args = ['migrate', str(src), '--screen', '--frontend-only', '--module', 'testScreen', '--out', str(out), *extra]
        if config:
            path = self.root / (label + '-config.json'); path.write_text(json.dumps(config))
            args += ['--config', str(path)]
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            status = main(args)
        self.assertEqual(status, 0, err.getvalue())
        component = out / 'frontend/testScreen/testScreen.component.ts'
        return out, json.loads((out / 'analysis/screen-plan.json').read_text()), component.read_text()

    def test_fadlek_keeps_three_regions_nine_columns_and_checkbox_evidence(self):
        out, plan, source = self.generate(ROOT / 'review-output/fadlek/analysis/source.xml')
        # The Headstart LOV button beside the code field is folded into the
        # autocomplete's own opener, which then takes over its columns.
        self.assertEqual([(s['block'], s['mode'], len(s['items'])) for s in plan['sections']],
                         [('V_ELEK_ADLAP', 'form', 5), ('AIT', 'table', 9), ('CGNV$W01_1', 'form', 2)])
        self.assertEqual([i['col'] for i in plan['sections'][0]['items']], [3, 9, 4, 4, 4])
        self.assertEqual([(f['owner'], f['target'], f['target_widget']) for f in plan['folded_buttons']],
                         [('V_ELEK_ADLAP.UBI_INPTIP_KOD2', 'V_ELEK_ADLAP.UBI_INPTIP_KOD', 'autocomplete')])
        self.assertNotIn('UBI_INPTIP_KOD2', source)
        self.assertIn("labelText: 'Lekérdezés'", source); self.assertNotIn('btnLabel', source)
        self.assertEqual(plan['sections'][1]['records'], 15)
        self.assertEqual([s['name'] for s in plan['surfaces']], ['CG$PAGE_1'])
        self.assertEqual([i['widget'] for i in plan['sections'][0]['items']][-3:], ['checkbox'] * 3)
        self.assertIn("'Feldolgozatlan adatlapok lekérdezése'", source)
        self.assertNotIn('CALENDAR', source); self.assertNotIn('QMS$', source)
        self.assertNotIn('primeng', source); self.assertNotIn('pTemplate', source)
        self.assertEqual(len(list((out / 'frontend').rglob('*.ts'))), 1)
        notes = (out / 'frontend/testScreen/MIGRATION_NOTES.md').read_text()
        self.assertIn('ME_KERT_ERTEK', notes); self.assertIn('CG', notes)
        model = json.loads((out / 'analysis/ui-model.json').read_text())
        item = next(i for b in model['blocks'] for i in b['items'] if i['owner'] == 'V_ELEK_ADLAP.UBI_E04')
        self.assertEqual(item['widget'], 'checkbox')
        self.assertEqual(item['type_source'], 'inferred')
        self.assertIn('CheckedValue', item['inference_reason'])
        self.assertEqual(item['item_type'], 'Text Item')
        self.assertEqual(item['item_type_property_source'], 'explicit')
        self.assertEqual(item['representation'], 'boolean')
        self.assertTrue(item['validation']['required'])
        self.assertEqual(item['checkbox']['unchecked'], '0')
        self.assertEqual(len(plan['inferred_types']), 5)
        self.assertIn('### Beolvasztott listanyitó gombok', notes)
        folded_issue = next(i for i in model['issues'] if i['owner'] == 'V_ELEK_ADLAP.UBI_INPTIP_KOD2')
        self.assertEqual((folded_issue['code'], folded_issue['severity']), ('SCREEN_FOLDED_LIST_BUTTON', 'review'))
        self.assertIn('## Kikövetkeztetett típusok', notes)
        strict = json.loads((out / 'analysis/ui-model-strict.json').read_text())
        strict_item = next(i for b in strict['blocks'] for i in b['items'] if i['owner'] == item['owner'])
        self.assertEqual(strict_item['widget'], 'text')
        self.assertEqual(strict_item['type_source'], 'explicit')
        for screen in [i for s in plan['sections'] for i in s['items']]:
            audit = next(i for b in model['blocks'] for i in b['items'] if i['owner'] == screen['owner'])
            for key in ('widget', 'representation', 'type_source', 'inference_reason'):
                self.assertEqual(audit[key], screen[key])
        self.assertTrue((out / 'analysis/source.xml').read_bytes() == (ROOT / 'review-output/fadlek/analysis/source.xml').read_bytes())

    LOV_FORM = '''<FormModule Name="F" CoordinateSystem="Real" RealUnit="Pixel">
      <Block Name="B" DatabaseDataBlock="false">
        <Item Name="CODE" ItemType="Text Item" LOVName="L" CanvasName="C" XPosition="10" YPosition="10" Width="80" Height="20" Prompt="Kód"/>
        <Item Name="CODE_BTN" ItemType="Push Button" CanvasName="C" XPosition="92" YPosition="10" Width="20" Height="20">
          <Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="%s"/></Item>
        <Item Name="NAME" ItemType="Display Item" CanvasName="C" XPosition="120" YPosition="10" Width="200" Height="20" Prompt="Név"/>
      </Block>
      <Canvas Name="C" Width="400" Height="200"/><LOV Name="L" RecordGroupName="RG"/><RecordGroup Name="RG" RecordGroupQuery="SELECT 1 FROM DUAL"/>
    </FormModule>'''
    HEADSTART = "/* CGLY$WHEN_BUTTON_PRESSED */&amp;#10;BEGIN&amp;#10;  go_item('B.CODE');&amp;#10;  do_key('List_values');&amp;#10;  copy ('0', 'GLOBAL.save_mouse_record');&amp;#10;END;"

    def test_list_opener_recognises_only_pure_list_triggers(self):
        from frm_forms.screen_model import list_opener
        self.assertEqual(list_opener("BEGIN go_item('B.CODE'); do_key('List_values'); END;")['target'], 'B.CODE')
        self.assertEqual(list_opener("go_item('CODE'); LIST_VALUES(NO_RESTRICT);")['target'], 'CODE')
        self.assertEqual(list_opener("/* x */ go_item('B.X');&#10;-- note&#10;list_values; null;")['target'], 'B.X')
        found = list_opener("begin go_item('B.X'); do_key('LIST_VALUES'); copy('0','GLOBAL.save_mouse_record'); end;")
        self.assertEqual(found['ignored'], ["copy('0','GLOBAL.save_mouse_record')"])
        for body in ["IF :B.X IS NULL THEN go_item('B.X'); do_key('list_values'); END IF;",
                     "go_item('B.X'); do_key('list_values'); lekerdez;",
                     "do_key('list_values'); go_item('B.X');",
                     "go_item('B.X');",
                     "go_item(:system.cursor_item); do_key('list_values');",
                     "go_item('B.X'); do_key('list_values'); :B.Y := 1;",
                     "go_item('B.X'); do_key('list_values'); EXCEPTION WHEN OTHERS THEN NULL;", ""]:
            self.assertIsNone(list_opener(body), body)

    def test_list_button_folds_into_the_fields_own_opener(self):
        out, plan, source = self.generate(self.LOV_FORM % self.HEADSTART)
        self.assertEqual([i['owner'] for i in plan['sections'][0]['items']], ['B.CODE', 'B.NAME'])
        self.assertEqual(sum(i['col'] + i['col_before'] + i['col_after'] for i in plan['sections'][0]['items']), 12)
        self.assertEqual(plan['folded_buttons'][0]['ignored'], ["copy ('0', 'GLOBAL.save_mouse_record')"])
        self.assertNotIn('CODE_BTN', source)
        self.assertIn("ownId: 'B.CODE', formControlName: 'code', labelText: 'Kód', col: '3', dropdown: true, optionLabel: 'label', "
                      "optionValue: 'value', suggestions: [] }", source)  # no backend (frontend-only): no search method
        self.assertFalse(plan['actions'])

    def test_list_button_is_kept_when_its_trigger_does_more_or_target_has_no_opener(self):
        busy = self.HEADSTART.replace("END;", "  lekerdez;&amp;#10;END;")
        _, plan, source = self.generate(self.LOV_FORM % busy, label='busy')
        self.assertIn('B.CODE_BTN', [i['owner'] for i in plan['sections'][0]['items']])
        self.assertFalse(plan['folded_buttons']); self.assertIn('labelText:', source)
        plain = (self.LOV_FORM % self.HEADSTART).replace(' LOVName="L"', '')
        _, plan, _ = self.generate(plain, label='plain')
        self.assertIn('B.CODE_BTN', [i['owner'] for i in plan['sections'][0]['items']])
        kept = next(n for n in plan['notices'] if n['code'] == 'LIST_BUTTON_KEPT')
        self.assertIn('nincs saját lenyitó vezérlője', kept['detail'])

    def test_list_button_folding_and_caption_property_are_configurable(self):
        _, plan, source = self.generate(self.LOV_FORM % self.HEADSTART, {'screen_fold_list_buttons': False}, label='kept')
        self.assertIn('B.CODE_BTN', [i['owner'] for i in plan['sections'][0]['items']])
        self.assertFalse(plan['folded_buttons'])
        button = next(line for line in source.splitlines() if 'B.CODE_BTN' in line and "type: 'button'" in line)
        self.assertIn('labelText:', button); self.assertNotIn('btnLabel', button)
        _, _, source = self.generate(self.LOV_FORM % self.HEADSTART,
                                     {'screen_fold_list_buttons': False, 'screen_button_label_property': 'btnLabel'}, label='legacy')
        button = next(line for line in source.splitlines() if 'B.CODE_BTN' in line and "type: 'button'" in line)
        self.assertIn('btnLabel:', button); self.assertNotIn('labelText', button)

    def test_reviewed_button_override_keeps_the_list_button(self):
        rules = json.loads((ROOT / 'examples/fadlek-screen-overrides.json').read_text(encoding='utf-8'))
        path = self.root / 'rules.json'; path.write_text(json.dumps(rules))
        _, plan, source = self.generate(ROOT / 'review-output/fadlek/analysis/source.xml',
                                        extra=['--screen-overrides', str(path)], label='reviewed')
        self.assertFalse(plan['folded_buttons'])
        self.assertIn("labelText: 'Adatlap típusa…'", source)

    PICKER = "/* CGAP$OLES_SEQUENCE_BEFORE */&amp;#10;begin&amp;#10;   qms$calendar.key_listval;&amp;#10;end;"
    RANGE_FORM = '''<FormModule Name="QRY" Title="Lekérdezés" CoordinateSystem="Real" RealUnit="Pixel" FirstNavigationBlock="FILTER">
      <Block Name="FILTER" DatabaseDataBlock="false">
        <Item Name="TECH" ItemType="Text Item" CanvasName="P1" XPosition="0" YPosition="10" Width="80" Height="20" Hint="Adja meg a(z)  értékét"/>
        <Item Name="DATUM_TOL" ItemType="Text Item" DataType="Date" LOVName="CAL" CanvasName="P1" XPosition="100" YPosition="10" Width="120" Height="20" Prompt="Dátuma">
          <Trigger Name="KEY-LISTVAL" TriggerText="%(picker)s"/></Item>
        <Item Name="DATUM_IG" ItemType="Text Item" DataType="Date" LOVName="CAL" CanvasName="P1" XPosition="240" YPosition="10" Width="120" Height="20" Prompt="-">
          <Trigger Name="KEY-LISTVAL" TriggerText="%(picker)s"/></Item>
        <Item Name="STATUS" ItemType="Text Item" CanvasName="P1" XPosition="0" YPosition="50" Width="200" Height="20" Prompt="Státusz"/>
        <Item Name="KERES" ItemType="Push Button" CanvasName="P1" XPosition="300" YPosition="50" Width="80" Height="20" Label="Keresés">
          <Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="begin qms$event_item('WHEN-BUTTON-PRESSED'); end;&amp;#10;BEGIN go_block('RESULT'); execute_query; END;"/></Item>
      </Block>
      <Block Name="RESULT" QueryDataSourceName="T_RESULT" RecordsDisplayCount="5" WhereClause="rownum &lt;= 100">
        <Item Name="ID" ItemType="Text Item" CanvasName="P1" XPosition="0" YPosition="100" Width="80" Height="20" Prompt="Azonosító"/>
      </Block>
      <Block Name="CALENDAR" DatabaseDataBlock="false">%(cells)s
        <Item Name="OK" ItemType="Push Button" CanvasName="CALENDAR" XPosition="10" YPosition="200" Width="60" Height="20" Label="OK"/>
      </Block>
      <Canvas Name="P1" WindowName="W_MAIN" Width="600" Height="300"/>
      <Canvas Name="CALENDAR" WindowName="CALENDAR" Width="260" Height="240"/>
      <Window Name="W_MAIN" WindowStyle="Document"/><Window Name="CALENDAR" WindowStyle="Document"/>
      <LOV Name="CAL" RecordGroupName="RG"/><RecordGroup Name="RG" RecordGroupQuery="SELECT SYSDATE FROM DUAL"/>
    </FormModule>'''

    def range_form(self):
        return self.RANGE_FORM % {'picker': self.PICKER, 'cells': '<Item Name="CELL1" ItemType="Display Item" CanvasName="CALENDAR" XPosition="10" YPosition="40" Width="25" Height="20" Prompt="1"/><Item Name="CELL2" ItemType="Display Item" CanvasName="CALENDAR" XPosition="40" YPosition="40" Width="25" Height="20" Prompt="2"/><Item Name="CELL3" ItemType="Display Item" CanvasName="CALENDAR" XPosition="70" YPosition="40" Width="25" Height="20" Prompt="3"/><Item Name="CELL4" ItemType="Display Item" CanvasName="CALENDAR" XPosition="100" YPosition="40" Width="25" Height="20" Prompt="4"/><Item Name="CELL5" ItemType="Display Item" CanvasName="CALENDAR" XPosition="130" YPosition="40" Width="25" Height="20" Prompt="5"/><Item Name="CELL6" ItemType="Display Item" CanvasName="CALENDAR" XPosition="160" YPosition="40" Width="25" Height="20" Prompt="6"/><Item Name="CELL7" ItemType="Display Item" CanvasName="CALENDAR" XPosition="190" YPosition="40" Width="25" Height="20" Prompt="7"/><Item Name="CELL8" ItemType="Display Item" CanvasName="CALENDAR" XPosition="10" YPosition="65" Width="25" Height="20" Prompt="8"/><Item Name="CELL9" ItemType="Display Item" CanvasName="CALENDAR" XPosition="40" YPosition="65" Width="25" Height="20" Prompt="9"/><Item Name="CELL10" ItemType="Display Item" CanvasName="CALENDAR" XPosition="70" YPosition="65" Width="25" Height="20" Prompt="10"/><Item Name="CELL11" ItemType="Display Item" CanvasName="CALENDAR" XPosition="100" YPosition="65" Width="25" Height="20" Prompt="11"/><Item Name="CELL12" ItemType="Display Item" CanvasName="CALENDAR" XPosition="130" YPosition="65" Width="25" Height="20" Prompt="12"/><Item Name="CELL13" ItemType="Display Item" CanvasName="CALENDAR" XPosition="160" YPosition="65" Width="25" Height="20" Prompt="13"/><Item Name="CELL14" ItemType="Display Item" CanvasName="CALENDAR" XPosition="190" YPosition="65" Width="25" Height="20" Prompt="14"/><Item Name="CELL15" ItemType="Display Item" CanvasName="CALENDAR" XPosition="10" YPosition="90" Width="25" Height="20" Prompt="15"/><Item Name="CELL16" ItemType="Display Item" CanvasName="CALENDAR" XPosition="40" YPosition="90" Width="25" Height="20" Prompt="16"/><Item Name="CELL17" ItemType="Display Item" CanvasName="CALENDAR" XPosition="70" YPosition="90" Width="25" Height="20" Prompt="17"/><Item Name="CELL18" ItemType="Display Item" CanvasName="CALENDAR" XPosition="100" YPosition="90" Width="25" Height="20" Prompt="18"/><Item Name="CELL19" ItemType="Display Item" CanvasName="CALENDAR" XPosition="130" YPosition="90" Width="25" Height="20" Prompt="19"/><Item Name="CELL20" ItemType="Display Item" CanvasName="CALENDAR" XPosition="160" YPosition="90" Width="25" Height="20" Prompt="20"/><Item Name="CELL21" ItemType="Display Item" CanvasName="CALENDAR" XPosition="190" YPosition="90" Width="25" Height="20" Prompt="21"/><Item Name="CELL22" ItemType="Display Item" CanvasName="CALENDAR" XPosition="10" YPosition="115" Width="25" Height="20" Prompt="22"/><Item Name="CELL23" ItemType="Display Item" CanvasName="CALENDAR" XPosition="40" YPosition="115" Width="25" Height="20" Prompt="23"/><Item Name="CELL24" ItemType="Display Item" CanvasName="CALENDAR" XPosition="70" YPosition="115" Width="25" Height="20" Prompt="24"/><Item Name="CELL25" ItemType="Display Item" CanvasName="CALENDAR" XPosition="100" YPosition="115" Width="25" Height="20" Prompt="25"/><Item Name="CELL26" ItemType="Display Item" CanvasName="CALENDAR" XPosition="130" YPosition="115" Width="25" Height="20" Prompt="26"/><Item Name="CELL27" ItemType="Display Item" CanvasName="CALENDAR" XPosition="160" YPosition="115" Width="25" Height="20" Prompt="27"/><Item Name="CELL28" ItemType="Display Item" CanvasName="CALENDAR" XPosition="190" YPosition="115" Width="25" Height="20" Prompt="28"/><Item Name="CELL29" ItemType="Display Item" CanvasName="CALENDAR" XPosition="10" YPosition="140" Width="25" Height="20" Prompt="29"/><Item Name="CELL30" ItemType="Display Item" CanvasName="CALENDAR" XPosition="40" YPosition="140" Width="25" Height="20" Prompt="30"/><Item Name="CELL31" ItemType="Display Item" CanvasName="CALENDAR" XPosition="70" YPosition="140" Width="25" Height="20" Prompt="31"/><Item Name="CELL32" ItemType="Display Item" CanvasName="CALENDAR" XPosition="100" YPosition="140" Width="25" Height="20" Prompt="32"/><Item Name="CELL33" ItemType="Display Item" CanvasName="CALENDAR" XPosition="130" YPosition="140" Width="25" Height="20" Prompt="33"/><Item Name="CELL34" ItemType="Display Item" CanvasName="CALENDAR" XPosition="160" YPosition="140" Width="25" Height="20" Prompt="34"/><Item Name="CELL35" ItemType="Display Item" CanvasName="CALENDAR" XPosition="190" YPosition="140" Width="25" Height="20" Prompt="35"/><Item Name="CELL36" ItemType="Display Item" CanvasName="CALENDAR" XPosition="10" YPosition="165" Width="25" Height="20" Prompt="36"/><Item Name="CELL37" ItemType="Display Item" CanvasName="CALENDAR" XPosition="40" YPosition="165" Width="25" Height="20" Prompt="37"/><Item Name="CELL38" ItemType="Display Item" CanvasName="CALENDAR" XPosition="70" YPosition="165" Width="25" Height="20" Prompt="38"/><Item Name="CELL39" ItemType="Display Item" CanvasName="CALENDAR" XPosition="100" YPosition="165" Width="25" Height="20" Prompt="39"/><Item Name="CELL40" ItemType="Display Item" CanvasName="CALENDAR" XPosition="130" YPosition="165" Width="25" Height="20" Prompt="40"/><Item Name="CELL41" ItemType="Display Item" CanvasName="CALENDAR" XPosition="160" YPosition="165" Width="25" Height="20" Prompt="41"/><Item Name="CELL42" ItemType="Display Item" CanvasName="CALENDAR" XPosition="190" YPosition="165" Width="25" Height="20" Prompt="42"/>'}

    def test_framework_calendar_block_is_replaced_by_native_date_pickers(self):
        out, plan, source = self.generate(self.range_form())
        self.assertNotIn('CALENDAR.CELL', source); self.assertNotIn('calendar', source.lower().replace("'calendar'", ''))
        self.assertEqual([b['block'] for b in plan['framework_blocks']], ['CALENDAR'])
        dates = {i['owner']: i for s in plan['sections'] for i in s['items'] if i['owner'].startswith('FILTER.DATUM')}
        self.assertEqual({i['widget'] for i in dates.values()}, {'date'})
        self.assertFalse(any(i['lov'] for i in dates.values())); self.assertFalse(plan['lookups'])
        self.assertIn('showIcon: true', source); self.assertNotIn("this.lov('FILTER.DATUM", source)
        notes = (out / 'frontend/testScreen/MIGRATION_NOTES.md').read_text()
        self.assertIn('## Keretrendszer-blokkok', notes); self.assertIn('KEY-LISTVAL a katalógusban', notes)

    def test_range_separator_prompt_sits_between_the_two_fields(self):
        _, plan, source = self.generate(self.range_form(), label='range')
        section = next(s for s in plan['sections'] if s['block'] == 'FILTER')
        second = next(i for i in section['items'] if i['owner'] == 'FILTER.DATUM_IG')
        self.assertEqual((second['separator'], second['separator_col'], second['label']), ('-', 1, ''))
        row = [i for i in section['items'] if i['row'] == second['row']]
        self.assertEqual(sum(i['col'] + i['col_before'] + i['col_after'] + i.get('separator_col', 0) for i in row), 12)
        lines = source.splitlines()
        at = next(n for n, line in enumerate(lines) if 'FILTER.DATUM_IG#separator' in line)
        self.assertIn("type: 'label'", lines[at]); self.assertIn("labelText: '-'", lines[at])
        self.assertIn("ownId: 'FILTER.DATUM_IG'", lines[at + 1]); self.assertIn("labelText: ''", lines[at + 1])
        preview = (self.root / 'range/analysis/layout-preview.html').read_text(encoding='utf-8')
        self.assertIn('title="FILTER.DATUM_IG"', preview); self.assertIn('cell sep', preview)
        self.assertIn('grid-column:1 / span', preview); self.assertNotIn('<script', preview)

    def test_separator_without_a_field_before_it_stays_a_label(self):
        xml = self.range_form().replace('Prompt="Dátuma"', 'Prompt="-"').replace('XPosition="240" YPosition="10"', 'XPosition="240" YPosition="80"')
        _, plan, source = self.generate(xml, label='lonely')
        first = next(i for s in plan['sections'] for i in s['items'] if i['owner'] == 'FILTER.DATUM_TOL')
        self.assertEqual((first['label'], first['separator']), ('-', ''))
        self.assertNotIn('FILTER.DATUM_TOL#separator', source)

    def test_unfilled_hint_template_item_is_not_rendered(self):
        out, plan, source = self.generate(self.range_form(), label='hint')
        self.assertNotIn('FILTER.TECH', source)
        hidden = next(h for h in plan['hidden_items'] if h['owner'] == 'FILTER.TECH')
        self.assertIn('kitöltetlen sablon', hidden['reason'])
        from frm_forms import framework
        catalog = framework.load({})
        self.assertTrue(framework.empty_hint('Adja meg a(z) értékét', catalog))
        self.assertFalse(framework.empty_hint('Adatlap megnevezés', catalog))

    def test_recognised_button_steps_travel_with_the_action(self):
        out, plan, source = self.generate(self.range_form(), label='steps')
        action = next(a for a in plan['actions'] if a['owner'] == 'FILTER.KERES')
        self.assertEqual(action['steps'], [{'op': 'goBlock', 'block': 'RESULT'}, {'op': 'executeQuery'}])
        self.assertEqual(action['framework_calls'], ["qms$event_item('WHEN-BUTTON-PRESSED')"])
        # frontend-only: no query of RESULT to call, the button has a TODO with its original code (4.27)
        click = source[source.index('  protected onKeresClick(): void {'):]
        self.assertIn('    // TODO: a gomb kódját kézzel kell átültetni.\n    //#region Eredeti Forms-kód\n', click)
        self.assertIn('execute_query', click.split('//#endregion', 1)[0].lower())
        self.assertNotIn('actionRequested', source)  # routed component: no output events
        notes = (out / 'frontend/testScreen/MIGRATION_NOTES.md').read_text()
        self.assertIn('## Migrációs teendők', notes); self.assertIn('1 felismert gomb', notes)
        self.assertIn('## Oracle-specifikus SQL', notes); self.assertIn('ROWNUM', notes)

    def test_custom_catalog_can_keep_the_calendar_and_bad_catalogs_are_rejected(self):
        catalog = self.root / 'catalog.json'
        catalog.write_text(json.dumps({'version': 1, 'call_prefixes': ['qms$'], 'blocks': {}, 'date_picker_calls': []}))
        _, plan, source = self.generate(self.range_form(), {'framework_catalog': str(catalog)}, label='own-catalog')
        self.assertIn('CALENDAR.CELL1', source); self.assertFalse(plan['framework_blocks'])
        catalog.write_text(json.dumps({'version': 2}))
        config = self.root / 'bad.json'; config.write_text(json.dumps({'framework_catalog': str(catalog)}))
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            status = main(['migrate', str(self.root / 'own-catalog.xml'), '--screen', '--frontend-only',
                           '--out', str(self.root / 'bad-out'), '--config', str(config)])
        self.assertNotEqual(status, 0); self.assertIn('FRAMEWORK_CATALOG', err.getvalue())

    def test_source_order_does_not_move_buttons_above_filters(self):
        _, p, _ = self.generate('''<FormModule Name="F" CoordinateSystem="Real" RealUnit="Pixel"><Canvas Name="C"/>
          <Block Name="BOTTOM"><Item Name="GO" ItemType="Push Button" CanvasName="C" XPosition="0" YPosition="500" Width="30"/></Block>
          <Block Name="TOP"><Item Name="F" ItemType="Text Item" CanvasName="C" XPosition="0" YPosition="20" Width="30"/></Block></FormModule>''')
        self.assertEqual([s['block'] for s in p['sections']], ['TOP', 'BOTTOM'])

    def test_non_database_rows_and_single_record_controls_in_same_block(self):
        _, p, source = self.generate('''<FormModule Name="F" CoordinateSystem="Real" RealUnit="Pixel"><Block Name="B" RecordsDisplayCount="5">
          <Item Name="ROW" ItemType="Text Item" ItemsDisplay="7"/><Item Name="GO" ItemType="Push Button" ItemsDisplay="1"/>
          </Block></FormModule>''')
        self.assertEqual(sorted(s['mode'] for s in p['sections']), ['form', 'table'])
        self.assertEqual(next(s for s in p['sections'] if s['mode'] == 'table')['records'], 7)
        self.assertIn('<wf-table [value]="bRows" [columns]="bColumns" [rows]="7" [(selection)]="bSelection" />', source)

    def test_tabs_and_accordion_use_real_components_and_preserve_regions(self):
        for mode in ['tabs', 'accordion']:
            _, plan, source = self.generate(ROOT / 'examples/ui-features_fmb.xml', {'screen_tab_layout': mode}, label=mode)
            self.assertIn('<p-' + mode, source)
            self.assertNotIn('*ngFor', source)
            self.assertEqual(len(plan['surfaces'][0]['tabs']), 2)
            self.assertIn('onFormGroupGenerated(', source)

    def test_canvasless_fields_are_preserved_in_analysis(self):
        _, plan, source = self.generate('''<FormModule Name="F" CoordinateSystem="Real" RealUnit="Pixel"><Canvas Name="C"/>
          <Block Name="B"><Item Name="TECHNICAL" ItemType="Text Item"/><Item Name="VISIBLE" ItemType="Text Item" CanvasName="C"/>
          <Item Name="HIDDEN" ItemType="Text Item" CanvasName="C" Visible="false"/></Block></FormModule>''')
        self.assertEqual({i['owner'] for i in plan['hidden_items']}, {'B.TECHNICAL', 'B.HIDDEN'})
        self.assertNotIn('TECHNICAL', source); self.assertNotIn('B.HIDDEN', source)

    def test_no_canvas_fixture_can_still_generate_flow_layout(self):
        _, plan, source = self.generate('''<FormModule Name="F" CoordinateSystem="Real" RealUnit="Pixel"><Block Name="B">
          <Item Name="A" ItemType="Text Item"/><Item Name="B" ItemType="Text Item"/></Block></FormModule>''')
        self.assertEqual([i['col'] for i in plan['sections'][0]['items']], [12, 12])

    def test_widget_inference_can_be_disabled_and_ambiguous_evidence_is_not_guessed(self):
        xml = '''<FormModule Name="F" CoordinateSystem="Real" RealUnit="Pixel"><Block Name="B">
          <Item Name="X" ItemType="Text Item" CheckedValue="Y" UncheckedValue="N"><Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="NULL;"/></Item>
          </Block></FormModule>'''
        _, plan, _ = self.generate(xml)
        self.assertEqual(plan['sections'][0]['items'][0]['widget'], 'unsupported')
        _, plan, _ = self.generate(xml, {'screen_infer_widgets': False}, label='off')
        self.assertEqual(plan['sections'][0]['items'][0]['widget'], 'text')

    def test_graphics_fieldsets_and_modal_windows(self):
        _, plan, source = self.generate('''<FormModule Name="F" CoordinateSystem="Real" RealUnit="Pixel"><Window Name="W" Modal="true" Title="Részletek"/>
          <Canvas Name="C" WindowName="W"><Graphics Name="FRAME" GraphicsType="Frame" FrameTitle="Személy" XPosition="0" YPosition="0" Width="100" Height="100"/>
          <Graphics Name="TXT" GraphicsType="Text" XPosition="0" YPosition="110"><CompoundText><TextSegment Text="Tájékoztató"/></CompoundText></Graphics></Canvas>
          <Block Name="B"><Item Name="NAME" ItemType="Text Item" CanvasName="C" XPosition="10" YPosition="10" Width="80"/></Block></FormModule>''')
        self.assertIn('<p-fieldset', source); self.assertIn('<p-dialog', source)
        self.assertFalse(plan['window_controls']['windows']['W']); self.assertIn('Tájékoztató', source)

    def test_inherited_effective_widgets_remain_correct(self):
        _, plan, source = self.generate(ROOT / 'examples/inheritance/checkbox_fmb.xml', extra=['--olb', str(ROOT / 'examples/inheritance/qmsolb65_olb.xml'), '--olb', str(ROOT / 'examples/inheritance/base_olb.xml')])
        self.assertTrue(any(i['widget'] == 'checkbox' for s in plan['sections'] for i in s['items']))
        self.assertIn('startValue: false', source)

    def test_regeneration_keeps_edited_component_and_mode_change_is_rejected(self):
        xml = '<FormModule Name="F" CoordinateSystem="Real" RealUnit="Pixel"><Block Name="B"><Item Name="A" ItemType="Text Item"/></Block></FormModule>'
        out, _, _ = self.generate(xml)
        component = out / 'frontend/testScreen/testScreen.component.ts'; component.write_text('// developer code\n')
        self.generate(xml, extra=['--regenerate'])
        self.assertEqual(component.read_text(), '// developer code\n')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            result = main(['migrate', str(self.root / 'out.xml'), '--out', str(out), '--regenerate'])
        self.assertEqual(result, 1); self.assertIn('GENERATION_MODE_CHANGED', err.getvalue())

    def test_checkbox_false_is_not_required_true_and_no_automatic_http(self):
        _, _, source = self.generate('''<FormModule Name="F" CoordinateSystem="Real" RealUnit="Pixel"><Block Name="B">
          <Item Name="C" ItemType="Check Box" CheckedValue="1" UncheckedValue="0" Required="true" InitialValue="0"/>
          <Item Name="GO" ItemType="Push Button"/></Block></FormModule>''')
        self.assertIn('startValue: false', source); self.assertNotIn('validator: true', source)
        self.assertIn("type: 'checkBox', ownId: 'B.C', formControlName: 'c', labelText: 'C', col: '12', binary: true", source)
        self.assertNotIn('Validators', source); self.assertNotIn('HttpClient', source)  # 4.26: no own validators

    def test_validation_rules_have_implementation_or_explicit_gap(self):
        out, plan, source = self.generate('''<FormModule Name="F" CoordinateSystem="Real" RealUnit="Pixel"><Block Name="B">
          <Item Name="TEXT" ItemType="Text Item" Required="true" MaximumLength="4" FixedLength="true" CaseRestriction="Uppercase" AutoSkip="true" NavigationStyle="Same Record"/>
          <Item Name="NUMBER" ItemType="Text Item" DataType="Number" Precision="30" Scale="2" LowestAllowedValue="0.01" HighestAllowedValue="99999999999999999999.99" FormatMask="FM999D00"/>
          <Item Name="INT" ItemType="Text Item" DataType="Integer" Precision="5" Scale="0" LowestAllowedValue="1.5" HighestAllowedValue="9.5"/>
          <Item Name="DATE" ItemType="Text Item" DataType="Date" FormatMask="YYYY-MM-DD"/>
          </Block></FormModule>''')
        # required, the lengths and the pattern are FormBlock properties; 4.26: no validators of our own
        self.assertIn("validator: true, maxLenght: 4, minLenght: 4, regexRule: { regex: /^[^\\p{Ll}]*$/u, example: '' }", source)
        self.assertNotRegex(source, r'\bValidators\b')
        self.assertIn('min: 2, max: 9', source)  # the integer's bounds: FormBlock min/max
        self.assertIn("dateFormat: 'yy-mm-dd'", source)
        rules = {(r['owner'], r['property']): r for r in plan['validation_audit']}
        self.assertEqual(rules['B.INT', 'LowestAllowedValue']['status'], 'implemented')
        self.assertEqual(rules['B.NUMBER', 'LowestAllowedValue']['status'], 'manual')  # a decimal range: the backend checks it
        self.assertEqual(rules['B.NUMBER', 'FormatMask']['status'], 'manual')
        self.assertEqual(rules['B.TEXT', 'CaseRestriction']['status'], 'partial')
        self.assertEqual(rules['B.DATE', 'FormatMask']['status'], 'implemented')
        notes = (out / 'frontend/testScreen/MIGRATION_NOTES.md').read_text()
        for expected in ('FM999D00', 'LowestAllowedValue', 'HighestAllowedValue', 'CaseRestriction=Uppercase', 'AutoSkip=true', 'NavigationStyle=Same Record'):
            self.assertIn(expected, notes)

    def test_sql_notes_decode_breaks_without_changing_source(self):
        out, plan, _ = self.generate('''<FormModule Name="F" CoordinateSystem="Real" RealUnit="Pixel"><Block Name="B"
          WhereClause="X=1&amp;#10;AND Y=2" OrderByClause="X&amp;#x0A;,Y&amp;#13;&amp;#10;,Z">
          <Item Name="A" ItemType="Text Item" LOVName="L"/></Block>
          <LOV Name="L" RecordGroupName="RG"/><RecordGroup Name="RG" RecordGroupQuery="SELECT A -- comment&amp;#10;FROM T&amp;#9;WHERE X=1"/></FormModule>''')
        notes = (out / 'frontend/testScreen/MIGRATION_NOTES.md').read_text()
        self.assertIn('X=1\nAND Y=2', notes)
        self.assertIn('X\n,Y\n,Z', notes)
        self.assertIn('SELECT A -- comment\nFROM T\tWHERE X=1', notes)
        self.assertNotIn('&#10;', notes)
        self.assertIn('&#10;', plan['blocks'][0]['where'])
        self.assertIn('&amp;#10;', (out / 'analysis/source.xml').read_text())

    def test_graphics_audit_includes_used_duplicate_hidden_and_unattached_elements(self):
        out, plan, source = self.generate('''<FormModule Name="F" Title="Cím" CoordinateSystem="Real" RealUnit="Pixel">
          <Canvas Name="C"><Graphics Name="FRAME" GraphicsType="Frame" FrameTitle="Csoport" XPosition="0" YPosition="0" Width="100" Height="100"/>
          <Graphics Name="TITLE1" GraphicsType="Text" Text="Cím"/><Graphics Name="TITLE2" GraphicsType="Text" Text="Cím"/>
          <Graphics Name="GROUP_LABEL" GraphicsType="Text" Text="Másik csoport" YPosition="120"/>
          <Graphics Name="HIDDEN" GraphicsType="Text" Text="Rejtett" Visible="false"/>
          <Graphics Name="EMPTY_FRAME" GraphicsType="Frame" FrameTitle="Üres" XPosition="200" YPosition="200" Width="10" Height="10"/>
          <TabPage Name="HIDDEN_TAB" Visible="false"><Graphics Name="ON_HIDDEN_TAB" GraphicsType="Text" Text="Rejtett fülszöveg"/></TabPage></Canvas>
          <Block Name="B"><Item Name="A" ItemType="Text Item" CanvasName="C" XPosition="10" YPosition="10" Width="40"/></Block></FormModule>''')
        graphics = {g['name']: g for g in plan['graphics']}
        self.assertEqual(graphics['FRAME']['status'], 'rendered')
        self.assertIn('p-fieldset.legend', graphics['FRAME']['target'])
        for title in ('TITLE1', 'TITLE2'):
            self.assertEqual(graphics[title]['status'], 'deduplicated')
        self.assertEqual(graphics['GROUP_LABEL']['status'], 'rendered')
        for hidden in ('HIDDEN', 'EMPTY_FRAME', 'ON_HIDDEN_TAB'):
            self.assertEqual(graphics[hidden]['status'], 'unused')
            self.assertTrue(graphics[hidden]['reason'])
        notes = (out / 'frontend/testScreen/MIGRATION_NOTES.md').read_text()
        self.assertIn('## Boilerplate és grafikai elemek', notes)
        for name in graphics: self.assertIn(name, notes)
        self.assertNotIn('Rejtett fülszöveg', source)

    def test_block_permissions_and_hidden_property_gaps_are_documented(self):
        out, plan, _ = self.generate('''<FormModule Name="F" CoordinateSystem="Real" RealUnit="Pixel">
          <Block Name="B" InsertAllowed="false" UpdateAllowed="true" DeleteAllowed="false" QueryAllowed="true">
          <Item Name="A" ItemType="Text Item" AutoSkip="false" CaseRestriction="Mixed"/>
          <Item Name="H" ItemType="Text Item" Visible="false" AutoSkip="true" NavigationStyle="Change Record" Required="true"/>
          </Block><Block Name="DEFAULTS"><Item Name="X" ItemType="Text Item"/></Block></FormModule>''')
        b = plan['blocks'][0]
        self.assertEqual(b['allowed_operations'], {'insert': False, 'update': True, 'delete': False, 'query': True})
        self.assertTrue(all(v == 'explicit' for v in b['operation_sources'].values()))
        self.assertTrue(all(v == 'default' for v in plan['blocks'][1]['operation_sources'].values()))
        self.assertTrue(any(r['owner'] == 'B.H' and r['location'] == 'hidden' for r in plan['property_gaps']))
        notes = (out / 'frontend/testScreen/MIGRATION_NOTES.md').read_text()
        self.assertIn('| B | false (explicit) | true (explicit) | false (explicit) | true (explicit) |', notes)
        self.assertIn('CaseRestriction=Mixed; AutoSkip=false', notes)
        self.assertIn('B.H | Rejtett/technikai', notes)
        self.assertIn('NavigationStyle=Change Record', notes)

    def test_unsupported_metadata_retained_and_backend_cannot_run(self):
        xml = '<FormModule Name="F" CoordinateSystem="Real" RealUnit="Pixel"><Block Name="B" QueryDataSourceName="TABLE1"><Item Name="A" ItemType="Text Item" FontName="Arial"/></Block></FormModule>'
        out, plan, _ = self.generate(xml)
        self.assertIn('UNMAPPED_ATTRIBUTE', plan['ui_issue_counts'])
        summary = json.loads((out / 'analysis/summary.json').read_text())
        self.assertEqual(summary['readable_blocks'], 0); self.assertEqual(summary['writable_blocks'], 0)

    def test_mojibake_repair_is_reversible_and_optional(self):
        source = 'Lekérdezés'.encode('utf-8').decode('cp1250'); changes = []
        self.assertEqual(display_text(source, True, changes), 'Lekérdezés')
        self.assertEqual(display_text(source, False, []), source)
        self.assertEqual(display_text('Árvíztűrő tükörfúrógép', True, []), 'Árvíztűrő tükörfúrógép')
        self.assertEqual(len(changes), 1)

    def test_lov_return_metadata_includes_cross_block_target(self):
        _, plan, source = self.generate('''<FormModule Name="F" CoordinateSystem="Real" RealUnit="Pixel"><Block Name="A"><Item Name="CODE" ItemType="Text Item" LOVName="L"/></Block>
          <Block Name="B"><Item Name="NAME" ItemType="Display Item"/></Block><LOV Name="L" RecordGroupName="RG"><LOVColumnMapping ColumnName="NAME" ReturnItem="B.NAME"/></LOV>
          <RecordGroup Name="RG" RecordGroupQuery="SELECT NAME FROM T WHERE ID=:A.CODE"/></FormModule>''')
        self.assertEqual(plan['lookups'][0]['return_items'], ['B.NAME'])
        self.assertIn("ownId: 'A.CODE', formControlName: 'code', labelText: 'CODE', col: '12', dropdown: true", source)


if __name__ == '__main__': unittest.main()

"""4.26/4.27: the generated screen is a frame: FormBlock regions, wf-tables, endpoints, the requests of the buttons.

The component extends the company ServiceBase. 4.27: it has no Forms emulation; its endpoint methods and, per button,
the button's HTTP request built simply from the screen's values (or its navigation, or a TODO with the original
Forms code) are all it has. The shared wf-table.ts wraps the p-table: every p-table input, output and template
(#header, #body, #caption ...) can be given to it. With tsc (FRM_TSC or on PATH) the screens are type-checked with the tsconfig
of a new Angular CLI project against stubs (tests/ts_stubs); with the Angular compiler (FRM_NGC=<path to ngc>) the
templates too (strictTemplates), a developer's own wf-table templates included.
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

from java_support import COMPANY_IMPORTS
from frm_forms.angular_screen import wf_table_source
from frm_forms.cli import main
from screen_support import ANGULAR_CLI_OPTIONS, component, ngc_check, screen_field, screen_method, with_company_imports

ROOT = Path(__file__).resolve().parents[1]
REPLICA = ROOT / 'tests' / 'fixtures' / 'felmeres_replika_fmb.xml'
SIMPLE = '''<Module><FormModule Name="EGYSZERU" Title="Egyszerű"><Coordinate CoordinateSystem="Real" RealUnit="Pixel"/>
 <Block Name="B" DatabaseDataBlock="false"><Item Name="NEV" ItemType="Text Item" DataType="Char" Prompt="Név" CanvasName="C"
  XPosition="10" YPosition="10" Width="100" Height="20"/>
  <Item Name="AKTIV" ItemType="Check Box" Prompt="Aktív" CheckedValue="I" UncheckedValue="N" CanvasName="C"
   XPosition="120" YPosition="10" Width="80" Height="20">
   <Trigger Name="WHEN-CHECKBOX-CHANGED" TriggerText="IF :B.AKTIV = &apos;I&apos; THEN set_item_property(&apos;B.NEV&apos;, ENABLED, PROPERTY_TRUE);
ELSE set_item_property(&apos;B.NEV&apos;, ENABLED, PROPERTY_FALSE); END IF;"/></Item></Block>
 <Canvas Name="C" CanvasType="Content" WindowName="W"/><Window Name="W" Title="Egyszerű"/></FormModule></Module>'''

# A list screen without buttons: backend calls only.
LISTAS = '''<Module><FormModule Name="LISTAS" Title="Listás"><Coordinate CoordinateSystem="Real" RealUnit="Pixel"/>
 <Block Name="PARTNER" DatabaseDataBlock="true" QueryDataSourceName="PARTNER" RecordsDisplayCount="5">
  <Item Name="KOD" ItemType="Text Item" DataType="Char" Prompt="Kód" CanvasName="C" ColumnName="KOD" PrimaryKey="true"
   XPosition="10" YPosition="10" Width="100" Height="20"/>
  <Item Name="NEV" ItemType="Text Item" DataType="Char" Prompt="Név" CanvasName="C" ColumnName="NEV"
   XPosition="120" YPosition="10" Width="100" Height="20"/></Block>
 <Canvas Name="C" CanvasType="Content" WindowName="W"/><Window Name="W" Title="Listás"/></FormModule></Module>'''


# A dialog window with tab pages, a hidden stacked canvas, a frame, and buttons whose steps open and close them.
DIALOGS = '''<Module><FormModule Name="ABLAKOS" Title="Ablakos" FirstNavigationBlock="B"><Coordinate CoordinateSystem="Real" RealUnit="Pixel"/>
 <Window Name="MAIN" Title="Ablakos"/><Window Name="EDIT" Title="Szerkesztés" WindowStyle="Dialog" Modal="true"/>
 <Canvas Name="PAGE" CanvasType="Content" WindowName="MAIN">
  <Graphics Name="KERET" GraphicsType="Frame" FrameTitle="Adatok" XPosition="0" YPosition="0" Width="400" Height="60"/></Canvas>
 <Canvas Name="EXTRA" CanvasType="Stacked" WindowName="MAIN" Visible="false"/>
 <Canvas Name="EDIT_C" CanvasType="Content" WindowName="EDIT"/>
 <Canvas Name="TABS" CanvasType="Tab" WindowName="EDIT"><TabPage Name="P1" Label="Első"/><TabPage Name="P2" Label="Második"/></Canvas>
 <Block Name="B" DatabaseDataBlock="false">
  <Item Name="NEV" ItemType="Text Item" Prompt="Név" CanvasName="PAGE" XPosition="10" YPosition="10" Width="100" Height="20"/>
  <Item Name="PB_NYIT" ItemType="Push Button" Label="Nyit" CanvasName="PAGE" XPosition="120" YPosition="10" Width="80" Height="20">
   <Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="show_window('EDIT'); show_view('EXTRA');"/></Item>
  <Item Name="E" ItemType="Text Item" Prompt="Extra" CanvasName="EXTRA" XPosition="10" YPosition="100" Width="100" Height="20"/>
  <Item Name="D" ItemType="Text Item" Prompt="Dialógus" CanvasName="EDIT_C" XPosition="10" YPosition="10" Width="100" Height="20"/>
  <Item Name="T1" ItemType="Text Item" Prompt="Első mező" CanvasName="TABS" TabPageName="P1" XPosition="10" YPosition="10" Width="100" Height="20"/>
  <Item Name="T2" ItemType="Text Item" Prompt="Második mező" CanvasName="TABS" TabPageName="P2" XPosition="10" YPosition="10" Width="100" Height="20"/>
  <Item Name="PB_BEZAR" ItemType="Push Button" Label="Bezár" CanvasName="EDIT_C" XPosition="120" YPosition="10" Width="80" Height="20">
   <Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="hide_window('EDIT');"/></Item>
 </Block>
</FormModule></Module>'''

# One saved record per screen: a checkbox and a date between the screen (true/false, Date) and the record (I/N, ISO text).
TORZS = '''<Module><FormModule Name="TORZS" Title="Törzs"><Coordinate CoordinateSystem="Real" RealUnit="Pixel"/>
 <Block Name="T" DatabaseDataBlock="true" QueryDataSourceName="TORZS" RecordsDisplayCount="1">
  <Item Name="ID" ItemType="Text Item" DataType="Number" Prompt="Azonosító" CanvasName="C" ColumnName="ID" PrimaryKey="true"
   XPosition="10" YPosition="10" Width="80" Height="20"/>
  <Item Name="AKTIV" ItemType="Check Box" DataType="Char" Prompt="Aktív" CheckedValue="I" UncheckedValue="N" CanvasName="C"
   ColumnName="AKTIV" XPosition="100" YPosition="10" Width="80" Height="20"/>
  <Item Name="ERVENYES" ItemType="Text Item" DataType="Date" Prompt="Érvényes" CanvasName="C" ColumnName="ERVENYES"
   XPosition="190" YPosition="10" Width="100" Height="20"/></Block>
 <Block Name="CTRL" DatabaseDataBlock="false">
  <Item Name="PB_KERES" ItemType="Push Button" Label="Keresés" CanvasName="C" XPosition="10" YPosition="40" Width="80" Height="20">
   <Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="go_block('T'); execute_query;"/></Item></Block>
 <Canvas Name="C" CanvasType="Content" WindowName="W"/><Window Name="W" Title="Törzs"/></FormModule></Module>'''

# A developer's own table: extra filters in the caption, an own row, the p-table inputs it wants.
OWN_TABLE = '''import { Component } from '@angular/core';
import { WfTable } from '../wf-table';

@Component({
  selector: 'app-sajat-tabla',
  standalone: true,
  imports: [WfTable],
  template: `
    <wf-table [value]="rows" [columns]="columns" [rows]="25" [globalFilterFields]="['nev']" [sortField]="'nev'" [lazy]="true"
      [rowsPerPageOptions]="[10, 25, 50]" (onLazyLoad)="load($event)" (onSort)="sorted = true" [(selection)]="selected">
      <ng-template #caption><input type="text" placeholder="Keresés" /></ng-template>
      <ng-template #header let-columns><tr>@for (col of columns; track col.field) { <th>{{ col.header }}</th> }</tr></ng-template>
      <ng-template #body let-row let-columns="columns" let-rowIndex="rowIndex">
        <tr><td>{{ rowIndex }}</td>@for (col of columns; track col.field) { <td>{{ row[col.field] }}</td> }</tr>
      </ng-template>
      <ng-template #emptymessage><tr><td>Üres</td></tr></ng-template>
    </wf-table>
  `,
})
export class SajatTablaComponent {
  protected readonly columns = [{ field: 'nev', header: 'Név' }];
  protected rows: Record<string, unknown>[] = [];
  protected selected: Record<string, unknown> | null = null;
  protected sorted = false;
  protected load(event: unknown): void { void event; }
}
'''


def generate(root: Path, source: Path, module: str, *extra: str) -> Path:
    config = root / (module + '.json')
    config.write_text(json.dumps({'java_company_imports': COMPANY_IMPORTS, 'backend_live': True,
                                  'screen_window_selection': 'all', 'screen_primary_window_auto': True}))
    out = root / module
    with contextlib.redirect_stdout(io.StringIO()):
        assert main(['migrate', str(source), '--out', str(out), '--screen', '--module', module, '--config', str(config), *extra]) == 0
    return out


class ScreenComponentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('FRM_JAVA_IMPORT_MAP', '-')
        os.environ.setdefault('FRM_JAVA_VARIABLE_MAP', '-')  # nor a developer's own java-variables.json
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.out = generate(cls.root, REPLICA, 'rendeles')
        outputs = {}
        for module, xml in (('egyszeru', SIMPLE), ('listas', LISTAS), ('ablakos', DIALOGS), ('torzs', TORZS)):
            source = cls.root / (module + '_fmb.xml')
            source.write_text(xml, encoding='utf-8')
            outputs[module] = generate(cls.root, source, module, *(['--frontend-only'] if module == 'ablakos' else []))
        cls.simple, cls.listas, cls.dialogs, cls.torzs = outputs['egyszeru'], outputs['listas'], outputs['ablakos'], outputs['torzs']
        cls.outputs = (cls.out, cls.simple, cls.listas, cls.dialogs, cls.torzs)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_no_shared_runtime_and_the_table_is_wf_table(self):
        for out in self.outputs:
            self.assertFalse((out / 'frontend/frm-forms-screen.ts').exists())
            self.assertIn(' extends ServiceBase {', component(out))
            self.assertNotIn('override', component(out))
        screen = component(self.out)
        self.assertEqual((self.out / 'frontend/wf-table.ts').read_text(encoding='utf-8'), wf_table_source())
        self.assertIn("import { WfTable } from '../wf-table';", screen)
        self.assertIn('<wf-table [value]="tetelRows" [columns]="tetelColumns" [rows]="5" [(selection)]="tetelSelection" />', screen)
        self.assertIn("    { field: 'cikk', header: 'Cikk', width: '45.45%' },", screen)  # the backend DTO field names
        self.assertNotRegex(screen, r'<frm-|Frm[A-Z]')
        self.assertFalse((self.simple / 'frontend/wf-table.ts').exists())  # no table: no wf-table.ts
        self.assertIn('// TODO: importáld a saját csomagodból: ServiceBase, WFF (java-imports.json).', screen)

    def test_wf_table_passes_the_p_table_api_and_the_templates(self):
        source = wf_table_source()
        self.assertIn("selector: 'wf-table',", source)
        self.assertIn("export const WF_TABLE_VERSION = '1';", source)
        for binding in ('[value]="value()"', '[globalFilterFields]="globalFilterFields()"', '[lazy]="lazy()"', '[sortField]="sortField()"',
                        '[virtualScroll]="virtualScroll()"', '(onLazyLoad)="onLazyLoad.emit($event)"', '(selectionChange)="selection.set($event)"'):
            self.assertIn(binding, source)
        for name in ('caption', 'header', 'body', 'emptymessage', 'footer', 'summary', 'rowexpansion'):
            self.assertIn(f"readonly {name}Template = contentChild<TemplateRef<any>>('{name}');", source)
        # without a template of its own, the default: the columns, the row buttons, the empty text
        self.assertIn('@for (col of columns; track col.field) { <td>{{ row[col.field] }}</td> }', source)
        self.assertIn("readonly emptyMessage = input<string>('Nincs megjeleníthető adat.');", source)
        self.assertIn('readonly paginator = input<boolean>(true);', source)
        self.assertIn("readonly selectionMode = input<'single' | 'multiple' | null | undefined>('single');", source)

    def test_every_successful_request_is_logged_first_with_the_module_and_method_name(self):
        for out in (self.out, self.listas):
            source = component(out)
            methods = re.findall(r"\n  (\w+)\([^)]*\) \{\n    return this\.http\.(?:get|post|put|delete)<any>\(this\.url\('[^']*'\)", source)
            logged = re.findall(r"tap\(res => WFF\.debug\(this\.modName \+ '\.(\w+)', res\)\),", source)
            self.assertTrue(methods)
            self.assertEqual(methods, logged)  # the name logged is the method's own
            self.assertEqual(source.count('this.http.'), len(methods))
            self.assertEqual(source.count("WFF.err('Hiba', error);"), len(methods))
            self.assertNotIn('inject(Router)', source)  # the router comes from ServiceBase
            self.assertNotIn('get modName', source)

    def test_the_constructor_is_always_between_the_fields_and_the_methods(self):
        for out in self.outputs:
            screen = component(out)
            constructor = screen.index('  constructor() {\n    super();\n')
            fields = [m.start() for m in re.finditer(r'\n  (?:protected|private) (?:readonly )?\w+(?:: [^=\n]+)? = ', screen)]
            methods = [m.start() for m in re.finditer(r'\n  (?:private |protected )?\w+\([^)]*\)(?:: \w+)? \{', screen) if m.start() != constructor - 1]
            self.assertLess(max(fields), constructor, out.name)
            self.assertTrue(all(constructor < m for m in methods), out.name)
        self.assertIn('  constructor() {\n    super();\n  }\n', component(self.simple))  # nothing to start: still there

    def test_only_the_frame_no_forms_emulation(self):
        for out in self.outputs:
            screen = component(out)
            # 4.35: a backend action's answer is applied by one plain method (applyActionResult); nothing else.
            if 'protected applyActionResult(' in screen:
                start = screen.index('  // A backend akció válasza')
                handler = screen[start:screen.index('\n  }\n', start) + 4]
                self.assertLess(len(handler.splitlines()), 60, out.name)
                screen = screen.replace(handler, '').replace('this.applyActionResult(', 'this.handler(')
            # A query that left rows out (PageResult.hasMore) says so instead of a silent cut: one plain method.
            if 'protected migrationMoreRows(' in screen:
                start = screen.index('  // A lekérdezés legfeljebb annyi rekordot kér')
                helper = screen[start:screen.index('\n  }\n', start) + 4]
                self.assertLess(len(helper.splitlines()), 9, out.name)
                screen = screen.replace(helper, '')
            # The save measures a block's record against the queried one: one plain method, no record buffer.
            if 'protected migrationChanges(' in screen:
                start = screen.index('  // Mentés: egy blokk rekordja a képernyőn')
                helper = screen[start:screen.index('\n  }\n', start) + 4]
                self.assertLess(len(helper.splitlines()), 22, out.name)
                screen = screen.replace(helper, '')
            for absent in ('private text(', 'private data<', 'private blocks(', 'private parameters(', 'showResult', 'showBlocks',
                           'setItemState', 'stateValue', 'private value(', 'private readonly items', 'originals', 'newRecord',
                           'deleteRecord', 'interface Page', 'ActionResult', 'this.toast', 'DestroyRef', 'takeUntilDestroyed', 'suggest(',
                           'completeMethod', 'Validators'):
                self.assertFalse(absent in screen, out.name + ': ' + absent)
        simple = component(self.simple)
        self.assertNotIn('HttpClient', simple)  # nothing to call
        self.assertIn('protected readonly toast = inject(ToastService);', simple)  # the company toast: in every component
        self.assertNotIn('AKTIV', simple.split('constructor()', 1)[1])  # the item state trigger: not translated
        listas = component(self.listas)
        self.assertRegex(listas, r'\n  partnerList\(offset = 0, limit = 200\) \{')  # no button queries it: the endpoint is there to call
        self.assertNotIn('save()', listas)  # no form block to save
        replica = component(self.out)
        self.assertIn('<button pButton type="button" (click)="save()">Mentés</button>', replica)
        # 4.35: + applyActionResult/migrationQuery (about 45 lines); the save's change lists: + migrationChanges (about 20)
        self.assertLess(len(replica.splitlines()), 500)

    def test_a_button_sends_its_request_with_the_screen_values(self):
        replica = component(self.out)
        method = screen_method(self.out, 'onPbUjraszamolClick')
        self.assertIn('this.actionOnctrlpbujraszamol({', method)
        self.assertIn("this.migrationInputText(this.forms['rendeles']?.getRawValue()?.['id'] ?? null)", method)
        self.assertIn("this.migrationInputText(this.forms['rendeles']?.getRawValue()?.['osszeg'] ?? null)", method)
        from frm_forms.input_bindings import sheet
        rows = sheet(self.out)['rows']
        global_row = next(r for r in rows if r['source'] == 'GLOBAL.EV')
        self.assertEqual(global_row['status'], 'missing')
        init_row = next(r for r in rows if r['source'] == 'GLOBAL.CG$APP')
        self.assertEqual(init_row['status'], 'lifecycle')
        self.assertNotIn('FRM:', replica)
        save = screen_method(self.out, 'save')
        # The record of the form against the queried one; the saved record (its generated key) back on the screen.
        self.assertIn("changesRendeles: this.migrationChanges('RENDELES', rendeles, this.forms['rendeles']?.dirty),", save)
        self.assertIn("const row = res.rowsRendeles[0];\n        this.migrationLoaded['RENDELES'] = row;", save)
        self.assertIn('this.applyActionResult(res);', save)  # the answer's messages and commands
        self.assertIn("blocks: { RENDELES: { ID: /* frm-input:", save)  # the master key of a new TETEL record
        self.assertNotIn('TODO', save)

    def test_the_button_requests_run_in_node(self):
        if not shutil.which('node'):
            self.skipTest('Node with TypeScript stripping required')
        members = [screen_method(self.out, name) for name in ('onPbUjraszamolClick', 'onPbAlapClick', 'save', 'migrationInputText',
                                                               'migrationChanges')] + [screen_field(self.out, 'migrationLoaded')]
        self.assertNotIn(None, members)
        script = self.root / 'button-flow.ts'
        script.write_text('''import assert from 'node:assert/strict';
class Group {  // the FormGroup as the code uses it: reset() sets every control (null when missing) and clears dirty
  values: Record<string, unknown> = {};
  dirty = false;
  getRawValue() { return {...this.values}; }
  reset(value: Record<string, unknown>) {
    this.values = Object.fromEntries(Object.keys(this.values).map(key => [key, value?.[key] ?? null]));
    this.dirty = false;
  }
}
const sent: [string, any][] = [];
let answer: unknown = null;  // the backend's answer to the next save
const call = (name: string) => (body: unknown) => {
  sent.push([name, structuredClone(body)]);
  return {subscribe(next?: (res: unknown) => void) { if (name === 'commit' && next) { next(answer); } }};
};
class Screen {
  forms: Record<string, Group> = {rendeles: new Group(), ctrl: new Group()};
  actionOnctrlpbujraszamol = call('ujraszamol');
  actionOnctrlpbalap = call('alap');
  commitForm = call('commit');
  applied: unknown[] = [];
  applyActionResult(res: unknown) { this.applied.push(res); }
__MEMBERS__
}
const screen = new Screen();
screen.forms.rendeles.values = {id: 7, osszeg: 1200, vevo: 'V1'};
screen.onPbUjraszamolClick();
assert.deepEqual(sent[0], ['ujraszamol', {blocks: {RENDELES: {ID: '7', OSSZEG: '1200'}}, parameters: {}}]);
delete screen.forms.ctrl;  // a region not yet generated: its values are missing, the request still goes
screen.onPbAlapClick();
assert.deepEqual(sent[1], ['alap', {blocks: {CTRL: {EV: null}}, parameters: {'GLOBAL.EV': null}}]);
// Nothing queried, nothing entered (the start values do not count): no record to save.
screen.forms.rendeles.values = {id: null, vevo: null, vevoNev: null, datum: null, statusz: 'N', osszeg: null};
answer = {rowsRendeles: [], messages: []};
screen.save();
assert.deepEqual(sent[2], ['commit', {blocks: {RENDELES: {ID: null}}, changesRendeles: {inserted: [], updated: [], deleted: []}}]);
// The user enters a record: a new record (empty text: null, the date in local time); the saved one comes back.
screen.forms.rendeles.values = {id: null, vevo: 'V1', vevoNev: '', datum: new Date(2024, 0, 31, 10, 30), statusz: 'N', osszeg: 1200};
screen.forms.rendeles.dirty = true;
const saved = {id: 7, vevo: 'V1', vevoNev: 'Vevő 1', datum: '2024-01-31T10:30:00', statusz: 'N', osszeg: 1200, rowid: 'AAAx'};
answer = {rowsRendeles: [saved], messages: ['Mentve']};
screen.save();
assert.deepEqual(sent[3][1].changesRendeles, {inserted: [{id: null, vevo: 'V1', vevoNev: null, datum: '2024-01-31T10:30:00',
  statusz: 'N', osszeg: 1200}], updated: [], deleted: []});
assert.equal(screen.migrationLoaded['RENDELES'], saved);
assert.equal((screen.forms.rendeles.values.datum as Date).getTime(), new Date(2024, 0, 31, 10, 30).getTime());
assert.equal(screen.forms.rendeles.values.id, 7);
assert.deepEqual(screen.applied.at(-1), answer);
// Saved again unchanged: nothing to send (the Date on the screen is the saved text).
screen.save();
assert.deepEqual(sent[4], ['commit', {blocks: {RENDELES: {ID: '7'}}, changesRendeles: {inserted: [], updated: [], deleted: []}}]);
// A change: the queried record as original (its ROWID too), the screen's values over it.
screen.forms.rendeles.values = {...screen.forms.rendeles.values, osszeg: 1500};
screen.save();
assert.deepEqual(sent[5][1].changesRendeles, {inserted: [], updated: [{original: saved, value: {...saved, osszeg: 1500}}], deleted: []});
console.log('typescript buttons OK');
'''.replace('__MEMBERS__', '\n'.join(members)))
        run = subprocess.run(['node', '--experimental-strip-types', '--no-warnings', str(script)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('typescript buttons OK', run.stdout)

    def test_a_saved_record_round_trips_between_the_screen_and_the_record(self):
        if not shutil.which('node'):
            self.skipTest('Node with TypeScript stripping required')
        members = [screen_method(self.torzs, name) for name in ('onPbKeresClick', 'save', 'migrationChanges', 'migrationMoreRows')]
        members.append(screen_field(self.torzs, 'migrationLoaded'))
        self.assertNotIn(None, members)
        self.assertIn('this.tList(0, 1).subscribe(res => {', members[0])  # the form shows one record: the query asks one
        script = self.root / 'record-flow.ts'
        script.write_text('''import assert from 'node:assert/strict';
class Group {
  values: Record<string, unknown> = {id: null, aktiv: true, ervenyes: null};  // a start value: checked
  dirty = false;
  getRawValue() { return {...this.values}; }
  reset(value: Record<string, unknown>) {
    this.values = Object.fromEntries(Object.keys(this.values).map(key => [key, value?.[key] ?? null]));
    this.dirty = false;
  }
}
const sent: any[] = [];
const warnings: string[] = [];
let answer: any = null;
const call = (body?: unknown) => { sent.push(structuredClone(body)); return {subscribe(next: (res: unknown) => void) { next(answer); }}; };
class Screen {
  forms: Record<string, Group> = {t: new Group()};
  toast = {warning: (title: string, text: string) => warnings.push(text)};
  toastLife = {warning: 1};
  tList = (offset: number, limit: number) => call({offset, limit});
  commitForm = call;
  applyActionResult(res: unknown) { void res; }
__MEMBERS__
}
const screen = new Screen();
// The query: the record's I/N and ISO date as the controls hold them; more matched than the form shows: a warning.
answer = {rows: [{id: 5, aktiv: 'N', ervenyes: '2024-03-01T00:00:00'}], hasMore: true};
screen.onPbKeresClick();
assert.deepEqual(sent[0], {offset: 0, limit: 1});
assert.equal(screen.forms.t.values.aktiv, false);
assert.equal((screen.forms.t.values.ervenyes as Date).getTime(), new Date(2024, 2, 1).getTime());
assert.deepEqual(warnings, ['T: több rekord van, mint amennyi megjelent; szűkítsd a feltételt.']);
// Ticked: the record gets I; the unchanged date is not a change.
screen.forms.t.values.aktiv = true;
answer = {rowsT: []};
screen.save();
assert.deepEqual(sent[1].changesT, {inserted: [], updated: [{original: {id: 5, aktiv: 'N', ervenyes: '2024-03-01T00:00:00'},
  value: {id: 5, aktiv: 'I', ervenyes: '2024-03-01T00:00:00'}}], deleted: []});
// A date with milliseconds (the calendar's today): to the second, as the DTO reads it.
screen.forms.t.values.ervenyes = new Date(2024, 2, 2, 8, 15, 30, 250);
screen.save();
assert.equal(sent[2].changesT.updated[0].value.ervenyes, '2024-03-02T08:15:30');
// A query that finds nothing: an empty form, nothing to save.
answer = {rows: [], hasMore: false};
screen.onPbKeresClick();
assert.deepEqual(screen.forms.t.values, {id: null, aktiv: null, ervenyes: null});
answer = {rowsT: []};
screen.save();
assert.deepEqual(sent[4].changesT, {inserted: [], updated: [], deleted: []});
console.log('record round trip OK');
'''.replace('__MEMBERS__', '\n'.join(members)))
        run = subprocess.run(['node', '--experimental-strip-types', '--no-warnings', str(script)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('record round trip OK', run.stdout)

    def test_no_wrapper_around_the_form_blocks(self):
        for out in self.outputs:
            template = component(out).split('template: `', 1)[1].split('`,', 1)[0]
            self.assertNotRegex(template, r'<(?:div|section) class="flex flex-col gap-\d">', out.name)
        dialogs = component(self.dialogs)
        self.assertIn('<p-fieldset [legend]="labels.text1">\n    <ank-form-block', dialogs)
        self.assertIn("<p-tabpanel [value]=\"'P1'\">\n    <ank-form-block", dialogs)

    def test_windows_canvases_and_their_steps_are_plain_fields(self):
        dialogs = component(self.dialogs)
        self.assertIn("<p-dialog [header]=\"labels.text4\" [(visible)]=\"windowVisible['EDIT']\"", dialogs)
        self.assertIn("@if (canvasVisible['EXTRA']) {", dialogs)
        self.assertIn('  protected readonly windowVisible: Record<string, boolean> = {', dialogs)
        self.assertIn("  protected onPbNyitClick(): void {\n    this.windowVisible['EDIT'] = true;\n    this.canvasVisible['EXTRA'] = true;", dialogs)
        self.assertIn("  protected onPbBezarClick(): void {\n    this.windowVisible['EDIT'] = false;\n  }", dialogs)
        self.assertNotIn('signal', dialogs)

    def test_typescript_strict(self):
        tsc = os.environ.get('FRM_TSC') or shutil.which('tsc')
        if not tsc:
            self.skipTest('tsc required (FRM_TSC=<path to tsc>)')
        for out in self.outputs:
            with self.subTest(out=out.name):
                work = self.root / ('ts-' + out.name)
                shutil.copytree(out / 'frontend', work / 'frontend')
                shutil.copy(ROOT / 'tests/ts_stubs/company.ts', work / 'company.ts')
                files = with_company_imports(work / 'frontend')
                (work / 'tsconfig.json').write_text(json.dumps({'compilerOptions': {
                    **ANGULAR_CLI_OPTIONS, 'noUnusedLocals': True, 'useDefineForClassFields': True, 'noEmit': True},
                    'files': [str(ROOT / 'tests/ts_stubs/host.d.ts')] + files}))
                run = subprocess.run([tsc, '-p', str(work / 'tsconfig.json')], capture_output=True, text=True, timeout=180)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_angular_compiler_strict_templates(self):
        ngc = os.environ.get('FRM_NGC')
        if not ngc:
            self.skipTest('Angular compiler required (FRM_NGC=<path to node_modules/.bin/ngc> of an install with '
                          '@angular/compiler-cli, core, common, forms, router and rxjs)')
        own = self.root / 'own-table'
        shutil.copytree(self.out / 'frontend', own / 'frontend')
        (own / 'frontend/sajat').mkdir()
        (own / 'frontend/sajat/sajat.component.ts').write_text(OWN_TABLE, encoding='utf-8')
        for out in (*self.outputs, own):
            with self.subTest(out=out.name):
                code, output = ngc_check(out, ngc)
                self.assertEqual(code, 0, output)


if __name__ == '__main__':
    unittest.main()

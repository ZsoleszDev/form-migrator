"""Compile screens against real Optimus UI and exercise their FormGroup/LOV state.

The private FormBlock is a declared contract stub, not a claimed implementation.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from check_generated_angular import ROOT, HOST
from frm_forms.cli import main as migrate


def main():
    web = ROOT / 'web-ui'; compiler = web / 'node_modules/@angular/compiler-cli/bundles/src/bin/ngc.js'
    if not compiler.is_file(): raise SystemExit('Előbb: cd web-ui && npm ci')
    with tempfile.TemporaryDirectory(prefix='frm-screen-check-', dir=web / 'node_modules') as temp:
        stage = Path(temp); (stage / 'host.ts').write_text(HOST)
        profile = {'emit_imports': True, 'optimus_import_path': '@test/host', 'optimus_form_block_symbol': 'HostFormBlock',
                   'form_block_type_import_path': '@test/host'}
        fixtures = [('fadlek', ROOT / 'review-output/fadlek/analysis/source.xml', 'tabs'),
                    ('features', ROOT / 'examples/ui-features_fmb.xml', 'tabs'),
                    ('accordion', ROOT / 'examples/ui-features_fmb.xml', 'accordion'),
                    ('customer', ROOT / 'examples/customer_fmb.xml', 'tabs'),
                    ('nested', ROOT / 'examples/screen-layout_fmb.xml', 'tabs'),
                    ('reviewed', ROOT / 'examples/screen-layout_fmb.xml', 'tabs'),
                    ('dialogs', ROOT / 'examples/screen-dialogs_fmb.xml', 'tabs'),
                    ('dialogsAccordion', ROOT / 'examples/screen-dialogs_fmb.xml', 'accordion'),
                    ('pages', ROOT / 'examples/screen-pages_fmb.xml', 'tabs')]
        extra = '''<FormModule Name="EXTRA" Title="Hostile ` ${label} &amp; safe" CoordinateSystem="Real" RealUnit="Pixel">
          <Window Name="W" Title="Details" Modal="true"/><Canvas Name="C" WindowName="W"><TabPage Name="T" Label="Tab &quot; one"/>
          <Graphics Name="F" GraphicsType="Frame" FrameTitle="Fields" XPosition="0" YPosition="0" Width="400" Height="500"/></Canvas>
          <Block Name="A"><Item Name="CODE" ItemType="Text Item" CanvasName="C" TabPageName="T" LOVName="L"/></Block>
          <Block Name="B"><Item Name="NAME" ItemType="Display Item" CanvasName="C" TabPageName="T"/></Block>
          <Block Name="ROWS" RecordsDisplayCount="3"><Item Name="TEXT" ItemType="Text Item" CanvasName="C"/>
          <Item Name="ACTION" ItemType="Push Button" Label="Open" CanvasName="C"/></Block>
          <Block Name="CHECKS">
          <Item Name="CODE" ItemType="Text Item" CanvasName="C" Required="true" MaximumLength="4" FixedLength="true" CaseRestriction="Uppercase" InitialValue="ABCD"/>
          <Item Name="COUNT" ItemType="Text Item" CanvasName="C" DataType="Integer" Required="true" Precision="5" Scale="0" LowestAllowedValue="1.5" HighestAllowedValue="9.5" InitialValue="2"/>
          <Item Name="AMOUNT" ItemType="Text Item" CanvasName="C" DataType="Number" Precision="30" Scale="2" LowestAllowedValue="9007199254740993.10" HighestAllowedValue="9007199254740993.20" InitialValue="9007199254740993.15"/>
          <Item Name="DAY" ItemType="Text Item" CanvasName="C" DataType="Date" FormatMask="YYYY-MM-DD"/>
          </Block>
          <LOV Name="L" RecordGroupName="RG"><LOVColumnMapping ColumnName="NAME" ReturnItem="B.NAME"/></LOV>
          <RecordGroup Name="RG" RecordGroupQuery="SELECT NAME FROM T WHERE ID=:A.CODE"/></FormModule>'''
        (stage / 'extra.xml').write_text(extra)
        fixtures.append(('extra', stage / 'extra.xml', 'tabs'))
        (stage / 'symbols.xml').write_text('''<FormModule Name="SYMBOLS" CoordinateSystem="Real" RealUnit="Pixel">
          <Window Name="__proto__" WindowStyle="Dialog" Modal="false"/><Canvas Name="__proto__" WindowName="__proto__"/>
          <Block Name="ITEMS"><Item Name="VALUE" ItemType="Text Item" CanvasName="__proto__"/></Block></FormModule>''')
        fixtures.append(('symbols', stage / 'symbols.xml', 'tabs'))
        roots = []
        for key, source, mode in fixtures:
            current = {**profile, 'screen_tab_layout': mode}
            if key == 'reviewed':
                rules = json.loads((stage / 'nested/analysis/screen-overrides.template.json').read_text())
                rules['groups']['search'] = {'label': 'Kódkeresés'}
                for owner, col in [('FIELDS.CODE', 3), ('FIELDS.LOOKUP', 1), ('FIELDS.NAME', 7)]:
                    rules['items'][owner].update(reason='Ellenőrzött elrendezés.', set={'group': 'search', 'col': col, 'row': 0})
                rules['items']['FIELDS.NAME']['set']['col_before'] = 1
                rules['items']['FIELDS.ACTIVE'].update(reason='Ellenőrzött checkbox.', set={'widget': 'checkbox'})
                current['screen_overrides'] = rules
                (stage / 'review.json').write_text(json.dumps(rules))
            (stage / 'config.json').write_text(json.dumps(current))
            with contextlib.redirect_stdout(io.StringIO()):
                status = migrate(['migrate', str(source), '--screen', '--frontend-only', '--module', key,
                                  '--out', str(stage / key), '--config', str(stage / 'config.json')])
            assert status == 0, key
            roots.extend((stage / key / 'frontend').rglob('*.ts'))
        config = stage / 'tsconfig.json'
        config.write_text(json.dumps({'extends': str(web / 'tsconfig.json'), 'compilerOptions': {
            'outDir': str(stage / 'compiled'), 'paths': {'@test/host': [str(stage / 'host.ts')]}},
            'files': [str(stage / 'host.ts'), *map(str, roots)], 'include': []}))
        subprocess.run(['node', str(compiler), '-p', str(config)], cwd=web, check=True, timeout=60)
        runner = stage / 'runtime.mjs'
        runner.write_text('''import '@angular/compiler';
import assert from 'node:assert/strict';
import {ChangeDetectorRef, Injector, runInInjectionContext} from '@angular/core';
import {FormGroup, FormControl} from '@angular/forms';
import {FadlekComponent} from './compiled/fadlek/frontend/fadlek/fadlek.component.js';
import {ExtraComponent} from './compiled/extra/frontend/extra/extra.component.js';
import {DialogsComponent} from './compiled/dialogs/frontend/dialogs/dialogs.component.js';
import {PagesComponent} from './compiled/pages/frontend/pages/pages.component.js';
import {SymbolsComponent} from './compiled/symbols/frontend/symbols/symbols.component.js';
const injector = Injector.create({providers:[{provide:ChangeDetectorRef,useValue:{markForCheck(){}}}]});
const instance = runInInjectionContext(injector, () => new FadlekComponent());
const makeGroup = fields => new FormGroup(Object.fromEntries(fields.filter(f=>f.formControlName).map(f=>[f.formControlName,new FormControl(f.startValue ?? null)])));
const group = makeGroup(instance.vElekAdlapStructure);
instance.onFormGroupGenerated('V_ELEK_ADLAP','vElekAdlapRegion1',group);
assert.equal(group.valid,true);
for(const field of ['ubiE04','ubiE07','ubiP01']) {
  for(const missing of [null,'']) { group.get(field).setValue(missing); assert.ok(group.get(field).hasError('required')); }
  group.get(field).setValue(true); assert.ok(group.get(field).valid);
  group.get(field).setValue(false); assert.equal(group.valid,true);
}
let action; instance.actionRequested.subscribe(e=>action=e);
instance.aitSelection={aitKulcs:'ROW1'};
instance.onAction('CGNV$W01_1.PB_RESZLETEK');
assert.equal(action.values.V_ELEK_ADLAP.ubiE04,'0'); assert.equal(action.selectedRows.ait.aitKulcs,'ROW1');
let request; instance.lovRequested.subscribe(e=>request=e);
const owner='V_ELEK_ADLAP.UBI_INPTIP_KOD';
instance.onLovSearch(owner,'INPTIP',{query:'old'}); const old=request.requestId;
instance.onLovSearch(owner,'INPTIP',{query:'new'});
const choices=[{label:'Adatlap',value:'A',returnValues:{'V_ELEK_ADLAP.UBI_INPTIP_KOD':'A','V_ELEK_ADLAP.UBI_INPTIP_KOD_NEV':'Név'}}];
const oldStructure=instance.vElekAdlapStructure;
instance.setLovSuggestions(owner,choices,old); assert.equal(instance.vElekAdlapStructure,oldStructure);
instance.setLovSuggestions(owner,choices,request.requestId); assert.notEqual(instance.vElekAdlapStructure,oldStructure);
assert.deepEqual(instance.vElekAdlapStructure.find(i=>i.ownId===owner).suggestions,choices);
group.get('ubiInptipKod').setValue('A'); assert.equal(group.get('ubiInptipKodNev').value,'Név');
group.markAsDirty(); group.markAsTouched();
const replacement=makeGroup(instance.vElekAdlapStructure);
instance.onFormGroupGenerated('V_ELEK_ADLAP','vElekAdlapRegion1',replacement);
assert.equal(replacement.get('ubiInptipKod').value,'A');assert.ok(replacement.dirty);assert.ok(replacement.touched);
group.get('ubiInptipKod').setValue('STALE');instance.onAction('CHECK');assert.equal(action.values.V_ELEK_ADLAP.ubiInptipKod,'A');
instance.ngOnDestroy(); replacement.get('ubiInptipKod').setValue('AFTER_DESTROY');instance.onAction('CHECK');assert.equal(action.values.V_ELEK_ADLAP.ubiInptipKod,'A');
const other=runInInjectionContext(injector,()=>new ExtraComponent());
const source=new FormGroup({code:new FormControl(null)});const target=new FormGroup({name:new FormControl(null)});
other.onFormGroupGenerated('A','aRegion1',source);other.onFormGroupGenerated('B','bRegion2',target);
other.onLovSearch('A.CODE','L',{query:'x'});other.setLovSuggestions('A.CODE',[{label:'Name',value:'X',returnValues:{'B.NAME':'Cross block'}}],1);
source.get('code').setValue('X');assert.equal(target.get('name').value,'Cross block');
const checks=makeGroup(other.checksStructure);
checks.get('code').addValidators(c=>c.value==='ZZZZ'?{host:true}:null);
other.onFormGroupGenerated('CHECKS','checksRegion4',checks); assert.ok(checks.valid);
const code=checks.get('code'), count=checks.get('count'), amount=checks.get('amount'), day=checks.get('day');
code.setValue('');assert.ok(code.hasError('required'));
code.setValue('ABC');assert.ok(code.hasError('minlength'));
code.setValue('ABCDE');assert.ok(code.hasError('maxlength'));
code.setValue('ÁbCD');assert.ok(code.hasError('caseRestriction'));
code.setValue('ÁBCD');assert.ok(code.valid);
code.setValue('ZZZZ');assert.ok(code.hasError('host'));code.setValue('ABCD');
count.setValue(null);assert.ok(count.hasError('required'));
count.setValue(1);assert.ok(count.hasError('min'));count.setValue(10);assert.ok(count.hasError('max'));
count.setValue(2.5);assert.ok(count.hasError('number'));count.setValue(9);assert.ok(count.valid);
amount.setValue('9007199254740993.09');assert.ok(amount.hasError('min'));
amount.setValue('9007199254740993.21');assert.ok(amount.hasError('max'));
for(const value of ['9007199254740993.10','9007199254740993.20']) { amount.setValue(value);assert.ok(amount.valid); }
amount.setValue('9007199254740993.151');assert.ok(amount.hasError('scale'));
amount.setValue('10000000000000000000000000000.00');assert.ok(amount.hasError('precision'));
amount.setValue(9007199254740993.15);assert.ok(amount.hasError('number'));
amount.setValue('9007199254740993.15');assert.ok(amount.valid);
day.setValue('2026-01-01');assert.ok(day.hasError('date'));day.setValue(new Date('invalid'));assert.ok(day.hasError('date'));
day.setValue(new Date(2026,0,1));assert.ok(checks.valid);other.ngOnDestroy();
const dialogs=new DialogsComponent();const changes=[];dialogs.windowVisibilityChanged.subscribe(event=>changes.push(event));
assert.equal(dialogs.windowVisible().WORK_AREA,true);assert.equal(dialogs.windowVisible().EDIT_WINDOW,false);
dialogs.setWindowVisible('EDIT_WINDOW',true);dialogs.setWindowVisible('CONFIRM_WINDOW',true);
assert.equal(dialogs.windowVisible().EDIT_WINDOW,true);assert.equal(dialogs.windowVisible().CONFIRM_WINDOW,true);
dialogs.setWindowVisible('CONFIRM_WINDOW',false);assert.equal(dialogs.windowVisible().EDIT_WINDOW,true);
const events=changes.length;dialogs.setWindowVisible('CONFIRM_WINDOW',false);assert.equal(changes.length,events);
dialogs.setWindowVisible('EDIT_WINDOW',false);dialogs.showCanvas('DETAIL_EXTRA');
assert.equal(dialogs.windowVisible().EDIT_WINDOW,true);assert.equal(dialogs.canvasVisible().DETAIL_EXTRA,true);
dialogs.hideCanvas('DETAIL_EXTRA');assert.equal(dialogs.canvasVisible().DETAIL_EXTRA,false);assert.equal(dialogs.windowVisible().EDIT_WINDOW,true);
assert.throws(()=>dialogs.setWindowVisible('MISSING',true));assert.throws(()=>dialogs.showCanvas('MISSING'));
const detailKey=Object.keys(dialogs).find(k=>k.endsWith('Structure')&&dialogs[k].some(f=>f.ownId==='DETAILS.CODE'));
const detailRegion=detailKey.replace(/Structure$/,'');const detailGroup=makeGroup(dialogs[detailKey]);
dialogs.onFormGroupGenerated('DETAILS',detailRegion,detailGroup);detailGroup.get('code').setValue('KEEP');
detailGroup.markAsDirty();dialogs.setWindowVisible('EDIT_WINDOW',false);dialogs.setWindowVisible('EDIT_WINDOW',true);
const rebound=makeGroup(dialogs[detailKey]);dialogs.onFormGroupGenerated('DETAILS',detailRegion,rebound);
assert.equal(rebound.get('code').value,'KEEP');assert.ok(rebound.dirty);dialogs.ngOnDestroy();
const pages=new PagesComponent();assert.equal(pages.activeContentCanvas().WORKSPACE,'STEP_A');
pages.showCanvas('STEP_B');assert.equal(pages.activeContentCanvas().WORKSPACE,'STEP_B');assert.equal(pages.canvasVisible().STEP_B,true);
pages.showCanvas('STEP_A');assert.equal(pages.activeContentCanvas().WORKSPACE,'STEP_A');pages.ngOnDestroy();
const symbols=new SymbolsComponent();assert.equal(Object.hasOwn(symbols.windowVisible(),'__proto__'),true);
symbols.showCanvas('__proto__');assert.equal(symbols.windowVisible()['__proto__'],true);symbols.ngOnDestroy();
console.log('OK: separate dialogs, nested opening/closing, canvas activation, content-page switching, retained form values and arbitrary names');
console.log('OK: required/false, fixed/max length, Unicode case, exact min/max/precision/scale, date and host validators');
console.log('OK: checkbox 1/0, selection, LOV response ordering, immutable suggestions, cross-block return, rebind state and cleanup');
''')
        bundle = stage / 'runtime-bundle.mjs'
        script = "import {build} from 'esbuild'; await build({entryPoints:[process.argv[1]],outfile:process.argv[2],bundle:true,platform:'node',format:'esm',packages:'external',tsconfig:process.argv[3],alias:{'@test/host':process.argv[4]}});"
        subprocess.run(['node', '--input-type=module', '-e', script, str(runner), str(bundle), str(config), str(stage / 'compiled/host.js')], cwd=web, check=True)
        subprocess.run(['node', str(bundle)], cwd=web, env={**os.environ, 'TZ': 'Europe/Budapest'}, check=True, timeout=30)
        # Exercise the host-project entry point with the declared fixture contract.
        # A deliberately removed property must make this same check fail.
        (stage / 'profile.json').write_text(json.dumps(profile))
        command = [sys.executable, str(ROOT / 'scripts/check_screen_host.py'), '--host-project', str(web),
                   '--tsconfig', str(config), '--component-dir', str(stage), '--profile', str(stage / 'profile.json'),
                   '--source', str(ROOT / 'examples/screen-layout_fmb.xml'), '--screen-overrides', str(stage / 'review.json')]
        subprocess.run(command, cwd=ROOT, check=True, timeout=120)
        (stage / 'host.ts').write_text(HOST.replace('colBefore?: string;', ''))
        broken = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
        assert broken.returncode != 0 and 'colBefore' in broken.stderr, broken.stdout + broken.stderr
        assert not list(stage.glob('.frm-screen-check-*'))
        print('OK: host-project check compiles the fixture and rejects an incompatible FormBlock contract; temporary files cleaned.')
    print('OK: ' + str(len(fixtures)) + ' screen variants compile with actual Optimus UI 2.0.2; FormBlock remains a contract stub.')


if __name__ == '__main__': main()

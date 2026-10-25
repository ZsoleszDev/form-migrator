"""Angular 22 strict compile with real Optimus UI p-table and the supplied FormBlock API stub.
The private implementation itself is not bundled or claimed to have been tested.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from frm_forms.cli import main as migrate

HOST='''import { Component, Input, Output, EventEmitter, TemplateRef } from '@angular/core';
import { FormGroup } from '@angular/forms';
export namespace FormBlock {
 export interface Structure {
  type: 'text' | 'calendar' | 'button' | 'divider' | 'label' | 'checkBox' | 'dropdown' | 'radioButton' | 'password'
      | 'inputTextarea' | 'inputNumber' | 'multiSelect' | 'autocomplete' | 'templateRef' | 'treeSelect';
  templateRef?: TemplateRef<any>; formControlName?: string; ownId?: string; labelText?: string; invisible?: boolean; disabled?: boolean;
  readonly?: boolean; validator?: boolean; startValue?: unknown; col?: string; colBefore?: string; colAfter?: string;
  minLenght?: number; maxLenght?: number; dateFormat?: string; showTime?: boolean; showIcon?: boolean;
  options?: any[]; optionLabel?: string; optionValue?: string; suggestions?: any[]; completeMethod?: any;
  inputInfo?: string; tooltipData?: string; onClick?: () => void; showClear?: boolean; useGrouping?: boolean;
  btnLabel?: string; btnSeverity?: 'secondary';
  min?: number; max?: number; minFractionDigits?: number; maxFractionDigits?: number;
  regexRule?: { regex: RegExp; example: string }; binary?: boolean; filter?: boolean; dropdown?: boolean;
 }
}
@Component({selector:'ank-form-block',standalone:true,template:''})
export class HostFormBlock {
 @Input() formStructure: FormBlock.Structure[]=[];
 @Output() formGroupGenerated=new EventEmitter<FormGroup>();
}
export const environment={baseUrl:'http://localhost:9555/gateway/'};
'''

def main():
    web=ROOT/'web-ui'; compiler=web/'node_modules/@angular/compiler-cli/bundles/src/bin/ngc.js'
    if not compiler.is_file():raise SystemExit('Előbb: cd web-ui && npm ci')
    with tempfile.TemporaryDirectory(prefix='frm-ui-check-',dir=web/'node_modules') as tmp:
        stage=Path(tmp);(stage/'host.ts').write_text(HOST)
        profile={'emit_imports':True,'optimus_import_path':'@test/host','optimus_form_block_symbol':'HostFormBlock',
                 'form_block_type_import_path':'@test/host','environment_import_path':'@test/host','table_import_path':'@openng/optimus-ui/table','table_symbol':'TableModule'}
        (stage/'config.json').write_text(json.dumps(profile))
        roots=[]
        for sample in ['customer','review','vasarlo-lekerdezo','ui-features']:
            args=['migrate',str(ROOT/f'examples/{sample}_fmb.xml'),'--out',str(stage/sample),'--config',str(stage/'config.json')]
            if sample=='customer':args+=['--schema',str(ROOT/'examples/schema.json')]
            with contextlib.redirect_stdout(io.StringIO()):assert migrate(args)==0
            roots.extend((stage/sample/'frontend').rglob('*.ts'))
        (stage/'tsconfig.json').write_text(json.dumps({'extends':str(web/'tsconfig.json'),'compilerOptions':{
            'outDir':str(stage/'compiled'),'paths':{'@test/host':[str(stage/'host.ts')]}},
                                                       'files':[str(stage/'host.ts'),*map(str,roots)],'include':[]}))
        subprocess.run(['node',str(compiler),'-p',str(stage/'tsconfig.json')],cwd=web,check=True)
        runner=stage/'runtime.mjs'
        runner.write_text('''import '@angular/compiler';
import assert from 'node:assert/strict';
import {FormGroup, FormControl} from '@angular/forms';
import {spec as customer} from './customer/frontend/ugyfelek/blocks/customer/form-structure';
import {makeStructure, bindGroup} from './customer/frontend/ugyfelek/form-structure';
import {initialValue, checkboxValue, validateValue, createBlockState, toOracleRow, loadOracleRow, localDate} from './customer/frontend/ugyfelek/model';
const active=structuredClone(customer.items.find(i=>i.key==='active'));
active.validation.required=true; active.checkbox={checked:'1',unchecked:'0',other_values:'reject'}; active.initial_value='0';
assert.equal(initialValue(active),false); assert.equal(validateValue(active,false),null); assert.equal(validateValue(active,null),'required');
assert.throws(()=>checkboxValue(active,'OTHER'));
active.checkbox.other_values='unchecked'; assert.equal(checkboxValue(active,'OTHER'),false);
const spec={...customer,items:[active]}, state=createBlockState(spec), t=k=>k;
const structure=makeStructure(spec,spec.items,state.values,t,()=>{},async()=>[],()=>{});
assert.equal(structure[0].startValue,false); assert.equal(structure[0].validator,true);
const group=new FormGroup({active:new FormControl(false)}); const sub=bindGroup(group,[active],state.values,spec);
assert.equal(group.valid,true);group.get('active').setValue(null);assert.equal(group.valid,false);
group.get('active').setValue(true);assert.equal(toOracleRow(spec,state.values).active,'1');sub.unsubscribe();
const amount=structuredClone(customer.items.find(i=>i.key==='amount'));amount.validation.maximum='99999999999999999999.99';amount.validation.minimum='0';
assert.equal(validateValue(amount,'99999999999999999999.98'),null);assert.equal(validateValue(amount,'100000000000000000000'),'max');
assert.equal(validateValue(amount,'-0.01'),'min');assert.equal(validateValue(amount,'abc'),'number');
const date=localDate('2026-02-03T14:25:09');assert.equal(date.getHours(),14);assert.throws(()=>localDate('2026-02-30'));
const dateItem=customer.items.find(i=>i.key==='createdAt');const roundtrip=toOracleRow({...customer,items:[dateItem]},{createdAt:date});
assert.equal(roundtrip.createdAt,'2026-02-03T14:25:09');assert.equal(loadOracleRow({...customer,items:[dateItem]},roundtrip).createdAt.getHours(),14);
const {spec: advanced} = await import('./ui-features/frontend/feluletiElemek/blocks/filter/form-structure');
const {makeStructure: makeLookup, bindGroup: bindLookup} = await import('./ui-features/frontend/feluletiElemek/form-structure');
const {createBlockState: createLookupState} = await import('./ui-features/frontend/feluletiElemek/model');
const lookupState = createLookupState(advanced);
const choices=[{label:'Teszt ügyfél',value:'X1',columns:{CODE:'X1',NAME:'Teszt név'}}];
let queryValues;
const lookupStructures=makeLookup(advanced,advanced.items,lookupState.values,t,()=>{},async (_endpoint,_query,values)=>{queryValues=values;return choices;},()=>{});
const lookupControl=lookupStructures.find(s=>s.formControlName==='code');
const lookupGroup=new FormGroup(Object.fromEntries(advanced.items.filter(i=>i.widget!=='button').map(i=>[i.key,new FormControl(lookupState.values[i.key])])));
const lookupSubscription=bindLookup(lookupGroup,advanced.items,lookupState.values,advanced);
await lookupControl.completeMethod({query:'X'});
assert.equal(queryValues.active,'0');assert.equal(lookupControl.optionValue,'value');assert.deepEqual(lookupControl.suggestions,choices);
lookupGroup.get('code').setValue('X1');assert.equal(lookupState.values.description,'Teszt név');assert.equal(lookupGroup.get('description').value,'Teszt név');
lookupSubscription.unsubscribe();
const invalidLookup=makeLookup(advanced,advanced.items,lookupState.values,t,()=>{},async ()=>[{label:'X',value:'same',columns:{}},{label:'Y',value:'same',columns:{}}],()=>{}).find(s=>s.formControlName==='code');
await assert.rejects(()=>invalidLookup.completeMethod({query:'x'}),/Duplicate LOV/);
console.log('Runtime: checkbox required/1:0, exact decimals, FormGroup updates, local dates OK');
''')
        bundle=stage/'runtime-bundle.mjs'
        script="import {build} from 'esbuild'; await build({entryPoints:[process.argv[1]],outfile:process.argv[2],bundle:true,platform:'node',format:'esm',packages:'external',tsconfig:process.argv[3]});"
        subprocess.run(['node','--input-type=module','-e',script,str(runner),str(bundle),str(stage/'tsconfig.json')],cwd=web,check=True)
        subprocess.run(['node',str(bundle)],cwd=web,env={**os.environ,'TZ':'Europe/Budapest'},check=True,timeout=20)
    print('OK: Angular 22 strict compile, real Optimus UI table, test FormBlock contract, runtime checks')
if __name__=='__main__':main()

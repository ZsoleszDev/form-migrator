"""Angular 22 emitter. The validated UI model is its only input."""
from pathlib import Path
import json
from .common import jstr, name
from .ui_schema import validate_ui_model
from .ui_model import require_supported
from .ui_config import DATA

GENERATED='// ALWAYS_REGENERATE: generated from analysis/ui-model.json. Edit XML/config, not this file.\n'
PROTECTED='// CREATE_ONCE: preserved on --regenerate, including developer edits.\n'

def dump(value): return json.dumps(value,ensure_ascii=True,indent=2)

def write(path,text):
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(text,encoding='utf-8')

def imports(r,lines):
    return '\n'.join(lines)+'\n' if r['emit_imports'] else '// TODO: add the imports listed in INTEGRATION.md; emit_imports=false.\n'

def class_id(block): return name(block['key'],'pascal')+'BlockComponent'

def render_spec(block,model):
    result={k:block[k] for k in ['name','key','mode','regions']}
    result['regions']=[{k:r[k] for k in ['id','canvas','tab_page','items']} for r in block['regions']]
    result['items']=[]
    for item in block['items']:
        data={k:item[k] for k in ['name','key','owner','widget','representation','type_source','inference_reason','visible','enabled','readonly','allowed_operations','text_keys','validation','initial_value','checkbox','options','layout','lov']}
        data['layout']={k:data['layout'][k] for k in ['col','col_before','col_after','row','order']}
        if data['lov']:
            data['lov']={k:data['lov'][k] for k in ['name','endpoint_id','return_mappings']}
            data['lov']['return_mappings']=[{k:v[k] for k in ['column','return_item']} for v in data['lov']['return_mappings']]
        entry=model['rendering']['widget_map']['widgets'][item['widget']]
        data['widget_type']=entry['type']; data['widget_props']=entry.get('props',{})
        if 'exact_decimal_type' in entry: data['exact_decimal_type']=entry['exact_decimal_type']
        result['items'].append(data)
    return result


def generate(model,output):
    validate_ui_model(model); require_supported(model)
    module=model['module']; r=model['rendering']; root=output/'frontend'/module['key']
    type_name=r['form_block_structure_type']; fb=r['html_selectors']['form_block']; table=r['html_selectors']['table']
    runtime=(DATA/'frontend-runtime.ts.txt').read_text(encoding='utf-8')
    interfaces=[]
    for block in model['blocks']:
        base=name(module['key'],'pascal')+name(block['key'],'pascal')
        for suffix in ['Value','Wire']:
            fields=[]
            for item in block['items']:
                if item['widget'] in {'button','image'}: continue
                typ={'safe-integer':'number','date':'Date','boolean':'boolean','string-array':'string[]'}.get(item['representation'],'string')
                if suffix=='Wire' and typ in {'number','Date','boolean'}: typ='string'
                fields.append('  '+item['key']+': '+typ+' | null;')
            interfaces.append('export interface '+base+suffix+' {\n'+'\n'.join(fields)+'\n}\n')
    write(root/'model.ts',runtime+'\n'+'\n'.join(interfaces))
    write(root/'i18n'/ (module['key']+'.hu.json'),json.dumps(model['i18n'],ensure_ascii=False,sort_keys=True,indent=2)+'\n')
    endpoints=[{k:e[k] for k in ['id','kind','block','item','method','path','implemented','declaration_only','record_group','query_parameters']} for e in model['endpoints']]
    write(root/'endpoints.ts',GENERATED+imports(r,[f"import {{ environment }} from {jstr(r['environment_import_path'])};"])+
          '// LOV paths remain null until a reviewed backend contract exists.\nexport const endpoints = '+dump(endpoints)+' as const;\n'+
          'export function endpointUrl(path: string): string { return environment.baseUrl.replace(/\\/$/, "") + path; }\n')
    actions=[{k:t[k] for k in ['id','event','target','sha256']} for t in model['triggers']]
    write(root/'actions.ts',GENERATED+'// TODO: implement reviewed host handlers. These declarations do not execute PL/SQL.\nexport const actions = '+dump(actions)+' as const;\n')
    standard=["import { TemplateRef } from '@angular/core';", "import { AbstractControl, FormGroup } from '@angular/forms';", f"import {{ FormBlock }} from {jstr(r['form_block_type_import_path'])};",
              "import { UiItem, UiBlockSpec, UiValue, LookupOption, Translate, ActionHandler, LookupHandler, initialValue, validateValue, toOracleRow } from './model';"]
    helper=GENERATED+imports(r,standard)+r'''
const lovChoices = new WeakMap<Record<string, UiValue>, Map<string, LookupOption[]>>();
const controls = new WeakMap<Record<string, UiValue>, Map<string, Set<AbstractControl>>>();
export function makeStructure(spec: UiBlockSpec, items: UiItem[], values: Record<string, UiValue>, t: Translate,
    action: ActionHandler, lookup: LookupHandler, changed: () => void, imageTemplates: Record<string, TemplateRef<unknown>> = {}): __STRUCT__[] {
  return items.map(item => {
    const v = item.validation;
    if (item.widget === 'image' && !imageTemplates[item.owner]) throw new Error('TODO: supply imageTemplates[' + item.owner + '] using the approved host template.');
    const structure: __STRUCT__ = {
      ...item.widget_props,
      type: (item.representation === 'decimal-string' ? item.exact_decimal_type : item.widget_type) as __STRUCT__['type'],
      ownId: item.owner, labelText: t(item.text_keys.prompt ?? item.text_keys.label!),
      col: String(item.layout.col) as __STRUCT__['col'],
      ...(item.layout.col_before ? { colBefore: String(item.layout.col_before) as __STRUCT__['colBefore'] } : {}),
      ...(item.layout.col_after ? { colAfter: String(item.layout.col_after) as __STRUCT__['colAfter'] } : {}),
      invisible: !item.visible, disabled: !item.enabled, readonly: item.readonly,
      validator: v.required,
      ...(item.text_keys.hint ? { inputInfo: t(item.text_keys.hint) } : {}),
      ...((item.text_keys.tooltip ?? item.text_keys.hint) ? { tooltipData: t((item.text_keys.tooltip ?? item.text_keys.hint)!) } : {}),
      ...(v.maximum_length !== null ? { maxLenght: v.maximum_length } : {}),
      ...(v.fixed_length !== null ? { minLenght: v.fixed_length, maxLenght: v.fixed_length } : {}),
      ...(v.date_format ? { dateFormat: v.date_format } : {}),
      ...(v.case === 'upper' ? { regexRule: { regex: /^[^\p{Ll}]*$/u, example: '' } } : {}),
      ...(v.case === 'lower' ? { regexRule: { regex: /^[^\p{Lu}]*$/u, example: '' } } : {})
    };
    if (item.widget === 'image') {
      structure.templateRef = imageTemplates[item.owner]; return structure;
    }
    if (item.widget === 'button') {
      structure.onClick = () => {
        for (const field of spec.items.filter(i => i.widget !== 'button' && i.widget !== 'image' && i.enabled)) {
          const error = validateValue(field, values[field.key]);
          if (error) throw new Error('Invalid form: ' + field.owner + ': ' + error);
        }
        action({ block: spec.name, item: item.name, values: toOracleRow(spec, values) });
      };
    } else {
      structure.formControlName = item.key;
      structure.startValue = Object.hasOwn(values, item.key) ? values[item.key] : initialValue(item);
    }
    if (item.representation === 'safe-integer') {
      if (v.minimum !== null) structure.min = Number(v.minimum);
      if (v.maximum !== null) structure.max = Number(v.maximum);
      structure.minFractionDigits = 0; structure.maxFractionDigits = 0;
    }
    if (item.options.length) {
      structure.options = item.options.filter(o => o.visible).map(o => ({ label: t(o.label_key), value: o.value, disabled: !o.enabled }));
      structure.optionLabel = 'label'; structure.optionValue = 'value';
    }
    if (item.lov) {
      structure.suggestions = []; structure.optionLabel = 'label'; structure.optionValue = 'value';
      let latest = 0;
      structure.completeMethod = async (event: {query: string}) => {
        if (!event || typeof event.query !== 'string') throw new Error('Invalid autocomplete event');
        const ticket = ++latest;
        const choices = await lookup(item.lov!.endpoint_id!, event.query, toOracleRow(spec, values));
        if (ticket !== latest) return;
        if (!Array.isArray(choices) || choices.some(c => !c || typeof c.label !== 'string' || !Object.hasOwn(c, 'value') || !c.columns)) throw new Error('Invalid LOV adapter response');
        if (new Set(choices.map(c => c.value)).size !== choices.length) throw new Error('Duplicate LOV option value');
        let cache = lovChoices.get(values); if (!cache) { cache = new Map(); lovChoices.set(values, cache); }
        cache.set(item.key, choices); structure.suggestions = choices; changed();
      };
    }
    return structure;
  });
}
export function bindGroup(group: FormGroup, items: UiItem[], values: Record<string, UiValue>, spec: UiBlockSpec): {unsubscribe(): void} {
  let registry = controls.get(values); if (!registry) { registry = new Map(); controls.set(values, registry); }
  const attached: {key: string; control: AbstractControl}[] = [];
  for (const item of items) {
    if (item.widget === 'button' || item.widget === 'image') continue;
    const control = group.get(item.key);
    if (!control) throw new Error('FormBlock did not create control: ' + item.key);
    if (!registry.has(item.key)) registry.set(item.key, new Set());
    registry.get(item.key)!.add(control); attached.push({key: item.key, control});
    // Required checkbox accepts both true and false (1 and 0), and rejects null.
    if (Object.hasOwn(values, item.key)) control.setValue(values[item.key], { emitEvent: false });
    control.setValidators((c: AbstractControl) => { const error = validateValue(item, c.value); return error ? { [error]: true } : null; });
    control.updateValueAndValidity({ emitEvent: false });
  }
  const update = () => {
    const raw: Record<string, UiValue> = group.getRawValue();
    for (const item of items) {
      if (item.widget === 'button' || item.widget === 'image') continue;
      const value = raw[item.key];
      if (item.lov) {
        if (value && typeof value === 'object') throw new Error('LOV optionValue contract requires a scalar value');
        const selected = lovChoices.get(values)?.get(item.key)?.find(c => c.value === value);
        if (selected) {
          const updates = item.lov.return_mappings.filter(m => m.return_item).map(mapping => {
            const target = spec.items.find(i => i.owner === mapping.return_item);
            if (!target) throw new Error('TODO: cross-block LOV return adapter: ' + mapping.return_item);
            if (!mapping.column || !Object.hasOwn(selected.columns, mapping.column)) throw new Error('Missing LOV return column');
            return {key: target.key, value: selected.columns[mapping.column] ?? null};
          });
          for (const update of updates) {
            values[update.key] = update.value;
            for (const control of registry!.get(update.key) ?? []) control.setValue(update.value, { emitEvent: false });
            raw[update.key] = update.value;
          }
        }
      }
      values[item.key] = raw[item.key] ?? null;
    }
  };
  update(); const subscription = group.valueChanges.subscribe(update);
  return { unsubscribe() { subscription.unsubscribe(); for (const a of attached) registry!.get(a.key)?.delete(a.control); } };
}
'''.replace('__STRUCT__',type_name)
    write(root/'form-structure.ts',helper)
    for block in model['blocks']:
        spec=render_spec(block,model); key=block['key']; cls=class_id(block)
        inferred=['// REVIEW: '+i['owner'].replace('\n',' ').replace('\r',' ')+' type_source=inferred; '+i['inference_reason'] for i in block['items'] if i['type_source']=='inferred']
        source=GENERATED+imports(r,["import { UiBlockSpec } from '../../model';"])+ '\n'.join(inferred)+'\nexport const spec: UiBlockSpec = '+dump(spec)+';\n'
        write(root/'blocks'/key/'form-structure.ts',source)
        bindings=["import { Component, Input, OnChanges, OnDestroy, ChangeDetectorRef, TemplateRef } from '@angular/core';", "import { FormGroup } from '@angular/forms';",
            f"import {{ {r['optimus_form_block_symbol']} }} from {jstr(r['optimus_import_path'])};", f"import {{ FormBlock }} from {jstr(r['form_block_type_import_path'])};",
            "import { UiItem, UiValue, UiBlockState, Translate, ActionHandler, LookupHandler, missingAction, missingLookup } from '../model';",
            "import { makeStructure, bindGroup } from '../form-structure';",f"import {{ spec }} from './{key}/form-structure';"]
        if block['mode']=='table':
            bindings.append(f"import {{ {r['table_symbol']} }} from {jstr(r['table_import_path'])};")
        symbols=[r['optimus_form_block_symbol']]+([r['table_symbol']] if block['mode']=='table' else [])
        decorator_imports=('\n  imports: ['+', '.join(symbols)+'],') if r['emit_imports'] else '\n  // TODO: add the host FormBlock component and, for a table, the host p-table import.'
        template=f'''@if (spec.mode === 'table') {{
  <{table} [value]="state.rows" [columns]="items">
    <ng-template #header><tr>@for (item of items; track item.key) {{ <th>{{{{ t(item.text_keys.prompt ?? item.text_keys.label!) }}}}</th> }}</tr></ng-template>
    <ng-template #body let-row><tr>@for (item of items; track item.key) {{
      <td><{fb} [formStructure]="cellStructure(row, item)" (formGroupGenerated)="bindCell($event, row, item)" /></td>
    }}</tr></ng-template>
  </{table}>
}} @else {{
  <{fb} [formStructure]="formStructure" (formGroupGenerated)="onFormGroupGenerated($event)" />
}}'''
        if block['mode']!='table':
            template=f'<{fb} [formStructure]="formStructure" (formGroupGenerated)="onFormGroupGenerated($event)" />'
        # Generated block component is a protected integration shell, like the root.
        source=PROTECTED+imports(r,bindings)+f'''// TODO: wire actions and LOVs through the root component inputs; no business rule executes implicitly.
@Component({{
  selector: {jstr(module['selector']+'-'+name(key,'kebab'))}, standalone: true,{decorator_imports}
  template: `{template}`, styles: []
}})
export class {cls} implements OnChanges, OnDestroy {{
  @Input({{required: true}}) state!: UiBlockState;
  @Input({{required: true}}) regionId!: string;
  @Input({{required: true}}) t!: Translate;
  @Input() action: ActionHandler = missingAction;
  @Input() lookup: LookupHandler = missingLookup;
  @Input() imageTemplates: Record<string, TemplateRef<unknown>> = {{}};
  protected readonly spec = spec;
  protected items: UiItem[] = [];
  protected formStructure: {type_name}[] = [];
  protected formGroup!: FormGroup;
  private readonly subscriptions = new Map<FormGroup, {{unsubscribe(): void}}>();
  private cells = new WeakMap<object, Map<string, {type_name}[]>>();
  constructor(private readonly changeDetector: ChangeDetectorRef) {{}}
  ngOnChanges(): void {{
    for (const sub of this.subscriptions.values()) sub.unsubscribe(); this.subscriptions.clear();
    this.cells = new WeakMap();
    const region = spec.regions.find(r => r.id === this.regionId);
    if (!region) throw new Error('Unknown UI region: ' + this.regionId);
    this.items = region.items.map(key => spec.items.find(i => i.key === key)!);
    this.formStructure = makeStructure(spec, this.items, this.state.values, this.t, this.action, this.lookup, () => this.changeDetector.markForCheck(), this.imageTemplates);
  }}
  onFormGroupGenerated(group: FormGroup): void {{
    this.formGroup = group; this.bind(group, this.items, this.state.values);
  }}
  protected cellStructure(row: Record<string, UiValue>, item: UiItem): {type_name}[] {{
    let cells = this.cells.get(row); if (!cells) {{ cells = new Map(); this.cells.set(row, cells); }}
    if (!cells.has(item.key)) cells.set(item.key, makeStructure(spec, [item], row, this.t, this.action, this.lookup, () => this.changeDetector.markForCheck(), this.imageTemplates));
    return cells.get(item.key)!;
  }}
  protected bindCell(group: FormGroup, row: Record<string, UiValue>, item: UiItem): void {{ this.bind(group, [item], row); }}
  private bind(group: FormGroup, items: UiItem[], values: Record<string, UiValue>): void {{
    this.subscriptions.get(group)?.unsubscribe(); this.subscriptions.set(group, bindGroup(group, items, values, spec));
  }}
  ngOnDestroy(): void {{ for (const sub of this.subscriptions.values()) sub.unsubscribe(); }}
}}
'''
        write(root/'blocks'/(key+'.component.ts'),source)
    root_bindings=["import { Component, Input, TemplateRef } from '@angular/core';", "import { Translate, ActionHandler, LookupHandler, UiBlockState, createBlockState, missingAction, missingLookup } from './model';"]
    for b in model['blocks']:
        root_bindings.extend([f"import {{ {class_id(b)} }} from './blocks/{b['key']}.component';",f"import {{ spec as {b['key']}Spec }} from './blocks/{b['key']}/form-structure';"])
    # Canvas/tab selection uses standard HTML semantics. No unknown Optimus tabs selector.
    surfaces=[]
    canvas_by_name={c['name']:c for c in model['canvases']}
    seen={}
    for block in model['blocks']:
        for region in block['regions']:
            c=region['canvas']; t=region['tab_page']
            if c not in seen:
                canvas=canvas_by_name.get(c,{}); surface={'name':c,'visible':canvas.get('visible',True),'tabs':[],'panels':[]}
                seen[c]=surface; surfaces.append(surface)
                surface['tabs']=[{'name':x['name'],'label_key':x['text_keys']['prompt'] or x['text_keys']['label'],'enabled':x['enabled']} for x in canvas.get('tabs',[]) if x['visible']]
            seen[c]['panels'].append({'block':block['key'],'region':region['id'],'tab':t})
    write(root/'surfaces.ts',GENERATED+'export interface UiSurface { name: string; visible: boolean; tabs: {name: string; label_key: string | null; enabled: boolean}[]; panels: {block: string; region: string; tab: string}[]; }\nexport const surfaces: UiSurface[] = '+dump(surfaces)+';\n')
    root_bindings.append("import { surfaces } from './surfaces';")
    root_src=PROTECTED+imports(r,root_bindings)+'''// TODO: load i18n/<module>.hu.json with the host i18n service and pass translate.
// TODO: replace native accessible tabs with the approved Optimus tabs API when known.
'''+f'''@Component({{
  selector: {jstr(module['selector'])}, standalone: true,
'''+('  imports: ['+', '.join(class_id(b) for b in model['blocks'])+'],\n' if r['emit_imports'] else '  // TODO: add the generated block components to imports.\n')+f'''  templateUrl: './component.html', styleUrl: './component.scss'
}})
export class {module['class_name']} {{
  @Input({{required: true}}) translate!: Translate;
  @Input() action: ActionHandler = missingAction;
  @Input() lookup: LookupHandler = missingLookup;
  @Input() imageTemplates: Record<string, TemplateRef<unknown>> = {{}};
  readonly states: Record<string, UiBlockState> = {{
'''+',\n'.join('    '+jstr(b['key'])+': createBlockState('+b['key']+'Spec)' for b in model['blocks'])+'''
  };
  protected readonly surfaces = surfaces;
  protected readonly activeTabs: Record<string, string> = Object.fromEntries(surfaces.map(s => [s.name, s.tabs.find(t => t.enabled)?.name ?? '']));
}
'''
    write(root/'component.ts',root_src)
    html=['<!-- CREATE_ONCE: --regenerate preserves this file. TODO: approved Optimus tabs adapter. -->','@for (surface of surfaces; track surface.name) {','  @if (surface.visible) {','    @if (surface.tabs.length) {','      <div role="tablist" class="flex flex-wrap gap-2">','        @for (tab of surface.tabs; track tab.name) {','          <button type="button" role="tab" [disabled]="!tab.enabled" [attr.aria-selected]="activeTabs[surface.name] === tab.name" (click)="activeTabs[surface.name] = tab.name">{{ translate(tab.label_key!) }}</button>','        }','      </div>','    }','    @for (panel of surface.panels; track panel.region) {','      @if (!panel.tab || activeTabs[surface.name] === panel.tab) {']
    for b in model['blocks']:
        html.extend([f'        @if (panel.block === {jstr(b["key"])}) {{',f'          <{module["selector"]}-{name(b["key"],"kebab")} [state]="states[panel.block]!" [regionId]="panel.region" [t]="translate" [action]="action" [lookup]="lookup" [imageTemplates]="imageTemplates" />','        }'])
    html.extend(['      }','    }','  }','}'])
    write(root/'component.html','\n'.join(html)+'\n')
    write(root/'component.scss','/* CREATE_ONCE: Tailwind and the host Optimus theme provide styling. */\n')
    return root


def overwrite_policy(path):
    parts=Path(path).parts
    if len(parts) == 3 and parts[:2] in {('backend', 'DPS'), ('backend', 'WBS')} and parts[-1].endswith(('ServiceImpl.java', 'ControllerImpl.java')):
        return 'create_once'
    if parts and parts[0]=='frontend':
        filename=parts[-1]
        if filename in {'component.ts','component.html','component.scss'} or filename.endswith('.component.ts'):
            return 'create_once'
    return 'always_regenerate'

"""Generate an editable FormBlock definition, not a generic CRUD application."""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from .common import jstr, name, write_json
from .generate import template, write
from .presentation import build_presentation, field_kind
from .xmlmodel import get


class Code(str):
    """An expression generated solely by this module (never untrusted source)."""


def ts(value, level=0):
    if isinstance(value, Code):
        return str(value)
    if isinstance(value, dict):
        pad = '  ' * (level + 1)
        return '{\n' + ',\n'.join(pad + (k if k.isidentifier() else jstr(k)) + ': ' + ts(v, level + 1) for k, v in value.items()) + '\n' + '  ' * level + '}'
    if isinstance(value, list):
        if not value:
            return '[]'
        return '[\n' + ',\n'.join('  ' * (level + 1) + ts(v, level + 1) for v in value) + '\n' + '  ' * level + ']'
    return json.dumps(value, ensure_ascii=False).replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')


def start_value(item, kind, config):
    value = item.get('initial')
    if value is None or value == '':
        return None
    if item['kind'] == 'checkbox' and config['form_block_checkbox_boolean']:
        return value == item['checked_value'] if value in {item['checked_value'], item['unchecked_value']} else None
    if item['type'] == 'number' and kind == 'inputNumber':
        try:
            return float(value) if '.' in value else int(value)
        except (ValueError, TypeError):
            return None
    if item['type'] == 'datetime':
        try:
            date = datetime.datetime.fromisoformat(value)
            if not date.tzinfo:
                return Code(f'new Date({date.year}, {date.month - 1}, {date.day}, {date.hour}, {date.minute}, {date.second})')
        except (ValueError, TypeError):
            pass
        return None
    return value


def generate_single(model, output: Path, config, module, *unused):
    ui = build_presentation(model, config)
    model['presentation'] = ui
    stem = ui['name']
    cls = name(stem, 'pascal')
    form_type = config['form_block_structure_type']
    blocks = ui['blocks']
    definitions, fields, methods, html, endpoints = [], [], [], [], {}
    buttons = False
    form_count = 0
    for index, block in enumerate(blocks):
        key = block['key']
        prop = '' if form_count == 0 else key
        form = 'formGroup' if not prop else prop + 'FormGroup'
        structure = 'formStructure' if not prop else prop + 'FormStructure'
        handler = 'onFormGroupGenerated' if not prop else 'on' + name(prop, 'pascal') + 'FormGroupGenerated'
        dto = cls + name(key, 'pascal') + 'FormValue'
        declaration = []
        objects = []
        for item in block['items']:
            kind = field_kind(item, config)
            definition = {'type': kind, 'labelText': item['label'], 'col': config['form_block_columns']}
            if not item['visible']:
                definition['invisible'] = True
            if not item['enabled']:
                definition['disabled'] = True
            if item['kind'] == 'button':
                buttons = True
                definition['onClick'] = Code(f'() => this.emitAction({jstr(block["name"])}, {jstr(item["name"])}, this.{form})')
            else:
                definition['formControlName'] = item['field']
                if item['required']:
                    definition['validator'] = True
                if item['kind'] == 'display':
                    definition['readonly'] = True
                if item['max_length'] and item['type'] == 'text' and item['kind'] not in {'checkbox'}:
                    definition['maxLenght'] = item['max_length']
                hint = get(item['properties'], 'Hint', 'Tooltip')
                if hint:
                    definition['inputInfo'] = hint
                if item['type'] == 'datetime':
                    definition['dateFormat'] = 'yy.mm.dd'
                    definition['showIcon'] = True
                    if any(token in get(item['properties'], 'FormatMask').upper() for token in ('HH', 'MI', 'SS')):
                        definition['showTime'] = True
                if item['kind'] == 'checkbox' and config['form_block_checkbox_boolean']:
                    definition['binary'] = True
                if item['options']:
                    definition.update(options=item['options'], optionLabel='label', optionValue='value')
                if item['kind'] == 'select':
                    definition['showClear'] = not item['required']
                if item['type'] == 'number' and kind == 'inputNumber':
                    definition['useGrouping'] = False
                    if item.get('scale') is not None:
                        definition['maxFractionDigits'] = item['scale']
                if item['type'] == 'number' and kind != 'inputNumber' and not block['table']:
                    definition['regexRule'] = {'regex': Code(r'/^[+-]?\d+(\.\d+)?$/'), 'example': '123.45'}
                    model['issues'].append({'code': 'NUMBER_TEXT_WIDGET', 'owner': f"{block['name']}.{item['name']}", 'scope': 'review',
                                           'detail': 'Hiányzó vagy 15-nél nagyobb precision: pontos decimális szöveg, nem JavaScript number. schema.json precision alapján inputNumber generálható.'})
                initial = start_value(item, kind, config)
                if initial is not None:
                    definition['startValue'] = initial
                value_type = 'Date' if item['type'] == 'datetime' else 'boolean' if item['kind'] == 'checkbox' and config['form_block_checkbox_boolean'] else 'number' if item['type'] == 'number' and kind == 'inputNumber' else 'string'
                declaration.append(f"  {item['field']}: {value_type} | null;")
            if not block['table'] or item['kind'] == 'button':
                objects.append(definition)
        definitions.append('export interface ' + dto + ' {\n' + '\n'.join(declaration) + '\n}')
        if objects:
            form_count += 1
            fields.append(f'  protected {form}!: FormGroup;\n  protected {structure}: {form_type}[] = ' + ts(objects, 1) + ';')
            methods.append(f'''  protected {handler}(formGroup: FormGroup): void {{
    if (this.{form} && this.{form} !== formGroup) {{
      formGroup.patchValue(this.{form}.getRawValue(), {{ emitEvent: false }});
      if (this.{form}.dirty) formGroup.markAsDirty();
    }}
    this.{form} = formGroup;
  }}''')
            html.append(f'<{config["html_selectors"]["form_block"]} [formStructure]="{structure}" (formGroupGenerated)="{handler}($event)" />')
        if block['database']:
            path = config['api_prefix'].rstrip('/') + '/' + module + '/' + block['key']
            endpoints[key] = {op: Code('this.apiBaseUrl + ' + jstr(path + '/' + endpoint)) for op, endpoint in config['endpoint_names'].items()}
        if block['table']:
            columns = [{config['table_bindings']['field']: i['field'], config['table_bindings']['header']: i['label']} for i in block['items'] if i['kind'] != 'button' and i['visible'] and not i['concealed']]
            fields.append(f'  protected {key}Rows: {dto}[] = [];\n  protected readonly {key}Columns = ' + ts(columns, 1) + ';')
            html.append(template('optimus-table.component.html.tpl', {
                'TAG_TABLE': config['html_selectors']['table'], 'ROWS_INPUT': config['table_bindings']['rows'],
                'COLUMNS_INPUT': config['table_bindings']['columns'], 'ROWS': key + 'Rows', 'COLUMNS': key + 'Columns'}, config).strip())
            model['issues'].append({'code': 'OPTIMUS_TABLE_CONTRACT', 'owner': block['name'], 'scope': 'review',
                'detail': 'Egy céges táblázatkomponens készült. Az alap ank-table/value/columns kötés integrációs sablon, nem ismert Optimus API. A tényleges selector/input/columns neveket a profilban vagy az optimus-table.component.html.tpl fájlban add meg.'})
    if buttons:
        definitions.append(f'''export interface {cls}Action {{
  block: string;
  action: string;
  value: Record<string, unknown>;
}}''')
        fields.insert(0, f'  @Output() readonly actionRequested = new EventEmitter<{cls}Action>();')
        methods.append('''  private emitAction(block: string, action: string, formGroup: FormGroup | undefined): void {
    this.actionRequested.emit({ block, action, value: formGroup?.getRawValue() ?? {} });
  }''')
    fields.insert(0, '  protected readonly apiBaseUrl = environment.baseUrl.replace(/\\/+$/, "");')
    if endpoints:
        fields.insert(1, '  protected readonly endpoints = ' + ts(endpoints, 1) + ';')
    values = {'CLASS': cls, 'SELECTOR': config['angular_selector_prefix'] + '-' + name(stem, 'kebab'),
              'DEFINITIONS': '\n\n'.join(definitions), 'FIELDS': '\n\n'.join(fields), 'METHODS': '\n\n'.join(methods),
              'HTML': '\n'.join('    ' + line for line in html),
              'IMPORT_HINT': 'Component' + (', Output, EventEmitter' if buttons else '')}
    write(output / 'frontend' / stem / (stem + '.component.ts'), template('single.component.ts.tpl', values, config))
    write_json(output / 'analysis' / 'frontend-plan.json', ui)
    model['issues'].append({'code': 'FORMBLOCK_SOURCE_ONLY', 'owner': model['name'], 'scope': 'review',
        'detail': 'Szerkeszthető FormBlock képernyőváz: a FormGroup-ot a céges komponens hozza létre. Csak az eredeti gombokhoz készül actionRequested; nincs automatikus CRUD vagy Forms triggerfuttatás. environment és Angular/Optimus importok a hostból.'})
    for change in ui['changes']:
        if change['rule'] == 'calendar-target-review':
            model['issues'].append({'code': 'CALENDAR_TARGET_REVIEW', 'owner': change['source'], 'scope': 'review', 'detail': change['detail']})

"""Editable, single-file Angular screen using the supplied FormBlock contract."""
import datetime
import html
import json
import re
from collections import Counter
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from .angular_single import Code
from .common import MigrationError, write_json
from .generate import write
from .screen_model import apply_screen_types, build_screen
from .screen_validation import TEXT_WIDGETS
from .ts_code import sq
from .screen_layout import contains
from . import form_calls
from . import screen_api
from . import screen_windows
from .xmlmodel import canonical


WF_TABLE_FILE = 'wf-table.ts'
WF_TABLE_IMPORT = '../wf-table'


def wf_table_source() -> str:
    """frontend/wf-table.ts: the table every generated screen uses (one copy per project)."""
    from pathlib import Path
    return (Path(__file__).with_name('templates') / 'wf-table.ts.tpl').read_text(encoding='utf-8')


def button_sources(resolution) -> dict:
    """The WHEN-BUTTON-PRESSED code of every button (BLOCK.ITEM -> source): the TODO of an unwired button shows it."""
    from .forms_keys import screen_context
    from .xmlmodel import tag
    from .plsql_passthrough import decode_line_escapes
    context = screen_context(next(e for e in resolution.root.iter() if tag(e) == 'formmodule'))
    return {(t['block'] + '.' + t['item']).upper(): decode_line_escapes(t['source'] or '') for t in context['triggers']
            if t['event'] == 'WHEN-BUTTON-PRESSED' and t['item'] and t['block']}


def angular_string(value):
    # Keep the common output readable while still escaping hostile XML names.
    literal = "'" + str(value).replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n').replace('\r', '\\r') + "'"
    return html.escape(literal, quote=False).replace('"', '&quot;')


def initial(item):
    value = item['initial_value']
    if value in (None, ''):
        return None
    if item['widget'] == 'checkbox':
        return value == item['checked'] if value in {item['checked'], item['unchecked']} else None
    if re.match(r'\s*[:=]', str(value)) or str(value).upper() in {'SYSDATE', 'SYSTEM_DATE', 'USER'}:
        return None
    if item['representation'] == 'safe-integer':
        try:
            result = int(value)
            return result if abs(result) <= 9007199254740991 else None
        except (TypeError, ValueError):
            return None
    if item['representation'] == 'date':
        try:
            d = datetime.datetime.fromisoformat(value)
            if not d.tzinfo:
                return Code(f'new Date({d.year}, {d.month - 1}, {d.day}, {d.hour}, {d.minute}, {d.second})')
        except (TypeError, ValueError):
            pass
        return None
    return value


def structure(item, config, code=None):
    widget = item['widget']; v = item['validation']
    if item.get('spacer'):
        # Catalogued layout gap (L_URES_*): keeps its cell in the grid, but has no
        # form control, value or validation. Type: screen_spacer_type (default text).
        definition = {'type': config['screen_spacer_type'], 'ownId': item['owner'], 'labelText': item['label'],
                      'col': str(item['col'])}
        if item['col_before']: definition['colBefore'] = str(item['col_before'])
        if item['col_after']: definition['colAfter'] = str(item['col_after'])
        return definition
    if widget in {'unsupported', 'image', 'tree'}:
        return None
    from .ui_config import widget_map
    entry = widget_map(config)['widgets'][widget]
    definition = {'type': entry['exact_decimal_type'] if item['representation'] == 'decimal-string' and widget == 'number' else entry['type'],
                  'ownId': item['owner']}
    if widget == 'button':
        # The caption property is configurable: the standalone FormBlock button
        # reads labelText, btnLabel is the inputGroup add-on's caption.
        definition[config['screen_button_label_property']] = item['label']
    else:
        definition.update(formControlName=item['key'], labelText=item['label'])
    definition['col'] = str(item['col'])
    if item['col_before']: definition['colBefore'] = str(item['col_before'])
    if item['col_after']: definition['colAfter'] = str(item['col_after'])
    if item['hint'] and item['hint'] != item['label']:
        # inputInfo is the helper text under an input; a button has none.
        definition['tooltipData' if widget == 'button' else 'inputInfo'] = item['hint']
    if not item['enabled']: definition['disabled'] = True
    if item['readonly']: definition['readonly'] = True
    if v['required'] and widget not in {'checkbox', 'button'}: definition['validator'] = True
    if v['maximum_length'] and widget in {'text', 'textarea', 'password', 'display', 'autocomplete'}: definition['maxLenght'] = v['maximum_length']
    if v['fixed_length'] and widget in TEXT_WIDGETS: definition['minLenght'] = v['fixed_length']
    elif v.get('minimum_length') and widget in TEXT_WIDGETS: definition['minLenght'] = v['minimum_length']
    if v['case'] in {'upper', 'lower'} and widget in TEXT_WIDGETS:
        definition['regexRule'] = {'regex': Code(r'/^[^\p{Ll}]*$/u' if v['case'] == 'upper' else r'/^[^\p{Lu}]*$/u'), 'example': ''}
    if widget in {'date', 'datetime'}:
        definition.update(dateFormat=v['date_format'] or 'yy.mm.dd', showIcon=True, showTime=widget == 'datetime')
    if widget == 'number' and item['representation'] == 'safe-integer':
        definition.update(useGrouping=False, maxFractionDigits=0)
        for key, source, rounding in [('min', 'minimum', ROUND_CEILING), ('max', 'maximum', ROUND_FLOOR)]:
            if v[source] is not None:
                bound = int(Decimal(v[source]).to_integral_value(rounding=rounding))
                if abs(bound) <= 9007199254740991: definition[key] = bound
    if widget == 'checkbox': definition['binary'] = True
    if item['options']:
        definition.update(options=item['options'], optionLabel='label', optionValue='value')
    value = initial(item)
    if value is not None and widget != 'button': definition['startValue'] = value
    # The button's click: a method of the component (screen_code).
    if widget == 'button':
        definition['btnSeverity'] = 'primary'
        if code is not None and item['owner'] in code.click:
            definition['onClick'] = Code('() => this.' + code.click[item['owner']] + '()')
    if item['lov']:
        # The LOV endpoint has its method (lov<Name>); filling the suggestions is the developer's.
        definition.update(dropdown=True, optionLabel='label', optionValue='value', suggestions=[])
    return definition


def layout_preview(plan):
    """Side-by-side check: the Forms canvas from its coordinates, and the grid we emit.

    Offline, self-contained HTML. Widths of 1 are placeholders in some exports
    and are drawn as thin markers rather than trusted.
    """
    esc = lambda s: html.escape(str(s), quote=True)
    columns = plan['layout_settings']['columns']
    parts = ['<!doctype html><html lang="hu"><meta charset="utf-8"><title>' + esc(plan['module']['title']) + ' – elrendezés</title>',
             '<style>body{font:13px system-ui,sans-serif;margin:16px;color:#222}h2{margin:24px 0 8px}'
             '.pair{display:flex;gap:24px;align-items:flex-start;flex-wrap:wrap}.src{position:relative;border:1px solid #999;background:#f7f7f2}'
             '.it{position:absolute;border:1px solid #467;background:#fff;font-size:11px;white-space:nowrap}'
             '.it b{position:absolute;top:-14px;left:0;font-weight:500;color:#345;overflow:hidden;max-width:180px}.fr{position:absolute;border:1px dashed #b85}'
             '.fr span{position:absolute;top:-8px;left:6px;background:#f7f7f2;color:#853;font-size:11px}.tx{position:absolute;color:#555;font-size:11px}'
             '.grid{display:grid;grid-template-columns:repeat(' + str(columns) + ',1fr);gap:6px 8px;width:560px;border:1px solid #999;padding:10px;background:#fff}'
                                                                                 '.cell{border:1px solid #467;padding:2px 4px;font-size:11px;min-height:28px}.cell i{display:block;color:#345;font-style:normal}'
                                                                                 '.sep{display:flex;align-items:center;justify-content:center;font-weight:600}.btn{background:#e8eef4}'
                                                                                 '.gap{border-style:dashed;border-color:#bbb;color:#999}'
                                                                                 'caption{text-align:left;color:#666}</style>',
             '<h1>' + esc(plan['module']['title']) + '</h1><p>Bal: az eredeti Forms-canvas a koordinátákból. '
                                                     'Jobb: a generált ' + str(columns) + ' oszlopos FormBlock-rács. Csak a megjelenített régiók.</p>']
    for surface in plan['surfaces']:
        sections = [s for s in plan['sections'] if s['canvas'] == surface['name']]
        if not sections: continue
        items = [i for s in sections for i in s['items']]
        marks = [g for g in plan['graphics'] if g['canvas'] == surface['name'] and g['visible'] and g['label']]
        right = max([i['x'] + max(i['width'], 1) for i in items] + [g['x'] + g['width'] for g in marks] + [1])
        bottom = max([i['y'] + max(i['height'], 1) for i in items] + [g['y'] + g['height'] for g in marks] + [1])
        scale = 560 / right
        # Rows keep their order and spacing, stretched so that labels stay legible.
        levels = sorted({i['y'] for i in items})
        gaps = [b - a for a, b in zip(levels, levels[1:]) if b > a]
        yscale = min(max(scale, 34 / min(gaps)), max(scale, 900 / bottom)) if gaps else scale
        source = ['<div class="src" style="width:560px;height:' + str(int(bottom * yscale) + 40) + 'px">']
        for g in marks:
            box = 'left:%dpx;top:%dpx;width:%dpx;height:%dpx' % (g['x'] * scale, g['y'] * yscale + 16, g['width'] * scale, g['height'] * yscale)
            source.append('<div class="fr" style="' + box + '"><span>' + esc(g['label']) + '</span></div>' if g['kind'] == 'frame'
                          else '<div class="tx" style="' + box + '">' + esc(g['label']) + '</div>')
        for i in items:
            width = max(i['width'] * scale, 4)
            source.append('<div class="it" title="' + esc(i['owner']) + '" style="left:%dpx;top:%dpx;width:%dpx;height:%dpx">'
                          % (i['x'] * scale, i['y'] * yscale + 16, width, max(min(i['height'] * yscale, 24), 16))
                          + '<b>' + esc(i.get('separator') or i['label']) + '</b>' + esc('térköz' if i.get('spacer') else i['widget']) + '</div>')
        source.append('</div>')
        grid = []
        for s in sections:
            grid.append('<div class="grid"><div style="grid-column:1/-1;color:#666">' + esc(s['block']) + ' · ' + esc(s['mode']) + '</div>')
            previous = None
            for i in s['items']:
                first = i.get('row') != previous
                previous = i.get('row')
                def cell(css, width, body='', title=''):
                    nonlocal first
                    column = ('1 / span ' if first else 'span ') + str(width); first = False
                    return ('<div class="' + css + '"' + (' title="' + esc(title) + '"' if title else '')
                            + ' style="grid-column:' + column + '">' + body + '</div>')
                if i['col_before']: grid.append(cell('', i['col_before']))
                if i.get('separator') and i.get('separator_col'):
                    if i.get('separator_before'): grid.append(cell('', i['separator_before']))
                    grid.append(cell('cell sep', i['separator_col'], esc(i['separator'])))
                if i.get('spacer'):
                    grid.append(cell('cell gap', i['col'], '<i>' + esc(i['label']) + '</i>térköz', i['owner']))
                    continue
                grid.append(cell('cell btn' if i['widget'] == 'button' else 'cell', i['col'],
                                 '<i>' + esc(i['label']) + '</i>' + esc(i['widget']), i['owner']))
            grid.append('</div>')
        parts.append('<h2>' + esc(surface['name']) + '</h2><div class="pair">' + ''.join(source) + '<div>' + ''.join(grid) + '</div></div>')
    return ''.join(parts) + '</html>\n'


def todo_summary(plan):
    """One line the developer can act on, repeated at the top of every run."""
    actions = plan['actions']; audit = plan.get('trigger_audit') or {'counts': {'mixed': 0, 'own': 0}}
    manual = [a for a in actions if not a.get('steps')]
    parts = [str(len(manual)) + ' gomb-akció kézi bekötése', str(len(actions) - len(manual)) + ' felismert gomb',
             str(len(plan['lookups'])) + ' LOV-végpont', str(audit['counts']['own'] + audit['counts']['mixed']) + ' saját/vegyes trigger',
             str(len(plan['inferred_types'])) + ' kikövetkeztetett típus ellenőrzése']
    return ['## Migrációs teendők', '', ' · '.join(parts) + '.', '']


def manual_navigations(resolution, navigations) -> dict:
    """Buttons whose form call cannot be translated: a TODO with the readable original code."""
    from .forms_keys import screen_context
    from .xmlmodel import get, tag
    context = screen_context(next(e for e in resolution.root.iter() if tag(e) == 'formmodule'))
    model = {'program_units': [{'name': get(u, 'Name'), 'programunittext': get(u, 'ProgramUnitText')} for u in context['program_units']]}
    result = {}
    for trigger in context['triggers']:
        if trigger['event'] != 'WHEN-BUTTON-PRESSED' or not trigger['item'] or not trigger['block']:
            continue
        owner = trigger['block'] + '.' + trigger['item']
        if owner in navigations or not form_calls.manual_navigation(trigger['source'] or '', model):
            continue
        result[owner] = {'code': form_calls.readable(trigger['source'] or '', model)}
    return result


def screen_navigations(resolution, forms, config) -> dict:
    """Buttons whose WHEN-BUTTON-PRESSED is a pure Forms form call, with on-screen parameter values."""
    from .forms_keys import screen_context
    from .xmlmodel import get, tag
    context = screen_context(next(e for e in resolution.root.iter() if tag(e) == 'formmodule'))
    model = {'program_units': [{'name': get(u, 'Name'), 'programunittext': get(u, 'ProgramUnitText')} for u in context['program_units']]}
    keys = {i['owner'].upper(): (s['block'], i['key']) for s in forms for i in s['items']}
    result = {}
    for trigger in context['triggers']:
        if trigger['event'] != 'WHEN-BUTTON-PRESSED' or not trigger['item'] or not trigger['block']:
            continue
        nav = form_calls.navigation(trigger['source'] or '', model)
        if not nav:
            continue
        params = []
        for p in nav['params']:
            if 'item' in p:
                if p['item'] not in keys:
                    params = None  # the value is not on this screen: manual
                    break
                block, key = keys[p['item']]
                params.append({'name': p['name'], 'block': block, 'key': key})
            else:
                params.append({'name': p['name'], 'value': p['value']})
        if params is not None:
            owner = trigger['block'] + '.' + trigger['item']
            result[owner] = {'form': nav['form'], 'call': nav['call'], 'route': form_calls.route(config, nav['form']), 'params': params}
    return result


def generate(resolution, ui, output, config, module, discovery):
    if config['html_selectors']['table'] != 'p-table':
        raise MigrationError('SCREEN_TABLE_CONTRACT: a képernyőváz az Optimus p-table komponensét használja.')
    plan = build_screen(resolution, ui, module, config)
    plan['window_references'] = screen_windows.navigation_references(discovery, plan)
    plan['window_controls'] = screen_windows.controls(plan)
    write_json(output / 'analysis/screen-overrides.template.json', plan.pop('override_template'))
    if plan['overrides']: write_json(output / 'analysis/screen-overrides.applied.json', config['screen_overrides'])
    write_json(output / 'analysis/ui-model-strict.json', ui)
    apply_screen_types(ui, plan)
    plan['ui_issue_counts'] = dict(sorted(Counter(i['code'] for i in ui['issues']).items()))
    key = plan['module']['key']; root = output / 'frontend' / key
    form_symbol = config['optimus_form_block_symbol'] or 'FormBlocksComponent'
    form_type = config['form_block_structure_type']
    forms = [s for s in plan['sections'] if s['mode'] == 'form']
    tables = [s for s in plan['sections'] if s['mode'] == 'table']
    buttons = bool(plan['actions'])
    toast_symbol = config['toast_service_symbol']
    window_controls = plan['window_controls']
    # Backend calls through the exact CL Constants paths (analysis/backend-plan.json "api").
    from . import input_bindings
    api = input_bindings.enrich_api(screen_api.load_api(output), discovery, output)
    wiring = screen_api.wiring(plan, ui, api, key, forms, tables)
    # Forms CALL_FORM/OPEN_FORM/NEW_FORM buttons -> Angular Router, with the parameter list.
    navigations = screen_navigations(resolution, forms, config) if buttons else {}
    manual_navs = manual_navigations(resolution, navigations) if buttons else {}
    plan['manual_navigations'] = sorted(manual_navs)
    plan['navigations'] = navigations  # screen-plan.json and MIGRATION_NOTES
    # The screen's code (screen_code): the frame, the endpoints and the requests of the buttons.
    from .screen_code import ScreenCode
    code = ScreenCode(plan, forms, tables, wiring, config, navigations, manual_navs, button_sources(resolution))
    code.build()
    if wiring:
        wiring['called'] = sorted(code.called)
    plan['backend_calls'] = screen_api.summary(wiring)
    imports, symbols, labels = [], [], {}

    def use(symbol, path):
        if symbol not in symbols:
            symbols.append(symbol)
            imports.append('import { ' + symbol + " } from '@openng/optimus-ui/" + path + "';")

    def label(value):
        field = 'text' + str(len(labels) + 1)
        labels[field] = value
        return 'labels.' + field

    if forms:
        symbols.append(form_symbol)

    def section_html(section):
        prop = section['property']
        output = []
        if section['mode'] == 'table':
            t = code.table_name(section['block'])
            row_actions = (' [actions]="' + t + 'Actions" (action)="onRowAction($event)"'
                           if any(i['widget'] == 'button' for i in section['items']) else '')
            output.append('<wf-table [value]="' + t + 'Rows" [columns]="' + t + 'Columns" [rows]="' + str(max(1, min(1000, section['records'])))
                          + '" [(selection)]="' + t + 'Selection"' + row_actions + ' />')
            if 'WfTable' not in symbols:
                symbols.append('WfTable')
        else:
            fb = config['html_selectors']['form_block']
            # structures is a Record: noPropertyAccessFromIndexSignature (Angular CLI default) wants structures['x']
            output.append('<' + fb + ' [formStructure]="structures[' + angular_string(prop) + ']" (formGroupGenerated)="onFormGroupGenerated('
                          + angular_string(prop) + ', $event)" />')
        for item in section['items']:
            if item['widget'] in {'unsupported', 'image', 'tree'}:
                output.append('<p class="text-sm text-slate-500">{{ ' + label(item['label']) + ' }}</p>')
                plan['notices'].append({'code': 'WIDGET_ADAPTER_REQUIRED', 'owner': item['owner'], 'detail': item['widget'] + ': helye és felirata megmaradt; a vezérlőt a hostban kell kialakítani.'})
        if section['group_label']:
            use('FieldsetModule', 'fieldset')
            output = ['<p-fieldset [legend]="' + label(section['group_label']) + '">', *output, '</p-fieldset>']
        return output

    def contents(surface, page=''):
        relevant = [s for s in plan['sections'] if s['canvas'] == surface['name'] and s['tab'] == page]
        graphics = [g for g in plan['graphics'] if g['visible'] and g['label'] and g['canvas'] == surface['name'] and g['tab'] == page]
        frames = {g['name']: g for g in graphics if g['kind'] == 'frame'}
        nodes = {'': []}
        for frame in frames: nodes[frame] = []
        for s in relevant:
            # The FormBlock lays out its own fields: no wrapper element around a region.
            nodes[s['frame']].append((s['y'], s['x'], section_html(s)))
        for g in graphics:
            if g['kind'] == 'text':
                if g['label'] == plan['module']['title']:
                    g.update(status='deduplicated', target='h1 / title', reason='A form címével egyezik; külön szövegként nem ismételjük meg.')
                else:
                    target = label(g['label'])
                    containing = [f for f in frames.values() if contains(f, g)]
                    frame = min(containing, key=lambda f: f['width'] * f['height'])['name'] if containing else ''
                    g.update(status='rendered', target='p / ' + target + ('; keret: ' + frame if frame else ''), reason='Statikus szöveg a canvas/fül olvasási sorrendjében.')
                    nodes[frame].append((g['y'], g['x'], ['<p class="text-sm">{{ ' + target + ' }}</p>']))

        def frame_contents(parent):
            children = list(nodes[parent])
            for key, frame in frames.items():
                if frame.get('parent', '') != parent: continue
                inner = frame_contents(key)
                if not inner: continue
                use('FieldsetModule', 'fieldset')
                frame.update(status='rendered', target='p-fieldset.legend; szülő: ' + (parent or 'canvas/fül') + '; régiók: ' + ', '.join(s['key'] for s in relevant if s['frame'] == key), reason='FrameTitle a keret felirata; teljes befoglaló téglalap szerinti csoportosítás.')
                lines = ['<p-fieldset [legend]="' + label(frame['label']) + '">', *inner, '</p-fieldset>']
                children.append((frame['y'], frame['x'], lines))
            return [line for _, _, lines in sorted(children, key=lambda x: (x[0], x[1])) for line in lines]
        return frame_contents('')

    # The host page provides the frame and the heading: no wrapper div, no <h1> title.
    template, tab_fields = [], []

    def surface_html(surface):
        inner = contents(surface)
        tabs = [t for t in surface['tabs'] if t['visible']]
        sk = surface['key']
        if tabs:
            active = next((t['name'] for t in tabs if t['enabled']), '')
            tab_fields.append('  protected ' + sk + 'ActiveTab = ' + sq(active) + ';')
            accordion = config['screen_tab_layout'] == 'accordion'
            use('AccordionModule' if accordion else 'TabsModule', 'accordion' if accordion else 'tabs')
            if accordion:
                inner.append('<p-accordion [(value)]="' + sk + 'ActiveTab">')
                for tab in tabs:
                    inner += ['<p-accordion-panel [value]="' + angular_string(tab['name']) + '" [disabled]="' + str(not tab['enabled']).lower() + '">',
                              '<p-accordion-header>{{ ' + label(tab['label']) + ' }}</p-accordion-header>', '<p-accordion-content>',
                              *contents(surface, tab['name']), '</p-accordion-content>', '</p-accordion-panel>']
                inner.append('</p-accordion>')
            else:
                inner += ['<p-tabs [(value)]="' + sk + 'ActiveTab">', '<p-tablist>']
                for tab in tabs:
                    inner.append('<p-tab [value]="' + angular_string(tab['name']) + '" [disabled]="' + str(not tab['enabled']).lower() + '">{{ ' + label(tab['label']) + ' }}</p-tab>')
                inner += ['</p-tablist>', '<p-tabpanels>']
                for tab in tabs:
                    inner += ['<p-tabpanel [value]="' + angular_string(tab['name']) + '">', *contents(surface, tab['name']), '</p-tabpanel>']
                inner += ['</p-tabpanels>', '</p-tabs>']
        conditions = []
        if surface['name'] in window_controls['canvases']:
            conditions.append('canvasVisible[' + angular_string(surface['name']) + ']')
        if surface['window'] in window_controls['content'] and canonical(surface['type']) == 'content':
            conditions.append('activeContentCanvas[' + angular_string(surface['window']) + '] === ' + angular_string(surface['name']))
        return ['@if (' + ' && '.join(conditions) + ') {', *inner, '}'] if conditions else inner

    used_windows = [w for w in plan['windows'] if w['role'] != 'unused']
    containers = [w for w in used_windows if w['role'] == 'main'] + [None] + [w for w in used_windows if w['role'] == 'dialog']
    for window in containers:
        inner = [line for surface in plan['surfaces'] if surface['window'] == (window['name'] if window else '') for line in surface_html(surface)]
        if not inner: continue
        if window and window['role'] == 'dialog':
            use('DialogModule', 'dialog')
            target = angular_string(window['name'])
            inner = ['<p-dialog [header]="' + label(window['title']) + '" [(visible)]="windowVisible[' + target + ']"',
                     '  [modal]="' + str(window['modal']).lower() + '" [closable]="' + str(window['close_allowed']).lower() + '"',
                     '  [closeOnEscape]="false" [dismissableMask]="false" [focusTrap]="' + str(window['modal']).lower() + '"',
                     '  styleClass="w-[95vw] max-w-5xl">', *inner, '</p-dialog>']
        elif window and window['name'] in window_controls['windows']:
            inner = ['@if (windowVisible[' + angular_string(window['name']) + ']) {', *inner, '}']
        template += inner
    disabled = {op for spec in (wiring or {}).get('ui_disabled', {}).values() for op in spec}
    if wiring and wiring.get('commit') and 'write' not in disabled:
        # The save of the screen (Forms COMMIT_FORM); a KEY-COMMIT without its default save offers none.
        use('ButtonModule', 'button')
        template[:0] = ['<button pButton type="button" (click)="save()">Mentés</button>']

    # ---------------------------------------------------------------- the component
    structures = {}
    for section in forms:
        entries = []
        for item in section['items']:
            definition = structure(item, config, code)
            if definition is None: continue
            if item.get('separator') and item.get('separator_col'):
                # Forms draws this prompt between the two fields, not above the second.
                separator = {'type': 'label', 'ownId': item['owner'] + '#separator', 'labelText': item['separator'],
                             'col': str(item['separator_col'])}
                if item.get('separator_before'): separator['colBefore'] = str(item['separator_before'])
                entries.append(separator)
            entries.append(definition)
        structures[section['property']] = entries
    for section in tables:
        if not [i for i in section['items'] if i['widget'] not in {'button', 'image', 'unsupported', 'tree'} and not i.get('spacer')]:
            plan['notices'].append({'code': 'EMPTY_TABLE', 'owner': section['block'], 'detail': 'Nincs biztonságosan megjeleníthető táblázatos mező.'})
    methods = [code.constructor()]
    if wiring and wiring['endpoints']:
        methods += [code.endpoint_method(e) for e in wiring['endpoints']]
    group_method = code.form_group_method()
    if group_method:
        methods.append(group_method)
    methods += code.methods
    methods += code.result_methods()
    methods += code.more_rows_methods()
    methods += code.changes_methods()
    fields = code.class_fields(tab_fields, labels, toast_symbol, form_type, structures)
    if code.input_rows:
        methods.append(code.input_helper())
    body = '\n\n'.join(fields) + '\n\n' + '\n\n'.join(methods)

    # ---------------------------------------------------------------- the source
    head = ["import { " + ', '.join(sorted(code.core, key=['Component', 'Input', 'inject'].index)) + " } from '@angular/core';"]
    if wiring and wiring['endpoints']:
        head.append("import { HttpClient } from '@angular/common/http';")
    if code.angular_forms:
        head.append("import { " + ', '.join(sorted(code.angular_forms)) + " } from '@angular/forms';")
    if code.rxjs:
        head.append("import { " + ', '.join(sorted(code.rxjs, key=lambda n: (n != 'EMPTY', n))) + " } from 'rxjs';")
    if config['emit_imports'] and config['toast_service_import_path']:
        head.append('import { ' + toast_symbol + ' } from ' + sq(config['toast_service_import_path']) + ';')
    else:
        head.append('// TODO: importáld a saját csomagodból: ' + toast_symbol + ' (config: toast_service_import_path).')
    company = ['ServiceBase'] + (['WFF'] if re.search(r'\bWFF\.', body) else [])
    head.append('// TODO: importáld a saját csomagodból: ' + ', '.join(company) + ' (java-imports.json).')
    if tables:
        head.append("import { WfTable } from '" + WF_TABLE_IMPORT + "';")
    if forms:
        if config['emit_imports'] and config['optimus_import_path'] and config['form_block_type_import_path']:
            head.append('import { ' + form_symbol + ' } from ' + sq(config['optimus_import_path']) + ';')
            head.append('import { FormBlock } from ' + sq(config['form_block_type_import_path']) + ';')
        else:
            head.append('// TODO: importáld a saját csomagodból: ' + form_symbol + ' és ' + form_type + '.')
    template_text = '\n'.join('    ' + line for line in template).replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')
    source = '// CREATE_ONCE: szerkeszthető képernyő. Migrációs részletek: MIGRATION_NOTES.md.\n' + '\n'.join(head + imports) + '\n'
    source += '\n@Component({\n  selector: ' + sq(plan['module']['selector']) + ',\n  standalone: true,\n  imports: [' + ', '.join(symbols) + '],\n  template: `\n' + template_text + '\n  `,\n})\n'
    source += 'export class ' + plan['module']['class'] + ' extends ServiceBase {\n' + body + '\n}\n'
    # Imports from java-imports.json ("/" paths): ToastService, FormBlock, ServiceBase, WfTable ...
    from . import java_imports, ts_imports
    source, ts_report = ts_imports.tidy(source, java_imports.load_ts(config))
    write(root / (key + '.component.ts'), source)
    input_bindings.write_report(output, (root / (key + '.component.ts')).relative_to(output).as_posix(),
                                source, list(code.input_rows.values()), code.input_sources, config, code.input_warnings)
    # One copy per project, identical each time (like CommonMigrateTools): replace it on a new version.
    if tables:
        table_source, _ = ts_imports.tidy(wf_table_source(), java_imports.load_ts(config))
        write(output / 'frontend' / WF_TABLE_FILE, table_source)
        plan['wf_table'] = WF_TABLE_FILE
    write_json(output / 'analysis/ts-imports.json', ts_report)
    write_json(output / 'analysis/screen-plan.json', plan)
    write(output / 'analysis/layout-preview.html', layout_preview(plan))
    # Every screen (main window, dialogs, tab pages) as it will look: the web UI shows this.
    from .screen_preview import preview_html
    write(output / 'analysis/screen-preview.html', preview_html(plan))
    write_json(output / 'analysis/field-lengths.template.json', field_lengths_template(plan))
    write(root / 'MIGRATION_NOTES.md', notes(plan, discovery, config))
    return plan


def field_lengths_template(plan):
    """Pre-filled helper JSON: every text input of this form with its current lengths."""
    from .screen_model import LENGTH_WIDGETS
    controls = [(s['block'], i) for s in plan['sections'] if s['mode'] == 'form'
                for i in s['items'] if i['widget'] in LENGTH_WIDGETS and not i.get('spacer')]
    counts = {}
    for _, item in controls:
        counts[item['key']] = counts.get(item['key'], 0) + 1
    fields = {}
    for block, item in controls:
        v = item['validation']
        key = item['key'] if counts[item['key']] == 1 else block + '.' + item['key']
        fields[key] = {'min': v.get('minimum_length') if v.get('minimum_length') is not None else v['fixed_length'],
                       'max': v['maximum_length'] if v['maximum_length'] is not None else v['fixed_length']}
    return {'version': 1,
            'description': 'Mezőhosszak: formControlName (vagy BLOKK.formControlName) -> {min, max}; null = nincs. '
                           'Használat: migrate/batch --field-lengths, vagy a webes felület Mezőhosszak feltöltése. '
                           'A megadott érték felülírja a Forms MaximumLength/FixedLength értékét; a nem módosított sorok törölhetők.',
            'fields': fields}


def notes(plan, discovery, config):
    from .discovery import source_view
    def cell(v):
        view, _ = source_view(str(v))
        return html.escape(view, quote=False).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
    def sql_block(source):
        sql, _ = source_view(source)
        fence = '`' * max(3, max((len(s) + 1 for s in re.findall(r'`+', sql)), default=3))
        return [fence + 'sql', sql, fence, '']
    lines = ['# ' + plan['module']['title'], '', 'Szerkeszthető Angular képernyőváz; az üzleti működés bekötése fejlesztői feladat.', '',
             'A triggerfordítás és a tényleges eseménybekötés külön leltára: `RUNTIME_COVERAGE.md` és `analysis/runtime-coverage.json` a modul gyökerétől.', '',
             *todo_summary(plan),
             '## Beillesztés', '', '- A komponens egyetlen `.component.ts`; a FormBlock és a Tailwind a fogadó alkalmazásból érkezik.',
             '- A publikus komponensek importja kizárólag `@openng/optimus-ui/*`. A privát FormBlock importját a céges profil adja meg, vagy egészítsd ki a jelölt TODO-t.',
             '- Táblázat: `<wf-table>` (`' + WF_TABLE_FILE + '`, egyszer kell a projektbe tenni; a java-imports.json `WfTable` bejegyzése '
             'adja az importját). Sorai: `<blokk>Rows`, kijelölt sora: `<blokk>Selection`, oszlopai: `<blokk>Columns`. A p-table minden '
             'bemenete és sablonja (`#header`, `#body`, `#caption` ...) átadható neki, például saját szűrőkhöz.',
             '- A komponens a céges `ServiceBase`-t örökli, és csak a keretet adja (4.27): a FormBlock-régiók szerkezetét, a '
             'táblázatokat, a végpontmetódusokat és gombonként (`on<Gomb>Click()`) a gomb HTTP-kérését, kezdetlegesen, a '
             'képernyő értékeivel. Forms-emulációt nem tartalmaz: a mezőállapotok, a LOV-javaslatok és visszaírt értékek, az '
             'alertek, a :GLOBAL értékek, a checkbox Forms-értékei és a válaszok feldolgozása fejlesztői feladat (TODO a metódusban, '
             'a nem fordított gomboknál az eredeti Forms-kóddal).',
             '- A mezők műveleti engedélyeit és formátummaszkjait a query/insert/update móddal együtt ellenőrizd; a képernyő nem Forms-futtató.',
             '- `--regenerate` megőrzi a komponens kézi módosításait. Új elrendezéshez generálj új célmappába, és hasonlítsd össze.', '',
             '## Képernyőrészek', '', '| Blokk | Canvas / fül | Megjelenítés | Mezők | Látható rekordok |', '|---|---|---|---:|---:|']
    for s in plan['sections']:
        lines.append('| ' + ' | '.join(map(cell, [s['block'], s['canvas'] + (' / ' + s['tab'] if s['tab'] else ''), s['mode'], len(s['items']), s['records']])) + ' |')
    if plan.get('framework_canvases'):
        lines += ['', '### Natív vezérlővel kiváltott segédképernyők', '',
                  'Ezek a canvasok kizárólag kihagyható keretrendszer-blokkok mezőit tartalmazzák. '
                  'A feliratok és grafikai elemek az elemzésben maradnak, külön képernyőt nem hoznak létre.', '',
                  '| Canvas | Blokkok | Indok |', '|---|---|---|']
        lines += ['| ' + ' | '.join(map(cell, [c['canvas'], ', '.join(c['blocks']), c['reason']])) + ' |'
                  for c in plan['framework_canvases']]
    if plan['windows']:
        lines += ['', '## Ablakok és párbeszédablakok', '',
                  'Egy Forms Window egy megjelenítési egység: az összes hozzárendelt Content/Tab/Stacked canvas ugyanabban az ablakban marad. '
                  'A Dialog stílus és a Modal érték külön tulajdonság; a nem modális másodlagos ablak is p-dialog.', '',
                  '| Window | Szerep | Modal / forrás | Canvasok | Kezdő Content canvas | Döntés alapja |', '|---|---|---|---|---|---|']
        roles = {'main': 'Főoldal', 'dialog': 'p-dialog', 'unused': 'Nincs generált tartalom'}
        lines += ['| ' + ' | '.join(map(cell, [w['name'], roles[w['role']], str(w['modal']).lower() + ' / ' + w['property_sources']['modal'],
                                               ', '.join(w['canvases']), w['initial_canvas'], w['reason']])) + ' |' for w in plan['windows']]
        lines += ['', 'A `default` generátor-fallback, nem bizonyított Oracle-alapérték. A felugró ablakok kezdetben zártak; a startup triggereket a váz nem futtatja. '
                      'Több lehetséges Document főablaknál a FirstNavigationBlock, illetve a screen_primary_window beállítás dönt; nincs modulnévhez kötött kivétel.']
    if plan['window_controls']['canvases']:
        lines += ['', '### Bekötési pontok', '',
                  "- `canvasVisible['<CANVAS>'] = true / false` megjeleníti vagy elrejti a canvast; Content canvasnál "
                  "`activeContentCanvas['<ABLAK>'] = '<CANVAS>'` teszi az ablak aktív tartalmi oldalává. A stacked régiók egymásra "
                  'helyezésének és kölcsönös elrejtésének szabályai kézi bekötést igényelnek.',
                  '- Az ablak/canvas állapota a komponens sima mezője (4.26). Mentés/visszavonás nem történik automatikusan.']
        if plan['window_controls']['windows']:
            lines += ["- `windowVisible['<ABLAK>'] = true / false` vezérli az ablakot (a dialógus `[(visible)]` kötéssel).",
                      '- A dialog X gombja a CloseAllowed értékét követi (hiányában true); Esc és háttérkattintás nem zárja be. A WHEN-WINDOW-CLOSED és a mentési/Cancel szabályok bekötése fejlesztői feladat.']
    if plan['window_references']:
        lines += ['', '### Ablak- és canvasműveletek a forrásban', '',
                  'Statikus hivatkozások, automatikus gombbekötés nélkül. A feltételek, eseménysorrend, GO_BLOCK/GO_ITEM fókusz- és rekordhatásai külön ellenőrzést igényelnek.', '',
                  '| Tulajdonos / esemény | Hívás | Window / canvas | Feloldás | Forrás / sor |', '|---|---|---|---|---|']
        lines += ['| ' + ' | '.join(map(cell, [r['owner'] + ' / ' + r['event'], r['call'], r['window'] + ' / ' + r['canvas'],
                                               r['status'], r['source_file'] + ':' + str(r['line'])])) + ' |' for r in plan['window_references']]
    lines += ['', '## Elrendezés és felülbírálások', '',
              'Sorillesztési tolerancia: ' + str(plan['layout_settings']['row_tolerance']) + ' × a kisebb mezőmagasság; '
                                                                                             'a valódi sorok elkülönítéséhez függőleges és vízszintes átfedésvizsgálat is történik. '
                                                                                             'Belső térközök megőrzése: ' + str(plan['layout_settings']['preserve_gaps']).lower() + '. '
                                                                                                                                                                                    'Kerethez a teljes mezőnek bele kell férnie; a beágyazott keretek megmaradnak.', '',
              'Szabálysablon: `analysis/screen-overrides.template.json`. Töltsd ki a szükséges mező `reason` és `set` részét, '
              'majd add át `--screen-overrides` kapcsolóval vagy a webes feltöltőn. A forrásváltozás miatt elavult aktív szabály hibával megállítja a generálást.']
    if plan['overrides']:
        lines += ['', '| Mező | Jóváhagyott módosítás | Indok |', '|---|---|---|']
        lines += ['| ' + ' | '.join(map(cell, [o['owner'], json.dumps(o['set'], ensure_ascii=False), o['reason']])) + ' |' for o in plan['overrides']]
        lines += ['', 'Az alkalmazott fájl másolata: `analysis/screen-overrides.applied.json`. Típust felülbíráló szabálynál `type_source: "override"`.']
    if plan['lookup_groups']:
        lines += ['', '### Összetartozó LOV-mezők', '', '| Kódmező | LOV | Köztes gombok | Visszaírt mezők ugyanabban a sorban |', '|---|---|---|---|']
        lines += ['| ' + ' | '.join(map(cell, [g['source'], g['lov'], ', '.join(g['buttons']), ', '.join(g['returns'])])) + ' |' for g in plan['lookup_groups']]
        lines += ['', 'Az összetartozás az explicit LOV ReturnItem és az olvasási sor alapján igazolt. '
                      'Az a köztes gomb, amelynek triggere bizonyítottan csak a kódmező listáját nyitja, beolvad a mező saját lenyitó gombjába (lásd lent).']
    if plan.get('folded_buttons'):
        lines += ['', '### Beolvasztott listanyitó gombok', '',
                  'A gomb WHEN-BUTTON-PRESSED triggere kizárólag `go_item` + `LIST_VALUES` a cél mezőre, a cél mező pedig saját '
                  'lenyitó vezérlővel jelenik meg (autocomplete/dropdown/naptár). Külön gomb ezért nem készül; a mező a felszabaduló '
                  'oszlopokat kapja. Ellenőrzött felülbírálás (`widget: button`) esetén a gomb megmarad. Kikapcsolható: `screen_fold_list_buttons=false`.', '',
                  '| Gomb | Cél mező | Cél vezérlő | Félretett utasítások | Cél KEY-LISTVAL |', '|---|---|---|---|---|']
        lines += ['| ' + ' | '.join(map(cell, [f['owner'], f['target'], f['target_widget'], '; '.join(f['ignored']) or '—',
                                               'van — a mező saját nyitójára köthető' if f['target_key_listval'] else 'nincs'])) + ' |' for f in plan['folded_buttons']]
    if plan['inferred_types']:
        lines += ['', '## Kikövetkeztetett típusok', '',
                  'Az `ui-model.json` és a képernyőterv ugyanazt a vezérlőtípust, `type_source: "inferred"` jelölést és indoklást tartalmazza. '
                  'Az `item_type` és annak property-eredete az XML szerinti érték marad. A következtetés előtti modell: `analysis/ui-model-strict.json`.', '',
                  '| Mező | XML ItemType | Generált widget | Következtetés alapja |', '|---|---|---|---|']
        lines += ['| ' + ' | '.join(cell(i[k]) for k in ('owner', 'item_type', 'widget', 'reason')) + ' |' for i in plan['inferred_types']]
    visible_owners = {i['owner'] for s in plan['sections'] for i in s['items']}
    validation_rows = [v for v in plan['validation_audit'] if v['owner'] in visible_owners]
    if validation_rows:
        lines += ['', '## Validációk és formátumok', '',
                  'A szabályokat a FormBlock tulajdonságai adják (validator, minLenght/maxLenght, min/max, regexRule); saját validátort '
                  'a képernyő nem tesz a kontrollokra (4.26), a többit a backend ellenőrzi. '
                  'Required checkboxnál a false is kitöltött érték; ezért ott nem használjuk a `validator: true` kapcsolót, amelynek checkbox-szemantikája a privát komponensben nem ismert.', '',
                  '| Mező | Forrás property | Érték | Lefedettség | Megvalósítás / teendő |', '|---|---|---|---|---|']
        statuses = {'implemented': 'Megvalósítva', 'partial': 'Részleges', 'manual': 'Adapter szükséges'}
        lines += ['| ' + ' | '.join(map(cell, [r['owner'], r['property'], r['value'], statuses[r['status']], r['target']])) + ' |' for r in validation_rows]
        lines += ['', 'A rejtett mezők szabályai is megmaradnak az `analysis/screen-plan.json` `validation_audit` listájában.']
    if plan['graphics']:
        lines += ['', '## Boilerplate és grafikai elemek', '',
                  '| Elem | Canvas / fül | Típus | Felirat | Állapot | Cél / indok |', '|---|---|---|---|---|---|']
        statuses = {'rendered': 'Megjelenítve', 'deduplicated': 'Címként már szerepel', 'unused': 'Fel nem használt'}
        lines += ['| ' + ' | '.join(map(cell, [g['name'], g['canvas'] + (' / ' + g['tab'] if g['tab'] else ''), g['kind'],
                                               g['label'], statuses[g['status']], (g['target'] + '; ' if g['target'] else '') + g['reason']])) + ' |' for g in plan['graphics']]
    lines += ['', '## Adatforrások', '']
    lines += ['- `' + b['name'] + '`: `' + (b['source'] or 'vezérlőblokk') + '`.' for b in plan['blocks'] if any(s['block'] == b['name'] for s in plan['sections'])]
    for b in plan['blocks']:
        for key, title in [('where', 'WhereClause'), ('order_by', 'OrderByClause')]:
            if b[key]:
                lines += ['', '**' + cell(b['name']) + ' — ' + title + '** (a backend adapterben őrizd meg):', '', *sql_block(b[key])]
    lines += ['', '## Blokk művelet-engedélyek', '',
              'A forrásbeli engedélyek következnek; nem engedélyeznek automatikus CRUD-műveleteket. '
              'A `default` jelzés a UI-modell fallbackje, nem igazolt Oracle-property; az adapterben ellenőrizendő.', '',
              '| Blokk | InsertAllowed | UpdateAllowed | DeleteAllowed | QueryAllowed | NavigationStyle |', '|---|---|---|---|---|---|']
    for b in plan['blocks']:
        cells = [b['name']] + [str(b['allowed_operations'][k]).lower() + ' (' + b['operation_sources'][k] + ')' for k in ('insert', 'update', 'delete', 'query')]
        cells.append(b['navigation_style'] or 'Nincs megadva')
        lines.append('| ' + ' | '.join(map(cell, cells)) + ' |')
    lines += ['', 'A blokk NavigationStyle tulajdonságához nincs FormBlock.Structure megfelelő; a rekordok közötti navigációt a host adapterben kell megvalósítani.']
    if plan['property_gaps']:
        lines += ['', '## FormBlockból közvetlenül nem kifejezhető property-k', '',
                  'Mezőnként a viselkedést érintő CaseRestriction és navigációs property-k; az explicit false/Mixed értékek is szerepelnek. '
                  'A validációs táblázat jelzi, hol van alternatív megvalósítás. A navigációs tulajdonságokat a host kezeli, automatikus fókuszváltás nincs. '
                  'Az összes további XML-property és eredete az `ui-model.json` mezőnkénti `properties` objektumában található.', '',
                  '| Mező | Elhelyezés | Property-k és forrásértékek |', '|---|---|---|']
        grouped = {}
        for row in plan['property_gaps']:
            grouped.setdefault((row['owner'], row['location']), []).append(row['property'] + '=' + str(row['value']))
        lines += ['| ' + ' | '.join(map(cell, [owner, 'Rejtett/technikai' if location == 'hidden' else 'Képernyő', '; '.join(values)])) + ' |' for (owner, location), values in grouped.items()]
    if plan['relations']:
        lines += ['', '## Master-detail kapcsolatok', '', '| Kapcsolat | Master | Detail | Join |', '|---|---|---|---|']
        lines += ['| ' + ' | '.join(cell(r[k]) for k in ['name', 'master', 'detail', 'join']) + ' |' for r in plan['relations']]
    if plan['actions']:
        lines += ['', '## Gombok és eredeti hívások', '',
                  'A gomb metódusa (`on<Gomb>Click()`) a felismert lépések kérését küldi (executeQuery: a blokk keresése, commit: '
                  '`save()`, showWindow/showView: láthatóság), vagy a gomb backend-akcióját hívja; a többi gombnál TODO és az '
                  'eredeti Forms-kód. A keretrendszer-diszpécserhívások (katalógus) nem teendők.', '',
                  '| ownId | Felirat | Felismert lépések | Saját hívások (kézi) |', '|---|---|---|---|']
        for a in plan['actions']:
            steps = ', '.join(s['op'] + ('(' + next(iter(v for k, v in s.items() if k != 'op'), '') + ')' if len(s) > 1 else '')
                              for s in a.get('steps') or [])
            own = ', '.join(a.get('own_calls') or [])
            lines.append('| ' + ' | '.join(map(cell, [a['owner'], a['label'], steps or '—',
                                                      own if not a.get('steps') else '—'])) + ' |')
    if plan['lookups']:
        lines += ['', '## LOV bekötés', '', 'A LOV-os mező autocomplete (`dropdown`, üres `suggestions`), a LOV-végpontnak saját metódusa van '
                  '(`lov<Név>`). A javaslatok betöltése (`completeMethod`) és a ReturnItem mezők kitöltése fejlesztői feladat. '
                  'Az alábbi SQL és paraméterek dokumentáció; nem böngészőből végrehajtandó kód.', '']
        for l in plan['lookups']:
            lines += ['### ' + l['name'] + ' — ' + l['owner'], '', 'Rekordcsoport: `' + l.get('record_group', '') + '`. Paraméterek: ' + ', '.join('`' + p + '`' for p in l['parameters']), '',
                      '| Oszlop | Felirat | ReturnItem |', '|---|---|---|']
            lines += ['| ' + ' | '.join(cell(c[k]) for k in ('column', 'title', 'return_item')) + ' |' for c in l['columns']]
            if l['query']:
                lines += ['', *sql_block(l['query'])]
    if plan.get('spacers'):
        lines += ['', '## Térköz-mezők', '',
                  'A keretrendszer-katalógus `spacer_items` mintáira (pl. `L_URES_*`) illeszkedő mezők üres `'
                  + config['screen_spacer_type'] + '` elemként, üres labelText-tel kerültek a rácsba, `formControlName`, érték és '
                  'validáció nélkül; csak a szélességüknek megfelelő helyet tartják. Ha a forrásban viselkedésük is van '
                  '(trigger, LOV, adatbázis-oszlop), SPACER_BEHAVIOUR jelzés mutatja; ellenőrzött widget-felülbírálással '
                  'mezőként visszaállítható. Típus: `screen_spacer_type`.', '',
                  '| Mező | Minta | col | Felirat |', '|---|---|---:|---|']
        lines += ['| ' + ' | '.join(map(cell, [x['owner'], x['pattern'], str(x['col']), x['label'] or '—'])) + ' |' for x in plan['spacers']]
    lengths = plan.get('field_lengths') or {}
    if lengths.get('applied') or lengths.get('unknown'):
        lines += ['', '## Mezőhosszak (segítő JSON)', '',
                  'A megadott min/max felülírja a Forms-hosszt: FormBlock `minLenght`/`maxLenght`. '
                  'Kitölthető minta: `../../analysis/field-lengths.template.json`.', '']
        if lengths.get('applied'):
            lines += ['| Mező | formControlName | Szabály | min | max |', '|---|---|---|---:|---:|']
            lines += ['| ' + ' | '.join(map(cell, [a['owner'], a['key'], a['rule'], '—' if a['min'] is None else str(a['min']),
                                                  '—' if a['max'] is None else str(a['max'])])) + ' |' for a in lengths['applied']]
        if lengths.get('unknown'):
            lines += ['', 'Ebben a formban nem talált mező (elírás vagy másik formhoz tartozik): ' + ', '.join('`' + k + '`' for k in lengths['unknown']) + '.']
    notices = [n for n in plan['notices'] if n['code'] != 'SUGGESTED_WIDGET']
    if notices:
        lines += ['', '## Ellenőrizendő megjelenítési döntések', '']
        lines += ['- `' + cell(n['owner']) + '` — ' + cell(n['detail']) for n in notices]
    if plan['semantic_review']:
        lines += ['', '## Adatformátumok és öröklés', '']
        lines += ['- `' + n['owner'] + '` — ' + n['detail'] for n in plan['semantic_review']]
    if plan.get('framework_blocks'):
        lines += ['', '## Keretrendszer-blokkok', '',
                  'A katalógusban (`' + plan['framework_blocks'][0]['catalog'] + '`) szereplő, adatforrás nélküli blokkok nem kerülnek a képernyőre; '
                                                                                 'a szerepüket a natív vezérlők és a host veszik át. Saját katalógus: `framework_catalog`.', '',
                  '| Blokk | Mezők | Miért maradt ki |', '|---|---:|---|']
        lines += ['| ' + ' | '.join(map(cell, [b['block'], b['items'], b['reason']])) + ' |' for b in plan['framework_blocks']]
    audit = plan.get('trigger_audit')
    if audit and audit['total']:
        c = audit['counts']
        lines += ['', '## Triggerek besorolása', '',
                  f"{audit['total']} trigger a megjelenített blokkokban: {c['framework']} csak keretrendszer-hívás (nem teendő), "
                  f"{c['mixed']} vegyes, {c['own']} saját kód, {c['empty']} üres. A vegyes és saját kódú triggerek a tényleges migrációs munka."]
        if audit['key_triggers']:
            lines += ['', '### Billentyűkezelés', '',
                      'Saját logikát tartalmazó KEY-* triggerek. A FormBlock `hotkeyShow` / `hotkeyBlock` beállításaival köthetők; '
                      'a tisztán keretrendszeri navigációs billentyűk nincsenek a listán.', '',
                      '| Tulajdonos | Billentyű | Saját hívások |', '|---|---|---|']
            lines += ['| ' + ' | '.join(map(cell, [k['owner'], k['event'], ', '.join(k['own_calls']) or '—'])) + ' |' for k in audit['key_triggers']]
    if plan.get('sql_portability'):
        lines += ['', '## Oracle-specifikus SQL', '',
                  'Csak akkor teendő, ha az adatbázis is cserélődik: ezek a szerkezetek más adatbázison átírást igényelnek.', '',
                  '| Tulajdonos | Hol | Szerkezet → megfelelő |', '|---|---|---|']
        lines += ['| ' + ' | '.join(map(cell, [s['owner'], s['kind'], '; '.join(x['construct'] + ' → ' + x['alternative'] for x in s['constructs'])])) + ' |'
                  for s in plan['sql_portability']]
    if plan['hidden_items']:
        lines += ['', '## Felületen nem megjelenített mezők', '', 'A mezők az XML-ben és az elemzésben megmaradnak. A kezdeti/runtime értékeiket a backend- és eseményadapterben ellenőrizd.', '']
        grouped = Counter((i['owner'].split('.')[0], i['reason']) for i in plan['hidden_items'])
        lines += ['- `' + b + '`: ' + str(count) + ' mező — ' + reason for (b, reason), count in grouped.items()]
    if plan['display_text_changes']:
        lines += ['', '## Megjelenített szövegek javítása', '', 'Csak feliratok és súgók: visszafordítható kódolásjavítás és sortörések normalizálása. Az XML, SQL és adatértékek változatlanok. Kikapcsolható: `screen_repair_display_text=false`.', '', '| Forrás | Megjelenítés |', '|---|---|']
        lines += ['| ' + cell(x['source']) + ' | ' + cell(x['display']) + ' |' for x in plan['display_text_changes']]
    lines += screen_api.notes(plan.get('backend_calls'))
    lines += form_calls.notes(plan.get('navigations') or {}, plan.get('manual_navigations') or {})
    lines += ['', '## Részletes források', '', '- `../../analysis/screen-plan.json`: elrendezési döntések, rejtett mezők, LOV-k és figyelmeztetések.',
              '- `../../analysis/screen-preview.html`: a generált képernyők előnézete (fő képernyő, párbeszédablakok, fülek), böngészőben megnyitható.',
              '- `../../analysis/field-lengths.template.json`: kitölthető mezőhossz-minta (formControlName -> min/max).',
              '- `../../analysis/layout-preview.html`: az eredeti canvas és a generált rács egymás mellett, böngészőben összevethető.',
              '- `../../analysis/discovery/form-explorer.html`: kereshető offline modultérkép, eredeti trigger/program unit forrásokkal.',
              '- `../../analysis/ui-model.json`: a generált képernyő vezérlőtípusai, típuseredet és property-audit.',
              '- `../../analysis/ui-model-strict.json`: a screen következtetése előtti, szigorú besorolás. `../../analysis/inheritance.json`: öröklési audit.',
              '- `../../analysis/issues-summary.json`: csoportosított problémák. A strict mód hibái továbbra is megmaradnak; a képernyőváz ezeket nem minősíti megoldottnak.', '']
    return '\n'.join(lines)


def append_screen_report(plan, output):
    message = '# Egyfájlos Angular képernyőváz\n\nElsőként: `frontend/' + plan['module']['key'] + '/MIGRATION_NOTES.md`.\n\n'
    message += 'A képernyő a szerkezetet követi, a pozíciókat relatív oszlopokra alakítja. Az események és adatok bekötése fejlesztői feladat. A generált backend műveletei ellenőrzésig tiltottak.\n'
    write(output / 'INTEGRATION.md', message)
    path = output / 'migration-report.md'
    original = path.read_text(encoding='utf-8')
    write(path, message + '\n' + str(len(plan['sections'])) + ' régió; ' + str(len(plan['hidden_items'])) + ' technikai/rejtett mező a megjelenítésen kívül.\n\n'
                                                                                                            'A következő rész a szigorú generátor auditját is tartalmazza. Az ismeretlen property-k továbbra is ellenőrizendők; '
                                                                                                            'a képernyőváz ettől független, prezentációs célú kimenet az effektív XML-ből és az UI-elemzésből.\n\n---\n\n' + original)

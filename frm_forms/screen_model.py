"""Small, presentation-only screen plan. Source semantics remain in the UI audit.

Screen-specific types are recorded in the generated UI model with their evidence.
The source XML and the strict classification remain available separately.
"""
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import copy
import re

from .common import MigrationError, name, unique_names
from .xmlmodel import get, props, tag
from .screen_validation import validation_coverage
from .screen_layout import contains, frame_hierarchy, lookup_groups, spans
from . import screen_overrides
from .screen_windows import build_windows
from . import framework


def number(value, default=0):
    try:
        result = Decimal(str(value))
        return float(result) if result.is_finite() and abs(result) < 1e10 else default
    except (InvalidOperation, TypeError, ValueError):
        return default


def truth(value, default=True):
    if value is None or value == '':
        return default
    return str(value).lower() in {'true', 'yes', '1'}


def display_text(value, repair, repairs):
    """Only repair reversible display-text mojibake. Never touch SQL or values."""
    original = str(value or '')
    value = re.sub(r'&#(?:10|13|9|x0*[aAdD9]);', ' ', original)
    if repair:
        score = lambda s: sum(s.count(c) for c in ('Ă', 'Ĺ', 'Ã', 'Â', 'â', '\u0081', '\u008d'))
        for _ in range(2):
            candidates = []
            for encoding in ('cp1250', 'cp1252', 'latin1'):
                try:
                    candidate = value.encode(encoding).decode('utf-8')
                    if candidate.encode('utf-8').decode(encoding) == value and score(candidate) < score(value):
                        candidates.append(candidate)
                except (UnicodeError, LookupError):
                    pass
            if not candidates:
                break
            value = min(candidates, key=score)
    value = re.sub(r'\s+', ' ', value).strip()
    if original != value:
        repairs.append({'source': original, 'display': value})
    return value


LINE_ESCAPES = re.compile(r'&#(?:10|13|9|x0*[aAdD9]);')
GO_ITEM = re.compile(r"go_item\s*\(\s*'([^']+)'\s*\)", re.I)
OPEN_LIST = re.compile(r"do_key\s*\(\s*'list_values'\s*\)|list_values(?:\s*\(\s*(?:no_)?restrict\s*\))?", re.I)
# Headstart records the mouse position in a global before opening a list. It
# is framework bookkeeping, set aside and listed, never silently dropped.
GLOBAL_COPY = re.compile(r"copy\s*\(\s*'[^']*'\s*,\s*'global\.[a-z0-9_$#]+'\s*\)", re.I)
# A prompt made only of range punctuation sits between two fields in Forms.
SEPARATOR = re.compile(r'[-\u2013\u2014/~:.]{1,3}')
# Widgets whose own control already carries a list opener.
OPENER_WIDGETS = {'autocomplete', 'select', 'multiselect', 'date', 'datetime'}


def list_opener(body):
    """Return the go_item target when a trigger does nothing but open that item's list.

    Accepted: go_item('X') followed by do_key('LIST_VALUES') or LIST_VALUES,
    plus NULL and a literal copied into a GLOBAL. Anything else - a condition,
    an assignment, another call, an exception handler - means the button does
    more than open a list, and the result is None.
    """
    code = LINE_ESCAPES.sub('\n', str(body or ''))
    code = re.sub(r'/\*.*?\*/', ' ', code, flags=re.S)
    code = re.sub(r'--[^\n]*', ' ', code)
    target, opened, ignored, key_dispatch = None, False, [], False
    for raw in code.split(';'):
        statement = re.sub(r'\s+', ' ', raw).strip()
        statement = re.sub(r'^(?:begin\s+)+', '', statement, flags=re.I).strip()
        if not statement or statement.lower() in {'begin', 'end', 'null'}:
            continue
        match = GO_ITEM.fullmatch(statement)
        if match and target is None and not opened:
            target = match.group(1).strip()
        elif OPEN_LIST.fullmatch(statement) and target is not None:
            opened = True
            key_dispatch = key_dispatch or statement.lower().startswith('do_key')
        elif GLOBAL_COPY.fullmatch(statement):
            ignored.append(statement)
        else:
            return None
    return {'target': target, 'ignored': ignored, 'key_dispatch': key_dispatch} if target and opened else None


def fold_list_buttons(groups, all_items, openers, active_overrides, notices, context=None, catalog=None):
    """Drop buttons proven to be list openers for a field that renders its own.

    Runs before layout, so the target field takes over the freed columns. A
    reviewed override on the button always wins, and every kept candidate is
    reported with the reason it stayed.
    """
    by_owner = {i['owner'].upper(): i for i in all_items}
    group_of = {i['owner']: key for key, members in groups.items() for i in members}
    folded = []
    for owner, opener in sorted(openers.items()):
        button = next((i for i in all_items if i['owner'] == owner), None)
        if button is None or button['widget'] != 'button' or owner in active_overrides:
            continue
        reference = opener['target'].upper()
        if '.' not in reference:
            reference = button['block'].upper() + '.' + reference
        target = by_owner.get(reference)
        reason = ''
        if target and opener.get('key_dispatch') and context is not None:
            from .forms_keys import overrides
            keys = overrides(context, 'LIST_VALUES', target['block'], target['name'])
            # Forms runs the most specific KEY-LISTVAL; whichever level runs, each one only opens
            # the catalogued calendar, which the date field's own picker replaces.
            native_calendar = target['date_picker'] and all(framework.date_picker(t['source'], catalog) for t in keys)
            if keys and not native_calendar:
                reason = ('A DO_KEY saját KEY-LISTVAL triggert indít (' + ', '.join(t['id'] for t in keys)
                          + '); a gomb logikáját meg kell őrizni.')
            elif any(get(u, 'Name').upper() == 'DO_KEY' for u in context['program_units']):
                reason = 'A DO_KEY helyi programegység; a gomb saját logikáját meg kell őrizni.'
        if target is None:
            reason = 'A go_item célja nem jelenik meg a felületen: ' + opener['target'] + '; a gomb megmarad.'
        elif not reason and (group_of[owner][3] != 'form' or group_of[target['owner']][3] != 'form'):
            reason = 'Táblázatos régióban a listanyitó gomb megmarad.'
        elif not reason and target['widget'] not in OPENER_WIDGETS and not target['lov']:
            reason = 'A cél mezőnek (' + target['owner'] + ', ' + target['widget'] + ') nincs saját lenyitó vezérlője; a gomb megmarad.'
        if reason:
            notices.append({'code': 'LIST_BUTTON_KEPT', 'owner': owner, 'detail': reason})
            continue
        members = groups[group_of[owner]]
        members[:] = [i for i in members if i is not button]
        all_items[:] = [i for i in all_items if i is not button]
        folded.append({'owner': owner, 'target': target['owner'], 'target_widget': target['widget'],
                       'lov': target['lov'], 'ignored': opener['ignored'],
                       'target_key_listval': 'KEY-LISTVAL' in target['triggers'],
                       'basis': 'WHEN-BUTTON-PRESSED: go_item + LIST_VALUES a(z) ' + target['owner']
                                + ' mezőre; a mező saját lenyitó gombja ugyanezt a listát nyitja.'})
    return folded


def place_separators(members, notices):
    """Give a punctuation prompt its own column between two fields of one row.

    The field keeps an empty label so it lines up with its neighbour, and the
    punctuation becomes a FormBlock label element in front of it. Without a
    field before it in the same row the punctuation stays an ordinary label.
    """
    for index, item in enumerate(members):
        if not item.get('separator'):
            continue
        previous = members[index - 1] if index else None
        if previous is None or previous['row'] != item['row'] or previous['widget'] == 'button':
            item['label'], item['separator'] = item['separator'], ''
            continue
        if item['col_before'] >= 1:
            item['separator_col'] = 1; item['separator_before'] = item['col_before'] - 1; item['col_before'] = 0
        elif previous['col'] > 1:
            previous['col'] -= 1; item['separator_col'] = 1
        elif item['col'] > 1:
            item['col'] -= 1; item['separator_col'] = 1
        else:
            item['label'], item['separator'] = item['separator'], ''
            notices.append({'code': 'SEPARATOR_NO_ROOM', 'owner': item['owner'],
                            'detail': 'Nincs hely az elválasztónak a sorban; feliratként marad.'})


def trigger_audit(form, catalog, skipped_blocks):
    """Sort every trigger into framework plumbing, mixed, own code or empty."""
    rows = []
    def visit(node, owner):
        for child in node:
            kind = tag(child)
            if kind == 'block':
                name = get(props(child), 'Name')
                if name.upper() not in skipped_blocks: visit(child, name)
            elif kind == 'item':
                visit(child, owner + '.' + get(props(child), 'Name'))
            elif kind == 'trigger':
                cp = props(child); event = get(cp, 'Name').upper()
                category, own = framework.classify(get(cp, 'TriggerText'), catalog)
                rows.append({'owner': owner or '(form)', 'event': event, 'category': category, 'own_calls': own})
    visit(form, '')
    counts = Counter(r['category'] for r in rows)
    keys = [r for r in rows if r['event'].startswith('KEY-') and r['category'] in {'own', 'mixed'} and r['own_calls']]
    return {'counts': {k: counts.get(k, 0) for k in ('framework', 'mixed', 'own', 'empty')}, 'total': len(rows),
            'key_triggers': keys, 'catalog': catalog['source']}


FALSE_WORDS = {'false', 'no', 'n', '0'}
LENGTH_WIDGETS = {'text', 'textarea', 'password', 'autocomplete'}


def apply_field_lengths(sections, lengths, notices):
    """Reviewed text lengths from the helper JSON: "BLOCK.formControlName" first, then "formControlName".

    An explicit min/max replaces the Forms MaximumLength/FixedLength of that control.
    Unknown names are reported, not refused: one file may serve a whole batch.
    """
    fields = (lengths or {}).get('fields', {})
    used, applied = set(), []
    for section in sections:
        for item in section['items']:
            if item.get('spacer') or item['widget'] in {'button', 'image', 'tree', 'unsupported'}:
                continue
            qualified = section['block'] + '.' + item['key']
            rule = qualified if qualified in fields else item['key'] if item['key'] in fields else None
            if rule is None:
                continue
            used.add(rule)
            if item['widget'] not in LENGTH_WIDGETS or section['mode'] != 'form':
                notices.append({'code': 'FIELD_LENGTH_IGNORED', 'owner': item['owner'],
                                'detail': 'A mezőhossz-JSON ' + rule + ' szabálya nem szöveges beviteli mezőre vonatkozik (' + item['widget'] + ', ' + section['mode'] + ').'})
                continue
            entry = fields[rule]
            validation = dict(item['validation'], fixed_length=None)
            if entry.get('max') is not None:
                validation['maximum_length'] = entry['max']
            if entry.get('min') is not None:
                validation['minimum_length'] = entry['min']
            item['validation'] = validation
            applied.append({'owner': item['owner'], 'key': item['key'], 'rule': rule, 'min': entry.get('min'), 'max': entry.get('max')})
    unknown = sorted(set(fields) - used)
    if unknown:
        notices.append({'code': 'FIELD_LENGTH_UNKNOWN', 'owner': '@FORM',
                        'detail': 'A mezőhossz-JSON ezekre a formControlName-ekre nem talált mezőt ebben a formban: ' + ', '.join(unknown) + '.'})
    return {'applied': applied, 'unknown': unknown}


def spacer_conflicts(original, p, trigger_names, block):
    """Why a catalogued spacer name (L_URES_*) must still stay a real field.

    Only presentation may be dropped: a column binding, code, list, required
    flag, value or calculation is behaviour, so such an item keeps its control.
    """
    reasons = []
    kind = re.sub(r'[\s_-]', '', str(original['item_type'] or '')).lower()
    if kind not in {'', 'textitem', 'displayitem'}:
        reasons.append('ItemType=' + str(original['item_type']))
    if block['data_source']['name'] and str(get(p, 'DatabaseItem')).strip().lower() not in FALSE_WORDS:
        reasons.append('adatbázis-mező (DatabaseItem nem false, a blokknak van adatforrása)')
    if trigger_names:
        reasons.append('triggere van: ' + ', '.join(sorted(trigger_names)))
    if get(p, 'LOVName'):
        reasons.append('LOV: ' + get(p, 'LOVName'))
    if original['validation'].get('required'):
        reasons.append('Required=true')
    if original['initial_value'] not in (None, ''):
        reasons.append('kezdőértéke van: ' + str(original['initial_value']))
    for prop in ('CalculationMode', 'CopyValueFromItem', 'SynchronizeWithItem'):
        value = str(get(p, prop) or '').strip()
        if value and value.lower() != 'none':
            reasons.append(prop + '=' + value)
    return reasons


def build_screen(resolution, ui, module, config):
    form = next(e for e in resolution.root.iter() if tag(e) == 'formmodule')
    repaired = []
    text = lambda s: display_text(s, config['screen_repair_display_text'], repaired)
    notices, hidden, graphics, actions, lookups = [], [], [], [], {}
    inferred_types, property_gaps = [], []
    openers = {}
    catalog = framework.load(config)
    from .forms_keys import screen_context
    key_context = screen_context(form)
    framework_blocks = []
    pform = props(form)
    title = text(get(pform, 'Title') or get(pform, 'Name'))
    canvases = {get(props(e), 'Name'): e for e in form.iter() if tag(e) == 'canvas'}
    if len(canvases) != sum(tag(e) == 'canvas' for e in form.iter()):
        raise MigrationError('SCREEN_CANVAS_NAME: ismétlődő Canvas.Name.')
    if len({b['key'] for b in ui['blocks']}) != len(ui['blocks']):
        raise MigrationError('SCREEN_IDENTIFIER_COLLISION: azonos blokkazonosítók.')
    if any(len({i['key'] for i in b['items']}) != len(b['items']) for b in ui['blocks']):
        raise MigrationError('SCREEN_IDENTIFIER_COLLISION: azonos mezőazonosítók.')
    source_blocks = {get(props(e), 'Name'): e for e in form if tag(e) == 'block'}
    source_items = {bn + '.' + get(props(i), 'Name'): i for bn, b in source_blocks.items() for i in b if tag(i) == 'item'}
    overrides = config['screen_overrides']
    active_overrides, fingerprints = screen_overrides.prepare(overrides, get(pform, 'Name'), source_items, source_blocks)
    override_audit = []
    known_owners = set(source_items)
    canvas_names = set(canvases)
    groups = defaultdict(list)
    all_items = []
    blocks = []
    for block in ui['blocks']:
        role = catalog['blocks'].get(block['name'].upper())
        own_triggers = [t for t in source_blocks[block['name']].iter() if tag(t) == 'trigger'
                        and framework.classify(get(props(t), 'TriggerText'), catalog)[0] != 'framework'
                        and not framework.explicit_noop(get(props(t), 'TriggerText'))]
        reviewed = any(owner.startswith(block['name'] + '.') for owner in active_overrides)
        if role and not block['data_source']['name'] and not reviewed and framework.calendar_block(
                block['name'], [i['name'] for i in block['items']], catalog):
            # Its own cell and button triggers only run the Forms calendar: never a window in Angular.
            framework_blocks.append({'block': block['name'], 'items': len(block['items']),
                                     'reason': role + ' (napcellás Headstart naptár, a saját triggereivel együtt)',
                                     'catalog': catalog['source']})
            continue
        if role and not block['data_source']['name'] and not own_triggers and not reviewed:
            framework_blocks.append({'block': block['name'], 'items': len(block['items']), 'reason': role,
                                     'catalog': catalog['source']})
            continue
        if role:
            notices.append({'code': 'FRAMEWORK_BLOCK_KEPT', 'owner': block['name'],
                            'detail': 'A katalógusban szerepel, de adatforrása, saját/ismeretlen triggerlogikája vagy ellenőrzött mező-felülbírálása van; megjelenik.'})
        bp = props(source_blocks[block['name']])
        count = max(1, int(number(get(bp, 'NumberOfRecordsDisplayed', 'RecordsDisplayCount', default='1'), 1)))
        blocks.append({'name': block['name'], 'key': block['key'], 'source': block['data_source']['name'], 'records': count,
                       'where': get(bp, 'WhereClause'), 'order_by': get(bp, 'OrderByClause'),
                       'navigation_style': get(bp, 'NavigationStyle'),
                       'allowed_operations': copy.deepcopy(block['allowed_operations']),
                       'operation_sources': {k: block['properties'].get(prop.lower(), {}).get('source', 'default')
                                             for k, prop in [('insert', 'InsertAllowed'), ('update', 'UpdateAllowed'), ('delete', 'DeleteAllowed'), ('query', 'QueryAllowed')]}})
        for index, original in enumerate(block['items']):
            owner = original['owner']; node = source_items[owner]; p = props(node)
            canvas = get(p, 'CanvasName'); tab = get(p, 'TabPageName')
            reason = None
            if not original['visible']:
                reason = 'Visible=false'
            elif canvas_names and not canvas:
                reason = 'Nincs CanvasName: technikai vagy futásidőben elhelyezett mező.'
            elif canvas and canvas not in canvas_names:
                reason = 'Ismeretlen canvas: ' + canvas
            elif (owner not in active_overrides and not get(p, 'Prompt') and not get(p, 'Label')
                  and framework.empty_hint(get(p, 'Hint'), catalog)):
                reason = ('Nincs felirata, a súgója kitöltetlen sablon ("' + text(get(p, 'Hint'))
                          + '"): technikai mező. Ellenőrzött felülbírálással megjeleníthető.')
            trigger_names = {get(props(t), 'Name').upper() for t in node if tag(t) == 'trigger'}
            # Catalogued layout gaps (L_URES_*): an empty FormBlock element, not an input.
            spacer = '' if reason else framework.spacer_pattern(owner, catalog)
            if spacer and 'widget' in (active_overrides.get(owner) or {}).get('set', {}):
                spacer = ''  # A reviewed widget decision wins over the catalogue.
            if spacer:
                # The naming convention is the rule: an L_URES_* item is empty space.
                # Behaviour found on it (often inherited framework triggers) is only reported.
                conflicts = spacer_conflicts(original, p, trigger_names, block)
                if conflicts:
                    notices.append({'code': 'SPACER_BEHAVIOUR', 'owner': owner,
                                    'detail': 'Térközként jelenik meg (' + spacer + '), de a forrásban viselkedése is van: ' + '; '.join(conflicts)
                                              + '. Ha mégis mező, ellenőrzött felülbírálással (widget) visszaállítható.'})
            for prop in ('CaseRestriction', 'AutoSkip', 'NavigationStyle', 'NextNavigationItem', 'PreviousNavigationItem', 'KeyboardNavigable'):
                if spacer or prop.lower() not in p:
                    continue
                value = get(p, prop)
                if value == '':
                    continue
                detail = 'Nincs közvetlen FormBlock.Structure megfelelő; szükség esetén host navigációs adapter.'
                if prop == 'CaseRestriction':
                    detail = ('Mixed: nincs betűkorlátozás.' if str(value).lower() == 'mixed' else
                              'Betűkorlátozás: a validációs táblázat mutatja a lefedettséget; automatikus betűátalakítás nincs.')
                property_gaps.append({'owner': owner, 'property': prop, 'value': value,
                                      'location': 'hidden' if reason else 'screen', 'detail': detail})
            if reason:
                if owner in active_overrides:
                    raise MigrationError('SCREEN_OVERRIDE_NOT_RENDERED: ' + owner + ': ' + reason)
                hidden.append({'owner': owner, 'reason': reason, 'initial_value': original['initial_value']})
                continue
            widget = original['widget']
            evidence = [original['inference_reason']] if original['type_source'] == 'inferred' and original['inference_reason'] else []
            type_source, inference_reason = original['type_source'], original['inference_reason']
            checked = get(p, 'CheckedValue', 'CheckBoxCheckedValue')
            unchecked = get(p, 'UncheckedValue', 'CheckBoxUncheckedValue')
            if config['screen_infer_widgets'] and not spacer and (widget == 'text' or widget == 'unsupported' and not get(p, 'ItemType')):
                signals = []
                if 'WHEN-BUTTON-PRESSED' in trigger_names:
                    signals.append(('button', 'WHEN-BUTTON-PRESSED trigger'))
                if checked and unchecked and checked != unchecked:
                    signals.append(('checkbox', 'CheckedValue=' + repr(checked) + ', UncheckedValue=' + repr(unchecked)))
                if len(signals) == 1:
                    widget, basis = signals[0]
                    evidence.append(basis)
                    type_source = 'inferred'; inference_reason = basis + '; screen módú típuskövetkeztetés, ellenőrizendő.'
                    notices.append({'code': 'SUGGESTED_WIDGET', 'owner': owner, 'detail': inference_reason})
                elif len(signals) > 1:
                    widget = 'unsupported'
                    type_source = 'inferred'; inference_reason = 'Ellentmondó gomb/checkbox jelek; nincs találgatott vezérlő.'
                    evidence = [basis for _, basis in signals]
                    notices.append({'code': 'AMBIGUOUS_WIDGET', 'owner': owner, 'detail': inference_reason})
            display_count = int(number(get(p, 'ItemsDisplay'), 0))
            repeated = (display_count if display_count > 0 else count) > 1
            date_picker = False
            # Only an editable single-record field needs a picker; a grid shows text.
            if config['screen_infer_widgets'] and not repeated and not spacer:
                listval = next((get(props(t), 'TriggerText') for t in node if tag(t) == 'trigger'
                                and get(props(t), 'Name').upper() == 'KEY-LISTVAL'), '')
                if framework.date_picker(listval, catalog) and widget in {'text', 'autocomplete', 'date', 'datetime'}:
                    date_picker = True
                    mask = get(p, 'FormatMask').upper()
                    target = 'datetime' if any(x in mask for x in ('HH', 'MI', 'SS')) else 'date'
                    if widget != target:
                        basis = 'KEY-LISTVAL a katalógusban szereplő naptárhívást indítja'
                        widget = target; evidence.append(basis)
                        type_source = 'inferred'; inference_reason = basis + '; natív naptárvezérlő, ellenőrizendő.'
                        notices.append({'code': 'SUGGESTED_WIDGET', 'owner': owner, 'detail': inference_reason})
            prompt = text(get(p, 'Prompt'))
            separator = prompt if SEPARATOR.fullmatch(prompt or '') and not spacer else ''
            if spacer:
                # Empty space of the item's width: no caption at all, never the item name.
                label = ''
            else:
                label = '' if separator else text(get(p, 'Prompt') or get(p, 'Label') or get(p, 'Hint'))
                if not label and not separator:
                    label = '…' if widget == 'button' and number(get(p, 'Width')) <= 1 else get(p, 'Name')
            item = {'owner': owner, 'name': original['name'], 'key': original['key'], 'block': block['name'], 'block_key': block['key'],
                    'label': label, 'hint': text(get(p, 'Hint', 'ToolTip')), 'widget': widget, 'evidence': evidence,
                    'item_type': original['item_type'], 'type_source': type_source, 'inference_reason': inference_reason,
                    'x': number(get(p, 'XPosition')), 'y': number(get(p, 'YPosition'), index), 'width': number(get(p, 'Width'), 1),
                    'height': number(get(p, 'Height'), 1), 'order': index, 'col': 12, 'col_before': 0, 'col_after': 0,
                    'positioned': bool(get(p, 'XPosition') and get(p, 'YPosition')), 'layout_override': {}, 'group': '',
                    'records': display_count if display_count > 0 else count,
                    'enabled': original['enabled'], 'readonly': original['readonly'] or
                                                                (not original['allowed_operations']['insert'] and not original['allowed_operations']['update']),
                    'validation': copy.deepcopy(original['validation']), 'initial_value': original['initial_value'],
                    'representation': original['representation'], 'options': [], 'lov': get(p, 'LOVName'),
                    'checked': checked, 'unchecked': unchecked, 'triggers': sorted(trigger_names),
                    'separator': separator, 'separator_col': 0, 'date_picker': date_picker, 'spacer': spacer}
            if spacer:
                item.update(hint='', lov='')
            if date_picker:
                # The catalogued calendar replaces the list: no lookup, native picker.
                item['lov'] = ''
                if get(p, 'DataType').lower() in {'date', 'datetime', 'timestamp'}:
                    item['representation'] = 'date'
                else:
                    notices.append({'code': 'DATE_PICKER_TEXT_VALUE', 'owner': owner,
                                    'detail': 'A forrásmező ' + (get(p, 'DataType') or 'Char') + ' típusú: a naptár Date értéket ad, '
                                                                                                 'a szöveges formátumra alakítás a host adapter feladata.'})
            if widget == 'checkbox':
                item['representation'] = 'boolean'
                if not checked or not unchecked or checked == unchecked:
                    item['widget'] = 'unsupported'
                    notices.append({'code': 'CHECKBOX_MAPPING_REQUIRED', 'owner': owner, 'detail': 'Nincs egyértelmű checkbox értékpár.'})
            if widget in {'button', 'unsupported', 'image', 'tree'}:
                item['readonly'] = False
            for opt in original['options']:
                if opt['visible']:
                    item['options'].append({'label': text(ui['i18n'][opt['label_key']]), 'value': opt['value'], 'disabled': not opt['enabled']})
            applied = screen_overrides.apply(item, active_overrides.get(owner))
            if applied: override_audit.append(applied)
            widget = item['widget']
            if item['type_source'] == 'inferred':
                inferred_types.append({'owner': owner, 'item_type': original['item_type'], 'widget': widget, 'reason': item['inference_reason']})
            if widget in {'button', 'unsupported', 'image', 'tree'}: item['readonly'] = False
            if widget == 'display': item['readonly'] = True
            if widget == 'button':
                actions.append({'owner': owner, 'label': item['label'], 'evidence': evidence, 'triggers': sorted(trigger_names)})
                pressed = next((get(props(t), 'TriggerText') for t in node if tag(t) == 'trigger'
                                and get(props(t), 'Name').upper() == 'WHEN-BUTTON-PRESSED'), '')
                opener = list_opener(pressed)
                if opener: openers[owner] = opener
                recognised = framework.action_steps(pressed, catalog, key_context)
                actions[-1].update(steps=recognised['steps'] if recognised else None,
                                   framework_calls=recognised['framework'] if recognised else [],
                                   own_calls=framework.classify(pressed, catalog)[1])
            if item['lov']:
                lookups[owner] = {'name': item['lov'], 'owner': owner, 'return_items': [], 'columns': [], 'query': '', 'parameters': []}
            groups[(canvas, tab, block['key'], 'table' if repeated else 'form')].append(item)
            all_items.append(item)
    folded_buttons = []
    if config['screen_fold_list_buttons'] and openers:
        folded_buttons = fold_list_buttons(groups, all_items, openers, active_overrides, notices, key_context, catalog)
        gone = {f['owner'] for f in folded_buttons}
        actions[:] = [a for a in actions if a['owner'] not in gone]
        inferred_types[:] = [i for i in inferred_types if i['owner'] not in gone]
        property_gaps[:] = [g for g in property_gaps if g['owner'] not in gone]
    # The XML hierarchy supplies the canvas of graphics, including nested groups.
    def visit(node, canvas='', page='', visible=True):
        p = props(node); kind = tag(node)
        if kind == 'canvas': canvas = get(p, 'Name')
        if kind == 'tabpage': page = get(p, 'Name')
        if kind == 'graphics':
            visible = visible and truth(get(p, 'Visible'))
            shape = get(p, 'GraphicsType').lower()
            label = get(p, 'FrameTitle') if shape == 'frame' else ''
            if shape == 'text':
                segments = [get(props(e), 'Text', default=e.text or '') for e in node.iter() if tag(e) == 'textsegment']
                label = ''.join(segments) or get(p, 'Text')
            graphics.append({'name': get(p, 'Name'), 'canvas': get(p, 'CanvasName') or canvas, 'tab': get(p, 'TabPageName') or page,
                             'kind': shape, 'label': text(label), 'visible': visible, 'x': number(get(p, 'XPosition')), 'y': number(get(p, 'YPosition')),
                             'width': number(get(p, 'Width')), 'height': number(get(p, 'Height')),
                             'status': 'unused', 'target': '', 'reason': 'Nem jelenik meg: nincs hozzá megjelenített régió vagy támogatott grafikai leképezés.' if visible else 'Visible=false (elem vagy grafikai szülő).'})
        for child in node:
            visit(child, canvas, page, visible)
    visit(form)
    # Text graphics used to resurrect the calendar after its block was omitted.
    # Require exclusive ownership by proven framework blocks. A shared canvas,
    # even with hidden business items, and unowned text-only canvases stay intact.
    skipped_blocks = {b['block'] for b in framework_blocks}
    canvas_blocks = defaultdict(set)
    for owner, node in source_items.items():
        canvas = get(props(node), 'CanvasName')
        if canvas:
            canvas_blocks[canvas].add(owner.split('.', 1)[0])
    framework_canvases = [{'canvas': canvas, 'blocks': sorted(owners),
                           'reason': 'Kizárólag kihagyott keretrendszer-blokkok megjelenítési felülete; a natív vezérlő helyettesíti.',
                           'catalog': catalog['source']}
                          for canvas, owners in sorted(canvas_blocks.items()) if owners <= skipped_blocks]
    skipped_canvases = {c['canvas'] for c in framework_canvases}
    for graphic in graphics:
        if graphic['canvas'] in skipped_canvases:
            graphic['reason'] = 'Kihagyott keretrendszer-canvas; a natív vezérlő helyettesíti.'
            graphic['framework'] = True
    frame_hierarchy(graphics)
    sections = []
    group_scopes = {}
    for (canvas, page, block_key, mode), items in groups.items():
        partitions = defaultdict(list)
        for item in items:
            frames = [g for g in graphics if g['visible'] and g['label'] and g['kind'] == 'frame' and g['canvas'] == canvas and g['tab'] == page
                      and item['positioned'] and contains(g, item)]
            frame = min(frames, key=lambda g: g['width'] * g['height']) if frames else None
            frame_name = frame['name'] if frame else ''
            if item['group']:
                scope = (canvas, page, block_key, mode, frame_name)
                if item['group'] in group_scopes and group_scopes[item['group']] != scope:
                    raise MigrationError('SCREEN_OVERRIDE_GROUP_SCOPE: egy csoport egy blokkon, canvas/fülön és forráskereten belül használható: ' + item['group'])
                group_scopes[item['group']] = scope
            partitions[(frame_name, item['group'])].append(item)
        for (frame, group), members in partitions.items():
            members = spans(members, config['layout_columns'], config['screen_row_tolerance'], config['screen_preserve_gaps'])
            place_separators(members, notices)
            if mode == 'table' and any('order' in i['layout_override'] for i in members):
                members.sort(key=lambda i: (i['layout_override'].get('order', i['order']), i['x']))
            base = next(b for b in blocks if b['key'] == block_key)
            sections.append({'key': block_key + 'Region' + str(len(sections) + 1), 'block': base['name'], 'block_key': block_key,
                             'canvas': canvas, 'tab': page, 'frame': frame, 'mode': mode, 'records': max(i['records'] for i in members),
                             'group': group, 'group_label': overrides.get('groups', {}).get(group, {}).get('label', ''),
                             'y': min(i['y'] for i in members), 'x': min(i['x'] for i in members), 'items': members})
    sections.sort(key=lambda s: (s['y'], s['x'], s['key']))
    # Keep simple screens pleasant to edit: one definition per block when possible.
    counts = Counter(s['block_key'] for s in sections)
    for s in sections:
        s['property'] = s['block_key'] if counts[s['block_key']] == 1 else s['key']
    used_canvases = list(dict.fromkeys([s['canvas'] for s in sections] + [g['canvas'] for g in graphics
                                                                          if g['canvas'] in canvases and g['canvas'] not in skipped_canvases
                                                                          and g['visible'] and g['kind'] == 'text' and g['label'] and g['label'] != title]))
    surfaces = []
    for canvas in used_canvases:
        node = canvases.get(canvas); p = props(node) if node is not None else {}
        tabs = []
        if node is not None:
            for t in node:
                if tag(t) == 'tabpage':
                    tp = props(t); tabs.append({'name': get(tp, 'Name'), 'label': text(get(tp, 'Label', 'Prompt', 'Name')),
                                                'visible': truth(get(tp, 'Visible')), 'enabled': truth(get(tp, 'Enabled'))})
        known_tabs = {t['name'] for t in tabs}
        for s in sections:
            if s['canvas'] == canvas and s['tab'] and s['tab'] not in known_tabs:
                raise MigrationError('SCREEN_UNKNOWN_TAB: ' + canvas + '/' + s['tab'])
        surfaces.append({'name': canvas, 'key': name(canvas or 'main'), 'type': get(p, 'CanvasType', default='Content'),
                         'visible': truth(get(p, 'Visible')), 'window': '', 'modal': False, 'title': '', 'tabs': tabs})
    # The developer's choice of windows (screen_windows); 'ask' stops here once with a preview.
    from .screen_windows import select_windows
    excluded = select_windows(form, surfaces, sections, graphics, config, module, text(get(pform, 'Title')))
    if excluded:
        notices.append({'code': 'SCREEN_WINDOWS_SELECTED', 'owner': module,
                        'detail': 'Generált ablakok (screen_windows): ' + ', '.join(config['screen_windows'])})
    windows = build_windows(form, canvases, surfaces, sections, config, text, notices, resolution.metadata, skipped_canvases | excluded)
    surface_keys = unique_names([s['name'] or '@MAIN' for s in surfaces])
    for s in surfaces: s['key'] = surface_keys[s['name'] or '@MAIN']
    for graphic in graphics:
        if not graphic['visible'] or graphic.get('framework'): continue
        surface = next((s for s in surfaces if s['name'] == graphic['canvas']), None)
        page = next((t for t in surface['tabs'] if t['name'] == graphic['tab']), None) if surface else None
        if surface is None:
            graphic['reason'] = 'Nincs megjelenített canvas a grafikai elemhez.'
        elif graphic['tab'] and (page is None or not page['visible']):
            graphic['reason'] = 'Ismeretlen fül.' if page is None else 'A fül Visible=false.'
        elif not graphic['label']:
            graphic['reason'] = 'Nincs szöveg/FrameTitle, vagy nem támogatott grafikai alakzat.'
    lov_nodes = {get(props(e), 'Name'): e for e in form.iter() if tag(e) == 'lov'}
    record_groups = {get(props(e), 'Name'): props(e) for e in form.iter() if tag(e) == 'recordgroup'}
    for lookup in lookups.values():
        node = lov_nodes.get(lookup['name'])
        if node is None:
            notices.append({'code': 'MISSING_LOV', 'owner': lookup['owner'], 'detail': lookup['name']})
            continue
        p = props(node); rg = get(p, 'RecordGroupName'); query = get(record_groups.get(rg, {}), 'RecordGroupQuery')
        lookup.update({'record_group': rg, 'query': query, 'parameters': sorted(set(re.findall(r'(?<!:):([A-Za-z][A-Za-z0-9_$.]*)', query)))})
        for c in node:
            if tag(c) != 'lovcolumnmapping': continue
            cp = props(c); target = get(cp, 'ReturnItem')
            lookup['columns'].append({'column': get(cp, 'ColumnName', 'Name'), 'title': text(get(cp, 'Title')), 'return_item': target})
            if target in known_owners: lookup['return_items'].append(target)
    skipped = {b['block'].upper() for b in framework_blocks}
    audit = trigger_audit(form, catalog, skipped)
    portability = [{'owner': l['owner'], 'kind': 'LOV ' + l['name'], 'constructs': framework.oracle_sql(l['query'])}
                   for l in lookups.values() if l.get('query')]
    for b in blocks:
        for kind, sql in (('WHERE', b['where']), ('ORDER BY', b['order_by'])):
            if sql: portability.append({'owner': b['name'], 'kind': kind, 'constructs': framework.oracle_sql(sql)})
    portability = [p for p in portability if p['constructs']]
    field_lengths = apply_field_lengths(sections, config['screen_field_lengths'], notices)
    validation_audit = [r for section in sections for item in section['items'] if not item.get('spacer')
                        for r in validation_coverage(item, section['mode'])]
    spacers = [{'owner': i['owner'], 'pattern': i['spacer'], 'label': i['label'], 'mode': section['mode'],
                'col': i['col'], 'col_before': i['col_before'], 'col_after': i['col_after'], 'row': i.get('row')}
               for section in sections for i in section['items'] if i.get('spacer')]
    hidden_owners = {i['owner'] for i in hidden}
    validation_audit += [r for b in ui['blocks'] for i in b['items'] if i['owner'] in hidden_owners for r in validation_coverage(i, 'hidden')]
    return {'screen_version': 4, 'module': {'key': name(module), 'class': name(module, 'pascal') + 'Component',
                                            'selector': config['angular_selector_prefix'] + '-' + name(module, 'kebab'), 'title': title},
            'surfaces': surfaces, 'windows': windows, 'sections': sections, 'blocks': blocks, 'graphics': graphics, 'actions': actions,
            'lookups': list(lookups.values()), 'hidden_items': hidden, 'notices': notices, 'spacers': spacers,
            'field_lengths': field_lengths,
            'folded_buttons': folded_buttons, 'framework_blocks': framework_blocks, 'framework_canvases': framework_canvases,
            'trigger_audit': audit, 'sql_portability': portability,
            'inferred_types': inferred_types, 'validation_audit': validation_audit, 'property_gaps': property_gaps,
            'overrides': override_audit, 'override_template': screen_overrides.template(get(pform, 'Name'), fingerprints, [i['owner'] for i in all_items]),
            'lookup_groups': lookup_groups(sections, list(lookups.values())),
            'layout_settings': {'columns': config['layout_columns'], 'row_tolerance': config['screen_row_tolerance'], 'preserve_gaps': config['screen_preserve_gaps']},
            'relations': [{k: r[k] for k in ['name', 'master', 'detail', 'join']} for r in ui['relations']],
            'semantic_review': [{k: i[k] for k in ['owner', 'code', 'detail']} for i in ui['issues']
                                if i['code'] in {'UNSUPPORTED_FORMAT_MASK', 'DYNAMIC_INITIAL_VALUE', 'UNSUPPORTED_DATE_INITIAL', 'INVALID_NUMBER_INITIAL',
                                                 'UNSUPPORTED_LIST_STYLE', 'UNSUPPORTED_DISABLED_OPTION', 'INHERITANCE_UNVERIFIED'}],
            'display_text_changes': list({(x['source'], x['display']): x for x in repaired}.values()),
            'ui_issue_counts': dict(sorted(Counter(i['code'] for i in ui['issues']).items()))}


def apply_screen_types(ui, plan):
    """Keep the screen model truthful without rewriting raw ItemType provenance."""
    by_owner = {i['owner']: i for s in plan['sections'] for i in s['items']}
    for block in ui['blocks']:
        for item in block['items']:
            screen = by_owner.get(item['owner'])
            if screen is None:
                continue
            item.update({key: screen[key] for key in ('widget', 'representation', 'type_source', 'inference_reason', 'readonly', 'enabled')})
            item['layout'].update({key: screen[key] for key in ('col', 'col_before', 'col_after', 'row')})
            override = next((o for o in plan['overrides'] if o['owner'] == item['owner']), None)
            if override and 'label' in override['set']:
                text_key = item['text_keys']['prompt'] or item['text_keys']['label']
                if text_key: ui['i18n'][text_key] = screen['label']
            if screen['widget'] == 'checkbox' and item['checkbox'] is None:
                item['checkbox'] = {'checked': screen['checked'], 'unchecked': screen['unchecked'], 'other_values': 'reject'}
            elif screen['widget'] != 'checkbox': item['checkbox'] = None
            if screen['type_source'] == 'inferred' and not screen.get('spacer'):
                issue = {'code': 'SCREEN_INFERRED_ITEM_TYPE', 'owner': item['owner'], 'detail': screen['inference_reason'], 'severity': 'review'}
                if issue not in ui['issues']: ui['issues'].append(issue)
            if screen.get('spacer'):
                issue = {'code': 'SCREEN_SPACER_ITEM', 'owner': item['owner'], 'severity': 'review',
                         'detail': 'Elrendezési térköz (keretrendszer-katalógus: ' + screen['spacer'] + '): üres FormBlock-elem, adatkötés és validáció nélkül.'}
                if issue not in ui['issues']: ui['issues'].append(issue)
            if override:
                ui['issues'].append({'code': 'SCREEN_REVIEWED_OVERRIDE', 'owner': item['owner'], 'detail': override['reason'], 'severity': 'review'})
    for folded in plan.get('folded_buttons', []):
        issue = {'code': 'SCREEN_FOLDED_LIST_BUTTON', 'owner': folded['owner'], 'severity': 'review',
                 'detail': 'Listanyitó gomb, beolvasztva ide: ' + folded['target'] + '. ' + folded['basis']}
        if issue not in ui['issues']: ui['issues'].append(issue)
    from .ui_schema import validate_ui_model
    validate_ui_model(ui)

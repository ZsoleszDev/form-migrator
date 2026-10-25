"""Forms presentation -> small, explicit FormBlock descriptions (no Forms runtime)."""
from __future__ import annotations

import copy
import hashlib
import re
from .common import name
from .xmlmodel import canonical, get, integer, yes


def output_name(model):
    title = model.get('title', '').strip()
    stem = name(title or model['name'])
    return stem if len(stem) <= 80 else stem[:64] + hashlib.sha256(stem.encode()).hexdigest()[:8]


def build_presentation(model, config):
    """Keep the source IR intact. Collapse only identifiable presentation helpers."""
    blocks = copy.deepcopy(model['blocks'])
    changes = []
    helpers = []
    forced = {value.upper() for value in config['calendar_blocks']}
    for block in blocks:
        cells = [i for i in block['items'] if re.fullmatch(r'CELL_?\d+(?:_\d+)?', i['name'])]
        known_name = canonical(block['name']) in {'calendar', 'calendardialog', 'datecalendar'}
        control_only = not block['database'] and not any(i['database'] for i in block['items'])
        if control_only and (block['name'] in forced or (known_name and len(cells) >= 7)):
            helpers.append(block['name'])
            changes.append({'rule': 'calendar-helper', 'source': block['name'], 'removed_items': len(block['items']),
                            'detail': 'A cellákból épített naptárt FormBlock calendar mező váltja ki.'})
    blocks = [b for b in blocks if b['name'] not in helpers]
    date_targets = [f"{b['name']}.{i['name']}" for b in blocks for i in b['items'] if i['type'] == 'datetime']
    if helpers and not date_targets:
        # A helper with no discoverable business date gets exactly one picker;
        # never guess which character field is the original return target.
        blocks.append({'name': 'DATE_PICKER', 'key': 'datePicker', 'class': 'DatePicker', 'database': False, 'properties': {},
                       'items': [{'name': 'SELECTED_DATE', 'field': 'selectedDate', 'label': 'Dátum', 'kind': 'text',
                                  'type': 'datetime', 'required': False, 'visible': True, 'enabled': True, 'concealed': False,
                                  'initial': None, 'initial_value': '', 'max_length': None, 'options': [], 'properties': {},
                                  'database': False, 'checked_value': 'Y', 'unchecked_value': 'N'}]})
        changes.append({'rule': 'calendar-target-review', 'source': ', '.join(helpers),
                        'detail': 'Nem található DATE célmező: egy selectedDate picker készült. A visszaadási célmezőt kösd be.'})
    for block in blocks:
        kept = []
        for item in block['items']:
            triggers = [t for t in model['triggers'] if t.get('block') == block['name'] and t.get('item') == item['name']]
            # Only remove a button whose entire body merely opens a collapsed calendar.
            pure_calendar = False
            if item['kind'] == 'button' and len(triggers) == 1 and helpers and date_targets:
                text = triggers[0]['source'].strip()
                text = re.sub(r'^BEGIN\s+|\s+END\s*;?$', '', text, flags=re.I).strip()
                match = re.fullmatch(r"GO_BLOCK\s*\(\s*'([^']+)'\s*\)\s*;", text, flags=re.I)
                pure_calendar = bool(match and match[1].upper() in helpers)
            if pure_calendar:
                changes.append({'rule': 'calendar-launch-button', 'source': f"{block['name']}.{item['name']}",
                                'detail': 'A datepicker saját megnyitója kiváltja ezt a naptárnyitó gombot.'})
            else:
                kept.append(item)
        block['items'] = kept
        count = integer(block['properties'], 'RecordsDisplayCount', 'NumberOfRecordsDisplayed') or 1
        block['table'] = count > 1 or block['name'] in {v.upper() for v in config['table_blocks']}
        block['label'] = get(block['properties'], 'Title', default=block['name'])
    return {'name': output_name(model), 'blocks': [b for b in blocks if b['items']],
            'changes': changes, 'calendar_helpers': helpers}


def field_kind(item, config):
    if item['concealed']:
        return config['form_block_types']['password'] or 'password'
    if item['kind'] == 'button':
        return 'button'
    if item['type'] == 'datetime':
        return config['form_block_types']['datetime']
    if item['kind'] in {'checkbox', 'select', 'radio'}:
        return config['form_block_types'][item['kind']]
    if item['type'] == 'number':
        # Arbitrary-precision NUMBER must not enter a JS-number widget.
        precision = item.get('precision')
        if precision is None or precision > 15:
            return config['form_block_types']['text']
        return config['form_block_types']['number']
    if yes(item['properties'], 'MultiLine', default=False):
        return config['form_block_types']['textarea']
    return config['form_block_types']['text']

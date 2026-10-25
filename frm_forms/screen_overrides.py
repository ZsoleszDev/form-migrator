"""Reviewed screen decisions, bound to exact source objects, never executable code."""
import copy
import hashlib
import json
import re

from .common import MigrationError
from .ui_types import WIDGETS
from .xmlmodel import props, tag

EMPTY = {'version': 1, 'items': {}, 'groups': {}}
FIELDS = {'widget', 'label', 'col', 'col_before', 'row', 'order', 'group', 'readonly', 'enabled'}


def validate(value, columns=12):
    def fail(message): raise MigrationError('SCREEN_OVERRIDES: ' + message)
    if not isinstance(value, dict) or set(value) - {'version', 'form_name', 'items', 'groups'} or type(value.get('version')) is not int or value['version'] != 1:
        fail('version=1, form_name, items és groups gyökérkulcsok használhatók.')
    if not isinstance(value.get('items'), dict) or not isinstance(value.get('groups', {}), dict):
        fail('items és groups objektum szükséges.')
    if len(value['items']) > 10000 or len(value.get('groups', {})) > 1000:
        fail('Túl sok szabály vagy csoport.')
    if value.get('form_name') is not None and (not isinstance(value['form_name'], str) or not value['form_name'].strip()):
        fail('form_name: nem üres modulnév szükséges.')
    for group, details in value.get('groups', {}).items():
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,79}', group) or not isinstance(details, dict) or set(details) != {'label'} or not isinstance(details['label'], str):
            fail('groups: azonosító és {label: szöveg} szükséges.')
    for owner, rule in value['items'].items():
        if not isinstance(owner, str) or '.' not in owner or not isinstance(rule, dict) or set(rule) - {'source_fingerprint', 'reason', 'set'}:
            fail('Hibás mezőszabály: ' + str(owner))
        edits = rule.get('set')
        if not isinstance(edits, dict) or set(edits) - FIELDS:
            fail(owner + ': nem támogatott set kulcs.')
        if not edits: continue  # Unedited template entries do not impose stale decisions.
        if not value.get('form_name'): fail('Aktív szabályhoz form_name szükséges.')
        if not isinstance(rule.get('reason'), str) or not rule['reason'].strip(): fail(owner + ': reason szükséges.')
        if not isinstance(rule.get('source_fingerprint'), str) or not re.fullmatch(r'[0-9a-f]{64}', rule['source_fingerprint']):
            fail(owner + ': az elemzési sablonból származó SHA-256 source_fingerprint szükséges.')
        for key, entry in edits.items():
            if key == 'widget' and (not isinstance(entry, str) or entry not in WIDGETS): fail(owner + ': ismeretlen widget.')
            if key in {'readonly', 'enabled'} and type(entry) is not bool: fail(owner + ': ' + key + ' boolean szükséges.')
            if key == 'label' and not isinstance(entry, str): fail(owner + ': label szöveg szükséges.')
            if key in {'col', 'col_before', 'row', 'order'}:
                maximum = columns if key in {'col', 'col_before'} else 10000
                if type(entry) is not int or not (1 if key == 'col' else 0) <= entry <= maximum:
                    fail(owner + ': hibás ' + key + '.')
            if key == 'group' and (not isinstance(entry, str) or entry not in value.get('groups', {})):
                fail(owner + ': ismeretlen group.')
    return value


def fingerprint(node, block):
    def record(element):
        return {'tag': tag(element), 'text': (element.text or '').strip(), 'properties': {k: v for k, v in sorted(props(element).items()) if k not in {'dirtyinfo', 'persistentclientinfolength'}},
                'children': [record(c) for c in element if tag(c) not in {'property', 'propertyvalue'}]}
    # Include effective item properties/children (also trigger bodies), plus the
    # block properties affecting presentation and allowed operations.
    context = {k: v for k, v in sorted(props(block).items()) if k in {
        'name', 'numberofrecordsdisplayed', 'recordsdisplaycount', 'insertallowed', 'updateallowed', 'queryallowed', 'deleteallowed'}}
    payload = {'version': 1, 'item': record(node), 'block': context}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def prepare(value, form_name, source_items, source_blocks):
    if value.get('form_name') and value['form_name'] != form_name:
        raise MigrationError('SCREEN_OVERRIDE_FORM: a szabályfájl más modulhoz tartozik: ' + value['form_name'])
    fingerprints = {owner: fingerprint(node, source_blocks[owner.rsplit('.', 1)[0]]) for owner, node in source_items.items()}
    active = {owner: rule for owner, rule in value['items'].items() if rule['set']}
    for owner, rule in active.items():
        if owner not in source_items:
            raise MigrationError('SCREEN_OVERRIDE_UNKNOWN_ITEM: ' + owner)
        if rule['source_fingerprint'] != fingerprints[owner]:
            raise MigrationError('SCREEN_OVERRIDE_STALE: ' + owner + ': az effektív XML megváltozott. Generálj szabályfájl nélkül új sablont, ellenőrizd a döntést, majd frissítsd a source_fingerprint értékét.')
    return active, fingerprints


def apply(item, rule):
    if not rule: return None
    before = {key: item.get(key) for key in rule['set']}
    edits = rule['set']
    widget = edits.get('widget', item['widget'])
    if widget in {'unsupported', 'image', 'tree'} and set(edits) & {'col', 'col_before', 'row', 'order', 'readonly', 'enabled'}:
        raise MigrationError('SCREEN_OVERRIDE_PLACEHOLDER: az adaptert igénylő helyőrzőn csak felirat, csoport vagy támogatott widget állítható: ' + item['owner'])
    if 'readonly' in edits and ((widget in {'button', 'unsupported', 'image', 'tree'} and edits['readonly']) or (widget == 'display' and not edits['readonly'])):
        raise MigrationError('SCREEN_OVERRIDE_READONLY: ez a widget nem támogatja a kért readonly állapotot: ' + item['owner'])
    if item['records'] > 1:
        invalid = set(edits) & {'col', 'col_before', 'row', 'readonly', 'group'}
        if widget != 'button' and 'enabled' in edits: invalid.add('enabled')
        if invalid:
            raise MigrationError('SCREEN_OVERRIDE_TABLE: a csak olvasható táblázatban nem használható: ' + ', '.join(sorted(invalid)) + ': ' + item['owner'])
    if 'widget' in edits:
        widget = edits['widget']; v = item['validation']
        if widget == 'checkbox' and (not item['checked'] or not item['unchecked'] or item['checked'] == item['unchecked']):
            raise MigrationError('SCREEN_OVERRIDE_WIDGET: checkboxhoz eltérő CheckedValue/UncheckedValue szükséges: ' + item['owner'])
        if widget in {'select', 'radio', 'multiselect'} and not item['options']:
            raise MigrationError('SCREEN_OVERRIDE_WIDGET: listaopciók hiányoznak: ' + item['owner'])
        if widget == 'autocomplete' and not item['lov']:
            raise MigrationError('SCREEN_OVERRIDE_WIDGET: LOVName hiányzik: ' + item['owner'])
        if item['lov'] and widget != 'autocomplete':
            raise MigrationError('SCREEN_OVERRIDE_WIDGET: a LOV-ot használó mező autocomplete maradjon: ' + item['owner'])
        item['representation'] = ('boolean' if widget == 'checkbox' else 'date' if widget in {'date', 'datetime'} else
            'string-array' if widget == 'multiselect' else
            ('safe-integer' if v['precision'] is not None and v['precision'] <= 15 and v['scale'] == 0 else 'decimal-string') if widget == 'number' else 'string')
        item['type_source'] = 'override'
        item['inference_reason'] = 'Ellenőrzött felülbírálás: ' + rule['reason']
    for key, value in edits.items():
        if key in {'col', 'col_before', 'row', 'order'}:
            item['layout_override'][key] = value
        else:
            item[key] = value
    return {'owner': item['owner'], 'reason': rule['reason'], 'source_fingerprint': rule['source_fingerprint'],
            'before': before, 'set': copy.deepcopy(edits)}


def template(form_name, fingerprints, owners):
    return {'version': 1, 'form_name': form_name, 'groups': {},
            'items': {owner: {'source_fingerprint': fingerprints[owner], 'reason': '', 'set': {}} for owner in owners}}

import copy
import json
import re
import math
from pathlib import Path
from .common import MigrationError
from .ui_types import WIDGETS
from .ui_inventory import SUPPORTED
from .xmlmodel import canonical

DATA = Path(__file__).with_name('data')
UI_DEFAULTS = {'screen_input_bindings': {'version': 1, 'bindings': {}}, 'emit_imports': False, 'widget_map': None, 'property_aliases': {}, 'ignored_properties': {},
               'layout_columns': 12, 'optimus_import_path': '', 'optimus_form_block_symbol': '',
               'form_block_type_import_path': '', 'environment_import_path': '', 'table_import_path': '', 'table_symbol': '',
               'screen_tab_layout': 'tabs', 'screen_infer_widgets': True, 'screen_repair_display_text': True,
               # Fields fill their row; empty space only where the form has an L_URES_* spacer.
               'screen_row_tolerance': 0.25, 'screen_preserve_gaps': False,
               'screen_primary_window': '',
               # Survey runs: several possible main windows -> the first one instead of a question.
               'screen_primary_window_auto': False,
               # Which windows become screens: 'all' (CLI default) or 'ask' (the web asks once per form,
               # with a preview); screen_windows holds the developer's answer.
               'screen_window_selection': 'all', 'screen_windows': [],
               # The standalone FormBlock button renders labelText (as ButtonGroup
               # does); btnLabel belongs to the inputGroup add-on button.
               'screen_button_label_property': 'labelText',
               # A button whose trigger only does go_item + LIST_VALUES duplicates
               # the opener that the target's own dropdown/autocomplete renders.
               'screen_fold_list_buttons': True,
               # Editable list of Designer/Headstart objects; None = bundled catalog.
               'framework_catalog': None,
               # FormBlock type of a catalogued layout gap (spacer_items, e.g. L_URES_*):
               # an empty element with col/colBefore only, no form control.
               'screen_spacer_type': 'label',
               # Reviewed text lengths: {"fields": {"formControlName" | "BLOKK.formControlName": {"min": n, "max": n}}}.
               'screen_field_lengths': {},
               # Company toast: injected as `toast` into every generated component.
               'toast_service_import_path': '', 'toast_service_symbol': 'ToastService',
               'toast_life_ms': {'success': 3000, 'warning': 8000, 'danger': 6000},
               'screen_overrides': {'version': 1, 'items': {}, 'groups': {}}}
BUTTON_LABEL_PROPERTIES = {'labelText', 'btnLabel'}
FIELD_KEY = re.compile(r'(?:[A-Za-z0-9_$#]+\.)?[A-Za-z_$][A-Za-z0-9_$]*')


def validate_field_lengths(value):
    """The reviewed field length file: formControlName (or BLOCK.formControlName) -> {min, max}."""
    if not isinstance(value, dict) or set(value) - {'version', 'description', 'fields'} or value.get('version', 1) != 1:
        raise MigrationError('FIELD_LENGTHS: {"version": 1, "fields": {...}} objektum szükséges.')
    fields = value.get('fields', {})
    if not isinstance(fields, dict):
        raise MigrationError('FIELD_LENGTHS: a fields objektum formControlName -> {min, max}.')
    for key, entry in fields.items():
        if not FIELD_KEY.fullmatch(str(key)) or not isinstance(entry, dict) or not entry or set(entry) - {'min', 'max'}:
            raise MigrationError('FIELD_LENGTHS: hibás bejegyzés: ' + str(key) + ' (kulcs: formControlName vagy BLOKK.formControlName; érték: min és/vagy max).')
        low, high = entry.get('min'), entry.get('max')
        if (low is not None and (type(low) is not int or low < 0)) or (high is not None and (type(high) is not int or high < 1)):
            raise MigrationError('FIELD_LENGTHS: ' + key + ': min >= 0 és max >= 1 egész szám (vagy null).')
        if low is not None and high is not None and low > high:
            raise MigrationError('FIELD_LENGTHS: ' + key + ': a min nem lehet nagyobb a max-nál.')
SPACER_TYPES = {'text', 'label', 'divider'}


def widget_map(config):
    file = Path(config['widget_map']) if config.get('widget_map') else DATA / 'widget-map.json'
    try: value = json.loads(file.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc: raise MigrationError('WIDGET_MAP: ' + str(exc)) from exc
    if set(value) != {'version','contract','widgets'} or value['version'] != 1 or set(value['widgets']) != set(WIDGETS):
        raise MigrationError('WIDGET_MAP: version=1 és minden absztrakt widget explicit leképezése szükséges.')
    for key, entry in value['widgets'].items():
        if not isinstance(entry,dict) or 'type' not in entry or set(entry)-{'type','props','exact_decimal_type','adapter'}:
            raise MigrationError('WIDGET_MAP: hibás widget: ' + key)
        if (key=='unsupported') != (entry['type'] is None):
            raise MigrationError('WIDGET_MAP: csak unsupported esetén lehet type=null.')
        if key=='number' and not entry.get('exact_decimal_type'):
            raise MigrationError('WIDGET_MAP: pontos decimális NUMBER-hez exact_decimal_type szükséges.')
        for field in ['type','exact_decimal_type']:
            item=entry.get(field)
            if item is not None and (not isinstance(item,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9]*',item)):
                raise MigrationError('WIDGET_MAP: hibás típus: ' + key)
        props=entry.get('props',{})
        if not isinstance(props,dict) or set(props)-{'showTime','binary','readonly','filter','showClear','dropdown','showIcon'} or any(type(v) is not bool for v in props.values()):
            raise MigrationError('WIDGET_MAP: nem engedélyezett property / érték: '+key)
        if 'adapter' in entry and not isinstance(entry['adapter'],str): raise MigrationError('WIDGET_MAP: adapter szöveg szükséges.')
    # Legacy explicit overrides remain useful; defaults agree with the data file.
    from .contracts import COMPANY_DEFAULTS
    for key, typ in config.get('form_block_types',{}).items():
        if typ != COMPANY_DEFAULTS['form_block_types'].get(key) and key in value['widgets']:
            if not typ: raise MigrationError('WIDGET_MAP: nem törölhető támogatott típus: '+key)
            value['widgets'][key]['type']=typ
    return value


def validate(config, screen_mode=False):
    if not isinstance(config['screen_primary_window'], str) or len(config['screen_primary_window']) > 240 or any(ord(c) < 32 for c in config['screen_primary_window']):
        raise MigrationError('SCREEN_CONFIG: screen_primary_window legfeljebb 240 karakteres ablaknév szükséges.')
    if type(config['screen_primary_window_auto']) is not bool:
        raise MigrationError('SCREEN_CONFIG: screen_primary_window_auto: true vagy false szükséges.')
    if config['screen_window_selection'] not in {'all', 'ask'}:
        raise MigrationError('SCREEN_CONFIG: screen_window_selection: "all" vagy "ask" szükséges.')
    if not isinstance(config['screen_windows'], list) or not all(isinstance(w, str) and 0 < len(w) <= 240 for w in config['screen_windows']):
        raise MigrationError('SCREEN_CONFIG: screen_windows: ablaknevek listája szükséges.')
    if type(config['emit_imports']) is not bool or type(config['layout_columns']) is not int or config['layout_columns'] not in {12,18,24}:
        raise MigrationError('UI_CONFIG: emit_imports boolean; layout_columns 12/18/24 szükséges.')
    if config['screen_tab_layout'] not in {'tabs', 'accordion'} or any(type(config[k]) is not bool for k in ['screen_infer_widgets', 'screen_repair_display_text']):
        raise MigrationError('SCREEN_CONFIG: screen_tab_layout=tabs/accordion; a következtetés és szövegjavítás boolean.')
    if config['screen_button_label_property'] not in BUTTON_LABEL_PROPERTIES:
        raise MigrationError('SCREEN_CONFIG: screen_button_label_property értéke labelText vagy btnLabel lehet.')
    if config['framework_catalog'] is not None and (not isinstance(config['framework_catalog'], str) or not config['framework_catalog'].strip()):
        raise MigrationError('SCREEN_CONFIG: framework_catalog egy katalógusfájl útvonala vagy null.')
    if type(config['screen_fold_list_buttons']) is not bool:
        raise MigrationError('SCREEN_CONFIG: screen_fold_list_buttons boolean.')
    validate_field_lengths(config['screen_field_lengths'])
    from .input_bindings import validate
    validate(config['screen_input_bindings'])
    if not isinstance(config['toast_service_import_path'], str) or (config['toast_service_import_path'] and not re.fullmatch(r'[@A-Za-z0-9_./-]+', config['toast_service_import_path'])):
        raise MigrationError('UI_CONFIG: hibás import útvonal: toast_service_import_path')
    if not isinstance(config['toast_service_symbol'], str) or not re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$]*', config['toast_service_symbol']):
        raise MigrationError('UI_CONFIG: hibás toast_service_symbol.')
    life = config['toast_life_ms']
    if not isinstance(life, dict) or set(life) != {'success', 'warning', 'danger'} or not all(type(v) is int and 500 <= v <= 120000 for v in life.values()):
        raise MigrationError('UI_CONFIG: toast_life_ms = {success, warning, danger} ezredmásodperc, 500..120000.')
    if config['screen_spacer_type'] not in SPACER_TYPES:
        raise MigrationError('SCREEN_CONFIG: screen_spacer_type értéke ' + '/'.join(sorted(SPACER_TYPES)) + ' lehet.')
    tolerance = config['screen_row_tolerance']
    if type(tolerance) not in {int, float} or not math.isfinite(tolerance) or not 0 <= tolerance <= 0.5 or type(config['screen_preserve_gaps']) is not bool:
        raise MigrationError('SCREEN_CONFIG: screen_row_tolerance 0..0.5 közötti szám; screen_preserve_gaps boolean.')
    from .screen_overrides import validate as validate_overrides
    validate_overrides(config['screen_overrides'], config['layout_columns'])
    for key in ['optimus_import_path','form_block_type_import_path','environment_import_path','table_import_path']:
        if not isinstance(config[key],str) or (config[key] and not re.fullmatch(r'[@A-Za-z0-9_./-]+',config[key])):
            raise MigrationError('UI_CONFIG: hibás import útvonal: '+key)
    if config['table_symbol'] and not re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$]*',config['table_symbol']):
        raise MigrationError('UI_CONFIG: hibás table symbol.')
    if config['optimus_form_block_symbol'] and not re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$]*',config['optimus_form_block_symbol']):
        raise MigrationError('UI_CONFIG: hibás Optimus symbol.')
    import_keys = ['optimus_import_path','form_block_type_import_path','optimus_form_block_symbol'] + ([] if screen_mode else ['environment_import_path'])
    if config['emit_imports'] and any(not config[k] for k in import_keys):
        raise MigrationError('UI_CONFIG: emit_imports=true esetén kötelező: ' + ', '.join(import_keys) + '; privát API-nevet nem találunk ki.')
    if config['emit_imports'] and config['form_block_structure_type'] != 'FormBlock.Structure':
        raise MigrationError('UI_CONFIG: automatikus type-import jelenleg FormBlock.Structure szerződéshez támogatott.')
    for field in ['property_aliases','ignored_properties']:
        value=config[field]
        if not isinstance(value,dict): raise MigrationError('UI_CONFIG: '+field+' objektum szükséges.')
        normalized={}
        for kind,entries in value.items():
            ck=canonical(kind)
            if ck not in SUPPORTED or not isinstance(entries,dict): raise MigrationError('UI_CONFIG: ismeretlen elem: '+kind)
            normalized[ck]={}
            for original,target in entries.items():
                if not isinstance(target,str) or not target.strip(): raise MigrationError('UI_CONFIG: alias cél / figyelmen kívül hagyás indoka kötelező.')
                ca=canonical(original)
                if field=='property_aliases':
                    target=canonical(target)
                    if target not in SUPPORTED[ck] or ca in SUPPORTED[ck]: raise MigrationError('UI_CONFIG: alias csak új névből ismert property-re mutathat.')
                elif ca in SUPPORTED[ck]: raise MigrationError('UI_CONFIG: ismert property nem hagyható figyelmen kívül.')
                normalized[ck][ca]=target
        config[field]=normalized
    if not config['form_block_checkbox_boolean']:
        raise MigrationError('UI_CONFIG: a 4.x checkbox kontraktus boolean + explicit Oracle értékkonverzió. A nem bináris adapterhez saját widget-leképezés szükséges.')
    if config.get('calendar_blocks') or config.get('table_blocks'):
        raise MigrationError('UI_CONFIG: calendar_blocks/table_blocks kézi heurisztika megszűnt. A kijelzés alapja az XML NumberOfRecordsDisplayed és ItemType.')
    if config['table_bindings'] != {'rows':'value','columns':'columns','field':'field','header':'header'}:
        raise MigrationError('UI_CONFIG: a p-table adatkontraktus value/columns és field/header. A selector cserélhető, az ismeretlen API nem találgatható.')
    return widget_map(config)

"""Lossless names/counts, explicit parser support catalog, no spelling heuristics."""
from collections import Counter, defaultdict
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from .common import MigrationError, write_json
from .xmlmodel import canonical

# These are supported spellings, not a claim to be an exhaustive Oracle DTD.
INHERITANCE = 'ParentFilename ParentModule ParentName ParentType ParentModuleType SubclassSubObject'.split()
AUDIT = 'Name DirtyInfo PersistentClientInfoLength SubclassInformation'.split()
SUPPORT = {
    'property': 'Name Value', 'propertyvalue': 'Name Value',
    'formmodule': 'Title CoordinateSystem RealUnit CharacterCellWidth CharacterCellHeight',
    'coordinate': 'CoordinateSystem RealUnit CharacterCellWidth CharacterCellHeight DefaultFontScaling',
    'canvas': 'Name CanvasType Width Height ViewportWidth ViewportHeight Visible WindowName',
    'tabpage': 'Name Label Prompt Hint ToolTip CanvasName Visible Enabled',
    'block': 'Name DatabaseDataBlock DatabaseBlock QueryDataSourceType QueryDataSourceName DMLDataTargetName DMLDataName DMLDataTargetType DMLDataType Alias NumberOfRecordsDisplayed RecordsDisplayCount InsertAllowed UpdateAllowed DeleteAllowed QueryAllowed WhereClause DefaultWhere OrderByClause',
    'item': '''Name ItemType DataType Prompt Label Hint ToolTip CanvasName TabPageName XPosition YPosition Width Height
 Required MaximumLength MaxLength FixedLength LowestAllowedValue HighestAllowedValue Precision Scale FormatMask CaseRestriction
 CheckedValue UncheckedValue CheckBoxCheckedValue CheckBoxUncheckedValue MappingOfOtherValues InitialValue InitializeValue
 Enabled Visible Displayed QueryAllowed InsertAllowed UpdateAllowed DatabaseItem ColumnName PrimaryKey MultiLine ConcealData ListStyle MultiSelection
 LOVName LovName ReadOnly Iconic IconName Justification WrapStyle CopyValueFromItem SynchronizeWithItem CalculationMode''',
    'listitemelement': 'Name Label Value ListItemLabel ListItemValue',
    'radiobutton': 'Name Label Value RadioButtonValue XPosition YPosition Width Height Enabled Visible',
    'lov': 'Name Title RecordGroupName Width Height',
    'lovcolumnmapping': 'Name ColumnName ReturnItem DisplayWidth Title',
    'recordgroup': 'Name RecordGroupType RecordGroupQuery',
    'recordgroupcolumn': 'Name DataType MaximumLength',
    'relation': 'Name MasterDataBlock MasterBlock DetailDataBlock DetailBlock JoinCondition DeleteRecordBehavior DeleteRecord Deferred AutoQuery RelationType PreventMasterlessOperations',
    'trigger': 'Name TriggerName TriggerText Text ExecuteHierarchy FireInEnterQueryMode DisplayInKeyboardHelp KeyboardHelpText',
    'programunit': 'Name ProgramUnitType ProgramUnitText',
    'attachedlibrary': 'Name LibraryLocation LibrarySource',
    'window': 'Name Title Width Height XPosition YPosition Modal Visible',
    'alert': 'Name Title Message AlertMessage AlertStyle Button1Label Button2Label Button3Label DefaultAlertButton',
    'objectlibrary': 'Name', 'objectlibrarytab': 'Name Label', 'module': '',
    'menumodule': 'Name', 'menu': 'Name', 'menuitem': 'Name Label CommandText',
    # Builder-only grouping: an entry points at another object in the same module
    # and carries no UI, data or business semantics of its own.
    'objectgroup': 'Name',
    'objectgroupchild': 'Name ObjectType ChildObjectType ObjectGroupChildType ObjectName',
}
# Preserved and counted, never rendered. Auditing their attributes as missing
# widget bindings would be a category error, not a finding.
NON_UI_ELEMENTS = {'objectgroup', 'objectgroupchild'}
SUPPORTED = {kind: {canonical(k) for k in text.split()} | {canonical(k) for k in INHERITANCE + AUDIT}
             for kind, text in SUPPORT.items()}
SCALAR_ELEMENTS = {'triggertext', 'programunittext', 'recordgroupquery'}


def support_status(element, attribute, aliases=None):
    kind, key = canonical(element), canonical(attribute)
    key = (aliases or {}).get(kind, {}).get(key, key)
    if key in {canonical(k) for k in INHERITANCE}: return 'inheritance'
    if key in {canonical(k) for k in AUDIT}: return 'audit'
    return 'recognized' if key in SUPPORTED.get(kind, set()) else 'unused'


def read_xml(path):
    if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
        raise MigrationError('INVENTORY_INPUT: létező, legfeljebb 32 MiB XML szükséges: ' + path.name)
    raw = path.read_bytes()
    if re.search(br'<!\s*ENTITY\b', raw.replace(b'\x00',b''), re.I):
        raise MigrationError('INVENTORY_XML: entitásdeklaráció: ' + path.name)
    try: return ET.fromstring(raw)
    except ET.ParseError as exc: raise MigrationError('INVENTORY_XML: ' + path.name + ': ' + str(exc)) from exc


def collect(documents, aliases=None):
    elements, counts, values, properties = Counter(), Counter(), defaultdict(set), Counter()
    property_uses=Counter(); property_values=defaultdict(set)
    for filename, root in sorted(documents, key=lambda x: x[0]):
        parents={id(child):parent for parent in root.iter() for child in parent}
        for element in root.iter():
            tag = element.tag.split('}')[-1]
            elements[tag] += 1
            for attr, value in element.attrib.items():
                key = (tag, attr.split('}')[-1])
                counts[key] += 1; values[key].add(value)
            if canonical(tag) in {'property', 'propertyvalue'}:
                p = {canonical(k): v for k,v in element.attrib.items()}
                properties[p.get('name', '')] += 1
                parent=parents.get(id(element)); owner=parent.tag.split('}')[-1] if parent is not None else ''
                key=(owner,p.get('name','')); property_uses[key]+=1; property_values[key].add(p.get('value',element.text or ''))
    rows = [{'element': k[0], 'attribute': k[1], 'count': counts[k], 'examples': sorted(values[k])[:3],
             'usage': support_status(*k, aliases=aliases)} for k in sorted(counts)]
    property_rows=[{'element':k[0],'property':k[1],'count':property_uses[k],'examples':sorted(property_values[k])[:3],'usage':support_status(*k,aliases=aliases)} for k in sorted(property_uses)]
    return {'inventory_version': 1, 'files': [x[0] for x in sorted(documents, key=lambda x: x[0])],
            'elements': [{'name': k, 'count': elements[k], 'recognized': canonical(k) in SUPPORTED or canonical(k) in SCALAR_ELEMENTS or canonical(k) in {'property', 'propertyvalue'}} for k in sorted(elements)],
            'attributes': rows, 'unused_attributes': [x for x in rows if x['usage'] == 'unused'],
            'property_element_usage':property_rows, 'unused_property_elements':[row for row in property_rows if row['usage']=='unused'],
            'property_element_names': [{'name': k, 'count': properties[k]} for k in sorted(properties)],
            'usage_definition': 'recognized = explicit support catalog; not proof of runtime consumption; audit/inheritance are not widget bindings'}


def inventory(paths, output):
    if output.exists(): raise MigrationError('INVENTORY_OUTPUT_EXISTS: válassz új célmappát.')
    # All reads/validation complete before creating any output.
    if not paths: raise MigrationError('INVENTORY_INPUT: legalább egy XML szükséges.')
    if len({p.name for p in paths}) != len(paths): raise MigrationError('INVENTORY_DUPLICATE: azonos fájlnevek.')
    documents = [(p.name, read_xml(p)) for p in paths]
    data = collect(documents)
    import tempfile
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.inventory-', dir=output.parent) as tmp:
        stage = Path(tmp) / 'inventory'
        write_json(stage / 'inventory.json', data)
        write_json(stage / 'unused-attributes.json', data['unused_attributes'])
        write_json(stage / 'unused-property-elements.json', data['unused_property_elements'])
        def esc(v): return str(v).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')
        rows = ['# XML-attribútumleltár', '', 'A nevek az XML-ből származnak; nincs verziófüggetlen property-találgatás.', '',
                '| Elem | Attribútum | Darab | Feldolgozás | Példa |', '|---|---|---:|---|---|']
        rows += ['| ' + ' | '.join(esc(x[k]) for k in ['element','attribute','count','usage']) + ' | ' + esc(x['examples'][0]) + ' |' for x in data['attributes']]
        (stage / 'inventory.md').write_text('\n'.join(rows) + '\n', encoding='utf-8')
        from .discovery import build_map, write_map
        write_map(build_map(documents), stage)
        stage.rename(output)
    return data

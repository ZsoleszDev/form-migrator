"""Conservative Forms item classification; deliberately independent of the Java IR."""
from .xmlmodel import canonical

WIDGETS = ('text', 'textarea', 'number', 'date', 'datetime', 'password', 'checkbox',
           'select', 'multiselect', 'radio', 'autocomplete', 'button', 'display', 'image', 'tree', 'unsupported')
ITEM_TYPES = {
    'textitem': 'text', 'displayitem': 'display', 'checkbox': 'checkbox',
    'listitem': 'select', 'radiogroup': 'radio', 'pushbutton': 'button', 'image': 'image',
    'chartitem': 'unsupported', 'beanarea': 'unsupported', 'hierarchicaltree': 'unsupported',
    'ole': 'unsupported', 'activex': 'unsupported', 'vbx': 'unsupported',
    'oleactivexvbx': 'unsupported', 'olecontainer': 'unsupported',
    'sound': 'unsupported', 'userarea': 'unsupported',
}


def classify(properties, child_tags=()):
    p = {canonical(k): v for k, v in properties.items()}
    tags = {canonical(tag) for tag in child_tags}
    explicit = str(p.get('itemtype', '')).strip()
    result = {'item_type': explicit or None, 'type_source': 'explicit' if explicit else 'inferred',
              'widget': 'unsupported', 'reason': ''}
    if explicit:
        kind = ITEM_TYPES.get(canonical(explicit))
        if kind is None or kind == 'unsupported':
            result['reason'] = 'Nem támogatott ItemType: ' + explicit
            return result
        result['widget'] = kind
    else:
        signals = []
        if (('checkedvalue' in p or 'checkboxcheckedvalue' in p) and
                ('uncheckedvalue' in p or 'checkboxuncheckedvalue' in p)):
            signals.append(('checkbox', 'CheckedValue + UncheckedValue'))
        if 'radiobutton' in tags:
            signals.append(('radio', 'RadioButton gyerekelemek'))
        if 'listitemelement' in tags or str(p.get('liststyle', '')).strip():
            signals.append(('select', 'ListItemElement / ListStyle'))
        if str(p.get('concealdata', '')).casefold() in {'true', 'yes', '1'}:
            signals.append(('password', 'ConcealData=true'))
        if len(signals) != 1:
            result['reason'] = ('Többértelmű típusjelek: ' + ', '.join(x[1] for x in signals)
                                if signals else 'Hiányzó ItemType és nincs egyértelmű másodlagos típusjel.')
            return result
        result['widget'], result['reason'] = signals[0]
    return result

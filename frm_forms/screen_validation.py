"""Validation coverage shared by the screen emitter and migration notes."""

TEXT_WIDGETS = {'text', 'textarea', 'password', 'display', 'autocomplete'}
CONTROL_WIDGETS = TEXT_WIDGETS | {'number', 'date', 'datetime', 'checkbox', 'select', 'multiselect', 'radio'}
PROPERTIES = {
    'required': 'Required', 'maximum_length': 'MaximumLength', 'fixed_length': 'FixedLength',
    'minimum_length': 'MinLength (mezőhossz-JSON)',
    'minimum': 'LowestAllowedValue', 'maximum': 'HighestAllowedValue',
    'precision': 'Precision', 'scale': 'Scale', 'format_mask': 'FormatMask', 'case': 'CaseRestriction',
}


def validation_coverage(item, mode):
    """Every non-default rule has a destination or an explicit migration gap."""
    widget = item['widget']; rules = item['validation']; rows = []
    for key, prop in PROPERTIES.items():
        value = rules.get(key)
        if value is None or value == '' or key == 'required' and not value or key == 'case' and value == 'mixed':
            continue
        status = 'manual'; target = 'Nincs mezővezérlő: az adapterben kell érvényesíteni.'
        if mode == 'form' and widget in CONTROL_WIDGETS:
            if key == 'required':
                status = 'implemented'
                target = 'FormBlock binary checkbox: értéke mindig true vagy false.' if widget == 'checkbox' else 'FormBlock.validator.'
            elif key in {'maximum_length', 'fixed_length', 'minimum_length'} and widget in TEXT_WIDGETS:
                status = 'implemented'; target = 'FormBlock minLenght/maxLenght.'
            elif key in {'minimum', 'maximum'} and widget == 'number' and item['representation'] == 'safe-integer':
                status = 'implemented'; target = 'FormBlock min/max.'
            elif key in {'minimum', 'maximum', 'precision', 'scale'} and widget == 'number':
                target = 'A felületen nincs saját validátor (4.26): a backend ellenőrzi; szükség esetén a képernyőn kell felvenni.'
            elif key == 'case' and widget in TEXT_WIDGETS:
                status = 'partial'; target = 'FormBlock regexRule: kis-/nagybetű ellenőrzés. Automatikus betűátalakítás nincs.'
            elif key == 'format_mask' and widget in {'date', 'datetime'} and rules['date_format']:
                status = 'implemented'; target = 'FormBlock.dateFormat=' + rules['date_format'] + '; showTime=' + str(widget == 'datetime').lower() + '.'
                if widget == 'datetime':
                    status = 'partial'; target += ' A másodpercmezőt és az óraciklust a privát naptárkomponensben ellenőrizni kell.'
            else:
                target = 'Nincs ellenőrzött FormBlock-leképezés ehhez a vezérlőhöz; host adapter szükséges.'
        elif mode == 'table':
            target = 'Csak olvasható táblázat: a szabályt és formázást a szerkesztő/backend adapterben kell átvenni.'
        elif mode == 'hidden':
            target = 'Rejtett/technikai mező: a szabály a runtime/backend adapter feladata.'
        rows.append({'owner': item['owner'], 'property': prop, 'value': value, 'status': status, 'target': target})
    return rows


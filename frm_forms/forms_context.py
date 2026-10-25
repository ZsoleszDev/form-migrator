"""Fixed Forms context for Angular: there is no Enter Query or fetch mode."""

NORMAL_MODE = 'NORMAL'
NORMAL_MODE_NOTE = 'SYSTEM.MODE -> NORMAL (az Angular felület nem használ Forms keresési módokat).'


def normal_mode_reference(name: str) -> bool:
    """Match only SYSTEM.MODE, never other system, global or parameter values."""
    return name.upper().removeprefix(':') == 'SYSTEM.MODE'


def normal_mode_expression(node: dict) -> bool:
    """Recognise the bind and the literal NAME_IN('SYSTEM.MODE') getter."""
    if node['op'] == 'ref':
        return normal_mode_reference(node['name'])
    if node['op'] != 'function' or node['name'] != 'NAME_IN':
        return False
    args = node['args']
    return (len(args) == 1 and args[0]['op'] == 'literal' and args[0]['type'] == 'text'
            and isinstance(args[0]['value'], str) and normal_mode_reference(args[0]['value']))

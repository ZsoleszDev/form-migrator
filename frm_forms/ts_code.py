"""TypeScript literals of the generated screens: single quotes, one line per value, one entry per line in a record."""
from __future__ import annotations

import json
import re

from .angular_single import Code

IDENTIFIER = re.compile(r'[A-Za-z_$][\w$]*')


def sq(value) -> str:
    text = json.dumps(str(value), ensure_ascii=False)[1:-1].replace('\\"', '"').replace("'", "\\'")
    return "'" + text.replace('\u2028', '\\u2028').replace('\u2029', '\\u2029') + "'"


def key(name: str) -> str:
    # A computed key keeps __proto__ an own property instead of setting the prototype.
    if name == '__proto__':
        return "['__proto__']"
    return name if IDENTIFIER.fullmatch(name) else sq(name)


def member(name: str) -> str:
    """Property access in a template or in code: .NAME, or ['NAME'] for any other name."""
    return '.' + name if IDENTIFIER.fullmatch(name) and name != '__proto__' else '[' + sq(name) + ']'


def tsv(value) -> str:
    if isinstance(value, Code):
        return str(value)
    if isinstance(value, dict):
        parts = [('...' + str(v)) if k.startswith('...') else key(k) + ': ' + tsv(v) for k, v in value.items()]
        return '{ ' + ', '.join(parts) + ' }' if parts else '{}'
    if isinstance(value, (list, tuple)):
        return '[' + ', '.join(tsv(v) for v in value) + ']'
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return sq(value)


def record(entries: dict, indent: str = '    ') -> str:
    if not entries:
        return '{}'
    return '{\n' + ''.join(indent + key(k) + ': ' + tsv(v) + ',\n' for k, v in entries.items()) + indent[:-2] + '}'


def nested(entries: dict) -> str:
    """A record of arrays, one element per line (the FormBlock definitions of the regions)."""
    lines = []
    for name, items in entries.items():
        lines.append('    ' + key(name) + ': [')
        lines += ['      ' + tsv(item) + ',' for item in items]
        lines.append('    ],')
    return '{\n' + '\n'.join(lines) + '\n  }' if lines else '{}'

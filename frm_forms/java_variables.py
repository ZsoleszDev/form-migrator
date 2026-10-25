"""Variable map: the value of a variable the generated Java creates itself (java-variables.json).

The DPS ServiceImpl methods declare their own local variables: the developer inputs (a value the migrated code has
no source for: `String ibuKod = null;` with a TODO) and the screen values of the Java query buttons. An entry of the
map gives such a variable its value, by name:

    {"variableName": "ibuKod", "variableValue": "commonService.Details(param)",
     "autowired": "commonService", "import": "hu.company.pelda.CommonService"}

Where the generator declares ibuKod, it writes `String ibuKod = commonService.Details(param);` (the type stays the
generated one), the ServiceImpl gets `import hu.company.pelda.CommonService;` and, at the top of the class,
`@Autowired private CommonService commonService;` (the field type: autowired with a capital first letter). Only the
entries a ServiceImpl uses get there. The default file is java-variables.json in the migrator root; the
`java_variable_map` setting or FRM_JAVA_VARIABLE_MAP points elsewhere ("-": no map).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .common import MigrationError

DEFAULT_FILE = 'java-variables.json'
NAME = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*')
QUALIFIED = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+')
KEYS = {'variableName', 'variableValue', 'autowired', 'import'}
JAVA_KEYWORDS = set('''abstract assert boolean break byte case catch char class const continue default do double else
    enum extends final finally float for goto if implements import instanceof int interface long native new package
    private protected public return short static strictfp super switch synchronized this throw throws transient try
    void volatile while var record yield true false null'''.split())
# Fields the generated ServiceImpl already has: an autowired service never replaces them.
TAKEN_FIELDS = {'jdbc', 'log', 'MODULE_REVIEWED'}

# The map of the running migration (configure): {variableName: entry}.
ACTIVE: dict = {}
# The generated files that use an entry: {file: [variableName, ...]} (class_parts; for analysis/java-variables.json).
APPLIED: dict = {}


def default_path() -> Path:
    return Path(__file__).resolve().parents[1] / DEFAULT_FILE


def path(config) -> Path | None:
    """The map file of the run: the java_variable_map setting, then FRM_JAVA_VARIABLE_MAP, then the default file."""
    configured = (config.get('java_variable_map') or os.environ.get('FRM_JAVA_VARIABLE_MAP', '')).strip()
    if configured == '-':
        return None
    result = Path(configured) if configured else default_path()
    if not result.is_file():
        if configured:
            raise MigrationError(f'JAVA_VARIABLE_MAP: a fájl nem található: {result}')
        return None
    return result


def load(config) -> dict:
    """{variableName: entry}; empty when the default file does not exist or the map is off ("-")."""
    source = path(config)
    if source is None:
        return {}
    try:
        data = json.loads(source.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise MigrationError(f'JAVA_VARIABLE_MAP: hibás JSON ({source}): {exc}') from exc
    return parse(data, source)


def parse(data, source='java-variables.json') -> dict:
    """A list of entries, one entry, or {"variables": [...]}; keys starting with _ are comments."""
    if isinstance(data, dict) and 'variableName' not in data:
        unknown = sorted(key for key in data if key != 'variables' and not key.startswith('_'))
        if unknown:
            raise MigrationError(f'JAVA_VARIABLE_MAP: ismeretlen kulcs: {unknown} (variables, vagy _ kezdetű megjegyzés) ({source}).')
        data = data.get('variables')
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise MigrationError(f'JAVA_VARIABLE_MAP: [{{"variableName": ..., "variableValue": ...}}, ...] lista szükséges ({source}).')
    result = {}
    for raw in data:
        entry = entry_of(raw)
        if entry['name'] in result:
            raise MigrationError(f'JAVA_VARIABLE_MAP: a(z) "{entry["name"]}" változó kétszer szerepel ({source}).')
        result[entry['name']] = entry
    return result


def entry_of(raw) -> dict:
    if not isinstance(raw, dict):
        raise MigrationError(f'JAVA_VARIABLE_MAP: hibás bejegyzés (objektum szükséges): {raw!r}.')
    raw = {key: value for key, value in raw.items() if not key.startswith('_')}
    unknown = set(raw) - KEYS
    label = raw.get('variableName', '?')
    if unknown:
        raise MigrationError(f'JAVA_VARIABLE_MAP: ismeretlen kulcs a(z) "{label}" bejegyzésben: {sorted(unknown)} '
                             '(variableName, variableValue, autowired, import).')
    name = raw.get('variableName')
    if not isinstance(name, str) or not NAME.fullmatch(name.strip()) or name.strip() in JAVA_KEYWORDS:
        raise MigrationError(f'JAVA_VARIABLE_MAP: a variableName Java-változónév legyen (például ibuKod): {name!r}.')
    name = name.strip()
    value = raw.get('variableValue')
    value = value.strip().rstrip(';').rstrip() if isinstance(value, str) else value
    if not isinstance(value, str) or not value or '\n' in value:
        raise MigrationError(f'JAVA_VARIABLE_MAP: "{name}": a variableValue egysoros Java-kifejezés legyen '
                             f'(például commonService.Details(param)): {raw.get("variableValue")!r}.')
    autowired = raw.get('autowired')
    autowired = autowired.strip() if isinstance(autowired, str) else autowired
    if autowired in (None, ''):
        autowired = None
    elif not isinstance(autowired, str) or not NAME.fullmatch(autowired) or autowired in JAVA_KEYWORDS | TAKEN_FIELDS:
        raise MigrationError(f'JAVA_VARIABLE_MAP: "{name}": az autowired egy mező neve legyen (például commonService): {autowired!r}.')
    imports = raw.get('import')
    imports = [] if imports in (None, '') else [imports] if isinstance(imports, str) else imports
    if not isinstance(imports, list) or not all(isinstance(i, str) and QUALIFIED.fullmatch(i.strip().removeprefix('import ').rstrip(';').strip())
                                                for i in imports):
        raise MigrationError(f'JAVA_VARIABLE_MAP: "{name}": az import teljes osztálynév legyen '
                             f'(például hu.company.pelda.CommonService): {raw.get("import")!r}.')
    imports = [i.strip().removeprefix('import ').rstrip(';').strip() for i in imports]
    return {'name': name, 'value': value, 'autowired': autowired,
            'type': autowired[:1].upper() + autowired[1:] if autowired else None, 'imports': imports}


def configure(entries: dict) -> None:
    """The map of the running migration (cli.migration: before the analysis, which writes data-trigger Java)."""
    ACTIVE.clear()
    ACTIVE.update(entries)
    APPLIED.clear()


def lookup(variable: str) -> dict | None:
    return ACTIVE.get(variable)


def declaration(java_type: str, variable: str, entry: dict) -> str:
    return f'{java_type} {variable} = {entry["value"]};'


def used(text: str) -> list[dict]:
    """The entries whose declaration the Java text has (`<type> name = value;`), in map order."""
    return [entry for entry in ACTIVE.values()
            if re.search(r'[\w>\]] ' + re.escape(entry['name']) + ' = ' + re.escape(entry['value']) + ';', text)]


def class_parts(text: str, existing: str = '', file: str = '') -> tuple[str, str]:
    """(imports, fields) of the entries a class uses: `import x.Y;` lines and the @Autowired fields of the class top.
    A field the class already declares (existing) is not repeated."""
    entries = used(text)
    if file and entries:
        APPLIED[file] = [e['name'] for e in entries]
    imports = ''.join(f'import {name};\n' for name in dict.fromkeys(i for e in entries for i in e['imports']))
    fields, seen = [], set()
    for entry in entries:
        field = entry['autowired']
        if not field or field in seen or re.search(r'\b' + re.escape(field) + r'\s*;', existing):
            continue
        seen.add(field)
        fields.append(f'    @Autowired\n    private {entry["type"]} {field};\n')
    return imports, ''.join(fields)


def report() -> dict:
    """analysis/java-variables.json: every entry of the map and the generated files that use it."""
    return {'variables': [{'variableName': e['name'], 'variableValue': e['value'], 'autowired': e['autowired'],
                           'autowiredType': e['type'], 'import': e['imports'],
                           'used_in': [file for file, names in APPLIED.items() if e['name'] in names]} for e in ACTIVE.values()]}

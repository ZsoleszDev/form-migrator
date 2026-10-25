"""Import map: simple Java class name -> fully qualified name (java-imports.json).

Every generated backend file that uses a mapped name gets `import <fully qualified name>;`.
The map is read at every generation, so an edited path applies to the next generated
module. A mapped name is authoritative: it replaces the generator's own import for
that name. The default file is java-imports.json in the migrator root; the
`java_import_map` setting or FRM_JAVA_IMPORT_MAP points elsewhere ("-": no map).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .common import MigrationError

DEFAULT_FILE = 'java-imports.json'
NAME = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*')
QUALIFIED = re.compile(r'[a-z_][a-z0-9_]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+')


def cl_package(config, package: str) -> str:
    """The module's CL package: the cl_package setting ({module} = the module name), else <package>.cl."""
    return layer_package(config, package, 'CL')


def layer_package(config, package: str, layer: str) -> str:
    """The module's package in a layer: the cl_package / dps_package / wbs_package setting ({module} = the module
    name; set from the chosen project folders), else <package>.cl / .dps / .wbs."""
    value = (config.get(layer.lower() + '_package') or '').strip()
    return value.replace('{module}', package.rsplit('.', 1)[-1]) if value else package + '.' + layer.lower()


def default_path() -> Path:
    return Path(__file__).resolve().parents[1] / DEFAULT_FILE


TS_PATH = re.compile(r'[@.\w][\w@.~/-]*')


def entries(config) -> dict:
    """The raw map {name: value}; empty when the default file does not exist or the map is off ("-")."""
    # Order: the java_import_map setting, then FRM_JAVA_IMPORT_MAP, then <migrator>/java-imports.json; "-" = no map.
    configured = (config.get('java_import_map') or os.environ.get('FRM_JAVA_IMPORT_MAP', '')).strip()
    if configured == '-':
        return {}
    path = Path(configured) if configured else default_path()
    if not path.is_file():
        if configured:
            raise MigrationError(f'JAVA_IMPORT_MAP: a fájl nem található: {path}')
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise MigrationError(f'JAVA_IMPORT_MAP: hibás JSON ({path}): {exc}') from exc
    result = data.get('imports', data) if isinstance(data, dict) else None
    if not isinstance(result, dict):
        raise MigrationError(f'JAVA_IMPORT_MAP: {{"Osztálynév": "teljes.csomag.Osztálynév"}} objektum szükséges ({path}).')
    return {name: value for name, value in result.items() if not name.startswith(('_', '$'))}  # "_…": comments


# A Java class name: lower-case packages, then the capitalised class (hu.ff.xy.RestResponseDto).
JAVA_LIKE = re.compile(r'[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*\.[A-Z][A-Za-z0-9_$]*')


def is_ts_path(value: str) -> bool:
    """Angular import: anything that is not a dotted Java class name - wf-package, @ff/ui, src/app/core/wff."""
    return not JAVA_LIKE.fullmatch(value)


def split(name: str, value) -> tuple[str | None, str | None]:
    """(java, ts) of one entry: a string is one of them, {"java": ..., "ts": ...} may give both."""
    if not NAME.fullmatch(name):
        raise MigrationError(f'JAVA_IMPORT_MAP: hibás név: "{name}".')
    if isinstance(value, dict) and value and not set(value) - {'java', 'ts'}:
        java, ts = value.get('java'), value.get('ts')
    elif isinstance(value, str):
        java, ts = (None, value) if is_ts_path(value.strip()) else (value, None)
    else:
        raise MigrationError(f'JAVA_IMPORT_MAP: hibás bejegyzés: "{name}": {value!r}.')
    if java is not None:
        java = java.strip() if isinstance(java, str) else java
        if not isinstance(java, str) or not QUALIFIED.fullmatch(java):
            raise MigrationError(f'JAVA_IMPORT_MAP: hibás Java-név: "{name}": {java!r}.')
        if java.rsplit('.', 1)[1] != name:
            raise MigrationError(f'JAVA_IMPORT_MAP: "{name}" értéke ugyanazzal a névvel végződjön (például valami.csomag.{name}): {java}')
    if ts is not None:
        ts = ts.strip() if isinstance(ts, str) else ts
        if not isinstance(ts, str) or not TS_PATH.fullmatch(ts) or ts.endswith('.ts'):
            raise MigrationError(f'JAVA_IMPORT_MAP: hibás Angular-import: "{name}": {ts!r} '
                                 '(például wf-package, @ff/ui vagy src/app/core/wff, .ts nélkül).')
    return java, ts


def load(config) -> dict:
    """Java: {name: fully qualified name}."""
    return {name: java for name, value in entries(config).items() for java, _ in [split(name, value)] if java}


def load_ts(config) -> dict:
    """TypeScript: {name: Angular import} - every entry that is not a dotted Java class name."""
    return {name: ts for name, value in entries(config).items() for _, ts in [split(name, value)] if ts}

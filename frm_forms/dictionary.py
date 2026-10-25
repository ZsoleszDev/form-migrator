"""Oracle data dictionary: SQL export for the DBA, import into schema.json.

dictionary-sql   collects the tables and external routines that the generated
                 modules use and writes one SQL*Plus script. It needs no JSON
                 support in the database: every row is a pipe-separated line.
dictionary-import turns the spooled lines into the schema.json "tables" and
                 "procedures" sections. Existing (reviewed) entries always win.

The generator stays offline: only the DBA's script touches the database.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re

from .common import MigrationError, read_json, write_json

NAME = re.compile(r'[A-Z][A-Z0-9_$#]{0,127}')
CHUNK = 200


def output_folders(paths: list[Path]) -> list[Path]:
    """migrate outputs, or a batch folder whose subfolders are migrate outputs."""
    found = []
    for path in paths:
        if (path / 'analysis/form.ir.json').is_file():
            found.append(path)
        elif path.is_dir():
            found += sorted(d for d in path.iterdir() if (d / 'analysis/form.ir.json').is_file())
        else:
            raise MigrationError('DICTIONARY: nem migrate/batch kimeneti mappa: ' + str(path))
    if not found:
        raise MigrationError('DICTIONARY: nem található analysis/form.ir.json.')
    return found


def references(folders: list[Path], prefixes: tuple) -> tuple[set, set]:
    """(tables, routines) referenced by the generated modules, as upper-case names."""
    tables, routines = set(), set()
    for folder in folders:
        model = read_json(folder / 'analysis/form.ir.json')
        tables |= {b['table'].strip().upper() for b in model.get('blocks', []) if b.get('database') and b.get('table')}
        discovery = folder / 'analysis/discovery/form-map.json'
        if discovery.is_file():
            for code in read_json(discovery).get('code', []):
                if code.get('module_kind') != 'formmodule':
                    continue
                from .schema_workbench import table_references
                tables |= {ref['table'] for ref in table_references(code.get('source_view', code.get('source', '')))['tables']
                           if ref['operation'] not in {'%TYPE', '%ROWTYPE'}}
                for call in code.get('calls', []):
                    name = call.get('name', '').upper()
                    if call.get('kind') in {'external_candidate', 'unresolved'} and not name.lower().startswith(prefixes):
                        routines.add(name)
    return tables, routines


def valid(name: str) -> bool:
    return all(NAME.fullmatch(part) for part in name.split('.')) and 1 <= name.count('.') + 1 <= 3


def quoted(value: str) -> str:
    return "'" + value + "'"


def chunks(items: list, size: int = CHUNK):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def dictionary_sql(tables: set, routines: set, spool: str = 'dictionary.txt') -> tuple[str, list[str]]:
    skipped = sorted(n for n in tables | routines if not valid(n))
    tables = sorted(n for n in tables if valid(n) and n.count('.') <= 1)
    routines = sorted(n for n in routines if valid(n))
    lines = ['-- FRM Forms Migrator: adatszótár-export (csak olvasás: ALL_TAB_COLUMNS, ALL_CONSTRAINTS, ALL_ARGUMENTS).',
             '-- Futtatás annak a sémának a felhasználójával, amellyel a formok futnak:',
             '--   sqlplus -s felhasznalo/jelszo@adatbazis @dictionary.sql',
             '-- A kimenet (' + spool + ') a dictionary-import parancs bemenete.',
             f'-- {len(tables)} tábla/nézet, {len(routines)} rutin.',
             'SET PAGESIZE 0', 'SET LINESIZE 4000', 'SET TRIMSPOOL ON', 'SET FEEDBACK OFF', 'SET HEADING OFF',
             'SET VERIFY OFF', 'SET ECHO OFF', 'SET TERMOUT OFF', 'SPOOL ' + spool]
    def table_filter(alias):
        bare = [t for t in tables if '.' not in t]
        owned = [t.split('.') for t in tables if '.' in t]
        parts = [f"{alias}.table_name IN ({', '.join(map(quoted, part))})" for part in chunks(bare)]
        parts += [f"({alias}.owner, {alias}.table_name) IN ({', '.join('(' + quoted(o) + ', ' + quoted(t) + ')' for o, t in part)})"
                  for part in chunks(owned)]
        return ' OR '.join(parts)
    if tables:
        lines += ["SELECT 'COL|' || c.owner || '|' || c.table_name || '|' || c.column_name || '|' || c.data_type || '|' ||",
                  "       c.data_length || '|' || c.data_precision || '|' || c.data_scale || '|' || c.nullable || '|' || c.column_id",
                  '  FROM all_tab_columns c', ' WHERE ' + table_filter('c'), ' ORDER BY c.owner, c.table_name, c.column_id;',
                  "SELECT 'PK|' || k.owner || '|' || k.table_name || '|' || cc.column_name || '|' || cc.position",
                  '  FROM all_constraints k', '  JOIN all_cons_columns cc ON cc.owner = k.owner AND cc.constraint_name = k.constraint_name',
                  " WHERE k.constraint_type = 'P' AND (" + table_filter('k') + ')',
                  ' ORDER BY k.owner, k.table_name, cc.position;']
    for part in chunks(routines):
        conditions = []
        for name in part:
            pieces = name.split('.')
            if len(pieces) == 3:
                conditions.append(f"(a.owner = {quoted(pieces[0])} AND a.package_name = {quoted(pieces[1])} AND a.object_name = {quoted(pieces[2])})")
            elif len(pieces) == 2:
                # PKG.PROC or OWNER.PROC: both readings, the import keeps what exists.
                conditions.append(f"(a.package_name = {quoted(pieces[0])} AND a.object_name = {quoted(pieces[1])})")
                conditions.append(f"(a.owner = {quoted(pieces[0])} AND a.package_name IS NULL AND a.object_name = {quoted(pieces[1])})")
            else:
                conditions.append(f"(a.package_name IS NULL AND a.object_name = {quoted(pieces[0])})")
        lines += ["SELECT 'ARG|' || a.owner || '|' || NVL(a.package_name, '-') || '|' || a.object_name || '|' || NVL(a.overload, '0') || '|' ||",
                  "       a.position || '|' || NVL(a.argument_name, '-') || '|' || a.in_out || '|' || a.data_type || '|' || a.defaulted || '|' || a.data_level",
                  '  FROM all_arguments a', ' WHERE a.data_level = 0 AND (' + '\n    OR '.join(conditions) + ')',
                  ' ORDER BY a.owner, a.package_name, a.object_name, a.overload, a.position;']
    lines += ['SPOOL OFF', 'EXIT', '']
    return '\n'.join(lines), skipped


def db_type(data_type: str) -> str:
    t = data_type.strip().upper()
    if t in {'NUMBER', 'FLOAT', 'INTEGER', 'BINARY_FLOAT', 'BINARY_DOUBLE', 'PLS_INTEGER', 'BINARY_INTEGER', 'NATURAL', 'POSITIVE'}:
        return 'number'
    if t in {'VARCHAR2', 'VARCHAR', 'CHAR', 'NVARCHAR2', 'NCHAR', 'LONG', 'CLOB', 'NCLOB'}:
        return 'text'
    if t == 'DATE' or re.fullmatch(r'TIMESTAMP(\(\d\))?', t):
        return 'datetime'
    return 'unsupported'


def dictionary_import(text: str) -> tuple[dict, dict]:
    """Spooled lines -> ({'tables', 'procedures'}, report)."""
    columns, keys, arguments = defaultdict(dict), defaultdict(list), defaultdict(lambda: defaultdict(list))
    for raw in text.splitlines():
        parts = [p.strip() for p in raw.strip().split('|')]
        if parts[0] == 'COL' and len(parts) == 10:
            _, owner, table, column, data_type, length, precision, scale, nullable, _ = parts
            entry = {'type': db_type(data_type), 'data_type': data_type, 'nullable': nullable == 'Y'}
            if entry['type'] == 'text' and length.isdigit():
                entry['max_length'] = int(length)
            if precision.isdigit():
                entry['precision'] = int(precision)
                entry['scale'] = int(scale) if scale.isdigit() else 0
            columns[owner + '.' + table][column] = entry
        elif parts[0] == 'PK' and len(parts) == 5:
            _, owner, table, column, position = parts
            keys[owner + '.' + table].append((int(position) if position.isdigit() else 0, column))
        elif parts[0] == 'ARG' and len(parts) == 11:
            _, owner, package, obj, overload, position, name, mode, data_type, defaulted, _ = parts
            routine = '.'.join(p for p in (owner, None if package == '-' else package, obj) if p)
            arguments[routine][overload].append({'position': int(position) if position.lstrip('-').isdigit() else 0,
                                                 'name': '' if name == '-' else name, 'mode': mode.replace('/', ' '),
                                                 'data_type': data_type, 'default': defaulted == 'Y'})
    tables = {}
    for table in sorted(set(columns) | set(keys)):
        tables[table] = {'primary_key': [c for _, c in sorted(keys.get(table, []))], 'columns': columns.get(table, {})}
    procedures, report = {}, {'overloaded': [], 'ambiguous': [], 'unsupported_types': []}
    by_short = defaultdict(list)
    for routine, overloads in sorted(arguments.items()):
        if len(overloads) > 1:
            report['overloaded'].append(routine)
            continue  # The call site cannot pick an overload without full type inference.
        rows = sorted(next(iter(overloads.values())), key=lambda r: r['position'])
        returns = next((r for r in rows if r['position'] == 0), None)
        params = [r for r in rows if r['position'] > 0 and (r['name'] or r['data_type'])]
        entry = {'kind': 'function' if returns else 'procedure', 'source': 'dictionary:' + routine,
                 'arguments': [{'name': p['name'], 'mode': p['mode'], 'type': db_type(p['data_type']), 'default': p['default']}
                               for p in params]}
        if returns:
            entry['returns'] = db_type(returns['data_type'])
        if any(a['type'] == 'unsupported' for a in entry['arguments']) or entry.get('returns') == 'unsupported':
            report['unsupported_types'].append(routine)  # kept: the compiler refuses it with the reason
        procedures[routine] = entry
        by_short[routine.split('.', 1)[1]].append(routine)
    for short, full in sorted(by_short.items()):
        if len(full) == 1:
            procedures.setdefault(short, procedures[full[0]])
        else:
            report['ambiguous'].append(short)  # same PKG.PROC in several schemas: only OWNER.PKG.PROC
    return {'tables': tables, 'procedures': procedures}, report


def run_sql(args) -> int:
    from . import framework
    from .portfolio import catalog_prefixes
    prefixes = catalog_prefixes(args) if getattr(args, 'config', None) else framework.load({})['call_prefixes']
    tables, routines = references(output_folders(args.outputs), prefixes)
    script, skipped = dictionary_sql(tables, routines, args.spool)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(script, encoding='utf-8')
    print(f'Adatszótár-szkript: {args.out} ({len(tables)} tábla, {len(routines)} rutin'
          + (f'; érvénytelen név miatt kihagyva: {", ".join(skipped)}' if skipped else '') + ').')
    print(f'Futtatás után: python -m frm_forms dictionary-import {args.spool} --out schema.json')
    return 0


def run_import(args) -> int:
    imported, report = dictionary_import(args.export.read_text(encoding='utf-8', errors='replace'))
    base = read_json(args.merge) if args.merge else {}
    if set(base) - {'blocks', 'procedures', 'tables'}:
        raise MigrationError('DICTIONARY: a --merge fájl csak blocks, procedures és tables kulcsot tartalmazhat.')
    result = {'blocks': base.get('blocks', {})} if base.get('blocks') else {}
    for section in ('tables', 'procedures'):
        # Reviewed, hand-written entries win over the dictionary.
        result[section] = {**imported[section], **base.get(section, {})}
    write_json(args.out, result)
    print(f"schema.json: {args.out} — {len(result['tables'])} tábla, {len(result['procedures'])} rutinnév.")
    for key, label in [('overloaded', 'túlterhelt, kimarad'), ('ambiguous', 'több sémában is létezik, csak OWNER.-rel hívható'),
                       ('unsupported_types', 'nem támogatott paramétertípus, a hívás tiltott marad')]:
        if report[key]:
            print(f'  {label}: ' + ', '.join(report[key]))
    return 0

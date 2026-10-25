"""Attached Forms PL/SQL libraries: the program units of a .pld (or a .pll converted to .pld).

Forms resolves a name in this order: the form's own program units, the attached libraries
(in attach order), then the database. Without the library source every library call went to
the database and failed there at run time (PLS-00201), or a Forms-only routine looked like a
database call. With the .pld text, the library units the form actually reaches become program
units of the model, so they are inlined, emulated or refused exactly like the form's own units.

Only reachable units are added: unused library code never blocks an endpoint.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import time
from pathlib import Path

from .common import MigrationError, decode_line_escapes
from .plsql import Unsupported

DIRECTIVE = re.compile(r'^[ \t]*\.([A-Za-z]+)\b(.*)$', re.M)
ATTACH = re.compile(r'\bLIBRARY\s+("?)([A-Za-z0-9_$#.\\/:-]+)\1', re.I)
SUFFIXES = {'.pld', '.sql', '.pll'}


class LibraryError(MigrationError):
    pass


def library_name(path: Path) -> str:
    """QMSLIB65 for qmslib65.pld, qmslib65.pll or C:\\forms\\qmslib65.pll (attached library names)."""
    return re.split(r'[\\/]', str(path))[-1].rsplit('.', 1)[0].upper()


def decode(raw: bytes, encoding: str = '') -> str:
    """A .pld comes from a Windows Forms installation: UTF-8 when it is, else the configured or cp1250 code page."""
    if encoding:
        return raw.decode(encoding)
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return raw.decode('cp1250')


def parse_pld(text: str, name: str) -> dict:
    """{'name', 'units': {NAME: {'kind', 'text', 'spec', 'body'}}, 'attached': [...], 'directives': [...]}.

    The .pld is PL/SQL source, one program unit after the other, with '.attach LIBRARY X ...' lines.
    Fail-closed: a top-level element that is not a procedure, function or package rejects the file.
    """
    from .plsql_passthrough import scan, significant
    text = decode_line_escapes(text).replace('\r\n', '\n').replace('\r', '\n')
    attached, directives = [], []

    def directive(match):
        word = match.group(1).lower()
        if word == 'attach':
            found = ATTACH.search(match.group(2))
            if found:
                attached.append(library_name(Path(found.group(2))))
        else:
            directives.append('.' + match.group(1) + match.group(2).rstrip())
        return ' ' * len(match.group(0))  # keep offsets and line numbers

    text = DIRECTIVE.sub(directive, text)
    try:
        sig = significant(scan(text))
    except Unsupported as exc:
        raise LibraryError(f'PLD ({name}): {exc}') from exc
    units = {}
    i, n = 0, len(sig)
    while i < n:
        if sig[i][1] in {'/', ';'}:
            i += 1
            continue
        start = i
        if upper(sig, i) == 'CREATE':
            i += 1
            if upper(sig, i) == 'OR' and upper(sig, i + 1) == 'REPLACE':
                i += 2
            if upper(sig, i) in {'EDITIONABLE', 'NONEDITIONABLE'}:
                i += 1
        word = upper(sig, i)
        if word not in {'PROCEDURE', 'FUNCTION', 'PACKAGE'}:
            raise LibraryError(f'PLD ({name}): a {line(text, sig[start][2])}. sorban nem programegység kezdődik: '
                               + ' '.join(t[1] for t in sig[start:start + 4]))
        body = word == 'PACKAGE' and upper(sig, i + 1) == 'BODY'
        name_at = i + (2 if body else 1)
        unit_name = qualified_name(sig, name_at)
        if not unit_name:
            raise LibraryError(f'PLD ({name}): névtelen programegység a {line(text, sig[i][2])}. sorban.')
        try:
            end = (package_end(sig, i, spec=not body) if word == 'PACKAGE' else subprogram_end(sig, i))
        except Unsupported as exc:
            raise LibraryError(f'PLD ({name}): {unit_name} ({line(text, sig[i][2])}. sor): {exc}') from exc
        source = text[sig[i][2]:sig[end][3]].strip()
        if word == 'PACKAGE':
            entry = units.setdefault(unit_name, {'kind': 'package', 'text': '', 'spec': '', 'body': ''})
            if entry['kind'] != 'package':
                raise LibraryError(f'PLD ({name}): {unit_name} eljárásként és csomagként is szerepel.')
            part = 'body' if body else 'spec'
            if entry[part]:
                raise LibraryError(f'PLD ({name}): ismétlődő csomag{"törzs" if body else "fej"}: {unit_name}')
            entry[part] = source
            entry['text'] = (entry['spec'] + '\n' + entry['body']).strip()
        else:
            if unit_name in units:
                raise LibraryError(f'PLD ({name}): ismétlődő programegység: {unit_name}')
            units[unit_name] = {'kind': word.lower(), 'text': source}
        i = end + 1
    return {'name': name, 'units': units, 'attached': attached, 'directives': directives}


def upper(sig, index):
    return sig[index][1].upper() if index < len(sig) and sig[index][0] == 'ident' else (sig[index][1] if index < len(sig) else '')


def line(text, offset):
    return text.count('\n', 0, offset) + 1


def qualified_name(sig, index):
    """OWNER.NAME or NAME starting at sig[index]: the last part, without quotes."""
    if index >= len(sig) or sig[index][0] != 'ident':
        return ''
    parts = [sig[index][1]]
    while index + 2 < len(sig) and sig[index + 1][1] == '.' and sig[index + 2][0] == 'ident':
        index += 2
        parts.append(sig[index][1])
    return parts[-1].strip('"').upper()


def header_end(sig, i):
    """The IS/AS (or ';' of a forward declaration) that ends the header starting at sig[i]."""
    depth = 0
    for k in range(i + 1, len(sig)):
        value = upper(sig, k)
        if value == '(':
            depth += 1
        elif value == ')':
            depth -= 1
        elif depth == 0 and value in {'IS', 'AS', ';'}:
            return k
    raise Unsupported('lezáratlan fejléc')


def statement_end(sig, i):
    depth = 0
    for k in range(i, len(sig)):
        value = sig[k][1]
        if value == '(':
            depth += 1
        elif value == ')':
            depth -= 1
        elif value == ';' and depth == 0:
            return k
    raise Unsupported('lezáratlan deklaráció')


def declarations(sig, i):
    """Skip a declaration section from sig[i]: the index of the BEGIN or END that ends it."""
    while i < len(sig):
        word = upper(sig, i)
        if word in {'BEGIN', 'END'}:
            return i
        if word in {'PROCEDURE', 'FUNCTION'}:
            i = subprogram_end(sig, i) + 1
            continue
        i = statement_end(sig, i) + 1
    raise Unsupported('a deklarációs részt nem zárja BEGIN vagy END')


def subprogram_end(sig, i):
    """The ';' that ends the procedure or function starting at sig[i] (forward declarations included)."""
    head = header_end(sig, i)
    if sig[head][1] == ';':
        return head
    if upper(sig, head + 1) in {'LANGUAGE', 'EXTERNAL'}:
        return statement_end(sig, head + 1)
    start = declarations(sig, head + 1)
    if upper(sig, start) != 'BEGIN':
        raise Unsupported('alprogram BEGIN nélkül')
    return block_end(sig, start)


def block_end(sig, i):
    """The ';' after the END that closes the BEGIN at sig[i]."""
    stack = []
    k = i
    while k < len(sig):
        word = upper(sig, k)
        if word in {'BEGIN', 'IF', 'LOOP', 'CASE'}:
            stack.append(word)
        elif word == 'END':
            if not stack:
                raise Unsupported('felesleges END')
            stack.pop()
            if upper(sig, k + 1) in {'IF', 'LOOP', 'CASE'}:
                k += 1
            elif not stack:
                return statement_end(sig, k)
        k += 1
    raise Unsupported('lezáratlan BEGIN')


def package_end(sig, i, spec):
    head = header_end(sig, i)
    if sig[head][1] == ';':
        raise Unsupported('csomag IS/AS nélkül')
    k = declarations(sig, head + 1)
    if upper(sig, k) == 'BEGIN':
        if spec:
            raise Unsupported('BEGIN a csomag specifikációjában')
        return block_end(sig, k)  # the initialisation part shares the package's END
    return statement_end(sig, k)


def export_pll(source: Path, destination: Path, config: dict) -> Path:
    """Convert a binary .pll to .pld with Forms Compiler (script=YES); never decode a .pll as text."""
    command = config.get('library_export_command')
    if command is None:
        executable = next((shutil.which(name) for name in ('frmcmp_batch', 'frmcmp_batch.sh', 'frmcmp', 'frmcmp.exe')
                           if shutil.which(name)), None)
        if not executable:
            raise LibraryError('A .pll bináris. Nem található frmcmp_batch/frmcmp: alakítsd .pld-vé Forms környezetben '
                               '(frmcmp_batch module=KONYVTAR.pll module_type=LIBRARY script=YES), vagy állítsd be a '
                               'library_export_command argumentumlistát a config.json-ban.')
        command = [executable, 'module={input}', 'module_type=LIBRARY', 'script=YES']
    if not isinstance(command, list) or not command or not all(isinstance(p, str) for p in command):
        raise LibraryError('library_export_command: nem üres JSON string-lista kell, shell parancslánc helyett.')
    if not any('{input}' in part for part in command):
        raise LibraryError('library_export_command: hiányzik az {input} helyőrző.')
    destination.mkdir(parents=True, exist_ok=True)
    copy = destination / source.name  # Forms Compiler writes the .pld next to its input
    shutil.copyfile(source, copy)
    args = [p.replace('{input}', str(copy.resolve())).replace('{output_dir}', str(destination.resolve())) for p in command]
    started = time.time()
    try:
        proc = subprocess.run(args, cwd=destination, capture_output=True, timeout=config.get('export_timeout_seconds', 180), shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LibraryError(f'A könyvtár-export nem futott le: {exc}') from exc
    log = proc.stdout.decode('utf-8', 'replace') + proc.stderr.decode('utf-8', 'replace')
    (destination / (source.stem + '.export.log')).write_text(log, encoding='utf-8')
    if proc.returncode:
        raise LibraryError(f'Könyvtár-export hibakód: {proc.returncode}. Kimenet vége:\n{log[-3000:]}')
    for candidate in destination.iterdir():
        if candidate.is_file() and candidate.name.lower() == source.stem.lower() + '.pld' and candidate.stat().st_mtime >= started - 1:
            return candidate
    raise LibraryError(f'A könyvtár-export nem adott {source.stem}.pld fájlt. {log[-1500:]}')


def load_libraries(paths, config: dict, stage: Path | None = None) -> list[dict]:
    """Every given library: parsed units, file name and SHA256. Duplicate library names are an error."""
    result, seen = [], {}
    for path in paths or []:
        path = Path(path)
        if not path.is_file() or path.suffix.lower() not in SUFFIXES:
            raise LibraryError(f'--pld: létező .pld (vagy .pll) fájl kell: {path}')
        name = library_name(path)
        if name in seen:
            raise LibraryError(f'--pld: a(z) {name} könyvtár kétszer szerepel ({seen[name]}, {path.name}).')
        seen[name] = path.name
        text_path = path
        if path.suffix.lower() == '.pll':
            if stage is None:
                raise LibraryError('A .pll exportjához munkamappa szükséges.')
            text_path = export_pll(path, stage / 'library-export', config)
        raw = text_path.read_bytes()
        parsed = parse_pld(decode(raw, config.get('pld_encoding', '')), name)
        result.append({**parsed, 'file': path.name, 'sha256': hashlib.sha256(raw).hexdigest()})
    return result


def identifiers(text: str) -> set:
    from .plsql_passthrough import scan
    text = decode_line_escapes(text or '')
    try:
        return {t[1].upper() for t in scan(text) if t[0] == 'ident'}
    except Unsupported:
        return {w.upper() for w in re.findall(r'[A-Za-z][A-Za-z0-9_$#]*', text)}


def attach(model: dict, libraries: list[dict], catalog: dict) -> dict:
    """Add the library units the form reaches to model['program_units']; returns analysis/libraries.json.

    Scope: the form's attached libraries in attach order, then the libraries they attach. A unit the
    form defines itself wins, then the first library that defines the name. Catalogued framework
    routines stay plumbing (framework_catalog), they are never inlined from the library.
    """
    from .framework import framework_name
    from .xmlmodel import get
    by_name = {lib['name']: lib for lib in libraries}
    attached = [library_name(Path(get(p, 'Name') or get(p, 'LibraryLocation'))) for p in model.get('libraries', [])]
    order, pending = [], list(attached)
    while pending:
        name = pending.pop(0)
        if name in order:
            continue
        order.append(name)
        if name in by_name:
            pending += by_name[name]['attached']
    own = {get(u, 'Name').upper() for u in model['program_units']}
    index = {}
    for name in order:
        for unit_name, unit in by_name.get(name, {}).get('units', {}).items():
            if unit_name not in own and unit_name not in index:
                index[unit_name] = (name, unit)
    reached, pending = [], [t['source'] for t in model['triggers']] + [get(u, 'ProgramUnitText') for u in model['program_units']]
    while pending:
        for word in identifiers(pending.pop()):
            if word in index and word not in reached and not framework_name(word, catalog):
                reached.append(word)
                pending.append(index[word][1]['text'])
    for unit_name in reached:
        library, unit = index[unit_name]
        if unit['kind'] == 'package':
            for part, label in (('spec', 'Package Spec'), ('body', 'Package Body')):
                if unit[part]:
                    model['program_units'].append({'name': unit_name, 'programunittype': label,
                                                   'programunittext': unit[part], 'frm_library': library})
        else:
            model['program_units'].append({'name': unit_name, 'programunittype': unit['kind'].title(),
                                           'programunittext': unit['text'], 'frm_library': library})
    report = {'version': 1, 'attached': attached, 'scope': order, 'libraries': []}
    for lib in libraries:
        used = sorted(u for u in reached if index[u][0] == lib['name'])
        report['libraries'].append({'name': lib['name'], 'file': lib['file'], 'sha256': lib['sha256'],
                                    'in_scope': lib['name'] in order, 'units': len(lib['units']), 'used_units': used,
                                    'attaches': lib['attached'], 'directives': lib['directives']})
    report['missing'] = [name for name in order if name not in by_name]
    loaded = {lib['name'] for lib in libraries if lib['name'] in order}
    for issue in model['issues']:
        found = re.match(r'Külső könyvtár megőrizve: (.*); nincs PLL/PLD fordítás\.$', issue['detail'])
        if issue['code'] == 'ATTACHED_LIBRARY' and found:
            name = library_name(Path(found.group(1)))
            if name in loaded:
                used = sum(1 for u in reached if index[u][0] == name)
                issue['detail'] = (f'Csatolt könyvtár beolvasva (PLD): {name}; {used} programegységét éri el a form, '
                                   'ezek a form saját egységeivel azonos módon fordulnak.')
    model['library_report'] = report
    return report


def unloaded(model: dict) -> list[str]:
    """Attached libraries (also those a loaded library attaches) without source: the database resolves their calls."""
    from .xmlmodel import get
    report = model.get('library_report') or {}
    loaded = {lib['name'] for lib in report.get('libraries', []) if lib['in_scope']}
    direct = [get(l, 'Name') for l in model.get('libraries', []) if library_name(Path(get(l, 'Name') or '')) not in loaded]
    named = {library_name(Path(n)) for n in direct}
    return direct + [n for n in report.get('missing', []) if n not in named]

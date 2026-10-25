"""Frontend request inputs: evidence, explicit sources and guarded local edits.

Only generated expression slots can be changed. A module parameter is a host
integration contract, not a proven runtime value. Manual/server bindings remain
open work; no browser value becomes trusted authentication context.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import tempfile

from .common import MigrationError, read_json, write_json
from .ts_code import sq

REPORT = 'analysis/input-bindings.json'
CONFIG = 'screen_input_bindings'
ID = re.compile(r'[a-f0-9]{20}')
PARAMETER = re.compile(r'[A-Za-z_][A-Za-z0-9_.-]{0,127}')


class BindingConflict(MigrationError):
    pass


def digest(value) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else
                          json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def path_in(output, relative):
    from .backend_assistant import safe_file
    return safe_file(output, relative)


def validate(config):
    if not isinstance(config, dict) or set(config) - {'version', 'bindings'} or config.get('version', 1) != 1:
        raise MigrationError('INPUT_BINDINGS: version=1 és bindings objektum szükséges.')
    bindings = config.get('bindings', {})
    if not isinstance(bindings, dict) or len(bindings) > 2000:
        raise MigrationError('INPUT_BINDINGS: legfeljebb 2000 bekötés adható meg.')
    for key, value in bindings.items():
        if not isinstance(key, str) or not ID.fullmatch(key) or not isinstance(value, dict):
            raise MigrationError('INPUT_BINDINGS: hibás azonosító vagy bejegyzés.')
        kind = value.get('kind')
        if kind not in {'field', 'parameter', 'manual'} or set(value) - {'kind', 'source', 'note'}:
            raise MigrationError('INPUT_BINDINGS: field, parameter vagy manual forrás szükséges.')
        note = value.get('note', '')
        if not isinstance(note, str) or len(note) > 2000:
            raise MigrationError('INPUT_BINDINGS: a megjegyzés legfeljebb 2000 karakter.')
        source = value.get('source', '')
        if not isinstance(source, str) or len(source) > 257:
            raise MigrationError('INPUT_BINDINGS: hibás forrás.')
        if kind == 'parameter' and (not PARAMETER.fullmatch(source) or source in {'__proto__', 'constructor', 'prototype'}):
            raise MigrationError('INPUT_BINDINGS: érvényes modulparaméternév szükséges.')
        if kind == 'field' and not source:
            raise MigrationError('INPUT_BINDINGS: válassz képernyőmezőt.')
        if kind == 'manual' and (source or not note.strip()):
            raise MigrationError('INPUT_BINDINGS: a kézi bekötéshez megjegyzés szükséges, forrás nélkül.')
    return bindings


def selected_expression(row, binding, sources):
    if not binding or binding['kind'] == 'manual':
        return row['default_expression']
    if row.get('timing') == 'constructor':
        raise MigrationError('INPUT_BINDINGS: az indítási kérés a konstruktorban fut; az input/űrlap elkészüléséhez igazított kézi bekötés szükséges.')
    if row['source'].startswith('SYSTEM.'):
        raise MigrationError('INPUT_BINDINGS: SYSTEM értékhez kézi, szerveroldali bekötés szükséges.')
    if not row['automatic']:
        raise MigrationError('INPUT_BINDINGS: ehhez a végponthoz a hívást is kézzel kell bekötni.')
    if binding['kind'] == 'parameter':
        return 'this.migrationParameters[' + sq(binding['source']) + '] ?? null'
    source = next((s for s in sources if s['owner'] == binding['source']), None)
    if source is None:
        raise MigrationError('INPUT_BINDINGS: a kiválasztott mező nincs a generált képernyőn.')
    return source['text_expression'] if row['transport'] == 'string' else source['expression']


def marked(key, expression):
    return '/* frm-input:' + key + ' */ (' + expression + ') /* /frm-input:' + key + ' */'


def slot(endpoint, owner, source, location, default, transport, *, automatic=True, inferred=False):
    key = digest([endpoint, source, location])[:20]
    return {'id': key, 'endpoint': endpoint, 'owner': owner, 'source': source, 'location': location,
            'default_expression': default or 'null', 'transport': transport,
            'automatic': automatic, 'inferred': inferred}


def enrich_api(api, discovery, output):
    """Manual actions have no compiled read contract: surface conservative candidates."""
    if not api:
        return api
    api = deepcopy(api)
    action_file = output / 'analysis/action-plan.json'
    if not action_file.is_file():
        return api
    actions = read_json(action_file)['actions']
    backend = read_json(output / 'analysis/backend-plan.json')
    for endpoint in backend['endpoints']:
        spec = api.get('actions', {}).get(endpoint.get('owner'))
        if spec is not None and endpoint.get('developer_inputs'):
            spec['server_inputs'] = endpoint['developer_inputs']
    codes = {c['id']: c for c in discovery['code']}
    for action in actions:
        spec = api['actions'].get(action['owner'])
        if spec is None or 'reads' in spec:
            continue
        ids = set(action.get('reachable_code', [])) | set(action.get('event_code_candidates', [])) | {action['trigger_id']}
        pending = list(ids)
        while pending:
            for call in codes.get(pending.pop(), {}).get('calls', []):
                for target in call.get('target_ids', []):
                    if target not in ids:
                        ids.add(target); pending.append(target)
        refs = sorted({b['name'] for cid in ids for b in codes.get(cid, {}).get('binds', [])
                       if b['usage'] != 'assignment-target' and '.' in b['name'] and not b['name'].startswith('FRM.')})
        context = [s for s in refs if s.split('.', 1)[0] in {'GLOBAL', 'PARAMETER', 'SYSTEM'}]
        spec.update(reads=[s for s in refs if s not in context], parameters=context, bindings_inferred=True)
    return api


def write_report(output, target, source, rows, sources, config, warnings):
    bindings = validate(config.get(CONFIG, {}))
    for row in rows:
        row['expression'] = selected_expression(row, bindings.get(row['id']), sources)
    report = {'version': 1, 'target': target, 'source_sha256': digest(source.encode()),
              'rows': rows, 'sources': sources, 'bindings': bindings, 'warnings': warnings}
    unknown = set(bindings) - {r['id'] for r in rows}
    if unknown:
        report['warnings'].append('Korábbi bekötésekhez már nincs bemenet: ' + ', '.join(sorted(unknown)))
    write_json(path_in(output, REPORT), report)


def preserve_report(previous, fresh):
    """A preserved component keeps its slots, but cannot hide a changed contract."""
    def contract(report):
        return [report['target'], report['sources'],
                [{k: r[k] for k in ('id', 'default_expression', 'automatic', 'inferred', 'transport', 'timing')}
                 for r in report['rows']]]
    if contract(previous) != contract(fresh):
        previous['contract_changed'] = True
        previous['fresh_requirements'] = [{k: r[k] for k in ('endpoint', 'source', 'location')} for r in fresh['rows']]
        warning = ('Az újragenerálás bemeneti szerződése megváltozott, a képernyő megmaradt. '
                   'Generálj új mappába, és kézzel egyeztesd a képernyőt a friss backenddel.')
        if warning not in previous['warnings']: previous['warnings'].append(warning)
    return previous


def sheet(output):
    file = path_in(output, REPORT)
    if not file.is_file():
        return {'available': False, 'rows': [], 'sources': [], 'warnings': [
            'A bemenetellenőrzéshez generálj képernyőt ezzel a migrátorverzióval.'], 'pending': []}
    report = read_json(file)
    target = path_in(output, report['target'])
    actual = digest(target.read_bytes()) if target.is_file() else None
    effective_path = path_in(output, 'analysis/effective-config.json')
    effective = read_json(effective_path) if effective_path.is_file() else {}
    revision = digest([report, actual, effective.get('config', {}).get(CONFIG)])
    changed = actual != report['source_sha256']
    result = deepcopy(report)
    for row in result['rows']:
        binding = report['bindings'].get(row['id'])
        row['binding'] = binding
        row['status'] = ('file_changed' if changed else 'contract_changed' if report.get('contract_changed') else
                         'lifecycle' if row.get('timing') == 'constructor' else 'unwired' if not row['automatic'] else
                         'manual' if binding and binding['kind'] == 'manual' else
                         'host_required' if binding and binding['kind'] == 'parameter' else
                         'missing' if row['expression'] == 'null' else 'review' if row['inferred'] else 'connected')
    result.update(available=True, revision=revision, file_changed=changed,
                  pending=[r['id'] for r in result['rows'] if r['status'] != 'connected'])
    return result


def save(output: Path, config: dict, revision: str, source_config: Path | None = None):
    bindings = validate(config)
    current = sheet(output)
    if not current['available'] or current['revision'] != revision:
        raise BindingConflict('A bemenetek közben változtak. Töltsd újra mentés előtt.')
    if current['file_changed']:
        raise BindingConflict('A komponens kézzel módosult. A bekötéseket kézzel emeld át; a szerkesztett kódot nem írjuk felül.')
    if current.get('contract_changed'):
        raise BindingConflict('A bemeneti szerződés változott. A megőrzött képernyőt a friss backenddel kézzel kell egyeztetni.')
    report = read_json(path_in(output, REPORT))
    known = {r['id'] for r in report['rows']}
    if set(bindings) - known - set(report['bindings']):
        raise MigrationError('INPUT_BINDINGS: ismeretlen bemenetazonosító.')
    target = path_in(output, report['target'])
    original = target.read_text(encoding='utf-8')
    updated = original
    for row in report['rows']:
        expression = selected_expression(row, bindings.get(row['id']), report['sources'])
        if row['automatic']:
            old = marked(row['id'], row['expression'])
            if old not in updated:
                raise BindingConflict('A generált bemeneti jelölés módosult. Kézi bekötés szükséges: ' + row['source'])
            updated = updated.replace(old, marked(row['id'], expression))
        row['expression'] = expression
    report['bindings'] = bindings
    report['source_sha256'] = digest(updated.encode())
    effective_path = path_in(output, 'analysis/effective-config.json')
    effective = read_json(effective_path)
    normalized = {'version': 1, 'bindings': bindings}
    effective['config'][CONFIG] = normalized
    as_bytes = lambda v: (json.dumps(v, ensure_ascii=False, indent=2) + '\n').encode()
    changes = [(target, updated.encode()), (path_in(output, REPORT), as_bytes(report)),
               (effective_path, as_bytes(effective))]
    if source_config is not None:
        source = read_json(source_config)
        source[CONFIG] = normalized
        changes.append((source_config, as_bytes(source)))
    backup = path_in(output, 'analysis/input-binding-originals/' + digest(original.encode())[:20] + '.ts.txt')
    changes.insert(0, (backup, original.encode()))
    previous = [(p, p.read_bytes() if p.is_file() else None) for p, _ in changes]
    written = []
    try:
        for path, content in changes:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name); stream.write(content)
            try:
                temporary.replace(path); written.append(path)
            finally:
                temporary.unlink(missing_ok=True)
    except OSError:
        for path, old in reversed(previous):
            if path not in written: continue
            if old is None: path.unlink(missing_ok=True)
            else: path.write_bytes(old)
        raise
    return sheet(output)

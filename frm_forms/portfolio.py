"""Portfolio: many forms in one run, and one ranked report of what blocks them.

The per-form outputs are ordinary migrate outputs. The report answers one
question: which fix would unblock the most endpoints and triggers across the
whole application, so development effort goes where it pays off most.
"""
from __future__ import annotations

import argparse
import contextlib
from collections import Counter, defaultdict
import csv
import io
import json
from pathlib import Path
import re
import sys

from .common import MigrationError, read_json, write_json

# --- inputs -----------------------------------------------------------------

LIBRARY_SUFFIXES = ('_olb.xml', '_mmb.xml')


def form_inputs(paths: list[Path]) -> list[Path]:
    """Explicit files as given; directories: *.fmb and Forms2XML form exports."""
    found = []
    for path in paths:
        if path.is_file():
            found.append(path.resolve())
            continue
        if not path.is_dir():
            raise MigrationError('BATCH_INPUT: nem létező fájl vagy mappa: ' + str(path))
        for candidate in sorted(path.rglob('*')):
            lower = candidate.name.lower()
            if not candidate.is_file() or lower.endswith(LIBRARY_SUFFIXES):
                continue
            if lower.endswith('.fmb'):
                found.append(candidate.resolve())
            elif lower.endswith('.xml'):
                try:
                    head = candidate.read_bytes()[:8192].decode('utf-8', 'ignore')
                except OSError:
                    continue
                if 'FormModule' in head:
                    found.append(candidate.resolve())
    unique = list(dict.fromkeys(found))
    if not unique:
        raise MigrationError('BATCH_INPUT: nem található form (.fmb vagy FormModule XML).')
    return unique


def folder_name(path: Path, used: set[str]) -> str:
    stem = re.sub(r'_fmb$', '', path.stem, flags=re.I)
    base = re.sub(r'[^A-Za-z0-9_.-]+', '_', stem).strip('._') or 'form'
    name, index = base, 2
    while name.lower() in used:
        name, index = f'{base}_{index}', index + 1
    used.add(name.lower())
    return name


def migrate_args(source: Path, out: Path, options) -> argparse.Namespace:
    """The same argument set the migrate command builds, with its defaults."""
    return argparse.Namespace(
        command='migrate', input=source, out=out, olb=list(options.olb), pld=list(getattr(options, 'pld', [])), mmb=None,
        regenerate=options.regenerate, analysis_only=False,
        scaffold=options.mode == 'scaffold', screen=options.mode == 'screen', screen_overrides=None,
        strict_inheritance=False, frontend_only=False, config=options.config, schema=options.schema,
        rules=options.rules, module=None, java_package=options.java_package, ai='off', max_ai_calls=None,
        cache_dir=Path('.frm-ai-cache'), zip=False, strict=False, field_lengths=getattr(options, 'field_lengths', None),
        config_overrides=getattr(options, 'config_overrides', None))


# A survey measures every form unattended: no menu id, no window question.
# backend_live as on the web: no MODULE_REVIEWED gate and writes allowed unless schema.json forbids them,
# so the survey shows what the generator cannot translate, not the review policy.
SURVEY_OVERRIDES = {'AWU_AZON': '', 'screen_window_selection': 'all', 'screen_primary_window_auto': True,
                    'backend_live': True}


def run_batch(options, survey: bool = False) -> int:
    from .cli import configuration, migration
    out = options.out.resolve()
    if survey:
        options.config_overrides = SURVEY_OVERRIDES
    entries = []
    if options.report_only:
        if not out.is_dir():
            raise MigrationError('BATCH_REPORT: a --out mappa nem létezik: ' + str(out))
        entries = [{'folder': d.name, 'input': None, 'status': 'ok', 'error': None}
                   for d in sorted(out.iterdir()) if (d / 'analysis/summary.json').is_file()]
    else:
        forms = form_inputs(options.inputs)
        if len(forms) > 1 and configuration(migrate_args(forms[0], out, options)).get('AWU_AZON'):
            raise MigrationError('BATCH_AWU_AZON: formonként külön menüazonosító szükséges. Használd a webes formonkénti AWU_AZON mezőket vagy külön migrate --awu-azon parancsokat.')
        out.mkdir(parents=True, exist_ok=True)
        used = set()
        for index, source in enumerate(forms, 1):
            folder = folder_name(source, used)
            entry = {'folder': folder, 'input': str(source), 'status': 'ok', 'error': None}
            print(f'[{index}/{len(forms)}] {source.name} ...', end=' ', file=sys.stderr, flush=True)
            captured = io.StringIO()
            try:
                with contextlib.redirect_stdout(captured):
                    code = migration(migrate_args(source, out / folder, options))
                if code not in (0, 3):
                    entry.update(status='failed', error='kilépési kód: ' + str(code))
            except (MigrationError, OSError, ValueError, TypeError) as exc:
                entry.update(status='failed', error=failure(exc))
            print(entry['status'] if entry['status'] == 'ok' else 'HIBA', file=sys.stderr, flush=True)
            entries.append(entry)
    report = aggregate(out, entries, catalog_prefixes(options))
    write_json(out / 'portfolio.json', report)
    (out / 'PORTFOLIO_HU.md').write_text(markdown(report), encoding='utf-8')
    write_csv(out / 'portfolio-forms.csv', report['forms'])
    totals = report['totals']
    summary = {'output': str(out), 'report': str(out / 'PORTFOLIO_HU.md'), 'forms': totals['forms'], 'failed': totals['failed']}
    if survey:
        from . import survey as felmeres
        felmeres.write(out, felmeres.collect(out, entries, names=getattr(options, 'names', False)))
        summary['survey'] = str(out / 'FELMERES_HU.md')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not totals['failed'] else 2


def failure(exc) -> str:
    """Input rejections carry a JSON document; show its codes, not the raw JSON."""
    text = str(exc)
    try:
        issues = json.loads(text).get('issues', [])
        text = '; '.join(str(i.get('code', '')) + ': ' + str(i.get('detail', '')) for i in issues) or text
    except (ValueError, AttributeError):
        pass
    return ' '.join(text.split())[:600]


def catalog_prefixes(options) -> tuple:
    from . import framework
    config = read_json(options.config) if getattr(options, 'config', None) else {}
    if config.get('framework_catalog'):
        config['framework_catalog'] = str((options.config.resolve().parent / config['framework_catalog']).resolve())
    return framework.load(config)['call_prefixes']


# --- aggregation --------------------------------------------------------------

TRIGGER_ID = re.compile(r'^[A-Za-z0-9_$#.]+:[A-Z][A-Z0-9-]*: ')


PASSTHROUGH_REASON = 'Átfuttatás az adatbázisban sem lehetséges: '


def normalize(text: str) -> str:
    """One template per reason: identifiers, literals and numbers removed."""
    text = TRIGGER_ID.sub('', ' '.join(str(text).split()))
    token = re.match(r"(Nem támogatott token a\(z\) )\d+(\. karakternél: )'(.)", text)
    if token:
        # The first unparsed character is the actionable part (e.g. '&' or '@'); the reason why the
        # database passthrough failed too (the real cause) stays after it.
        head = token.group(1) + 'N' + token.group(2) + repr(token.group(3)) + '…'
        rest = text.find(PASSTHROUGH_REASON)
        return head + (' ' + normalize(text[rest:]) if rest >= 0 else '')
    text = re.sub(r"'(?:[^']|'')*'", "'…'", text)
    text = re.sub(r'"[^"]*"', '"…"', text)
    text = re.sub(r':[A-Za-z_][\w$#]*(?:\.[A-Za-z_][\w$#]*)?', ':<bind>', text)
    # Forms/PLSQL names arrive upper-case; lower-case words (schema.json) stay.
    text = re.sub(r'\b[A-Z_][A-Z0-9_$#]*(?:\.[A-Z_][A-Z0-9_$#]*)+(?![\w$#])', '<név>', text)
    return re.sub(r'\b\d+\b', 'N', text)


def load(path: Path, default=None):
    try:
        return read_json(path) if path.is_file() else default
    except (MigrationError, OSError, ValueError):
        return default


def aggregate(out: Path, entries: list[dict], framework_prefixes: tuple) -> dict:
    forms = []
    blockers = defaultdict(lambda: {'endpoints': 0, 'forms': set(), 'example': ''})
    reasons = defaultdict(lambda: {'triggers': 0, 'forms': set(), 'events': Counter(), 'scopes': Counter(), 'example': ''})
    routines = defaultdict(lambda: {'calls': 0, 'forms': set(), 'kinds': Counter()})
    issue_codes = Counter()
    gates = Counter()
    for entry in entries:
        root = out / entry['folder']
        row = {'folder': entry['folder'], 'input': entry['input'], 'status': entry['status'], 'error': entry['error']}
        summary = load(root / 'analysis/summary.json')
        if entry['status'] != 'ok' or summary is None:
            row['status'] = 'failed'
            forms.append(row)
            continue
        model = load(root / 'analysis/form.ir.json', {})
        plan = load(root / 'analysis/backend-plan.json', {})
        name = summary.get('form', entry['folder'])
        endpoints = [e for e in plan.get('endpoints', []) if e.get('operation')]
        actions = [e for e in plan.get('endpoints', []) if not e.get('operation')]
        row.update(form=name, mode=summary.get('generation_mode'), blocks=summary.get('blocks', 0),
                   triggers=summary.get('triggers', 0), converted=summary.get('converted_triggers', 0),
                   framework=summary.get('framework_triggers', 0), review=summary.get('review_triggers', 0),
                   endpoints=len(endpoints), enabled=sum(bool(e.get('implemented')) for e in endpoints),
                   ready=sum(bool(e.get('ready_after_module_review')) for e in endpoints),
                   blocked=sum(not e.get('implemented') and not e.get('ready_after_module_review') for e in endpoints),
                   actions=len(actions), skipped_operations=len(plan.get('skipped_operations', [])),
                   skipped_blocks=len(plan.get('skipped_blocks', [])),
                   module_gate=bool(plan.get('module_gate', {}).get('required')))
        for code in plan.get('module_gate', {}).get('codes', []):
            gates[code] += 1
        for endpoint in endpoints:
            for reason in dict.fromkeys(endpoint.get('blockers', [])):
                key = normalize(reason)
                blockers[key]['endpoints'] += 1
                blockers[key]['forms'].add(name)
                blockers[key]['example'] = blockers[key]['example'] or name + ': ' + reason
        scope_of = {i['detail']: i['scope'] for i in model.get('issues', []) if i.get('code') == 'UNSUPPORTED_TRIGGER'}
        for trigger in model.get('triggers', []):
            if trigger.get('status') != 'review':
                continue
            key = normalize(trigger.get('reason') or '')
            bucket = reasons[key]
            bucket['triggers'] += 1; bucket['forms'].add(name); bucket['events'][trigger.get('event', '')] += 1
            bucket['scopes'][scope_of.get(trigger['id'] + ': ' + (trigger.get('reason') or ''), 'review')] += 1
            bucket['example'] = bucket['example'] or name + ' / ' + trigger['id'] + ': ' + (trigger.get('reason') or '')
        for issue in model.get('issues', []):
            issue_codes[(issue.get('code'), issue.get('scope'))] += 1
        discovery = load(root / 'analysis/discovery/form-map.json', {})
        for code in discovery.get('code', []):
            if code.get('module_kind') != 'formmodule':
                continue
            for call in code.get('calls', []):
                routine = call.get('name', '').upper()
                if call.get('kind') not in {'external_candidate', 'unresolved'} or routine.lower().startswith(framework_prefixes):
                    continue
                routines[routine]['calls'] += 1; routines[routine]['forms'].add(name); routines[routine]['kinds'][code.get('kind')] += 1
        forms.append(row)
    ok = [f for f in forms if f['status'] == 'ok']
    totals = {'forms': len(forms), 'failed': len(forms) - len(ok)}
    for key in ('triggers', 'converted', 'framework', 'review', 'endpoints', 'enabled', 'ready', 'blocked',
                'actions', 'skipped_operations', 'skipped_blocks'):
        totals[key] = sum(f.get(key, 0) for f in ok)
    totals['module_gate_forms'] = sum(f.get('module_gate', False) for f in ok)
    def ranked(table, weight):
        rows = [{'reason': k, **{f: (sorted(v) if isinstance(v, set) else dict(v.most_common()) if isinstance(v, Counter) else v)
                                 for f, v in data.items()}} for k, data in table.items()]
        return sorted(rows, key=lambda r: (-r[weight], -len(r['forms']), r['reason']))
    return {'portfolio_version': 1, 'totals': totals, 'forms': forms,
            'endpoint_blockers': ranked(blockers, 'endpoints'),
            'trigger_reasons': ranked(reasons, 'triggers'),
            'external_routines': sorted(({'routine': k, 'calls': v['calls'], 'forms': sorted(v['forms']),
                                          'code_kinds': dict(v['kinds'])} for k, v in routines.items()),
                                        key=lambda r: (-len(r['forms']), -r['calls'], r['routine'])),
            'issue_codes': [{'code': c, 'scope': s, 'count': n} for (c, s), n in issue_codes.most_common()],
            'module_gates': dict(gates)}


# --- output --------------------------------------------------------------------

def cell(value) -> str:
    return str(value).replace('|', '\\|').replace('\n', ' ')


def markdown(report: dict, limit: int = 15) -> str:
    t = report['totals']
    percent = lambda part, whole: f'{(100 * part / whole):.0f}%' if whole else '—'
    lines = ['# Portfólió-összesítő', '',
             f"{t['forms']} form, ebből {t['failed']} generálása sikertelen. "
             f"Triggerek: {t['triggers']}, ebből felismert {t['converted']}, keretrendszeri {t['framework']}, átültetendő {t['review']}.", '',
             '| Végpontok (CRUD + keresés) | db | arány |', '|---|---:|---:|',
             f"| Generált | {t['endpoints']} | |",
             f"| Engedélyezett | {t['enabled']} | {percent(t['enabled'], t['endpoints'])} |",
             f"| Kész, csak a MODULE_REVIEWED kapcsolóra vár | {t['ready']} | {percent(t['ready'], t['endpoints'])} |",
             f"| Saját okkal tiltott | {t['blocked']} | {percent(t['blocked'], t['endpoints'])} |",
             f"| Nem generált (a Forms nem engedi / nincs adatforrás) | {t['skipped_operations']} művelet, {t['skipped_blocks']} blokk | |",
             f"| Gombvégpontok | {t['actions']} | |", '',
             '## Mit érdemes először javítani', '',
             'A saját okkal tiltott végpontok okai, a feloldott végpontok száma szerint. Egy sor egy üzenetsablon: a nevek, bindek és literálok ki vannak emelve.', '',
             '| # | Ok | Végpont | Form | Példa |', '|---:|---|---:|---:|---|']
    for index, row in enumerate(report['endpoint_blockers'][:limit], 1):
        lines.append(f"| {index} | {cell(row['reason'])} | {row['endpoints']} | {len(row['forms'])} | {cell(row['example'][:120])} |")
    if not report['endpoint_blockers']:
        lines.append('| — | Nincs saját okkal tiltott végpont. | 0 | 0 | |')
    lines += ['', '## Átültetendő triggerek okai', '',
              'Hatókör: all/read/write/create/update/delete = végpontot tilt; frontend = képernyő-feladat; button = gombvégpont.', '',
              '| # | Ok | Trigger | Form | Leggyakoribb események | Hatókör |', '|---:|---|---:|---:|---|---|']
    for index, row in enumerate(report['trigger_reasons'][:limit], 1):
        events = ', '.join(f'{e} ({n})' for e, n in list(row['events'].items())[:3])
        scopes = ', '.join(f'{s} ({n})' for s, n in row['scopes'].items())
        lines.append(f"| {index} | {cell(row['reason'])} | {row['triggers']} | {len(row['forms'])} | {cell(events)} | {cell(scopes)} |")
    lines += ['', '## Külső és ismeretlen hívások', '',
              'Adatbázis-csomagok, csatolt könyvtárak vagy ismeretlen rutinok (a keretrendszeri hívások nélkül). '
              'Ezek aláírását érdemes a `schema.json` `procedures` szakaszába felvenni (`frm_forms dictionary-sql`).', '',
              '| # | Rutin | Hívás | Form |', '|---:|---|---:|---:|']
    for index, row in enumerate(report['external_routines'][:limit], 1):
        lines.append(f"| {index} | {cell(row['routine'])} | {row['calls']} | {len(row['forms'])} |")
    if not report['external_routines']:
        lines.append('| — | Nincs külső hívás. | 0 | 0 |')
    if report['module_gates']:
        lines += ['', '## Modulszintű ellenőrzés', '', 'Formok száma kódonként (MODULE_REVIEWED kapcsoló mögött):', '']
        lines += [f'- `{code}`: {count}' for code, count in sorted(report['module_gates'].items())]
    lines += ['', '## Formok', '', '| Form | Mód | Trigger (felismert / keretr. / átültetendő) | Végpont | Engedélyezett | Kapcsolóra vár | Tiltott | Gomb |',
              '|---|---|---|---:|---:|---:|---:|---:|']
    for f in report['forms']:
        if f['status'] != 'ok':
            continue
        lines.append(f"| {cell(f['form'])} | {f['mode']} | {f['triggers']} ({f['converted']} / {f['framework']} / {f['review']}) | "
                     f"{f['endpoints']} | {f['enabled']} | {f['ready']} | {f['blocked']} | {f['actions']} |")
    failed = [f for f in report['forms'] if f['status'] != 'ok']
    if failed:
        lines += ['', '## Sikertelen generálás', '', '| Mappa | Hiba |', '|---|---|']
        lines += [f"| {cell(f['folder'])} | {cell(f['error'] or 'ismeretlen')} |" for f in failed]
    lines += ['', 'Részletek: `portfolio.json` (teljes listák), `portfolio-forms.csv` (Excelben megnyitható), formonként `<mappa>/analysis/`.', '']
    return '\n'.join(lines)


FORM_COLUMNS = ['folder', 'form', 'status', 'mode', 'blocks', 'triggers', 'converted', 'framework', 'review',
                'endpoints', 'enabled', 'ready', 'blocked', 'actions', 'skipped_operations', 'skipped_blocks',
                'module_gate', 'input', 'error']


def write_csv(path: Path, forms: list[dict]) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=FORM_COLUMNS, delimiter=';', extrasaction='ignore', lineterminator='\n')
    writer.writeheader()
    for row in forms:
        writer.writerow({k: row.get(k, '') for k in FORM_COLUMNS})
    # BOM + semicolon: opens correctly in a Hungarian Excel.
    path.write_text('\ufeff' + buffer.getvalue(), encoding='utf-8')

"""Actual trigger attachment points, separate from successful code conversion.

No percentage here claims Forms equivalence. This is a plan of the generated
candidate; CREATE_ONCE files and host adapters may have been edited afterwards.
"""
from collections import Counter
import json

from .common import write_json
from .generate import write
from .report import markdown_cell


HOOKS = {
    'WHEN-VALIDATE-ITEM': ({'create', 'update'}, 'Mentéskor, a validációs láncban; nincs szerverhívás mezőelhagyáskor.'),
    'WHEN-VALIDATE-RECORD': ({'create', 'update'}, 'Mentéskor, a mezővalidációk után; nincs rekordelhagyási esemény.'),
    'PRE-INSERT': ({'create'}, 'INSERT előtt, a végpont tranzakciójában.'),
    'POST-INSERT': ({'create'}, 'INSERT után, commit előtt.'),
    'PRE-UPDATE': ({'update'}, 'UPDATE előtt, a végpont tranzakciójában.'),
    'POST-UPDATE': ({'update'}, 'UPDATE után, commit előtt.'),
    'PRE-DELETE': ({'delete'}, 'DELETE előtt, a végpont tranzakciójában.'),
    'POST-DELETE': ({'delete'}, 'DELETE után, commit előtt.'),
    'POST-QUERY': ({'list', 'search', 'create', 'update'}, 'Lekérdezett soronként, valamint mentés utáni újraolvasáskor.'),
    'POST-CHANGE': ({'create', 'update', 'list', 'search'}, 'Mentéskor a mezővalidáció előtt; a csak nem lekérdezett mezőt író változat lekérdezett soronként is.'),
    'ON-INSERT': ({'create'}, 'A generált INSERT helyett, a végpont tranzakciójában.'),
    'ON-UPDATE': ({'update'}, 'A generált UPDATE helyett, a végpont tranzakciójában.'),
    'ON-DELETE': ({'delete'}, 'A generált DELETE helyett, a végpont tranzakciójában.'),
    'ON-CHECK-DELETE-MASTER': ({'delete'}, 'Törlés előtt (PRE-DELETE előtt), a végpont tranzakciójában.'),
}


def coverage(model, backend=None, screen=None):
    backend, screen = backend or {}, screen or {}
    endpoints = backend.get('endpoints', [])
    calls = screen.get('backend_calls') or {}
    visible = {a['owner'] for a in screen.get('actions', [])}
    steps = {a['owner']: a.get('steps') for a in screen.get('actions', [])}
    rows = []
    for index, trigger in enumerate(model['triggers']):
        event, owner = trigger['event'], trigger['owner']
        row = {'id': trigger['id'], 'owner': owner, 'event': event,
               'conversion': trigger['status'], 'status': 'manual', 'execution': 'Nincs teljes automatikus bekötés.',
               'engine': 'none', 'endpoints': [], 'gaps': [],
               'source': f"analysis/original-triggers/{index:04d}-{trigger['sha256'][:12]}.sql",
               'source_sha256': trigger['sha256']}
        linked = []
        if trigger['status'] == 'framework' or trigger.get('target') == 'noop':
            row.update(status='omitted', execution='Katalogizált keretrendszerhívás / bizonyított NULL trigger kihagyva.')
        elif trigger.get('target') == 'per-block':
            row.update(status='backend', engine='per-block',
                       execution='Form-szintű trigger: a blokkonkénti másolatai futnak (' + ', '.join(trigger['form_level_blocks']) + ').')
        elif trigger.get('target') == 'backend' and trigger['status'] == 'converted':
            operations, moment = HOOKS[event]
            linked = [e for e in endpoints if (e['block'] == trigger['block'] and e['operation'] in operations)
                      or (event == 'POST-QUERY' and e.get('query_block') == trigger['block'] and e.get('runs') == 'plsql-query')]
            row.update(status='backend' if linked else 'manual', execution=moment,
                       engine='plsql' if trigger.get('passthrough') else 'java')
            row['gaps'].append('A CRUD-végpont meghívását és a módosított rekordok gyűjtését a hostnak kell bekötnie.')
            if event.startswith('WHEN-VALIDATE-'):
                row['gaps'].append('A Forms mező-/rekordelhagyási időzítése és a csak módosult mezők validálása nincs megvalósítva.')
            if not linked:
                row['gaps'].append('Nincs generált végpont, amely ezt a triggert futtatná.')
        elif trigger.get('target') == 'action':
            linked = [e for e in endpoints if e.get('owner') == owner and e['operation'] == 'action']
            automatic = owner in visible and owner in calls.get('actions', {})
            query = trigger.get('query_action')
            row.update(status='backend' if linked else 'manual',
                       engine=('java-query' if query.get('java') else 'plsql-query') if query else 'plsql',
                       execution='Gombnyomás → Angular HTTP → DPS ServiceImpl.' if automatic else 'Akció-végpont; a hívását a hostnak kell bekötnie.')
            if not automatic:
                row['gaps'].append('Nincs hozzá automatikusan hívó, megjelenített Angular gomb.')
            plan = query['prepared'] if query else trigger.get('passthrough_plan') or {}
            binds = [b for b in plan.get('binds', []) if not query or b['block'] != query['context']]
            if query:
                row['query_block'] = query['target']
                binds += [{'block': b['source'].split('.', 1)[0], 'parameter': False}
                          for variant in query['variants'] for b in variant['binds']]
            if any(b['parameter'] for b in plan.get('binds', [])):
                row['gaps'].append('GLOBAL/PARAMETER kontextus szükséges; az értékforrások aktuális állapota: analysis/input-bindings.json / Java asszisztens.')
            if any(b['block'] not in {s['block'] for s in screen.get('sections', [])} for b in binds
                   if not b['parameter'] and not b.get('input')):
                row['gaps'].append('Képernyőn nem szereplő blokkértékeket is vár; a hostnak kell átadnia őket.')
            from . import java_variables
            given = {i['source'] for i in plan.get('inputs', []) if java_variables.lookup(i['variable'])}  # java-variables.json
            if any(b.get('input') and b['source'] not in given for b in binds):
                row['gaps'].append('Fejlesztői bemenet: ' + ', '.join(':' + b['source'] for b in binds if b.get('input') and b['source'] not in given)
                                   + ' (a Java-metódusban TODO változó, értékét a fejlesztő adja át).')
            if query and query['target'] not in {s['block'] for s in screen.get('sections', [])}:
                row['gaps'].append('A lekérdezett blokk nincs a generált képernyőn; az eredmény megjelenítését külön be kell kötni.')
        else:
            # A trigger outside the backend: only a button's query runs (the screen's frame has no Forms emulation).
            sequence = steps.get(owner) if event == 'WHEN-BUTTON-PRESSED' else None
            if sequence and calls.get('queries'):
                block, queries = '', []
                for step in sequence:
                    if step['op'] == 'goBlock':
                        block = step['block'].upper()
                    elif step['op'] == 'executeQuery' and block in calls['queries']:
                        queries.append(block)
                    else:
                        queries = []; break
                if queries:
                    row.update(status='frontend', engine='typescript', execution='Gombnyomás → executeQuery: ' + ', '.join(queries) + '.')
                    linked = [e for e in endpoints if e['block'] in queries and e['operation'] in {'list', 'search'}]
                    row['gaps'].append('A GO_BLOCK fókusz-, validációs és rekordnavigációs mellékhatásai hostfeladatok.')
                    if len(queries) > 1:
                        row['gaps'].append('A lekérdezések aszinkronok; az egymás eredményétől függő hívássorrend nincs garantálva.')
            if row['status'] == 'manual':
                row['gaps'].append(trigger.get('reason') or 'Felismert frontendutasítás is lehet: az actionRequested jelzés önmagában nem végrehajtás.')
        for endpoint in linked:
            row['endpoints'].append({k: endpoint.get(k) for k in ('method', 'operation', 'implemented', 'ready_after_module_review', 'blockers')})
        if linked and not any(e['implemented'] for e in linked):
            row['status'] = 'blocked'
            row['gaps'].append('A kapcsolódó végpontok jelenleg tiltottak; lásd a MODULE_REVIEWED és a műveleti blokkolók állapotát.')
        if trigger.get('passthrough', {}).get('unresolved'):
            row['gaps'].append('Adatbázisban/PLL-ben ellenőrizendő rutinok: ' + ', '.join(trigger['passthrough']['unresolved']))
        rows.append(row)
    return {'version': 1, 'equivalence_verified': False, 'basis': 'generated_candidate',
            'counts': dict(sorted(Counter(r['status'] for r in rows).items())), 'triggers': rows,
            'limitations': [
                'A PL/SQL futtathatósága nem igazolja a teljes Forms eseménylánc azonosságát.',
                'Nincs több HTTP-kérésen át élő Forms rekordpuffer, commit/rollback és adatbázis-session.',
                'A startup, navigáció, KEY-/ON-triggerek, master-detail és dinamikus állapotok hostintegrációt igényelhetnek.',
                'Valódi Oracle-adatbázissal és a céges Angular/Java alkalmazással funkcionális ellenőrzés szükséges.',
                'Újrageneráláskor ez a friss generált javaslat leltára; a megőrzött CREATE_ONCE kód eltérhet tőle.',
            ]}


def write_coverage(model, output):
    def read(name):
        path = output / 'analysis' / name
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
    data = coverage(model, read('backend-plan.json'), read('screen-plan.json'))
    write_json(output / 'analysis/runtime-coverage.json', data)
    labels = {'omitted': 'Kihagyható', 'backend': 'Backendhez kötve', 'frontend': 'Frontendhez kötve',
              'manual': 'Bekötendő', 'blocked': 'Tiltott végpont'}
    lines = ['# A triggerek tényleges bekötése', '',
             'A „converted” kódfordítási eredmény; nem jelenti automatikusan az eredeti Forms működését. '
             'Az alábbi lista a generált kód útvonalait mutatja, HTTP módban, a megadott konfigurációval.', '',
             *['- ' + text for text in data['limitations']], '',
             '| Trigger | Állapot | Futtatás | Bekötés / időzítés | Hátralévő munka |', '|---|---|---|---|---|']
    for row in data['triggers']:
        values = [row['id'], labels[row['status']], row['engine'], row['execution'], '; '.join(row['gaps']) or '—']
        lines.append('| ' + ' | '.join(markdown_cell(v) for v in values) + ' |')
    lines += ['', 'A kapcsolódó végpontok, tiltások, eredeti SQL-források és SHA256 lenyomatok: `analysis/runtime-coverage.json`.']
    write(output / 'RUNTIME_COVERAGE.md', '\n'.join(lines) + '\n')

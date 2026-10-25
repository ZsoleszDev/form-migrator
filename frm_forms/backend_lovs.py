"""LOV endpoints from RecordGroupQuery: the reviewed read-only SQL a LOV needs.

The query is not reinterpreted: it runs as written, wrapped for the typed-text
filter and a row limit. Only form item binds (:BLOCK.ITEM) become parameters;
SYSTEM.MODE is fixed to NORMAL; other GLOBAL/SYSTEM/PARAMETER or unknown binds
need server context and block the endpoint. Like every generated endpoint it
is also behind the module gate.
"""
from __future__ import annotations

import re

from .common import decode_line_escapes, jstr, name
from .plsql import Unsupported, tokenize
from .plsql_passthrough import normal_mode_sql
from . import framework

SIMPLE = re.compile(r'[A-Z][A-Z0-9_$#]*')
DEFAULT_LIMIT, MAX_LIMIT = 50, 500


def lov_plans(model: dict, discovery: dict, catalog: dict) -> list[dict]:
    items = {b['name'] + '.' + i['name']: i for b in model['blocks'] for i in b['items']}
    lovs = {str(l.get('name', '')).upper(): l for l in model.get('lovs', [])}
    groups = {str(g.get('name', '')).upper(): g for g in model.get('record_groups', [])}
    objects = [o for o in discovery.get('objects', []) if o.get('module_kind') == 'formmodule']
    lov_ids = {o['id']: o['name'].upper() for o in objects if o['kind'] == 'lov'}
    mappings = {}
    for o in objects:
        if o['kind'] == 'lovcolumnmapping' and o.get('parent_id') in lov_ids:
            mappings.setdefault(lov_ids[o['parent_id']], []).append(o['properties'])
    pickers = {t['owner'] for t in model['triggers']
               if t['event'] == 'KEY-LISTVAL' and t.get('item') and framework.date_picker(t['source'], catalog)}
    users = {}
    for owner, item in items.items():
        if item.get('lov'):
            users.setdefault(item['lov'].upper(), []).append(owner)
    plans, used = [], set()
    for lov_name, owners in sorted(users.items()):
        if all(owner in pickers for owner in owners):
            continue  # a catalogued calendar replaces this LOV with a native date picker
        lov = lovs.get(lov_name, {})
        group = groups.get(str(lov.get('recordgroupname', '')).upper(), {})
        key = name(lov_name, 'pascal') or 'Lov'
        suffix, index = key, 2
        while suffix in used:
            suffix, index = key + str(index), index + 1
        used.add(suffix)
        plan = {'name': lov_name, 'class': suffix, 'key': name(lov_name, 'kebab') or suffix.lower(),
                'record_group': group.get('name', lov.get('recordgroupname', '')), 'query': group.get('recordgroupquery', ''),
                'users': sorted(owners), 'block': sorted(owners)[0].split('.')[0], 'columns': [], 'display_column': '',
                'binds': [], 'sql': '', 'blockers': []}
        for m in mappings.get(lov_name, []):
            plan['columns'].append({'column': str(m.get('columnname', '')).upper(), 'return_item': m.get('returnitem', ''),
                                    'display_width': m.get('displaywidth', '')})
        shown = [c for c in plan['columns'] if str(c['display_width']).strip() not in {'0', '0.0'}]
        display = (shown or plan['columns'] or [{'column': ''}])[0]['column']
        plan['display_column'] = display if SIMPLE.fullmatch(display) else ''
        if not lov:
            plan['blockers'].append('A LOV definíciója nem található a modulban (' + lov_name + ').')
        elif not plan['query'].strip():
            plan['blockers'].append('Nincs RecordGroupQuery (statikus vagy hiányzó rekordcsoport: '
                                    + (plan['record_group'] or '—') + '); az értékeket külön kell biztosítani.')
        else:
            try:
                plan['sql'], plan['binds'] = rewrite(plan['query'], items, plan['display_column'])
            except Unsupported as exc:
                plan['blockers'].append(str(exc))
        plans.append(plan)
    return plans


def rewrite(query: str, items: dict, display: str) -> tuple[str, list[dict]]:
    """The RecordGroupQuery with :BLOCK.ITEM binds as named parameters, wrapped for filter and limit."""
    source = normal_mode_sql(decode_line_escapes(query))
    tokens = tokenize(source)
    words = [t for t in tokens if t.kind != 'eof']
    if not words or words[0].value not in {'SELECT', 'WITH'}:
        raise Unsupported('A LOV lekérdezése nem SELECT/WITH utasítás.')
    if any(t.value == ';' for t in words):
        raise Unsupported('A LOV lekérdezése több utasítást tartalmaz.')
    parts, last, binds = [], 0, {}
    for token in words:
        if token.kind != 'bind':
            continue
        source_name = token.value[1:]
        if source_name.split('.')[0] in {'GLOBAL', 'SYSTEM', 'PARAMETER'}:
            raise Unsupported('A LOV lekérdezése szervercontextet igényel: :' + source_name + '; a szerveroldali értéket külön kell bekötni.')
        item = items.get(source_name)
        if item is None or item['type'] not in {'text', 'number', 'datetime'}:
            raise Unsupported('A LOV lekérdezése ismeretlen vagy nem támogatott típusú mezőre hivatkozik: :' + source_name)
        if source_name not in binds:
            binds[source_name] = {'parameter': 'p' + str(len(binds)), 'source': source_name, 'type': item['type']}
        parts += [source[last:token.pos], ':' + binds[source_name]['parameter']]
        last = token.pos + len(token.value)
    parts.append(source[last:])
    inner = ''.join(parts).strip()
    # The inner query keeps its own lines, so a trailing "--" comment cannot swallow the wrapper.
    sql = 'SELECT * FROM (\n' + inner + '\n) lov'
    if display:
        sql += "\n WHERE (:term IS NULL OR UPPER(lov." + display + ") LIKE UPPER(:term) || '%')"
    sql += '\n FETCH FIRST :limit ROWS ONLY'
    return sql, list(binds.values())


def enabled_flag(plan: dict, gated: bool) -> str:
    if plan['blockers']:
        return 'false'
    return 'MODULE_REVIEWED' if gated else 'true'


def lov_evidence(plan: dict, gated: bool) -> list[str]:
    lines = [f"LOV: {plan['name']} | rekordcsoport: {plan['record_group'] or '—'} | használja: {', '.join(plan['users'])}",
             'Eredeti lekérdezés:', *(decode_line_escapes(plan['query']).splitlines() or ['—'])]
    if plan['columns']:
        lines.append('Oszlopok: ' + ', '.join(c['column'] + (' -> ' + c['return_item'] if c['return_item'] else '') for c in plan['columns']))
    lines.append('Szűrés a begépelt szövegre: ' + (plan['display_column'] + " LIKE 'szöveg%' (kis- és nagybetűtől függetlenül)"
                                                  if plan['display_column'] else 'nincs (nem egyszerű oszlopnév); a host szűr'))
    if plan['binds']:
        lines.append('Paraméterek (LovRequest.parameters): ' + ', '.join(b['source'] + ' -> ' + b['type'] for b in plan['binds']))
    flag = enabled_flag(plan, gated)
    if flag == 'true':
        lines.append('Generált művelet: engedélyezett')
    elif flag == 'MODULE_REVIEWED':
        lines.append('Generált művelet: kész, csak a modulszintű ellenőrzésre vár (MODULE_REVIEWED, lásd az osztály elején); addig HTTP 501.')
    else:
        lines.append('Generált művelet: HTTP 501, ellenőrzésig tiltott')
        lines += ['Tiltás oka: ' + r for r in plan['blockers']]
    return lines


def lov_method(plan: dict, gated: bool, comment_lines, log1x_open: str, user_type: str, method: str) -> str:
    flag = enabled_flag(plan, gated)
    guard = '' if flag == 'true' else (
        f'            if (!{flag}) throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, "Ez a LOV ebben a modulban még nem érhető el.");\n')
    binds = ', '.join('{' + ', '.join(map(jstr, (b['parameter'], b['source'], b['type']))) + '}' for b in plan['binds'])
    sql = jstr(plan['sql'] or 'SELECT NULL FROM DUAL WHERE 1 = 0')
    info = [f"Forms LOV: {plan['name']} (rekordcsoport: {plan['record_group']}), mező: {', '.join(plan['users']) or '—'}; "
            "a rekordcsoport SQL-je fut, a beírt szöveggel szűrve."]
    if plan['blockers']:
        info.append('Ok, amiért nem elérhető: ' + '; '.join(plan['blockers']))
    return (comment_lines('\n'.join(info), '    ') + '\n'
            f'''    @Transactional(readOnly = true)
    @Override
    public LovResult {method}({user_type} user, LovRequest request) throws Exception {{
        {log1x_open}
{guard}            if (request == null) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Hiányzó kérés.");
            return new LovResult(LovQuery.rows(jdbc, {sql}, request.term(), request.parameters(), request.limit(),
                new String[][] {{{binds}}}, {'true' if plan['display_column'] else 'false'}));
        }});
    }}''')


# The LOV query itself is the shared runtime's LovQuery (templates/runtime/LovQuery.java.tpl).

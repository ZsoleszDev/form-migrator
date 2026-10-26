"""Module contracts with all database work directly in the DPS ServiceImpl."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from textwrap import indent, wrap

from .action_scaffold import comment_lines
from .common import jstr, name, write_json, java_text_block
from .java_imports import cl_package, layer_package
from .discovery import source_view
from .generate import block_values, row_declarations, rule_methods, template, write


OPERATION_SECTIONS = {'list': {'LIST'}, 'create': {'CREATE', 'WRITE', 'CHANGE'},
                      'update': {'UPDATE', 'WRITE', 'CHANGE', 'STALE'}, 'delete': {'DELETE', 'WRITE', 'STALE'}}


def planned(b):
    """The block's endpoint plan (computed by finalize_capabilities)."""
    if b.get('endpoint_plan') is None:
        from .rules import endpoint_plan
        b['endpoint_plan'], b['skipped_operations'] = endpoint_plan(b)
    return b['endpoint_plan']


def backend_blocks(model):
    """Database blocks with at least one generated endpoint."""
    return [b for b in model['blocks'] if b['database'] and any(planned(b).values())]


def operations(model, actions):
    result = []
    for b in backend_blocks(model):
        plan = planned(b)
        row = b['class'] + 'Row'
        for op, http, verb, returns, args, controller_args, call in [
            ('list', 'Get', 'read', f'PageResult<{row}>', 'int offset, int limit',
             '@RequestParam(name="offset", defaultValue="0") int offset, @RequestParam(name="limit", defaultValue="50") int limit', 'offset, limit'),
            ('create', 'Post', 'create', f'RowResult<{row}>', f'{row} row', f'@RequestBody {row} row', 'row'),
            ('update', 'Put', 'update', f'RowResult<{row}>', f'{row} original, {row} value',
             f'@RequestBody UpdateRequest<{row}> request', 'original, value'),
            ('delete', 'Delete', 'delete', 'List<String>', f'{row} original', f'@RequestBody {row} original', 'original'),
        ]:
            if not plan.get(op):
                continue  # Forms forbids it or search replaces it: see analysis/backend-plan.json.
            result.append(dict(block=b, op=op, verb=verb, http=http, returns=returns, args=args,
                               controller_args=controller_args, call=call, method=op+b['class'],
                               constant=b['class'].upper()+'_'+op.upper()+'_PATH'))
        if plan.get('search'):
            arg = f'SearchRequest<{b["class"]}Criteria> request'
            result.append(dict(block=b, op='search', verb='read', http='Post', returns=f'PageResult<{row}>',
                               args=arg, controller_args='@RequestBody '+arg, call='request', method='search'+b['class'],
                               constant=b['class'].upper()+'_SEARCH_PATH'))
    for a in actions:
        trigger = next((t for t in model['triggers'] if t['owner'] == a['owner'] and t['event'] == a['event']), {})
        query = trigger.get('query_action')
        target = next((b for b in model['blocks'] if query and b['name'] == query['target']), None)
        returns = f'PageResult<{target["class"]}Row>' if target else 'ActionResult'
        request = 'QueryActionRequest request' if target else 'ActionRequest request'
        result.append(dict(action=a, op='action', verb='action:'+a['key'], http='Post', returns=returns,
                           args=request, controller_args='@RequestBody '+request, query_action=query,
                           call='request', method=a['method_name'], api_name=a['api_name'],
                           constant='ACTION_'+a['method_name'].upper()+'_PATH'))
    return result


def capability(b, op):
    """Endpoint state: key of can_/ready_/blockers for list/search/create/update/delete."""
    key = 'read' if op == 'list' else op
    return b['can_'+key], b.get('ready_'+key, b['can_'+key]), b.get('blockers', {}).get(key, [])


def frontend_api(ops, base, cls):
    """The CL Constants as the generated frontend uses them: same names, same paths.

    Blocks carry the Oracle item -> DTO field map, so the screen can translate its
    own control keys; searches carry the criteria fields with their Forms source.
    """
    api = {'constants_class': cls + 'Constants', 'base_path': base,
           'paths': {o['constant']: o['path'] for o in ops}, 'blocks': {}, 'lovs': {}, 'actions': {}}
    for o in ops:
        if o['op'] == 'lov':
            api['lovs'][o['lov']['name']] = {'constant': o['constant'], 'display_column': o['lov']['display_column'],
                                             'binds': [{'source': b['source'], 'type': b['type']} for b in o['lov']['binds']],
                                             'ready': not o['lov']['blockers']}
        elif o['op'] == 'commit':
            from .commit_chain import field
            reads, context = o.get('reads') or ([], [])
            api['commit'] = {'constant': o['constant'],
                             'blocks': {b['name']: {'request': 'changes' + field(b), 'result': 'rows' + field(b),
                                                    'operations': [op for op in ('create', 'update', 'delete') if b['endpoint_plan'].get(op)]}
                                        for b in o['blocks']},
                             'reads': reads, 'parameters': context}
        elif o['op'] == 'action':
            api['actions'][o['action']['owner']] = {'constant': o['constant']}
            # What the code reads: the screen sends these (blocks: BLOCK.ITEM, parameters: GLOBAL/PARAMETER/SYSTEM).
            query = o.get('query_action') or {}
            plan = query.get('prepared') or o.get('passthrough')
            if o.get('ai'):
                # The values the original code references: the AI-written body reads them from the request.
                reads, context = o.get('ai_reads') or action_reads(o['action'])
                api['actions'][o['action']['owner']].update(reads=reads, parameters=context, ai=True)
                if o.get('ai_alerts'):
                    api['actions'][o['action']['owner']]['alerts'] = True  # SHOW_ALERT: the screen asks, then calls again
            elif plan or query.get('java'):
                sources = [b['source'] for b in (plan or {}).get('binds', []) if not b.get('input') and b['block'] != query.get('context')]
                sources += [b['source'] for variant in query.get('variants', []) for b in variant['binds']]
                sources += [s for s, r in ((query.get('java') or {}).get('reads') or {}).items() if not r.get('input')]
                # FRM.* (alert reply, resume and save points) is the backend's own protocol, not a screen value.
                sources = [s for s in dict.fromkeys(sources) if s.split('.', 1)[0] != 'FRM']
                context = [s for s in sources if s.split('.', 1)[0] in {'GLOBAL', 'PARAMETER', 'SYSTEM'}]
                api['actions'][o['action']['owner']].update(reads=[s for s in sources if s not in context], parameters=context)
            if 'SHOW_ALERT' in (o.get('passthrough') or {}).get('commands', []):
                api['actions'][o['action']['owner']]['alerts'] = True  # the code shows a Forms alert
            if (o.get('passthrough') or {}).get('commit_points'):
                api['actions'][o['action']['owner']]['commit_point'] = True  # FRM_COMMIT: save, then resume
            if (o.get('passthrough') or {}).get('screen_points'):
                api['actions'][o['action']['owner']]['screen_points'] = True  # FRM_RESUME: a screen step, then resume
            commands = [c for c in (o.get('passthrough') or {}).get('commands', []) if 'ALERT' not in c]
            if commands:
                # The Forms built-ins the code returns as commands (the screen leaves them to the developer).
                api['actions'][o['action']['owner']]['commands'] = commands
            if o['action'].get('init'):
                api['init'] = o['action']['owner']  # the screen calls it when it opens
            if o.get('query_action'):
                api['actions'][o['action']['owner']]['query_block'] = o['query_action']['target']
        else:
            b = o['block']
            entry = api['blocks'].setdefault(b['name'], {'fields': {i['name']: i['field'] for i in row_fields(b)},
                                                         'operations': {}})
            if b.get('rowid_item'):
                entry['rowid'] = b['rowid_item']['field']  # the record's identity: kept by the screen, never shown
            if b.get('ui_disabled_operations'):
                # A key trigger (KEY-DELREC ...) without its default operation: the screen does not offer it.
                entry['ui_disabled'] = dict(sorted(b['ui_disabled_operations'].items()))
            operation = {'constant': o['constant'], 'http': o['http'].upper()}
            if o['op'] == 'search':
                operation['criteria'] = [{'source': v['source'], 'field': v['field']} for v in b['query_plan']['binds']]
            entry['operations'][o['op']] = operation
    return api


def row_fields(b):
    """The fields of the block's Row DTO: its items, and the ROWID of a block without a primary key."""
    return [i for i in b['items'] if i['kind'] != 'button'] + ([b['rowid_item']] if b.get('rowid_item') else [])


def skipped_blocks(model):
    result = []
    for b in model['blocks']:
        if b.get('backend_skip'):
            result.append({'block': b['name'], 'reason': b['backend_skip']})
        elif b['database'] and not any(planned(b).values()):
            result.append({'block': b['name'], 'reason': 'A Forms-blokk egyetlen adatműveletet sem enged (Query/Insert/Update/DeleteAllowed=false).'})
    return result


def input_note(prepared) -> str:
    """The developer inputs of a generated method, for its comment."""
    from .plsql_passthrough import open_inputs
    waiting = open_inputs(prepared['inputs'])
    given = [i for i in prepared['inputs'] if i not in waiting]
    parts = []
    if waiting:
        parts.append('Fejlesztői bemenet (a metódus elején, null; TODO): ' + ', '.join(':' + i['source'] + ' -> ' + i['variable']
                     for i in waiting) + '. A kód ezekkel fut; add át nekik a megfelelő értéket.')
    if given:
        parts.append('Fejlesztői bemenet a java-variables.json-ból: ' + ', '.join(':' + i['source'] + ' -> ' + i['variable']
                     for i in given) + '.')
    return ' '.join(parts)


def action_method(o, block, gated, discovery, model, log1x, user_type):
    """A button whose trigger only needs the database: its PL/SQL runs as written, in one transaction."""
    from .rules import JDBC_TYPES
    from .plsql_passthrough import input_declarations, input_variable, sql_expression
    prepared = o['passthrough']
    read = {'text': 'text', 'number': 'number', 'datetime': 'datetime'}
    params = []
    for b in prepared['binds']:
        if b.get('input'):
            params.append(f"DbCalls.in({input_variable(prepared, b)}, {JDBC_TYPES[b['type']]})")
        elif b['parameter']:
            params.append(f"DbCalls.in(PlsqlValues.parameter(parameters, {jstr(b['source'])}), java.sql.Types.VARCHAR)")
        else:
            params.append(f"DbCalls.in(PlsqlValues.{read[b['type']]}(values, {jstr(b['block'])}, {jstr(b['item'])}), {JDBC_TYPES[b['type']]})")
    emulated = bool(prepared.get('ui'))
    written_globals = prepared.get('globals', [])
    params += [f"DbCalls.out({JDBC_TYPES[b['type']]})" for b in prepared['outs']]
    params += ['DbCalls.out(java.sql.Types.VARCHAR)' for _ in written_globals] + ['DbCalls.out(java.sql.Types.VARCHAR)']
    if emulated:
        params.append('DbCalls.out(java.sql.Types.VARCHAR)')  # the screen commands
    offset = len(prepared['binds'])
    puts = ''.join(f"            PlsqlValues.put(blocks, {jstr(b['block'])}, {jstr(b['item'])}, out[{offset + k}]);\n" for k, b in enumerate(prepared['outs']))
    first_global = offset + len(prepared['outs'])
    puts += ''.join(f"            PlsqlValues.global(globals, {jstr(b['source'])}, out[{first_global + k}]);\n"
                    for k, b in enumerate(written_globals))
    messages = first_global + len(written_globals)
    guard = ('            if (!MODULE_REVIEWED) throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, "Ez a művelet ebben a modulban még nem érhető el.");\n'
             if gated else '')
    info = [o['action']['owner'] + ' / ' + o['action']['event'] + ': az eredeti PL/SQL fut az adatbázisban, egy tranzakcióban.',
            'Bemenet (a kérés blocks értékei): ' + (', '.join(b['source'] for b in prepared['binds'] if not b['parameter'] and not b.get('input')) or '—'),
            'Visszaírva a képernyőre: ' + (', '.join(b['source'] for b in prepared['outs']) or '—')]
    context = [b['source'] for b in prepared['binds'] if b['parameter']]
    if context:
        info.append('Képernyő-kontextus (parameters): ' + ', '.join(context))
    if written_globals:
        info.append('Visszaadott :GLOBAL értékek: ' + ', '.join(b['source'] for b in written_globals))
    if prepared.get('inputs'):
        info.append(input_note(prepared))
    if prepared.get('commands'):
        info.append('Forms-hívások felületi utasításként: ' + ', '.join(prepared['commands']))
    info += units_note(prepared)
    if o.get('query_java_reason'):
        info.append('Lekérdezőgomb, de a Java-lekérdezés nem készült el, ezért az eredeti PL/SQL fut: ' + o['query_java_reason'])
    if prepared.get('unresolved'):
        info.append(unresolved_note(prepared['unresolved'], model))
    if prepared.get('screen_points'):
        info.append('Képernyőpont (' + str(prepared['screen_points']) + '): a képernyő a FRM_RESUME utasításra végrehajtja '
                    'a lépést (EXECUTE_QUERY …), majd FRM.RESUME-mal a pont után folytatja. A pont előtti '
                    'adatbázis-módosítások a ponton véglegesednek (a Formsban a következő mentéskor).')
    info.append('Eredeti kód: analysis/backend-evidence.md')
    arguments = ',\n                '.join(params)  # no backslash inside the f-string: Python 3.10/3.11
    if prepared.get('commit_points'):
        return commit_point_action(o, info, gated, log1x, user_type, prepared, params, messages, emulated)
    # Only the request maps the block binds: no unused local (PMD UnusedLocalVariable).
    inputs = ''
    if any(not b['parameter'] and not b.get('input') for b in prepared['binds']):
        inputs += '            var values = request.blocks() == null ? java.util.Map.<String, java.util.Map<String, String>>of() : request.blocks();\n'
    if any(b['parameter'] for b in prepared['binds']):
        inputs += '            var parameters = request.parameters() == null ? java.util.Map.<String, String>of() : request.parameters();\n'
    inputs += input_declarations(prepared, '            ')
    return (comment_lines('\n'.join(info), '    ') + f'''
    @org.springframework.transaction.annotation.Transactional(rollbackFor = Exception.class)
    @Override
    public ActionResult {o['method']}({user_type} user, ActionRequest request) throws Exception {{
        {log1x(o, '() -> {')}
            if (request == null) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Hiányzó kérés.");
{guard}{inputs}            Object[] out = DbCalls.call(jdbc, {sql_expression(prepared)},
                {arguments});
            var blocks = new java.util.LinkedHashMap<String, java.util.Map<String, String>>();
            var globals = new java.util.LinkedHashMap<String, String>();
{puts}            return new ActionResult(blocks, PlsqlValues.lines(out[{messages}]), {('PlsqlValues.commands(out[' + str(messages + 1) + '])') if emulated else 'List.of()'}, globals);
        }});
    }}''')


def db_statements(model, ops, vals, cls, code=''):
    """Every SQL and PL/SQL text the module sends to Oracle, for verify-db (compiled there, never executed)."""
    statements = []

    def add(kind, source, sql):
        if sql and sql.strip():
            statements.append({'id': len(statements) + 1, 'kind': kind, 'source': source, 'sql': sql})

    for o in ops:
        if o['op'] == 'action' and o.get('passthrough'):
            add('plsql', o['action']['owner'] + ' / ' + o['action']['event'], o['passthrough']['sql'])
        elif o['op'] == 'lov' and not o['lov']['blockers']:
            add('sql', 'LOV ' + o['lov']['name'], o['lov']['sql'])
    for event, plan in sorted(model.get('commit_plan', {}).items()):
        if plan.get('plan'):
            add('plsql', event + ' (commitForm)', plan['plan']['sql'])
    for trigger in model['triggers']:
        add('plsql', trigger['id'], (trigger.get('passthrough') or {}).get('sql'))
    for block, values in vals.items():
        for key, label in (('SELECT_PAGE', 'lista'), ('SELECT_KEY', 'rekord betöltése'), ('INSERT_SQL', 'beszúrás'),
                           ('DELETE_SQL', 'törlés')):
            if values.get(key) and values[key] in code:  # only what the ServiceImpl really runs (ON-INSERT ... replaces DML)
                add('sql', block + ' ' + label, json.loads(values[key]))
    return {'version': 1, 'module_class': cls,
            'binds': 'PL/SQL: pozicionális ? (verify-db :b1, :b2 ... névre cseréli); SQL: :név', 'statements': statements}


def runner_name(o):
    """The private method that runs a button's PL/SQL: the action and the commit endpoint's prelude call it."""
    return 'run' + o['method'][0].upper() + o['method'][1:]


def commit_point_action(o, info, gated, log1x, user_type, prepared, params, messages, emulated):
    """A button with COMMIT_FORM in the middle of its code (commit_points): the PL/SQL in a runner method.

    The action calls it; so does commitForm, which runs the code up to the commit point again, in the
    commit's transaction, before it saves the blocks (CommitRequest.action / actionBlocks / actionParameters).
    """
    info = info[:-1] + ['Mentési pont (' + str(prepared['commit_points']) + '): a képernyő a FRM_COMMIT utasításra ment '
                        '(a commitForm ugyanebben a tranzakcióban újrafuttatja a kódot a pontig), majd FRM.RESUME-mal '
                        'folytatja a pont utáni résszel.', info[-1]]
    guard = ('            if (!MODULE_REVIEWED) throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, "Ez a művelet ebben a modulban még nem érhető el.");\n'
             if gated else '')
    from .plsql_passthrough import input_declarations, sql_expression
    runner = runner_name(o)
    commands = f'PlsqlValues.commands(out[{messages + 1}])' if emulated else 'List.of()'
    arguments = ',\n            '.join(params)
    offset = len(prepared['binds'])
    first_global = offset + len(prepared['outs'])
    puts = ''.join(f"        PlsqlValues.put(blocks, {jstr(b['block'])}, {jstr(b['item'])}, out[{offset + k}]);\n"
                   for k, b in enumerate(prepared['outs']))
    puts += ''.join(f"        PlsqlValues.global(globals, {jstr(b['source'])}, out[{first_global + k}]);\n"
                    for k, b in enumerate(prepared.get('globals', [])))
    return (comment_lines('\n'.join(info), '    ') + f'''
    @org.springframework.transaction.annotation.Transactional(rollbackFor = Exception.class)
    @Override
    public ActionResult {o['method']}({user_type} user, ActionRequest request) throws Exception {{
        {log1x(o, '() -> {')}
            if (request == null) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Hiányzó kérés.");
{guard}            var values = request.blocks() == null ? java.util.Map.<String, java.util.Map<String, String>>of() : request.blocks();
            var parameters = request.parameters() == null ? java.util.Map.<String, String>of() : request.parameters();
            var blocks = new java.util.LinkedHashMap<String, java.util.Map<String, String>>();
            var globals = new java.util.LinkedHashMap<String, String>();
            var messages = new java.util.ArrayList<String>();
            var commands = new java.util.ArrayList<List<String>>();
            {runner}(values, parameters, blocks, globals, messages, commands);
            return new ActionResult(blocks, messages, commands, globals);
        }});
    }}

    // {o['action']['owner']}: a gomb PL/SQL-je; a commitForm a mentési pontig ezt futtatja újra (FRM.COMMIT = POST).
    private void {runner}(java.util.Map<String, java.util.Map<String, String>> values, java.util.Map<String, String> parameters,
            java.util.Map<String, java.util.Map<String, String>> blocks, java.util.Map<String, String> globals,
            List<String> messages, List<List<String>> commands) {{
{input_declarations(prepared, '        ')}        Object[] out = DbCalls.call(jdbc, {sql_expression(prepared)},
            {arguments});
{puts}        messages.addAll(PlsqlValues.lines(out[{messages}]));
        commands.addAll({commands});
    }}''')


INIT_OWNER = '@INIT'


def init_evidence(o, model):
    lines = ['INDÍTÁSI VÉGPONT: ' + ', '.join(o['action']['init_triggers']),
             'A képernyő megnyitásakor fut: az eredeti PL/SQL az adatbázisban, a Forms-hívások felületi utasításként.']
    info = (model.get('init_plan') or {}).get('passthrough', {})
    for key, label in (('notes', 'Átalakítás'), ('commands', 'Felületi utasítások'), ('globals', ':GLOBAL kimenet'),
                       ('unresolved', 'Az adatbázis oldja fel')):
        if info.get(key):
            lines.append(label + ': ' + '; '.join(info[key]))
    for trigger in model['triggers']:
        if trigger['id'] in o['action']['init_triggers']:
            lines += ['', 'TRIGGER ' + trigger['id'] + ' SHA256: ' + trigger['sha256'], source_view(trigger['source'])[0]]
    return '\n'.join(lines)


def short(text, limit=160):
    text = ' '.join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def crud_info(o, model):
    """Two or three lines above a CRUD method: what runs, which Forms triggers, and why it may be off."""
    b, op = o['block'], o['op']
    enabled, ready, blockers = capability(b, op)
    words = {'list': 'lista', 'search': 'keresés a Forms WHERE feltételével', 'create': 'új rekord', 'update': 'módosítás', 'delete': 'törlés'}
    state = 'engedélyezett' if enabled else 'a MODULE_REVIEWED kapcsolóval engedélyezhető' if ready else 'tiltva'
    lines = [f"Forms blokk: {b['name']} ({b['table']}) · {words.get(op, op)} · {state}."]
    events = {'list': {'POST-QUERY', 'POST-CHANGE'}, 'search': {'POST-QUERY', 'POST-CHANGE'},
              'create': {'POST-CHANGE', 'WHEN-VALIDATE-ITEM', 'WHEN-VALIDATE-RECORD', 'PRE-INSERT', 'ON-INSERT', 'POST-INSERT', 'POST-QUERY'},
              'update': {'POST-CHANGE', 'WHEN-VALIDATE-ITEM', 'WHEN-VALIDATE-RECORD', 'PRE-UPDATE', 'ON-UPDATE', 'POST-UPDATE', 'POST-QUERY'},
              'delete': {'ON-CHECK-DELETE-MASTER', 'PRE-DELETE', 'ON-DELETE', 'POST-DELETE'}}[op]
    how = []
    for t in model['triggers']:
        if t['block'] != b['name'] or t['event'] not in events:
            continue
        kind = ('AI-ra vár' if t.get('ai') else 'PL/SQL az adatbázisban' if t.get('passthrough') else 'Java') if t['status'] == 'converted' else \
            ('Forms-futtatókörnyezet, kihagyva' if t.get('forms_runtime') else 'keretrendszer, kihagyva') \
                if t['status'] == 'framework' else 'kézi átültetés'
        how.append(t['event'] + ' (' + kind + ')')
    if how:
        lines.append('Triggerek: ' + ', '.join(sorted(set(how))) + '.')
    if not enabled and blockers:
        lines.append('Ok: ' + short(blockers[0]) + (f' (+{len(blockers) - 1})' if len(blockers) > 1 else ''))
    lines.append('SQL és triggerkód: ebben a DPS ServiceImpl-ben; részletek: analysis/backend-evidence.md')
    return lines


def unresolved_note(names, model):
    """External calls resolved by the database at run time; attached libraries without a .pld are named."""
    from .libraries import unloaded
    libraries = unloaded(model)
    note = 'Az adatbázis oldja fel futáskor: ' + ', '.join(names)
    if libraries:
        note += ('. A formhoz csatolt könyvtár(ak): ' + ', '.join(libraries)
                 + '; ha a rutin ott van, a hívás HTTP 501-et ad a rutin nevével, a többi művelet működik.')
    return note


def units_note(prepared):
    inline = prepared.get('inline_units') or {}
    shared = [n for n in prepared['units'] if n not in inline]
    return ((['Hívott Forms-eljárások (PlsqlUnits): ' + ', '.join(shared)] if shared else [])
            + (['Helyi csomag, csak a hívott tagjai (a blokk szövegében): ' + ', '.join(inline)] if inline else []))


def plsql_units_class(model, used, used_ui=()):
    """The form's own procedures and functions: runnable in Oracle, named, with their callers.

    used_ui: units of buttons and start-up code, whose Forms built-ins are screen commands (<NAME>_UI).
    """
    from .plsql_passthrough import unit_constant, Rewriter
    callers = {}
    for t in model['triggers']:
        for unit in (t.get('passthrough') or {}).get('units', []):
            callers.setdefault(unit, []).append(t['id'])
    for unit in ((model.get('init_plan') or {}).get('passthrough') or {}).get('units', []):
        callers.setdefault(unit, []).append('indítási végpont')
    lines = ['    /**', '     * A Forms-modul saját programegységei (Program Units). A futtathatók a triggerek és gombok',
             '     * névtelen blokkjaiba ágyazva az adatbázisban futnak; a :BLOKK.MEZŐ hivatkozások nv_* változók lettek.',
             '     */', '    static final class PlsqlUnits {', '        private PlsqlUnits() {}']
    for name, unit in sorted(model['plsql_units'].items()):
        if name not in used:
            continue  # unused Forms framework libraries stay in the analysis only
        info = [f"{name} ({unit['kind']}" + (f", csatolt könyvtár: {unit['library']}" if unit.get('library') else '') + ')',
                'Hívja: ' + (', '.join(sorted(set(callers.get(name, [])))) or 'egyik generált végpont sem')]
        if unit['binds']:
            info.append('Mezők: ' + ', '.join(f"{b} -> {Rewriter.var(b)}" for b in unit['binds']))
        if unit['calls']:
            info.append('Hívott helyi eljárások: ' + ', '.join(unit['calls']))
        if unit['unresolved']:
            info.append('Adatbázisban feloldott hívások: ' + ', '.join(unit['unresolved']))
        if unit['error']:
            info += ['NEM FUTTATHATÓ AZ ADATBÁZISBAN: ' + unit['error'], 'Eredeti forrás:'] + source_view(unit['source'])[0].splitlines()
            lines += ['', comment_lines('\n'.join(info), '        ')]
            continue
        lines += ['', comment_lines('\n'.join(info), '        '),
                  f"        static final String {unit_constant(name)} = " + java_text_block(unit['text'], '                ') + ';']
    for name, unit in sorted(model.get('plsql_units_ui', {}).items()):
        if name not in used_ui or unit['error']:
            continue
        info = [f"{name} ({unit['kind']}" + (f", csatolt könyvtár: {unit['library']}" if unit.get('library') else '')
                + "), gombok és indítási kód változata: a Forms-hívások felületi utasítások",
                'Hívja: ' + (', '.join(sorted(set(callers.get(name, [])))) or 'egyik generált végpont sem')]
        if unit.get('commands'):
            info.append('Forms-hívások: ' + ', '.join(dict.fromkeys(unit['commands'])))
        lines += ['', comment_lines('\n'.join(info), '        '),
                  f"        static final String {unit_constant(name)}_UI = " + java_text_block(unit['text'] or ' ', '                ') + ';']
    lines.append('    }')
    return '\n'.join(lines)


def short_skip(reason):
    if reason.startswith('Keretrendszer-blokk'):
        return 'keretrendszer-blokk, nincs adatforrás'
    if 'QueryDataSourceName' in reason:
        return 'nincs adatforrás (DatabaseDataBlock=true, de nincs tábla)'
    return reason


def module_header(model, cls):
    """One comment for everything that is module-wide, instead of one per method."""
    gate = model.get('module_gate', {})
    lines = []
    if gate.get('required'):
        lines += ['MODULSZINTŰ ELLENŐRZÉS: minden generált adatvégpontra (CRUD, keresés, LOV) vonatkozik, ezért itt, egyszer szerepel.']
        lines += ['  - ' + reason for reason in gate['reasons']]
        lines += ['Ha ezeket ellenőrizted, állítsd true-ra a MODULE_REVIEWED értékét: a saját tiltás nélküli ("kész")',
                  'műveletek ekkor élesednek; a saját tiltással rendelkezők továbbra is HTTP 501-et adnak.']
    skipped = skipped_blocks(model)
    ops = [(b['name'], o) for b in model['blocks'] for o in b.get('skipped_operations', [])]
    if skipped or ops:
        if lines: lines.append('')
        lines.append('Nem generált végpontok (részletek: analysis/backend-plan.json):')
        lines += ['  - ' + s['block'] + ': ' + short_skip(s['reason']) for s in skipped]
        grouped = {}
        for block, o in ops:
            grouped.setdefault(block, []).append(o['operation'] + ' (' + o['reason'].split(':')[0] + ')')
        lines += ['  - ' + block + ': ' + ', '.join(items) for block, items in grouped.items()]
    text = ''.join('    //' + (' ' + line.replace('\\', '[backslash]') if line else '') + '\n' for line in lines)
    if gate.get('required'):
        live = bool(model.get('options', {}).get('backend_live'))
        if live:
            text += '    // backend_live: generáláskor élesítve; a fenti pontokat ettől még érdemes átnézni.\n'
        text += '    static final boolean MODULE_REVIEWED = ' + str(live).lower() + ';\n'
    return text


def manual_source(action, discovery) -> str:
    """The Forms code of a button that is not ported yet, as Java comments in its method body (a foldable region)."""
    from .query_java import region
    codes = discovery.get('_code_index', {})
    sections = [(codes[cid]['path'] + ':', codes[cid]['source_view']) for cid in action.get('reachable_code', []) if cid in codes]
    sections += [('DO_KEY által indított key-trigger (jelölt): ' + codes[cid]['path'] + ':', codes[cid]['source_view'])
                 for cid in action.get('event_code_candidates', []) if cid in codes]
    if not sections:
        return ''
    return region('Eredeti Forms-kód kiindulásnak (nem fut; a trigger és az általa hívott helyi eljárások)', sections, '            ')


def ai_action_sources(o, model, discovery) -> list[tuple[str, str]]:
    """The original code of an action for its AI stub: the trigger(s), the local units they call, DO_KEY targets."""
    from . import ai_triggers
    action = o['action']
    if action.get('init'):
        sections = []
        for trigger in model['triggers']:
            if trigger['id'] in action.get('init_triggers', []):
                source = (trigger.get('replacement') or {}).get('source', trigger['source'])
                sections.append((trigger['id'], source))
        seen = {label for label, _ in sections}
        for trigger_id, source in list(sections):
            for label, text in ai_triggers.unit_sections(source, model):
                if label not in seen:
                    seen.add(label); sections.append((label, text))
        return sections
    codes = discovery.get('_code_index', {})
    sections = [(codes[cid]['path'].rsplit('/', 1)[-1] if codes[cid]['kind'] != 'trigger' else action['owner'] + ' / ' + action['event'],
                 codes[cid]['source_view']) for cid in action.get('reachable_code', []) if cid in codes]
    sections += [('DO_KEY által indított key-trigger (jelölt): ' + codes[cid]['path'], codes[cid]['source_view'])
                 for cid in action.get('event_code_candidates', []) if cid in codes]
    return sections


def ai_action_method(o, model, discovery, log1x, user_type, blocks) -> str:
    """backend_trigger_mode=ai: the endpoint frame of a button / the start-up code, its body for the Java assistant."""
    from . import ai_triggers
    action = o['action']
    label = action['owner'] + ' / ' + action['event']
    reads, parameters = o.get('ai_reads') or action_reads(action, discovery)
    query = o.get('query_action') or {}
    notes = ['AI-ra vár. A képernyő ezeket küldi: ' + (', '.join(reads) or '—')
             + ((' · paraméterek: ' + ', '.join(parameters)) if parameters else '') + '.']
    trigger = next((t for t in model['triggers'] if t['owner'] == action['owner'] and t['event'] == action['event']), {})
    notes += ai_triggers.framework_note('', None, trigger.get('framework_calls'))
    if query:
        target = next(b for b in blocks if b['name'] == query['target'])
        notes.append('Lekérdezőgomb: a(z) ' + target['name'] + ' (' + target['table'] + ') sorait adja vissza '
                     '(PageResult<' + target['class'] + 'Row>), a Forms-kód szűrőjével és rendezésével.')
    original = ai_triggers.region(label, ai_action_sources(o, model, discovery), '            ', notes)
    info = [label + ': a Forms-kód Java-változatát a Java asszisztens (helyi AI) írja ide.',
            'A metódus kerete (végpont, DTO, tranzakció, log1x) végleges; csak a törzs változik.']
    if action.get('init'):
        info.append('Indítási végpont: a képernyő megnyitáskor hívja (PRE-FORM, WHEN-NEW-FORM-INSTANCE).')
    if query:
        returns, request = f"PageResult<{target['class']}Row>", 'QueryActionRequest'
    else:
        returns, request = 'ActionResult', 'ActionRequest'
    return (comment_lines('\n'.join(info), '    ') + f'''
    @org.springframework.transaction.annotation.Transactional(rollbackFor = Exception.class)
    @Override
    public {returns} {o['method']}({user_type} user, {request} request) throws Exception {{
        {log1x(o, '() -> {')}
{original}{ai_triggers.placeholder(label, '            ')}
        }});
    }}''')


def action_reads(action, discovery=None) -> tuple[list[str], list[str]]:
    """The screen values an action's code references (discovery binds): BLOCK.ITEM and :GLOBAL/:PARAMETER/:SYSTEM.

    The key triggers a DO_KEY starts (event_code_candidates) count too: their code runs in the same request.
    """
    binds = list(action.get('binds', []))
    codes = (discovery or {}).get('_code_index') or {c['id']: c for c in (discovery or {}).get('code', [])}
    for cid in action.get('reachable_code', []) + action.get('event_code_candidates', []):
        binds += [b['name'] for b in codes.get(cid, {}).get('binds', [])]
    sources = [b.upper() for b in dict.fromkeys(binds) if '.' in b and b.split('.', 1)[0].upper() != 'FRM']
    sources = list(dict.fromkeys(sources))
    context = [s for s in sources if s.split('.', 1)[0] in {'GLOBAL', 'PARAMETER', 'SYSTEM'}]
    return [s for s in sources if s not in context], context


def action_evidence(action, discovery):
    codes = discovery['_code_index']
    lines = ['REVIEW REQUIRED: '+action['owner']+' / '+action['event'],
             'Source: '+action['source_file']+' SHA256: '+action['source_sha256'],
             'TODO: verify event hierarchy, authorization, tenant/row scope and transaction boundaries.',
             'If needed, put @Transactional on the public action entry point, not on this self-invoked hook.',
             'TODO: define typed input/output parameters from approved DB/PLL signatures.',
             'Local FMB program units cannot be invoked as JDBC stored procedures.',
             'Binds (directions unverified): '+', '.join(action['binds']),
             'External/unresolved call candidates: '+', '.join(action['external_calls'])]
    for cid in action['reachable_code']:
        c = codes[cid]
        lines += ['', 'SOURCE '+c['path'], 'Raw: analysis/discovery/'+c['source_file'], c['source_view']]
        lines += ['CALL CANDIDATE ['+call['kind']+'] '+call['text'] for call in c['calls']]
    for cid in action.get('event_code_candidates', []):
        c = codes[cid]
        lines += ['', 'KEY/EVENT SOURCE CANDIDATE (scope/hierarchy unverified): ' + c['path'],
                  'Raw: analysis/discovery/' + c['source_file'], c['source_view']]
        lines += ['CALL CANDIDATE ['+call['kind']+'] '+call['text'] for call in c['calls']]
    return '\n'.join(lines)


def crud_evidence(op, model, discovery, values):
    b, verb = op['block'], op['verb']
    p = b['properties']
    lines = [f"Forms blokk: {b['name']} | művelet: {verb}",
             'Adatforrás: '+p.get('querydatasourcetype', 'Table')+' / '+p.get('querydatasourcename', b['table']),
             'Ellenőrzött mapping: '+b['table'],
             'Kulcs: '+(', '.join(i['name']+' -> '+i['column'] for i in b['pk']) or 'nincs igazolt kulcs'),
             'Mezők/oszlopok: '+', '.join(i['field']+' -> '+i['column'] for i in b['db_items'])]
    for key, label in [('whereclause', 'Eredeti WHERE'), ('orderbyclause', 'Eredeti ORDER BY'),
                       ('querydatasourcecolumns', 'QueryDataSourceColumns'), ('dmldatasourcename', 'DML cél'),
                       ('dmldatatargetname', 'DML cél')]:
        if p.get(key):
            lines.append(label+': '+source_view(p[key])[0])
    for key in ('query_allowed', 'insert_allowed', 'update_allowed', 'delete_allowed'):
        lines.append(key+': '+str(b[key]).lower())
    enabled, ready, blockers = capability(b, op['op'])
    if enabled:
        lines.append('Generált művelet: engedélyezett')
    elif ready:
        lines.append('Generált művelet: kész, csak a modulszintű ellenőrzésre vár (MODULE_REVIEWED, lásd az osztály elején); addig HTTP 501.')
    else:
        lines.append('Generált művelet: HTTP 501, ellenőrzésig tiltott')
        lines += ['Tiltás oka: '+r for r in dict.fromkeys(blockers)] or ['Tiltás oka: a Forms-blokk ezt a műveletet statikusan nem engedi.']
    if verb == 'read':
        lines.append('Generált SQL: '+json.loads(values['SELECT_PAGE']))
        for bind in b.get('query_plan',{}).get('binds',[]):
            lines.append('Keresési bind: :'+bind['source']+' -> criteria.'+bind['field']+' -> :'+bind['parameter'])
    elif verb == 'create':
        lines.append('Generált SQL: '+json.loads(values['INSERT_SQL']))
        if b['sequence']:
            lines.append('Azonosító: SELECT '+b['sequence']+'.NEXTVAL FROM DUAL')
    else:
        lines.append('Rekord betöltése és zárolása: '+json.loads(values['SELECT_KEY'])+' FOR UPDATE NOWAIT')
        if verb == 'delete':
            lines.append('Generált SQL: '+json.loads(values['DELETE_SQL']))
        else:
            lines.append('UPDATE oszlopok: '+', '.join(i['column'] for i in b['db_items'] if i['update_allowed'] and not i['primary_key']))
    events = {
        'read': {'PRE-QUERY', 'POST-QUERY', 'POST-CHANGE', 'ON-SELECT', 'ON-FETCH'},
        'create': {'POST-CHANGE', 'WHEN-VALIDATE-ITEM', 'WHEN-VALIDATE-RECORD', 'PRE-INSERT', 'ON-INSERT', 'POST-INSERT', 'POST-QUERY'},
        'update': {'POST-CHANGE', 'WHEN-VALIDATE-ITEM', 'WHEN-VALIDATE-RECORD', 'PRE-UPDATE', 'ON-UPDATE', 'POST-UPDATE', 'POST-QUERY'},
        'delete': {'ON-CHECK-DELETE-MASTER', 'PRE-DELETE', 'ON-DELETE', 'POST-DELETE'},
    }[verb]
    if verb != 'read':
        events |= {'PRE-COMMIT', 'ON-COMMIT', 'POST-FORMS-COMMIT', 'POST-DATABASE-COMMIT', 'KEY-COMMIT'}
    codes = discovery['_code_index']
    for t in discovery['_block_triggers'][b['name']] + discovery['_form_triggers']:
        if t['event'] not in events:
            continue
        if t['status'] == 'framework':
            # Catalogued plumbing: one line, the source stays in analysis/discovery.
            label = 'csak Forms-futtatókörnyezet, nincs adatbázis-hívás' if t.get('forms_runtime') else 'keretrendszer, nem üzleti logika'
            lines += ['', f"TRIGGER {t['owner']} / {t['event']} [{label}]: " + '; '.join(t.get('framework_calls', []))]
            continue
        lines += ['', f"TRIGGER {t['owner']} / {t['event']} [{t['status']} -> {t['target']}]",
                  'SHA256: '+t['sha256'], source_view(t['source'])[0]]
        if t.get('framework_calls'):
            lines.append('Keretrendszer-hívás (katalógus, a fordításból kihagyva): '+'; '.join(t['framework_calls']))
        if t.get('forms_runtime'):
            lines.append('Csak Forms-futtatókörnyezetben működő hívás (katalógus, nem kerül az adatbázisba): '
                         + '; '.join(r['call'] + ' [' + r['pattern'] + ']' for r in t['forms_runtime']))
        if t.get('passthrough'):
            info = t['passthrough']
            lines.append('Futtatás: az eredeti PL/SQL az adatbázisban (névtelen blokk); visszaírt mezők: '
                         + (', '.join(info['written']) or '—') + (('; átalakítás: ' + '; '.join(info['notes'])) if info['notes'] else '')
                         + (('; beágyazott eljárások: ' + ', '.join(info['units'])) if info['units'] else ''))
            if info.get('unresolved'):
                lines.append(unresolved_note(info['unresolved'], model))
            if info.get('inputs'):
                lines.append(input_note(info))
        for unit in t.get('inlined_program_units', []):
            lines += ['HELYI PROCEDURE '+unit['name']+' SHA256: '+unit['sha256'], source_view(unit['source'])[0]]
        for c in discovery['_trigger_index'].get((t['owner'],t['event']),[]):
            lines.append('Forrás: analysis/discovery/'+c['source_file'])
            for call in c['calls']:
                lines.append('CALL CANDIDATE ['+call['kind']+'] '+call['text'])
                lines += ['Helyi forrás: analysis/discovery/'+codes[cid]['source_file'] for cid in call['target_ids']]
    lov_names = {i['lov'].upper() for i in b['items'] if i['lov']}
    group_names = {group for n in lov_names for group in discovery['_lov_groups'].get(n,())}
    for group in sorted(group_names):
        for obj in discovery['_record_groups'].get(group,[]):
            sql = obj['properties'].get('recordgroupquery', '')
            if sql:
                lines.append('LOV rekordcsoport (külön bekötendő): '+obj['name']+'\n'+source_view(sql)[0])
    lines += ['', 'Folytatás: a DPS ServiceImpl SQL/mapping és szabálymetódusaiban.',
              'TODO: ellenőrizd a WHERE/ORDER BY, bindek, jogosultsági/tenant-szűrés és DB-triggerek egyenértékűségét.',
              'A fenti eredeti SQL/PLSQL bizonyíték; nem kerül automatikusan végrehajtásra.',
              'Teljes forrás és blokkoló okok: analysis/form.ir.json, analysis/discovery/form-map.json.']
    return '\n'.join(lines)


COMMON_TOOLS_CLASSES = ('SqlValues', 'RuleContext', 'FormsErrors', 'DbCalls', 'PlsqlValues', 'FormsPlsql', 'LovQuery', 'FormsChecks')


def common_tools_package(config) -> str:
    return config.get('common_migrate_tools_package') or config['java_package'] + '.cl'


def write_common_tools(output: Path, config) -> str:
    """CommonMigrateTools (CL): one helper file for every module of the project; identical each time."""
    package = common_tools_package(config)
    text = (Path(__file__).with_name('templates') / 'CommonMigrateTools.java.tpl').read_text(encoding='utf-8')
    write(output / 'backend' / 'CL' / 'CommonMigrateTools.java', text.replace('@@PACKAGE@@', package))
    return package


def value_class(declaration, fields, extra_constructors=()):
    """A Java 11 DTO for the wire: public fields (Jackson), no-arg and full constructors, record-style accessors.

    extra_constructors: leading field counts of further constructors (an older contract still compiles).
    """
    name = declaration.split('<')[0]
    body = [f'    public static class {declaration} {{']
    body += [f'        public {t} {n};' for t, n in fields]
    body.append(f'        public {name}() {{}}')
    for count in extra_constructors:
        body.append(f'        public {name}(' + ', '.join(f'{t} {n}' for t, n in fields[:count]) + ') { '
                    + ' '.join(f'this.{n} = {n};' for _, n in fields[:count]) + ' }')
    body.append(f'        public {name}(' + ', '.join(f'{t} {n}' for t, n in fields) + ') { '
                + ' '.join(f'this.{n} = {n};' for _, n in fields) + ' }')
    body += [f'        public {t} {n}() {{ return {n}; }}' for t, n in fields]
    return '\n'.join(body) + '\n    }\n'


def nested_runtime(filename, config):
    """Read a body-only template: never rewrite already-interpolated source text."""
    return template(filename, {}, config)


def generate(model, output: Path, config, module, package, discovery, actions):
    # Build once: a large module used to rescan every code unit/object for each
    # CRUD method and trigger comment (quadratic on modules with many triggers).
    discovery = {**discovery, '_code_index':{c['id']:c for c in discovery['code']},
                 '_trigger_index':defaultdict(list), '_block_triggers':defaultdict(list),
                 '_form_triggers':[], '_lov_groups':defaultdict(set), '_record_groups':defaultdict(list)}
    for c in discovery['code']:
        if c['kind']=='trigger': discovery['_trigger_index'][(c['owner'],c['name'])].append(c)
    for t in model['triggers']:
        if t['block']: discovery['_block_triggers'][t['block']].append(t)
        else: discovery['_form_triggers'].append(t)
    for obj in discovery['objects']:
        if obj['kind']=='lov': discovery['_lov_groups'][obj['name'].upper()].add(obj['properties'].get('recordgroupname','').upper())
        elif obj['kind']=='recordgroup': discovery['_record_groups'][obj['name'].upper()].append(obj)
    cls = name(module, 'pascal')
    cl = cl_package(config, package)  # where the developer puts the module's CL files
    init = model.get('init_plan') or {}
    if init.get('status') in ('generated', 'ai'):
        # The form's start-up code (PRE-FORM, WHEN-NEW-FORM-INSTANCE): the screen calls it when it opens.
        events = {t.split(':', 1)[1] for t in init['triggers']}
        init_binds = [b['name'] for c in discovery['code'] if c['kind'] == 'trigger' and not c.get('block')
                      and not c.get('item') and c.get('name') in events for b in c.get('binds', [])] if init['status'] == 'ai' else []
        actions = list(actions) + [{'owner': INIT_OWNER, 'event': ' + '.join(t.split(':', 1)[1] for t in init['triggers']),
                                    'block': None, 'item': None, 'key': 'init', 'api_name': 'init',
                                    'path': config['api_prefix'].rstrip('/') + '/' + module + '/init', 'method': 'POST',
                                    'method_name': 'onFormInit', 'trigger_id': None, 'reachable_code': [],
                                    'event_code_candidates': [], 'binds': list(dict.fromkeys(init_binds)), 'external_calls': [],
                                    'source_file': '', 'source_sha256': '', 'init': True, 'init_triggers': init['triggers'],
                                    'ai': init['status'] == 'ai'}]
    ops = operations(model, actions)
    # LOVs: reviewed read-only SQL from RecordGroupQuery, one endpoint per LOV.
    from .backend_lovs import lov_plans, lov_method, lov_evidence
    from . import framework
    lovs = lov_plans(model, discovery, framework.load(config)) if config.get('backend_lov_endpoints', True) else []
    model['lov_plans'] = lovs
    ops += [dict(lov=l, op='lov', verb='lov:'+l['name'], http='Post', returns='LovResult', args='LovRequest request',
                 controller_args='@RequestBody LovRequest request', call='request', method='lov'+l['class'],
                 constant='LOV_'+l['class'].upper()+'_PATH') for l in lovs]
    # Forms COMMIT_FORM: every changed record of the screen in one request, in Forms order (commit_chain).
    from . import commit_chain
    commit = commit_chain.operation(backend_blocks(model))
    if commit:
        ops.append(commit)
    gated = bool(model.get('module_gate', {}).get('required'))
    live = bool(model.get('options', {}).get('backend_live'))
    # Button triggers that only need the database run as written (rules.action_passthrough).
    for o in ops:
        if o['op'] != 'action':
            continue
        if o['action'].get('init'):
            o['passthrough'] = init.get('plan')
            o['ai'] = init.get('status') == 'ai'
            if o['ai']:
                o['ai_reads'] = action_reads(o['action'], discovery)
            continue
        trigger = next((t for t in model['triggers'] if t['owner'] == o['action']['owner'] and t['event'] == o['action']['event']), None)
        o['passthrough'] = trigger.get('passthrough_plan') if trigger else None
        if trigger and trigger.get('ai'):
            o['ai'] = True  # backend_trigger_mode=ai: the frame only, the body is the Java assistant's (ai_action_method)
            o['ai_reads'] = action_reads(o['action'], discovery)
            o['ai_alerts'] = any(re.search(r'\bSHOW_ALERT\s*\(', text, re.I) for _, text in ai_action_sources(o, model, discovery))
            continue
        if trigger and trigger.get('query_java_reason') and 'SET_BLOCK_PROPERTY' in str(trigger.get('source', '')).upper():
            o['query_java_reason'] = trigger['query_java_reason']  # why this query button is not a Java query
        if trigger and not o['passthrough'] and trigger['status'] == 'review':
            o['passthrough_reason'] = trigger.get('reason', '')
            o['adapter_diagnostics'] = trigger.get('adapter_diagnostics', {})
    from .query_actions import blockers as query_action_blockers
    for o in ops:
        if o.get('query_action') and not o.get('ai'):
            o['query_blockers'] = query_action_blockers(o['query_action'], model)
    if commit:
        # Buttons with a commit point: commitForm runs their code up to the point first (commit_points).
        commit['preludes'] = [(o['action']['owner'], runner_name(o)) for o in ops
                              if o['op'] == 'action' and (o.get('passthrough') or {}).get('commit_points')]
        commit['reads'] = commit_chain.screen_reads(commit, model, discovery)  # the screen sends them with the changes
    runnable_actions = [o for o in ops if (o.get('passthrough') or o.get('query_action')) and not o.get('ai')]
    blocks = backend_blocks(model)
    served = {b['name'] for b in blocks}
    folders = {layer: output/'backend'/layer for layer in ('CL', 'DPS', 'WBS')}
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    imports = f'import {cl}.{cls}Dtos.*;\nimport {cl}.{cls}Constants;\nimport java.util.List;\n'
    def emit(layer, suffix, body, extra=''):
        write(folders[layer]/f'{cls}{suffix}.java', f'package {layer_package(config, package, layer)};\n\n'+extra+'\n'+body+'\n')

    dto_rows, selections = [], []
    for b in model['blocks']:
        fields = row_fields(b) if b['name'] in served else []
        selections.append({'block': b['name'], 'dto': b['class']+'Row' if b['name'] in served else None,
                           'reason': 'CRUD contract; includes hidden keys and rule inputs' if b['name'] in served else
                           b.get('backend_skip') or 'No CRUD contract. Control values stay in ActionRequest.blocks when an action needs them.',
                           'fields': [i['name'] for i in fields]})
        if b['name'] in served:
            dto_rows.append(comment_lines('Forms rekord: '+b['name']+'; adatforrás: '+b['table'], '    ')+'\n'
                                                                                                          f'    public static class {b["class"]}Row {{\n'+indent(row_declarations(fields), '    ')+'\n    }')
            if b['endpoint_plan'].get('search'):
                criteria = [v['item'] for v in b['query_plan']['binds']]
                dto_rows.append(f'    public static class {b["class"]}Criteria {{\n'+indent(row_declarations(criteria),'    ')+'\n    }')
    wrappers = ''
    if blocks:
        wrappers += (value_class('RowResult<T>', [('T', 'row'), ('List<String>', 'messages')])
                     # hasMore: the page left rows out (the list asks one row more); (rows, messages) still compiles
                     + value_class('PageResult<T>', [('List<T>', 'rows'), ('List<String>', 'messages'), ('boolean', 'hasMore')], (2,))
                     + value_class('UpdateRequest<T>', [('T', 'original'), ('T', 'value')]))
    if any(o['op']=='search' for o in ops):
        wrappers += value_class('SearchRequest<T>', [('T', 'criteria'), ('int', 'offset'), ('int', 'limit')])
    if commit:
        wrappers += commit_chain.dtos(commit, value_class)
    if lovs:
        wrappers += '''    // LOV: term = typed text (LIKE 'term%' on the displayed column), parameters = Forms binds by BLOCK.ITEM.
''' + value_class('LovRequest', [('String', 'term'), ('Map<String, String>', 'parameters'), ('Integer', 'limit')]) \
                    + value_class('LovResult', [('List<Map<String, Object>>', 'rows')])
    if actions:
        wrappers += '''    // Review contract: Oracle item names, decimal/date strings; validate types and bind directions in DPS.
    // parameters: :GLOBAL.*, :PARAMETER.*, :SYSTEM.* values of the screen; FRM.ALERTS = the alert answers so far.
''' + value_class('ActionRequest', [('Map<String, Map<String, String>>', 'blocks'), ('Map<String, String>', 'parameters')]) \
                    + '''    // commands: the Forms built-ins the screen executes in order (GO_BLOCK, SET_ITEM_PROPERTY, EXECUTE_QUERY,
    // SHOW_ALERT ...): [operation, arguments...]; globals: the written :GLOBAL values.
''' + value_class('ActionResult', [('Map<String, Map<String, String>>', 'blocks'), ('List<String>', 'messages'),
                                           ('List<List<String>>', 'commands'), ('Map<String, String>', 'globals')],
                              extra_constructors=(2,))
    if any(o.get('query_action') for o in ops):
        wrappers += value_class('QueryActionRequest', [('Map<String, Map<String, String>>', 'blocks'),
                                                      ('Map<String, String>', 'parameters'), ('int', 'offset'), ('int', 'limit')])
    emit('CL', 'Dtos', f'''/** Only endpoint DTOs. Oracle NUMBER stays a decimal string on the wire. */
public final class {cls}Dtos {{
    private {cls}Dtos() {{}}
{wrappers}
{chr(10).join(dto_rows)}
}}''', 'import java.util.List;\n'+('import java.util.Map;\n' if actions or lovs else ''))

    base = config['api_prefix'].rstrip('/')+'/'+module
    constants = [f'    public static final String BASE_PATH = {jstr(base)};']
    for op in ops:
        path = (op['action']['path'][len(base):] if op['op'] == 'action' else '/lov/'+op['lov']['key'] if op['op'] == 'lov'
        else '/commit' if op['op'] == 'commit'
        else '/'+op['block']['key']+'/'+('query/search' if op['op']=='search' else config['endpoint_names'][op['op']]))
        op['path'] = path  # the frontend API map mirrors these exact values
        constants.append(f'    public static final String {op["constant"]} = {jstr(path)};')
    for op in ops:
        # log1x(log, <Module>Constants.<METHOD>_NAME, user, null, () -> ...): the logged operation name.
        op['name_constant'] = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', op['method']).upper() + '_NAME'
        constants.append(f'    public static final String {op["name_constant"]} = {jstr(op["method"])};')
    emit('CL', 'Constants', f'public final class {cls}Constants {{\n    private {cls}Constants() {{}}\n'+'\n'.join(constants)+'\n}')
    methods = '\n'.join(f'    {o["returns"]} {o["method"]}({o["args"]});' for o in ops)
    user_type = config['java_user_type']
    service_methods = '\n'.join(f'    {o["returns"]} {o["method"]}({user_type} user{", " + o["args"] if o["args"] else ""}) throws Exception;' for o in ops)
    company_symbols = sorted({t for key in ('java_service_base_dps', 'java_service_base_wbs', 'java_controller_base_dps',
                                            'java_controller_base_wbs', 'java_user_type') for t in re.findall(r'[A-Za-z_]\w*', config[key])})
    # Company classes (base classes, UserDto): imported only when configured; otherwise the developer imports them.
    company = ''.join(f'import {i};\n' for i in config['java_company_imports'])
    logging = 'import lombok.extern.slf4j.XSlf4j;\n'
    emit('CL', 'RestClient', f'''public interface {cls}RestClient {{
{methods}
}}''', imports)
    client_methods = []
    for o in ops:
        path = f'{cls}Constants.{o["constant"]}'
        reference = f'new ParameterizedTypeReference<{o["returns"]}>() {{}}'
        if o['op'] == 'list':
            request = f'HttpMethod.GET, {path} + "?offset={{offset}}&limit={{limit}}", null, {reference}, offset, limit'
        else:
            body = 'new UpdateRequest<>(original, value)' if o['op'] == 'update' else o['call']
            request = f'HttpMethod.{o["http"].upper()}, {path}, {body}, {reference}'
        client_methods.append(f'''    @Override public {o['returns']} {o['method']}({o['args']}) {{
        return call({request});
    }}''')
    emit('CL', 'RestClientImpl', f'''/** RestTemplate from the WBS host (RestTemplateBuilder): authentication, interceptors and timeouts. */
public class {cls}RestClientImpl implements {cls}RestClient {{
    private final RestTemplate rest;
    private final String baseUrl;
    public {cls}RestClientImpl(RestTemplate rest, String baseUrl) {{
        this.rest = Objects.requireNonNull(rest);
        this.baseUrl = Objects.requireNonNull(baseUrl);
    }}
{chr(10).join(client_methods)}
    private <T> T call(HttpMethod method, String path, Object body, ParameterizedTypeReference<T> type, Object... variables) {{
        try {{
            HttpEntity<Object> entity = body == null ? null : new HttpEntity<>(body);
            T result = rest.exchange(baseUrl + {cls}Constants.BASE_PATH + path, method, entity, type, variables).getBody();
            if (result == null) throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "Üres DPS-válasz.");
            return result;
        }} catch (RestClientResponseException error) {{
            HttpStatus status = HttpStatus.resolve(error.getRawStatusCode());
            throw new ResponseStatusException(status == null ? HttpStatus.BAD_GATEWAY : status, "A DPS elutasította a kérést.", error);
        }} catch (RestClientException error) {{
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "A DPS nem érhető el.", error);
        }}
    }}
}}''', imports+'''import java.util.Objects;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.server.ResponseStatusException;
''')

    for layer in ('DPS', 'WBS'):
        emit(layer, 'Service', f'public interface {cls}Service {{\n{service_methods}\n}}', imports + company)
        mappings, implementations = [], []
        for o in ops:
            mapping = f'    @{o["http"]}Mapping({cls}Constants.{o["constant"]})\n'
            if o['op'] == 'create':
                mapping += '    @ResponseStatus(HttpStatus.CREATED)\n'
            mapping += f'    {o["returns"]} {o["method"]}({o["controller_args"]}) throws Exception;'
            mappings.append(mapping)
            args = o['controller_args'].replace('@RequestBody ', '') if o['op'] == 'update' else o['args']
            args = re.sub(r'@\w+(?:\([^)]*\))?\s+', '', args)
            call = 'request.original(), request.value()' if o['op'] == 'update' else o['call']
            if o['op'] == 'update':
                body = ('() -> {\n            if (request == null) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Hiányzó kérés.");\n'
                        f'            return service.{o["method"]}(user, {call});\n        }}')
            else:
                body = f'() -> service.{o["method"]}(user{", " + call if call else ""})'
            implementations.append(f'''    @Override
    public {o["returns"]} {o["method"]}({args}) throws Exception {{
        {user_type} user = {config['java_user_expression']};
        return log1x(log, {cls}Constants.{o["name_constant"]}, user, null, {body});
    }}''')
        emit(layer, 'Controller', f'public interface {cls}Controller {{\n'+ '\n'.join(mappings)+'\n}',
             imports+'import org.springframework.web.bind.annotation.*;\nimport org.springframework.http.HttpStatus;\n')
        controller_base = config['java_controller_base_' + layer.lower()]
        emit(layer, 'ControllerImpl', f'''/** CREATE_ONCE: --regenerate preserves host customizations. Every endpoint runs in log1x. */
@XSlf4j
@RestController({jstr(layer_package(config, package, layer)+'.'+cls+'Controller')})
@RequestMapping({cls}Constants.BASE_PATH)
public class {cls}ControllerImpl extends {controller_base} implements {cls}Controller {{
    private final {cls}Service service;

    public {cls}ControllerImpl({cls}Service service) {{
        this.service = java.util.Objects.requireNonNull(service);
    }}

{(chr(10) + chr(10)).join(implementations)}
}}''', imports + company + logging + 'import org.springframework.http.HttpStatus;\nimport org.springframework.web.bind.annotation.*;\n'
                                     'import org.springframework.web.server.ResponseStatusException;\n')

    wbs_methods = []
    dps_methods, evidence = [], []
    support, operation_bodies = [], {}
    from .service_inline import inline_block
    vals = {b['name']: block_values(model, b) for b in blocks}
    for b in blocks:
        # Only the internals of generated endpoints: no create() for InsertAllowed=false.
        sections = set().union(*(OPERATION_SECTIONS[op] for op in OPERATION_SECTIONS if b['endpoint_plan'].get(op)))
        source = template('CompactBlock.java.tpl', {**vals[b['name']], 'RULE_METHODS': rule_methods(model, b)}, config, sections)
        bodies, helpers = inline_block(source, b, b['endpoint_plan'])
        operation_bodies[b['name']] = bodies
        support.append(helpers)
    # A block lambda ('() -> {') is closed by the caller with '});'.
    log1x = lambda o, body: (f"return log1x(log, {cls}Constants.{o['name_constant']}, user, null, {body}"
                             + ('' if body.endswith('{') else ');'))
    for o in ops:
        block = (o['action']['block'] or '@FORM' if o['op'] == 'action' else o['lov']['block'] if o['op'] == 'lov'
                 else '@FORM' if o['op'] == 'commit' else o['block']['name'])
        wbs_methods.append(f'''    @Override
    public {o['returns']} {o['method']}({user_type} user, {o['args']}) throws Exception {{
        {log1x(o, '() -> client.' + o['method'] + '(' + o['call'] + ')')}
    }}''')
        if o['op'] == 'lov':
            dps_methods.append(lov_method(o['lov'], gated, comment_lines, log1x(o, '() -> {'), user_type, o['method']))
            evidence.append((o['method'], '\n'.join(lov_evidence(o['lov'], gated))))
        elif o['op'] == 'action' and o.get('ai'):
            dps_methods.append(ai_action_method(o, model, discovery, log1x, user_type, blocks))
            evidence.append((o['method'], 'AI-RA VÁR: ' + o['action']['owner'] + ' / ' + o['action']['event'] + '\n\n'
                             + (init_evidence(o, model) if o['action'].get('init') else action_evidence(o['action'], discovery))))
        elif o['op'] == 'action' and o.get('query_action'):
            from .query_actions import java_method, evidence as query_evidence
            target = next(b for b in blocks if b['name'] == o['query_action']['target'])
            if o['query_action'].get('java'):
                from .query_java import method as java_query_method
                dps_methods.append(java_query_method(o, target, gated, log1x, user_type, support, model, discovery))
            else:
                dps_methods.append(java_method(o, target, gated, log1x, user_type, support))
            evidence.append((o['method'], query_evidence(o['query_action']) + '\n\n' + action_evidence(o['action'], discovery)))
        elif o['op'] == 'action' and o.get('passthrough'):
            dps_methods.append(action_method(o, block, gated, discovery, model, log1x, user_type))
            evidence.append((o['method'], init_evidence(o, model) if o['action'].get('init') else action_evidence(o['action'], discovery)))
        elif o['op'] == 'action':
            reason = o.get('passthrough_reason') or 'A gomb kódja kézi átültetést igényel.'
            info = [o['action']['owner'] + ' / ' + o['action']['event'] + ': az adatbázisban nem futtatható, kézi átültetés.',
                    *wrap('Ok: ' + reason, width=108, break_long_words=False, break_on_hyphens=False),
                    *(wrap('Java-lekérdezés sem készült: ' + o['query_java_reason'], width=108, break_long_words=False,
                           break_on_hyphens=False) if o.get('query_java_reason') else []),
                    'Teljes adapterdiagnózis: analysis/backend-plan.json; eredeti kód: analysis/backend-evidence.md']
            # The original code where the developer ports it: trigger, local units, DO_KEY targets.
            original = manual_source(o['action'], discovery)
            dps_methods.append(comment_lines('\n'.join(info), '    ') + f'''
    @Override
    public ActionResult {o['method']}({user_type} user, ActionRequest request) throws Exception {{
        {log1x(o, '() -> {')}
{original}            throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, {jstr('Ez a gomb még nincs átültetve: ' + o['action']['owner'])});
        }});
    }}''')
            evidence.append((o['method'], 'Tiltás oka: ' + reason + '\n'
                             + '\n'.join(name + ': ' + detail for name, detail in o.get('adapter_diagnostics', {}).items())
                             + '\n\n' + action_evidence(o['action'], discovery)))
        elif o['op'] == 'commit':
            dps_methods.append(commit_chain.method(o, model, log1x, user_type, gated))
            evidence.append((o['method'], commit_chain.evidence(o, model)))
        else:
            b = o['block']
            # POST-QUERY can contain an Oracle call (including logging/DML).
            # Do not promise a read-only connection for a read that runs it.
            has_query_rule = any(t['block'] == b['name'] and t['event'] == 'POST-QUERY' and
                                 t.get('target') == 'backend' and t.get('status') == 'converted'
                                 for t in model['triggers'])
            txn = ('@Transactional(readOnly = true, rollbackFor = Exception.class)'
                   if o['op'] in {'list','search'} and not has_query_rule else '@Transactional(rollbackFor = Exception.class)')
            body = operation_bodies[b['name']][o['op']]
            if o['op'] == 'update':
                body = 'var row = value;\n' + body
            dps_methods.append(comment_lines('\n'.join(crud_info(o, model)), '    ')+f'''
    {txn}
    @Override
    public {o['returns']} {o['method']}({user_type} user, {o['args']}) throws Exception {{
        {log1x(o, '() -> {')}
{indent(body, '            ')}
        }});
    }}''')
            evidence.append((o['method'], crud_evidence(o, model, discovery, vals[b['name']])))
    write_json(output / 'analysis/db-statements.json', db_statements(model, ops, vals, cls, '\n'.join(dps_methods + support)))
    write(output/'analysis/backend-evidence.md', '# Backend-bizonyíték\n\nA DPS ServiceImpl metódusai mögötti eredeti Forms SQL és PL/SQL, '
                                                 'és a generált SQL. A kódban csak rövid összefoglaló áll; a részletek itt vannak.\n\n'
          + ''.join('## ' + method + '\n\n```text\n' + text.strip() + '\n```\n\n' for method, text in evidence))
    dps_fields = []
    dps_fields += ['    private final NamedParameterJdbcTemplate jdbc;'] if blocks or lovs or runnable_actions else []
    ctor_args = 'NamedParameterJdbcTemplate jdbc' if blocks or lovs or runnable_actions else ''
    ctor_body = '\n'.join(['        this.jdbc = Objects.requireNonNull(jdbc);'] if blocks or lovs or runnable_actions else [])
    # SqlValues, RuleContext, DbCalls, PlsqlValues, LovQuery, FormsChecks, FormsErrors: nested classes of the
    # one CL helper file CommonMigrateTools, shared by every module - not copied into each ServiceImpl.
    tools_package = write_common_tools(output, config)
    runtime = ''
    from .plsql_passthrough import unit_constant
    used_code = '\n'.join(dps_methods + support)
    used_units = {unit for unit in model.get('plsql_units', {}) if re.search(r'PlsqlUnits\.' + unit_constant(unit) + r'\b', used_code)}
    used_ui = {unit for unit in model.get('plsql_units_ui', {}) if 'PlsqlUnits.' + unit_constant(unit) + '_UI' in used_code}
    if used_units or used_ui:
        runtime += ('\n' if runtime else '') + plsql_units_class(model, used_units, used_ui)
    if any((o.get('query_action') or {}).get('java') for o in ops):
        from .query_java import QUERY_TEXT_CLASS
        runtime += ('\n\n' if runtime else '') + QUERY_TEXT_CLASS
    dps_imports = imports+'''import org.springframework.stereotype.Service;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;
'''
    if runnable_actions and not blocks:
        # Only runnable button actions: the JDBC call helpers, nothing of the CRUD machinery.
        dps_imports += '''import java.sql.SQLException;
import java.sql.Types;
import java.math.BigDecimal;
import java.math.MathContext;
import java.util.Locale;
import java.util.function.Supplier;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
''' + ('''import java.util.ArrayList;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
''' if lovs else '')
    elif blocks:
        dps_imports += '''import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Types;
import java.math.BigDecimal;
import java.math.MathContext;
import java.util.Locale;
import java.util.ArrayList;
import java.util.function.Supplier;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.transaction.annotation.Transactional;
'''
    elif lovs:
        dps_imports += '''import java.math.BigDecimal;
import java.util.ArrayList;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.transaction.annotation.Transactional;
'''
    service_base = config['java_service_base_dps']
    service_text = '\n'.join(dps_methods + support) + runtime
    dps_imports += ''.join(f'import {tools_package}.CommonMigrateTools.{name};\n' for name in COMMON_TOOLS_CLASSES if re.search(r'\b' + name + r'\b', service_text))
    # java-variables.json: the imports and the @Autowired services of the variable values the methods use.
    from . import java_variables
    variable_imports, variable_fields = java_variables.class_parts(service_text, '\n'.join(dps_fields),
                                                                   f'backend/DPS/{cls}ServiceImpl.java')
    dps_imports += variable_imports
    variable_fields += '\n' if variable_fields else ''
    emit('DPS', 'ServiceImpl', f'''/** CREATE_ONCE: --regenerate preserves this file. SQL/PLSQL and private helpers are kept here; no domain/repository layer. */
@XSlf4j
@Service
public class {cls}ServiceImpl extends {service_base} implements {cls}Service {{
{variable_fields}{module_header(model, cls)}{chr(10).join(dps_fields)}

    public {cls}ServiceImpl({ctor_args}) {{
{ctor_body}
    }}

{(chr(10) + chr(10)).join(dps_methods)}

{(chr(10) + chr(10)).join(support)}

{runtime}
}}''', dps_imports + company + logging)
    java_queries = [o for o in ops if (o.get('query_action') or {}).get('java')]
    if config.get('query_java_tests') and java_queries:
        from .query_java import junit_test
        write(output / 'backend' / 'DPS-test' / f'{cls}QueryTextTest.java',
              junit_test(cls, layer_package(config, package, 'DPS'), java_queries))
    service_base = config['java_service_base_wbs']
    emit('WBS', 'ServiceImpl', f'''/** CREATE_ONCE: host adapter. No database or DPS class dependencies. Every method runs in log1x. */
@XSlf4j
@Service
public class {cls}ServiceImpl extends {service_base} implements {cls}Service {{
    private static final String GENERATED_DPS_URL = {jstr(config['dps_base_url'])};
    private final {cls}RestClient client;

    public {cls}ServiceImpl(RestTemplateBuilder builder,
            @Value("${{frm.{module}.dps-base-url:}}") String configuredUrl) {{
        String url = configuredUrl == null || configuredUrl.isBlank() ? GENERATED_DPS_URL : configuredUrl.trim();
        URI uri = URI.create(url);
        if (!("http".equals(uri.getScheme()) || "https".equals(uri.getScheme())) || uri.getHost() == null
            || uri.getRawUserInfo() != null || uri.getRawQuery() != null || uri.getRawFragment() != null)
            throw new IllegalArgumentException("Állítsd be: frm.{module}.dps-base-url");
        this.client = new {cls}RestClientImpl(builder.build(), url.replaceAll("/+$", ""));
    }}

{(chr(10) + chr(10)).join(wbs_methods)}
}}''', imports+company+logging+f'import {cl}.{cls}RestClient;\nimport {cl}.{cls}RestClientImpl;\n'+'''import java.net.URI;
import org.springframework.stereotype.Service;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.web.client.RestTemplateBuilder;
''')
    gate = model.get('module_gate', {})
    endpoints = []
    for o in ops:
        entry = {'method': o['method'], 'http': o['http'].upper(), 'constant': cls+'Constants.'+o['constant'], 'implemented': False}
        if o['op'] == 'action' and o.get('ai'):
            # The frame is final; the body waits for the Java assistant (HTTP 501 until then).
            entry.update(block=o['action']['block'] or '@FORM', operation='action', owner=o['action']['owner'],
                         implemented=False, ready_after_module_review=False, runs='ai',
                         blockers=['AI-ra vár: a Java asszisztens írja át a kikommentezett Forms-kódot.'], ai=True)
            if o.get('query_action'):
                entry.update(query_block=o['query_action']['target'], query_unit=o['query_action']['unit'])
        elif o['op'] == 'action':
            java_query = bool((o.get('query_action') or {}).get('java'))
            # a Java query button runs even with other Forms code untranslated: that is its TODO, not a blocker
            ready = bool(o.get('passthrough')) or java_query or bool(o.get('query_action') and not o.get('query_blockers'))
            entry.update(block=o['action']['block'] or '@FORM', operation='action', owner=o['action']['owner'],
                         implemented=ready and (not gated or live),
                         ready_after_module_review=ready and gated and not live,
                         runs='java-query' if java_query else 'plsql-query' if o.get('query_action') else 'plsql' if o.get('passthrough') else 'manual',
                         blockers=[] if java_query else o.get('query_blockers', []) if o.get('query_action') else [] if o.get('passthrough') else
                                  [o.get('passthrough_reason') or 'Kézi implementáció szükséges.'])
            if java_query and o.get('query_blockers'):
                entry['todo'] = o['query_blockers']
            if o.get('query_action'):
                entry.update(query_block=o['query_action']['target'], query_unit=o['query_action']['unit'])
            plan = (o.get('query_action') or {}).get('prepared') or o.get('passthrough') or {}
            if plan.get('inputs'):
                from . import java_variables
                entry['developer_inputs'] = [{**{k: i[k] for k in ('source', 'variable', 'reason')},
                                              **({'value': given['value']} if given else {})}
                                             for i in plan['inputs'] for given in [java_variables.lookup(i['variable'])]]
            if o.get('adapter_diagnostics'):
                entry['adapter_diagnostics'] = o['adapter_diagnostics']
            if o.get('query_java_reason'):
                entry['query_java_reason'] = o['query_java_reason']
            if java_query:
                entry['equivalence'] = o['query_action']['java'].get('equivalence')
        elif o['op'] == 'lov':
            ready = not o['lov']['blockers']
            entry.update(block=o['lov']['block'], operation='lov', lov=o['lov']['name'], implemented=ready and (not gated or live),
                         ready_after_module_review=ready and gated and not live, blockers=o['lov']['blockers'])
        elif o['op'] == 'commit':
            # Ready when every block operation it calls is: each one still checks its own state (HTTP 501).
            entry.update(block='@FORM', operation='commit', implemented=not gated or live,
                         ready_after_module_review=gated and not live, blockers=[],
                         blocks=[b['name'] for b in o['blocks']])
        elif o['op'] != 'action':
            enabled, ready, blockers = capability(o['block'], o['op'])
            entry.update(block=o['block']['name'], operation=o['op'], implemented=enabled,
                         ready_after_module_review=bool(ready and not enabled), blockers=blockers)
        endpoints.append(entry)
    cl_contract = None
    if config.get('AWU_AZON'):
        from .company_cl import generate as generate_company_cl
        cl_contract = generate_company_cl(output, config, package, cls, blocks, ops)
        from .company_backend import generate as generate_company_backend
        cl_contract = generate_company_backend(output, config, package, cls, blocks, ops, cl_contract)
        by_method = {op['method']: op for op in ops}
        transports = {e['method']: e['transport_ready'] for e in cl_contract['endpoints']}
        for endpoint in endpoints:
            endpoint['constant'] = cls + 'Constants.' + by_method[endpoint['method']]['constant']
            endpoint['transport_ready'] = transports[endpoint['method']]
        for selection in selections:
            selection['dto'] = cl_contract['dto_types'].get(selection['dto'], selection['dto'])
    api = frontend_api(ops, '' if cl_contract else base, cls)
    if cl_contract:
        api.update(requires_host_adapter=True, response_envelope='RestResponseDto',
                   base_path_expression=cl_contract['wbs_base_path_expression'])
    write_json(output/'analysis/backend-plan.json', {
        'version': 2, 'layout': 'module-service-v2', 'module_class': cls,
        **({'cl_contract': cl_contract, 'backend_contract_ready': True, 'integration_ready': False} if cl_contract else {}),
        'files': {layer: sorted(p.name for p in folder.glob('*.java')) for layer, folder in folders.items()},
        'dto_selection': selections,
        'module_gate': {'switch': cls+'ServiceImpl.MODULE_REVIEWED', **gate} if gate.get('required') else {'required': False},
        'endpoints': endpoints,
        'skipped_blocks': skipped_blocks(model),
        'skipped_actions': model.get('skipped_actions', []),
        'lovs': [{k: l[k] for k in ('name', 'record_group', 'users', 'display_column', 'binds', 'blockers')} for l in lovs],
        'api': api,
        'skipped_operations': [{'block': b['name'], **o} for b in model['blocks'] for o in b.get('skipped_operations', [])],
        'regeneration': 'ServiceImpl and ControllerImpl are CREATE_ONCE. Fresh candidates and changes: analysis/backend-regeneration/.',
    })

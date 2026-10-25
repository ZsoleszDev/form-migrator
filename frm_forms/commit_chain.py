"""Forms COMMIT_FORM as one endpoint: the changed records of every block, in Forms order, in one transaction.

Forms posts the blocks in their sequence; inside a block the deleted records first, then the
inserted and updated ones in record order; PRE-COMMIT before, POST-FORMS-COMMIT after all of
them (Oracle Forms "Post and Commit Transactions"). The endpoint does the same with the
generated block operations, so each record runs its own triggers (validation, PRE-/ON-/POST-
INSERT/UPDATE/DELETE, ON-CHECK-DELETE-MASTER) exactly as through the single-record endpoints.

The key of a new detail record comes from the master like in Forms: from the master record
saved in this commit (a sequence-generated key included), else from the master's current
value on the screen (relation join, Copy Value from Item).
"""
from __future__ import annotations

from .common import jstr

OPERATIONS = {'deleted': ('delete', 'törlés'), 'inserted': ('create', 'új rekord'), 'updated': ('update', 'módosítás')}
READERS = {'text': 'text', 'number': 'number', 'datetime': 'datetime'}


def commit_blocks(blocks: list) -> list:
    """The backend blocks with a write operation, in Forms block order."""
    return [b for b in blocks if any(b['endpoint_plan'].get(op) for op in ('create', 'update', 'delete'))]


def field(b: dict) -> str:
    """The block part of the DTO field names: changes<Block>, rows<Block> (PMD: two lowercase leading letters)."""
    return b['class']


def operation(blocks: list) -> dict | None:
    if not commit_blocks(blocks):
        return None
    return dict(op='commit', verb='commit', http='Post', returns='CommitResult', args='CommitRequest request',
                controller_args='@RequestBody CommitRequest request', call='request', method='commitForm',
                constant='COMMIT_FORM_PATH', blocks=commit_blocks(blocks))


PRELUDE_FIELDS = [('String', 'action'), ('Map<String, Map<String, String>>', 'actionBlocks'), ('Map<String, String>', 'actionParameters')]


def request_fields(o: dict) -> list:
    """CommitRequest: the screen values, the changes per block, and - with a commit point - the button to run first."""
    fields = [('Map<String, Map<String, String>>', 'blocks'), ('Map<String, String>', 'parameters')]
    fields += [(f"BlockChanges<{b['class']}Row>", 'changes' + field(b)) for b in o['blocks']]
    return fields + (PRELUDE_FIELDS if o.get('preludes') else [])


def dtos(o: dict, value_class) -> str:
    blocks = o['blocks']
    request = request_fields(o)
    result = [('Map<String, Map<String, String>>', 'blocks'), ('List<String>', 'messages'),
              ('List<List<String>>', 'commands'), ('Map<String, String>', 'globals')]
    result += [(f"List<{b['class']}Row>", 'rows' + field(b)) for b in blocks]
    return ('    // Forms COMMIT_FORM: blokkonként az új, módosított és törölt rekordok; blocks/parameters = a képernyő értékei.\n'
            + value_class('BlockChanges<T>', [('List<T>', 'inserted'), ('List<UpdateRequest<T>>', 'updated'), ('List<T>', 'deleted')])
            + value_class('CommitRequest', request)
            + '    // A mentett rekordok blokkonként (újraolvasva), a triggerek üzenetei, utasításai és :GLOBAL értékei.\n'
            + value_class('CommitResult', result))


def key_sources(model: dict, block: dict) -> list:
    """(detail item, master block, master item) pairs that give a new detail record its key."""
    blocks = {b['name']: b for b in model['blocks']}
    result = []
    for relation in model.get('relation_contexts', []):
        if relation.get('status') != 'compiled' or relation['detail'] != block['name']:
            continue
        for pair in relation.get('mapping', []):
            master, master_item = pair['master_item'].split('.', 1)
            detail = next((i for i in block['db_items'] if i['column'] == pair['detail_column']), None)
            source = next((i for i in blocks[master]['items'] if i['name'] == master_item), None)
            if detail and source:
                result.append((detail, blocks[master], source))
    for item in block['items']:
        copy = str(item['properties'].get('copyvaluefromitem', '')).strip().upper()
        if '.' not in copy or any(d is item for d, _, _ in result):
            continue
        master, master_item = copy.split('.', 1)
        source = next((i for i in blocks.get(master, {}).get('items', []) if i['name'] == master_item), None)
        if source and source['type'] == item['type'] and item['kind'] != 'button':
            result.append((item, blocks[master], source))
    return result


def trigger_lines(event: str, prepared: dict, indent: str) -> list[str]:
    """PRE-COMMIT / POST-FORMS-COMMIT: the anonymous block with the screen values, outputs merged into the result."""
    from .plsql_passthrough import input_declarations, input_variable, sql_expression
    from .rules import JDBC_TYPES
    params = []
    for b in prepared['binds']:
        if b.get('input'):
            params.append(f"DbCalls.in({input_variable(prepared, b)}, {JDBC_TYPES[b['type']]})")
        elif b['parameter']:
            params.append(f"DbCalls.in(PlsqlValues.parameter(parameters, {jstr(b['source'])}), java.sql.Types.VARCHAR)")
        else:
            params.append(f"DbCalls.in(PlsqlValues.{READERS[b['type']]}(values, {jstr(b['block'])}, {jstr(b['item'])}), {JDBC_TYPES[b['type']]})")
    params += [f"DbCalls.out({JDBC_TYPES[b['type']]})" for b in prepared['outs']]
    params += ['DbCalls.out(java.sql.Types.VARCHAR)' for _ in prepared['globals']] + ['DbCalls.out(java.sql.Types.VARCHAR)'] * 2
    offset = len(prepared['binds'])
    lines = [f'// {event}: az eredeti PL/SQL az adatbázisban, a képernyő értékeivel.', '{']
    lines += input_declarations(prepared, '    ').splitlines()
    lines += ['    Object[] out = DbCalls.call(jdbc, ' + sql_expression(prepared) + ',',
             '        ' + ',\n        '.join(params) + ');']
    lines += [f"    PlsqlValues.put(blocks, {jstr(b['block'])}, {jstr(b['item'])}, out[{offset + k}]);" for k, b in enumerate(prepared['outs'])]
    first = offset + len(prepared['outs'])
    lines += [f"    PlsqlValues.global(globals, {jstr(b['source'])}, out[{first + k}]);" for k, b in enumerate(prepared['globals'])]
    messages = first + len(prepared['globals'])
    lines += [f'    messages.addAll(PlsqlValues.lines(out[{messages}]));', f'    commands.addAll(PlsqlValues.commands(out[{messages + 1}]));', '}']
    return [indent + line if line else line for line in '\n'.join(lines).split('\n')]


AI_HOOKS = {'PRE-COMMIT': 'triggerPreCommitForm', 'POST-FORMS-COMMIT': 'triggerPostFormsCommitForm'}


def ai_hook(event: str, plan: dict, model: dict) -> tuple[str, str]:
    """backend_trigger_mode=ai: the form trigger as a hook of commitForm, its body for the Java assistant."""
    from . import ai_triggers
    trigger = next(t for t in model['triggers'] if t['id'] == plan['trigger'])
    source = (trigger.get('replacement') or {}).get('source', trigger['source'])
    hook = AI_HOOKS[event]
    notes = ['AI-ra vár. Bemenet: values (a képernyő BLOKK -> MEZŐ -> érték), parameters (:GLOBAL/:PARAMETER/:SYSTEM);',
             'kimenet: blocks (visszaírt mezők), globals, messages (MESSAGE), commands (Forms-utasítások a képernyőnek).']
    notes += ai_triggers.framework_note(source, None)
    body = (ai_triggers.region(trigger['id'] + ' (' + event + ', a COMMIT_FORM ' + ('előtt' if event == 'PRE-COMMIT' else 'után') + ')',
                               [(trigger['id'], source)] + ai_triggers.unit_sections(source, model), '        ', notes)
            + ai_triggers.placeholder(trigger['id'], '        '))
    method_text = (f'''    // Forms {event} ({trigger['id']}): a commitForm hívja, ugyanabban a tranzakcióban.
    private void {hook}(java.util.Map<String, java.util.Map<String, String>> values, java.util.Map<String, String> parameters,
            java.util.Map<String, java.util.Map<String, String>> blocks, java.util.Map<String, String> globals,
            List<String> messages, List<List<String>> commands) {{
{body}
    }}''')
    return f'{hook}(values, parameters, blocks, globals, messages, commands);', method_text


def method(o: dict, model: dict, log1x, user_type: str, gated: bool) -> str:
    plans = model.get('commit_plan', {})
    hooks = {event: ai_hook(event, plan, model) for event, plan in plans.items() if plan.get('ai') and event in AI_HOOKS}
    body = ['if (request == null) {', '    throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Hiányzó kérés.");', '}']
    if gated:
        body += ['if (!MODULE_REVIEWED) {',
                 '    throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, "Ez a művelet ebben a modulban még nem érhető el.");', '}']
    copies = any(READERS.get(source['type']) for b in o['blocks'] for _, _, source in key_sources(model, b))
    if plans or copies:  # the screen values: the form triggers and the master key of a new detail record read them
        body.append('var values = request.blocks() == null ? java.util.Map.<String, java.util.Map<String, String>>of() : request.blocks();')
    if plans:
        body.append('var parameters = request.parameters() == null ? java.util.Map.<String, String>of() : request.parameters();')
    body += ['var blocks = new java.util.LinkedHashMap<String, java.util.Map<String, String>>();',
             'var globals = new java.util.LinkedHashMap<String, String>();',
             'var messages = new ArrayList<String>();',
             'var commands = new ArrayList<List<String>>();']
    if o.get('preludes'):
        # COMMIT_FORM in the middle of a button's code: its code up to the commit point runs first, in this
        # transaction, and must arrive at the state the screen saw (PlsqlValues.prelude, HTTP 409 otherwise).
        body += ['if (request.action() != null) {',
                 '    var actionValues = request.actionBlocks() == null ? java.util.Map.<String, java.util.Map<String, String>>of() : request.actionBlocks();',
                 '    var actionParameters = new java.util.LinkedHashMap<String, String>(request.actionParameters() == null ? java.util.Map.<String, String>of() : request.actionParameters());',
                 '    actionParameters.put("FRM.COMMIT", "POST");',
                 '    var actionCommands = new ArrayList<List<String>>();',
                 '    switch (request.action()) {']
        for owner, runner in o['preludes']:
            body += [f'        case {jstr(owner)}:',
                     f'            {runner}(actionValues, actionParameters, blocks, globals, messages, actionCommands);',
                     '            break;']
        body += ['        default:',
                 '            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Ismeretlen mentés előtti művelet: " + request.action());',
                 '    }',
                 '    commands.addAll(PlsqlValues.prelude(actionCommands, actionParameters));',
                 '}']
    if 'PRE-COMMIT' in hooks:
        body += ['// PRE-COMMIT: a form triggere (Java asszisztens).', hooks['PRE-COMMIT'][0]]
    elif 'PRE-COMMIT' in plans:
        body += trigger_lines('PRE-COMMIT', plans['PRE-COMMIT']['plan'], '')
    saved = {}
    for b in o['blocks']:
        rows, changes = 'rows' + field(b), 'request.changes' + field(b) + '()'
        saved[b['name']] = rows
        body += [f"// {b['name']}: előbb a törölt, aztán az új és a módosított rekordok (Forms-sorrend).",
                 f"var {rows} = new ArrayList<{b['class']}Row>();", f'if ({changes} != null) {{']
        for kind, (op, label) in OPERATIONS.items():
            source = f'commitRows({changes}.{kind}())'
            if not b['endpoint_plan'].get(op):
                body += [f'    if (!{source}.isEmpty()) {{',
                         f'        throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, {jstr(b["name"] + ": a Forms-blokk nem enged: " + label + ".")});',
                         '    }']
                continue
            if kind == 'deleted':
                body += [f'    for (var row : {source}) {{', f"        messages.addAll(delete{b['class']}(user, row));", '    }']
            elif kind == 'inserted':
                body += [f'    for (var row : {source}) {{']
                for detail, master, source_item in key_sources(model, b):
                    target = detail['field']
                    if master['name'] in saved:
                        master_rows = saved[master['name']]
                        body += [f'        if (row.{target} == null && !{master_rows}.isEmpty()) {{',
                                 f'            var saved = {master_rows}.get({master_rows}.size() - 1);',
                                 f'            row.{target} = saved.{source_item["field"]};', '        }']
                    reader = READERS.get(source_item['type'])
                    if reader:
                        body += [f'        if (row.{target} == null) {{',
                                 f'            row.{target} = PlsqlValues.{reader}(values, {jstr(master["name"])}, {jstr(source_item["name"])});',
                                 '        }']
                body += [f"        var committed = create{b['class']}(user, row);", f'        {rows}.add(committed.row());',
                         '        messages.addAll(committed.messages());', '    }']
            else:
                body += [f'    for (var update : {source}) {{',
                         f"        var committed = update{b['class']}(user, update.original(), update.value());",
                         f'        {rows}.add(committed.row());', '        messages.addAll(committed.messages());', '    }']
        body.append('}')
    if 'POST-FORMS-COMMIT' in hooks:
        body += ['// POST-FORMS-COMMIT: a form triggere (Java asszisztens).', hooks['POST-FORMS-COMMIT'][0]]
    elif 'POST-FORMS-COMMIT' in plans:
        body += trigger_lines('POST-FORMS-COMMIT', plans['POST-FORMS-COMMIT']['plan'], '')
    body.append('return new CommitResult(blocks, messages, commands, globals' + ''.join(', ' + saved[b['name']] for b in o['blocks']) + ');')
    info = ['Forms COMMIT_FORM: a képernyő összes változása egy tranzakcióban, Forms-sorrendben',
            '(blokksorrend; blokkonként törlés, majd beszúrás és módosítás; minden rekord a saját triggereivel).']
    if hooks:
        info.append('Form-triggerek: ' + ', '.join(hooks) + ' (külön metódusban, a Java asszisztens írja át).')
    if set(plans) - set(hooks):
        info.append('Form-triggerek: ' + ', '.join(e for e in plans if e not in hooks) + ' (eredeti PL/SQL az adatbázisban).')
    if o.get('preludes'):
        info.append('Mentési pontos gombok (előbb a kódjuk fut a pontig): ' + ', '.join(owner for owner, _ in o['preludes']) + '.')
    from .action_scaffold import comment_lines
    text = '\n'.join('            ' + line if line else '' for line in body)
    return (comment_lines('\n'.join(info), '    ') + f'''
    @org.springframework.transaction.annotation.Transactional(rollbackFor = Exception.class)
    @Override
    public CommitResult {o['method']}({user_type} user, CommitRequest request) throws Exception {{
        {log1x(o, '() -> {')}
{text}
        }});
    }}

    private static <T> List<T> commitRows(List<T> rows) {{
        return rows == null ? List.of() : rows;
    }}''' + ''.join('\n\n' + text for _, text in hooks.values()))


def evidence(o: dict, model: dict) -> str:
    lines = ['Forms COMMIT_FORM sorrend: ' + ' → '.join(b['name'] for b in o['blocks']),
             'Blokkonként: DELETE (PRE-DELETE, ON-CHECK-DELETE-MASTER, ON-DELETE/DELETE, POST-DELETE), '
             'majd INSERT/UPDATE rekordonként a saját triggereivel.']
    for b in o['blocks']:
        for detail, master, source in key_sources(model, b):
            lines.append(f"Új {b['name']} rekord kulcsa: {detail['name']} ← {master['name']}.{source['name']} "
                         '(a most mentett master, különben a képernyő aktuális értéke).')
    for event, plan in model.get('commit_plan', {}).items():
        if plan.get('ai'):
            lines.append(event + ': ' + plan['trigger'] + '; a Java asszisztens írja át (külön metódus a commitForm-ban).')
            continue
        lines.append(event + ': ' + plan['trigger'] + '; ' + '; '.join(plan['passthrough']['notes']))
    return '\n'.join(lines)

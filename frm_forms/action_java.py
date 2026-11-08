"""Simple buttons as Java without the local AI (backend_trigger_mode=ai, data access jpa).

The common button is short: read a few screen values, check them (MESSAGE; RAISE FORM_TRIGGER_FAILURE), maybe ask
first (one SHOW_ALERT), run its SQL, write a value back, re-query a block. That needs no model: the SQL is a generated
repository method (jpa_sql, INSERT / UPDATE / DELETE through hook_java.with_dml), the rest is translated with Oracle
semantics like a record trigger (hook_java.HookCompiler, rules.Compiler, SqlValues), and the method answers the
screen's contract: ActionResult(blocks written back, messages, commands, globals).

  - :BLOCK.ITEM: the value the screen sent (request.blocks()), in the `screen` holder; an assignment changes it there
    and the written items go back in the result's blocks;
  - MESSAGE: context.message; RAISE FORM_TRIGGER_FAILURE: context.abort() (HTTP 422 with the last message);
  - SHOW_ALERT: the screen asks (command SHOW_ALERT, then the same request again with FRM.ALERTS = the pressed
    button); only one alert, and only where nothing happened before it but checks that stop the button;
  - GO_BLOCK / GO_ITEM / EXECUTE_QUERY: commands for the screen; COMMIT_FORM only as the last statement (the screen
    saves); SYNCHRONIZE, CLEAR_MESSAGE, BELL: nothing to do in a web page.

A button outside this subset - a program unit of the form, a database routine, :GLOBAL / :SYSTEM / :PARAMETER, a loop,
a second alert, an unknown call - stays the Java assistant's task, unchanged.
"""
from __future__ import annotations

import re

from .common import decode_line_escapes, jstr
from .hook_java import HookCompiler, with_dml
from .plsql import Unsupported, parse

READERS = {'text': 'text', 'number': 'number', 'datetime': 'datetime'}
JAVA = {'text': 'String', 'number': 'BigDecimal', 'datetime': 'LocalDateTime'}
QUIET = {'SYNCHRONIZE', 'CLEAR_MESSAGE', 'BELL'}
ALERT_BUTTONS = {'ALERT_BUTTON1': '1', 'ALERT_BUTTON2': '2', 'ALERT_BUTTON3': '3'}


def response(packet: dict, generated: dict, catalog: dict | None = None) -> tuple[dict | None, str]:
    """The proposal of a button translated without the model, or (None, why not)."""
    if packet.get('kind') != 'implement' or (packet.get('endpoint') or {}).get('operation') != 'action':
        return None, 'nem gombvégpont'
    if 'ActionResult' not in packet.get('signature', '') or 'QueryActionRequest' in packet.get('signature', ''):
        return None, 'nem ActionResult-végpont'
    sources = [s for s in packet.get('sources') or [] if isinstance(s, dict)]
    if len(sources) != 1 or sources[0].get('kind') != 'trigger':
        return None, 'a gomb a form saját programegységét vagy más triggert futtat'
    items = packet.get('screen_items') or {}
    if not items:
        return None, 'nincs a képernyő mezőiről adat'
    try:
        body = translate(decode_line_escapes(sources[0].get('source') or ''), items, generated, catalog or {})
    except Unsupported as exc:
        return None, str(exc)
    return {'status': 'proposal', 'body': body, 'imports': [], 'private_methods': '', 'repositories': [], 'dtos': [],
            'summary': 'A(z) ' + packet['method'] + ' Java-változata a migrátortól (determinisztikus, AI-hívás nélkül).',
            'assumptions': [], 'missing_context': [], 'test_cases': []}, ''


def translate(text: str, items: dict, generated: dict, catalog: dict) -> str:
    from .assistant_reply import decode_literal_escapes  # "Túl" -> "Túl": the source is UTF-8
    from .rules import strip_framework
    text, dml = with_dml(text)
    nodes = parse(text, extended=True)
    nodes, _ = strip_framework(nodes, catalog)  # qms$event_item ...: plumbing, not the button's logic
    compiler = ActionCompiler(items, generated)
    compiler.dml = dml
    statements = compiler.top(nodes)
    head = ['var values = request.blocks() == null ? Map.<String, Map<String, String>>of() : request.blocks();']
    if compiler.read:
        head.append('var screen = new Object() {')
        head += ['    ' + JAVA[f['type']] + ' ' + f['field'] + ' = PlsqlValues.' + READERS[f['type']] + '(values, '
                 + jstr(f['block']) + ', ' + jstr(f['item']) + ');' for f in compiler.read.values()]
        head.append('};')
    if compiler.locals:
        head.append(compiler.holder(''))
    head += ['var context = new RuleContext();', 'List<List<String>> commands = new ArrayList<>();']
    tail = ['Map<String, Map<String, String>> blocks = new LinkedHashMap<>();']
    tail += ['PlsqlValues.put(blocks, ' + jstr(f['block']) + ', ' + jstr(f['item']) + ', screen.' + f['field'] + ');'
             for f in compiler.written.values()]
    tail.append('return new ActionResult(blocks, context.messages(), commands, Map.of());')
    body = '\n'.join(line for line in head + statements + tail if line.strip())
    return decode_literal_escapes(body).replace('java.math.BigDecimal', 'BigDecimal')


def pure(node: dict) -> bool:
    """Nothing persists when it runs: a check that stops the button may precede the alert (it runs again then)."""
    op = node['op']
    if op in {'noop', 'pragma', 'declare', 'local_assign'}:
        return True
    if op == 'call' and node['name'] in {'SET_ALERT_PROPERTY'} | QUIET:
        return True
    if op == 'if':
        return all(stops(branch['body']) or all(map(pure, branch['body'])) for branch in node['branches']) and (
            stops(node['else']) or all(map(pure, node['else'])))
    if op == 'block' and not node.get('handlers'):
        return all(map(pure, node['body']))
    return False


def stops(nodes: list) -> bool:
    """The branch ends the button (RAISE FORM_TRIGGER_FAILURE) and does nothing else that lasts."""
    real = [n for n in nodes if n['op'] not in {'noop', 'pragma'}]
    return bool(real) and real[-1]['op'] == 'abort' and all(
        n['op'] == 'abort' or (n['op'] == 'call' and n['name'] == 'MESSAGE') or pure(n) for n in real)


def has_alert(node) -> bool:
    if isinstance(node, dict):
        if node.get('op') == 'function' and node.get('name') == 'SHOW_ALERT':
            return True
        return any(has_alert(v) for v in node.values())
    if isinstance(node, list):
        return any(has_alert(v) for v in node)
    return False


class ActionCompiler(HookCompiler):
    """HookCompiler on the screen's values (screen.field), with the commands and the one alert of a button."""

    def __init__(self, items: dict, generated: dict):
        super().__init__({'block': '', 'fields': []}, generated)
        from .query_java import camel
        self.fields = {}
        for name, typ in items.items():
            block, item = name.split('.', 1)
            field = camel(block + '_' + item)
            while field in {f['field'] for f in self.fields.values()}:
                field += '_'
            self.fields[name] = {'name': name, 'block': block, 'item': item, 'field': field, 'type': typ}
        self.read, self.written = {}, {}
        self.alerts = {}       # ALERT variable -> {'name': alert, 'message': Java text or None}
        self.asked = None      # the alert asked (its name), once
        self.statement = None  # the top-level statement being compiled, its index and the count (COMMIT_FORM)

    # the screen's values
    def ref(self, value: str) -> dict:
        field = self.fields.get(value)
        if field is None:
            raise Unsupported('Nem a képernyő mezője (SYSTEM/GLOBAL/PARAMETER vagy ismeretlen): ' + value)
        return field

    def value(self, field: dict) -> str:
        self.read.setdefault(field['name'], field)
        return 'screen.' + field['field']

    def assign(self, field: dict, expr: str, indent: str) -> str:
        self.read.setdefault(field['name'], field)
        self.written.setdefault(field['name'], field)
        return indent + 'screen.' + field['field'] + ' = ' + expr + ';'

    # the top level: the alert is asked before the statement that needs its answer
    def top(self, nodes: list) -> list[str]:
        def flat(body):
            result = []
            for node in body:
                if node['op'] in {'noop', 'pragma'}:
                    continue
                result += flat(node['body']) if node['op'] == 'block' and not node.get('handlers') else [node]
            return result
        nodes = flat(nodes)
        lines = []
        for index, node in enumerate(nodes):
            self.statement = (index, len(nodes))
            if has_alert(node):
                if self.asked is not None:
                    raise Unsupported('Egynél több SHOW_ALERT egy gombban.')
                if not all(map(pure, nodes[:index])):
                    raise Unsupported('A SHOW_ALERT előtt már történik valami (adatbázis, mezőírás, üzenet).')
                lines += self.ask(node)
            lines += self.statements([node], '')
        return lines

    def ask(self, node: dict) -> list[str]:
        """The first request asks the screen; the second (FRM.ALERTS = the pressed button) runs the code."""
        call = self.alert_call(node)
        argument = call['args'][0] if len(call['args']) == 1 else None
        if argument is None:
            raise Unsupported('SHOW_ALERT egy argumentummal szükséges.')
        if argument['op'] == 'literal' and argument['type'] == 'text':
            name, message = argument['value'], None
        elif argument['op'] == 'symbol' and argument['name'] in self.alerts:
            name, message = self.alerts[argument['name']]['name'], self.alerts[argument['name']]['message']
        else:
            raise Unsupported('A SHOW_ALERT argumentuma nem ismert alert.')
        self.asked = name
        command = 'List.of("SHOW_ALERT", ' + jstr(name) + (', ' + message if message else '') + ')'
        return ['String alertAnswer = request.parameters() == null ? null : PlsqlValues.parameter(request.parameters(), "FRM.ALERTS");',
                'if (alertAnswer == null) {',
                '    return new ActionResult(Map.of(), List.of(), List.of(' + command + '), Map.of());',
                '}']

    def alert_call(self, node):
        if isinstance(node, dict):
            if node.get('op') == 'function' and node.get('name') == 'SHOW_ALERT':
                return node
            for value in node.values():
                found = self.alert_call(value)
                if found:
                    return found
        if isinstance(node, list):
            for value in node:
                found = self.alert_call(value)
                if found:
                    return found
        return None

    def declare(self, node: dict, indent: str) -> str:
        if node['type'].upper() == 'ALERT':
            self.alerts[node['name']] = {'name': None, 'message': None}
            return ''
        return super().declare(node, indent)

    def expression(self, node: dict) -> tuple[str, str]:
        op = node['op']
        if op == 'symbol' and node['name'] in ALERT_BUTTONS:
            return 'new BigDecimal(' + jstr(ALERT_BUTTONS[node['name']]) + ')', 'number'
        if op == 'function' and node['name'] == 'SHOW_ALERT':
            if self.asked is None:
                raise Unsupported('SHOW_ALERT itt nem kérdezhető meg.')
            return 'new BigDecimal(alertAnswer)', 'number'  # "1", "2", "3": the pressed button
        if op == 'function' and node['name'] == 'FIND_ALERT':
            raise Unsupported('FIND_ALERT csak ALERT változóba.')
        return super().expression(node)

    def statements(self, nodes: list[dict], indent: str) -> list[str]:
        lines = []
        for node in nodes:
            op = node['op']
            if op == 'local_assign' and node['variable'] in self.alerts:
                value = node['value']
                if value['op'] != 'function' or value['name'] != 'FIND_ALERT' or len(value['args']) != 1 \
                        or value['args'][0]['op'] != 'literal' or value['args'][0]['type'] != 'text':
                    raise Unsupported('ALERT változó csak FIND_ALERT(\'NÉV\') lehet.')
                self.alerts[node['variable']]['name'] = value['args'][0]['value']
                continue
            lines += super().statements([node], indent)
        return lines

    def other(self, node: dict, indent: str) -> list[str]:
        name, args = node.get('name', node['op']), node.get('args') or []
        if node['op'] != 'call':
            return super().other(node, indent)
        if name in QUIET:
            return []
        if name == 'MESSAGE' and len(args) == 2 and args[1]['op'] == 'symbol' and args[1]['name'] in {'ACKNOWLEDGE', 'NO_ACKNOWLEDGE'}:
            return self.statements([{'op': 'call', 'name': 'MESSAGE', 'args': args[:1]}], indent)
        if name == 'SET_ALERT_PROPERTY' and len(args) == 3 and args[0]['op'] == 'symbol' and args[0]['name'] in self.alerts \
                and args[1]['op'] == 'symbol' and args[1]['name'] == 'ALERT_MESSAGE_TEXT':
            if self.asked is not None:
                raise Unsupported('SET_ALERT_PROPERTY a kérdés után.')
            expr, typ = self.expression(args[2])
            if typ not in {'text', 'null'}:
                raise Unsupported('Az alert szövege nem szöveg.')
            self.alerts[args[0]['name']]['message'] = expr
            return []
        if name in {'GO_BLOCK', 'GO_ITEM'} and len(args) == 1 and args[0]['op'] == 'literal' and args[0]['type'] == 'text':
            return [indent + 'commands.add(List.of(' + jstr(name) + ', ' + jstr(args[0]['value'].upper()) + '));']
        if name == 'EXECUTE_QUERY' and not args:
            return [indent + 'commands.add(List.of("EXECUTE_QUERY"));']
        if name == 'COMMIT_FORM' and not args:
            index, count = self.statement or (0, 0)
            if indent or index != count - 1:
                raise Unsupported('COMMIT_FORM csak a gomb utolsó utasításaként (a képernyő ment).')
            return [indent + 'commands.add(List.of("COMMIT_FORM"));']
        raise Unsupported('Nem támogatott hívás az AI nélküli fordításban: ' + name)


def screen_items(model: dict) -> dict:
    """BLOCK.ITEM -> text / number / datetime of the form's items (buttons left out): the packet's screen values."""
    result = {}
    for block in model.get('blocks', []):
        for item in block.get('items', []):
            if item.get('kind') != 'button' and item.get('type') in READERS and re.fullmatch(r'[\w$#]+', item.get('name', '')):
                result[block['name'] + '.' + item['name']] = item['type']
    return result

"""Query buttons as plain Java (4.23, 4.24): only the SQL request of the DEFAULT_WHERE builder, in readable code.

A query button builds a WHERE text and runs SET_BLOCK_PROPERTY(..., DEFAULT_WHERE, ...) / GO_BLOCK / EXECUTE_QUERY,
in a local procedure (LEKERDEZESI_FELTETELEK, with or without parameters) or in the trigger itself (DECLARE ...).
It becomes two Java methods:

  - <method>Query(values...): the WHERE, the ORDER BY, the binds and the messages, built like the Forms code built
    them: the IFs are Java ifs, the constant SQL pieces are appended (the ';' -> CHR(39) replacement is done at
    generation time), and every value of the screen that the Forms code pasted into the SQL text - :BLOCK.ITEM in a
    piece, ' = ''' || :XY_LAP.KOD || '''', '%' || NAME_IN('XY_LAP.NEV') || '%', a local variable - is a JDBC bind;
  - the endpoint: reads the screen's values from the request, calls it, runs one JDBC query on the target block
    (with its POST-QUERY rules, when they are translated).

The compiled form is an intermediate representation (statements, conditions, SQL templates) that the generator also
evaluates in Python: the original PL/SQL is interpreted with Oracle semantics on a set of input cases and the WHERE
texts are compared (equivalence). A difference falls back to the PL/SQL query adapter; with query_java_tests the same
cases become a JUnit test of the generated <method>Query.

The Forms plumbing is not emulated: SET_BLOCK_PROPERTY, GO_BLOCK, EXECUTE_QUERY, CLEAR_FORM and the navigation
built-ins are left out, a message procedure (WUZENET, QMS$FORMS_ERRORS.PUSH, an alert dialog) is a message. The
original Forms code stays in the method as a comment, in a foldable //region. A value with no source on the screen is
a developer input (null, TODO). Whatever does not fit falls back to the PL/SQL query adapter (query_actions); the
reason names the source line.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import itertools
import re

from .common import decode_line_escapes, java_text_block, jstr, name
from .forms_context import NORMAL_MODE, normal_mode_expression
from .plsql import Parser, Unsupported, parse

# Forms built-ins of the screen's navigation and the query plumbing: the migrated query does not need them.
IGNORED = {'GO_BLOCK', 'GO_ITEM', 'GO_RECORD', 'FIRST_RECORD', 'LAST_RECORD', 'NEXT_RECORD', 'PREVIOUS_RECORD',
           'SYNCHRONIZE', 'CLEAR_MESSAGE', 'BELL', 'SET_ITEM_PROPERTY', 'SET_BLOCK_PROPERTY', 'SHOW_VIEW', 'HIDE_VIEW',
           'CLEAR_BLOCK', 'CLEAR_FORM', 'EXECUTE_QUERY', 'DO_KEY', 'SET_WINDOW_PROPERTY', 'SHOW_WINDOW', 'HIDE_WINDOW',
           'REDISPLAY', 'SET_APPLICATION_PROPERTY', 'SET_RECORD_PROPERTY'}
RESERVED = {'values', 'parameters', 'messages', 'where', 'params', 'rows', 'row', 'context', 'request', 'user', 'sql',
            'jdbc', 'log', 'rs', 'rowNum', 'e', 'q', 'query'}
READERS = {'text': 'text', 'number': 'number', 'datetime': 'datetime'}
JAVA_TYPES = {'text': 'String', 'number': 'java.math.BigDecimal', 'datetime': 'java.time.LocalDateTime'}
NUMBERS = {'0': 'java.math.BigDecimal.ZERO', '1': 'java.math.BigDecimal.ONE', '10': 'java.math.BigDecimal.TEN'}
MIRROR = {'=': '=', '<>': '<>', '!=': '<>', '<': '>', '>': '<', '<=': '>=', '>=': '<='}
BIND = re.compile(r':([A-Za-z][\w$#]*(?:\.[A-Za-z][\w$#]*)?)')
TEXT_TYPES = {'VARCHAR2', 'VARCHAR', 'CHAR', 'NVARCHAR2', 'NCHAR', 'LONG', 'STRING', 'CLOB'}
NUMBER_TYPES = {'NUMBER', 'INTEGER', 'INT', 'PLS_INTEGER', 'BINARY_INTEGER', 'NATURAL', 'POSITIVE', 'SMALLINT', 'DECIMAL',
                'FLOAT', 'NUMERIC', 'SIGNTYPE', 'SIMPLE_INTEGER'}
DATE_TYPES = {'DATE', 'TIMESTAMP'}
# SQL functions a value may pass through on its way into the SQL text: kept in the SQL, around the bind.
SQL_FUNCTIONS = {'NVL': None, 'UPPER': 'text', 'LOWER': 'text', 'TRIM': 'text', 'LTRIM': 'text', 'RTRIM': 'text',
                 'SUBSTR': 'text', 'TO_CHAR': 'text', 'TO_NUMBER': 'number', 'TO_DATE': 'datetime', 'TRUNC': None,
                 'LPAD': 'text', 'RPAD': 'text', 'ROUND': 'number'}
MAX_CASES = 96


def camel(text: str) -> str:
    words = [w for w in re.split(r'[^A-Za-z0-9]+', text) if w]
    result = (words[0].lower() + ''.join(w[:1].upper() + w[1:].lower() for w in words[1:])) if words else 'value'
    return result if re.match(r'[a-z]', result) else 'v' + result


def declared_type(type_name: str) -> str | None:
    """text / number / datetime of a PL/SQL declaration; None: %TYPE (from the first value)."""
    base = type_name.upper()
    if base.endswith('%TYPE'):
        return None
    if base.endswith('%ROWTYPE'):
        return 'rowtype'
    base = base.split('.')[-1]
    if base in TEXT_TYPES:
        return 'text'
    if base in NUMBER_TYPES:
        return 'number'
    if base in DATE_TYPES:
        return 'datetime'
    return 'boolean' if base == 'BOOLEAN' else 'other'


class Var:
    """A local PL/SQL variable (or parameter) of the button: an SQL text the WHERE is built in, or a value."""

    def __init__(self, name_: str, java: str, typ: str | None, role: str):
        self.name, self.java, self.type, self.role = name_, java, typ, role
        self.constant = None  # a literal argument that never changes (LEK('BLK'): the block name)
        self.origin = None  # the item a value variable copies, while it is not reassigned (CLEAR_FORM restore)
        self.separator = None  # the quote stand-in of an SQL text (select replace(x, ';', chr(39)) into x)
        self.assigned = 0


def concat_symbols(node: dict | None) -> set:
    """The local names that are operands of an SQL text: the || chain, the result branches of DECODE / CASE."""
    if node is None:
        return set()
    op = node['op']
    if op == 'symbol':
        return {node['name']}
    if op == 'binary' and node['operator'] == '||':
        return concat_symbols(node['left']) | concat_symbols(node['right'])
    if op == 'function' and node['name'] == 'DECODE':
        args = node['args']
        results = args[2::2] + ([args[-1]] if len(args) % 2 == 0 and len(args) > 2 else [])
        return set().union(*(concat_symbols(r) for r in results)) if results else set()
    if op == 'case':
        return set().union(*(concat_symbols(w['then']) for w in node['whens'])) | concat_symbols(node.get('else'))
    return set()


def textual(node: dict | None, sql: set) -> bool:
    """An SQL-text assignment: literals, CHR, the SQL text variables, DECODE / CASE of those."""
    if node is None:
        return True
    op = node['op']
    if op == 'literal':
        return node['type'] in {'text', 'null'}
    if op == 'symbol':
        return node['name'] in sql
    if op == 'binary' and node['operator'] == '||':
        return textual(node['left'], sql) and textual(node['right'], sql)
    if op == 'function' and node['name'] == 'CHR':
        return True
    if op == 'function' and node['name'] == 'DECODE':
        args = node['args']
        return all(textual(r, sql) for r in args[2::2] + ([args[-1]] if len(args) % 2 == 0 and len(args) > 2 else []))
    if op == 'case':
        return all(textual(w['then'], sql) for w in node['whens']) and textual(node.get('else'), sql)
    return False


def quote_replace(node: dict) -> tuple | None:
    """(variable, separator) of SELECT REPLACE(x, ';', CHR(39)) INTO x FROM DUAL."""
    match = re.fullmatch(r"replace\s*\(\s*([\w$#]+)\s*,\s*'((?:[^']|'')+)'\s*,\s*chr\s*\(\s*39\s*\)\s*\)", node['columns'], re.I)
    if (match and len(node['into']) == 1 and node['into'][0].upper() == match.group(1).upper()
            and re.fullmatch(r'from\s+dual', node['rest'], re.I)):
        return match.group(1).upper(), match.group(2).replace("''", "'")
    return None


class Builder:
    """The trigger (and the local procedures it calls) as an IR the Java code and the equivalence check come from."""

    def __init__(self, model: dict, trigger: dict, units: dict, messages_catalog: dict | None = None):
        self.model, self.units = model, units
        self.messages_catalog = messages_catalog
        self.block = (trigger.get('block') or '').upper()
        self.current = self.block  # the cursor block (GO_BLOCK): what CLEAR_BLOCK clears
        self.items = {b['name']: {i['name']: i for i in b['items'] if i['kind'] != 'button'} for b in model['blocks']}
        self.reads = {}  # source -> {variable, type, java, source}
        self.inputs = []  # developer inputs: values with no source on the screen
        self.binds = {}  # bind name (Java variable) -> the read source or the local variable
        self.taken = set(RESERVED)
        self.vars = {}  # NAME -> Var
        self.expanded = []  # the local procedures inlined
        self.target = None
        self.order_static = None
        self.executed = False
        self.called = []  # the local procedures shown as a message
        self.left_out = []  # Forms built-ins the migrated query does not need
        self.cleared = set()  # blocks cleared by CLEAR_FORM / CLEAR_BLOCK: their unwritten items read NULL
        self.overrides = {}  # item source -> the Var written into it (CLEAR_FORM; :X := saved_x)
        self.writes = []  # (item source, Var) for the final screen check
        self.nesting = 0  # inside an IF: CLEAR_FORM and item writes would depend on the branch
        self.ast, self.ir = [], []

    # ------------------------------------------------------------------ the AST: local procedures inlined

    def procedure(self, unit: str) -> tuple[list, list, list]:
        """(parameters as declarations, body, handlers) of a local procedure, in the extended grammar."""
        parser = Parser(decode_line_escapes(self.units[unit]['text']), extended=True)
        parser.need('PROCEDURE')
        parser.need(unit)
        params = []
        if parser.accept('('):
            while True:
                token = parser.take()
                if token.kind != 'id':
                    raise parser.refuse('Nem értelmezhető paraméter: ' + token.value, token)
                if parser.accept('OUT') or (parser.accept('IN') and parser.accept('OUT')):
                    raise parser.refuse('OUT paraméteres eljárás a lekérdezőgombban: ' + token.value, token)
                parser.accept('NOCOPY')
                type_name = parser.take().value
                if parser.accept('%'):
                    type_name += '%' + parser.take().value
                default = None
                if parser.accept(':=') or parser.accept('DEFAULT'):
                    default = parser.expression()
                params.append({'op': 'declare', 'name': token.value, 'type': type_name, 'constant': False, 'value': default,
                               'parameter': True})
                if not parser.accept(','):
                    break
            parser.need(')')
        if not (parser.accept('IS') or parser.accept('AS')):
            raise parser.refuse('A(z) ' + unit + ' eljárás IS/AS deklarációt igényel.')
        declarations = parser.declarations()
        parser.need('BEGIN')
        block = parser.block_rest()
        parser.need('<EOF>')
        return params, declarations + block['body'], block['handlers']

    def expand(self, nodes: list, stack=()) -> list:
        result = []
        for node in nodes:
            op = node['op']
            if op == 'block':
                node = {**node, 'body': self.expand(node['body'], stack),
                        'handlers': [{**h, 'body': self.expand(h['body'], stack)} for h in node.get('handlers', [])]}
            elif op == 'if':
                node = {**node, 'branches': [{**b, 'body': self.expand(b['body'], stack)} for b in node['branches']],
                        'else': self.expand(node['else'], stack)}
            elif (op == 'call' and node['name'] in self.units and self.units[node['name']]['kind'] == 'procedure'
                  and self.message(node, note=False) is None):
                unit = node['name']
                if unit in stack or len(stack) > 4:
                    raise Unsupported('Egymást hívó helyi eljárások a lekérdezőgombban: ' + ' -> '.join(stack + (unit,)))
                params, body, handlers = self.procedure(unit)
                if len(node['args']) > len(params) or any(p['value'] is None for p in params[len(node['args']):]):
                    raise Unsupported('A(z) ' + unit + ' hívásának argumentumai nem egyeznek a paraméterekkel.')
                for param, arg in zip(params, node['args']):
                    param['value'] = arg
                if unit not in self.expanded:
                    self.expanded.append(unit)
                node = {'op': 'block', 'body': params + self.expand(body, stack + (unit,)), 'handlers': handlers, 'unit': unit}
            result.append(node)
        return result

    # ------------------------------------------------------------------ local variables

    def variable(self, base: str) -> str:
        candidate, n = base, 2
        while candidate in self.taken:
            candidate, n = base + str(n), n + 1
        self.taken.add(candidate)
        return candidate

    def declare_all(self, nodes: list) -> None:
        """Every DECLARE (and inlined parameter) of the button: one Java local each; the roles from the use.

        An SQL text is a variable DEFAULT_WHERE / ORDER_BY gets, or one appended to such a text that only ever holds
        constant SQL; every other variable holds a value (a bind where it is pasted into the SQL).
        """
        declarations, assignments, roots, replaced = [], {}, set(), {}

        def walk(body):
            for node in body:
                op = node['op']
                if op == 'declare':
                    declarations.append(node)
                    if node['value'] is not None:
                        assignments.setdefault(node['name'], []).append(node['value'])
                elif op == 'local_assign':
                    assignments.setdefault(node['variable'], []).append(node['value'])
                elif op == 'block':
                    walk(node['body'])
                    for handler in node.get('handlers', []):
                        walk(handler['body'])
                elif op == 'if':
                    for branch in node['branches']:
                        walk(branch['body'])
                    walk(node['else'])
                elif op == 'call' and node['name'] == 'SET_BLOCK_PROPERTY' and len(node['args']) == 3:
                    prop, value = node['args'][1], node['args'][2]
                    if prop['op'] == 'symbol' and prop['name'] in {'DEFAULT_WHERE', 'ORDER_BY', 'ONETIME_WHERE'}:
                        roots.update(concat_symbols(value))
                elif op == 'select':
                    quote = quote_replace(node)
                    if quote:
                        replaced[quote[0]] = quote[1]
        walk(nodes)
        types = {}
        for node in declarations:
            if node['type'] == 'EXCEPTION':
                continue
            typ = declared_type(node['type'])
            if typ in {'rowtype', 'other', 'boolean'}:
                raise Unsupported('Nem támogatott változótípus a lekérdezőgombban: ' + node['name'] + ' ' + node['type'])
            if typ is not None and types.get(node['name']) not in {None, typ}:
                raise Unsupported('Azonos nevű, eltérő típusú helyi változók: ' + node['name'])
            types[node['name']] = typ if typ is not None else types.get(node['name'])
        sql = (set(roots) | set(replaced)) & set(types)
        changed = True
        while changed:
            changed = False
            for target in list(sql):
                for value in assignments.get(target, []):
                    for symbol in concat_symbols(value):
                        if symbol in types and symbol not in sql and all(textual(v, sql | {symbol}) for v in assignments.get(symbol, [])):
                            sql.add(symbol)
                            changed = True
        for node in declarations:
            if node['type'] == 'EXCEPTION' or node['name'] in self.vars:
                continue
            role = 'sql' if node['name'] in sql else 'value'
            typ = types.get(node['name'])
            if role == 'sql' and typ not in {'text', None}:
                raise Unsupported('Az SQL-szöveg változója nem szöveges: ' + node['name'])
            if role == 'value' and typ is None:  # %TYPE: the type of its first value
                typ = self.guess_type((assignments.get(node['name']) or [None])[0])
            var = Var(node['name'], self.variable(camel(node['name'])), 'text' if role == 'sql' else typ, role)
            var.separator = replaced.get(node['name'])
            values = assignments.get(node['name'], [])
            if node.get('parameter') and len(values) == 1 and values[0]['op'] == 'literal' and values[0]['type'] in {'text', 'number'}:
                var.constant = values[0]  # LEK('BLK'): the argument, wherever the procedure uses the parameter
            self.vars[node['name']] = var
        missing = sorted((set(roots) | set(replaced)) - set(self.vars))
        if missing:
            raise Unsupported('Nem deklarált (csomag-) szűrőváltozó a lekérdezőgombban: ' + ', '.join(missing))

    def guess_type(self, node: dict | None) -> str:
        if node is None:
            return 'text'
        source = self.reference(node) if node['op'] in {'ref', 'function'} else None
        if source is not None:
            head, _, item = source.rpartition('.')
            info = self.items.get(head, {}).get(item)
            return info['type'] if info and info['type'] in READERS else 'text'
        if node['op'] == 'literal' and node['type'] in {'number', 'text'}:
            return node['type']
        if node['op'] == 'symbol' and node['name'] in self.vars:
            return self.vars[node['name']].type or 'text'
        if node['op'] == 'function' and node['name'] in SQL_FUNCTIONS and SQL_FUNCTIONS[node['name']]:
            return SQL_FUNCTIONS[node['name']]
        if node['op'] == 'function' and node['name'] == 'NVL' and node['args']:
            return self.guess_type(node['args'][0])
        return 'text'

    # ------------------------------------------------------------------ values of the screen

    def value(self, reference: str) -> dict:
        """The Java variable of a :BLOCK.ITEM / :GLOBAL.X ... reference: read from the request, or a developer input."""
        source = reference.lstrip(':').upper()
        if '.' not in source and self.block and source in self.items.get(self.block, {}):
            source = self.block + '.' + source
        if source in self.reads:
            return self.reads[source]
        head, _, item = source.rpartition('.')
        info = self.items.get(head, {}).get(item)
        if head in {'GLOBAL', 'PARAMETER', 'SYSTEM'}:
            read = {'variable': self.variable(camel(source)), 'type': 'text',
                    'java': f'PlsqlValues.parameter(parameters, {jstr(source)})', 'parameters': True}
        elif info is not None and info['type'] in READERS:
            same = sum(1 for block in self.items.values() if item in block) > 1
            read = {'variable': self.variable(camel(source if same else item)), 'type': info['type'], 'item': info,
                    'java': f"PlsqlValues.{READERS[info['type']]}(values, {jstr(head)}, {jstr(item)})"}
        elif info is not None:
            raise Unsupported('Nem támogatott mezőtípus a lekérdezésben: :' + source)
        else:
            reason = 'ismeretlen mező, nincs a formban' if head else 'blokk nélküli mezőhivatkozás, nem egyértelmű'
            read = {'variable': self.variable(camel(source)), 'type': 'text', 'java': 'null', 'input': reason}
            self.inputs.append({'source': source, 'variable': read['variable'], 'type': 'text', 'reason': reason})
        read['source'] = source
        self.reads[source] = read
        return read

    def reference(self, node: dict) -> str | None:
        """The item a :B.I or NAME_IN('B.I') node names (normalised), or None."""
        if node['op'] == 'ref':
            source = node['name'].upper()
        elif node['op'] == 'function' and node['name'] == 'NAME_IN' and 'NAME_IN' not in self.units:
            args = node['args']
            if len(args) != 1 or args[0]['op'] != 'literal' or args[0]['type'] != 'text' or not args[0]['value']:
                raise Unsupported('NAME_IN számított mezőnévvel: a lekérdezőgombban nem oldható fel.')
            source = args[0]['value'].strip().upper()
        else:
            return None
        if '.' not in source and self.block and source in self.items.get(self.block, {}):
            source = self.block + '.' + source
        return source

    # ------------------------------------------------------------------ operands and conditions

    def operand(self, node: dict) -> dict:
        """A value as an IR operand: {kind, java, type, nullable, literal, checkbox, ...}."""
        if normal_mode_expression(node):
            return {'kind': 'literal', 'java': jstr(NORMAL_MODE), 'type': 'text', 'nullable': False, 'literal': NORMAL_MODE}
        op = node['op']
        if op == 'literal':
            if node['value'] is None or node['type'] == 'null':
                return {'kind': 'literal', 'java': 'null', 'type': 'null', 'nullable': True, 'literal': None}
            if node['type'] == 'number':
                return {'kind': 'literal', 'java': NUMBERS.get(node['value'], 'new java.math.BigDecimal(' + jstr(node['value']) + ')'),
                        'type': 'number', 'nullable': False, 'literal': node['value']}
            if node['type'] == 'text':
                return {'kind': 'literal', 'java': jstr(node['value']), 'type': 'text', 'nullable': False, 'literal': node['value']}
            raise Unsupported('Logikai érték a lekérdezőgomb kifejezésében.')
        source = self.reference(node)
        if source is not None:
            if source in self.overrides:
                var = self.overrides[source]
                return {'kind': 'var', 'java': var.java, 'type': var.type, 'nullable': True, 'literal': None, 'var': var.name}
            if source.rpartition('.')[0] in self.cleared:
                return {'kind': 'literal', 'java': 'null', 'type': 'null', 'nullable': True, 'literal': None}
            read = self.value(source)
            return {'kind': 'read', 'java': read['variable'], 'type': read['type'], 'nullable': True, 'literal': None,
                    'source': read['source'], 'checkbox': (read.get('item') or {}).get('kind') == 'checkbox'}
        if op == 'symbol' and node['name'] in self.vars:
            var = self.vars[node['name']]
            if var.constant is not None:
                return self.operand(var.constant)
            return {'kind': 'var', 'java': var.java, 'type': var.type, 'nullable': True, 'literal': None, 'var': var.name,
                    'sql': var.role == 'sql'}
        if op == 'function' and node['name'] == 'NVL' and len(node['args']) == 2 and 'NVL' not in self.units:
            first, second = (self.operand(a) for a in node['args'])
            if first['type'] == second['type'] and second['literal'] is not None:
                return {'kind': 'nvl', 'java': f"java.util.Objects.requireNonNullElse({first['java']}, {second['java']})",
                        'type': first['type'], 'nullable': False, 'literal': None, 'args': [first, second]}
        raise Unsupported('A lekérdezőgomb kifejezése nem fordítható egyszerű Java-kifejezésre: ' + str(node.get('name', op)))

    def compare(self, operator: str, left: dict, right: dict) -> tuple:
        if left['literal'] is not None and right['literal'] is None:
            left, right, operator = right, left, MIRROR[operator]
        operator = MIRROR[operator] if operator == '!=' else operator
        if left['literal'] is not None and right['literal'] is not None:
            if operator not in {'=', '<>'} or left['type'] != right['type']:
                raise Unsupported('Két állandó összehasonlítása.')
            return ('const', (left['literal'] == right['literal']) == (operator == '='))
        if 'null' in {left['type'], right['type']}:
            return ('const', False)  # a comparison with NULL is never true
        if left.get('sql') or right.get('sql'):
            raise Unsupported('SQL-szöveg összehasonlítása a lekérdezőgomb feltételében.')
        if right['literal'] is not None:
            if left['type'] == 'text' or (left['type'] == 'number' and right['type'] == 'number'):
                return ('cmp', operator, left, right)
            raise Unsupported('Típuseltérés a lekérdezőgomb feltételében: ' + left['type'] + ' / ' + right['type'])
        if left['type'] != right['type']:
            raise Unsupported('Típuseltérés a lekérdezőgomb feltételében: ' + left['type'] + ' / ' + right['type'])
        return ('cmp', operator, left, right)

    def condition(self, node: dict) -> tuple:
        op = node['op']
        if op == 'is_null':
            return ('isnull', self.operand(node['value']), node['negated'])
        if op == 'unary' and node['operator'] == 'NOT':
            return ('not', self.condition(node['value']))
        if op == 'binary' and node['operator'] in {'AND', 'OR'}:
            return (node['operator'].lower(), self.condition(node['left']), self.condition(node['right']))
        if op == 'binary' and node['operator'] in MIRROR:
            return self.compare(node['operator'], self.operand(node['left']), self.operand(node['right']))
        if op == 'literal' and node['type'] == 'boolean':
            return ('const', bool(node['value']))
        if op == 'in':
            operand = self.operand(node['value'])
            items = [self.operand(i) for i in node['items']]
            if (any(i['literal'] is None for i in items) or operand['type'] not in {'text', 'number'} or operand.get('sql')
                    or any(i['type'] != operand['type'] and operand['type'] != 'text' for i in items)):
                raise Unsupported('Az IN lista csak állandókat tartalmazhat a lekérdezőgombban.')
            return ('in', operand, items, node['negated'])
        if op == 'between':
            operand, low, high = self.operand(node['value']), self.operand(node['low']), self.operand(node['high'])
            inner = ('and', self.compare('>=', operand, low), self.compare('<=', operand, high))
            return ('not', inner) if node['negated'] else inner
        raise Unsupported('A lekérdezőgomb feltétele nem fordítható egyszerű Java-feltételre: '
                          + str(node.get('operator', node.get('name', op))))

    # ------------------------------------------------------------------ SQL templates

    def template(self, node: dict, var: Var | None) -> list:
        """The atoms of an SQL text expression: ('text', s), ('self',), ('sql', Var), ('value', node), ('choice', ...)."""
        atoms = []

        def walk(n, first):
            op = n['op']
            if op == 'binary' and n['operator'] == '||':
                walk(n['left'], first)
                walk(n['right'], False)
            elif op == 'literal' and n['type'] == 'text':
                atoms.append(('text', n['value'] or ''))
            elif op == 'literal' and n['type'] == 'number':
                atoms.append(('text', n['value']))
            elif op == 'literal' and n['type'] == 'null':
                pass  # NULL || x = x
            elif op == 'symbol' and var is not None and n['name'] == var.name:
                if not first:
                    raise Unsupported('Az SQL-szöveg változója nem az elején áll az összefűzésben: ' + var.name)
                atoms.append(('self',))
            elif op == 'symbol' and n['name'] in self.vars and self.vars[n['name']].role == 'sql':
                atoms.append(('sql', self.vars[n['name']]))
            elif (op == 'function' and n['name'] == 'CHR' and len(n['args']) == 1 and n['args'][0]['op'] == 'literal'
                  and n['args'][0]['type'] == 'number' and 'CHR' not in self.units):
                atoms.append(('text', chr(int(Decimal(n['args'][0]['value'])))))
            elif op == 'function' and n['name'] == 'DECODE' and len(n['args']) >= 3 and 'DECODE' not in self.units:
                atoms.append(self.decode(n))
            elif op == 'case':
                atoms.append(self.case(n))
            else:
                atoms.append(('value', n))
        walk(node, True)
        return atoms

    def alternative(self, node: dict | None) -> list:
        atoms = self.template(node, None) if node is not None else []
        if any(a[0] == 'self' for a in atoms):
            raise Unsupported('Az SQL-szöveg változója egy DECODE / CASE ágában.')
        return atoms

    def decode(self, node: dict) -> tuple:
        subject, rest = node['args'][0], node['args'][1:]
        branches = []
        while len(rest) >= 2:
            match, result = rest[0], rest[1]
            rest = rest[2:]
            if match['op'] == 'literal' and match['value'] is None:
                condition = self.condition({'op': 'is_null', 'value': subject, 'negated': False})  # DECODE: NULL = NULL
            else:
                condition = self.condition({'op': 'binary', 'operator': '=', 'left': subject, 'right': match})
            branches.append((condition, self.alternative(result)))
        return ('choice', branches, self.alternative(rest[0] if rest else None))

    def case(self, node: dict) -> tuple:
        branches = []
        for when in node['whens']:
            condition = when['when'] if node['operand'] is None else {'op': 'binary', 'operator': '=', 'left': node['operand'],
                                                                      'right': when['when']}
            branches.append((self.condition(condition), self.alternative(when['then'])))
        return ('choice', branches, self.alternative(node['else']))

    def sql_value(self, node: dict) -> tuple[str, str]:
        """(SQL text with binds, type) of a value pasted into the SQL text: :bind, or an SQL function around binds."""
        op = node['op']
        if op == 'symbol' and node['name'] in self.vars and self.vars[node['name']].constant is not None:
            constant = self.operand(self.vars[node['name']].constant)
            return self.sql_literal(constant), constant['type']
        if self.reference(node) is not None or (op == 'symbol' and node['name'] in self.vars
                                                and self.vars[node['name']].role == 'value'):
            operand = self.operand(node)
            if operand['kind'] == 'literal':
                return self.sql_literal(operand), operand['type']
            self.binds[operand['java']] = operand.get('source') or operand.get('var')
            return ':' + operand['java'], operand['type']
        if op == 'literal':
            operand = self.operand(node)
            return self.sql_literal(operand), operand['type']
        if op == 'function' and node['name'] in SQL_FUNCTIONS and node['name'] not in self.units:
            args = [self.sql_value(a) for a in node['args']]
            typ = SQL_FUNCTIONS[node['name']] or (args[0][1] if args else 'text')
            return node['name'] + '(' + ', '.join(a[0] for a in args) + ')', typ
        raise Unsupported('A WHERE-be fűzött érték nem fordítható kötött értékre: ' + str(node.get('name', op)))

    @staticmethod
    def sql_literal(operand: dict) -> str:
        if operand['literal'] is None:
            return 'NULL'
        if operand['type'] == 'number':
            return str(operand['literal'])
        return "'" + str(operand['literal']).replace("'", "''") + "'"

    def finish(self, atoms: list, var: Var | None, before_replace: bool) -> tuple[bool, list]:
        """(append, parts) of a template: the quotes analysed, the pasted values made binds.

        parts: ('s', text) | ('v', Java variable of an SQL text) | ('choice', [(condition, parts)], parts). A value
        inside a quoted literal closes it ('...' || :bind || '...'), an empty half disappears (= :bind).
        """
        separator = var.separator if (var is not None and before_replace) else None
        append = bool(atoms) and atoms[0][0] == 'self'
        parts = []
        state = {'out': '', 'inside': False, 'open_at': -1, 'pending': False}

        def flush():
            if state['out']:
                parts.append(('s', state['out']))
                state['out'] = ''

        def emit_text(text):
            if state['pending']:  # just after a pasted value inside a literal
                state['pending'] = False
                if text.startswith("'") and not text.startswith("''"):
                    text = text[1:]
                    state['inside'] = False
                else:
                    state['out'] += " || "
                    state['open_at'] = len(state['out'])
                    state['out'] += "'"
            i = 0
            while i < len(text):
                char = text[i]
                bind = BIND.match(text, i) if char == ':' and not state['inside'] else None
                if bind:  # :BLOCK.ITEM written into the SQL text itself: a bind of the screen's value
                    state['out'] += ':' + self.bind(bind.group(1))
                    i = bind.end()
                    continue
                if char == "'":
                    if not state['inside']:
                        state['inside'], state['open_at'] = True, len(state['out'])
                    elif i + 1 < len(text) and text[i + 1] == "'":
                        state['out'] += "''"
                        i += 2
                        continue
                    else:
                        state['inside'] = False
                state['out'] += char
                i += 1

        for atom in atoms[1:] if append else atoms:
            kind = atom[0]
            if kind == 'text':
                emit_text(atom[1].replace(separator, "'") if separator else atom[1])
            elif kind == 'value':
                sql, typ = self.sql_value(atom[1])
                if state['inside']:
                    if state['pending']:
                        state['out'] += ' || '
                    elif state['open_at'] == len(state['out']) - 1:
                        state['out'] = state['out'][:-1]  # = '' || :x  ->  = :x
                    else:
                        state['out'] += "' || "
                    state['out'] += sql if typ == 'text' else 'TO_CHAR(' + sql + ')'
                    state['pending'] = True
                elif typ == 'number':
                    state['out'] += sql
                else:
                    raise Unsupported('Szöveges érték idézőjel nélkül kerülne az SQL-be (SQL-részletként nem köthető): ' + sql)
            else:
                if state['inside']:
                    raise Unsupported('SQL-szövegváltozó vagy DECODE / CASE egy idézett szövegen belül.')
                flush()
                if kind == 'sql':
                    parts.append(('v', atom[1].java))
                elif kind == 'choice':
                    branches = [(condition, self.finish(alt, None, False)[1]) for condition, alt in atom[1]]
                    parts.append(('choice', branches, self.finish(atom[2], None, False)[1]))
                else:
                    raise Unsupported('Az SQL-szöveg változója nem az elején áll az összefűzésben.')
        if state['pending']:
            state['out'] += " || '"
            state['inside'] = True
        if state['inside']:
            raise Unsupported('Az SQL-részlet idézőjelei nem párosak; a kötött értékek nem azonosíthatók.')
        flush()
        return append, parts

    def bind(self, reference: str) -> str:
        """A Forms bind in the DEFAULT_WHERE text: the item's value at EXECUTE_QUERY time."""
        operand = self.operand({'op': 'ref', 'name': reference.upper()})
        if operand['kind'] == 'literal':
            raise Unsupported('A WHERE egy törölt (CLEAR_FORM) mezőre hivatkozik: :' + reference.upper())
        self.binds[operand['java']] = operand.get('source') or operand.get('var')
        return operand['java']

    # ------------------------------------------------------------------ statements

    def builder_like(self, unit: str) -> bool:
        return bool(re.search(r'\b(?:set_block_property|execute_query|default_where|do_key)\b', self.units[unit]['text'] or '', re.I))

    def message(self, node: dict, note: bool = True) -> tuple | None:
        """MESSAGE(text), a catalogued message routine or a procedure called with one text: ('message', text IR)."""
        from .discovery import FORMS_BUILTINS
        from .messages import configured
        from .query_actions import message_wrapper
        if node['op'] != 'call' or not node['args']:
            return None
        name_ = node['name']
        catalog = configured(self.messages_catalog)
        position = 1 if name_ == 'MESSAGE' else catalog['calls'].get(name_)
        if position is None:
            local = name_ in self.units and self.units[name_]['kind'] == 'procedure'
            wrapper = local and message_wrapper(self.units[name_]['text'])
            one_text = len(node['args']) == 1 and node['args'][0]['op'] == 'literal' and node['args'][0]['type'] == 'text'
            if not wrapper and (not one_text or name_ in self.model.get('procedures', {})
                                or (name_ in FORMS_BUILTINS and not local)
                                or (name_ in self.units and not local) or (local and self.builder_like(name_))):
                return None
            position = 1
        if len(node['args']) < position:
            return None
        if note and name_ != 'MESSAGE' and name_ not in self.called:
            self.called.append(name_)
        return ('message', self.message_text(node['args'][position - 1]))

    def message_text(self, node: dict) -> dict:
        """A message: literals, screen values and value variables, concatenated (NULL is an empty text)."""
        parts = []

        def walk(n):
            if n['op'] == 'binary' and n['operator'] == '||':
                walk(n['left'])
                walk(n['right'])
            else:
                operand = self.operand(n)
                if operand.get('sql'):
                    raise Unsupported('SQL-szöveg az üzenetben.')
                parts.append(operand)
        walk(node)
        return {'kind': 'concat', 'parts': parts}

    def statements(self, nodes: list) -> list:
        result = []
        for node in nodes:
            op = node['op']
            if op in {'noop', 'pragma'}:
                continue
            if op == 'declare':
                var = self.vars.get(node['name'])
                if var is not None and node['value'] is not None and var.constant is None:
                    result += self.assign(var, node['value'])
                continue
            if op == 'block':
                if node.get('handlers'):
                    raise Unsupported('Kivételkezelő a lekérdezőgombban (EXCEPTION WHEN '
                                      + ', '.join(n for h in node['handlers'] for n in h['names']) + ').')
                result += self.statements(node['body'])
            elif op == 'if':
                self.nesting += 1
                branches = [(self.condition(b['condition']), self.statements(b['body'])) for b in node['branches']]
                result.append(('if', branches, self.statements(node['else'])))
                self.nesting -= 1
            elif op == 'abort':
                result.append(('abort',))
            elif op == 'local_assign':
                var = self.vars.get(node['variable'])
                if var is None:
                    raise Unsupported('Nem deklarált változó vagy csomagváltozó a lekérdezőgombban: ' + node['variable'])
                if var.constant is not None:
                    raise Unsupported('Paraméter módosítása a szűrőépítőben: ' + var.name)
                result += self.assign(var, node['value'])
            elif op == 'assign':
                self.item_write(node)
            elif op == 'select':
                quote = quote_replace(node)
                if quote and quote[0] in self.vars:
                    self.vars[quote[0]].separator = None  # the pieces from here on are written with real quotes
                    continue
                raise Unsupported('SELECT ... INTO a lekérdezőgombban (INTO ' + ', '.join(
                    ':' + t if '.' in t else t for t in node['into']) + '): a lekérdezés eredménye (PageResult) a képernyő '
                    'más mezőit nem tölti.')
            elif op == 'call' and self.message(node):
                result.append(self.message(node))
            elif op == 'call' and node['name'] == 'SET_BLOCK_PROPERTY' and len(node['args']) == 3:
                result += self.block_property(node)
            elif op == 'call' and node['name'] in IGNORED and node['name'] not in self.units:
                result += self.plumbing(node)
            elif op == 'raise':
                raise Unsupported('Saját kivétel dobása a lekérdezőgombban: RAISE ' + str(node.get('name')))
            else:
                raise Unsupported('A lekérdezőgombban nem lekérdezési utasítás van: ' + str(node.get('name', op)))
        return result

    def plumbing(self, node: dict) -> list:
        name_, args = node['name'], node['args']
        if name_ == 'EXECUTE_QUERY' or (name_ == 'DO_KEY' and args and str(args[0].get('value', '')).upper() == 'EXECUTE_QUERY'):
            self.executed = True
            return [('query',)]
        if name_ in {'CLEAR_FORM', 'CLEAR_BLOCK'}:
            if self.nesting:
                raise Unsupported(name_ + ' egy IF ágában a lekérdezőgombban.')
            self.cleared |= set(self.items) if name_ == 'CLEAR_FORM' else {self.current}
            self.overrides = {k: v for k, v in self.overrides.items() if k.rpartition('.')[0] not in self.cleared}
        if name_ == 'GO_BLOCK' and args and args[0]['op'] == 'literal' and args[0]['type'] == 'text':
            self.current = (args[0]['value'] or '').upper()
        if name_ not in self.left_out:
            self.left_out.append(name_)
        return []

    def assign(self, var: Var, value: dict) -> list:
        var.assigned += 1
        if var.role == 'sql':
            append, parts = self.finish(self.template(value, var), var, var.separator is not None)
            return [('set', var, append, parts)]
        if value['op'] == 'binary' and value['operator'] == '||':
            if var.type != 'text':
                raise Unsupported('Összefűzött szöveg nem szöveges változóban: ' + var.name)
            operand = self.message_text(value)
        else:
            operand = self.operand(value)
            if operand['type'] not in {var.type, 'null'}:
                raise Unsupported('Típuseltérés az értékadásban: ' + var.name + ' (' + str(var.type) + ' <- ' + operand['type'] + ')')
        var.origin = operand.get('source') if operand.get('kind') == 'read' and var.assigned == 1 and not self.nesting else None
        return [('let', var, operand)]

    def item_write(self, node: dict) -> None:
        """:ITEM := saved_value after CLEAR_FORM (the Forms way to keep a value): the reads see it; nothing else."""
        source = self.reference({'op': 'ref', 'name': node['target']})
        value = node['value']
        var = self.vars.get(value['name']) if value['op'] == 'symbol' else None
        if var is None or var.role != 'value' or self.nesting:
            raise Unsupported('A lekérdezőgomb képernyőmezőt ír (:' + source + '): a lekérdezés eredménye nem viszi vissza.')
        self.overrides[source] = var
        self.writes.append((source, var))

    def block_property(self, node: dict) -> list:
        block, prop, value = node['args']
        if block['op'] == 'symbol' and block['name'] in self.vars and self.vars[block['name']].constant is not None:
            block = self.vars[block['name']].constant
        if block['op'] != 'literal' or block['type'] != 'text' or prop['op'] != 'symbol':
            raise Unsupported('SET_BLOCK_PROPERTY nem szó szerinti blokkal.')
        target = (block['value'] or '').upper()
        if prop['name'] in {'DEFAULT_WHERE', 'ONETIME_WHERE'}:
            if self.target not in {None, target}:
                raise Unsupported('A lekérdezőgomb több blokk DEFAULT_WHERE-jét állítja.')
            self.target = target
            return [('where', self.text_expression(value))]
        if prop['name'] == 'ORDER_BY':
            if value['op'] == 'literal' and value['type'] == 'text':
                self.order_static = value['value']
            return [('order', self.text_expression(value))]
        if 'SET_BLOCK_PROPERTY ' + prop['name'] not in self.left_out:
            self.left_out.append('SET_BLOCK_PROPERTY ' + prop['name'])
        return []

    def text_expression(self, value: dict) -> list:
        """The SQL text given to DEFAULT_WHERE / ORDER_BY: an SQL text variable or an expression of pieces."""
        if value['op'] == 'symbol' and value['name'] in self.vars and self.vars[value['name']].role == 'sql':
            return [('v', self.vars[value['name']].java)]
        return self.finish(self.template(value, None), None, False)[1]

    def final_checks(self) -> None:
        for source, var in self.writes:
            if var.origin != source:
                raise Unsupported('A lekérdezőgomb képernyőmezőt ír (:' + source + '): a lekérdezés eredménye nem viszi vissza.')


# ---------------------------------------------------------------------- Java from the IR

def java_compare(operator: str, left: dict, right: dict) -> str:
    value, guard = left['java'], (left['java'] + ' != null && ') if left['nullable'] else ''
    if right['literal'] is not None:
        if left['type'] == 'text':
            text = right['literal'] if right['type'] == 'text' else str(right['literal'])  # a CHAR checkbox vs 1
            if operator == '=':
                return f'{jstr(text)}.equals({value})'
            if operator == '<>':
                return f'{guard}!{jstr(text)}.equals({value})'
            return f"{guard}{value}.compareTo({jstr(text)}) {operator} 0"
        if operator == '=' and left.get('checkbox') and re.fullmatch(r'\d+', right['literal']):
            return f"{right['java']}.equals({value})"  # the screen sends the checkbox value as written
        return f"{guard}{value}.compareTo({right['java']}) {'!=' if operator == '<>' else operator} 0"
    guards = guard + ((right['java'] + ' != null && ') if right['nullable'] else '')
    if left['type'] == 'text' and operator in {'=', '<>'}:
        return f"{guards}{'' if operator == '=' else '!'}{value}.equals({right['java']})"
    return f"{guards}{value}.compareTo({right['java']}) {'!=' if operator == '<>' else operator} 0"


def java_condition(condition: tuple, top: bool = True) -> str:
    kind = condition[0]
    if kind == 'const':
        return 'true' if condition[1] else 'false'
    if kind == 'isnull':
        operand = condition[1]
        if operand.get('sql'):
            return ('!' if condition[2] else '') + operand['java'] + '.isEmpty()'
        return operand['java'] + (' != null' if condition[2] else ' == null')
    if kind == 'not':
        return '!(' + java_condition(condition[1]) + ')'
    if kind == 'and':
        return java_condition(condition[1], False) + ' && ' + java_condition(condition[2], False)
    if kind == 'or':
        text = java_condition(condition[1]) + ' || ' + java_condition(condition[2])
        return text if top else '(' + text + ')'
    if kind == 'cmp':
        return java_compare(condition[1], condition[2], condition[3])
    if kind == 'in':
        operand, items, negated = condition[1], condition[2], condition[3]
        tests = [java_compare('=', operand, item) for item in items]
        if not negated:
            text = ' || '.join(tests)
            return text if top or len(tests) == 1 else '(' + text + ')'
        return operand['java'] + ' != null && ' + ' && '.join('!' + t for t in tests)
    raise AssertionError(kind)


def java_parts(parts: list, indent: str) -> str:
    out = []
    for part in parts:
        if part[0] == 's':
            out.append(java_text_block(part[1], indent + '        ') if '\n' in part[1] else jstr(part[1]))
        elif part[0] == 'v':
            out.append(part[1])
        else:
            text = java_parts(part[2], indent) if part[2] else '""'
            for condition, alt in reversed(part[1]):
                text = '(' + java_condition(condition) + ' ? ' + (java_parts(alt, indent) if alt else '""') + ' : ' + text + ')'
            out.append(text)
    return ' + '.join(out) if out else '""'


def java_value(operand: dict) -> str:
    if operand.get('kind') == 'concat':
        if len(operand['parts']) == 1 and operand['parts'][0]['kind'] == 'literal':
            return operand['parts'][0]['java']
        return ' + '.join(p['java'] if p['kind'] == 'literal' else 'java.util.Objects.toString(' + p['java'] + ', "")'
                          for p in operand['parts']) or '""'
    return operand['java']


def java_statements(statements: list, indent: str, binds: list) -> list[str]:
    lines = []
    for statement in statements:
        kind = statement[0]
        if kind == 'if':
            for index, (condition, body) in enumerate(statement[1]):
                lines.append(indent + ('if (' if index == 0 else '} else if (') + java_condition(condition) + ') {')
                lines += java_statements(body, indent + '    ', binds)
            if statement[2]:
                lines.append(indent + '} else {')
                lines += java_statements(statement[2], indent + '    ', binds)
            lines.append(indent + '}')
        elif kind == 'set':
            var, append, parts = statement[1], statement[2], statement[3]
            lines.append(indent + var.java + (' += ' if append else ' = ') + java_parts(parts, indent) + ';')
        elif kind == 'let':
            lines.append(indent + statement[1].java + ' = ' + java_value(statement[2]) + ';')
        elif kind == 'where':
            lines.append(indent + 'q.where = ' + java_parts(statement[1], indent) + ';')
        elif kind == 'order':
            lines.append(indent + 'q.orderBy = ' + java_parts(statement[1], indent) + ';')
        elif kind == 'message':
            lines.append(indent + 'q.messages.add(' + java_value(statement[1]) + ');')
        elif kind == 'abort':
            lines.append(indent + 'return q;')
        elif kind == 'query':
            lines.append(indent + 'q.run = true;')
            lines += [indent + f'q.params.addValue({jstr(name_)}, {name_});' for name_ in binds]
        else:
            raise AssertionError(kind)
    return lines


# ---------------------------------------------------------------------- the equivalence check

class Unverifiable(Exception):
    """A case the check cannot decide in Python (an SQL function of the database): not a difference."""


class Abort(Exception):
    pass


def oracle_text(value) -> str | None:
    """A PL/SQL value as text (implicit conversion); '' is NULL."""
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise Unverifiable('logikai érték szövegként')
    if isinstance(value, Decimal):
        text = format(value.normalize(), 'f')
        return text if '.' not in text else text.rstrip('0').rstrip('.')
    return str(value)


def oracle_compare(operator: str, left, right):
    if left is None or right is None or left == '' or right == '':
        return None
    if isinstance(left, Decimal) or isinstance(right, Decimal):
        try:
            left, right = Decimal(str(left)), Decimal(str(right))
        except InvalidOperation:
            raise Unverifiable('szám és szöveg összehasonlítása (ORA-01722)')
    return {'=': left == right, '<>': left != right, '!=': left != right, '<': left < right, '>': left > right,
            '<=': left <= right, '>=': left >= right}[operator]


def resolve_forms_binds(text: str | None, builder: Builder, screen: dict) -> str | None:
    """The :BLOCK.ITEM binds of a DEFAULT_WHERE as Forms binds them at EXECUTE_QUERY: the items' values."""
    if text is None:
        return None
    pieces = text.split("'")
    for index in range(0, len(pieces), 2):
        def value(match):
            source = builder.reference({'op': 'ref', 'name': match.group(1).upper()})
            return sql_literal(screen.get(source))
        pieces[index] = BIND.sub(value, pieces[index])
    return "'".join(pieces)


class PlsqlEvaluator:
    """The expanded original PL/SQL, interpreted with Oracle semantics: the WHERE, the order, the messages."""

    def __init__(self, builder: Builder, case: dict):
        self.builder = builder
        self.locals, self.screen = {}, dict(case)
        self.where = self.order = None
        self.messages, self.run = [], False

    def evaluate(self, node: dict | None):
        if node is None:
            return None
        op = node['op']
        if op == 'literal':
            if node['type'] == 'number':
                return Decimal(node['value'])
            return node['value'] if node['type'] != 'null' else None
        if normal_mode_expression(node):
            return NORMAL_MODE
        source = self.builder.reference(node) if op in {'ref', 'function'} else None
        if source is not None:
            return self.screen.get(source)
        if op == 'symbol':
            if node['name'] not in self.locals:
                raise Unverifiable('ismeretlen név: ' + node['name'])
            return self.locals[node['name']]
        if op == 'is_null':
            value = self.evaluate(node['value'])
            return (value is not None and value != '') if node['negated'] else (value is None or value == '')
        if op == 'unary':
            value = self.evaluate(node['value'])
            if node['operator'] == 'NOT':
                return None if value is None else not value
            return None if value is None else (-value if node['operator'] == '-' else value)
        if op == 'binary':
            operator = node['operator']
            left, right = self.evaluate(node['left']), self.evaluate(node['right'])
            if operator == 'AND':
                return False if left is False or right is False else (None if left is None or right is None else True)
            if operator == 'OR':
                return True if left is True or right is True else (None if left is None or right is None else False)
            if operator == '||':
                return ((oracle_text(left) or '') + (oracle_text(right) or '')) or None
            if operator in MIRROR:
                return oracle_compare(operator, left, right)
            if left is None or right is None:
                return None
            left, right = Decimal(str(left)), Decimal(str(right))
            return {'+': left + right, '-': left - right, '*': left * right, '/': left / right}[operator]
        if op == 'in':
            value = self.evaluate(node['value'])
            results = [oracle_compare('=', value, self.evaluate(i)) for i in node['items']]
            found = True if any(r is True for r in results) else (None if any(r is None for r in results) else False)
            return (None if found is None else not found) if node['negated'] else found
        if op == 'between':
            value = self.evaluate(node['value'])
            low = oracle_compare('>=', value, self.evaluate(node['low']))
            high = oracle_compare('<=', value, self.evaluate(node['high']))
            result = False if low is False or high is False else (None if low is None or high is None else True)
            return (None if result is None else not result) if node['negated'] else result
        if op == 'case':
            for when in node['whens']:
                test = (self.evaluate(when['when']) if node['operand'] is None
                        else oracle_compare('=', self.evaluate(node['operand']), self.evaluate(when['when'])))
                if test is True:
                    return self.evaluate(when['then'])
            return self.evaluate(node['else'])
        if op == 'function':
            return self.function(node['name'], node['args'])
        raise Unverifiable(op)

    def function(self, name_: str, args: list):
        if name_ == 'DECODE':
            subject, rest = self.evaluate(args[0]), args[1:]
            while len(rest) >= 2:
                match = self.evaluate(rest[0])
                if (subject is None and match is None) or oracle_compare('=', subject, match) is True:
                    return self.evaluate(rest[1])
                rest = rest[2:]
            return self.evaluate(rest[0]) if rest else None
        values = [self.evaluate(a) for a in args]
        if name_ == 'NVL' and len(values) == 2:
            return values[1] if values[0] is None or values[0] == '' else values[0]
        if name_ == 'CHR' and len(values) == 1:
            return chr(int(values[0]))
        if name_ == 'REPLACE' and len(values) in {2, 3}:
            text = oracle_text(values[0]) or ''
            return text.replace(oracle_text(values[1]) or '', (oracle_text(values[2]) or '') if len(values) > 2 else '') or None
        if name_ in {'UPPER', 'LOWER', 'TRIM', 'LTRIM', 'RTRIM'} and len(values) == 1:
            text = oracle_text(values[0])
            if text is None:
                return None
            return {'UPPER': text.upper(), 'LOWER': text.lower(), 'TRIM': text.strip(' '), 'LTRIM': text.lstrip(' '),
                    'RTRIM': text.rstrip(' ')}[name_] or None
        if name_ == 'TO_CHAR' and len(values) == 1 and (values[0] is None or isinstance(values[0], Decimal)):
            return oracle_text(values[0])
        raise Unverifiable(name_ + '(...)')

    def execute(self, nodes: list):
        for node in nodes:
            op = node['op']
            if op in {'noop', 'pragma'}:
                continue
            if op == 'declare':
                if node['type'] != 'EXCEPTION':
                    self.locals[node['name']] = self.evaluate(node['value'])
            elif op == 'block':
                self.execute(node['body'])
            elif op == 'if':
                for branch in node['branches']:
                    if self.evaluate(branch['condition']) is True:
                        self.execute(branch['body'])
                        break
                else:
                    self.execute(node['else'])
            elif op == 'abort':
                raise Abort()
            elif op == 'local_assign':
                self.locals[node['variable']] = self.evaluate(node['value'])
            elif op == 'assign':
                self.screen[self.builder.reference({'op': 'ref', 'name': node['target']})] = self.evaluate(node['value'])
            elif op == 'select':
                variable, separator = quote_replace(node)
                text = oracle_text(self.locals.get(variable))
                self.locals[variable] = text.replace(separator, "'") if text else None
            elif op == 'call':
                self.call(node)

    def call(self, node: dict):
        name_, args = node['name'], node['args']
        shown = self.builder.message(node, note=False)
        if shown:
            self.messages.append(''.join(oracle_text(self.operand(p)) or '' for p in shown[1]['parts']))
            return
        if name_ == 'SET_BLOCK_PROPERTY':
            prop = args[1].get('name')
            if prop in {'DEFAULT_WHERE', 'ONETIME_WHERE'}:
                self.where = oracle_text(self.evaluate(args[2]))
            elif prop == 'ORDER_BY':
                self.order = oracle_text(self.evaluate(args[2]))
        elif name_ == 'EXECUTE_QUERY' or (name_ == 'DO_KEY' and args and str(args[0].get('value', '')).upper() == 'EXECUTE_QUERY'):
            self.run = True
            self.where = resolve_forms_binds(self.where, self.builder, self.screen)
        elif name_ == 'CLEAR_FORM':
            self.screen = {k: None for k in self.screen}
        elif name_ == 'CLEAR_BLOCK':
            current = getattr(self, 'current', self.builder.block)
            self.screen = {k: (None if k.rpartition('.')[0] == current else v) for k, v in self.screen.items()}
        elif name_ == 'GO_BLOCK' and args and args[0]['op'] == 'literal':
            self.current = (args[0]['value'] or '').upper()

    def operand(self, operand: dict):
        kind = operand['kind']
        if kind == 'literal':
            return Decimal(operand['literal']) if operand['type'] == 'number' and operand['literal'] is not None else operand['literal']
        if kind == 'read':
            return self.screen.get(operand['source'])
        if kind == 'var':
            return self.locals.get(operand['var'])
        if kind == 'nvl':
            first = self.operand(operand['args'][0])
            return self.operand(operand['args'][1]) if first is None or first == '' else first
        raise Unverifiable(kind)

    def outcome(self) -> dict:
        try:
            self.execute(self.builder.ast)
        except Abort:
            pass
        return {'run': self.run, 'where': self.where if self.run else None, 'order': self.order if self.run else None,
                'messages': self.messages}


class IrEvaluator:
    """The compiled IR (what the Java code does), on the same case: the WHERE with binds, the binds' values."""

    def __init__(self, builder: Builder, case: dict):
        self.builder, self.case = builder, case
        self.locals = {var.java: ('' if var.role == 'sql' else None) for var in builder.vars.values() if var.constant is None}
        self.where = self.order = None
        self.messages, self.run, self.params = [], False, {}

    def operand(self, operand: dict):
        kind = operand['kind']
        if kind == 'literal':
            if operand['literal'] is None:
                return None
            return Decimal(operand['literal']) if operand['type'] == 'number' else operand['literal']
        if kind == 'read':
            return self.case.get(operand['source'])
        if kind == 'var':
            return self.locals.get(operand['java'])
        if kind == 'nvl':
            first = self.operand(operand['args'][0])
            return self.operand(operand['args'][1]) if first is None else first
        if kind == 'concat':
            return ''.join(oracle_text(self.operand(p)) or '' for p in operand['parts'])
        raise AssertionError(kind)

    def compare(self, operator: str, left: dict, right: dict) -> bool:
        """java_compare's semantics: null is never equal, a text compares with the literal's text."""
        value = self.operand(left)
        if right['literal'] is not None:
            if value is None:
                return False
            if left['type'] == 'text':
                text = str(right['literal'])
                return {'=': text == value, '<>': text != value, '<': value < text, '>': value > text,
                        '<=': value <= text, '>=': value >= text}[operator]
            literal = Decimal(right['literal'])
            return {'=': literal == value, '<>': value != literal, '<': value < literal, '>': value > literal,
                    '<=': value <= literal, '>=': value >= literal}[operator]
        other = self.operand(right)
        if value is None or other is None:
            return False
        return {'=': value == other, '<>': value != other, '<': value < other, '>': value > other,
                '<=': value <= other, '>=': value >= other}[operator]

    def condition(self, condition: tuple) -> bool:
        kind = condition[0]
        if kind == 'const':
            return condition[1]
        if kind == 'isnull':
            value = self.operand(condition[1])
            empty = value is None or value == ''
            return not empty if condition[2] else empty
        if kind == 'not':
            return not self.condition(condition[1])
        if kind == 'and':
            return self.condition(condition[1]) and self.condition(condition[2])
        if kind == 'or':
            return self.condition(condition[1]) or self.condition(condition[2])
        if kind == 'cmp':
            return self.compare(condition[1], condition[2], condition[3])
        if kind == 'in':
            value = self.operand(condition[1])
            hits = [self.compare('=', condition[1], item) for item in condition[2]]
            return (value is not None and not any(hits)) if condition[3] else any(hits)
        raise AssertionError(kind)

    def parts(self, parts: list) -> str:
        out = []
        for part in parts:
            if part[0] == 's':
                out.append(part[1])
            elif part[0] == 'v':
                out.append(self.locals.get(part[1]) or '')
            else:
                for condition, alt in part[1]:
                    if self.condition(condition):
                        out.append(self.parts(alt))
                        break
                else:
                    out.append(self.parts(part[2]))
        return ''.join(out)

    def execute(self, statements: list):
        for statement in statements:
            kind = statement[0]
            if kind == 'if':
                for condition, body in statement[1]:
                    if self.condition(condition):
                        self.execute(body)
                        break
                else:
                    self.execute(statement[2])
            elif kind == 'set':
                var, append, parts = statement[1], statement[2], statement[3]
                self.locals[var.java] = ((self.locals.get(var.java) or '') if append else '') + self.parts(parts)
            elif kind == 'let':
                value = self.operand(statement[2])
                self.locals[statement[1].java] = None if value == '' else value
            elif kind == 'where':
                self.where = self.parts(statement[1])
            elif kind == 'order':
                self.order = self.parts(statement[1])
            elif kind == 'message':
                self.messages.append(self.operand(statement[1]))
            elif kind == 'abort':
                raise Abort()
            elif kind == 'query':
                self.run = True
                for name_, source in self.builder.binds.items():
                    self.params[name_] = self.locals[name_] if name_ in self.locals else self.case.get(source)

    def outcome(self) -> dict:
        try:
            self.execute(self.builder.ir)
        except Abort:
            pass
        return {'run': self.run, 'where': (self.where or None) if self.run else None,
                'order': (self.order or None) if self.run else None, 'messages': self.messages, 'params': self.params}


def sql_literal(value) -> str:
    if value is None:
        return 'NULL'
    if isinstance(value, Decimal):
        return oracle_text(value)
    return "'" + str(value).replace("'", "''") + "'"


SQL_TOKEN = re.compile(r"\s+|'(?:[^']|'')*'|\d+(?:\.\d+)?|[A-Za-z_][\w$#.]*|\|\||<>|!=|<=|>=|.", re.S)


def sql_tokens(sql: str) -> list[str]:
    return [t for t in SQL_TOKEN.findall(sql) if not t.isspace()]


def literal_value(token: str):
    """The text of an SQL literal token ('a', 12, NULL), or ... for anything else; '' is NULL."""
    if token.upper() == 'NULL':
        return None
    if token.startswith("'"):
        return token[1:-1].replace("''", "'") or None
    if re.fullmatch(r'\d+(?:\.\d+)?', token):
        return token
    return ...


def as_literal(value) -> str:
    return 'NULL' if value is None else "'" + value.replace("'", "''") + "'"


def normalise(sql: str | None) -> str | None:
    """Comparable SQL text, with Oracle's literal semantics: '' is NULL, 'a' || NULL || 'b' is 'ab', the SQL functions
    NVL / TO_CHAR / UPPER / LOWER / TRIM of literals folded; whitespace ignored."""
    if sql is None:
        return None
    tokens = sql_tokens(sql)
    changed = True
    while changed:
        changed = False
        for i in range(len(tokens)):
            # FUNC ( lit [, lit] )
            if (tokens[i].upper() in {'NVL', 'TO_CHAR', 'UPPER', 'LOWER', 'TRIM'} and i + 3 < len(tokens) and tokens[i + 1] == '('
                    and literal_value(tokens[i + 2]) is not ...):
                first = literal_value(tokens[i + 2])
                if tokens[i + 3] == ')':
                    if tokens[i].upper() == 'NVL':
                        continue
                    text = None if first is None else {'TO_CHAR': first, 'UPPER': first.upper(), 'LOWER': first.lower(),
                                                       'TRIM': first.strip()}[tokens[i].upper()]
                    tokens[i:i + 4] = [as_literal(text)]
                    changed = True
                    break
                if (tokens[i].upper() == 'NVL' and tokens[i + 3] == ',' and i + 5 < len(tokens)
                        and literal_value(tokens[i + 4]) is not ... and tokens[i + 5] == ')'):
                    tokens[i:i + 6] = [tokens[i + 2] if first is not None else tokens[i + 4]]
                    changed = True
                    break
            # lit || lit
            if (tokens[i] == '||' and 0 < i < len(tokens) - 1 and literal_value(tokens[i - 1]) is not ...
                    and literal_value(tokens[i + 1]) is not ...):
                left, right = literal_value(tokens[i - 1]), literal_value(tokens[i + 1])
                tokens[i - 1:i + 2] = [as_literal(((left or '') + (right or '')) or None)]
                changed = True
                break
    text = ' '.join(as_literal(literal_value(t)) if t.startswith("'") else t for t in tokens)
    return re.sub(r'\s+([,)])', r'\1', re.sub(r'\(\s+', '(', text))


def inline(sql: str | None, params: dict) -> str | None:
    """The binds of the Java WHERE replaced by their values' SQL literals (outside the quoted literals only)."""
    if sql is None:
        return None
    pieces = sql.split("'")
    for index in range(0, len(pieces), 2):
        pieces[index] = re.sub(r':([A-Za-z][\w$#]*)', lambda m: sql_literal(params[m.group(1)]) if m.group(1) in params
                               else m.group(0), pieces[index])
    return "'".join(pieces)


def cases(builder: Builder) -> list[dict]:
    """Input cases: every screen value null, each literal it is compared with, and one other value."""
    candidates = {}

    def note(operand, literal=None):
        if operand.get('kind') == 'read':
            values = candidates.setdefault(operand['source'], [None])
            if literal is not None and literal not in values:
                values.append(literal)
        elif operand.get('kind') == 'nvl':
            note(operand['args'][0], literal)

    def walk_condition(condition):
        kind = condition[0]
        if kind in {'and', 'or'}:
            walk_condition(condition[1])
            walk_condition(condition[2])
        elif kind == 'not':
            walk_condition(condition[1])
        elif kind == 'isnull':
            note(condition[1])
        elif kind == 'cmp':
            note(condition[2], condition[3]['literal'])
            note(condition[3], condition[2]['literal'])
        elif kind == 'in':
            for item in condition[2]:
                note(condition[1], item['literal'])

    def walk(statements):
        for statement in statements:
            if statement[0] == 'if':
                for condition, body in statement[1]:
                    walk_condition(condition)
                    walk(body)
                walk(statement[2])
            elif statement[0] in {'set', 'where', 'order'}:
                for part in statement[-1]:
                    if part[0] == 'choice':
                        for condition, _ in part[1]:
                            walk_condition(condition)
    walk(builder.ir)
    for source, read in builder.reads.items():
        values = candidates.setdefault(source, [None])
        if read['type'] == 'number':
            values[:] = [None if v is None else Decimal(str(v)) for v in values]
            values.append(max([v for v in values if v is not None] or [Decimal(0)]) + 7)
        elif read['type'] == 'text':
            values[:] = [None if v is None else str(v) for v in values]
            values.append('Q' + str(len(values)))
        # dates stay NULL here: their text form depends on NLS
    sources = sorted(candidates)
    total = 1
    for source in sources:
        total *= len(candidates[source])
    if total <= MAX_CASES:
        return [dict(zip(sources, combination)) for combination in itertools.product(*(candidates[s] for s in sources))]
    # Too many combinations: the "everything filled" case, the empty screen, one case per path of IF branches (the
    # values their conditions need), and one factor at a time.
    base = {s: candidates[s][-1] for s in sources}
    types = {s: r['type'] for s, r in builder.reads.items()}
    result = [dict(base), {s: None for s in sources}]
    for constraints in branch_paths(builder.ir, {}, types):
        result.append({**base, **constraints})
    for source in sources:
        for value in candidates[source][:-1]:
            result.append({**base, source: value})
    unique = []
    for case in result:
        if case not in unique:
            unique.append(case)
    return unique[:MAX_CASES * 4]


def condition_values(condition: tuple, types: dict) -> list[dict]:
    """Screen values that make a condition true (alternatives of an OR; {} where nothing is known)."""
    kind = condition[0]
    if kind == 'and':
        result = []
        for left in condition_values(condition[1], types):
            for right in condition_values(condition[2], types):
                if all(left[k] == right[k] for k in left.keys() & right.keys()):
                    result.append({**left, **right})
        return result[:16] or [{}]
    if kind == 'or':
        return (condition_values(condition[1], types) + condition_values(condition[2], types))[:16]
    operand = condition[1] if kind in {'isnull', 'in'} else condition[2] if kind == 'cmp' else None
    while operand is not None and operand.get('kind') == 'nvl':
        operand = operand['args'][0]
    if operand is None or operand.get('kind') != 'read':
        return [{}]
    source = operand['source']
    typed = (lambda v: Decimal(str(v))) if types.get(source) == 'number' else str
    if kind == 'isnull':
        return [{}] if condition[2] else [{source: None}]
    if kind == 'cmp' and condition[1] == '=' and condition[3].get('literal') is not None:
        return [{source: typed(condition[3]['literal'])}]
    if kind == 'in' and not condition[3]:
        return [{source: typed(item['literal'])} for item in condition[2]][:4]
    return [{}]


def branch_paths(statements: list, path: dict, types: dict):
    """The screen values along every path into the IF branches (and the DECODE / CASE choices) of the IR."""
    for statement in statements:
        if statement[0] == 'if':
            for condition, body in statement[1]:
                for values in condition_values(condition, types):
                    if all(path[k] == values[k] for k in path.keys() & values.keys()):
                        merged = {**path, **values}
                        yield merged
                        yield from branch_paths(body, merged, types)
            yield from branch_paths(statement[2], path, types)
        elif statement[0] in {'set', 'where', 'order'}:
            for part in statement[-1]:
                if part[0] == 'choice':
                    for condition, _ in part[1]:
                        for values in condition_values(condition, types):
                            if all(path[k] == values[k] for k in path.keys() & values.keys()):
                                yield {**path, **values}


def equivalence(builder: Builder) -> dict:
    """The original PL/SQL and the compiled IR on every case: {'cases', 'checked', 'unverifiable', 'difference', 'tests'}."""
    report = {'cases': 0, 'checked': 0, 'unverifiable': 0, 'difference': None, 'tests': []}
    for case in cases(builder):
        report['cases'] += 1
        java = IrEvaluator(builder, case).outcome()
        try:
            original = PlsqlEvaluator(builder, case).outcome()
        except (Unverifiable, InvalidOperation, ZeroDivisionError, TypeError, ValueError) as exc:
            report['unverifiable'] += 1
            report.setdefault('unverifiable_reason', str(exc))
            continue
        report['tests'].append({'case': case, 'expected': java})
        got = {'run': java['run'], 'where': normalise(inline(java['where'], java['params'])),
               'order': normalise(inline(java['order'], java['params'])), 'messages': java['messages']}
        want = {'run': original['run'], 'where': normalise(original['where']), 'order': normalise(original['order']),
                'messages': original['messages']}
        if got != want and got['where'] != want['where'] and {k: v for k, v in got.items() if k != 'where'} == \
                {k: v for k, v in want.items() if k != 'where'} and want['where'] is not None and java['where'] is not None:
            # A NULL the Forms code pasted into the SQL text outside the quotes leaves nothing there: the original
            # SQL would not even parse (ORA-00936). The bind is NULL in Java: not a difference to report.
            empty = {k: (v if v is not None else '') for k, v in java['params'].items()}
            if normalise(inline_raw(java['where'], empty)) == want['where']:
                report['unverifiable'] += 1
                report.setdefault('unverifiable_reason', 'NULL érték idézőjel nélkül az eredeti SQL-szövegben (a Formsban hibás SQL)')
                continue
        if got != want:
            report['difference'] = {'case': {k: (str(v) if v is not None else None) for k, v in case.items()},
                                    'plsql': want, 'java': got}
            break
        report['checked'] += 1
    return report


def inline_raw(sql: str, params: dict) -> str:
    """inline(), but an empty value as nothing (the Forms code pasted it as written)."""
    pieces = sql.split("'")
    for index in range(0, len(pieces), 2):
        pieces[index] = re.sub(r':([A-Za-z][\w$#]*)', lambda m: ('' if params.get(m.group(1)) == '' else sql_literal(params[m.group(1)]))
                               if m.group(1) in params else m.group(0), pieces[index])
    return "'".join(pieces)


def describe_difference(difference: dict) -> str:
    plsql, java = difference['plsql'], difference['java']
    field = next(k for k in ('run', 'where', 'order', 'messages') if plsql[k] != java[k])
    case = ', '.join(f"{k}={v!r}" for k, v in difference['case'].items()) or 'üres képernyő'
    label = {'run': 'lekérdezés fut', 'where': 'WHERE', 'order': 'ORDER BY', 'messages': 'üzenetek'}[field]
    return f"eset: {case}; {label} - PL/SQL: {plsql[field]!r}, Java: {java[field]!r}"


# ---------------------------------------------------------------------- the plan

def plan(trigger: dict, source: str, model: dict, catalog: dict) -> dict:
    """The Java query of a button, or Unsupported (the PL/SQL query adapter is the fallback)."""
    from .messages import simplify
    from .plsql_passthrough import local_units
    from .query_actions import message_wrapper
    from .rules import strip_framework
    if not model['blocks']:
        raise Unsupported('Nincs leképezett adatblokk.')
    units = local_units(model)
    messages_catalog = catalog.get('messages')
    text, notes = simplify(decode_line_escapes(source), messages_catalog)
    nodes, _ = strip_framework(parse(text, extended=True), catalog)
    builder = Builder(model, trigger, units, messages_catalog)
    # the catalogued message routines (WUZENET ...) are MESSAGE calls by now: still named, their code in the region
    builder.called += [n.split(' -> ')[0] for n in notes if n.endswith(' -> MESSAGE') and ' ' not in n.split(' -> ')[0]]
    builder.ast = builder.expand(nodes)
    builder.declare_all(builder.ast)
    builder.ir = builder.statements(builder.ast)
    builder.final_checks()
    if not any(statement_has(builder.ir, 'where')):
        raise Unsupported('A gomb nem hív szűrőépítő eljárást és nem állít szűrőt (SET_BLOCK_PROPERTY DEFAULT_WHERE).')
    if not builder.executed or builder.target is None:  # EXECUTE_QUERY in the builder or after it, in the trigger
        raise Unsupported('A gomb nem futtat lekérdezést (SET_BLOCK_PROPERTY DEFAULT_WHERE + EXECUTE_QUERY).')
    block = next((b for b in model['blocks'] if b['name'] == builder.target), None)
    if not block or not block['database'] or not block['db_items'] or block.get('backend_skip'):
        raise Unsupported('A lekérdezés célja nem leképezett, lekérdezhető adatblokk: ' + str(builder.target))
    report = equivalence(builder)
    if report['difference']:
        raise Unsupported('A Java WHERE eltér az eredeti PL/SQL-étől (' + describe_difference(report['difference']) + ').')
    unit = next((u for u in builder.expanded if procedure_sets_where(builder.ast, u)), None)
    binds = list(dict.fromkeys(builder.binds))
    text_value = lambda v: None if v is None else oracle_text(v) if isinstance(v, Decimal) else str(v)
    return {'target': builder.target, 'unit': unit or trigger.get('owner') or '', 'inline': unit is None,
            'expanded': builder.expanded, 'statements': java_statements(builder.ir, '        ', binds),
            'locals': [{'java': v.java, 'type': v.type, 'role': v.role} for v in builder.vars.values() if v.constant is None],
            'reads': builder.reads, 'binds': {b: builder.binds[b] for b in binds}, 'inputs': builder.inputs,
            'order': builder.order_static, 'dynamic_order': any(statement_has(builder.ir, 'order')) and builder.order_static is None,
            'called': builder.called, 'left_out': builder.left_out,
            'equivalence': {k: report[k] for k in ('cases', 'checked', 'unverifiable')}
                           | ({'unverifiable_reason': report['unverifiable_reason']} if report.get('unverifiable_reason') else {}),
            'tests': [{'case': {k: text_value(v) for k, v in t['case'].items()}, 'run': t['expected']['run'],
                       'where': t['expected']['where'], 'order': t['expected']['order'], 'messages': t['expected']['messages'],
                       'params': {k: text_value(v) for k, v in t['expected']['params'].items()}} for t in report['tests'][:32]],
            # shown as a message, although they do more (a log table ...): their code is in the region, a TODO says so
            'more_than_message': [n for n in builder.called if n in units and not message_wrapper(units[n]['text'])]}


def statement_has(statements: list, kind: str):
    for statement in statements:
        if statement[0] == kind:
            yield True
        elif statement[0] == 'if':
            for _, body in statement[1]:
                yield from statement_has(body, kind)
            yield from statement_has(statement[2], kind)


def procedure_sets_where(nodes: list, unit: str) -> bool:
    def walk(body, inside):
        for node in body:
            if node['op'] == 'block':
                if walk(node['body'], inside or node.get('unit') == unit):
                    return True
            elif node['op'] == 'if':
                if any(walk(b['body'], inside) for b in node['branches']) or walk(node['else'], inside):
                    return True
            elif (inside and node['op'] == 'call' and node['name'] == 'SET_BLOCK_PROPERTY' and len(node['args']) == 3
                  and node['args'][1].get('name') in {'DEFAULT_WHERE', 'ONETIME_WHERE'}):
                return True
        return False
    return walk(nodes, False)


def region(title: str, parts: list[tuple[str, str]], indent: str) -> str:
    """The original Forms code as a comment between //region and //endregion (foldable in the IDE)."""
    lines = [indent + '//region ' + title]
    for heading, code in parts:
        lines.append(indent + '// ' + heading)
        lines += [indent + ('// ' + line.replace('\\', '[backslash]') if line.strip() else '//') for line in code.rstrip().splitlines()]
        lines.append(indent + '//')
    return '\n'.join(lines[:-1] + [indent + '//endregion']) + '\n'


QUERY_TEXT_CLASS = '''    /**
     * A Java lekérdezőgombok SQL-feltétele: a WHERE és a rendezés, ahogy a Forms-kód összeállította, a kötött értékek
     * és az üzenetek. run: a Forms-kód eljutott az EXECUTE_QUERY-ig.
     */
    static final class QueryText {
        boolean run;
        String where;
        String orderBy;
        final MapSqlParameterSource params = new MapSqlParameterSource();
        final java.util.List<String> messages = new java.util.ArrayList<>();
    }

    /** A Forms DEFAULT_WHERE szövege a SELECT WHERE-je után: a WHERE kulcsszó nélkül, zárójelben; üresen minden sor. */
    static String whereText(String where) {
        String text = where == null ? "" : where.strip().replaceFirst("(?i)^where\\\\s+", "");
        return text.isEmpty() ? "1 = 1" : "(" + text + ")";
    }'''


def query_method_name(operation: dict) -> str:
    return operation['method'] + 'Query'


def text_method(operation: dict, query: dict) -> str:
    """The static <method>Query: the WHERE / ORDER BY / binds / messages of the button, from the screen's values."""
    params = ', '.join(f"{JAVA_TYPES[r['type']]} {r['variable']}" for r in query['reads'].values())
    lines = ['    // ' + operation['action']['owner'] + ': a lekérdezés feltétele, ahogy a Forms-kód összeállította '
             '(a generált teszt is ezt hívja).',
             f"    static QueryText {query_method_name(operation)}({params}) {{", '        var q = new QueryText();']
    for local in query['locals']:
        initial = '""' if local['role'] == 'sql' else 'null'
        lines.append(f"        {JAVA_TYPES[local['type']]} {local['java']} = {initial};")
    lines += query['statements'] + ['        return q;', '    }']
    return '\n'.join(lines)


def method(operation: dict, block: dict, gated: bool, log1x, user_type: str, support, model: dict, discovery: dict) -> str:
    """The generated DPS methods of a Java query button: the endpoint and its static <method>Query."""
    from .action_scaffold import comment_lines
    from .backend_queries import QueryCompiler, RawClause, clause_body
    from .generate import column_sql, table_alias
    from .plsql_passthrough import local_units
    from .rules import JDBC_TYPES
    from .service_inline import support_methods
    from .xmlmodel import get
    query = operation['query_action']['java']
    trigger = next(t for t in model['triggers'] if t['owner'] == operation['action']['owner']
                   and t['event'] == operation['action']['event'])
    units = local_units(model)
    # the original code: the trigger, the builder, the message procedures, the Forms code the query leaves out
    parts = [(trigger['owner'] + ' / ' + trigger['event'] + ':', decode_line_escapes(trigger['source']))]
    shown = list(dict.fromkeys(n for n in [*query.get('expanded', [query['unit']]), *query['called']] if n in units))
    parts += [(name_ + ' (helyi eljárás' + (', üzenetként jelenik meg' if name_ in query['called'] else '') + '):',
               decode_line_escapes(units[name_]['text'])) for name_ in shown]
    todo = operation.get('query_blockers') or []
    blocking = [t for t in model['triggers'] if t['status'] == 'review' and any(d.startswith(t['id'] + ':') for d in todo)]
    parts += [(t['id'] + ' (nem fordult le; a migrált lekérdezés nem futtatja):', decode_line_escapes(t['source'])) for t in blocking]
    original = region('Eredeti Forms-kód: ' + ', '.join([trigger['owner'], *shown]), parts, '        ')
    compiler = QueryCompiler(model, block)
    relation = compiler.predicate(block.get('relation_where', ''))
    order_source = query['order'] or clause_body(get(block['properties'], 'OrderByClause'), 'ORDER', 'BY')
    try:
        order = compiler.order(order_source)
    except Unsupported:
        order = RawClause(model, block, compiler.binds).sql(order_source)
    order = order or ', '.join(i['column'] for i in block['pk']) or block['db_items'][0]['column']
    select = ('SELECT ' + ', '.join(column_sql(i) for i in block['db_items']) + ' FROM ' + block['table']
              + table_alias(block, block['table']) + ' WHERE ')
    reads = query['reads']
    from . import java_variables
    given = {source: java_variables.lookup(read['variable']) for source, read in reads.items()}  # java-variables.json
    values = any('parameters' not in r and 'input' not in r and not given[s] for s, r in reads.items()) or bool(compiler.binds)
    head = []
    if values:
        head.append('            var values = request.blocks() == null ? java.util.Map.<String, java.util.Map<String, String>>of() : request.blocks();')
    if any('parameters' in r and not given[s] for s, r in reads.items()):
        head.append('            var parameters = request.parameters() == null ? java.util.Map.<String, String>of() : request.parameters();')
    for source, read in reads.items():
        if given[source]:
            head.append(f"            // :{source}: az érték a java-variables.json-ból.")
            head.append('            ' + java_variables.declaration(JAVA_TYPES[read['type']], read['variable'], given[source]))
            continue
        if 'input' in read:
            head.append(f"            // TODO: :{source} ({read['input']}): add át ennek a változónak a megfelelő értéket.")
        head.append(f"            {JAVA_TYPES[read['type']]} {read['variable']} = {read['java']};")
    params = []
    for bind in compiler.binds.values():  # the master-detail relation
        owner, item = bind['source'].split('.', 1)
        value = f"PlsqlValues.{READERS[bind['item']['type']]}(values, {jstr(owner)}, {jstr(item)})"
        params.append(f"            q.params.addValue({jstr(bind['parameter'])}, {value}, {JDBC_TYPES[bind['item']['type']]});")
    params.append('            q.params.addValue("offset", request.offset()).addValue("limit", request.limit());')
    suffix = name(block['class'])
    suffix = suffix[:1].upper() + suffix[1:]
    post = support_methods('\n'.join(support)).get('postQuery' + suffix)
    post_call = ''
    if post:
        args = 'row, context' if 'RuleContext' in post['header'] else 'row'
        post_call = f'''            var context = new RuleContext();
            for (var row : rows) {{
                postQuery{suffix}({args});
            }}
            q.messages.addAll(context.messages());
'''
    guard = ''
    if gated:
        guard = '''            if (!MODULE_REVIEWED) {
                throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, "Ez a művelet ebben a modulban még nem érhető el.");
            }
'''
    info = [operation['action']['owner'] + ': lekérdezés a(z) ' + query['target'] + ' blokkra. A WHERE feltételt '
            + ('a trigger saját kódja' if query.get('inline') else 'a(z) ' + query['unit']) + ' alapján a(z) '
            + query_method_name(operation) + ' állítja össze a képernyő értékeiből, egy JDBC-lekérdezés fut.']
    equivalence = query.get('equivalence') or {}
    if equivalence.get('checked'):
        info.append('Egyezés-ellenőrzés: ' + str(equivalence['checked']) + ' bemeneti esetben a Java WHERE megegyezik az eredeti '
                    'PL/SQL-ével' + (' (további ' + str(equivalence['unverifiable']) + ' eset Pythonban nem ellenőrizhető)'
                                    if equivalence.get('unverifiable') else '') + '.')
    if query['left_out']:
        info.append('Kimaradt Forms-hívások (a webes lekérdezésnek nem kellenek): ' + ', '.join(query['left_out']) + '.')
    waiting = [i for i in query['inputs'] if not java_variables.lookup(i['variable'])]
    if waiting:
        info.append('Fejlesztői bemenet (a metódus elején, null; TODO): '
                    + ', '.join(':' + i['source'] + ' -> ' + i['variable'] for i in waiting) + '.')
    configured = [':' + source + ' -> ' + read['variable'] for source, read in reads.items() if given[source]]
    if configured:
        info.append('Érték a java-variables.json-ból: ' + ', '.join(configured) + '.')
    for unit in query.get('more_than_message', []):
        info.append('TODO: a(z) ' + unit + ' itt csak üzenet, de a Formsban mást is csinál (az eredeti kódja a regionban).')
    for detail in todo:
        info.append('TODO: a Formsban ez is fut, a migrált lekérdezés nem: ' + detail)
    order_java = (jstr(' ORDER BY ' + order) if not query.get('dynamic_order') else
                  f'(q.orderBy == null || q.orderBy.isBlank() ? {jstr(" ORDER BY " + order)} : " ORDER BY " + q.orderBy)')
    tail = ((jstr(' AND (' + relation + ')') + ' + ') if relation else '') + order_java + ' + " OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY"'
    call_args = ', '.join(r['variable'] for r in reads.values())
    return comment_lines('\n'.join(info), '    ') + f'''
    @org.springframework.transaction.annotation.Transactional(rollbackFor = Exception.class)
    @Override
    public {operation['returns']} {operation['method']}({user_type} user, QueryActionRequest request) throws Exception {{
{original}        {log1x(operation, '() -> {')}
            if (request == null) {{
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Hiányzó kérés.");
            }}
{guard}            if (request.offset() < 0 || request.offset() > 1000000 || request.limit() < 1 || request.limit() > 200) {{
                throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "offset: 0..1000000, limit: 1..200 szükséges.");
            }}
{chr(10).join(head)}
            var q = {query_method_name(operation)}({call_args});
            if (!q.run) {{
                return new PageResult<>(null, q.messages);
            }}
{chr(10).join(params)}
            var rows = jdbc.query({jstr(select)} + whereText(q.where) + {tail}, q.params, (rs, rowNum) -> map{suffix}(rs));
{post_call}            return new PageResult<>(rows, q.messages);
        }});
    }}

''' + text_method(operation, query)


def java_literal(value, typ: str) -> str:
    if value is None:
        return 'null'
    if typ == 'number':
        return 'new java.math.BigDecimal(' + jstr(str(value)) + ')'
    return jstr(str(value))


def junit_test(cls: str, package: str, operations: list) -> str:
    """backend/DPS-test/<Module>QueryTextTest.java: JUnit 5 cases of the Java query buttons' <method>Query.

    The expected WHERE / ORDER BY / binds / messages are the ones the generator computed for the equivalence check,
    where the original PL/SQL gave the same result on the same screen values: the test proves the generated Java
    builds that text (and keeps proving it when the developer edits the method).
    """
    methods = []
    for operation in operations:
        query = operation['query_action']['java']
        reads = list(query['reads'].items())
        lines = [f"    @Test", f"    void {operation['method']}() {{"]
        for test in query.get('tests', []):
            args = ', '.join(java_literal(test['case'].get(source), read['type']) for source, read in reads)
            types = {b: next((r['type'] for s, r in reads if r['variable'] == b), None) for b in test['params']}
            local_types = {l['java']: l['type'] for l in query.get('locals', [])}
            params = ''.join(', ' + jstr(b) + ', ' + java_literal(v, types.get(b) or local_types.get(b, 'text'))
                             for b, v in test['params'].items())
            messages = 'java.util.List.of(' + ', '.join(jstr(m) for m in test['messages']) + ')'
            lines.append(f"        check({cls}ServiceImpl.{query_method_name(operation)}({args}), {str(test['run']).lower()}, "
                         f"{jstr(test['where']) if test['where'] is not None else 'null'}, "
                         f"{jstr(test['order']) if test['order'] is not None else 'null'}, {messages}{params});")
        lines.append('    }')
        methods.append('\n'.join(lines))
    return f'''package {package};

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

/**
 * Generált teszt (query_java_tests): a Java lekérdezőgombok WHERE-je, rendezése, kötött értékei és üzenetei a
 * képernyő értékeiből. Az elvárt értékeket a migrátor számolta, és mindegyik esetben egyeztek az eredeti PL/SQL-kód
 * kiértékelésével (egyezés-ellenőrzés). Helye: a DPS modul src/test/java mappája, a ServiceImpl csomagjában.
 */
class {cls}QueryTextTest {{
{(chr(10) + chr(10)).join(methods)}

    private static void check({cls}ServiceImpl.QueryText q, boolean run, String where, String orderBy,
                              java.util.List<String> messages, Object... params) {{
        assertEquals(run, q.run, "run");
        assertEquals(messages, q.messages, "messages");
        if (!run) {{
            return;
        }}
        assertEquals(where, q.where, "where");
        if (orderBy != null) {{
            assertEquals(orderBy, q.orderBy, "orderBy");
        }}
        for (int i = 0; i < params.length; i += 2) {{
            assertEquals(params[i + 1], q.params.getValue((String) params[i]), (String) params[i]);
        }}
    }}
}}
'''

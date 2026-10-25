"""Adapt bounded, local DEFAULT_WHERE builders without executing Forms built-ins.

The original PL/SQL chooses the predicate in Oracle. Only predicates compiled
from the form's constant strings may be used by the following JDBC SELECT.
Request values are typed binds, never fragments of SQL.
"""
from __future__ import annotations

from decimal import Decimal

from . import forms_runtime
from .backend_queries import QueryCompiler
from .common import decode_line_escapes
from .forms_context import normal_mode_expression
from .plsql import Parser, Unsupported, flatten, parse
from .plsql_passthrough import items_by_block, local_units, prepare
from .xmlmodel import get


MAX_VARIANTS = 64


class NativeCondition:
    """Validate a pure PL/SQL guard; Oracle evaluates it with its own types.

    These expressions are never emitted as JDBC SQL or evaluated in Java.
    In particular, CHAR checkbox values may be compared to numeric literals
    without changing the source or the VARCHAR2 bind's type.
    """

    def __init__(self, model):
        self.fields = {b['name'] + '.' + i['name']: i for b in model['blocks']
                       for i in b['items'] if i['kind'] != 'button'}
        self.units = local_units(model)

    def expression(self, node):
        op = node['op']
        if normal_mode_expression(node) and (op != 'function' or 'NAME_IN' not in self.units):
            return 'value'
        if op == 'literal':
            return 'boolean' if node['type'] == 'boolean' else 'value'
        if op == 'ref':
            field = self.fields.get(node['name'])
            if not field or field['type'] not in {'text', 'number', 'datetime'}:
                raise Unsupported('Nem igazolt PL/SQL-feltétel bindje: :' + node['name'])
            return 'value'
        if (op == 'symbol' and node['name'] in {'SYSDATE', 'SYSTIMESTAMP', 'CURRENT_DATE', 'CURRENT_TIMESTAMP'}
                and node['name'] not in self.units):
            return 'value'
        if op == 'is_null':
            self.expression(node['value'])
            return 'boolean'
        if op == 'unary':
            kind = self.expression(node['value'])
            if node['operator'] == 'NOT' and kind == 'boolean':
                return 'boolean'
            if node['operator'] in {'+', '-'} and kind == 'value':
                return 'value'
        if op == 'binary':
            left = self.expression(node['left'])
            right = self.expression(node['right'])
            operator = node['operator']
            if operator in {'AND', 'OR'} and left == right == 'boolean':
                return 'boolean'
            if operator in {'=', '<>', '!='} and left == right:
                return 'boolean'
            if operator in {'<', '>', '<=', '>='} and left == right == 'value':
                return 'boolean'
            if operator in {'+', '-', '*', '/', '||'} and left == right == 'value':
                return 'value'
        if op == 'function':
            function = node['name']
            if (function in forms_runtime.PURE_FUNCTIONS and function not in self.units
                    and not forms_runtime.builtin(function)
                    and all(self.expression(arg) == 'value' for arg in node['args'])):
                return 'value'
            raise Unsupported('Nem igazolt tiszta PL/SQL-függvény a feltételben: ' + function)
        raise Unsupported('Nem támogatott PL/SQL-feltétel: ' + str(node.get('name', op)))

    def condition(self, node):
        if self.expression(node) != 'boolean':
            raise Unsupported('A lekérdezés-adapter IF feltétele nem logikai.')


def literal(node):
    if node['op'] != 'literal' or node['type'] != 'text' or node['value'] is None:
        raise Unsupported('A lekérdezés-adapter itt állandó szöveget igényel.')
    return node['value']


class BuilderParser(Parser):
    """One VARCHAR2 variable, assignments, IFs, and the quote replacement."""

    def statement(self):
        start = self.tokens[self.i].pos
        if (self.tokens[self.i].kind == 'id'
                and self.tokens[self.i + 1].value == ':='):
            variable = self.take().value
            self.need(':=')
            node = {'op': 'local_assign', 'variable': variable, 'value': self.expression()}
            self.need(';')
        elif self.accept('SELECT'):
            node = {'op': 'quote_replace', 'value': self.expression()}
            self.need('INTO')
            node['variable'] = self.take().value
            self.need('FROM')
            self.need('DUAL')
            self.need(';')
        else:
            node = super().statement()
        node['span'] = [start, self.tokens[self.i - 1].pos + 1]
        return node


def procedure(source, expected, model=None):
    source = decode_line_escapes(source)
    parser = BuilderParser(source)
    parser.need('PROCEDURE')
    parser.need(expected)
    if parser.accept('('):
        parser.need(')')
    if not (parser.accept('IS') or parser.accept('AS')):
        raise Unsupported('A szűrőépítő eljárás IS/AS deklarációt igényel.')
    token = parser.take()
    if token.kind != 'id':
        raise Unsupported('A szűrőépítő egy helyi VARCHAR2 változót igényel.')
    variable = token.value
    parser.need('VARCHAR2')
    parser.need('(')
    size = parser.take()
    if size.kind != 'number' or not size.value.isdigit() or not 1 <= int(size.value) <= 32767:
        raise Unsupported('A szűrőváltozó mérete 1..32767 lehet.')
    parser.need(')')
    parser.need(';')
    parser.need('BEGIN')
    body = parser.statements({'END', '<EOF>'})
    parser.need('END')
    parser.accept(expected)
    parser.need(';')
    parser.need('<EOF>')
    if len(body) < 4:
        raise Unsupported('A szűrőépítő eljárásból hiányzik a lekérdezési lépéssor.')
    prop, go, execute = body[-3:]
    if (prop['op'] != 'call' or prop['name'] != 'SET_BLOCK_PROPERTY' or len(prop['args']) != 3
            or prop['args'][1] != {'op': 'symbol', 'name': 'DEFAULT_WHERE'}
            or prop['args'][2] != {'op': 'symbol', 'name': variable}):
        raise Unsupported('A szűrőépítő végén SET_BLOCK_PROPERTY(blokk, DEFAULT_WHERE, változó) szükséges.')
    target = literal(prop['args'][0]).upper()
    if (go['op'] != 'call' or go['name'] != 'GO_BLOCK' or len(go['args']) != 1
            or literal(go['args'][0]).upper() != target):
        raise Unsupported('A DEFAULT_WHERE után ugyanazon blokk GO_BLOCK + EXECUTE_QUERY hívása szükséges.')
    direct_query = execute['op'] == 'call' and execute['name'] == 'EXECUTE_QUERY' and not execute['args']
    key_query = (execute['op'] == 'call' and execute['name'] == 'DO_KEY' and len(execute['args']) == 1
                 and literal(execute['args'][0]).upper() == 'EXECUTE_QUERY')
    if not direct_query and not key_query:
        raise Unsupported('A DEFAULT_WHERE után EXECUTE_QUERY vagy DO_KEY(\'EXECUTE_QUERY\') szükséges.')
    if key_query:
        from .forms_keys import overrides
        if model is None or 'DO_KEY' in local_units(model):
            raise Unsupported('A DO_KEY lekérdezési eseménylánca nem igazolt.')
        keys = overrides(model, 'EXECUTE_QUERY', target)
        if keys:
            raise Unsupported('A DO_KEY(\'EXECUTE_QUERY\') saját KEY-EXEQRY triggert indít: '
                              + ', '.join(t['id'] for t in keys) + '; ennek logikájához eseményadapter kell.')
    return source, variable, target, body[:-3], body[-3:]


def string_expression(node, variable, current):
    if node['op'] == 'literal':
        return literal(node)
    if node == {'op': 'symbol', 'name': variable}:
        return current or ''  # Oracle concatenation with NULL
    if node['op'] == 'binary' and node['operator'] == '||':
        return (string_expression(node['left'], variable, current)
                + string_expression(node['right'], variable, current))
    raise Unsupported('A DEFAULT_WHERE csak állandó SQL-szövegből építhető; mezőértékhez bind szükséges.')


def number_requirements(condition, compiler):
    """Necessary numeric equalities when an IF is true (conservative).

    This eliminates impossible combinations such as flag=0 AND flag=1 without
    evaluating business conditions in Java or changing Oracle's NULL semantics.
    Oracle's CHAR-to-number comparison cannot be true for two different numbers
    either; the original PL/SQL retains conversion errors and NLS behavior.
    """
    if condition['op'] == 'binary' and condition['operator'] == 'AND':
        left = number_requirements(condition['left'], compiler)
        right = number_requirements(condition['right'], compiler)
        if left is None or right is None or any(k in left and left[k] != v for k, v in right.items()):
            return None
        return {**left, **right}
    if condition['op'] == 'binary' and condition['operator'] == '=':
        for ref, value in ((condition['left'], condition['right']), (condition['right'], condition['left'])):
            if (ref['op'] == 'ref' and compiler.fields.get(ref['name'], {}).get('type') in {'number', 'text'}
                    and value['op'] == 'literal' and value['type'] == 'number'):
                number = Decimal(value['value'])
                # Only exact Oracle NUMBER literals justify pruning a path.
                # High precision/range values can round or underflow in Oracle.
                if len(number.as_tuple().digits) <= 38 and (number == 0 or -130 <= number.adjusted() < 126):
                    return {ref['name']: number}
    return {}


def variant_states(body, variable, compiler, states):
    for node in body:
        op = node['op']
        if op == 'local_assign' and node['variable'] == variable:
            states = {(string_expression(node['value'], variable, value), guards) for value, guards in states}
        elif op == 'quote_replace' and node['variable'] == variable:
            expr = node['value']
            if (expr['op'] != 'function' or expr['name'] != 'REPLACE' or len(expr['args']) != 3
                    or expr['args'][0] != {'op': 'symbol', 'name': variable}
                    or expr['args'][2] != {'op': 'function', 'name': 'CHR',
                                         'args': [{'op': 'literal', 'type': 'number', 'value': '39'}]}):
                raise Unsupported('Csak REPLACE(szűrőváltozó, állandó jel, CHR(39)) fordítható.')
            if 'REPLACE' in compiler.units or 'CHR' in compiler.units:
                raise Unsupported('A helyi REPLACE/CHR saját logikája nem helyettesíthető állandó szövegcserével.')
            separator = literal(expr['args'][1])
            states = {(None if value is None else value.replace(separator, "'"), guards) for value, guards in states}
        elif op == 'if':
            choices = set()
            for branch in node['branches']:
                compiler.condition(branch['condition'])
                required = number_requirements(branch['condition'], compiler)
                compatible = set()
                if required is not None:
                    for value, guards in states:
                        known = dict(guards)
                        if all(k not in known or known[k] == v for k, v in required.items()):
                            compatible.add((value, frozenset({**known, **required}.items())))
                choices.update(variant_states(branch['body'], variable, compiler, compatible))
            choices.update(variant_states(node['else'], variable, compiler, states))
            states = choices
        elif op == 'noop':
            continue
        else:
            raise Unsupported('Nem támogatott szűrőépítő utasítás: ' + op)
        if len(states) > MAX_VARIANTS or any(v is not None and len(v) > 32767 for v, _ in states):
            raise Unsupported('Túl sok vagy túl hosszú DEFAULT_WHERE változat.')
    return states


def variants(body, variable, compiler):
    return {value for value, _ in variant_states(body, variable, compiler, {(None, frozenset())})}


def message_wrapper(text: str) -> bool:
    """A local procedure that only shows its one text parameter (MESSAGE or an alert dialog with that text).

    Accepted: PROCEDURE x(p [IN] VARCHAR2 [DEFAULT '...']) IS [declarations] BEGIN ... END, where the declarations
    are NUMBER / INTEGER / PLS_INTEGER / BOOLEAN / ALERT / VARCHAR2(n) / CHAR(n) variables or constants with a literal
    (the alert's name), and the statements show the text: MESSAGE(p [, ACKNOWLEDGE]) | SET_ALERT_PROPERTY(.., ALERT_MESSAGE_TEXT, p)
    | SET_ALERT_PROPERTY(.., TITLE, ..) | CHANGE_ALERT_MESSAGE(.., p) | SET_ALERT_BUTTON_PROPERTY(..) | v := FIND_ALERT(..)
    | v := SHOW_ALERT(..) | v := 'literal' | SYNCHRONIZE | BELL | NULL, also inside IF [NOT] ID_NULL(v) ... ELSE ... END IF
    (the alert is missing: MESSAGE), [RAISE FORM_TRIGGER_FAILURE] last. Anything else - a log table, another call, a
    condition on the pressed button - is real logic and stays a manual task.
    """
    import re
    from .plsql_passthrough import scan
    code = re.sub(r'&#(?:10|13|9);', '\n', str(text or ''))
    try:
        # comments out, string literals masked: their words never count as code
        code = ''.join(' ' if kind == 'comment' else "'x'" if kind == 'string' else token for kind, token, _, _ in scan(code))
    except Exception:
        return False
    header = re.match(r"\s*procedure\s+[\w$#]+\s*\(\s*([\w$#]+)\s+(?:in\s+)?varchar2\s*(?:(?::=|default)\s*'x'\s*)?\)\s*(?:is|as)\b(.*?)\bbegin\b(.*)\bend\b\s*[\w$#]*\s*;?\s*$",
                      code, re.I | re.S)
    if not header:
        return False
    p = re.escape(header.group(1))
    variables = set()
    for declaration in [d.strip() for d in header.group(2).split(';') if d.strip()]:
        match = re.fullmatch(r"([\w$#]+)\s+(?:constant\s+)?(?:number|integer|pls_integer|boolean|alert|(?:varchar2|char)\s*\(\s*\d+\s*(?:byte|char)?\s*\))"
                             r"(?:\s+not\s+null)?(?:\s*(?::=|default)\s*(?:'x'|-?\d+|true|false|null))?", declaration, re.I | re.S)
        if not match:
            return False
        variables.add(match.group(1).upper())
    # IF [NOT] ID_NULL(alert) ... ELSE ... END IF: either branch shows the text (dialog or MESSAGE)
    body = re.sub(r'\b(?:els)?if\s+(?:not\s+)?id_null\s*\(\s*[\w$#]+\s*\)\s+then\b|\belse\b|\bend\s+if\b', ';', header.group(3), flags=re.I)
    statements = [re.sub(r'\s+', ' ', s).strip() for s in body.split(';') if s.strip()]
    target = r"(?:[\w$#]+|'x')"
    allowed = [rf"message\s*\(\s*{p}\s*(?:,\s*(?:no_)?acknowledge\s*)?\)",
               rf"set_alert_property\s*\(\s*{target}\s*,\s*alert_message_text\s*,\s*{p}\s*\)",
               rf"set_alert_property\s*\(\s*{target}\s*,\s*title\s*,\s*{target}\s*\)",
               rf"change_alert_message\s*\(\s*{target}\s*,\s*{p}\s*\)",
               r"([\w$#]+)\s*:=\s*(?:(?:show_alert|find_alert)\s*\(\s*" + target + r"\s*\)|'x'|-?\d+)",
               r"set_alert_button_property\s*\([^)]*\)", r"synchronize", r"bell", r"null"]
    shown = False
    for index, statement in enumerate(statements):
        if re.fullmatch(r"raise\s+form_trigger_failure", statement, re.I) and index == len(statements) - 1:
            continue  # the branch ends here anyway
        match = next((m for m in (re.fullmatch(rx, statement, re.I) for rx in allowed) if m), None)
        if not match or (match.groups() and match.group(1).upper() not in variables):
            return False  # an assignment writes the procedure's own variables only
        shown = shown or bool(re.match(r'(?:message|set_alert_property\s*\([^,]+,\s*alert_message_text|change_alert_message)', statement, re.I))
    return shown


def validate_trigger(nodes, units, catalog, calls, compiler):
    from .rules import strip_framework
    nodes, _ = strip_framework(nodes, catalog)
    for node in flatten(nodes):
        if node['op'] == 'if':
            for branch in node['branches']:
                compiler.condition(branch['condition'])
                validate_trigger(branch['body'], units, catalog, calls, compiler)
            validate_trigger(node['else'], units, catalog, calls, compiler)
        elif node['op'] == 'call' and node['name'] in units and not node['args']:
            calls.append(node['name'])
        elif node['op'] == 'call' and (node['name'] in {'MESSAGE', 'WUZENET'}
                                       or node['name'] in units and message_wrapper(units[node['name']]['text'])):
            if len(node['args']) != 1:
                raise Unsupported('A lekérdezésgomb üzenete egy szöveges argumentumot igényel.')
            literal(node['args'][0])
            # A local routine counts as a message only if it does nothing but show its text.
            if node['name'] in units and not message_wrapper(units[node['name']]['text']):
                raise Unsupported('A helyi ' + node['name'] + ' saját logikáját külön üzenet-adapterrel kell átültetni '
                                  '(nem csak megjeleníti a szöveget).')
        elif node['op'] != 'noop':
            raise Unsupported('A lekérdezésgomb nem támogatott további műveletet tartalmaz.')


def query_action(trigger, source, model, catalog):
    if not model['blocks']:
        raise Unsupported('A lekérdezésgombhoz nincs leképezett adatblokk.')
    units = local_units(model)
    calls = []
    conditions = NativeCondition(model)
    validate_trigger(parse(source), units, catalog, calls, conditions)
    if len(calls) != 1:
        raise Unsupported('A lekérdezésgomb egy helyi szűrőépítő eljárást hívhat.')
    unit = calls[0]
    original, variable, target, body, tail = procedure(units[unit]['text'], unit, model)
    if any(node['name'] in units for node in tail):
        raise Unsupported('A lekérdezési lépéssor Forms built-in neve helyi eljárást takar; ennek saját logikájához adapter kell.')
    block = next((b for b in model['blocks'] if b['name'] == target), None)
    if not block or not block['database'] or not block['query_allowed'] or not block['db_items'] or block.get('backend_skip'):
        raise Unsupported('A lekérdezés célja nem leképezett, lekérdezhető adatblokk: ' + target)
    possible = variants(body, variable, conditions)
    if None in possible or not possible:
        raise Unsupported('A DEFAULT_WHERE nem minden ágban kap szűrőfeltételt.')
    plans = []
    for predicate in sorted(possible):
        compiler = QueryCompiler(model, block)
        sql = compiler.predicate(predicate)
        if not sql:
            raise Unsupported('Üres DEFAULT_WHERE: a szűrőépítőt külön át kell tekinteni.')
        relation = compiler.predicate(block.get('relation_where', ''))
        if relation:
            sql = '(' + sql + ') AND (' + relation + ')'
        from .backend_queries import RawClause, clause_body
        order_source = clause_body(get(block['properties'], 'OrderByClause'), 'ORDER', 'BY')
        try:
            order = compiler.order(order_source)
        except Unsupported:
            order = RawClause(model, block, compiler.binds).sql(order_source)  # the original ORDER BY, in Oracle
        order = order or ', '.join(i['column'] for i in block['pk']) or block['db_items'][0]['column']
        from .generate import column_sql, table_alias
        select = ('SELECT ' + ', '.join(column_sql(i) for i in block['db_items']) + ' FROM ' + block['table']
                  + table_alias(block, block['table'])
                  + ' WHERE ' + sql + ' ORDER BY ' + order + ' OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY')
        plans.append({'predicate': predicate, 'sql': select, 'binds': list(compiler.binds.values())})
    items = items_by_block(model)
    context = 'FRM_QUERY_CONTEXT'
    while context in items:
        context += '_X'
    items[context] = {n: {'type': 'text'} for n in ('WHERE_TEXT', 'EXECUTED')}
    adapted = original
    replacements = [f':{context}.WHERE_TEXT := {variable};', 'NULL;', f":{context}.EXECUTED := 'Y';"]
    for node, replacement in reversed(list(zip(tail, replacements))):
        start, end = node['span']
        adapted = adapted[:start] + replacement + adapted[end:]
    units = {**units, unit: {**units[unit], 'text': adapted}}
    # Only the recognized query-button's literal warning is an Angular message.
    # Never rewrite a user-defined routine or a name inside SQL/comments.
    from .plsql_passthrough import scan
    source = decode_line_escapes(source)
    tokens = scan(source)
    shown = {'WUZENET'} | {name for name, unit in units.items() if message_wrapper(unit['text'])}
    replaced = set()
    for kind, text, start, end in reversed(tokens):
        if kind == 'ident' and text.upper() in shown:
            source = source[:start] + 'MESSAGE' + source[end:]
            replaced.add(text.upper())
    prepared = prepare(source, block=trigger['block'] or None, items=items, units=units,
                       prefixes=catalog['call_prefixes'], other_blocks=True, parameters=True,
                       transaction=True, procedures=model.get('procedures', {}),
                       runtime_calls=catalog.get('runtime_calls', ()))
    if prepared['unresolved'] or set(prepared['assigned']) - {context + '.WHERE_TEXT', context + '.EXECUTED'}:
        raise Unsupported('A lekérdezésgomb ismeretlen rutint hív vagy a szűrőn túl képernyőértéket módosít.')
    return {'target': target, 'unit': unit, 'context': context, 'variants': plans, 'prepared': prepared,
            'runtime_call': original[tail[0]['span'][0]:tail[0]['span'][1]],
            # the local alert/message procedures shown as a message: their code stays in the method as a comment
            'message_units': {name: decode_line_escapes(units[name]['text']).strip() for name in sorted(replaced) if name in units}}


def blockers(plan, model):
    """Resolve only this builder's DEFAULT_WHERE; retain every other read blocker.

    A Java query button sets the WHERE (and the order, when its code does) of its own query: no runtime DEFAULT_WHERE
    of the target block concerns it.
    """
    from .rules import POLICY_CODES, runtime_block_properties
    target = plan['target']
    java = plan.get('java')
    if java:
        ignored = {'DEFAULT_WHERE', 'ONETIME_WHERE'} | ({'ORDER_BY'} if java.get('dynamic_order') or java.get('order') else set())
        resolved_java = {f['origin'] + ': futásidőben módosított ' + f['property'] + ' (' + f['call'] + '); '
                         'a ServiceImpl lekérdezését/DML-jét ennek megfelelően kell átvenni.'
                         for f in runtime_block_properties(model) if f['block'] in {target, None} and f['property'] in ignored}
        form = '@FORM:' + model['name']
        return list(dict.fromkeys(i['detail'] for i in model['issues']
                                  if i['owner'] in {form, target} and i['scope'] in {'all', 'read'}
                                  and i['code'] != 'QUERY_BIND_REQUIRED'
                                  and not (i['owner'] == form and i['code'] in POLICY_CODES)
                                  and not (i['code'] == 'RUNTIME_BLOCK_PROPERTY' and i['detail'] in resolved_java)))
    resolved = {f['origin'] + ': futásidőben módosított ' + f['property'] + ' (' + f['call'] + '); '
                'a ServiceImpl lekérdezését/DML-jét ennek megfelelően kell átvenni.'
                for f in runtime_block_properties(model)
                if f['origin'].upper() == 'PROGRAM UNIT ' + plan['unit'] and f['block'] == target
                and f['property'] == 'DEFAULT_WHERE'}
    form = '@FORM:' + model['name']
    return list(dict.fromkeys(i['detail'] for i in model['issues']
                             if i['owner'] in {form, target} and i['scope'] in {'all', 'read'}
                             and i['code'] != 'QUERY_BIND_REQUIRED'
                             and not (i['owner'] == form and i['code'] in POLICY_CODES)
                             and not (i['code'] == 'RUNTIME_BLOCK_PROPERTY' and i['detail'] in resolved)))


def message_comments(units: dict | None, indent: str) -> str:
    """The local alert/message procedures the screen shows as a message (toast): their original code, commented."""
    if not units:
        return ''
    from .query_java import region
    return region('Eredeti Forms-kód: ' + ', '.join(units), [(name + ' (helyi alert/üzenet-eljárás): a webes képernyőn üzenetként '
                  'jelenik meg. Az eredeti kódja, ha később kellene:', source) for name, source in units.items()], indent)


def java_method(operation, block, gated, log1x, user_type, support):
    from .action_scaffold import comment_lines
    from .common import java_text_block, jstr, name
    from .rules import JDBC_TYPES
    from .service_inline import support_methods

    from .plsql_passthrough import input_declarations, input_variable
    plan = operation['query_action']
    prepared = plan['prepared']
    reads = {'text': 'text', 'number': 'number', 'datetime': 'datetime'}
    arguments = []
    for bind in prepared['binds']:
        if bind['block'] == plan['context']:
            value = 'null'
        elif bind.get('input'):
            value = input_variable(prepared, bind)
        elif bind['parameter']:
            value = f"PlsqlValues.parameter(parameters, {jstr(bind['source'])})"
        else:
            value = (f"PlsqlValues.{reads[bind['type']]}(values, {jstr(bind['block'])}, "
                     f"{jstr(bind['item'])})")
        arguments.append(f"DbCalls.in({value}, {JDBC_TYPES[bind['type']]})")
    arguments.extend(f"DbCalls.out({JDBC_TYPES[b['type']]})" for b in prepared['outs'])
    arguments.append('DbCalls.out(java.sql.Types.VARCHAR)')
    out_indices = {b['source']: len(prepared['binds']) + n for n, b in enumerate(prepared['outs'])}
    where_index = out_indices[plan['context'] + '.WHERE_TEXT']
    executed_index = out_indices[plan['context'] + '.EXECUTED']
    message_index = len(prepared['binds']) + len(prepared['outs'])
    cases = []
    for n, variant in enumerate(plan['variants']):
        params = []
        for bind in variant['binds']:
            owner, item = bind['source'].split('.', 1)
            typ = bind['item']['type']
            value = f'PlsqlValues.{reads[typ]}(values, {jstr(owner)}, {jstr(item)})'
            params.append(f"                params.addValue({jstr(bind['parameter'])}, {value}, {JDBC_TYPES[typ]});")
        prefix = 'if' if n == 0 else 'else if'
        cases.append(f'''            {prefix} (whereText != null && whereText.equals({jstr(variant['predicate'])})) {{
                sql = {jstr(variant['sql'])};
{chr(10).join(params)}
            }}''')
    suffix = name(block['class'])
    suffix = suffix[:1].upper() + suffix[1:]
    helpers = support_methods('\n'.join(support))
    post = helpers.get('postQuery' + suffix)
    post_call = ''
    if post:
        args = 'row, context' if 'RuleContext' in post['header'] else 'row'
        post_call = f'''            var context = new RuleContext();
            for (var row : rows) {{
                postQuery{suffix}({args});
            }}
            messages.addAll(context.messages());
'''
    guard = ''
    if operation.get('query_blockers'):
        guard = ('            throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, '
                 + jstr('A lekérdezés további átültetést igényel: ' + operation['query_blockers'][0]) + ');\n')
        why = ('\nJava-lekérdezés nem készült: ' + operation['query_java_reason']) if operation.get('query_java_reason') else ''
        return comment_lines('DEFAULT_WHERE lekérdezés: ' + plan['unit'] + ' -> ' + plan['target'] + why, '    ') + f'''
    @Override
    public {operation['returns']} {operation['method']}({user_type} user, QueryActionRequest request) throws Exception {{
        {log1x(operation, '() -> {')}
{guard}        }});
    }}'''
    if gated:
        guard = '''            if (!MODULE_REVIEWED) {
                throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, "Ez a művelet ebben a modulban még nem érhető el.");
            }
'''
    parameters = ('            var parameters = request.parameters() == null ? java.util.Map.<String, String>of() : request.parameters();\n'
                  if any(b['parameter'] for b in prepared['binds']) else '')
    info = (plan['unit'] + ': az eredeti PL/SQL állítja össze a ' + plan['target'] + ' DEFAULT_WHERE feltételét.\n'
            'Csak a form forrásából lefordított SQL-változat fut; a mezőértékek kötött paraméterek.\n'
            'SET_BLOCK_PROPERTY / GO_BLOCK / EXECUTE_QUERY: JDBC lekérdezés és Angular rekordlista.\n'
            + ('Java-lekérdezés nem készült: ' + operation['query_java_reason'] + '\n' if operation.get('query_java_reason') else '')
            + 'Eredeti kód: analysis/backend-evidence.md')
    return comment_lines(info, '    ') + f'''
    @org.springframework.transaction.annotation.Transactional(rollbackFor = Exception.class)
    @Override
    public {operation['returns']} {operation['method']}({user_type} user, QueryActionRequest request) throws Exception {{
        {log1x(operation, '() -> {')}
            if (request == null) {{
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Hiányzó kérés.");
            }}
{guard}            if (request.offset() < 0 || request.offset() > 1000000 || request.limit() < 1 || request.limit() > 200) {{
                throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "offset: 0..1000000, limit: 1..200 szükséges.");
            }}
            var values = request.blocks() == null ? java.util.Map.<String, java.util.Map<String, String>>of() : request.blocks();
{parameters}{input_declarations(prepared, '            ')}{message_comments(plan.get('message_units'), '            ')}            Object[] out = DbCalls.call(jdbc, {java_text_block(prepared['sql'])},
                {(',' + chr(10) + '                ').join(arguments)});
            var messages = new ArrayList<>(PlsqlValues.lines(out[{message_index}]));
            if (!"Y".equals(out[{executed_index}])) {{
                return new PageResult<>(null, messages);
            }}
            var whereText = out[{where_index}];
            String sql = null;
            var params = new MapSqlParameterSource().addValue("offset", request.offset()).addValue("limit", request.limit());
{chr(10).join(cases)}
            if (sql == null) {{
                throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "A DEFAULT_WHERE nem egyezik a formból lefordított feltételekkel.");
            }}
            var rows = jdbc.query(sql, params, (rs, rowNum) -> map{suffix}(rs));
{post_call}            return new PageResult<>(rows, messages);
        }});
    }}'''


def evidence(plan):
    if plan.get('java'):
        java = plan['java']
        return '\n'.join(['Java lekérdezés: ' + java['unit'] + ' -> ' + java['target'],
                          'A WHERE a képernyő értékeiből, Java if-ekkel épül; a :BLOKK.MEZŐ hivatkozások JDBC bindek.',
                          'Kötött értékek: ' + (', '.join(':' + s for s in java['binds']) or '—'),
                          'Kimaradt Forms-hívások: ' + (', '.join(java['left_out']) or '—'),
                          'Üzenetként: ' + (', '.join(java['called']) or '—')])
    lines = ['DEFAULT_WHERE adapter: ' + plan['unit'] + ' -> ' + plan['target'],
             'PL/SQL: az eredeti IF ágak Oracle-ben futnak; a három Forms-hívás szerveroldali szűrőeredményre fordul.',
             'A kliens csak mezőértékeket küldhet, SQL-szöveget nem.',
             'WUZENET(állandó szöveg), ha nem helyi programegység: képernyőüzenet, adatlekérés nélkül.',
             'Bemeneti PL/SQL bindek: ' + ', '.join(b['source'] for b in plan['prepared']['binds'] if b['block'] != plan['context']),
             'Előre lefordított SQL-változatok: ' + str(len(plan['variants']))]
    for variant in plan['variants']:
        lines.extend(['', 'DEFAULT_WHERE: ' + variant['predicate'], 'SQL: ' + variant['sql']])
        lines.extend('Bind: :' + b['source'] + ' -> :' + b['parameter'] + ' (' + b['item']['type'] + ')'
                     for b in variant['binds'])
    return '\n'.join(lines)

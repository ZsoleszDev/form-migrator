"""Does a trigger need the database? The line between the Java backend and the screen (backend_trigger_mode=ai).

The Java application gets only database work: SQL, a database routine or package state, dynamic SQL, the Forms
built-ins that reach the server (FORMS_DDL, POPULATE_GROUP ...). Code that only works on the screen - items,
:GLOBAL values, navigation, messages, the queries and the save of the blocks (the screen runs them through the
generated endpoints) - is the screen's: it never becomes a Java endpoint or a task for the Java assistant.

The code is parsed (plsql.parse, extended grammar); the form's own program units it calls and the key trigger a
DO_KEY starts are looked into too. A program unit that does not parse is read lexically (discovery.scan). A doubt
counts as database work: unparsable code, an unknown routine or an unknown dotted name keep the code on the
server's side, where nothing of it is lost.
"""
from __future__ import annotations

import re

from . import forms_runtime, framework
from .common import decode_line_escapes
from .discovery import FORMS_BUILTINS, SQL_FUNCTIONS, scan
from .plsql import Unsupported, parse

# Names that read the session or the clock: evaluated on the screen where they occur in screen code.
SESSION_SYMBOLS = {'SYSDATE', 'SYSTIMESTAMP', 'USER', 'UID', 'SQLCODE', 'SQLERRM', 'TRUE', 'FALSE', 'NULL',
                   'NO_DATA_FOUND', 'TOO_MANY_ROWS', 'OTHERS', 'VALUE_ERROR', 'ZERO_DIVIDE', 'FORM_TRIGGER_FAILURE'}
DOTTED = re.compile(r'(?<![:\w$#.])([A-Za-z][\w$#]*)\s*\.\s*([A-Za-z][\w$#]*)(?!\s*\.)')
# Block properties that are the block's SQL: the query of such a block runs with the code's own SQL text.
QUERY_PROPERTIES = {'DEFAULT_WHERE', 'ONETIME_WHERE', 'ORDER_BY', 'QUERY_DATA_SOURCE_NAME', 'DML_DATA_TARGET_NAME'}


def evidence(source: str, model: dict, catalog: dict, block: str | None = None, item: str | None = None) -> list[str]:
    """Why the code needs the database, one Hungarian line per finding; [] when it only works on the screen."""
    from .plsql_passthrough import local_units
    finder = Finder(model, catalog, local_units(model))
    finder.code(decode_line_escapes(source or ''), block, item)
    return list(dict.fromkeys(finder.found))


class Finder:
    def __init__(self, model, catalog, units):
        self.model, self.catalog, self.units = model, catalog, units
        self.packages = {n for n, u in units.items() if u['kind'] == 'package'}
        self.found, self.seen = [], set()

    def add(self, text):
        self.found.append(text)

    # ------------------------------------------------------------------ code

    def code(self, text, block=None, item=None):
        try:
            nodes = parse(text, extended=True)
        except Unsupported as exc:
            before = len(self.found)
            self.lexical(text)  # what it does name: its SQL, its routines
            if len(self.found) == before:
                self.add('a kód nem elemezhető teljesen (' + str(exc).rstrip('.') + '): a biztonság kedvéért a szerveré')
            return
        self.block, self.item = block, item
        self.statements(nodes, set())

    def statements(self, nodes, declared):
        for node in nodes:
            op = node['op']
            if op in {'noop', 'pragma', 'abort', 'raise'}:
                continue
            if op == 'declare':
                declared.add(node['name'])
                self.expression(node['value'], declared)
            elif op == 'block':
                inner = set(declared)
                self.statements(node['body'], inner)
                for handler in node.get('handlers', []):
                    self.statements(handler['body'], set(inner))
            elif op == 'if':
                for branch in node['branches']:
                    self.expression(branch['condition'], declared)
                    self.statements(branch['body'], declared)
                self.statements(node['else'], declared)
            elif op == 'assign':  # :BLOCK.ITEM or :GLOBAL.X: the screen's
                self.expression(node['value'], declared)
            elif op == 'local_assign':
                target = node['variable'].upper()
                if '.' in target and target.split('.', 1)[0] not in self.packages and target not in declared:
                    self.add('adatbázis-csomag változója: ' + target)
                self.expression(node['value'], declared)
            elif op == 'select':
                self.add('SQL: SELECT … INTO')
            elif op == 'call':
                self.call(node['name'].upper(), node['args'], declared)
            else:
                self.add('nem ismert utasítás: ' + op)

    def expression(self, node, declared):
        if node is None:
            return
        op = node['op']
        if op in {'literal', 'ref'}:
            return
        if op == 'symbol':
            name = node['name'].upper()
            if name in declared or name in forms_runtime.CONSTANTS or name in FORMS_BUILTINS or name in SESSION_SYMBOLS:
                return
            if '.' in name and name.split('.', 1)[0] in self.packages and not self.package_function(name):
                return  # a variable of the form's own package: Forms state, not the database
            if name in self.units or name.split('.', 1)[0] in self.units:
                self.unit(name)
            elif '.' in name and (forms_runtime.builtin(name) or framework.framework_name(name, self.catalog)):
                return
            else:
                self.add(('adatbázis-függvény vagy csomagelem: ' if '.' in name else 'ismeretlen név (adatbázis-függvény lehet): ')
                         + name)
            return
        if op == 'function':
            name = node['name'].upper()
            if name in self.units or name.split('.', 1)[0] in self.units:
                self.unit(name)
            elif not (name in SQL_FUNCTIONS or name in forms_runtime.PURE_FUNCTIONS or self.screen_builtin(name)
                      or framework.framework_name(name, self.catalog)):
                self.add('adatbázis-függvény: ' + name)
            for arg in node['args']:
                self.expression(arg, declared)
            return
        for key in ('value', 'left', 'right', 'pattern', 'low', 'high', 'operand', 'else'):
            if isinstance(node.get(key), dict):
                self.expression(node[key], declared)
        for child in node.get('items', []):
            self.expression(child, declared)
        for when in node.get('whens', []):
            self.expression(when['when'], declared)
            self.expression(when['then'], declared)

    def call(self, name, args, declared):
        if framework.framework_name(name, self.catalog):
            return  # catalogued plumbing or a Forms-runtime-only routine
        if name in {'DO_KEY', 'EXECUTE_TRIGGER'}:
            literal = args[0] if len(args) == 1 and args[0]['op'] == 'literal' and args[0]['type'] == 'text' else None
            if literal is None or not literal['value']:
                self.add(name + ' dinamikus argumentummal')
            elif name == 'DO_KEY':
                from .forms_keys import overrides
                for trigger in overrides(self.model, literal['value'].upper(), self.block, self.item):
                    self.trigger(trigger)
            else:
                event = literal['value'].upper()
                found = [t for t in self.model['triggers'] if t['event'] == event]
                if not found:
                    self.add('EXECUTE_TRIGGER ismeretlen triggerrel: ' + event)
                for trigger in found:
                    self.trigger(trigger)
            return
        if name == 'SET_BLOCK_PROPERTY' and len(args) >= 2 and args[1]['op'] == 'symbol' \
                and args[1]['name'].upper() in QUERY_PROPERTIES:
            self.add('a blokk lekérdezésének SQL-je: SET_BLOCK_PROPERTY ' + args[1]['name'].upper())
        if name in self.units or name.split('.', 1)[0] in self.units:
            self.unit(name)
        elif not self.screen_builtin(name):
            found = forms_runtime.builtin(name)
            self.add(('Forms szerveroldali beépített hívás: ' if found else 'adatbázis-rutin: ') + name)
        for arg in args:
            self.expression(arg, declared)

    def package_function(self, name):
        """PKG.MEMBER of the form's own package is a function (its spec says FUNCTION MEMBER), not a variable."""
        package, member = name.split('.', 1)
        spec = self.units[package].get('spec') or self.units[package]['text']
        return re.search(r'\bFUNCTION\s+' + re.escape(member) + r'\b', decode_line_escapes(spec), re.I) is not None

    @staticmethod
    def screen_builtin(name):
        found = forms_runtime.builtin(name)
        return (found is not None and found[0] != 'server') or (found is None and name in FORMS_BUILTINS)

    # ------------------------------------------------------------------ other code it reaches

    def trigger(self, trigger):
        key = 'trigger:' + trigger['id']
        if key in self.seen:
            return
        self.seen.add(key)
        source = (trigger.get('replacement') or {}).get('source', trigger['source'])
        saved = (self.block, self.item)
        self.code(decode_line_escapes(source), trigger.get('block'), trigger.get('item'))
        self.block, self.item = saved

    def unit(self, name):
        """A program unit of the form (or of an attached library): a parameterless procedure parsed, the rest read."""
        unit_name = name if name in self.units else name.split('.', 1)[0]
        if unit_name in self.seen:
            return
        self.seen.add(unit_name)
        unit = self.units[unit_name]
        if unit['kind'] == 'procedure':
            from .backend_units import LocalProcedures
            try:
                body, _ = LocalProcedures(self.model).body(unit_name)
                self.statements(body, set())
                return
            except (Unsupported, KeyError):
                pass
        self.lexical(unit['text'])

    def lexical(self, text):
        """A unit the parser does not take (package, function, parameters, declarations): its SQL and calls."""
        found = scan(decode_line_escapes(text))
        for statement in found['sql']:
            self.add('SQL: ' + statement['kind'])
        if found['dynamic_sql']:
            self.add('dinamikus SQL (EXECUTE IMMEDIATE)')
        for call in found['calls']:
            name = call['name'].upper()
            if framework.framework_name(name, self.catalog) or name in SQL_FUNCTIONS:
                continue
            if name in self.units or name.split('.', 1)[0] in self.units:
                self.unit(name)
            elif not self.screen_builtin(name):
                self.add(('Forms szerveroldali beépített hívás: ' if forms_runtime.builtin(name) else 'adatbázis-rutin: ') + name)
        masked = ''.join(t[1] if t[0] != 'comment' else ' ' for t in tokens(text))
        for prop in re.findall(r'\bSET_BLOCK_PROPERTY\s*\([^,()]*,\s*([A-Za-z_]+)', masked, re.I):
            if prop.upper() in QUERY_PROPERTIES:
                self.add('a blokk lekérdezésének SQL-je: SET_BLOCK_PROPERTY ' + prop.upper())
        for first, second in DOTTED.findall(re.sub(r"'(?:''|[^'])*'", "''", masked)):
            first, dotted = first.upper(), (first + '.' + second).upper()
            if first in self.packages or first in self.units or forms_runtime.builtin(dotted) \
                    or framework.framework_name(dotted, self.catalog):
                continue
            self.add('adatbázis-függvény vagy csomagelem: ' + dotted)


def tokens(text):
    from .plsql_passthrough import scan as lex
    try:
        return lex(decode_line_escapes(text))
    except Unsupported:
        return [('code', decode_line_escapes(text))]

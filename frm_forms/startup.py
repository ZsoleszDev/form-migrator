"""The form's start-up code (PRE-FORM, WHEN-NEW-FORM-INSTANCE), sorted by what the web application needs of it.

A web screen does not "start a form": it opens in its initial state, and only work on the database needs the
server. So every statement goes where its effect belongs, and the Java assistant gets only what touches the
database (backend_trigger_mode=ai):

- dropped: Forms plumbing without a web equivalent - framework dispatch (qms$event_form), cursor style, window and
  focus moves, session NLS settings (FORMS_DDL('ALTER SESSION ...'));
- screen: what the opened screen shows, fixed at generation time - a constant in an item (its start value) or in a
  :GLOBAL (the value the screen sends), an item property with constant arguments, the block a GO_BLOCK +
  EXECUTE_QUERY loads;
- notes: Forms state the web application has no place for (in-memory record groups, package variables, block query
  properties, a message on opening): MIGRATION_NOTES.md lists them for the developer;
- backend: SQL, database routines, the form's own program units, unknown calls, conditions and loops - verbatim,
  inside their blocks with their declarations and handlers.

A statement leaves the backend only when its kind is certain; anything else stays, so no logic is lost. A constant
written to an item stays in the backend code too when a statement left there reads that item.
"""
from __future__ import annotations

import re

from . import forms_runtime, framework
from .common import decode_line_escapes
from .plsql import Unsupported
from .plsql_structure import apply, parse, prune

# Statements that work on the database or steer the flow: always the backend's.
FLOW = {'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'MERGE', 'COMMIT', 'ROLLBACK', 'SAVEPOINT', 'LOCK', 'OPEN', 'FETCH',
        'CLOSE', 'EXECUTE', 'RAISE', 'RETURN', 'EXIT', 'GOTO', 'CONTINUE', 'FORALL', 'PIPE', 'SET'}
# Forms presentation: windows, canvases, focus, menus, the cursor. The web screen has its own.
PRESENTATION = set('''SET_APPLICATION_PROPERTY SET_WINDOW_PROPERTY SHOW_WINDOW HIDE_WINDOW MOVE_WINDOW RESIZE_WINDOW
 SET_CANVAS_PROPERTY SET_VIEW_PROPERTY SHOW_VIEW HIDE_VIEW REPLACE_CONTENT_VIEW SET_TAB_PAGE_PROPERTY SET_FORM_PROPERTY
 SET_INPUT_FOCUS SYNCHRONIZE REDISPLAY BELL CLEAR_MESSAGE GO_ITEM GO_RECORD FIRST_RECORD LAST_RECORD NEXT_ITEM
 PREVIOUS_ITEM NEXT_BLOCK PREVIOUS_BLOCK NEXT_RECORD PREVIOUS_RECORD UP DOWN SCROLL_UP SCROLL_DOWN SCROLL_VIEW
 REPLACE_MENU SET_MENU_ITEM_PROPERTY SHOW_MENU HIDE_MENU SET_VA_PROPERTY SET_ALERT_PROPERTY SET_ALERT_BUTTON_PROPERTY
 DEBUG_MODE'''.split())
# In-memory record groups: Forms-side lists the screen holds itself.
GROUPS = set('''CREATE_GROUP ADD_GROUP_COLUMN ADD_GROUP_ROW SET_GROUP_CHAR_CELL SET_GROUP_NUMBER_CELL SET_GROUP_DATE_CELL
 DELETE_GROUP DELETE_GROUP_ROW FIND_GROUP FIND_COLUMN'''.split())
# Forms state with an effect later on: the developer re-creates it where it matters.
MANUAL = {'SET_BLOCK_PROPERTY': 'a blokk lekérdezését vagy viselkedését módosítja (pl. DEFAULT_WHERE, ORDER_BY): '
                                'a lekérdező végpont vagy a képernyő feladata',
          'SET_LOV_PROPERTY': 'értéklista-beállítás: a képernyő listájának feladata',
          'SET_LOV_COLUMN_PROPERTY': 'értéklista-beállítás: a képernyő listájának feladata',
          'CREATE_TIMER': 'időzítő: a képernyő feladata (setInterval/RxJS timer)',
          'CALL_FORM': 'indításkori átirányítás másik formra: a képernyő útválasztásának feladata',
          'OPEN_FORM': 'indításkori átirányítás másik formra: a képernyő útválasztásának feladata',
          'NEW_FORM': 'indításkori átirányítás másik formra: a képernyő útválasztásának feladata',
          'EXIT_FORM': 'indításkori kilépés: a képernyő útválasztásának feladata'}
NOTE_GROUP = 'memóriabeli értéklista (record group): ha egy lista vagy gomb használja, a képernyőn kell megadni'
NOTE_PACKAGE = 'csomagváltozó kezdőértéke: a webes backendnek nincs munkamenet-állapota; ha más kód olvassa, kézzel kell kezelni'
DROP_FRAMEWORK = 'keretrendszer-hívás: a webes alkalmazásban nincs szerepe'
DROP_PRESENTATION = 'Forms-felület (ablak, fókusz, menü, kurzor): a webes képernyőnek saját megoldása van'
DROP_SESSION = 'munkamenet-beállítás: a Java maga formáz, a kapcsolatkészlet kapcsolatait nem állítjuk át'
EMPTY = re.compile(r'(?:\s*NULL\s*;)*\s*', re.I)


def triage(source: str, catalog: dict, units=()) -> dict:
    """The start-up trigger sorted (see the module doc). backend: the PL/SQL left for the server, '' when none.

    The other keys hold the effects in statement order: values {BLOCK.ITEM: text}, globals {GLOBAL.X: text},
    properties [{item, property, value}], queries [block], notes and dropped [{statement, reason}].
    """
    text = decode_line_escapes(source).strip().rstrip('/').strip()
    result = {'backend': text, 'values': {}, 'globals': {}, 'properties': [], 'queries': [], 'notes': [], 'dropped': []}
    body = 'BEGIN\n' + text + '\nEND;'
    try:
        sig, root = parse(body, 'Az indítókód')
    except Unsupported:
        return result  # its structure is unknown: all of it stays the backend's, as before
    sorter = Sorter(body, sig, catalog, units)
    for node in root.body:
        sorter.decide(node, static=True)
    edits = sorter.emit(root.body, kept=any(sorter.category(n) == 'backend' for n in root.body))
    reduced = apply(body, edits)[len('BEGIN\n'):-len('\nEND;')] if edits else text
    reduced = prune(reduced).strip()
    result.update(sorter.effects, backend='' if EMPTY.fullmatch(reduced) else reduced)
    return result


class Sorter:
    def __init__(self, body, sig, catalog, units):
        self.body, self.sig, self.catalog = body, sig, catalog
        self.units = {str(u).upper() for u in units}
        self.decisions = {}  # id(node) -> (category, effect); category: backend | remove | local
        self.block = None  # the block of the last GO_BLOCK: EXECUTE_QUERY queries it
        self.effects = {'values': {}, 'globals': {}, 'properties': [], 'queries': [], 'notes': [], 'dropped': []}
        self.reads = set()  # the binds the backend code reads

    def text(self, node):
        return self.body[self.sig[node.start][2]:self.sig[node.end][3]]

    def category(self, node):
        return self.decisions[id(node)][0]

    # ------------------------------------------------------------------ deciding

    def decide(self, node, static):
        """Classify a node and its children; static: it runs unconditionally at start-up (no IF, loop or handler)."""
        if node.kind == 'block':
            kinds = [self.decide(n, static) for n in node.body]
            for handler in node.handlers:
                for n in handler:
                    self.decide(n, False)
            computed = self.initializers(node)
            category = 'backend' if 'backend' in kinds or computed else 'remove'
            self.decisions[id(node)] = (category, None)
        elif node.kind == 'if':
            kinds = [self.decide(n, False) for _, _, branch in node.branches for n in branch]
            category = 'remove' if kinds and all(k == 'remove' for k in kinds) else 'backend'
            self.decisions[id(node)] = (category, None)
        elif node.kind == 'simple':
            self.decisions[id(node)] = self.statement(self.sig[node.start:node.end], static)
        else:  # loops, CASE: not looked into
            self.decisions[id(node)] = ('backend', None)
        if self.decisions[id(node)][0] == 'backend' and node.kind != 'block':
            self.reads |= {t[1][1:].upper() for t in self.sig[node.start:node.end + 1] if t[0] == 'bind'}
        return self.decisions[id(node)][0]

    def initializers(self, node):
        """True when a declaration of the block computes its value (a call, a query): that work stays."""
        if not node.declare:
            return False
        tokens = self.sig[node.declare[0]:node.declare[1] + 1]
        for k, token in enumerate(tokens):
            if token[1] == ':=' or (token[0] == 'ident' and token[1].upper() == 'DEFAULT'):
                end = next((j for j in range(k + 1, len(tokens)) if tokens[j][1] == ';'), len(tokens))
                if literal_value(tokens[k + 1:end]) is None:
                    self.reads |= {t[1][1:].upper() for t in tokens if t[0] == 'bind'}
                    return True
        return False

    def statement(self, tokens, static):
        if not tokens or (len(tokens) == 1 and tokens[0][1].upper() == 'NULL'):
            return 'remove', None
        assign = next((k for k, t in enumerate(tokens) if t[0] == 'op' and t[1] == ':='), None)
        if assign is not None:
            return self.assignment(''.join(t[1] for t in tokens[:assign]).upper(), tokens[assign + 1:], static)
        if tokens[0][0] == 'ident' and tokens[0][1].upper() in FLOW:
            return 'backend', None
        call = parse_call(tokens)
        if call is None:
            return 'backend', None
        return self.call(*call, static)

    def assignment(self, target, value, static):
        literal = literal_value(value)
        if target.startswith(':'):
            name = target[1:]
            prefix = name.split('.', 1)[0]
            if literal is None or not static or '.' not in name or prefix in {'SYSTEM', 'PARAMETER'}:
                return 'backend', None
            return 'remove', ('global' if prefix == 'GLOBAL' else 'value', name, literal[0])
        call = parse_call(value)
        if call and call[0] in GROUPS:
            return 'remove', ('note', NOTE_GROUP)
        # A variable: it matters only to the code next to it, which decides whether it stays.
        return 'local', (('note', NOTE_PACKAGE) if '.' in target else None)

    def call(self, name, args, static):
        if framework.framework_name(name, self.catalog):
            return 'remove', ('dropped', DROP_FRAMEWORK)
        if name in self.units or name.split('.', 1)[0] in self.units:
            return 'backend', None  # the form's own program unit (it comes before a built-in): it may do anything
        found = forms_runtime.builtin(name)
        if not found:
            return 'backend', None  # a database routine or an unknown library call
        if name == 'GO_BLOCK':
            block = literal_value(args[0]) if len(args) == 1 else None
            self.block = block[0].upper() if static and block and block[0] else None
            return 'remove', ('dropped', DROP_PRESENTATION)
        if name == 'EXECUTE_QUERY' and static and self.block and all(constant(a) for a in args):
            return 'remove', ('query', self.block)
        if name == 'SET_ITEM_PROPERTY' and static and len(args) == 3:
            item, prop, value = literal_value(args[0]), constant(args[1]), literal_value(args[2]) or constant(args[2])
            if item and item[0] and '.' in item[0] and prop and value:
                return 'remove', ('property', item[0].upper(), prop[0], value[0])
            return 'backend', None
        if name == 'DEFAULT_VALUE' and static and len(args) == 2:
            value, target = literal_value(args[0]), literal_value(args[1])
            if value and target and target[0] and target[0].upper().startswith('GLOBAL.'):
                return 'remove', ('default', target[0].upper(), value[0])
            return 'backend', None
        if name == 'FORMS_DDL':
            statement = literal_value(args[0]) if len(args) == 1 else None
            if statement and statement[0] and re.match(r'\s*ALTER\s+SESSION\b', statement[0], re.I):
                return 'remove', ('dropped', DROP_SESSION)
            return 'backend', None
        if name in GROUPS:
            return 'remove', ('note', NOTE_GROUP)
        if name in PRESENTATION:
            return 'remove', ('dropped', DROP_PRESENTATION)
        if name == 'MESSAGE' and static and args and literal_value(args[0]):
            return 'remove', ('note', 'üzenet a megnyitáskor: a képernyő mutatja (toast), ha kell')
        if name in MANUAL and static:
            return 'remove', ('note', MANUAL[name])
        return 'backend', None

    # ------------------------------------------------------------------ emitting

    def emit(self, nodes, kept):
        """The edits that take out what leaves the backend; the effects of what leaves, in statement order."""
        edits = []
        for node in nodes:
            category, effect = self.decisions[id(node)]
            if category == 'backend':
                if node.kind == 'block':
                    edits += self.emit(node.body, kept=True)
                continue
            if category == 'local' and kept:
                continue
            stays = kept and effect and effect[0] in {'value', 'global'} and effect[1] in self.reads
            self.record(node)
            if not stays:
                start, end = self.sig[node.start][2], self.sig[node.end][3]
                edits.append((start, end, 'NULL;', 0))
        return edits

    def record(self, node):
        """The effects of a node that leaves the backend (a block or IF: of its statements)."""
        category, effect = self.decisions[id(node)]
        if node.kind == 'block':
            for child in node.body:
                self.record(child)
            return
        if node.kind == 'if':
            for _, _, branch in node.branches:
                for child in branch:
                    self.record(child)
            return
        if not effect:
            return
        statement = ' '.join(self.text(node).split())
        kind = effect[0]
        if kind == 'value':
            self.effects['values'][effect[1]] = effect[2]
        elif kind == 'global':
            self.effects['globals'][effect[1]] = effect[2]
        elif kind == 'default':
            self.effects['globals'].setdefault(effect[1], effect[2])
        elif kind == 'property':
            self.effects['properties'].append({'item': effect[1], 'property': effect[2], 'value': effect[3]})
        elif kind == 'query':
            if effect[1] not in self.effects['queries']:
                self.effects['queries'].append(effect[1])
        else:
            self.effects['notes' if kind == 'note' else 'dropped'].append({'statement': statement, 'reason': effect[1]})


def parse_call(tokens):
    """(NAME, [argument tokens]) of `name;` or `name(arguments);`, else None."""
    parts, k = [], 0
    while k < len(tokens) and tokens[k][0] == 'ident':
        parts.append(tokens[k][1])
        k += 1
        if k < len(tokens) and tokens[k][1] == '.' and k + 1 < len(tokens) and tokens[k + 1][0] == 'ident':
            k += 1
            continue
        break
    if not parts:
        return None
    name = '.'.join(parts).upper()
    if k == len(tokens):
        return name, []
    if tokens[k][1] != '(' or tokens[-1][1] != ')':
        return None
    args, current, depth = [], [], 0
    for token in tokens[k + 1:-1]:
        if token[0] == 'op' and token[1] == '(':
            depth += 1
        elif token[0] == 'op' and token[1] == ')':
            depth -= 1
            if depth < 0:
                return None  # `f(a)(b)` or `f(a) + g(b)`: not one call
        if token[0] == 'op' and token[1] == ',' and depth == 0:
            args.append(current)
            current = []
        else:
            current.append(token)
    if depth:
        return None
    if current or args:
        args.append(current)
    return name, args


def literal_value(tokens):
    """(value,) of a constant expression - a string, a number or NULL - else None."""
    if len(tokens) == 2 and tokens[0][1] in {'-', '+'} and tokens[1][0] == 'number':
        return ((tokens[0][1] if tokens[0][1] == '-' else '') + tokens[1][1],)
    if len(tokens) != 1:
        return None
    kind, text = tokens[0][0], tokens[0][1]
    if kind == 'string' and text.startswith("'") and text.endswith("'") and len(text) >= 2:
        return (text[1:-1].replace("''", "'"),)
    if kind == 'number':
        return (text,)
    if kind == 'ident' and text.upper() == 'NULL':
        return (None,)
    return None


def constant(tokens):
    """(NAME,) of a Forms constant argument (INSERT_ALLOWED, PROPERTY_FALSE ...), else None."""
    if len(tokens) == 1 and tokens[0][0] == 'ident' and tokens[0][1].upper() in forms_runtime.CONSTANTS:
        return (tokens[0][1].upper(),)
    return None


def merge(results: dict) -> dict:
    """The effects of the start-up triggers in Forms order (PRE-FORM first): the later write wins."""
    merged = {'values': {}, 'globals': {}, 'properties': [], 'queries': [], 'notes': [], 'dropped': []}
    for trigger_id, result in results.items():
        merged['values'].update(result['values'])
        merged['globals'].update(result['globals'])
        merged['properties'] += result['properties']
        merged['queries'] += [b for b in result['queries'] if b not in merged['queries']]
        for kind in ('notes', 'dropped'):
            for entry in result[kind]:
                last = merged[kind][-1] if merged[kind] else None
                if last and last['trigger'] == trigger_id and last['reason'] == entry['reason']:
                    last['statement'] += ' ' + entry['statement']  # one row for one thing (a record group, a dispatch pair)
                else:
                    merged[kind].append(dict(entry, trigger=trigger_id))
    return merged


# Item properties the screen holds itself: FormBlock structure flags.
FALSE = {'PROPERTY_FALSE', 'PROPERTY_OFF', 'FALSE'}
TRUE = {'PROPERTY_TRUE', 'PROPERTY_ON', 'TRUE'}


def screen_state(sections: list, state: dict) -> dict:
    """The opening state of the screen from the start-up code: start values and item properties on its items.

    sections: the screen plan's form and table sections (their items change in place). Returns what MIGRATION_NOTES.md
    lists: applied [{owner, what}], manual and dropped [{statement, reason}] (the triage's notes included).
    """
    items = {i['owner'].upper(): i for s in sections for i in s['items'] if s['mode'] == 'form'}
    applied, manual = [], [dict(n) for n in state.get('notes', [])]
    dropped = [dict(d) for d in state.get('dropped', [])]
    for owner, value in state.get('values', {}).items():
        if value is None:
            continue  # NULL: an empty item, as it starts anyway
        item = items.get(owner)
        if item is None or item.get('spacer'):
            manual.append({'statement': ':' + owner + " := '" + value + "'", 'reason': 'a mező nincs a képernyő űrlapján: kézzel kell átvinni'})
            continue
        item['initial_value'] = value
        applied.append({'owner': owner, 'what': 'kezdőérték: ' + value})
    grouped = {}
    for entry in state.get('properties', []):
        grouped.setdefault(entry['item'], {})[entry['property']] = entry['value']
    for owner, props in grouped.items():
        item = items.get(owner)
        statement = lambda p: 'SET_ITEM_PROPERTY(' + owner + ', ' + p + ', ' + str(props[p]) + ')'
        if item is None:
            manual += [{'statement': statement(p), 'reason': 'a mező nincs a képernyő űrlapján'} for p in props]
            continue
        for prop, value in props.items():
            if prop == 'ENABLED' and value in FALSE | TRUE:
                item['enabled'] = value in TRUE
                applied.append({'owner': owner, 'what': 'tiltva' if value in FALSE else 'engedélyezve'})
            elif prop in {'INSERT_ALLOWED', 'UPDATE_ALLOWED'} and props.get('INSERT_ALLOWED') in FALSE \
                    and props.get('UPDATE_ALLOWED') in FALSE:
                if prop == 'INSERT_ALLOWED':
                    item['readonly'] = True
                    applied.append({'owner': owner, 'what': 'csak olvasható'})
            elif prop == 'REQUIRED' and value in FALSE | TRUE:
                item['validation']['required'] = value in TRUE
                applied.append({'owner': owner, 'what': 'kötelező' if value in TRUE else 'nem kötelező'})
            elif prop in {'PROMPT_TEXT', 'LABEL'} and value not in FALSE | TRUE:
                item['label'] = value
                applied.append({'owner': owner, 'what': 'felirat: ' + value})
            elif prop in {'HINT_TEXT', 'TOOLTIP_TEXT'} and value not in FALSE | TRUE:
                item['hint'] = value
                applied.append({'owner': owner, 'what': 'súgó: ' + value})
            elif prop == 'VISUAL_ATTRIBUTE':
                dropped.append({'statement': statement(prop), 'reason': 'megjelenés (visual attribute): a képernyő stílusának része'})
            else:
                manual.append({'statement': statement(prop), 'reason': 'mezőtulajdonság a megnyitáskor: a képernyőn kell beállítani'})
    return {'applied': applied, 'manual': manual, 'dropped': dropped}

"""Framework catalog and conservative recognisers for Forms trigger code.

Designer/Headstart forms carry a lot of generated plumbing: event dispatch calls,
a calendar popup block, an error console, templated hints. None of it is
business logic, and none of it belongs in an Angular screen. The objects are
listed in an editable catalog (``data/framework-catalog.json`` or the file named
by ``framework_catalog``) so that every decision here is traceable to a line in
a reviewed file, never to a guess from a name.

The recognisers are deliberately strict. Each one accepts a trigger only if
every statement in it is understood; anything else returns ``None`` and the
trigger stays on the developer's list.
"""
import fnmatch
import json
from pathlib import Path
import re

from .common import MigrationError
from . import forms_runtime, messages

DATA = Path(__file__).with_name('data') / 'framework-catalog.json'
LINE_ESCAPES = re.compile(r'&#(?:10|13|9|x0*[aAdD9]);')
CALL = re.compile(r'([A-Za-z][\w$#]*(?:\.[A-Za-z][\w$#]*)?)\s*(\(|$)')
LITERAL = r"'((?:[^']|'')*)'"
KEYWORDS = {'if', 'then', 'else', 'elsif', 'end', 'begin', 'loop', 'while', 'for', 'return', 'exception',
            'when', 'others', 'and', 'or', 'not', 'in', 'is', 'null', 'declare', 'case', 'raise', 'exit'}
# Plain Forms built-ins with no argument, or a validation-mode argument only.
SIMPLE = {'execute_query': 'executeQuery', 'enter_query': 'enterQuery', 'commit_form': 'commit', 'commit': 'commit',
          'clear_block': 'clearBlock', 'clear_form': 'clearForm', 'clear_record': 'clearRecord',
          'exit_form': 'exitForm', 'next_record': 'nextRecord', 'previous_record': 'previousRecord',
          'first_record': 'firstRecord', 'last_record': 'lastRecord', 'create_record': 'createRecord',
          'delete_record': 'deleteRecord', 'next_block': 'nextBlock', 'previous_block': 'previousBlock',
          'next_item': 'nextItem', 'previous_item': 'previousItem', 'list_values': 'listValues'}
MODES = r'(?:no_validate|do_commit|no_commit|full_rollback|ask_commit|restrict|no_restrict|all_records|for_update|no_wait|\d+)'
NAMED = {'go_block': ('goBlock', 'block'), 'go_item': ('goItem', 'item'), 'show_window': ('showWindow', 'window'),
         'hide_window': ('hideWindow', 'window'), 'show_view': ('showCanvas', 'canvas'), 'hide_view': ('hideCanvas', 'canvas'),
         'message': ('message', 'text')}
SQL_CONSTRUCTS = [
    ('ROWNUM', re.compile(r'\brownum\b', re.I), 'LIMIT / ROW_NUMBER()'),
    ('CONNECT BY', re.compile(r'\bconnect\s+by\b', re.I), 'WITH RECURSIVE'),
    ('DECODE', re.compile(r'\bdecode\s*\(', re.I), 'CASE WHEN'),
    ('NVL / NVL2', re.compile(r'\bnvl2?\s*\(', re.I), 'COALESCE / CASE'),
    ('SYSDATE', re.compile(r'\bsysdate\b', re.I), 'LOCALTIMESTAMP / NOW()'),
    ('DUAL', re.compile(r'\bfrom\s+dual\b', re.I), 'SELECT FROM nélkül'),
    ('(+) külső join', re.compile(r'\(\s*\+\s*\)'), 'LEFT/RIGHT JOIN'),
    ('ROWID', re.compile(r'\browid\b', re.I), 'elsődleges kulcs'),
    ('szekvencia .NEXTVAL', re.compile(r'\.\s*nextval\b', re.I), "nextval('seq')"),
]


RUNTIME_PATTERN = re.compile(r'[A-Za-z0-9_$#*?]+(?:\.[A-Za-z0-9_$#*?]+)?')
ROUTINE = re.compile(r'[A-Za-z][\w$#]*(?:\.[A-Za-z][\w$#]*)?')
# A custom catalog without the key keeps these entries; {} switches them off.
DEFAULT_RUNTIME_CALLS = {
    'CALENDAR.*': 'A naptár-segédablak Forms-oldali eseménykezelője: csak az Oracle Forms futtatókörnyezetben működik, '
                  'az adatbázisban nincs ilyen rutin. A dátummezők natív naptárvezérlője váltja ki.'}


def _validate(value, source):
    expected = {'version', 'description', 'call_prefixes', 'blocks', 'date_picker_calls', 'empty_hint_templates', 'spacer_items',
                'forms_runtime_calls', 'message_calls', 'message_functions', 'failure_calls'}
    if not isinstance(value, dict) or set(value) - expected or value.get('version') != 1:
        raise MigrationError('FRAMEWORK_CATALOG: version=1 és csak ismert kulcsok engedélyezettek: ' + source)
    lists = ['call_prefixes', 'date_picker_calls', 'empty_hint_templates', 'spacer_items']
    if any(not isinstance(value.get(k, []), list) or not all(isinstance(x, str) and x.strip() for x in value.get(k, []))
           for k in lists):
        raise MigrationError('FRAMEWORK_CATALOG: ' + ', '.join(lists) + ' nem üres szövegek listája: ' + source)
    blocks = value.get('blocks', {})
    if not isinstance(blocks, dict) or not all(isinstance(k, str) and isinstance(v, str) and k and v for k, v in blocks.items()):
        raise MigrationError('FRAMEWORK_CATALOG: blocks = {blokknév: indoklás}: ' + source)
    for pattern in value.get('spacer_items', []):
        if not re.fullmatch(r'[A-Za-z0-9_$#*?]+(?:\.[A-Za-z0-9_$#*?]+)?', pattern.strip()):
            raise MigrationError('FRAMEWORK_CATALOG: spacer_items = ITEM vagy BLOKK.ITEM minta (* és ? helyettesítővel): ' + pattern)
    for key in ('message_calls', 'message_functions'):
        routines = value.get(key, {})
        if not isinstance(routines, dict) or not all(
                isinstance(k, str) and ROUTINE.fullmatch(k.strip()) and isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 20
                for k, v in routines.items()):
            raise MigrationError('FRAMEWORK_CATALOG: ' + key + ' = {RUTIN vagy CSOMAG.RUTIN: a szöveg argumentumának '
                                 'sorszáma (1-től)}: ' + source)
    failures = value.get('failure_calls', [])
    if not isinstance(failures, list) or not all(isinstance(x, str) and ROUTINE.fullmatch(x.strip()) for x in failures):
        raise MigrationError('FRAMEWORK_CATALOG: failure_calls = [RUTIN vagy CSOMAG.RUTIN, ...]: ' + source)
    runtime = value.get('forms_runtime_calls', {})
    if not isinstance(runtime, dict) or not all(isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip()
                                                for k, v in runtime.items()):
        raise MigrationError('FRAMEWORK_CATALOG: forms_runtime_calls = {RUTIN_MINTA: indoklás}: ' + source)
    for pattern in runtime:
        # The package (or standalone routine) part needs a literal character: '*.*' would swallow every call.
        if not RUNTIME_PATTERN.fullmatch(pattern.strip()) or not re.search(r'[A-Za-z0-9_$#]', pattern.strip().split('.')[0]):
            raise MigrationError('FRAMEWORK_CATALOG: forms_runtime_calls minta = RUTIN vagy CSOMAG.RUTIN (* és ? helyettesítővel, '
                                 'a csomagnévben konkrét karakterrel): ' + pattern)


def _read(path):
    # Read on every run: the web server is long-lived and catalogs get edited.
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise MigrationError('FRAMEWORK_CATALOG: ' + str(exc)) from exc
    _validate(value, str(path))
    return value


def load(config):
    value = _read(str(config.get('framework_catalog') or DATA))
    return {'call_prefixes': tuple(p.lower() for p in value.get('call_prefixes', [])),
            'blocks': {k.upper(): v for k, v in value.get('blocks', {}).items()},
            'date_picker_calls': {c.lower() for c in value.get('date_picker_calls', [])},
            'empty_hint_templates': {normal(t) for t in value.get('empty_hint_templates', [])},
            # A custom catalog without the key keeps the convention; [] switches it off.
            'spacer_items': tuple(p.strip().upper() for p in value.get('spacer_items', DEFAULT_SPACERS)),
            'runtime_calls': tuple((p.strip().upper(), r.strip()) for p, r in value.get('forms_runtime_calls', DEFAULT_RUNTIME_CALLS).items()),
            # Routines that only show a text (or only fail): MESSAGE / RAISE FORM_TRIGGER_FAILURE everywhere (messages.py).
            'messages': {'calls': {k.strip().upper(): v for k, v in value.get('message_calls', messages.DEFAULT_MESSAGE_CALLS).items()},
                         'functions': {k.strip().upper(): v
                                       for k, v in value.get('message_functions', messages.DEFAULT_MESSAGE_FUNCTIONS).items()},
                         'failures': tuple(x.strip().upper() for x in value.get('failure_calls', messages.DEFAULT_FAILURE_CALLS))},
            'source': str(config.get('framework_catalog') or 'frm_forms/data/framework-catalog.json')}


DEFAULT_SPACERS = ('L_URES', 'L_URES_*')


def runtime_call(name, catalog):
    """(pattern, reason) of a catalogued Forms-runtime routine matching PROC or PKG.PROC, or None.

    Parts are matched one by one, so CALENDAR.* never matches CALENDAR_UTIL.X, and an
    owner-qualified OWNER.PKG.PROC (always a database call) never matches.
    """
    parts = str(name or '').strip().upper().split('.')
    for pattern, reason in catalog.get('runtime_calls', ()):
        pieces = pattern.split('.')
        if len(pieces) == len(parts) and all(fnmatch.fnmatchcase(a, b) for a, b in zip(parts, pieces)):
            return pattern, reason
    return None


def framework_name(name, catalog):
    """A routine name (any case) that the catalog lists as framework plumbing or Forms-runtime-only."""
    text = str(name or '')
    prefixes = catalog.get('call_prefixes') or ()
    return (bool(prefixes) and text.lower().startswith(prefixes)) or runtime_call(text, catalog) is not None


def spacer_pattern(owner, catalog):
    """The catalogued spacer pattern (e.g. L_URES_*) matching BLOCK.ITEM, or ''.

    A pattern without a dot matches the item name in any block; with a dot it
    matches the whole owner. Only the catalog decides, never a guess from a name.
    """
    owner = str(owner or '').upper()
    item = owner.rsplit('.', 1)[-1]
    for pattern in catalog.get('spacer_items', ()):
        if fnmatch.fnmatchcase(owner if '.' in pattern else item, pattern):
            return pattern
    return ''


def normal(text):
    return re.sub(r'\s+', ' ', LINE_ESCAPES.sub(' ', str(text or ''))).strip().rstrip('.:!').strip().casefold()


def statements(body):
    """Split trigger text into bare statements; comments and block keywords go."""
    from .plsql_passthrough import scan
    from .plsql import Unsupported
    try:
        tokens = scan(LINE_ESCAPES.sub('\n', str(body or '')))
    except Unsupported:
        return [str(body)]  # never classify malformed text as empty/framework
    parts, current = [], []
    for kind, value, *_ in tokens:
        if kind == 'comment':
            current.append(' ')
        elif kind == 'op' and value == ';':
            parts.append(''.join(current)); current = []
        else:
            current.append(value)
    parts.append(''.join(current))
    result = []
    for raw in parts:
        statement = re.sub(r'\s+', ' ', raw).strip()
        statement = re.sub(r'^(?:begin\s+)+', '', statement, flags=re.I).strip()
        if statement and statement.lower() not in {'begin', 'end', 'null'}:
            result.append(statement)
    return result


def pure_argument(node):
    """No function evaluation may disappear with a framework call.

    A bare identifier can itself be a zero-argument PL/SQL function. Only known
    Forms constants, field references and literals are safe to discard here.
    """
    if node['op'] in {'literal', 'ref'}:
        return True
    if node['op'] == 'symbol':
        return node['name'] in forms_runtime.CONSTANTS
    if node['op'] == 'function':  # Forms getters and STANDARD functions evaluate nothing in the database
        return node['name'] in forms_runtime.PURE_FUNCTIONS and all(pure_argument(arg) for arg in node['args'])
    if node['op'] == 'binary':
        return pure_argument(node['left']) and pure_argument(node['right'])
    return node['op'] == 'unary' and node['operator'] in {'+', '-'} and node['value']['op'] == 'literal'


def safe_framework_node(node, catalog):
    return (node['op'] == 'call' and framework_name(node['name'], catalog)
            and all(pure_argument(arg) for arg in node['args']))


def explicit_noop(body):
    from .plsql import parse, flatten, Unsupported
    try:
        nodes = parse(body)
        return bool(nodes) and all(node['op'] == 'noop' for node in flatten(nodes))
    except Unsupported:
        return False


def framework_call(statement, catalog):
    """A standalone call to a catalogued framework routine, arguments ignored.

    A generated handler such as `exception when others then cgte$other_exceptions`
    counts too: the handler itself is framework code.
    """
    statement = re.sub(r'^exception\s+when\s+[\w$#,\s]+?\s+then\s+', '', statement, flags=re.I)
    # A framework package's state (qms$nav.nav_opening_wnd := FALSE) is plumbing as well, if the value evaluates nothing.
    assignment = re.fullmatch(r'\s*([A-Za-z][\w$#]*(?:\.[A-Za-z][\w$#]*)+)\s*:=\s*(.+?)\s*;?\s*', statement, flags=re.S)
    if assignment:
        statement = assignment.group(1) + '(' + assignment.group(2) + ')'
    from .plsql import parse, Unsupported
    try:
        nodes = parse(statement.rstrip(';') + ';')
    except Unsupported:
        return False
    return len(nodes) == 1 and safe_framework_node(nodes[0], catalog)


def called(body):
    """Every routine name called in the body, lowercased, keywords excluded."""
    names = []
    for statement in statements(body):
        plain = re.sub(LITERAL, "''", statement)
        for match in re.finditer(r'([A-Za-z][\w$#]*(?:\.[A-Za-z][\w$#]*)?)\s*\(', plain):
            if match.group(1).lower() not in KEYWORDS: names.append(match.group(1).lower())
        # A procedure without arguments is a bare word: at the start of a
        # statement or right after THEN / ELSE / LOOP.
        for fragment in re.split(r'\b(?:then|else|elsif|loop|begin)\b', plain, flags=re.I):
            bare = re.fullmatch(r'\s*([A-Za-z][\w$#]*(?:\.[A-Za-z][\w$#]*)?)\s*', fragment)
            if bare and bare.group(1).lower() not in KEYWORDS: names.append(bare.group(1).lower())
    return names


def classify(body, catalog):
    """framework / mixed / own / empty, with the non-framework calls kept."""
    from .plsql import parse, Unsupported
    def harmless(nodes):
        for node in nodes:
            if node['op'] == 'block':
                if not harmless(node['body']) or any(not harmless(h['body']) for h in node.get('handlers', [])):
                    return False
            elif node['op'] != 'noop' and not safe_framework_node(node, catalog):
                return False
        return True
    parts = statements(body)
    own = sorted({n for n in called(body) if not framework_name(n, catalog)})
    plumbing = [s for s in parts if framework_call(s, catalog)]
    try:
        if harmless(parse(body)):
            return ('framework' if plumbing else 'empty'), []
    except Unsupported:
        pass
    return ('mixed' if plumbing else 'own'), own


def runtime_calls(body, catalog):
    """Catalogued Forms-runtime calls of a trigger: [{'call', 'pattern', 'reason'}], for the evidence."""
    from .plsql import parse, Unsupported
    found = []
    def walk(nodes):
        for node in nodes:
            if node['op'] == 'call':
                hit = runtime_call(node['name'], catalog)
                entry = {'call': node['name'], 'pattern': hit[0], 'reason': hit[1]} if hit else None
                if entry and entry not in found:
                    found.append(entry)
            elif node['op'] == 'block':
                walk(node['body'])
                for handler in node.get('handlers', []):
                    walk(handler['body'])
            elif node['op'] == 'if':
                for branch in node['branches']:
                    walk(branch['body'])
                walk(node['else'])
    try:
        walk(parse(body))
    except Unsupported:
        pass
    return found


def forms_calls(body, catalog):
    """A trigger made only of Forms-runtime calls: [{'name', 'kind'}], or None.

    Every statement must be one call - a Forms built-in, a routine of a Forms built-in
    package or a catalogued framework/Forms-runtime routine - with pure arguments
    (literals, items, Forms constants, Forms getters). No condition, assignment,
    RAISE or SQL: nothing of it could run in the database, and there is no database
    work that a backend endpoint would have to do. kind: catalog / ui / data / server.
    """
    from .plsql import parse, Unsupported
    calls = []
    def walk(nodes):
        for node in nodes:
            if node['op'] == 'noop':
                continue
            if node['op'] == 'block':
                if not walk(node['body']) or not all(walk(h['body']) for h in node.get('handlers', [])):
                    return False
                continue
            if node['op'] != 'call' or not all(pure_argument(arg) for arg in node['args']):
                return False
            if framework_name(node['name'], catalog):
                calls.append({'name': node['name'], 'kind': 'catalog'})
                continue
            found = forms_runtime.builtin(node['name'])
            if not found:
                return False
            calls.append({'name': node['name'], 'kind': found[0]})
        return True
    try:
        return calls if walk(parse(body)) and calls else None
    except Unsupported:
        return None


def date_picker(body, catalog):
    """True when the list key only hands over to the catalogued calendar routine."""
    parts = [s for s in statements(body)]
    if not parts or not catalog['date_picker_calls']:
        return False
    for statement in parts:
        name = re.fullmatch(r'([A-Za-z][\w$#]*(?:\.[A-Za-z][\w$#]*)?)\s*(?:\(\s*\))?', statement)
        if not name or name.group(1).lower() not in catalog['date_picker_calls']:
            return False
    return True


def action_steps(body, catalog, model=None, block=None, item=None):
    """Translate a button trigger into Forms built-in steps, or return None.

    Framework dispatch calls are set aside and listed. Every other statement
    must be a recognised built-in with literal arguments; a condition, a
    variable, a custom procedure or an exception handler means manual work.
    DO_KEY requires the same built-in fallback, without a source KEY override.
    """
    steps, plumbing = [], []
    block = block.upper() if block else None
    item = item.upper() if item else None
    for statement in statements(body):
        if framework_call(statement, catalog):
            plumbing.append(statement)
            continue
        lowered = statement.lower()
        simple = re.fullmatch(r"([a-z_]+)(?:\s*\(\s*" + MODES + r"\s*\))?", lowered)
        if simple and simple.group(1) in SIMPLE:
            op = SIMPLE[simple.group(1)]
            steps.append({'op': op})
            # A navigation/data step can move focus. A following DO_KEY may
            # no longer dispatch the item that an earlier GO_ITEM selected.
            item = None
            if op in {'nextBlock', 'previousBlock', 'clearForm', 'exitForm'}:
                block = None
            continue
        key = re.fullmatch(r"do_key\s*\(\s*'([a-z_]+)'\s*\)", lowered)
        if key and key.group(1) in SIMPLE:
            from .forms_keys import KEY_EVENTS, overrides
            builtin = key.group(1).upper()
            if builtin not in KEY_EVENTS:
                return None
            if model is not None:
                from .xmlmodel import get
                if any(get(u, 'Name').upper() == 'DO_KEY' for u in model['program_units']):
                    return None
                keys = overrides(model, builtin, block, item)
                native_calendar = (builtin == 'LIST_VALUES' and item and all(
                    t['block'] == block and t['item'] == item and date_picker(t['source'], catalog) for t in keys))
                if keys and not native_calendar:
                    return None
            steps.append({'op': SIMPLE[key.group(1)]})
            if builtin != 'LIST_VALUES':
                block, item = None, None
            continue
        named = re.fullmatch(r'([a-z_]+)\s*\(\s*' + LITERAL + r'\s*(?:,\s*' + MODES + r'\s*)?\)', statement, flags=re.I)
        if named and named.group(1).lower() in NAMED:
            op, field = NAMED[named.group(1).lower()]
            target = named.group(2).replace("''", "'")
            steps.append({'op': op, field: target})
            if op == 'goBlock':
                block, item = target.upper(), None
            elif op == 'goItem':
                reference = target.upper().split('.', 1)
                if len(reference) == 2:
                    block, item = reference
                else:
                    item = reference[0]
            continue
        return None
    return {'steps': steps, 'framework': plumbing} if steps else None


def empty_hint(hint, catalog):
    """A hint that is a template whose subject was never filled in.

    Two independent signals: a doubled space left by an empty substitution, or
    a catalogued template text such as 'Adja meg a(z) értékét'.
    """
    raw = LINE_ESCAPES.sub(' ', str(hint or ''))
    if not raw.strip():
        return False
    variants = {raw}
    try:  # frmf2xml sometimes writes UTF-8 bytes decoded as CP1250
        variants.add(raw.encode('cp1250').decode('utf-8'))
    except UnicodeError:
        pass
    return any(re.search(r'\S {2,}\S', v.strip()) or normal(v) in catalog['empty_hint_templates'] for v in variants)


def oracle_sql(sql):
    """Oracle-specific constructs worth knowing about before a database change."""
    text = re.sub(LITERAL, "''", LINE_ESCAPES.sub(' ', str(sql or '')))
    return [{'construct': label, 'alternative': alternative} for label, pattern, alternative in SQL_CONSTRUCTS if pattern.search(text)]


DAY_CELL = re.compile(r'[A-Z_$#]*?\d{1,2}')


def calendar_block(name, item_names, catalog) -> bool:
    """The Headstart calendar helper (CALENDAR with a cell per day): the date field's own picker replaces it."""
    if str(name).upper() not in catalog.get('blocks', {}) or str(name).upper() != 'CALENDAR':
        return False
    return sum(1 for n in item_names if DAY_CELL.fullmatch(str(n).upper())) >= 28


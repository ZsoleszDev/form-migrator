"""Forms PL/SQL run as written: an anonymous Oracle block with the Forms items bound.

The trigger body is not reinterpreted. Only what exists in Forms but not in the
database is replaced: :BLOCK.ITEM references become local variables filled from
JDBC binds and written back; MESSAGE collects text for the caller;
FORM_TRIGGER_FAILURE is a local exception reported as ORA-20999; catalogued
framework calls become NULL (RAISE inside an exception handler); local program
units are nested declarations. Every other Forms built-in is refused with the
reason, so an unsupported trigger never runs half-translated.
SYSTEM.MODE and its static NAME_IN getter always evaluate to NORMAL in Angular.
"""
from __future__ import annotations

import hashlib
import re

from . import forms_runtime
from .common import decode_line_escapes
from .forms_context import NORMAL_MODE, NORMAL_MODE_NOTE, normal_mode_reference
from .plsql import Unsupported, adapter_error, where

IDENT = re.compile(r'[A-Za-z][A-Za-z0-9_$#]*')
BIND = re.compile(r':[A-Za-z][A-Za-z0-9_$#]*(?:\.[A-Za-z][A-Za-z0-9_$#]*)?')
NUMBER = re.compile(r'\d+(?:\.\d+)?(?:[eE][+-]?\d+)?')
Q_CLOSE = {'[': ']', '{': '}', '(': ')', '<': '>'}
# Forms-only names that also appear outside a call (functions, status values, constants).
FORMS_ONLY = {'FORM_SUCCESS', 'FORM_FAILURE', 'FORM_FATAL', 'ERROR_CODE', 'ERROR_TEXT', 'ERROR_TYPE',
              'MESSAGE_CODE', 'MESSAGE_TEXT', 'MESSAGE_TYPE', 'DBMS_ERROR_CODE', 'DBMS_ERROR_TEXT',
              'PROPERTY_TRUE', 'PROPERTY_FALSE', 'NO_VALIDATE', 'DO_COMMIT', 'NO_COMMIT', 'ID_NULL', 'NAME_IN', 'COPY'}
SQL_TYPES = {'text': 'VARCHAR2(32767)', 'number': 'NUMBER', 'datetime': 'DATE'}
# Statement keywords that are not procedure calls.
KEYWORDS = {'NULL', 'RAISE', 'RETURN', 'EXIT', 'CONTINUE', 'COMMIT', 'ROLLBACK', 'SAVEPOINT', 'BEGIN', 'END', 'ELSE',
            'ELSIF', 'LOOP', 'IF', 'CASE', 'WHEN', 'THEN', 'OPEN', 'CLOSE', 'FETCH', 'FOR', 'FORALL', 'WHILE', 'GOTO',
            'DECLARE', 'EXCEPTION', 'INSERT', 'UPDATE', 'DELETE', 'SELECT', 'MERGE', 'LOCK', 'SET', 'WITH', 'PRAGMA',
            'EXECUTE', 'PIPE', 'PROCEDURE', 'FUNCTION', 'CURSOR', 'TYPE', 'SUBTYPE', 'IS', 'AS'}
# Oracle-supplied packages and procedures: present in every database.
STANDARD = ('DBMS_', 'UTL_', 'SYS.', 'OWA_', 'HTP.', 'HTF.', 'APEX_')
STANDARD_CALLS = {'RAISE_APPLICATION_ERROR'}
STATEMENT_START = {';', 'BEGIN', 'THEN', 'ELSE', 'LOOP', 'DECLARE', 'IS', 'AS', 'EXCEPTION'}


def scan(text: str) -> list[tuple[str, str, int, int]]:
    """(kind, text, start, end) tokens; comments and whitespace included, nothing dropped."""
    tokens, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            j = i
            while j < n and text[j].isspace(): j += 1
            tokens.append(('ws', text[i:j], i, j)); i = j; continue
        if text.startswith('--', i):
            ends = [k for k in (text.find('\n', i), text.find('\r', i)) if k >= 0]  # CR-only line breaks end it too
            j = min(ends) if ends else n
            tokens.append(('comment', text[i:j], i, j)); i = j; continue
        if text.startswith('/*', i):
            j = text.find('*/', i + 2)
            if j < 0: raise Unsupported('Lezáratlan /* megjegyzés' + where(text, i) + '.')
            tokens.append(('comment', text[i:j + 2], i, j + 2)); i = j + 2; continue
        if c in 'qQnN' and re.match(r"[nN]?[qQ]'", text[i:i + 3]):
            start = i
            i += 3 if c in 'nN' else 2
            if i >= n: raise Unsupported('Hibás q-literál.')
            close = Q_CLOSE.get(text[i], text[i])
            j = text.find(close + "'", i + 1)
            if j < 0: raise Unsupported('Lezáratlan q-literál' + where(text, start) + '.')
            tokens.append(('string', text[start:j + 2], start, j + 2)); i = j + 2; continue
        if c == "'" or (c in 'nN' and text[i + 1:i + 2] == "'"):
            start = i; i += 2 if c != "'" else 1
            while True:
                j = text.find("'", i)
                if j < 0: raise Unsupported('Lezáratlan szöveg-literál' + where(text, start) + '.')
                if text[j + 1:j + 2] == "'": i = j + 2; continue
                i = j + 1; break
            tokens.append(('string', text[start:i], start, i)); continue
        if c == '"':
            j = text.find('"', i + 1)
            if j < 0: raise Unsupported('Lezáratlan idézett azonosító' + where(text, i) + '.')
            tokens.append(('ident', text[i:j + 1], i, j + 1)); i = j + 1; continue
        if c == ':' and text[i + 1:i + 2] != '=':
            m = BIND.match(text, i)
            if m:
                tokens.append(('bind', m.group(), i, m.end())); i = m.end(); continue
        m = IDENT.match(text, i)
        if m:
            tokens.append(('ident', m.group(), i, m.end())); i = m.end(); continue
        m = NUMBER.match(text, i)
        if m:
            tokens.append(('number', m.group(), i, m.end())); i = m.end(); continue
        width = 2 if text[i:i + 2] in {':=', '=>', '..', '||', '<>', '!=', '<=', '>=', '**'} else 1
        tokens.append(('op', text[i:i + width], i, i + width)); i += width
    return tokens


def significant(tokens):
    return [t for t in tokens if t[0] not in {'ws', 'comment'}]


def normal_mode_sql(text: str, local_units=()) -> str:
    """Replace mode reads as SQL literals, preserving comments and string data."""
    tokens = scan(text)
    sig = significant(tokens)
    edits, skip_until = {}, 0
    # The existing write detector also recognises SELECT/FETCH/RETURNING INTO.
    marker_index = 0
    marker = 'nv_0000000000'
    while marker in text.lower():
        marker_index += 1
        marker = 'nv_' + format(marker_index, '010x')
    visible = ''.join(marker if t[0] == 'bind' and normal_mode_reference(t[1]) else t[1]
                      for t in tokens if t[0] not in {'string', 'comment'})
    if any(t[0] == 'bind' and normal_mode_reference(t[1]) for t in tokens) and marker in assigned_vars(visible):
        raise Unsupported('SYSTEM.MODE csak olvasható; a migrált felületen mindig NORMAL.')
    for index, token in enumerate(sig):
        if token[2] < skip_until:
            continue
        if token[0] == 'bind' and normal_mode_reference(token[1]):
            edits[token[2]] = (token[3], "'" + NORMAL_MODE + "'")
        elif (token[0] == 'ident' and token[1].upper() == 'NAME_IN' and 'NAME_IN' not in local_units
              and (index == 0 or sig[index - 1][1] != '.') and index + 3 < len(sig)):
            opening, argument, closing = sig[index + 1:index + 4]
            if (opening[1] == '(' and argument[0] == 'string' and argument[1].startswith("'")
                    and closing[1] == ')' and normal_mode_reference(argument[1][1:-1].replace("''", "'"))):
                comments = ''.join(' ' + t[1] + ('\n' if t[1].startswith('--') else ' ')
                                   for t in tokens if t[0] == 'comment' and token[2] < t[2] < closing[3])
                edits[token[2]] = (closing[3], "'" + NORMAL_MODE + "'" + comments)
                skip_until = closing[3]
    parts, last = [], 0
    for start, (end, replacement) in sorted(edits.items()):
        parts += [text[last:start], replacement]
        last = end
    return ''.join(parts) + text[last:]


def validate_structure(text):
    """Reject visibly truncated blocks; Oracle remains the full PL/SQL compiler."""
    tokens = significant(scan(text))
    stack, skip = [], -1
    for index, token in enumerate(tokens):
        if index == skip or token[0] != 'ident':
            continue
        word = token[1].upper()
        if word in {'BEGIN', 'IF', 'LOOP', 'CASE'}:
            stack.append(word)
        elif word == 'END':
            following = tokens[index + 1][1].upper() if index + 1 < len(tokens) else ''
            expected = following if following in {'IF', 'LOOP', 'CASE'} else None
            if not stack or (expected and stack[-1] != expected) or (not expected and stack[-1] not in {'BEGIN', 'CASE'}):
                raise Unsupported('Hibás PL/SQL blokkszerkezet: END ' + following)
            stack.pop()
            if expected:
                skip = index + 1
    if stack:
        raise Unsupported('Lezáratlan PL/SQL blokk: ' + ', '.join(stack))
    if tokens and tokens[0][1].upper() == 'DECLARE' and not any(t[1].upper() == 'BEGIN' for t in tokens):
        raise Unsupported('DECLARE szakasz BEGIN/END nélkül.')


class Rewriter:
    """Rewrites one PL/SQL text; shared state collects binds and needs."""

    def __init__(self, block, items, catalog_prefixes, *, other_blocks, parameters, transaction, units, procedures=None,
                 runtime_calls=(), ui=False, form='', trigger_item=None, tail_units=frozenset(), members=frozenset(),
                 key_overrides=frozenset(), key_triggers=None, commit_points=False, cursor_on_item=True, block_info=None):
        self.block, self.items, self.prefixes = block, items, catalog_prefixes
        self.block_info = block_info or {}  # static block properties of the form (GET_BLOCK_PROPERTY folding)
        # Framework prefixes and catalogued Forms-runtime routines: both exist only in Forms.
        self.catalog = {'call_prefixes': tuple(catalog_prefixes or ()), 'runtime_calls': tuple(runtime_calls or ())}
        self.procedures = {k.upper(): v for k, v in (procedures or {}).items()}
        self.out_args = []  # argument texts written by a known OUT parameter
        self.unresolved = []  # external routines without a signature: resolved by the database
        self.other_blocks, self.parameters, self.transaction, self.units = other_blocks, parameters, transaction, units
        self.binds, self.needs, self.notes, self.used_units = {}, set(), [], []
        # Forms runtime emulation (buttons, start-up code): built-ins become screen commands (forms_emulation).
        self.ui, self.form, self.trigger_item, self.tail_units = ui, form, trigger_item, set(tail_units)
        self.members = set(members)  # members of the local packages: local declarations, called unqualified inside
        self.key_overrides = set(key_overrides)  # DO_KEY targets with an own KEY-* trigger: its code would be lost
        self.args_until = -1  # inside the argument list of an emulated built-in: Forms constants become text
        self.commands = []  # the emulated built-ins, for the evidence
        # DO_KEY with an own KEY-* trigger: its code is embedded (KEY event -> candidate triggers, see rules.key_triggers).
        self.key_triggers = key_triggers or {}
        self.key_stack = frozenset()  # the KEY triggers being embedded: a DO_KEY of one of them would recurse
        self.cursor_on_item = cursor_on_item  # the button takes the cursor (Mouse Navigate): its KEY triggers apply
        # COMMIT_FORM followed by more code: a commit point the request stops at and resumes after (commit_points).
        self.commit_points, self.points = commit_points, 0
        self.not_tail = False  # embedded KEY trigger code that more code follows: no screen command may be last here

    def bind(self, token: str) -> str:
        name = token[1:].upper()
        head = name.split('.')[0]
        if head == 'SYSTEM' and self.ui:
            from .forms_emulation import SYSTEM_FIXED, SYSTEM_REQUEST
            variable = name.split('.', 1)[1] if '.' in name else ''
            if variable in SYSTEM_FIXED:
                fixed = {'TRIGGER_BLOCK': self.block, 'TRIGGER_ITEM': self.trigger_item,
                         'CURRENT_FORM': self.form, 'TRIGGER_FORM': self.form}[variable]
                if fixed:
                    return "'" + fixed.replace("'", "''") + "'"
            if variable in SYSTEM_REQUEST or variable in SYSTEM_FIXED:
                # The screen sends its current context (cursor block, statuses...) with every request.
                if name not in self.binds:
                    self.binds[name] = {'source': name, 'block': 'SYSTEM', 'item': variable, 'type': 'text', 'parameter': True}
                return self.var(name)
            return self.input_bind(name, 'Forms rendszerváltozó, a webes képernyő nem adja át')
        if head == 'SYSTEM' and name == 'SYSTEM.TRIGGER_BLOCK' and self.block:
            return "'" + self.block + "'"  # a block's trigger (a form-level one's copy too) knows its block
        if head == 'SYSTEM':
            return self.input_bind(name, 'Forms rendszerváltozó, az adatbázisban nincs megfelelője')
        if head in {'GLOBAL', 'PARAMETER'}:
            if not self.parameters:
                return self.input_bind(name, 'szerveroldali kontextus, adatműveleti triggerben nem érhető el')
            key = name
            if key not in self.binds:
                self.binds[key] = {'source': key, 'block': head, 'item': name.split('.', 1)[1], 'type': 'text', 'parameter': True}
            return self.var(key)
        if '.' not in name:
            if self.block and name in self.items.get(self.block, {}):
                name = self.block + '.' + name
            else:
                return self.input_bind(name, 'blokk nélküli mezőhivatkozás, nem egyértelmű')
        block, item = name.split('.', 1)
        info = self.items.get(block, {}).get(item)
        if info is None:
            return self.input_bind(name, 'ismeretlen mező, nincs a formban')
        if info['type'] not in SQL_TYPES:
            raise Unsupported('Nem támogatott mezőtípus (' + token + '): ' + str(info['type']))
        if block != self.block and not self.other_blocks:
            return self.input_bind(name, 'másik blokk mezője, a rekordban nem érhető el', info['type'])
        if name not in self.binds:
            self.binds[name] = {'source': name, 'block': block, 'item': item, 'type': info['type'], 'parameter': False}
        return self.var(name)

    def input_bind(self, name: str, reason: str, typ: str = 'text') -> str:
        """Data the migrated code has no source for: a variable the developer fills (developer_inputs)."""
        if name not in self.binds:
            block, _, item = name.rpartition('.')
            self.binds[name] = {'source': name, 'block': block, 'item': item, 'type': typ, 'parameter': False, 'input': reason}
        return self.var(name)

    @staticmethod
    def var(key: str) -> str:
        # Fixed per item (not per position): a program unit reads the same in every block.
        return 'nv_' + hashlib.sha1(key.encode('utf-8')).hexdigest()[:10]

    def child(self):
        """A rewriter for program units: no block context, same binds and needs."""
        unit = Rewriter(None, self.items, self.prefixes, other_blocks=True, parameters=True, transaction=False,
                        units=self.units, procedures=self.procedures, runtime_calls=self.catalog['runtime_calls'],
                        ui=self.ui, form=self.form, tail_units=self.tail_units, block_info=self.block_info)
        return unit

    def plumbing(self, name: str) -> bool:
        from .framework import framework_name
        return framework_name(name, self.catalog)

    def discarded(self, name: str, handler: bool) -> str:
        """The evidence note of a Forms-only call replaced in the database block."""
        from .framework import runtime_call
        hit = runtime_call(name, self.catalog)
        target = 'RAISE (kivételkezelő)' if handler else 'NULL'
        return name.upper() + ' -> ' + target + ' (Forms-futtatókörnyezet: ' + hit[0] + ')' if hit else None

    def reraises(self, name, sig, k):
        from .framework import runtime_call
        # Removing a calendar/UI notification must not turn a handled exception
        # into a failure. Legacy error dispatchers still propagate real failures.
        return runtime_call(name, self.catalog) is None and self.in_handler(sig, k)

    @staticmethod
    def qualified(sig, k):
        """PKG.PROC / OWNER.PKG.PROC starting at sig[k], and the token after it."""
        parts, j = [sig[k][1].upper()], k
        while j + 2 < len(sig) and sig[j + 1][1] == '.' and sig[j + 2][0] == 'ident':
            parts.append(sig[j + 2][1].upper()); j += 2
        return '.'.join(parts), (sig[j + 1][1] if j + 1 < len(sig) else '')

    def rewrite(self, text: str) -> str:
        adapted = normal_mode_sql(text, self.units)
        if adapted != text and NORMAL_MODE_NOTE not in self.notes:
            self.notes.append(NORMAL_MODE_NOTE)
        text = adapted
        tokens = scan(text)
        sig = significant(tokens)
        replace = {}  # start offset -> (end offset, replacement)
        skip_until = 0
        protected = []  # (start, end): already replaced pieces inside an expression (GET_BLOCK_PROPERTY's property)
        for k, token in enumerate(sig):
            if token[2] < skip_until or any(a <= token[2] < b for a, b in protected):
                continue  # no binds/edits inside an already replaced statement
            kind, value = token[0], token[1]
            previous = sig[k - 1][1].upper() if k else ';'
            after = sig[k + 1][1] if k + 1 < len(sig) else ''
            if kind == 'bind':
                replace[token[2]] = (token[3], self.bind(value))
                continue
            if kind != 'ident' or previous == '.':
                continue
            word = value.upper()
            at_start = previous in STATEMENT_START or k == 0
            if self.ui:
                from . import forms_emulation as emu
                if token[2] < self.args_until and word in emu.CONSTANTS and after not in {'(', '.'}:
                    replace[token[2]] = (token[3], "'" + word + "'")  # a Forms constant as an argument: its name
                    continue
                if word in emu.TYPES and k and sig[k - 1][0] == 'ident' and after in {';', ':='}:
                    replace[token[2]] = (token[3], emu.HANDLE_TYPE)  # ALERT, ITEM, PARAMLIST ... handles are names
                    continue
                if word in emu.ALERT_BUTTONS:
                    self.needs.add('alert_buttons')
                    continue
            if after == '.':
                # Qualified name (PKG.PROC, qms$calendar.init, calendar.event): Forms-only plumbing,
                # a Forms built-in package, or a call the database resolves.
                name, following = self.qualified(sig, k)
                if self.plumbing(name):
                    if not at_start:
                        raise Unsupported('Keretrendszeri / Forms-futtatókörnyezeti hívás kifejezésben: ' + name
                                          + ' (az adatbázisban nem futtatható).')
                    end = self.statement_end(sig, k)
                    self.check_discard(text[token[2]:end])
                    handler = self.reraises(name, sig, k)
                    replace[token[2]] = (end, 'RAISE;' if handler else 'NULL;')
                    skip_until = end
                    self.notes.append(self.discarded(name, handler)
                                      or value + '... -> ' + ('RAISE (kivételkezelő)' if handler else 'NULL'))
                    continue
                if self.ui and name in {'WEB.SHOW_DOCUMENT'} and at_start and following == '(':
                    paren = k + 2 * name.count('.') + 1
                    close = self.closing(sig, paren)
                    replace[token[2]] = (sig[paren][3], "frm_cmd('" + name + "', ")
                    self.args_until = max(self.args_until, sig[close][3])
                    self.needs.add('ui')
                    self.commands.append(name)
                    continue
                if forms_runtime.builtin(name):
                    raise Unsupported(forms_runtime.refusal(name))
                if word in self.units and self.units[word]['kind'] == 'package':
                    # A member of a local package: a local declaration of the block (PKG.MEMBER -> MEMBER).
                    replace[token[2]] = (sig[k + 1][3], '')
                    if word not in self.used_units:
                        self.used_units.append(word)
                    continue
                if at_start and word in self.units and following in {'(', ';'}:
                    # A package the export reported without its type: its code is in the form, not in Oracle.
                    raise Unsupported('A(z) ' + word + ' a Forms-modul saját programegysége, itt csomagként hívva ('
                                      + name + '): az adatbázisban nem érhető el.')
                if word not in KEYWORDS and (at_start or following == '('):
                    self.check_call(sig, k, expression=not at_start)
                continue
            if word == 'GET_BLOCK_PROPERTY' and after == '(' and self.block_info:
                folded = self.block_property(sig, k, replace)
                if folded:
                    protected.append(folded)
                    continue
            if word == 'NAME_IN' and after == '(' and not self.ui and 'NAME_IN' not in self.units:
                close = self.closing(sig, k + 1)
                if close == k + 3 and sig[k + 2][0] == 'string' and sig[k + 2][1].startswith("'"):
                    # NAME_IN('BLOCK.ITEM'): the item's value, like :BLOCK.ITEM
                    replace[token[2]] = (sig[close][3], self.bind(':' + sig[k + 2][1][1:-1].replace("''", "'").strip()))
                    skip_until = sig[close][3]
                    continue
                raise Unsupported('NAME_IN számított névvel: az adatbázisban nem oldható fel.')
            if word == 'FORM_TRIGGER_FAILURE':
                self.needs.add('failure'); continue
            if word in {'ACKNOWLEDGE', 'NO_ACKNOWLEDGE'}:
                self.needs.add('acknowledge'); continue
            if word == 'MESSAGE' and after == '(':
                self.needs.add('message'); replace[token[2]] = (token[3], 'frm_msg'); continue
            if self.plumbing(word):
                if not at_start:
                    raise Unsupported('Keretrendszeri függvény kifejezésben: ' + value)
                end = self.statement_end(sig, k)
                self.check_discard(text[token[2]:end])
                handler = self.reraises(word, sig, k)
                replace[token[2]] = (end, 'RAISE;' if handler else 'NULL;')
                skip_until = end
                self.notes.append(self.discarded(word, handler) or value + (' -> RAISE (kivételkezelő)' if handler else ' -> NULL'))
                continue
            if at_start and word in {'COMMIT', 'ROLLBACK', 'SAVEPOINT', 'COMMIT_FORM'} and not (self.ui and word == 'COMMIT_FORM'):
                if word == 'COMMIT' and self.transaction:
                    replace[token[2]] = (self.statement_end(sig, k), 'NULL;')
                    self.notes.append('COMMIT -> a végpont tranzakciója véglegesít')
                    continue
                raise Unsupported('Tranzakcióvezérlés a triggerben: ' + word)
            if word in self.units:
                if word not in self.used_units:
                    self.used_units.append(word)
                if self.ui and at_start and word in self.tail_units:
                    self.check_tail(sig, k, word)  # its EXECUTE_QUERY / CALL_FORM ... runs at the end on the screen
                continue
            if word == 'SET_RECORD_PROPERTY' and at_start and after == '(':
                # Forms idiom (mostly POST-QUERY): keep the record QUERY after filling non-database items. The
                # generated endpoints have no record status to protect, so the call has nothing to do.
                end = self.statement_end(sig, k)
                words = {t[1].upper() for t in sig if token[2] < t[2] < end and t[0] == 'ident'}
                if {'STATUS', 'QUERY_STATUS'} <= words:
                    replace[token[2]] = (end, 'NULL;')
                    skip_until = end
                    self.notes.append('SET_RECORD_PROPERTY(..., STATUS, QUERY_STATUS) -> NULL (a végpontnak nincs rekordállapota)')
                    continue
            if self.ui:
                from .forms_emulation import emulated
                if emulated(word):
                    end = self.emulate(sig, k, word, at_start, after, replace)
                    if end is not None:
                        skip_until = end
                    continue
            from .discovery import FORMS_BUILTINS
            if word in FORMS_BUILTINS or word in FORMS_ONLY:
                raise Unsupported('Forms beépített hívás: ' + word + ' (az adatbázisban nem futtatható).')
            if forms_runtime.builtin(word) and (after == '(' or (at_start and after == ';')):
                # Only in call position: DELETE_RECORD, SET_ITEM_INSTANCE_PROPERTY, BELL ... (a column may be named HELP).
                raise Unsupported(forms_runtime.refusal(word))
            if not at_start and word in self.procedures and after == '(':
                self.check_call(sig, k, expression=True)
            if at_start and word not in KEYWORDS and after in {'(', ';', '.'}:
                self.check_call(sig, k)
        out, last = [], 0
        for start in sorted(replace):
            end, text_new = replace[start]
            out += [text[last:start], text_new]
            last = end
        out.append(text[last:])
        return ''.join(out)

    def block_property(self, sig, k, replace):
        """GET_BLOCK_PROPERTY(block, PROP) of a property the form fixes (QUERY_ALLOWED, DEFAULT_WHERE ...): the value
        from the form; a computed block name is a CASE over the blocks. (start, end) of the property part, or None."""
        close = self.closing(sig, k + 1)
        commas = [i for i in range(k + 2, close) if sig[i][1] == ',' and self.depth(sig, k + 1, i) == 1]
        if len(commas) != 1 or commas[0] + 2 != close or sig[commas[0] + 1][0] != 'ident':
            return None
        prop = sig[commas[0] + 1][1].upper()
        if not all(prop in values for values in self.block_info.values()):
            return None
        quote = lambda value: 'NULL' if value is None else "'" + str(value).replace("'", "''") + "'"
        first = sig[k + 2]
        if commas[0] == k + 3 and first[0] == 'string' and first[1].startswith("'"):
            block = first[1][1:-1].replace("''", "'").strip().upper()
            if block not in self.block_info:
                return None
            replace[sig[k][2]] = (sig[close][3], quote(self.block_info[block][prop]))
            self.notes.append('GET_BLOCK_PROPERTY(' + block + ', ' + prop + ') -> ' + quote(self.block_info[block][prop])
                              + ' (a form statikus beállítása)')
            return sig[k][2], sig[close][3]
        replace[sig[k][2]] = (sig[k + 1][3], '(CASE UPPER(')
        cases = ' '.join("WHEN '" + b + "' THEN " + quote(v[prop]) for b, v in sorted(self.block_info.items()))
        replace[sig[commas[0]][2]] = (sig[close][3], ') ' + cases + ' END)')
        self.notes.append('GET_BLOCK_PROPERTY(..., ' + prop + ') -> CASE a blokkok statikus beállításából')
        return sig[commas[0]][2], sig[close][3]

    @staticmethod
    def closing(sig, k):
        """The index of the ')' that closes the '(' at sig[k]."""
        depth = 0
        for index in range(k, len(sig)):
            if sig[index][1] == '(':
                depth += 1
            elif sig[index][1] == ')':
                depth -= 1
                if not depth:
                    return index
        raise Unsupported('Lezáratlan zárójel: ' + sig[k - 1][1])

    def parameter_bind(self, source: str) -> str:
        """A value of the request context (parameters map), e.g. the alert answers."""
        if source not in self.binds:
            block, item = source.split('.', 1)
            self.binds[source] = {'source': source, 'block': block, 'item': item, 'type': 'text', 'parameter': True}
        return self.var(source)

    def check_tail(self, sig, k, word):
        """A built-in the screen runs after the whole code (EXECUTE_QUERY, COMMIT_FORM, CALL_FORM ...):
        only if nothing that could observe its effect follows it (item reads/writes, SQL)."""
        from . import forms_emulation as emu
        end = self.statement_end(sig, k)
        rest = [t for t in sig if t[2] >= end]
        level, skipping, skip_next = 0, False, False
        for index, token in enumerate(rest):
            kind, upper = token[0], token[1].upper()
            if skip_next:
                skip_next = False
                continue
            if kind == 'ident' and upper == 'END':
                skip_next = index + 1 < len(rest) and rest[index + 1][1].upper() in {'IF', 'CASE', 'LOOP'}
                if level:
                    level -= 1
                else:
                    skipping = False  # out of the enclosing construct: what follows runs after the command
                continue
            if kind == 'ident' and upper in {'IF', 'CASE', 'LOOP', 'BEGIN'}:
                level += 1
            elif kind == 'ident' and level == 0 and upper in {'ELSE', 'ELSIF', 'WHEN', 'EXCEPTION'}:
                skipping = True  # a sibling branch or a handler: never runs after this command
                continue
            if skipping:
                continue
            if kind in {'string', 'number'} or (kind == 'op' and token[1] in {';', '(', ')', ','}):
                continue
            if kind == 'ident' and (upper in emu.TAIL_SAFE or upper in emu.CONSTANTS or self.plumbing(token[1])
                                    or (emu.emulated(upper) and upper not in emu.VALUE_SETTERS and upper != 'NAME_IN')
                                    or upper in self.tail_units):
                continue
            raise Unsupported(word + ' után további adat- vagy mezőművelet következik (' + token[1] + '): a webes képernyő a '
                              + word + ' lépést a kód végén hajtja végre, ezért a sorrend eltérne.')

    def emulate(self, sig, k, word, at_start, after, replace):
        """One Forms built-in replaced by its emulation (forms_emulation). Returns the offset to skip to."""
        from . import forms_emulation as emu
        token = sig[k]
        close = self.closing(sig, k + 1) if after == '(' else None
        literal = (close == k + 3 and sig[k + 2][0] == 'string' and sig[k + 2][1].startswith("'"))
        if word in emu.STATUS:
            if after == '(':
                raise Unsupported(word + ' argumentummal: nem Forms-állapotfüggvény.')
            replace[token[2]] = (token[3], emu.STATUS[word])
            return None
        if word == 'NAME_IN':
            if after == '(' and literal:
                target = sig[k + 2][1][1:-1].replace("''", "'").strip()
                replace[token[2]] = (sig[close][3], self.bind(':' + target))
                return sig[close][3]
            raise Unsupported('NAME_IN számított névvel: a webes képernyőn nem oldható fel.')
        if word == 'GET_APPLICATION_PROPERTY':
            if after == '(' and close == k + 3 and sig[k + 2][0] == 'ident':
                prop = sig[k + 2][1].upper()
                value = emu.APPLICATION_PROPERTIES.get(prop) or (
                    "'" + self.form + "'" if prop in {'CURRENT_FORM_NAME', 'CURRENT_FORM'} and self.form else None)
                if value:
                    replace[token[2]] = (sig[close][3], value)
                    return sig[close][3]
            raise Unsupported('GET_APPLICATION_PROPERTY: csak USERNAME és CURRENT_FORM_NAME érhető el a webes képernyőn.')
        if word in emu.FUNCTIONS or word in emu.GROUP_FUNCTIONS:
            if after != '(' or at_start:
                raise Unsupported(word + ': függvényként használható.')
            if word in emu.GROUP_FUNCTIONS:
                self.needs |= {'group', 'ui'}
                replace[token[2]] = (sig[k + 1][3], "frm_group('" + word + "', ")
            else:
                local = emu.FUNCTIONS[word]
                self.needs.add(local[5:])
                replace[token[2]] = (token[3], local)
                if word == 'SHOW_ALERT':
                    self.needs |= {'alert', 'ui'}
                    self.parameter_bind(emu.ALERT_PARAMETER)
            self.args_until = max(self.args_until, sig[close][3])
            self.commands.append(word)
            return None
        if not at_start:
            raise Unsupported(word + ' kifejezésben: a webes emuláció csak utasításként kezeli.')
        end = self.statement_end(sig, k)  # the whole statement: DO_KEY embedding and commit points replace it
        if word in emu.NOOPS:
            replace[token[2]] = (end, 'NULL;')
            note = word + ' -> NULL (a webes képernyőn nincs teendő)'
            if note not in self.notes:
                self.notes.append(note)
            return end
        if word == 'FORMS_DDL':
            if literal and re.match(r"'\s*ALTER\s+SESSION\b", sig[k + 2][1], re.I):
                replace[token[2]] = (end, 'NULL;')
                self.notes.append('FORMS_DDL(ALTER SESSION ...) -> NULL: a munkamenet-beállítás az adatforrás feladata '
                                  '(például connection-init-sql): ' + ' '.join(sig[k + 2][1].split()))
                return end
            raise Unsupported('FORMS_DDL nem munkamenet-beállítással: a dinamikus SQL kézi átültetést igényel.')
        if word in emu.ALERT_SETTERS:
            if after != '(':
                raise Unsupported(word + ' argumentum nélkül.')
            self.needs |= {'alert', 'ui'}
            self.parameter_bind(emu.ALERT_PARAMETER)
            replace[token[2]] = (token[3], 'frm_alert_prop')
            self.args_until = max(self.args_until, sig[close][3])
            return None
        if word in emu.VALUE_SETTERS:
            if after != '(':
                raise Unsupported(word + ' argumentum nélkül.')
            commas = [i for i in range(k + 2, close) if sig[i][1] == ',' and self.depth(sig, k + 1, i) == 1]
            target = commas[0] + 1 if len(commas) == 1 else None
            if target is None or target + 1 != close or sig[target][0] != 'string':
                raise Unsupported(word + ': a cél csak szó szerinti mezőnév lehet (BLOKK.MEZŐ vagy GLOBAL.NÉV).')
            name = sig[target][1][1:-1].replace("''", "'").strip()
            replace[sig[target][2]] = (sig[target][3], self.bind(':' + name))
            replace[token[2]] = (token[3], emu.VALUE_SETTERS[word])
            self.out_args.append(':' + name.upper())
            self.needs.add('copy' if word == 'COPY' else 'default_value')
            return None
        if word == 'SET_BLOCK_PROPERTY' and after == '(':
            prop = sig[k + 4][1].upper() if k + 4 < close else ''
            if prop in emu.QUERY_PROPERTIES:
                raise Unsupported('SET_BLOCK_PROPERTY ' + prop + ': a blokk lekérdezését/DML-jét módosítja, ezt a végpontnak kell követnie.')
        key = None
        if word == 'DO_KEY':
            key = sig[k + 2][1][1:-1].upper() if literal else None
            from .forms_keys import KEY_EVENTS
            if key is not None and key in self.key_overrides:
                target = self.key_target(key)
                if target is not None:
                    # Forms runs the KEY trigger's own code: it is embedded where DO_KEY stands.
                    replace[token[2]] = (end, self.inline_key(key, target, sig, k))
                    return end
            if key is None or key in self.key_overrides or key not in KEY_EVENTS:
                raise Unsupported('Nem támogatott UI eseménylánc: DO_KEY(' + (repr(key) if key else 'dinamikus argumentum') + ') → '
                                  + KEY_EVENTS.get(key or '', 'dinamikus/ismeretlen KEY esemény')
                                  + ': a saját key-trigger logikáját át kell ültetni.')
        if word in emu.TAIL_COMMANDS:
            reason = self.tail_reason(sig, k, word)
            if reason:
                if self.commit_points and (word == 'COMMIT_FORM' or key == 'COMMIT_FORM'):
                    # More code follows the commit: a commit point (commit_points.transform numbers it).
                    from .commit_points import PLACEHOLDER
                    replace[token[2]] = (end, PLACEHOLDER + ';')
                    self.points += 1
                    self.needs.add('ui')
                    self.commands.append('COMMIT_FORM')
                    return end
                if self.commit_points and key is None and word in emu.SCREEN_STEPS:
                    # A screen step followed by more code: a screen point; the screen carries out the step,
                    # then the code resumes after it with the screen's new values.
                    from .commit_points import screen_placeholder
                    replace[token[2]] = (end, screen_placeholder(word) + ';')
                    self.points += 1
                    self.needs.add('ui')
                    self.commands.append(word)
                    return end
                raise Unsupported(reason)
        self.needs.add('ui')
        self.commands.append(word)
        if after == '(' and close == k + 2:
            replace[token[2]] = (sig[close][3], "frm_cmd('" + word + "')")
            return sig[close][3]
        if after == '(':
            replace[token[2]] = (sig[k + 1][3], "frm_cmd('" + word + "', ")
            self.args_until = max(self.args_until, sig[close][3])
            return None
        replace[token[2]] = (token[3], "frm_cmd('" + word + "')")
        return None

    def tail_reason(self, sig, k, word):
        """Why the built-in at sig[k] cannot be the screen's last step, or None."""
        if self.not_tail:
            return (word + ' a DO_KEY-val beágyazott KEY-trigger kódjában: a DO_KEY után további kód következik, '
                    'a webes képernyő a lépést csak a kód végén tudná végrehajtani.')
        try:
            self.check_tail(sig, k, word)
        except Unsupported as exc:
            return str(exc)
        return None

    def key_target(self, key):
        """The KEY trigger DO_KEY(key) runs: the button item's, its block's, else the form's (Forms key scope)."""
        from .forms_keys import KEY_EVENTS
        event = KEY_EVENTS[key]
        candidates = self.key_triggers.get(event, [])
        if not candidates:
            return None
        # The cursor is on the button only if nothing moved it before DO_KEY (GO_ITEM, a navigating local unit ...).
        on_button = self.cursor_on_item and not self.navigated()
        item = self.trigger_item if on_button else None
        own = [c for c in candidates if item and c['item'] and c['block'] + '.' + c['item'] == item]
        block = [c for c in candidates if c['block'] and not c['item'] and self.block and on_button and c['block'] == self.block]
        form = [c for c in candidates if not c['block']]
        chosen = (own or block or form or [None])[0]
        scoped = own + block + form
        if chosen is None or (len(candidates) > len(scoped) and not item):
            raise Unsupported('Nem támogatott UI eseménylánc: DO_KEY(' + repr(key) + ') → ' + event + ': több blokkban is van '
                              'saját ' + event + ' trigger, és hogy melyik fut, a kurzor helyétől függ.')
        if chosen.get('hierarchy') in {'BEFORE', 'AFTER'}:
            raise Unsupported('Nem támogatott UI eseménylánc: DO_KEY(' + repr(key) + ') → ' + chosen['id']
                              + ' (Execution Hierarchy = ' + chosen['hierarchy'] + '): a szülőszintű trigger is fut, kézi átültetés.')
        return chosen

    def navigated(self) -> bool:
        """Did the code before this point move the cursor (navigation built-in, or a local unit that may)?"""
        from .forms_emulation import NAVIGATION
        if NAVIGATION & set(self.commands):
            return True
        for name in self.used_units:
            unit = self.units.get(name, {})
            try:
                words = {t[1].upper() for t in significant(scan(decode_line_escapes(unit.get('text') or ''))) if t[0] == 'ident'}
            except Unsupported:
                return True
            if words & NAVIGATION:
                return True
        return False

    def inline_key(self, key, target, sig, k):
        """The KEY trigger's code as a nested block, rewritten in its own block context."""
        if key in self.key_stack:
            raise Unsupported('DO_KEY(' + repr(key) + ') a saját ' + target['event'] + ' triggeréből: végtelen hívás.')
        sub = Rewriter(target['block'] or None, self.items, self.prefixes, other_blocks=True, parameters=self.parameters,
                       transaction=self.transaction, units=self.units, procedures=self.procedures,
                       runtime_calls=self.catalog['runtime_calls'], ui=self.ui, form=self.form, trigger_item=self.trigger_item,
                       tail_units=self.tail_units, members=self.members, key_overrides=self.key_overrides,
                       key_triggers=self.key_triggers, commit_points=self.commit_points, cursor_on_item=self.cursor_on_item)
        sub.key_stack = self.key_stack | {key}
        sub.not_tail = self.not_tail or self.tail_reason(sig, k, 'DO_KEY') is not None
        source = decode_line_escapes(target['source']).strip().rstrip('/').strip()
        validate_structure(source)
        text = sub.rewrite(source).strip()
        from .forms_emulation import NOT_FROM_BUTTON
        lost = [c for c in sub.commands if c in NOT_FROM_BUTTON]
        if lost:
            raise Unsupported('Nem támogatott UI eseménylánc: DO_KEY(' + repr(key) + ') → ' + target['event'] + ' (' + target['id']
                              + '): a kódja ' + ', '.join(dict.fromkeys(lost)) + ' lépést tartalmaz, amelyet a webes képernyő '
                              'gombból nem tud végrehajtani; a saját key-trigger logikáját át kell ültetni.')
        for name, bind in sub.binds.items():
            self.binds.setdefault(name, bind)
        self.needs |= sub.needs
        self.notes += [n for n in sub.notes if n not in self.notes]
        self.used_units += [u for u in sub.used_units if u not in self.used_units]
        self.unresolved += [u for u in sub.unresolved if u not in self.unresolved]
        self.out_args += sub.out_args
        self.commands += sub.commands
        self.points += sub.points
        self.notes.append('DO_KEY(' + repr(key) + ') -> a(z) ' + target['id'] + ' saját kódja beágyazva')
        tokens = significant(scan(text))
        if not tokens or tokens[0][1].upper() not in {'DECLARE', 'BEGIN'}:
            return 'BEGIN\n' + text + ('' if text.endswith(';') else ';') + '\nEND;'
        return text if text.endswith(';') else text + ';'

    @staticmethod
    def depth(sig, start, index):
        """Parenthesis depth at sig[index], counting from the '(' at sig[start]."""
        level = 0
        for token in sig[start:index]:
            if token[1] == '(':
                level += 1
            elif token[1] == ')':
                level -= 1
        return level

    def check_discard(self, statement):
        from .framework import framework_call
        if not framework_call(statement, self.catalog):
            raise Unsupported('Keretrendszer-hívás nem elhagyható argumentummal; a teljes trigger kézi átültetést igényel: '
                              + statement.split('(', 1)[0].strip())

    def check_call(self, sig, k, expression=False):
        """A procedure-call statement: only to a routine that surely exists, with its OUT arguments known."""
        parts, j = [sig[k][1].upper()], k
        while j + 2 < len(sig) and sig[j + 1][1] == '.' and sig[j + 2][0] == 'ident':
            parts.append(sig[j + 2][1].upper()); j += 2
        name = '.'.join(parts)
        following = sig[j + 1][1] if j + 1 < len(sig) else ''
        if following not in {'(', ';'}:
            return  # record field or similar, e.g. v_rec.field := ...
        if parts[0] in self.units or parts[0] in self.members or name.startswith(STANDARD) or name in STANDARD_CALLS:
            return
        signature = self.procedures.get(name)
        if signature is None:
            # Forms resolved it in the database (or an attached library): the database decides at run time.
            if name not in self.unresolved:
                self.unresolved.append(name)
            return
        if not expression and signature.get('kind') == 'function':
            raise Unsupported(name + ': függvény, utasításként hívva.')
        if expression and signature.get('kind') != 'function':
            raise Unsupported(name + ': eljárás, kifejezésként hívva.')
        args = self.arguments(sig, j + 1)
        params = signature.get('arguments', [])
        supplied = {}
        named = False
        for index, arg in enumerate(args):
            match = re.fullmatch(r'([A-Za-z][\w$#]*)\s*=>\s*(.*)', arg, re.S)
            if match:
                named = True
                position = next((i for i, p in enumerate(params) if p.get('name', '').upper() == match[1].upper()), -1)
                arg = match[2]
            else:
                if named:
                    raise Unsupported(name + ': pozicionális argumentum név szerinti argumentum után.')
                position = index
            if position < 0 or position >= len(params) or position in supplied:
                raise Unsupported(name + ': ismeretlen/ismételt argumentum vagy az argumentumok száma nem egyezik.')
            supplied[position] = arg
        if any(not p.get('default') for i, p in enumerate(params) if i not in supplied):
            raise Unsupported(name + ': az argumentumok száma nem egyezik az aláírással (' + str(len(args)) + ' / ' + str(len(params)) + ').')
        for index, text in supplied.items():
            param = params[index]
            if 'OUT' in param.get('mode', 'IN').upper():
                if not re.fullmatch(r'[A-Za-z][\w$#]*|:[A-Za-z][\w$#]*(?:\.[A-Za-z][\w$#]*)?', text.strip()):
                    raise Unsupported(name + ': a(z) ' + (param.get('name') or '?') + ' OUT paraméter csak változó vagy mező lehet.')
                self.out_args.append(text.strip())

    @staticmethod
    def arguments(sig, k):
        """Argument texts of a call whose '(' is sig[k] (none when the call has no parentheses)."""
        if k >= len(sig) or sig[k][1] != '(':
            return []
        args, current, depth = [], [], 0
        for token in sig[k + 1:]:
            if token[1] == '(':
                depth += 1
            elif token[1] == ')':
                if depth == 0:
                    break
                depth -= 1
            elif token[1] == ',' and depth == 0:
                args.append(' '.join(current)); current = []; continue
            current.append(token[1])
        if current or args:
            args.append(' '.join(current))
        return args

    @staticmethod
    def statement_end(sig, k):
        depth = 0
        for token in sig[k:]:
            if token[1] == '(': depth += 1
            elif token[1] == ')': depth -= 1
            elif token[1] == ';' and depth == 0:
                return token[3]
        raise Unsupported('Lezáratlan utasítás: ' + sig[k][1])

    @staticmethod
    def in_handler(sig, k):
        """Track actual EXCEPTION sections, including calls after other statements.

        CASE ... WHEN ... THEN is not an exception handler; looking only at the
        previous THEN produced illegal RAISE statements and swallowed late errors.
        """
        stack, skip = [], -1
        for index, token in enumerate(sig[:k]):
            if index == skip or token[0] != 'ident':
                continue
            word = token[1].upper()
            if word in {'BEGIN', 'IF', 'LOOP', 'CASE'}:
                stack.append([word, False])
            elif word == 'END':
                if stack:
                    stack.pop()
                if index + 1 < k and sig[index + 1][1].upper() in {'IF', 'LOOP', 'CASE'}:
                    skip = index + 1
            elif word == 'EXCEPTION' and index and sig[index - 1][1].upper() in {';', 'BEGIN'}:
                if stack and stack[-1][0] == 'BEGIN':
                    stack[-1][1] = True
        return any(kind == 'BEGIN' and handler for kind, handler in stack)


def unit_constant(name: str) -> str:
    constant = re.sub(r'[^A-Za-z0-9_]', '_', name.upper())
    return constant if constant[:1].isalpha() else 'U_' + constant


def sql_expression(prepared: dict) -> str:
    """Java expression of the block: head (shared helpers by name) + named program-unit constants + tail."""
    from .common import java_text_block
    suffix = '_UI' if prepared.get('ui') else ''  # the screen-emulating variant of a program unit
    head = [('FormsPlsql.' + part[1]) if isinstance(part, tuple) else java_lines(part)
            for part in prepared.get('head_parts') or [prepared['head']]]
    inline = prepared.get('inline_units') or {}  # a package with its reached members only: this block's own text
    parts = (head + [java_lines(inline[n] + '\n') if n in inline else 'PlsqlUnits.' + unit_constant(n) + suffix + ' + "\\n"'
                     for n in prepared['units']]
             + [java_text_block(prepared['tail'])])
    return ' + '.join(parts)


def java_lines(text: str, indent: str = '                ') -> str:
    """A Java string literal of lines that all end with a newline (no trailing empty literal)."""
    from .common import jstr
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines = lines[:-1]
        return ('\n' + indent + '+ ').join(jstr(line + '\n') for line in lines) if lines else '""'
    return ('\n' + indent + '+ ').join(jstr(line + ('\n' if i < len(lines) - 1 else '')) for i, line in enumerate(lines))


PACKAGE_HEADER = re.compile(r'^\s*PACKAGE\s+(BODY\s+)?([A-Za-z][\w$#]*(?:\.[A-Za-z][\w$#]*)?)\s+(?:IS|AS)\b', re.I)


INIT_REFUSED = {'INSERT', 'UPDATE', 'DELETE', 'MERGE', 'COMMIT', 'ROLLBACK', 'SAVEPOINT'}


def package_declarations(name: str, unit: dict) -> tuple[str, str, list[str], str, list[dict]]:
    """A local package as declarations of an anonymous block: (variables, subprograms, member names, init block, elements).

    The specification's variables, constants, types and cursors come first, then the subprograms:
    the specification's forward declarations and the body. Package state lives for one request, so the
    initialisation part (BEGIN at the end of the body) runs at the start of the block, once per request,
    as a nested block (its exception handler included). It may read and compute; writing data or the
    screen there would happen in every request instead of once per Forms session, so that is refused.
    The elements ({kind, text, member}, in the same order) let a caller embed only the members it reaches.
    """
    from . import forms_emulation as emu
    items, subprograms, members, init = [], [], [], ''
    elements = {'item': [], 'forward': [], 'subprogram': [], 'init': []}
    for part in ('spec', 'body'):
        text = decode_line_escapes(unit.get(part) or '').strip()
        if not text:
            if part == 'body':
                raise Unsupported('A csomag törzse (Package Body) hiányzik az exportból.')
            continue
        header = PACKAGE_HEADER.match(text)
        if not header or bool(header.group(1)) != (part == 'body') or header.group(2).upper().split('.')[-1] != name:
            raise Unsupported('Nem értelmezhető csomagfej: ' + name)
        inner = text[header.end():]
        footer = re.search(r'\bEND(?:\s+' + re.escape(name) + r')?\s*;?\s*/?\s*$', inner, re.I)
        if not footer:
            raise Unsupported('Lezáratlan csomag: ' + name)
        inner = inner[:footer.start()]
        for element in package_elements(inner):
            kind, text_part, member = element
            if kind == 'init':
                words = {t[1].upper() for t in significant(scan(text_part)) if t[0] == 'ident'}
                refused = sorted(words & (INIT_REFUSED | emu.COMMANDS | emu.TAIL_COMMANDS))
                if refused:
                    raise Unsupported('A csomag inicializáló része adatot vagy képernyőt módosít (' + ', '.join(refused)
                                      + '): a Formsban munkamenetenként egyszer, itt kérésenként futna; kézi átültetés.')
                init = text_part.rstrip() + '\nEND;'
                elements['init'].append({'kind': 'init', 'text': init, 'member': ''})
                continue
            if member:
                members.append(member)
            if kind == 'item':
                items.append('  ' + text_part)
                elements['item'].append({'kind': 'item', 'text': '  ' + text_part, 'member': member})
            elif kind == 'forward' and part == 'spec':
                subprograms.insert(len([x for x in subprograms if x.startswith('  -- forward')]), '  ' + text_part)
                elements['forward'].append({'kind': 'forward', 'text': '  ' + text_part, 'member': member})
            elif kind == 'subprogram':
                subprograms.append('  ' + text_part)
                elements['subprogram'].append({'kind': 'subprogram', 'text': '  ' + text_part, 'member': member})
    ordered = elements['item'] + elements['forward'] + elements['subprogram'] + elements['init']
    return '\n'.join(items), '\n'.join(subprograms), sorted(set(members)), init, ordered


def package_elements(text: str):
    """Top-level elements of a package part: ('item'|'forward'|'subprogram'|'init', text, member name)."""
    tokens = significant(scan(text))
    i, n = 0, len(tokens)
    while i < n:
        word = tokens[i][1].upper()
        start = tokens[i][2]
        if word in {'PROCEDURE', 'FUNCTION'}:
            from .libraries import header_end, subprogram_end
            member = tokens[i + 1][1].upper() if i + 1 < n else ''
            try:
                # Nested procedures and functions inside the member are its own declarations: kept as written.
                head, last = header_end(tokens, i), subprogram_end(tokens, i)
            except Unsupported as exc:
                raise Unsupported('Lezáratlan alprogram a csomagban: ' + member + ' (' + str(exc) + ').')
            yield ('forward' if tokens[head][1] == ';' else 'subprogram'), text[start:tokens[last][3]], member
            i = last + 1
            continue
        if word == 'BEGIN':
            yield 'init', text[start:], ''
            return
        j, depth = i, 0
        while j < n and not (tokens[j][1] == ';' and depth == 0):
            depth += {'(': 1, ')': -1}.get(tokens[j][1], 0)
            j += 1
        if j >= n:
            raise Unsupported('Lezáratlan deklaráció a csomagban.')
        member = tokens[i][1].upper() if tokens[i][0] == 'ident' else ''
        if word in {'TYPE', 'SUBTYPE', 'CURSOR'} and i + 1 < n:
            member = tokens[i + 1][1].upper()
        if word == 'PRAGMA':
            member = ''
        yield 'item', text[start:tokens[j][3]], member
        i = j + 1


def tail_units(units: dict) -> set:
    """Local procedures that end with a step the screen runs last (EXECUTE_QUERY, CALL_FORM ...), transitively."""
    from .forms_emulation import TAIL_COMMANDS
    direct, calls = set(), {}
    for name, unit in units.items():
        try:
            words = {t[1].upper() for t in significant(scan(decode_line_escapes(unit.get('text') or ''))) if t[0] == 'ident'}
        except Unsupported:
            continue
        if words & TAIL_COMMANDS:
            direct.add(name)
        calls[name] = words & set(units)
    result, changed = set(direct), True
    while changed:
        changed = False
        for name, called in calls.items():
            if name not in result and called & result:
                result.add(name)
                changed = True
    return result


def unit_library(units: dict, items: dict, prefixes: tuple, procedures: dict | None = None, runtime_calls: tuple = (),
                 ui: bool = False, form: str = '', messages: dict | None = None, block_info: dict | None = None) -> dict:
    """Every local procedure/function/package rewritten once, block-independently: text, binds, needs, calls.

    ui: the Forms runtime emulation of buttons and start-up code (forms_emulation).
    messages: the message routines of the framework catalog (messages.simplify); a procedure or package member that
    only shows its text parameter becomes PROCEDURE x(p) IS BEGIN MESSAGE(p); END (messages.wrapper).
    """
    from .messages import simplify, wrapper
    def plain(element, indent='  '):
        text = (wrapper(element['text']) if element['kind'] == 'subprogram' else None)
        if text:
            return dict(element, text='\n'.join(indent + line for line in text.splitlines()), note='message')
        text, notes = simplify(element['text'], messages)
        return dict(element, text=text, note='message' if notes else None)
    library = {}
    tails = tail_units(units) if ui else set()
    packages = {name for name, unit in units.items() if unit['kind'] == 'package'}
    declared = {}
    for name in packages:
        try:
            declared[name] = package_declarations(name, units[name])
        except Unsupported as exc:
            declared[name] = exc
            continue
        variables, subprograms, members_of, init, elements = declared[name]
        elements = [plain(e) for e in elements]
        if any(e['note'] for e in elements):
            forwards = [e['text'] for e in elements if e['kind'] == 'forward']
            declared[name] = ('\n'.join(e['text'] for e in elements if e['kind'] == 'item'),
                              '\n'.join(forwards[::-1] + [e['text'] for e in elements if e['kind'] == 'subprogram']),
                              members_of, next((e['text'] for e in elements if e['kind'] == 'init'), ''), elements)
    members = {m for d in declared.values() if not isinstance(d, Unsupported) for m in d[2]}
    for name, unit in units.items():
        entry = {'kind': unit['kind'], 'source': unit['text'], 'text': '', 'items': '', 'init': '', 'members': [], 'binds': {},
                 'needs': set(), 'calls': [], 'unresolved': [], 'out_args': [], 'error': None, 'commands': [],
                 'library': unit.get('library')}
        rewriter = Rewriter(None, items, prefixes, other_blocks=True, parameters=True, transaction=False,
                            units=units, procedures=procedures, runtime_calls=runtime_calls, ui=ui, form=form,
                            tail_units=tails, members=members, block_info=block_info)
        whole = unit['text'] or (unit.get('spec') or '') + (unit.get('body') or '')
        if not decode_line_escapes(whole).strip():
            # An inherited or truncated export: an empty declaration would break the whole block.
            entry['error'] = 'A programegység forrása üres (hiányos export vagy feloldatlan öröklés).'
            library[name] = entry
            continue
        try:
            if unit['kind'] == 'package':
                if isinstance(declared[name], Unsupported):
                    raise declared[name]
                variables, subprograms, entry['members'], init, _ = declared[name]
                validate_structure(subprograms)
                entry['items'] = rewriter.rewrite(variables) if variables.strip() else ''
                entry['text'] = rewriter.rewrite(subprograms).rstrip() if subprograms.strip() else ''
                entry['init'] = rewriter.rewrite(init).rstrip() if init else ''
            else:
                source = decode_line_escapes(unit['text'])
                validate_structure(source)
                source = plain({'kind': 'subprogram', 'text': source.strip()}, '')['text'].strip()
                entry['text'] = rewriter.rewrite(source).rstrip().rstrip(';') + ';'
        except Exception as exc:
            entry['error'] = str(adapter_error(exc))
        entry.update(binds=rewriter.binds, needs=rewriter.needs, calls=[u for u in rewriter.used_units if u != name],
                     unresolved=rewriter.unresolved, out_args=rewriter.out_args, commands=rewriter.commands)
        if entry['error'] and unit['kind'] == 'package' and not isinstance(declared[name], Unsupported):
            # One member the database cannot run (GET_BLOCK_PROPERTY, GO_BLOCK ...) need not block the others:
            # each element rewritten alone, prepare() embeds only the members a trigger reaches (package_members).
            entry['elements'] = []
            for element in declared[name][4]:
                part = Rewriter(None, items, prefixes, other_blocks=True, parameters=True, transaction=False,
                                units=units, procedures=procedures, runtime_calls=runtime_calls, ui=ui, form=form,
                                tail_units=tails, members=members, block_info=block_info)
                result = dict(element, error=None)
                try:
                    if element['kind'] == 'subprogram':
                        validate_structure(element['text'])
                    result['text'] = part.rewrite(element['text']).rstrip()
                except Exception as exc:
                    result.update(text='', error=str(adapter_error(exc)))
                result.update(binds=part.binds, needs=part.needs, calls=[u for u in part.used_units if u != name],
                              unresolved=part.unresolved, out_args=part.out_args, commands=part.commands)
                entry['elements'].append(result)
        library[name] = entry
    # Two packages (or a package and a procedure) with the same member name cannot share one block:
    # checked in prepare() for the units one block really embeds (attached libraries are large).
    return library


def unit_refusal(name: str, entry: dict) -> str:
    label = 'csomag' if entry['kind'] == 'package' else 'eljárás'
    return 'A(z) ' + name + ' helyi ' + label + ' nem futtatható: ' + entry['error']


def identifiers(text: str) -> set:
    try:
        return {t[1].upper() for t in significant(scan(text)) if t[0] == 'ident'}
    except Unsupported:
        return set()


def package_members(library: dict, body: str, used: list) -> dict:
    """Local packages one member of which the database cannot run: {name: the package with the reached members only}.

    The members the trigger, the other embedded units and the reached members themselves name are embedded (calls
    are unqualified by then), with the member-less declarations (PRAGMA) and the initialisation part. A reached
    member that cannot run still refuses the trigger, now naming that member.
    """
    chosen = {}
    def view(name):
        entry = library[name]
        if not entry.get('elements'):
            return entry
        elements = [e for i, e in enumerate(entry['elements']) if i in chosen.setdefault(name, set())]
        failed = next((e for e in elements if e['error']), None)
        return {**entry, 'error': ((name + '.' + failed['member'] + ' tag: ' if failed['member'] else '') + failed['error'])
                if failed else None,
                'items': '\n'.join(e['text'] for e in elements if e['kind'] == 'item' and e['text'].strip()),
                'text': '\n'.join(e['text'] for e in elements if e['kind'] in {'forward', 'subprogram'} and e['text'].strip()),
                'init': next((e['text'] for e in elements if e['kind'] == 'init'), ''),
                'members': sorted({e['member'] for e in elements if e['member']}),
                'binds': {k: v for e in elements for k, v in e['binds'].items()},
                'needs': set().union(*(e['needs'] for e in elements)),
                'calls': list(dict.fromkeys(c for e in elements for c in e['calls'])),
                'unresolved': list(dict.fromkeys(u for e in elements for u in e['unresolved'])),
                'out_args': [a for e in elements for a in e['out_args']],
                'commands': [c for e in elements for c in e['commands']]}
    while True:
        reached, stack = [], list(used)
        while stack:
            name = stack.pop()
            if name in reached or name not in library:
                continue
            reached.append(name)
            entry = view(name)
            if entry['error']:
                break  # refused in prepare(), with the member it is about
            stack += entry['calls']
        words = identifiers(body)
        for name in reached:
            entry = view(name)
            words |= identifiers(entry['items'] + '\n' + entry['text'] + '\n' + entry['init'])
        grown = False
        for name in reached:
            if not library[name].get('elements'):
                continue
            wanted = {i for i, e in enumerate(library[name]['elements'])
                      if e['kind'] == 'init' or (e['kind'] == 'item' and not e['member']) or e['member'] in words}
            if not wanted <= chosen[name]:
                chosen[name] |= wanted
                grown = True
        if not grown:
            return {name: view(name) for name in chosen}


def member_collisions(order, library):
    """Names declared twice in one block: a package member and another package's member or a procedure."""
    owners = {}
    for name in order:
        for member in (library[name]['members'] if library[name]['kind'] == 'package' else [name]):
            owners.setdefault(member, []).append(name)
    for member, names in sorted(owners.items()):
        if len(set(names)) > 1:
            raise Unsupported('A(z) ' + member + ' név több helyi programegységben is szerepel ('
                              + ', '.join(sorted(set(names))) + '): egy névtelen blokkban nem ágyazhatók együtt.')


def prepare(source: str, *, block: str | None, items: dict, units: dict, prefixes: tuple,
            other_blocks: bool = False, parameters: bool = False, transaction: bool = False,
            writable=lambda b: True, procedures: dict | None = None, library: dict | None = None,
            runtime_calls: tuple = (), ui: bool = False, form: str = '', trigger_item: str | None = None,
            key_overrides=frozenset(), key_triggers=None, commit_points: bool = False, cursor_on_item: bool = True,
            messages: dict | None = None, block_info: dict | None = None) -> dict:
    """The anonymous block and its binds, or Unsupported with the reason.

    Local program units come from the shared library (same text in every block);
    sql is head + unit texts + tail, so the Java code can reference named constants.
    ui: buttons and start-up code - Forms built-ins become screen commands (forms_emulation); the
    block also returns the written :GLOBAL values and the command buffer.
    """
    from . import forms_emulation as emu
    from .messages import simplify
    text = decode_line_escapes(source).strip().rstrip('/').strip()
    if not text:
        raise Unsupported('Üres triggerkód.')
    validate_structure(text)
    text, message_notes = simplify(text, messages)  # WUZENET, QMS$FORMS_ERRORS.PUSH, message-only alerts -> MESSAGE
    library = library if library is not None else unit_library(units, items, prefixes, procedures, runtime_calls, ui, form,
                                                               messages=messages, block_info=block_info)
    rewriter = Rewriter(block, items, prefixes, other_blocks=other_blocks, parameters=parameters,
                        transaction=transaction, units=units, procedures=procedures, runtime_calls=runtime_calls,
                        ui=ui, form=form, trigger_item=trigger_item, tail_units=tail_units(units) if ui else (),
                        key_overrides=key_overrides, key_triggers=key_triggers, commit_points=commit_points,
                        cursor_on_item=cursor_on_item, block_info=block_info)
    rewriter.notes += message_notes
    body = rewriter.rewrite(text)
    views = package_members(library, body, rewriter.used_units)
    order = []
    def include(name, stack):
        if name in order: return
        if name in stack: raise Unsupported('Egymást hívó helyi eljárások: ' + ' -> '.join(stack + [name]))
        entry = views.get(name) or library[name]
        if entry['error']:
            raise Unsupported(unit_refusal(name, entry))
        for inner in entry['calls']:
            include(inner, stack + [name])
        for key, bind in entry['binds'].items():
            # what the trigger itself could not read is a developer input in its procedures too
            if bind['parameter'] and not parameters:
                bind = {**bind, 'parameter': False, 'input': 'szerveroldali kontextus, adatműveleti triggerben nem érhető el'}
            elif not bind['parameter'] and not bind.get('input') and bind['block'] != block and not other_blocks:
                bind = {**bind, 'input': 'másik blokk mezője, a rekordban nem érhető el'}
            rewriter.binds.setdefault(key, bind)
        rewriter.needs |= entry['needs']
        rewriter.unresolved += [u for u in entry['unresolved'] if u not in rewriter.unresolved]
        rewriter.out_args += entry.get('out_args', [])
        rewriter.commands += entry.get('commands', [])
        order.append(name)
    for name in list(rewriter.used_units):
        include(name, [])
    library = {**library, **{n: v for n, v in views.items() if n in order}}  # the embedded members only, from here on
    for name in order:
        if name in views:
            rewriter.notes.append('A(z) ' + name + ' helyi csomagból csak a hívott tagok kerültek a blokkba ('
                                  + ', '.join(views[name]['members']) + '); a többi tag nem futtatható az adatbázisban.')
    member_collisions(order, library)
    if any(library[n]['kind'] == 'package' for n in order):
        rewriter.notes.append('Helyi csomag beágyazva (' + ', '.join(n for n in order if library[n]['kind'] == 'package')
                              + '): a csomagváltozók kérésenként újraindulnak.')
    inits = [n for n in order if library[n].get('init')]
    if inits:
        rewriter.notes.append('A(z) ' + ', '.join(inits) + ' csomag inicializáló része a blokk elején fut (kérésenként; '
                              'a Formsban az első hivatkozáskor, munkamenetenként egyszer).')
    body_tokens = significant(scan(body))
    if not body_tokens:
        raise Unsupported('A triggerben nincs végrehajtható forrás; az export ellenőrzése szükséges.')
    first = body_tokens[0][1].upper()
    body = body.rstrip()
    if first not in {'DECLARE', 'BEGIN'}:
        body = 'BEGIN\n' + body + ('' if body.endswith(';') else ';') + '\nEND;'
    elif not body.endswith(';'):
        body += ';'
    # What the emulation left empty (BEGIN NULL; END;, IF NOT TRUE ...) goes; the behaviour stays.
    from .plsql_structure import prune
    body = prune(body)
    if 'failure' in rewriter.needs and not any(re.search(r'\bFORM_TRIGGER_FAILURE\b', code, re.I)
                                               for code in [body] + [library[n]['text'] for n in order]):
        rewriter.needs.discard('failure')  # only the pruned IF NOT TRUE THEN RAISE FORM_TRIGGER_FAILURE used it
    points = screen_points = 0
    if rewriter.points:
        from . import commit_points as cp
        why = cp.purpose(body)
        body, total = cp.transform('BEGIN\n' + body + '\nEND;')  # the body is a statement list of the outer block
        screen_points = body.count('frm_screen_point(')
        points = total - screen_points
        stateful = [n for n in order if library[n]['kind'] == 'package' and library[n]['items'].strip()]
        if stateful:
            raise Unsupported(why + ': a(z) ' + ', '.join(stateful) + ' helyi csomag változói a '
                              'folytatásig nem őrizhetők meg (a folytatás új kérésben indul).')
        rewriter.parameter_bind('FRM.RESUME')
        if points:
            rewriter.parameter_bind('FRM.COMMIT')
            rewriter.notes.append('COMMIT_FORM a kód közepén -> mentési pont (' + str(points) + '): a képernyő ment, majd a kód '
                                  'a pont után folytatódik (FRM.RESUME).')
        if screen_points:
            rewriter.notes.append('Képernyőlépés a kód közepén -> képernyőpont (' + str(screen_points) + '): a képernyő '
                                  'végrehajtja a lépést, majd a kód a pont után folytatódik (FRM.RESUME). A pont előtti '
                                  'adatbázis-módosítások a ponton véglegesednek (a Formsban a következő mentéskor).')
    if ui:
        rewriter.needs.add('ui')
        handlers = []
        if 'alert' in rewriter.needs:
            # A pending dialog: the work of this request is undone, the screen asks and sends it again.
            handlers.append('  WHEN frm_alert_pending THEN\n    ROLLBACK TO SAVEPOINT frm_start;')
        if points or screen_points:
            from .commit_points import handler
            handlers.append(handler(points, screen_points))
        if handlers:
            body = 'BEGIN\n  SAVEPOINT frm_start;\n' + body + '\nEXCEPTION\n' + '\n'.join(handlers) + '\nEND;'
    binds = list(rewriter.binds.values())
    code = body + '\n' + '\n'.join(library[n]['items'] + '\n' + library[n]['text'] + '\n' + library[n].get('init', '')
                                    for n in order)
    visible = ''.join(t[1] for t in scan(code) if t[0] not in {'string', 'comment'})
    targets = assigned_vars(visible)
    for arg in rewriter.out_args:  # OUT arguments of known procedures write the item too
        if arg.startswith(':'):
            targets.add(rewriter.bind(arg))
    binds = list(rewriter.binds.values())
    assigned = {b['source'] for b in binds if Rewriter.var(b['source']) in targets}
    written_inputs = [b['source'] for b in binds if b.get('input') and b['source'] in assigned]
    if written_inputs:
        raise Unsupported('A kód olyan értéket ír, amelynek nincs helye a migrált felületen: ' + ', '.join(written_inputs)
                          + ' (' + '; '.join(dict.fromkeys(b['input'] for b in binds if b['source'] in written_inputs)) + ').')
    blocked = [b['source'] for b in binds if b['source'] in assigned and not b['parameter'] and not b.get('input')
               and not writable(b)]
    if blocked:
        raise Unsupported('A trigger ebben az eseményben nem visszaírható mezőt ír: ' + ', '.join(blocked)
                          + ' (lekérdezett adatbázismező, kulcs vagy nem módosítható oszlop).')
    var = Rewriter.var
    # An external routine without signature may write a protected item through an OUT parameter:
    # checked at run time instead of refused at generation (ORA-20998, reported as a migration limit).
    guarded = [b for b in binds if rewriter.unresolved and not b['parameter'] and not b.get('input') and not writable(b)]
    head = ['DECLARE', '  frm_messages VARCHAR2(32767);']
    if 'failure' in rewriter.needs: head.append('  FORM_TRIGGER_FAILURE EXCEPTION;')
    if 'acknowledge' in rewriter.needs: head.append('  ACKNOWLEDGE CONSTANT PLS_INTEGER := 0;\n  NO_ACKNOWLEDGE CONSTANT PLS_INTEGER := 1;')
    head += [f"  {var(b['source'])} {SQL_TYPES[b['type']]} := ?; -- {b['source']}" for b in binds]
    head += [f"  {var(b['source'])}_o {SQL_TYPES[b['type']]}; -- {b['source']} (védett)" for b in guarded]
    emulated_items, helpers = emu.declarations(rewriter.needs) if ui else ([], [])
    commit_subprograms = []
    if points or screen_points:
        from . import commit_points as cp
        commit_items, commit_subprograms = cp.declarations(var('FRM.RESUME'), var('FRM.COMMIT') if points else '',
                                                           cp.state_expression(binds, var), commit=bool(points),
                                                           screen=bool(screen_points))
        emulated_items += commit_items
    head += emulated_items
    head += [library[n]['items'] for n in order if library[n]['items'].strip()]  # package variables: before any subprogram
    # The fixed helpers by name (CommonMigrateTools.FormsPlsql in the Java code), then the block's own subprograms.
    head += [('helper', 'MSG')] + [('helper', name) for name in helpers] + commit_subprograms
    outs = [b for b in binds if not b['parameter'] and not b.get('input') and writable(b)]
    # Written :GLOBAL values go back to the screen, which keeps them for the following requests.
    globals_out = [b for b in binds if b['parameter'] and b['block'] == 'GLOBAL' and b['source'] in assigned] if ui else []
    start = ['BEGIN'] + [f"  {var(b['source'])}_o := {var(b['source'])};" for b in guarded]
    checks = [f"  IF {v} <> {v}_o OR ({v} IS NULL AND {v}_o IS NOT NULL) OR ({v} IS NOT NULL AND {v}_o IS NULL) THEN\n"
              f"    RAISE_APPLICATION_ERROR(-20998, '{b['source']}: a trigger módosította, de ebben az eseményben nem írható vissza.');\n  END IF;"
              for b in guarded for v in [var(b['source'])]]
    # Package initialisation parts, callees first (order): once per request, before the code.
    start += ['  -- ' + n + ' inicializálása\n' + library[n]['init'] for n in inits]
    tail = (start + [body] + checks + [f"  ? := {var(b['source'])};" for b in outs]
            + [f"  ? := {var(b['source'])};" for b in globals_out] + ['  ? := frm_messages;'] + (['  ? := frm_ui;'] if ui else []))
    if 'failure' in rewriter.needs:
        tail += ["EXCEPTION", "  WHEN FORM_TRIGGER_FAILURE THEN",
                 "    RAISE_APPLICATION_ERROR(-20999, NVL(SUBSTR(RTRIM(frm_messages, CHR(10)), 1, 2000), 'A művelet nem hajtható végre.'));"]
    tail.append('END;')
    head_parts = []  # literal text and ('helper', NAME) segments, in order
    for part in head:
        if isinstance(part, tuple):
            head_parts.append(part)
        elif head_parts and isinstance(head_parts[-1], str):
            head_parts[-1] += part + '\n'
        else:
            head_parts.append(part + '\n')
    head_text = ''.join(emu.HELPERS[p[1]] + '\n' if isinstance(p, tuple) else p for p in head_parts)
    tail_text = '\n'.join(tail)
    sql = head_text + ''.join(library[n]['text'] + '\n' for n in order) + tail_text
    return {'sql': sql, 'head': head_text, 'head_parts': head_parts, 'tail': tail_text, 'binds': binds, 'outs': outs,
            'notes': rewriter.notes, 'inline_units': {n: library[n]['text'] for n in order if n in views},
            'units': order, 'assigned': sorted(assigned), 'unresolved': rewriter.unresolved, 'guarded': [b['source'] for b in guarded],
            'ui': ui, 'globals': globals_out, 'commands': list(dict.fromkeys(rewriter.commands)), 'commit_points': points,
            'screen_points': screen_points, 'inputs': developer_inputs(binds)}


# Java types of the developer inputs: what PlsqlValues reads for the same SQL type.
INPUT_TYPES = {'text': 'String', 'number': 'java.math.BigDecimal', 'datetime': 'java.time.LocalDateTime'}
# Names the generated methods already use (parameters, locals, lambda arguments): an input never shadows them.
INPUT_RESERVED = {'values', 'parameters', 'out', 'row', 'rows', 'context', 'request', 'user', 'blocks', 'globals', 'messages',
                  'commands', 'sql', 'params', 'whereText', 'jdbc', 'log', 'result', 'changes', 'saved', 'current', 'state',
                  'actionBlocks', 'actionParameters', 'actionCommands', 'offset', 'limit', 'rs', 'rowNum', 'line', 'e'}


def developer_inputs(binds: list) -> list[dict]:
    """The binds the migrated code has no source for (an item that is not in the form, a :GLOBAL in a data trigger,
    a Forms system variable ...): each is a local variable of the Java method, null until the developer passes the
    right value (TODO). The code that reads them runs; writing them is refused (prepare)."""
    result, taken = [], set(INPUT_RESERVED)
    for bind in binds:
        if not bind.get('input'):
            continue
        words = [w for w in re.split(r'[^A-Za-z0-9]+', bind['source']) if w]
        variable = (words[0].lower() + ''.join(w[:1].upper() + w[1:].lower() for w in words[1:])) if words else 'input'
        if not re.match(r'[a-z]', variable):
            variable = 'v' + variable
        base, n = variable, 2
        while variable in taken:
            variable, n = base + str(n), n + 1
        taken.add(variable)
        result.append({'source': bind['source'], 'variable': variable, 'type': bind['type'], 'reason': bind['input']})
    return result


def input_variable(prepared: dict, bind: dict) -> str | None:
    """The Java variable of a developer input bind, None for any other bind."""
    return next((i['variable'] for i in prepared.get('inputs', []) if i['source'] == bind['source']), None) if bind.get('input') else None


def input_declarations(prepared: dict, indent: str) -> str:
    """The developer inputs as local variables of the Java method, each with its TODO; a variable java-variables.json
    names gets its value from there."""
    from . import java_variables
    lines = []
    for i in prepared.get('inputs', []):
        entry = java_variables.lookup(i['variable'])
        if entry:
            lines.append(f"{indent}// :{i['source']} ({i['reason']}): az érték a java-variables.json-ból.\n"
                         f"{indent}{java_variables.declaration(INPUT_TYPES[i['type']], i['variable'], entry)}\n")
        else:
            lines.append(f"{indent}// TODO: :{i['source']} ({i['reason']}): add át ennek a változónak a megfelelő értéket.\n"
                         f"{indent}{INPUT_TYPES[i['type']]} {i['variable']} = null;\n")
    return ''.join(lines)


def open_inputs(inputs: list) -> list:
    """The developer inputs still waiting for a value (java-variables.json gives none of them)."""
    from . import java_variables
    return [i for i in inputs if not java_variables.lookup(i['variable'])]


def assigned_vars(visible: str) -> set[str]:
    """Variables written by :=, or as SELECT/FETCH/RETURNING ... INTO targets (not INSERT/MERGE INTO)."""
    result = set(re.findall(r'\b(nv_[0-9a-f]{10})\s*:=', visible))
    for m in re.finditer(r'\bINTO\b', visible, re.I):
        before = re.search(r'(\w+)\s*$', visible[:m.start()])
        if before and before.group(1).upper() in {'INSERT', 'MERGE'}:
            continue
        segment = re.match(r'(?:(?!\bFROM\b|\bUSING\b)[^;])*', visible[m.end():], re.I | re.S).group(0)
        result |= set(re.findall(r'\bnv_[0-9a-f]{10}\b', segment))
    return result


BLOCK_PROPERTIES = ('QUERY_ALLOWED', 'INSERT_ALLOWED', 'UPDATE_ALLOWED', 'DELETE_ALLOWED', 'DEFAULT_WHERE', 'ORDER_BY',
                    'QUERY_DATA_SOURCE_NAME', 'DML_DATA_TARGET_NAME', 'RECORDS_DISPLAYED')


def block_statics(model) -> dict:
    """{BLOCK: {PROPERTY: value}} of the properties the form fixes; a property any code sets (SET_BLOCK_PROPERTY)
    is left out, so GET_BLOCK_PROPERTY of it is not folded."""
    from .common import model_cache
    return model_cache(model, 'block_statics', lambda: _block_statics(model))


def _block_statics(model) -> dict:
    from .xmlmodel import get
    sources = [t['source'] for t in model.get('triggers', [])] + [get(u, 'ProgramUnitText') for u in model.get('program_units', [])]
    text = decode_line_escapes('\n'.join(str(s or '') for s in sources))
    changed = {m.group(1).upper() for m in re.finditer(r'set_block_property\s*\([^;]*?,\s*([A-Za-z_]+)\s*,', text, re.I)}
    result = {}
    for b in model.get('blocks', []):
        props = b.get('properties', {})
        clause = lambda value, keyword: re.sub(r'^\s*' + keyword + r'\s+', '', decode_line_escapes(value or ''), flags=re.I).strip() or None
        values = {'QUERY_ALLOWED': 'TRUE' if b.get('query_allowed', True) else 'FALSE',
                  'INSERT_ALLOWED': 'TRUE' if b.get('insert_allowed', True) else 'FALSE',
                  'UPDATE_ALLOWED': 'TRUE' if b.get('update_allowed', True) else 'FALSE',
                  'DELETE_ALLOWED': 'TRUE' if b.get('delete_allowed', True) else 'FALSE',
                  'DEFAULT_WHERE': clause(get(props, 'WhereClause'), 'WHERE'),
                  'ORDER_BY': clause(get(props, 'OrderByClause'), 'ORDER BY'),
                  'QUERY_DATA_SOURCE_NAME': get(props, 'QueryDataSourceName').upper() or None,
                  'DML_DATA_TARGET_NAME': (get(props, 'DMLDataTargetName') or get(props, 'QueryDataSourceName')).upper() or None,
                  'RECORDS_DISPLAYED': get(props, 'RecordsDisplayCount') or '1'}
        result[b['name']] = {k: v for k, v in values.items() if k not in changed}
    return result


def local_units(model) -> dict:
    """The form's program units and the attached-library units it reaches (libraries.attach)."""
    from .xmlmodel import get
    units = {}
    for unit in model['program_units']:
        kind = (get(unit, 'ProgramUnitType') or '').lower()
        name = (get(unit, 'Name') or '').upper()
        text = get(unit, 'ProgramUnitText') or ''
        library = unit.get('frm_library') if isinstance(unit, dict) else None
        if 'package' in kind:
            # Package Spec + Package Body share the name: both parts are kept for the inlining.
            entry = units.setdefault(name, {'kind': 'package', 'text': '', 'spec': '', 'body': ''})
            entry['body' if 'body' in kind else 'spec'] = text
            entry['text'] = (entry['spec'] + '\n' + entry['body']).strip()
            if library:
                entry['library'] = library
            continue
        units[name] = {'kind': 'procedure' if 'procedure' in kind else 'function', 'text': text}
        if library:
            units[name]['library'] = library
    return units


def items_by_block(model) -> dict:
    return {b['name']: {i['name']: {'type': i['type'], 'field': i['field'], 'database': i['name'] in {d['name'] for d in b['db_items']},
                                    'primary_key': any(p['name'] == i['name'] for p in b['pk']), 'update_allowed': i.get('update_allowed', True)}
                        for i in b['items'] if i['kind'] != 'button'} for b in model['blocks']}

"""Message procedures: whatever only shows a text to the user becomes MESSAGE(text) before the code is rewritten.

The migrated screen shows the messages of a request in its own way, so the Forms means of showing them need not be
replicated:

    WUZENET('Nincs kiválasztva!');                        -> MESSAGE('Nincs kiválasztva!');
    QMS$FORMS_ERRORS.PUSH(QMS$FORMS_ERRORS.MSGGETTEXT(37, 'Nem kérdezhető le'), 'E', 'X', 37);
                                                          -> MESSAGE('Nem kérdezhető le');
    QMS$FORMS_ERRORS.RAISE_FAILURE;                       -> RAISE FORM_TRIGGER_FAILURE;
    DECLARE al ALERT; n NUMBER; BEGIN al := FIND_ALERT('A'); SET_ALERT_PROPERTY(al, ALERT_MESSAGE_TEXT, 'Hiba');
    n := SHOW_ALERT(al); END;                             -> MESSAGE('Hiba');
    PROCEDURE uzen(p VARCHAR2) IS ... alert dialog with p ... END;  -> PROCEDURE uzen(p VARCHAR2) IS BEGIN MESSAGE(p); END;

The routines come from the framework catalog (message_calls, message_functions, failure_calls); an alert block or a
procedure counts only if it does nothing but show its one text (query_actions.message_wrapper). A dialog whose answer
the code reads (IF SHOW_ALERT(..) = ALERT_BUTTON1) is real logic and stays with the emulation.
"""
from __future__ import annotations

import re

# The default catalog entries (framework-catalog.json has the same; a custom catalog without the keys keeps these).
DEFAULT_MESSAGE_CALLS = {'QMS$FORMS_ERRORS.PUSH': 1, 'WUZENET': 1}
DEFAULT_MESSAGE_FUNCTIONS = {'QMS$FORMS_ERRORS.MSGGETTEXT': 2}
DEFAULT_FAILURE_CALLS = ('QMS$FORMS_ERRORS.RAISE_FAILURE',)
STARTS = {';', 'BEGIN', 'THEN', 'ELSE', 'LOOP', 'DECLARE', 'IS', 'AS', 'EXCEPTION'}


def defaults() -> dict:
    return {'calls': dict(DEFAULT_MESSAGE_CALLS), 'functions': dict(DEFAULT_MESSAGE_FUNCTIONS),
            'failures': tuple(DEFAULT_FAILURE_CALLS)}


def configured(messages) -> dict:
    return messages if messages is not None else defaults()


def _qualified(sig, k):
    parts, j = [sig[k][1].upper()], k
    while j + 2 < len(sig) and sig[j + 1][1] == '.' and sig[j + 2][0] == 'ident':
        parts.append(sig[j + 2][1].upper())
        j += 2
    return '.'.join(parts), j


def _closing(sig, k):
    depth = 0
    for index in range(k, len(sig)):
        depth += {'(': 1, ')': -1}.get(sig[index][1], 0)
        if sig[index][1] == ')' and not depth:
            return index
    return None


def _arguments(text, sig, open_index, close_index):
    """The argument texts of the call whose '(' is sig[open_index]."""
    args, depth, start = [], 0, sig[open_index][3]
    for index in range(open_index + 1, close_index):
        token = sig[index]
        depth += {'(': 1, ')': -1}.get(token[1], 0)
        if token[1] == ',' and depth == 0:
            args.append(text[start:token[2]].strip())
            start = token[3]
    args.append(text[start:sig[close_index][2]].strip())
    return [a for a in args if a] if len(args) > 1 or args[0] else []


def simplify(text: str, messages=None) -> tuple[str, list[str]]:
    """The code with the message routines, the message-only alert blocks and the failure calls replaced; and notes."""
    from .plsql_passthrough import scan, significant
    from .plsql import Unsupported
    messages = configured(messages)
    calls, functions, failures = messages['calls'], messages['functions'], set(messages['failures'])
    try:
        sig = significant(scan(text))
    except Unsupported:
        return text, []
    words = {t[1].upper() for t in sig if t[0] == 'ident'}
    names = {n.split('.')[0] for n in list(calls) + list(functions) + list(failures)}
    if not (words & names or words & {'FIND_ALERT', 'SHOW_ALERT'}):
        return text, []
    replace, notes, k = {}, [], 0
    while k < len(sig):
        token = sig[k]
        previous = sig[k - 1][1].upper() if k else ';'
        if token[0] == 'ident' and (k == 0 or sig[k - 1][1] != '.'):
            at_start = previous in STARTS
            if token[1].upper() == 'DECLARE' and at_start:
                block = alert_block(text, sig, k)
                if block:
                    end, shown = block
                    replace[token[2]] = (sig[end][3], 'MESSAGE(' + shown + ');')
                    notes.append('Csak üzenetet mutató alert-blokk -> MESSAGE')
                    k = end + 1
                    continue
            name, last = _qualified(sig, k)
            following = sig[last + 1][1] if last + 1 < len(sig) else ''
            if at_start and name in failures and following in {';', '('}:
                end = last + 1 if following == ';' else (_closing(sig, last + 1) or last) + 1
                if end < len(sig) and sig[end][1] == ';':
                    replace[token[2]] = (sig[end][3], 'RAISE FORM_TRIGGER_FAILURE;')
                    notes.append(name + ' -> RAISE FORM_TRIGGER_FAILURE')
                    k = end + 1
                    continue
            if following == '(' and (name in functions or (at_start and name in calls)):
                close = _closing(sig, last + 1)
                if close is not None:
                    args = _arguments(text, sig, last + 1, close)
                    position = functions.get(name) if name in functions else calls[name]
                    if len(args) >= position:
                        shown, _ = simplify(args[position - 1], messages)
                        if name in functions:
                            plain = re.fullmatch(r"'(?:[^']|'')*'|[\w$#.]+|:[\w$#.]+", shown)
                            replace[token[2]] = (sig[close][3], shown if plain else '(' + shown + ')')
                            k = close + 1
                            continue
                        if close + 1 < len(sig) and sig[close + 1][1] == ';':
                            replace[token[2]] = (sig[close + 1][3], 'MESSAGE(' + shown + ');')
                            notes.append(name + ' -> MESSAGE')
                            k = close + 2
                            continue
        k += 1
    if not replace:
        return text, []
    out, last = [], 0
    for start in sorted(replace):
        end, new = replace[start]
        out += [text[last:start], new]
        last = end
    out.append(text[last:])
    return ''.join(out), list(dict.fromkeys(notes))


DECLARATION = re.compile(r"([\w$#]+)\s+(constant\s+)?(?:number|integer|pls_integer|boolean|alert|[\w$#]+|(?:varchar2|char)\s*\(\s*\d+"
                         r"\s*(?:byte|char)?\s*\))(?:\s+not\s+null)?(?:\s*(?::=|default)\s*('(?:[^']|'')*'|-?\d+|true|false|null))?",
                         re.I | re.S)


def alert_block(text, sig, k):
    """(index of the closing ';', the shown text expression) of a DECLARE ... END; at sig[k] that only shows a text."""
    depth, begin, end, last = 0, None, None, None
    for index in range(k + 1, len(sig)):
        word = sig[index][1].upper()
        if word == 'BEGIN':
            begin = index if begin is None and depth == 0 else begin
            depth += 1
        elif word in {'IF', 'LOOP', 'CASE'} and sig[index - 1][1].upper() != 'END':
            depth += 1
        elif word == 'END':
            depth -= 1
            if depth == 0:
                last, end = index, index + 1
                while end < len(sig) and sig[end][1] != ';':
                    end += 1
                break
        elif word == 'DECLARE' and depth == 0:
            return None
    if begin is None or end is None or end >= len(sig) or end - last > 2:
        return None
    head = text[sig[k][3]:sig[begin][2]]
    variables, literals = set(), {}
    for declaration in [d.strip() for d in _split(head) if d.strip()]:
        match = DECLARATION.fullmatch(declaration)
        if not match:
            return None
        variables.add(match.group(1).upper())
        if match.group(3) and match.group(3).startswith("'"):
            literals[match.group(1).upper()] = match.group(3)
    body = text[sig[begin][3]:sig[last][2]]
    shown = None
    for statement in [s.strip() for s in _split(body) if s.strip()]:
        plain = re.sub(r'\s+', ' ', statement)
        target = r"(?:[\w$#]+|'(?:[^']|'')*')"
        text_match = re.fullmatch(rf"(?:set_alert_property\s*\(\s*{target}\s*,\s*alert_message_text|change_alert_message\s*\(\s*{target})"
                                  r"\s*,\s*(.+)\)", plain, re.I | re.S)
        if text_match:
            shown = text_match.group(1).strip()
            continue
        assignment = re.fullmatch(rf"([\w$#]+)\s*:=\s*(?:(?:show_alert|find_alert)\s*\(\s*{target}\s*\)|'(?:[^']|'')*'|-?\d+)", plain, re.I)
        if assignment and assignment.group(1).upper() in variables:
            continue
        if re.fullmatch(rf"set_alert_property\s*\(\s*{target}\s*,\s*title\s*,\s*.+\)|set_alert_button_property\s*\(.*\)"
                        r"|synchronize|bell|null", plain, re.I | re.S):
            continue
        return None
    if not shown:
        return None
    if shown.upper() in literals:
        shown = literals[shown.upper()]
    elif re.search(r'\b(?:' + '|'.join(re.escape(v) for v in variables) + r')\b', shown, re.I) if variables else False:
        return None  # the text is computed in the block: not a plain message
    return end, shown


def _split(text):
    """Statements of a declaration or body text: ';' outside strings and parentheses."""
    parts, depth, current, quoted = [], 0, [], False
    for char in text:
        if char == "'":
            quoted = not quoted
        elif not quoted:
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
            elif char == ';' and depth == 0:
                parts.append(''.join(current))
                current = []
                continue
        current.append(char)
    parts.append(''.join(current))
    return parts


def wrapper(text: str) -> str | None:
    """A local procedure that only shows its one text parameter, as PROCEDURE x(p ...) IS BEGIN MESSAGE(p); END; or None."""
    from .query_actions import message_wrapper
    from .common import decode_line_escapes
    code = decode_line_escapes(str(text or ''))
    if not message_wrapper(code):
        return None
    header = re.match(r"\s*(procedure\s+([\w$#]+)\s*\(\s*([\w$#]+)\b.*?\))\s*(?:is|as)\b", code, re.I | re.S)
    if not header:
        return None
    raises = re.search(r'\braise\s+form_trigger_failure\s*;\s*end\b[^;]*;?\s*$', code, re.I) is not None
    return (header.group(1) + ' IS\nBEGIN\n  MESSAGE(' + header.group(3) + ');\n'
            + ('  RAISE FORM_TRIGGER_FAILURE;\n' if raises else '') + 'END ' + header.group(2) + ';')

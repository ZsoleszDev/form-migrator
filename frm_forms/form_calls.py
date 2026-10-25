"""Forms CALL_FORM / OPEN_FORM / NEW_FORM with a parameter list -> Angular navigation (Router).

Recognised in the button trigger, or in the local procedure (without arguments) it calls:

    pl := GET_PARAMETER_LIST('x');  IF NOT ID_NULL(pl) THEN DESTROY_PARAMETER_LIST(pl); END IF;
    pl := CREATE_PARAMETER_LIST('x');
    v_kod := :BLOCK.ITEM;                      -- or a literal, NAME_IN('BLOCK.ITEM'), TO_CHAR(:BLOCK.ITEM)
    ADD_PARAMETER(pl, 'P_KOD', TEXT_PARAMETER, v_kod);
    CALL_FORM('ROGZITO', NO_HIDE, DO_REPLACE, NO_QUERY_ONLY, pl);
    DESTROY_PARAMETER_LIST(pl);

Headstart's qms$... bookkeeping calls are framework noise. Anything else - a query, a
condition, a message, a DATA_PARAMETER - leaves the button a manual task (its Forms code
is then commented into the generated method).
"""
from __future__ import annotations

import re

FRAMEWORK = re.compile(r"qms\$[\w$#.]+\s*(?:\(.*\))?", re.I | re.S)
HEADER = re.compile(r"^\s*procedure\s+([\w$#]+)\s+(?:is|as)\b(.*?)\bbegin\b(.*)\bend\b\s*[\w$#]*\s*;?\s*$", re.I | re.S)
TYPES = re.compile(r"(?:paramlist|varchar2\s*\(\s*\d+\s*(?:char|byte)?\s*\)|char\s*\(\s*\d+\s*\)|number(?:\s*\([\d\s,]+\))?|integer|date)", re.I)


def clean(text: str) -> str:
    text = re.sub(r'&#(?:10|13|9);', '\n', str(text or ''))
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)
    return re.sub(r'--[^\n]*', ' ', text)


def statements(text: str) -> list[str]:
    """Split on ';' outside string literals."""
    parts, current, quoted = [], [], False
    for ch in text:
        if ch == "'":
            quoted = not quoted
        if ch == ';' and not quoted:
            parts.append(''.join(current))
            current = []
        else:
            current.append(ch)
    parts.append(''.join(current))
    result = []
    for part in parts:
        part = re.sub(r'\s+', ' ', part).strip()
        part = re.sub(r'^(?:(?:declare|begin)\s+)+', '', part, flags=re.I).strip()
        if part:
            result.append(part)
    return result


def operand(expression: str, variables: dict) -> dict | None:
    expression = expression.strip()
    match = re.fullmatch(r"(?:to_char\s*\(\s*)?:([\w$#]+)\.([\w$#]+)(?:\s*\))?", expression, re.I)
    if match:
        return {'item': (match.group(1) + '.' + match.group(2)).upper()}
    match = re.fullmatch(r"name_in\s*\(\s*'([\w$#]+)\.([\w$#]+)'\s*\)", expression, re.I)
    if match:
        return {'item': (match.group(1) + '.' + match.group(2)).upper()}
    match = re.fullmatch(r"'((?:[^']|'')*)'", expression)
    if match:
        return {'value': match.group(1).replace("''", "'")}
    if re.fullmatch(r'-?\d+(?:\.\d+)?', expression):
        return {'value': expression}
    return variables.get(expression.lower())


def form_name(literal: str) -> str:
    """'ROGZITO', 'ROGZITO.fmx' or a path -> ROGZITO."""
    return re.sub(r'\.(?:fmx|fmb)$', '', re.split(r'[\\/]', literal.strip())[-1], flags=re.I).upper()


def analyse(parts: list[str], units: dict, variables: dict, plan: dict, depth: int) -> bool:
    for statement in parts:
        low = statement.lower()
        if low in {'end', 'null', 'end if'} or re.fullmatch(r'end\s+[\w$#]+', low):
            continue
        if FRAMEWORK.fullmatch(statement):
            continue  # Headstart event bookkeeping (qms$event_item ...)
        if re.fullmatch(r"([\w$#]+)\s*:=\s*(?:get|create)_parameter_list\s*\(\s*'[^']*'\s*\)", statement, re.I):
            continue
        if re.fullmatch(r"if\s+not\s+id_null\s*\(\s*[\w$#]+\s*\)\s+then\s+destroy_parameter_list\s*\([^)]*\)", statement, re.I):
            continue
        if re.fullmatch(r"destroy_parameter_list\s*\([^)]*\)", statement, re.I):
            continue
        match = re.fullmatch(r"add_parameter\s*\(\s*[\w$#]+\s*,\s*'([^']+)'\s*,\s*text_parameter\s*,\s*(.+)\)", statement, re.I)
        if match:
            value = operand(match.group(2), variables)
            if value is None:
                return False
            plan['params'].append({'name': match.group(1), **value})
            continue
        match = re.fullmatch(r"(call_form|open_form|new_form)\s*\(\s*'([^']+)'(?:\s*,.*)?\)", statement, re.I)
        if match:
            if plan.get('form'):
                return False  # two form calls: a decision the developer makes
            plan['form'], plan['call'] = form_name(match.group(2)), match.group(1).upper()
            continue
        match = re.fullmatch(r"([\w$#]+)\s*:=\s*(.+)", statement)
        if match:
            value = operand(match.group(2), variables)
            if value is None:
                return False
            variables[match.group(1).lower()] = value
            continue
        unit = units.get(statement.upper())
        if unit is not None and depth < 3:
            if not procedure(unit, units, plan, depth + 1):
                return False
            continue
        return False
    return True


def procedure(text: str, units: dict, plan: dict, depth: int) -> bool:
    match = HEADER.match(clean(text))
    if not match:
        return False  # parameters, a function or an unusual header: manual
    variables = {}
    for declaration in statements(match.group(2)):
        decl = re.fullmatch(r"([\w$#]+)\s+(" + TYPES.pattern + r")(?:\s*(?::=|default)\s*(.+))?", declaration, re.I)
        if not decl:
            return False
        if decl.group(3):
            value = operand(decl.group(3), variables)
            if value is None:
                return False
            variables[decl.group(1).lower()] = value
    return analyse(statements(match.group(3)), units, variables, plan, depth)


def navigation(source: str, model: dict) -> dict | None:
    """{'form', 'call', 'params': [{'name', 'item'|'value'}]} for a pure form call, else None."""
    if not re.search(r'\b(?:call_form|open_form|new_form)\b', clean(source), re.I) and not any(
            re.search(r'\b(?:call_form|open_form|new_form)\b', clean(u.get('programunittext', '')), re.I)
            for u in model.get('program_units', [])):
        return None
    units = {str(u.get('name', '')).upper(): u.get('programunittext', '') for u in model.get('program_units', [])}
    plan = {'form': None, 'call': None, 'params': []}
    if not analyse(statements(clean(source)), units, {}, plan, 0) or not plan['form']:
        return None
    return plan


FORM_CALL = re.compile(r'\b(?:call_form|open_form|new_form)\b', re.I)
DATA_WRITE = re.compile(r'\b(?:insert\s+into|update\s+[\w$#.]+\s+set|delete\s+from|merge\s+into|commit_form|commit|post)\b', re.I)


def reachable(source: str, model: dict) -> list[tuple[str, str]]:
    """(label, code) of the trigger and of the local program units it reaches by name."""
    units = {str(u.get('name', '')).upper(): u.get('programunittext', '') for u in model.get('program_units', [])}
    found, queue, seen = [('trigger', source)], [source], set()
    while queue:
        code = clean(queue.pop())
        for name in units:
            if name not in seen and re.search(r'(?<![\w$#.])' + re.escape(name) + r'(?![\w$#])', code, re.I):
                seen.add(name)
                found.append(('programunit ' + name, units[name]))
                queue.append(units[name])
    return found


def manual_navigation(source: str, model: dict) -> bool:
    """A form call the adapter cannot translate (e.g. a conditional target form), without data changes."""
    codes = [clean(code) for _, code in reachable(source, model)]
    return any(FORM_CALL.search(c) for c in codes) and not any(DATA_WRITE.search(c) for c in codes) and navigation(source, model) is None


def readable(source: str, model: dict) -> str:
    """The Forms code of a manual navigation, for comments."""
    parts = []
    for label, code in reachable(source, model):
        text = re.sub(r'&#(?:10|13);', '\n', str(code or '')).replace('\r', '').strip()
        parts += [label + ':', text, '']
    return '\n'.join(parts).rstrip()


def route(config: dict, form: str) -> str:
    return (config.get('form_routes') or {}).get(form) or '/' + form.lower()


def notes(navigations: dict, manual: list | None = None) -> list[str]:
    if not navigations and not manual:
        return []
    lines = ['', '## Navigáció (CALL_FORM / OPEN_FORM / NEW_FORM)', '',
             'A gomb nem hív backendet: `this.router.navigate([útvonal], { queryParams })`. Az útvonal alapból '
             '`/<form neve kisbetűvel>`; a `form_routes` beállítás (`{"ROGZITO": "/pages/rogzito"}`) írja felül. '
             'A hívott oldal a paramétereket a `queryParams`-ból olvassa (Forms: `:PARAMETER.<név>`).', '',
             '| Gomb | Forms-hívás | Útvonal | Paraméterek |', '|---|---|---|---|']
    for owner, nav in navigations.items():
        params = ', '.join(p['name'] + ' = ' + (p['block'] + '.' + p['key'] if p.get('block') else repr(p.get('value')))
                           for p in nav['params']) or '–'
        lines.append(f"| `{owner}` | `{nav['call']}('{nav['form']}')` | `{nav['route']}` | {params} |")
    if manual:
        lines += ['', 'Összetett formhívás (például a célform egy kódtól függ): a gomb metódusában TODO és kommentként az '
                  'eredeti kód; a döntést és az útvonalat a fejlesztő írja meg. Backend-végpont nem készül hozzá.', '']
        lines += [f'- `{owner}`' for owner in manual]
    return lines

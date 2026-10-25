"""COMMIT_FORM in the middle of a button's code: a commit point the request stops at and resumes after.

Forms commits the screen where the code says COMMIT_FORM and then runs the rest of the code. The
web screen commits with its own endpoint, so the anonymous block stops there: it returns the item
values of that moment and a FRM_COMMIT command. The screen saves (the commit endpoint runs the code
up to the point again, in the commit's transaction, and checks that it arrived at the same state),
then calls the button once more with FRM.RESUME = the point. In that run every statement before
the point is skipped and the branches leading to it are taken without evaluating their conditions
again, so the code continues exactly after COMMIT_FORM, with the saved values of the screen.

    IF ank_jog.irhat THEN                  IF (frm_resume IN (1) OR (frm_resume NOT IN (1) AND (ank_jog.irhat))) THEN
        COMMIT_FORM;               ->          frm_commit_form(1);
        ank_naplo.mentes(:B.ID);               ank_naplo.mentes(nv_...);
    END IF;                                END IF;

Statements in front of a point get IF frm_resume NOT IN (...) THEN ... END IF. Refused (manual
work, with the reason): a point in a loop, a CASE statement or an exception handler; a local
variable set before the point and read after it (the second request starts the block again); a
local package with state; GOTO.
"""
from __future__ import annotations

import re

from .plsql import Unsupported
from .plsql_structure import apply, parse

PLACEHOLDER = 'frm_commit_form(FRM_POINT)'
PURPOSE = 'COMMIT_FORM a kód közepén'
# A screen step (EXECUTE_QUERY, CLEAR_BLOCK ...) followed by more code: the request stops there too, the screen
# carries out the step and calls the button again with FRM.RESUME (screen point; no save, no prelude).
SCREEN_PLACEHOLDER = "frm_screen_point(FRM_POINT, 'FRM_OP')"
MARK = 'FRM_POINT'
POINT = re.compile(r"frm_(?:commit_form\(FRM_POINT\)|screen_point\(FRM_POINT, '[A-Z_]+'\))")


def screen_placeholder(step: str) -> str:
    return SCREEN_PLACEHOLDER.replace('FRM_OP', step)


def purpose(body: str) -> str:
    """'EXECUTE_QUERY a kód közepén' ...: the steps of the points, for the refusal messages."""
    steps = ['COMMIT_FORM' if m.group().startswith('frm_commit') else m.group().split("'")[1] for m in POINT.finditer(body)]
    return ', '.join(dict.fromkeys(steps or ['COMMIT_FORM'])) + ' a kód közepén'


def walk(nodes, path, found, sig, text, why=PURPOSE):
    """Points in program order with their path: [(list, index, node), ...]."""
    for index, node in enumerate(nodes):
        here = path + [(id(nodes), index, node)]
        segment = text[sig[node.start][2]:sig[node.end][3]]
        if MARK not in segment:
            continue
        if node.kind == 'simple':
            if not POINT.fullmatch(segment.strip().rstrip(';').strip()):
                raise Unsupported(why + ': kifejezésben vagy összetett utasításban.')
            found.append(here)
        elif node.kind == 'opaque':
            raise Unsupported(why + ': ciklusban vagy CASE utasításban a folytatási pont nem követhető.')
        elif node.kind == 'if':
            for _, _, branch in node.branches:
                walk(branch, here, found, sig, text, why)
        else:
            for handler in node.handlers:
                if any(MARK in text[sig[n.start][2]:sig[n.end][3]] for n in handler):
                    raise Unsupported(why + ': kivételkezelőben a folytatási pont nem követhető.')
            walk(node.body, here, found, sig, text, why)


def declared_names(sig, first, last):
    """Variables and cursors of a declaration section (constants, exceptions, types, subprograms excluded)."""
    from .libraries import statement_end, subprogram_end
    names, i = [], first
    while i <= last:
        word = sig[i][1].upper() if sig[i][0] == 'ident' else sig[i][1]
        if word in {'PROCEDURE', 'FUNCTION'}:
            i = subprogram_end(sig, i) + 1
            continue
        end = statement_end(sig, i)
        words = [t[1].upper() for t in sig[i:end] if t[0] == 'ident']
        if word == 'CURSOR' and len(words) > 1:
            names.append(words[1])
        elif word not in {'TYPE', 'SUBTYPE', 'PRAGMA'} and len(words) > 1 and words[1] not in {'CONSTANT', 'EXCEPTION'}:
            names.append(words[0])
        i = end + 1
    return names


def transform(body: str) -> tuple[str, int]:
    """The body with numbered commit and screen points and resume guards; (text, number of points)."""
    why = purpose(body)
    sig, root = parse(body, why)
    if any(t[0] == 'ident' and t[1].upper() == 'GOTO' for t in sig):
        raise Unsupported(why + ': GOTO mellett a folytatási pont nem követhető.')
    points, top = [], [root]
    walk(top, [], points, sig, body, why)
    if not points:
        return body, 0
    number = {id(path[-1][2]): k + 1 for k, path in enumerate(points)}
    edits = []  # (start offset, end offset, text, order)

    # Statements in front of a point (in each list on its path): skipped when resuming at that point.
    preceding = {}
    for path in points:
        point = number[id(path[-1][2])]
        for list_id, index, node in path:
            preceding.setdefault(list_id, {}).setdefault(index, set()).add(point)
    lists = {}

    def collect(nodes):
        lists[id(nodes)] = nodes
        for node in nodes:
            for _, _, branch in node.branches:
                collect(branch)
            if node.kind == 'block':
                collect(node.body)
                for handler in node.handlers:
                    collect(handler)
    collect(top)
    for list_id, positions in preceding.items():
        nodes = lists[list_id]
        for index, node in enumerate(nodes):
            skip = sorted({p for later, ps in positions.items() if later > index for p in ps})
            if skip:
                start, end = sig[node.start][2], sig[node.end][3]
                line = body[body.rfind('\n', 0, start) + 1:start]
                margin = line if not line.strip() else ''
                edits.append((start, start, 'IF frm_resume NOT IN (' + ', '.join(map(str, skip)) + ') THEN\n' + margin, 1))
                edits.append((end, end, '\n' + margin + 'END IF;', 0))

    for path in points:
        for _, _, node in path:
            if node.kind == 'if' and not getattr(node, 'done', False):
                node.done = True
                inside = lambda nodes: sorted(number[id(p[-1][2])] for p in points if any(n is x for _, _, x in p for n in nodes))
                every = inside([node])
                for cond_start, cond_end, branch in node.branches:
                    if cond_start is None:
                        continue
                    own = inside(branch)
                    start, end = sig[cond_start][2], sig[cond_end][3]
                    condition = body[start:end]
                    others = 'frm_resume NOT IN (' + ', '.join(map(str, every)) + ') AND (' + condition + ')'
                    new = ('(frm_resume IN (' + ', '.join(map(str, own)) + ') OR (' + others + '))') if own else '(' + others + ')'
                    edits.append((start, end, new, 2))
            elif node.kind == 'block' and not getattr(node, 'done', False):
                node.done = True
                if node.exception_at is not None:
                    at = sig[node.exception_at][3]
                    edits.append((at, at, '\n  WHEN frm_commit_pending THEN\n    RAISE;', 2))
                if node.declare:
                    names = declared_names(sig, *node.declare)
                    for path_point in points:
                        if not any(x is node for _, _, x in path_point):
                            continue
                        stop = path_point[-1][2]
                        before = {t[1].upper() for t in sig[node.declare[1] + 1:stop.start] if t[0] == 'ident'}
                        later = {t[1].upper() for t in sig[stop.end + 1:node.end] if t[0] == 'ident'}
                        for name in names:
                            if name in before and name in later:
                                raise Unsupported(why + ': a(z) ' + name + ' helyi változó a pont előtt kaphat értéket és '
                                                  'utána is használt; a folytatás új kérésben indul.')
    for path in points:
        node = path[-1][2]
        start, end = sig[node.start][2], sig[node.end][2]
        edits.append((start, end, body[start:end].strip().replace(MARK, str(number[id(node)])), 2))
    return apply(body, edits), len(points)


def state_expression(binds, var) -> str:
    """The item values at the point, as one text: the commit endpoint checks it arrived at the same state."""
    parts = []
    for b in binds:
        if b['parameter'] and b['block'] != 'GLOBAL':
            continue
        v = var(b['source'])
        if b['type'] == 'number':
            parts.append(f"TO_CHAR({v}, 'TM9', 'NLS_NUMERIC_CHARACTERS=''.,''')")
        elif b['type'] == 'datetime':
            parts.append(f"TO_CHAR({v}, 'YYYYMMDDHH24MISS')")
        else:
            parts.append(v)
    return ' || CHR(29) || '.join(parts) if parts else 'NULL'


def declarations(resume_var: str, mode_var: str, state: str, commit: bool = True,
                 screen: bool = False) -> tuple[list[str], list[str]]:
    """(variables, subprograms) of a block with commit points and/or screen points."""
    items = [f'  frm_resume PLS_INTEGER := NVL(TO_NUMBER({resume_var}), 0);']
    if commit:
        items += [f'  frm_commit_mode VARCHAR2(10) := {mode_var};']
    items += ['  frm_commit_at PLS_INTEGER;']
    if commit:
        items += ['  frm_commit_state VARCHAR2(32767);']
    if screen:
        items += ["  frm_point_kind VARCHAR2(10) := 'COMMIT';"]
    items += ['  frm_commit_pending EXCEPTION;']
    subprograms = []
    if commit:
        subprograms += ['  PROCEDURE frm_commit_form(p_point PLS_INTEGER) IS',
                        '  BEGIN',
                        '    IF p_point <> frm_resume THEN',
                        '      frm_commit_at := p_point;',
                        f'      frm_commit_state := SUBSTR({state}, 1, 32000);',
                        '      RAISE frm_commit_pending;',
                        '    END IF;',
                        '  END;']
    if screen:
        subprograms += ['  PROCEDURE frm_screen_point(p_point PLS_INTEGER, p_step VARCHAR2) IS',
                        '  BEGIN',
                        '    IF p_point <> frm_resume THEN',
                        '      frm_cmd(p_step);',
                        '      frm_commit_at := p_point;',
                        "      frm_point_kind := 'STEP';",
                        '      RAISE frm_commit_pending;',
                        '    END IF;',
                        '  END;']
    return items, subprograms


HANDLER = ("  WHEN frm_commit_pending THEN\n"
           "    IF frm_commit_mode IS NULL THEN\n"
           "      ROLLBACK TO SAVEPOINT frm_start;\n"
           "    END IF;\n"
           "    frm_cmd('FRM_COMMIT', TO_CHAR(frm_commit_at), frm_commit_state);")
# A screen point: the work before it stays (the request ends normally), the screen resumes after the step.
SCREEN_HANDLER = ("  WHEN frm_commit_pending THEN\n"
                  "    frm_cmd('FRM_RESUME', TO_CHAR(frm_commit_at));")
BOTH_HANDLER = ("  WHEN frm_commit_pending THEN\n"
                "    IF frm_point_kind = 'STEP' THEN\n"
                "      frm_cmd('FRM_RESUME', TO_CHAR(frm_commit_at));\n"
                "    ELSE\n"
                "      IF frm_commit_mode IS NULL THEN\n"
                "        ROLLBACK TO SAVEPOINT frm_start;\n"
                "      END IF;\n"
                "      frm_cmd('FRM_COMMIT', TO_CHAR(frm_commit_at), frm_commit_state);\n"
                "    END IF;")


def handler(commit: int, screen: int) -> str:
    return BOTH_HANDLER if commit and screen else SCREEN_HANDLER if screen else HANDLER


def placeholder_count(text: str) -> int:
    return len(re.findall(re.escape(PLACEHOLDER), text))

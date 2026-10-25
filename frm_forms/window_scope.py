"""screen_windows: the windows left out disappear from the form before anything is generated.

The developer's choice applies to the whole module, as if those windows never existed: their
canvases, the items shown only there (with their triggers), the blocks shown only there (with
every trigger and relation of theirs), and the LOVs / record groups nothing refers to any more.
Form-level triggers, program units and blocks without a visible item stay.
"""
from __future__ import annotations

import re

from .common import MigrationError
from .xmlmodel import get, props, tag


def prune(root, chosen: list[str]) -> dict:
    """Remove what only the windows left out show. Returns what was removed (analysis/window-scope.json)."""
    form = next(e for e in root.iter() if tag(e) == 'formmodule')
    windows = [get(props(w), 'Name') for w in form if tag(w) == 'window']
    unknown = [c for c in chosen if c not in windows]
    if unknown:
        raise MigrationError('SCREEN_WINDOWS: nem létező ablak: ' + ', '.join(unknown))
    left_out = [w for w in windows if w not in chosen]
    removed = {'chosen': list(chosen), 'windows': left_out, 'canvases': [], 'blocks': [], 'items': [], 'lovs': [], 'record_groups': []}
    if not left_out:
        return removed
    canvases = {get(props(c), 'Name'): get(props(c), 'WindowName') for c in form if tag(c) == 'canvas'}
    gone_canvases = {c for c, w in canvases.items() if w in left_out}
    removed['canvases'] = sorted(gone_canvases)
    gone_blocks = set()
    for block in [b for b in form if tag(b) == 'block']:
        name = get(props(block), 'Name')
        items = [i for i in block if tag(i) == 'item']
        shown = [i for i in items if get(props(i), 'CanvasName')]
        hidden_away = [i for i in shown if get(props(i), 'CanvasName') in gone_canvases]
        if shown and len(hidden_away) == len(shown):
            form.remove(block)
            gone_blocks.add(name.upper())
            removed['blocks'].append(name)
            continue
        for item in hidden_away:
            block.remove(item)
            removed['items'].append(name + '.' + get(props(item), 'Name'))
    for block in [b for b in form if tag(b) == 'block']:
        for relation in [r for r in block if tag(r) == 'relation']:
            if get(props(relation), 'DetailDataBlock', 'DetailBlock').upper() in gone_blocks:
                block.remove(relation)
    for node in [n for n in form if tag(n) in ('canvas', 'window')]:
        if get(props(node), 'Name') in gone_canvases or get(props(node), 'Name') in left_out:
            form.remove(node)
    # LOVs and record groups only the removed parts used.
    code = '\n'.join(get(props(e), 'TriggerText', 'ProgramUnitText') or '' for e in form.iter() if tag(e) in ('trigger', 'programunit'))
    mentioned = lambda name: re.search(r'(?<![\w$#])' + re.escape(name) + r'(?![\w$#])', code, re.I) is not None
    used_lovs = {get(props(i), 'LovName').upper() for i in form.iter() if tag(i) == 'item' and get(props(i), 'LovName')}
    for lov in [l for l in form if tag(l) == 'lov']:
        name = get(props(lov), 'Name')
        if name.upper() not in used_lovs and not mentioned(name):
            form.remove(lov)
            removed['lovs'].append(name)
    used_groups = {get(props(l), 'RecordGroupName').upper() for l in form if tag(l) == 'lov' and get(props(l), 'RecordGroupName')}
    for group in [g for g in form if tag(g) == 'recordgroup']:
        name = get(props(group), 'Name')
        if name.upper() not in used_groups and not mentioned(name):
            form.remove(group)
            removed['record_groups'].append(name)
    return removed

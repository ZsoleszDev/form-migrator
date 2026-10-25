"""Relative layout in source units: reading rows, whitespace and frame hierarchy."""
from collections import defaultdict

from .common import MigrationError


def contains(outer, inner):
    return (outer['width'] > 0 and outer['height'] > 0
            and outer['x'] <= inner['x'] and outer['y'] <= inner['y']
            and inner['x'] + max(0, inner['width']) <= outer['x'] + outer['width']
            and inner['y'] + max(0, inner['height']) <= outer['y'] + outer['height'])


def frame_hierarchy(graphics):
    frames = [g for g in graphics if g['visible'] and g['kind'] == 'frame' and g['label']]
    seen = set()
    for frame in frames:
        if not frame['name']: raise MigrationError('SCREEN_FRAME_NAME: a címes grafikai keret Name értéke hiányzik.')
        key = (frame['canvas'], frame['tab'], frame['name'])
        if key in seen: raise MigrationError('SCREEN_FRAME_COLLISION: ' + '/'.join(key))
        seen.add(key)
        parents = [p for p in frames if (p['canvas'], p['tab']) == (frame['canvas'], frame['tab'])
                   and p['width'] * p['height'] > frame['width'] * frame['height'] and contains(p, frame)]
        frame['parent'] = min(parents, key=lambda p: (p['width'] * p['height'], p['name']))['name'] if parents else ''


def same_row(left, right, tolerance):
    if left['y'] == right['y']: return True
    if not left.get('positioned') or not right.get('positioned'): return False
    h = min(left['height'], right['height'])
    if h <= 1: return False  # Forms exports sometimes contain placeholder dimensions.
    overlap = min(left['y'] + left['height'], right['y'] + right['height']) - max(left['y'], right['y'])
    horizontal_overlap = min(left['x'] + left['width'], right['x'] + right['width']) - max(left['x'], right['x'])
    return horizontal_overlap <= 0 and abs(left['y'] - right['y']) <= h * tolerance and overlap >= h * 0.6


def distribute(weights, budget):
    sizes = [1] * len(weights)
    total = sum(weights) or 1
    for _ in range(budget - len(weights)):
        index = max(range(len(weights)), key=lambda j: (weights[j] / total * budget - sizes[j], -j))
        sizes[index] += 1
    return sizes


def spans(items, columns, tolerance=0.25, preserve_gaps=True):
    ordered = sorted(items, key=lambda i: (i['y'], i['x'], i['order']))
    natural = []
    for item in ordered:
        candidates = [row for row in natural if len(row) < columns and all(same_row(other, item, tolerance) for other in row)]
        if candidates:
            min(candidates, key=lambda row: abs(row[0]['y'] - item['y'])).append(item)
        else:
            natural.append([item])
    rows = defaultdict(list)
    for index, row in enumerate(natural):
        for item in row:
            item['natural_row'] = index
            rows[item.get('layout_override', {}).get('row', index)].append(item)
    result = []
    for row_index, members in sorted(rows.items()):
        manual_order = any('order' in i.get('layout_override', {}) for i in members)
        row = sorted(members, key=lambda i: (i.get('layout_override', {}).get('order', i['order']), i['x']) if manual_order else (i['x'], i['order']))
        if len(row) > columns:
            raise MigrationError('SCREEN_LAYOUT_OVERFLOW: túl sok mező egy explicit sorban: ' + ', '.join(i['owner'] for i in row))
        buttons = all(i['widget'] == 'button' for i in row)
        useful = [i['width'] for i in row if i['width'] > 1]
        fallback = min(useful) if useful else 1
        weights = [max(0.1, i['width'] if i['width'] > 1 else fallback * (0.4 if i['widget'] == 'button' else 1)) for i in row]
        overrides = [i.get('layout_override', {}) for i in row]
        gaps = [o.get('col_before', 0) for o in overrides]
        # Infer only visible internal whitespace, never from placeholder widths
        # or a manually reordered row. Manual coordinates take precedence.
        if preserve_gaps and not buttons and not manual_order and not any('row' in o for o in overrides):
            extent = max(i['x'] + i['width'] for i in row) - min(i['x'] for i in row)
            for j in range(1, len(row)):
                previous, item = row[j - 1], row[j]
                if 'col_before' in overrides[j] or not previous.get('positioned') or not item.get('positioned') or min(previous['width'], item['width']) <= 1:
                    continue
                gap = max(0, item['x'] - previous['x'] - previous['width'])
                fraction = gap / extent * columns if extent > 0 else 0
                if fraction >= 1: gaps[j] = int(fraction)
        fixed = {j: o['col'] for j, o in enumerate(overrides) if 'col' in o}
        available = columns - sum(fixed.values()) - sum(gaps)
        autos = [j for j in range(len(row)) if j not in fixed]
        # Source whitespace may shrink to leave one column per field; explicit
        # settings never shrink silently.
        while available < len(autos):
            candidates = [j for j, gap in enumerate(gaps) if gap and 'col_before' not in overrides[j]]
            if not candidates:
                raise MigrationError('SCREEN_LAYOUT_OVERFLOW: col + col_before meghaladja a rácsot: ' + ', '.join(i['owner'] for i in row))
            j = max(candidates, key=lambda j: gaps[j]); gaps[j] -= 1; available += 1
        sizes = [fixed.get(j, 0) for j in range(len(row))]
        if buttons:
            for j in autos: sizes[j] = min(2, available // len(autos))
            if not any('col_before' in o for o in overrides): gaps[0] += columns - sum(sizes) - sum(gaps)
        elif autos:
            for j, size in zip(autos, distribute([weights[j] for j in autos], available)): sizes[j] = size
        for j, item in enumerate(row):
            item.update(col=sizes[j], col_before=gaps[j], col_after=0, row=row_index)
        row[-1]['col_after'] = columns - sum(sizes) - sum(gaps)
        result.extend(row)
    return result


def lookup_groups(sections, lookups):
    """Evidence only: same row + known LOV returns + an adjacent source button."""
    result = []
    for section in sections:
        if section['mode'] != 'form': continue
        items = section['items']
        for source in items:
            lookup = next((l for l in lookups if l['owner'] == source['owner']), None)
            if not lookup: continue
            targets = [i for i in items if i['owner'] in lookup['return_items'] and i['owner'] != source['owner'] and i['row'] == source['row']]
            if not targets: continue
            positions = [source['x'], *[i['x'] for i in targets]]
            low, high = min(positions), max(positions)
            buttons = [i for i in items if i['widget'] == 'button' and i['row'] == source['row'] and low <= i['x'] <= high]
            result.append({'region': section['key'], 'source': source['owner'], 'lov': lookup['name'],
                           'buttons': [i['owner'] for i in buttons], 'returns': [i['owner'] for i in targets],
                           'basis': 'Azonos olvasási sor és explicit LOV ReturnItem; a köztes gomb csak elrendezési jel, működése nincs átírva.'})
    return result

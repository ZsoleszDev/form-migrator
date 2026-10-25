"""Visual preview of the generated screen: every window, tab page and region.

Self-contained, script-free HTML (analysis/screen-preview.html). Screens and tab
pages switch with CSS radio groups, so it works offline from the ZIP and inside
a sandboxed iframe without scripts. It shows structure only: labels, controls,
grid columns and tables; no data and no behaviour.
"""
from __future__ import annotations

from html import escape

WIDGET_HINT = {'number': '0', 'date': 'éééé.hh.nn', 'datetime': 'éééé.hh.nn óó:pp', 'password': '••••••',
               'autocomplete': 'keresés…'}
CSS = """
*{box-sizing:border-box}body{margin:0;font:13px/1.4 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#1f2937;background:#f3f4f6}
header{padding:14px 20px;background:#fff;border-bottom:1px solid #e5e7eb}header h1{margin:0;font-size:17px}
header p{margin:4px 0 0;color:#6b7280;font-size:12px}.sw{position:absolute;opacity:0;pointer-events:none}
nav.screens{display:flex;flex-wrap:wrap;gap:6px;padding:10px 20px;background:#fff;border-bottom:1px solid #e5e7eb}
nav.screens label{cursor:pointer;padding:6px 12px;border:1px solid #d1d5db;border-radius:6px;background:#f9fafb}
nav.screens label small{display:block;color:#6b7280;font-size:11px}main{padding:18px 20px}
.window{display:none;max-width:1100px;margin:0 auto;background:#fff;border:1px solid #d1d5db;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.window>.bar{display:flex;align-items:center;gap:8px;padding:9px 14px;border-bottom:1px solid #e5e7eb;background:#f9fafb;border-radius:8px 8px 0 0}
.window>.bar b{font-size:14px}.badge{font-size:11px;padding:1px 8px;border-radius:10px;background:#e5e7eb;color:#374151}
.badge.main{background:#dbeafe;color:#1e40af}.badge.dialog{background:#fef3c7;color:#92400e}.tech{margin-left:auto;color:#9ca3af;font-size:11px}
.body{padding:14px;display:flex;flex-direction:column;gap:14px}.region{border:1px dashed #cbd5e1;border-radius:6px;padding:10px}
.region>.cap,.section>.cap{font-size:11px;color:#6b7280;margin-bottom:6px}.section{display:flex;flex-direction:column;gap:8px}
.row{display:grid;grid-template-columns:repeat(var(--cols),minmax(0,1fr));gap:8px 12px;align-items:end}
.cell{display:flex;flex-direction:column;gap:3px;min-width:0}.cell>span{font-size:12px;color:#374151;min-height:17px}
.ctl{height:30px;border:1px solid #cbd5e1;border-radius:5px;background:#fff;padding:5px 8px;color:#9ca3af;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ctl.ro{background:#f3f4f6}.ctl.off{opacity:.55}.ctl.area{height:64px}.ctl.num{text-align:right}.ctl.pick:after{content:" ▾";float:right;color:#6b7280}
.check{display:flex;align-items:center;gap:6px;height:30px}.check i{width:16px;height:16px;border:1px solid #94a3b8;border-radius:3px;display:inline-block}
.radio{display:flex;gap:12px;height:30px;align-items:center}.radio i{width:14px;height:14px;border:1px solid #94a3b8;border-radius:50%;display:inline-block;margin-right:4px;vertical-align:-2px}
.btn{height:30px;border:0;border-radius:5px;background:#2563eb;color:#fff;font-weight:600;padding:5px 12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:center}
.sep{display:flex;align-items:center;justify-content:center;height:30px;font-weight:600}.gap{min-height:1px}
.todo{height:30px;border:1px dashed #f59e0b;border-radius:5px;color:#92400e;font-size:11px;padding:6px 8px;background:#fffbeb}
table{width:100%;border-collapse:collapse;font-size:12px}th{text-align:left;background:#f3f4f6;border:1px solid #e5e7eb;padding:6px 8px;font-weight:600}
td{border:1px solid #e5e7eb;padding:6px 8px;height:30px}.tabs{border:1px solid #e5e7eb;border-radius:6px}
.tabs>nav{display:flex;gap:2px;border-bottom:1px solid #e5e7eb;background:#f9fafb;border-radius:6px 6px 0 0}
.tabs>nav label{cursor:pointer;padding:7px 14px;border-bottom:2px solid transparent;color:#4b5563}.tabs .page{display:none;padding:12px}
.empty{color:#9ca3af;font-style:italic}
"""


def text(value) -> str:
    return escape(str(value or ''), quote=True)


def control(item: dict) -> str:
    widget = item['widget']
    label = text(item.get('label'))
    if widget == 'button':
        return f'<div class="cell"><span></span><div class="btn" title="{text(item["owner"])}">{label or "…"}</div></div>'
    if widget == 'checkbox':
        return f'<div class="cell"><span></span><div class="check"><i></i>{label}</div></div>'
    if widget == 'radio':
        options = ''.join(f'<span><i></i>{text(o["label"])}</span>' for o in item.get('options', [])[:4])
        return f'<div class="cell"><span>{label}</span><div class="radio">{options}</div></div>'
    if widget in {'unsupported', 'image', 'tree'}:
        return f'<div class="cell"><span>{label}</span><div class="todo">kézi átültetés: {text(widget)}</div></div>'
    classes = ['ctl']
    hint = WIDGET_HINT.get(widget, '')
    if widget == 'select':
        classes.append('pick')
        hint = (item.get('options') or [{'label': ''}])[0]['label']
    if widget in {'autocomplete', 'date', 'datetime'} or item.get('lov'):
        classes.append('pick')
    if widget == 'number':
        classes.append('num')
    if widget == 'textarea':
        classes.append('area')
    if item.get('readonly') or widget == 'display':
        classes.append('ro')
    if not item.get('enabled', True):
        classes.append('off')
    return (f'<div class="cell" title="{text(item["owner"])}"><span>{label}</span>'
            f'<div class="{" ".join(classes)}">{text(hint)}</div></div>')


def span(width, body='', css='cell gap') -> str:
    return f'<div class="{css}" style="grid-column:span {int(width)}">{body}</div>'


def form_section(section: dict, columns: int) -> str:
    rows, order = {}, []
    for item in section['items']:
        key = item.get('row', 0)
        if key not in rows:
            rows[key] = []; order.append(key)
        rows[key].append(item)
    html = []
    for key in order:
        cells = []
        for item in rows[key]:
            if item.get('col_before'):
                cells.append(span(item['col_before']))
            if item.get('separator') and item.get('separator_col'):
                if item.get('separator_before'):
                    cells.append(span(item['separator_before']))
                cells.append(span(item['separator_col'], f'<span></span><div class="sep">{text(item["separator"])}</div>', 'cell'))
            if item.get('spacer'):
                cells.append(span(item['col'], f'<span>{text(item.get("label"))}</span>'))
            else:
                cells.append(control(item).replace('<div class="cell"', f'<div class="cell" style="grid-column:span {int(item["col"])}"', 1))
            if item.get('col_after'):
                cells.append(span(item['col_after']))
        html.append(f'<div class="row" style="--cols:{columns}">' + ''.join(cells) + '</div>')
    return ''.join(html)


def table_section(section: dict) -> str:
    columns = [i for i in section['items'] if i['widget'] not in {'button', 'image', 'unsupported', 'tree'} and not i.get('spacer')]
    buttons = [i for i in section['items'] if i['widget'] == 'button']
    head = ''.join(f'<th title="{text(i["owner"])}">{text(i.get("label") or i["name"])}</th>' for i in columns)
    rows = ''.join('<tr>' + '<td></td>' * len(columns) + '</tr>' for _ in range(min(max(section.get('records', 1), 1), 3)))
    body = f'<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>' if columns else '<p class="empty">Nincs megjeleníthető oszlop.</p>'
    if buttons:
        body += '<div class="row" style="--cols:12">' + ''.join(
            control(b).replace('<div class="cell"', '<div class="cell" style="grid-column:span 2"', 1) for b in buttons) + '</div>'
    return body


def section_html(section: dict, columns: int) -> str:
    caption = section.get('group_label') or section.get('frame') or ''
    records = f' · {section["records"]} sor' if section['mode'] == 'table' else ''
    cap = f'<div class="cap">{text(caption) + " · " if caption else ""}{text(section["block"])}{records}</div>'
    body = table_section(section) if section['mode'] == 'table' else form_section(section, columns)
    return f'<div class="section">{cap}{body}</div>'


def canvas_html(surface: dict, sections: list[dict], columns: int, group: str) -> str:
    own = [s for s in sections if s['canvas'] == surface['name']]
    kind = (surface.get('type') or 'Content').lower()
    if kind == 'tab' and surface.get('tabs'):
        pages = [t for t in surface['tabs'] if t.get('visible', True)]
        radios, labels, panes, rules = [], [], [], []
        for index, page in enumerate(pages):
            pid = f'{group}-{index}'
            radios.append(f'<input type="radio" class="sw" name="{group}" id="{pid}"{" checked" if index == 0 else ""}>')
            labels.append(f'<label for="{pid}">{text(page.get("label") or page["name"])}</label>')
            content = ''.join(section_html(s, columns) for s in own if s.get('tab') == page['name']) or '<p class="empty">Üres fül.</p>'
            panes.append(f'<div class="page" id="p-{pid}">{content}</div>')
            rules.append(f'#{pid}:checked~nav label[for="{pid}"]{{border-bottom-color:#2563eb;color:#1e40af;font-weight:600}}'
                         f'#{pid}:checked~#p-{pid}{{display:block}}')
        return (f'<div class="tabs"><style>{"".join(rules)}</style>' + ''.join(radios)
                + '<nav>' + ''.join(labels) + '</nav>' + ''.join(panes) + '</div>')
    content = ''.join(section_html(s, columns) for s in own)
    if not content:
        return ''
    if kind == 'stacked':
        return f'<div class="region"><div class="cap">Rétegzett régió: {text(surface["name"])}</div>{content}</div>'
    return content


def preview_html(plan: dict) -> str:
    columns = plan['layout_settings']['columns']
    title = plan['module'].get('title') or plan['module']['key']
    surfaces = {s['name']: s for s in plan['surfaces']}
    windows = [w for w in plan['windows'] if w.get('role') in {'main', 'dialog'}]
    windows.sort(key=lambda w: (w['role'] != 'main', plan['windows'].index(w)))
    if not windows:  # no Window objects: one screen with every surface
        windows = [{'name': '', 'title': title, 'role': 'main', 'canvases': list(surfaces), 'initial_canvas': ''}]
    radios, labels, panes, rules = [], [], [], []
    for index, window in enumerate(windows):
        sid = f's-{index}'
        order = sorted(window['canvases'], key=lambda c: (c != window.get('initial_canvas'),
                                                          {'content': 0, 'tab': 1, 'stacked': 2}.get((surfaces.get(c, {}).get('type') or '').lower(), 3)))
        body = ''.join(canvas_html(surfaces[c], plan['sections'], columns, f't-{index}-{n}') for n, c in enumerate(order) if c in surfaces)
        role = 'Fő képernyő' if window['role'] == 'main' else 'Párbeszédablak' + (' (modális)' if window.get('modal') else '')
        radios.append(f'<input type="radio" class="sw" name="screen" id="{sid}"{" checked" if index == 0 else ""}>')
        labels.append(f'<label for="{sid}">{text(window["title"])}<small>{role}</small></label>')
        panes.append(f'<section class="window" id="w-{sid}"><div class="bar"><b>{text(window["title"])}</b>'
                     f'<span class="badge {"main" if window["role"] == "main" else "dialog"}">{role}</span>'
                     f'<span class="tech">{text(window["name"])}</span></div>'
                     f'<div class="body">{body or "<p class=empty>Nincs megjelenített mező.</p>"}</div></section>')
        rules.append(f'#{sid}:checked~main #w-{sid}{{display:block}}'
                     f'#{sid}:checked~nav label[for="{sid}"]{{background:#2563eb;color:#fff;border-color:#2563eb}}'
                     f'#{sid}:checked~nav label[for="{sid}"] small{{color:#dbeafe}}')
    nav = '<nav class="screens">' + ''.join(labels) + '</nav>' if len(windows) > 1 else ''
    return ('<!doctype html><html lang="hu"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{text(title)} – előnézet</title><style>{CSS}{"".join(rules)}</style></head><body>'
            f'<header><h1>{text(title)}</h1><p>A generált képernyő szerkezete: feliratok, vezérlők, rácsoszlopok és táblázatok; '
            f'adatok és működés nélkül. {len(windows)} képernyő.</p></header>'
            + ''.join(radios) + nav + '<main>' + ''.join(panes) + '</main></body></html>\n')

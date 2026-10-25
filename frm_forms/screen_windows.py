"""Window ownership and presentation, independent of module and object names."""
import re

from .common import MigrationError, unique_names
from .xmlmodel import canonical, get, props, tag


class WindowSelectionRequired(MigrationError):
    """The form has several windows; the developer picks the ones to generate (web: with a preview)."""
    def __init__(self, message, candidates):
        super().__init__(message)
        self.candidates = candidates


def select_windows(form, surfaces, sections, graphics, config, module, title):
    """Apply screen_windows (the developer's choice). Returns the canvases of the windows left out.

    With screen_window_selection = 'ask' and no answer yet, a form with more than one window that
    shows something raises WindowSelectionRequired: every window with its blocks, field count and
    a simplified, script-free preview.
    """
    # Canvas -> window from the form itself: the surfaces get their window only in build_windows.
    window_of = {get(props(c), 'Name'): get(props(c), 'WindowName') for c in form.iter() if tag(c) == 'canvas'}
    used = []
    for section in sections:
        window = window_of.get(section['canvas'])
        if window and window not in used:
            used.append(window)
    chosen = [w for w in config.get('screen_windows') or []]
    if not chosen:
        if config.get('screen_window_selection') != 'ask' or len(used) < 2:
            return set()
        from .screen_preview import preview_html
        titles = {get(props(w), 'Name'): get(props(w), 'Title') for w in form.iter() if tag(w) == 'window'}
        choices = []
        for window in used:
            canvases = [s['name'] for s in surfaces if window_of.get(s['name']) == window]
            own = [s for s in sections if window_of.get(s['canvas']) == window]
            preview = preview_html({'layout_settings': {'columns': config['layout_columns']},
                                    'module': {'key': module, 'title': titles.get(window) or window},
                                    'surfaces': [s for s in surfaces if s['name'] in canvases], 'sections': own,
                                    'windows': [{'name': window, 'title': titles.get(window) or window, 'role': 'main',
                                                 'canvases': canvases, 'initial_canvas': ''}]})
            choices.append({'name': window, 'title': titles.get(window) or window, 'blocks': sorted({s['block'] for s in own}),
                            'items': sum(len(s['items']) for s in own), 'first_navigation': False, 'preview': preview})
        raise WindowSelectionRequired('SCREEN_WINDOWS_REQUIRED: a form több ablakból áll (' + ', '.join(used)
                                      + '). Add meg a screen_windows beállításban a generálandókat.', choices)
    known = {w for w in window_of.values() if w}
    unknown = [w for w in chosen if w not in known]
    if unknown:
        raise MigrationError('SCREEN_WINDOWS: nem létező ablak: ' + ', '.join(unknown))
    excluded = {canvas for canvas, window in window_of.items() if window not in chosen}
    sections[:] = [s for s in sections if s['canvas'] not in excluded]
    surfaces[:] = [s for s in surfaces if s['name'] not in excluded]
    for graphic in graphics:
        if graphic.get('canvas') in excluded:
            graphic['reason'] = 'A fejlesztő nem kérte ezt az ablakot (screen_windows).'
            graphic['framework'] = True
    return excluded


class PrimaryWindowRequired(MigrationError):
    """Several windows can be the main screen; the user has to choose one.

    The message stays the CLI instruction; candidates lets an interactive
    client (the web UI) ask instead of failing.
    """
    def __init__(self, message, candidates):
        super().__init__(message)
        self.candidates = candidates


def flag(properties, key, default):
    value = get(properties, key)
    if value == '': return default
    if str(value).lower() not in {'true', 'false', 'yes', 'no', '1', '0'}:
        raise MigrationError('SCREEN_WINDOW_BOOLEAN: ' + key + '=' + str(value))
    return str(value).lower() in {'true', 'yes', '1'}


def build_windows(form, canvases, surfaces, sections, config, text, notices, metadata, skipped_canvases=()):
    windows = {}
    for element in form.iter():
        if tag(element) != 'window': continue
        p = props(element); name = get(p, 'Name')
        provenance = metadata.get(id(element), {}).get('properties', {})
        if not name or name in windows:
            raise MigrationError('SCREEN_WINDOW_NAME: hiányzó vagy ismétlődő Window.Name: ' + name)
        windows[name] = {'name': name, 'title': text(get(p, 'Title') or name),
                         'style': get(p, 'WindowStyle') or 'Document', 'modal': flag(p, 'Modal', False),
                         'visible': flag(p, 'Visible', True), 'close_allowed': flag(p, 'CloseAllowed', True),
                         'primary_canvas': get(p, 'PrimaryCanvas'), 'canvases': [], 'content_canvases': [],
                         'initial_canvas': '', 'initial_visible': False, 'role': 'unused', 'reason': 'Nincs megjelenített canvas.',
                         'property_sources': {key: (provenance.get(canonical(prop), {}).get('source', 'effective_xml') if get(p, prop) != '' else 'default') for key, prop in
                                              [('style', 'WindowStyle'), ('modal', 'Modal'), ('visible', 'Visible'), ('close_allowed', 'CloseAllowed')]}}
    by_canvas = {s['name']: s for s in surfaces}

    def assign(surface):
        explicit = get(props(canvases[surface['name']]), 'WindowName') if surface['name'] in canvases else ''
        owners = [w['name'] for w in windows.values() if w['primary_canvas'] and w['primary_canvas'] == surface['name']]
        if explicit and explicit not in windows:
            raise MigrationError('SCREEN_UNKNOWN_WINDOW: ' + surface['name'] + ' → ' + explicit)
        if len(owners) > 1 and not explicit:
            raise MigrationError('SCREEN_WINDOW_CANVAS_CONFLICT: WindowName / PrimaryCanvas: ' + surface['name'])
        # Canvas.WindowName decides where Forms shows the canvas; another window's PrimaryCanvas naming it is a stale
        # reference, ignored below (SCREEN_PRIMARY_CANVAS_IGNORED).
        target = explicit or (owners[0] if owners else '')
        basis = 'Canvas.WindowName' if explicit else 'Window.PrimaryCanvas' if owners else ''
        if not target and len(windows) == 1:
            target = next(iter(windows)); basis = 'Az egyetlen deklarált ablak; következtetés, ellenőrizendő.'
            notices.append({'code': 'SCREEN_INFERRED_CANVAS_WINDOW', 'owner': surface['name'], 'detail': basis + ' ' + target})
        if not target and windows:
            raise MigrationError('SCREEN_CANVAS_WINDOW_AMBIGUOUS: ' + surface['name'] + ': WindowName vagy egyértelmű PrimaryCanvas kapcsolat szükséges; ellenőrizd az OLB-feloldást.')
        surface.update(window=target, window_source=basis or 'Nincs deklarált ablak; inline canvas.')
        if target:
            window = windows[target]
            window['canvases'].append(surface['name'])
            surface.update(modal=window['modal'], title=window['title'])
    for surface in surfaces: assign(surface)

    # A blank primary canvas can still be the host of populated stacked/tab
    # canvases. Include it only for an already used window, never by its name.
    for window in windows.values():
        primary = window['primary_canvas']
        if not window['canvases'] or not primary or primary in skipped_canvases: continue
        if primary not in canvases:
            raise MigrationError('SCREEN_UNKNOWN_PRIMARY_CANVAS: ' + window['name'] + ' → ' + primary)
        problem = ''
        if primary not in by_canvas:
            node = canvases[primary]; p = props(node)
            kind = get(p, 'CanvasType') or 'Content'
            if any(tag(c) == 'tabpage' for c in node) or canonical(kind) != 'content':
                problem = 'üres, ' + ('Tab' if any(tag(c) == 'tabpage' for c in node) else kind) + ' típusú canvas'
            elif get(p, 'WindowName') and get(p, 'WindowName') != window['name']:
                problem = 'üres canvas, a(z) ' + get(p, 'WindowName') + ' ablakhoz tartozik'
            else:
                surface = {'name': primary, 'key': '', 'type': kind,
                           'visible': flag(p, 'Visible', True), 'window': '', 'modal': False, 'title': '', 'tabs': []}
                surfaces.append(surface); by_canvas[primary] = surface; assign(surface)
        if not problem and by_canvas[primary]['window'] != window['name']:
            problem = 'a(z) ' + (by_canvas[primary]['window'] or '(ablak nélküli)') + ' ablakhoz tartozik'
        elif not problem and canonical(by_canvas[primary]['type']) != 'content':
            problem = by_canvas[primary]['type'] + ' típusú, nem Content canvas'
        if problem:
            # Designer forms (CG$POPUP_n ...) may name a stacked or another window's canvas: the window still shows
            # its own canvases, it starts on its first Content canvas.
            notices.append({'code': 'SCREEN_PRIMARY_CANVAS_IGNORED', 'owner': window['name'],
                            'detail': 'A PrimaryCanvas (' + primary + ') nem az ablak saját Content canvasa: ' + problem
                                      + '. Figyelmen kívül hagyva; a kezdő canvas az ablak első Content canvasa.'})
            window['primary_canvas'] = ''

    used = [w for w in windows.values() if w['canvases']]
    for window in used:
        if canonical(window['style']) not in {'document', 'dialog'}:
            raise MigrationError('SCREEN_WINDOW_STYLE: ' + window['name'] + ': ' + window['style'])
        window['content_canvases'] = [s['name'] for s in surfaces if s['window'] == window['name'] and canonical(s['type']) == 'content']
        contents = window['content_canvases']
        primary = window['primary_canvas'] if window['primary_canvas'] not in skipped_canvases else ''
        window['initial_canvas'] = primary or next((c for c in contents if by_canvas[c]['visible']), contents[0] if contents else '')
        if len(contents) > 1 and not window['primary_canvas']:
            notices.append({'code': 'SCREEN_CONTENT_CANVAS_INFERRED', 'owner': window['name'],
                            'detail': 'Több Content canvas, PrimaryCanvas nélkül. Kezdő canvas: ' + window['initial_canvas'] + '; ellenőrizendő.'})
    candidates = [w for w in used if not w['modal'] and canonical(w['style']) != 'dialog']
    selected = config['screen_primary_window']; basis = 'screen_primary_window konfiguráció'
    if selected:
        if selected not in {w['name'] for w in candidates}:
            raise MigrationError('SCREEN_PRIMARY_WINDOW: létező, használt, nem modális Document ablak szükséges: ' + selected)
    elif len(candidates) == 1:
        selected = candidates[0]['name']; basis = 'Az egyetlen használt, nem modális Document ablak.'
    elif len(candidates) > 1:
        first_block = get(props(form), 'FirstNavigationBlock')
        first_windows = {by_canvas[s['canvas']]['window'] for s in sections if s['block'] == first_block}
        first_windows &= {w['name'] for w in candidates}
        if len(first_windows) != 1 and config.get('screen_primary_window_auto'):
            # Survey: no question, the first candidate stands in (the report counts, it does not ship).
            first_windows = {candidates[0]['name']}
        if len(first_windows) != 1:
            choices = [{'name': w['name'], 'title': w['title'],
                        'blocks': sorted({s['block'] for s in sections if by_canvas[s['canvas']]['window'] == w['name']}),
                        'first_navigation': w['name'] in first_windows} for w in candidates]
            raise PrimaryWindowRequired('SCREEN_PRIMARY_WINDOW_REQUIRED: több lehetséges főablak: ' + ', '.join(w['name'] for w in candidates)
                                        + '. Add meg a screen_primary_window beállítást; az ablaknevek alapján nem választunk.', choices)
        selected = next(iter(first_windows)); basis = 'Form.FirstNavigationBlock → canvas → WindowName.'
    for window in used:
        if window['name'] == selected:
            window.update(role='main', initial_visible=window['visible'], reason=basis)
        else:
            reason = 'Modal=true' if window['modal'] else 'WindowStyle=Dialog' if canonical(window['style']) == 'dialog' else 'A főablaktól különálló másodlagos Document ablak.'
            window.update(role='dialog', reason=reason)
        for canvas in window['canvases']: by_canvas[canvas]['window_role'] = window['role']
    for surface in surfaces:
        surface.setdefault('window_role', 'inline')
        if canonical(surface['type']) == 'stacked':
            notices.append({'code': 'SCREEN_STACKED_CANVAS', 'owner': surface['name'],
                            'detail': 'A saját ablakán belüli kapcsolható régió; az átfedés/z-sorrend és SHOW_VIEW/GO_ITEM hatásai hostbekötést igényelnek.'})
    keys = unique_names(list(windows))
    for window in windows.values(): window['key'] = keys[window['name']]
    return list(windows.values())


def navigation_references(discovery, plan):
    """Review evidence only: no condition or trigger is executed or auto-wired."""
    windows = {w['name']: w for w in plan['windows']}
    canvases = {s['name']: s for s in plan['surfaces']}
    result = []
    builtins = {'SHOW_WINDOW', 'HIDE_WINDOW', 'SHOW_VIEW', 'HIDE_VIEW', 'GO_BLOCK', 'GO_ITEM', 'SET_WINDOW_PROPERTY'}
    for code in discovery['code']:
        for call in code['calls']:
            if call['name'] not in builtins: continue
            args = call['arguments']; literal = re.fullmatch(r"'((?:[^']|'')*)'", args[0].strip()) if args else None
            target = literal[1].replace("''", "'") if literal else ''
            canvas = ''; window = ''; status = 'dynamic' if not literal else 'unresolved'
            if literal:
                if call['name'] in {'SHOW_WINDOW', 'HIDE_WINDOW', 'SET_WINDOW_PROPERTY'}:
                    window = target if target in windows else ''
                elif call['name'] in {'SHOW_VIEW', 'HIDE_VIEW'}:
                    canvas = target if target in canvases else ''
                else:
                    matches = {s['canvas'] for s in plan['sections'] for i in s['items']
                               if (call['name'] == 'GO_BLOCK' and s['block'] == target) or (call['name'] == 'GO_ITEM' and i['owner'] == target)}
                    if len(matches) == 1: canvas = next(iter(matches))
                if canvas: window = canvases[canvas]['window']
                if canvas or window: status = 'resolved' if canvas or windows[window]['role'] != 'unused' else 'not-rendered'
            result.append({'owner': code['owner'], 'event': code['name'], 'call': call['text'], 'line': call['line'],
                           'source_file': 'analysis/discovery/' + code['source_file'], 'window': window,
                           'canvas': canvas, 'status': status, 'execution': 'review-only'})
    return result


def controls(plan):
    used = [w for w in plan['windows'] if w['role'] != 'unused']
    content = {w['name']: w['initial_canvas'] for w in used if len(w['content_canvases']) > 1}
    needed = bool(content) or any(w['role'] == 'dialog' or not w['visible'] for w in used) or any(
        not s['visible'] or canonical(s['type']) == 'stacked' for s in plan['surfaces'])
    return {'windows': {w['name']: w['initial_visible'] for w in used} if needed else {},
            'canvases': {s['name']: s['visible'] for s in plan['surfaces']} if needed else {}, 'content': content}

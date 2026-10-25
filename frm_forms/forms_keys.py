"""DO_KEY falls back to a built-in only if no applicable key trigger exists.

Source: Oracle Forms Builder Reference, DO_KEY built-in (a73074, pp. 95-96).
An unknown current item conservatively includes every item in the current block.
This preserves overrides until their complete event chain has an adapter.
"""


KEY_EVENTS = {
    'BLOCK_MENU': 'KEY-MENU',
    'CLEAR_BLOCK': 'KEY-CLRBLK', 'CLEAR_FORM': 'KEY-CLRFRM', 'CLEAR_RECORD': 'KEY-CLRREC',
    'COMMIT_FORM': 'KEY-COMMIT', 'CREATE_RECORD': 'KEY-CREREC', 'DELETE_RECORD': 'KEY-DELREC',
    'ENTER_QUERY': 'KEY-ENTQRY', 'EXECUTE_QUERY': 'KEY-EXEQRY', 'EXIT_FORM': 'KEY-EXIT',
    'LIST_VALUES': 'KEY-LISTVAL', 'NEXT_BLOCK': 'KEY-NXTBLK', 'NEXT_ITEM': 'KEY-NEXT-ITEM',
    'NEXT_RECORD': 'KEY-NXTREC', 'PREVIOUS_BLOCK': 'KEY-PRVBLK',
    'PREVIOUS_ITEM': 'KEY-PREV-ITEM', 'PREVIOUS_RECORD': 'KEY-PRVREC',
    'COUNT_QUERY': 'KEY-CQUERY', 'DOWN': 'KEY-DOWN', 'DUPLICATE_ITEM': 'KEY-DUP-ITEM',
    'DUPLICATE_RECORD': 'KEY-DUPREC', 'EDIT_TEXTITEM': 'KEY-EDIT', 'ENTER': 'KEY-ENTER',
    'HELP': 'KEY-HELP', 'LOCK_RECORD': 'KEY-UPDREC', 'NEXT_KEY': 'KEY-NXTKEY', 'NEXT_SET': 'KEY-NXTSET',
    'PRINT': 'KEY-PRINT', 'SCROLL_DOWN': 'KEY-SCRDOWN', 'SCROLL_UP': 'KEY-SCRUP', 'UP': 'KEY-UP',
}


def overrides(model, builtin, block=None, item=None):
    event = KEY_EVENTS.get(builtin.upper())
    if event is None:
        return []
    block = block.upper() if block else None
    item = item.upper() if item else None
    return [t for t in model['triggers'] if t['event'] == event and
            (not t['block'] or (block is None or t['block'] == block) and
             (not t['item'] or item is None or t['item'] == item))]


def screen_context(form):
    """Extract only event ownership and local names from the resolved screen XML."""
    from .xmlmodel import get, props, tag
    model = {'triggers': [], 'program_units': []}

    def visit(node, block=None, item=None):
        for child in node:
            kind = tag(child)
            p = props(child)
            name = get(p, 'Name').upper()
            if kind == 'block':
                visit(child, name)
            elif kind == 'item':
                visit(child, block, name)
            elif kind == 'trigger':
                owner = block + ('.' + item if item else '') if block else get(props(form), 'Name')
                model['triggers'].append({'event': name, 'block': block, 'item': item,
                                          'id': owner + ':' + name, 'source': get(p, 'TriggerText', 'Text')})
            elif kind == 'programunit':
                model['program_units'].append(p)
    visit(form)
    return model

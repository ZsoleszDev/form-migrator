"""Adapt generated Java DTO accesses without ever rewriting SQL or comments."""
import re

from .common import MigrationError
from .service_inline import mask


def replace_ranges(source, edits):
    pieces, previous = [], 0
    for start, end, value in edits:
        pieces.extend((source[previous:start], value))
        previous = end
    pieces.append(source[previous:])
    return ''.join(pieces)


def rewrite(source, pattern, replacement):
    edits = []
    for match in pattern.finditer(mask(source)):
        value = replacement(match)
        if value != source[match.start():match.end()]:
            edits.append((match.start(), match.end(), value))
    return replace_ranges(source, edits)


def capitalized(field):
    return field[0].upper() + field[1:]


def bean_source(source, types, blocks):
    """Known generator locals only. Unknown mutations fail instead of losing writes."""
    fields = {i['field'] for b in blocks for i in b['items'] if i['kind'] != 'button'}
    fields.update(b['rowid_item']['field'] for b in blocks if b.get('rowid_item'))  # the ROWID key of a block without PK
    fields.update(v['field'] for b in blocks for v in b.get('query_plan', {}).get('binds', []))
    if fields:
        member = re.compile(r'(?<![\w$.])(?P<receiver>row|original|current|saved|key|criteria)\s*\.\s*'
                            r'(?P<field>' + '|'.join(re.escape(f) for f in sorted(fields, key=len, reverse=True)) + r')(?![\w$])(?!\s*\()')
        visible = mask(source)
        edits = []
        mutation = re.compile(r'\s*(?:\+\+|--|[+*/%&|^\-]=|<<=|>>>=|>>=)')
        assign = re.compile(r'\s*=(?!=)')
        for match in member.finditer(visible):
            if source[match.start():match.end()] != match[0]:
                raise MigrationError('COMPANY_DTO: kommenttel megszakított mezőhivatkozás nem támogatott.')
            before = match.start()
            while before and visible[before - 1].isspace(): before -= 1
            if mutation.match(visible, match.end()) or visible[max(0, before - 2):before] in {'++', '--'}:
                raise MigrationError('COMPANY_DTO: nem támogatott mezőmódosítás: ' + match[0])
            assignment = assign.match(visible, match.end())
            if not assignment:
                continue
            start = assignment.end()
            if source[match.end():start] != visible[match.end():start]:
                raise MigrationError('COMPANY_DTO: kommenttel megszakított értékadási operátor nem támogatott.')
            end, depth = start, 0
            while end < len(visible):
                char = visible[end]
                if char == ';' and depth == 0:
                    break
                if char in '([{': depth += 1
                elif char in ')]}': depth -= 1
                if depth < 0:
                    raise MigrationError('COMPANY_DTO: hibás mezőértékadás: ' + match[0])
                end += 1
            if end == len(visible):
                raise MigrationError('COMPANY_DTO: lezáratlan mezőértékadás: ' + match[0])
            edits.append((match.start(), end, match['receiver'] + '.set' + capitalized(match['field']) +
                          '(' + source[start:end].lstrip() + ')'))
        for left, right in zip(edits, edits[1:]):
            if left[1] > right[0]:
                raise MigrationError('COMPANY_DTO: egymásba ágyazott mezőértékadás nem támogatott.')
        source = replace_ranges(source, edits)
        source = rewrite(source, member, lambda m: m['receiver'] + '.get' + capitalized(m['field']) + '()')
    accessors = re.compile(r'\brequest\.(criteria|offset|limit|term|parameters|blocks|original|value|changes\w+|action\w*)\(\)')
    source = rewrite(source, accessors, lambda m: 'request.get' + capitalized(m[1]) + '()')
    # Forms COMMIT_FORM (commit_chain): the change lists, the update pairs and the saved results.
    changes = re.compile(r'\b(request\.getChanges\w+\(\))\.(inserted|updated|deleted)\(\)|\b(update)\.(original|value)\(\)'
                         r'|\b(committed)\.(row|messages)\(\)')
    source = rewrite(source, changes, lambda m: (m[1] + '.get' + capitalized(m[2]) + '()') if m[1] else
                     (m[3] or m[5]) + '.get' + capitalized(m[4] or m[6]) + '()')
    return rewrite(source, re.compile(r'\b[A-Za-z_$][A-Za-z0-9_$]*\b'), lambda m: types.get(m[0], m[0]))

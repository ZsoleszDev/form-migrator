"""Place generated block operations directly in the DPS ServiceImpl.

Only generated Java is processed here, never user code. Strings/comments are
masked before inspecting methods, so SQL text and messages remain byte-for-byte.
"""
from __future__ import annotations

from functools import lru_cache
import re
from textwrap import dedent

from .common import MigrationError, name

JAVA_NONCODE = re.compile(r'"""(?:\\.|(?!""")[\s\S])*"""|"(?:\\.|[^"\\])*"|'
                          r"'(?:\\.|[^'\\])*'|//[^\n]*|/\*[\s\S]*?\*/")
METHOD = re.compile(r'(?m)^[ \t]*(?:public|private)\s+[\w.<>, ?\[\]]+\s+'
                    r'(?P<name>\w+)\([^)]*\)\s*(?:throws\s+[\w., ]+)?\s*\{')


def mask(text):
    return JAVA_NONCODE.sub(lambda m: ''.join('\n' if c == '\n' else ' ' for c in m[0]), text)


@lru_cache(maxsize=4)
def support_methods(text):
    """methods() of the support code every button method looks into: parsed once per ServiceImpl (read only)."""
    return methods(text)


def methods(text):
    visible = mask(text)
    result = {}
    for match in METHOD.finditer(visible):
        start, end, depth = match.end(), match.end(), 1
        while end < len(visible) and depth:
            if visible[end] == '{': depth += 1
            elif visible[end] == '}': depth -= 1
            end += 1
        if depth or match['name'] in result:
            raise MigrationError('SERVICE_TEMPLATE: hibás vagy ismételt metódus: ' + match['name'])
        result[match['name']] = {'header': text[match.start():start],
                                 'body': text[start:end - 1]}
    return result


# An error list nobody fills: FormsChecks.throwIfAny of an empty list does nothing.
NO_CHECKS = re.compile(r'var\s+errors\s*=\s*new\s+ArrayList<String>\(\)\s*;\s*FormsChecks\.throwIfAny\(\s*errors\s*\)\s*;')
IDENTIFIER = re.compile(r'\s*[A-Za-z_$][\w$]*\s*')
# backend_trigger_mode=ai: a hook whose body is the commented Forms code (ai_triggers.region).
AI_REGION = re.compile(r'//region Eredeti Forms-k[óo]d')


def no_effect(body):
    """No code at all, or only an unused error list (a validate() without field checks)."""
    return not NO_CHECKS.sub('', mask(body)).strip().strip(';').strip()


def close_paren(text, start):
    depth = 0
    for index in range(start, len(text)):
        if text[index] == '(':
            depth += 1
        elif text[index] == ')':
            depth -= 1
            if not depth:
                return index
    raise MigrationError('SERVICE_TEMPLATE: lezáratlan zárójel')


def spans(text, generics):
    """Top-level comma-separated parts of a (masked) parameter or argument list as (start, end)."""
    if not text.strip():
        return []
    opening, closing = ('([{<', ')]}>') if generics else ('([{', ')]}')
    result, depth, start = [], 0, 0
    for index, char in enumerate(text):
        if char in opening:
            depth += 1
        elif char in closing:
            depth -= 1
        elif char == ',' and not depth:
            result.append((start, index))
            start = index + 1
    result.append((start, len(text)))
    return result


def drop_unused_parameters(parsed, entries):
    """PMD UnusedFormalParameter: a private helper keeps only the parameters it reads, and its
    calls lose the same arguments (plain variables only, so no expression is skipped)."""
    changed = True
    while changed:
        changed = False
        for key in sorted(parsed.keys() - entries):
            if AI_REGION.search(parsed[key]['body']):
                continue  # an AI hook keeps its whole signature: the Java assistant writes code that reads it
            header = mask(parsed[key]['header'])
            name_match = re.search(r'(?<![\w$])' + key + r'\s*\(', header)
            if not name_match:
                continue
            open_index = name_match.end() - 1
            close_index = close_paren(header, open_index)
            params = [(open_index + 1 + a, open_index + 1 + b) for a, b in spans(header[open_index + 1:close_index], True)]
            names = [header[a:b].split()[-1] for a, b in params]
            body = mask(parsed[key]['body'])
            unused = [i for i, n in enumerate(names) if not re.search(r'(?<![\w$.])' + re.escape(n) + r'(?![\w$])', body)]
            if not unused:
                continue
            sites, usable = [], True
            for other, method in parsed.items():
                text = mask(method['body'])
                if re.search(r'::\s*' + key + r'(?![\w$])', text):
                    usable = False
                    break
                found = []
                for call in re.finditer(r'(?<![\w$.])' + key + r'\s*\(', text):
                    start = call.end() - 1
                    end = close_paren(text, start)
                    args = [(start + 1 + a, start + 1 + b) for a, b in spans(text[start + 1:end], False)]
                    if len(args) != len(names) or any(not IDENTIFIER.fullmatch(text[args[i][0]:args[i][1]]) for i in unused):
                        usable = False
                        break
                    found.append((start, end, args))
                if not usable or any(a[0] < b[0] < a[1] for a in found for b in found):  # nested calls: leave as is
                    usable = False
                    break
                sites += [(other, *site) for site in found]
            if not usable:
                continue
            for other in {site[0] for site in sites}:
                text = parsed[other]['body']
                for _, start, end, args in sorted((s for s in sites if s[0] == other), key=lambda s: -s[1]):
                    kept = ', '.join(text[a:b].strip() for i, (a, b) in enumerate(args) if i not in unused)
                    text = text[:start + 1] + kept + text[end:]
                parsed[other]['body'] = text
            raw = parsed[key]['header']
            kept = ', '.join(raw[a:b].strip() for i, (a, b) in enumerate(params) if i not in unused)
            parsed[key]['header'] = raw[:open_index + 1] + kept + raw[close_index:]
            changed = True


def rewrite(text, pattern, replacement):
    """Replace only matches in Java code, never in an SQL literal or comment."""
    matches = list(pattern.finditer(mask(text)))
    for match in reversed(matches):
        text = text[:match.start()] + replacement(match) + text[match.end():]
    return text


def inline_block(source, block, operations):
    """Return public operation bodies and only their reachable private helpers."""
    parsed = methods(source)
    entries = {op for op, enabled in operations.items() if enabled}
    if not entries <= parsed.keys():
        raise MigrationError('SERVICE_TEMPLATE: hiányzó művelet: ' + ', '.join(sorted(entries - parsed.keys())))
    # Empty generated hooks and their wrappers add no behaviour. Remove their
    # pure variable-argument calls, then recompute until no wrapper is empty.
    def hook_calls(keys):
        return re.compile(r'(?m)^[ \t]*(?:for\s*\(var row : rows\)\s*)?(?:' + '|'.join(sorted(keys)) +
                          r')\(\s*(?:row|original|current|saved)(?:\s*,\s*context)?\s*\);[ \t]*\n?')
    while True:
        # Only a hook whose every use is such a statement call can go.
        empty = set()
        for key in {key for key, m in parsed.items() if no_effect(m['body'])} - entries:
            uses = re.compile(r'(?<![\w$.])' + key + r'\s*\(|::\s*' + key + r'(?![\w$])')
            others = [mask(m['body']) for other, m in parsed.items() if other != key]
            if sum(len(uses.findall(t)) for t in others) == sum(len(hook_calls({key}).findall(t)) for t in others):
                empty.add(key)
        if not empty:
            break
        call = hook_calls(empty)
        for key in empty:
            del parsed[key]
        for method in parsed.values():
            method['body'] = rewrite(method['body'], call, lambda _: '')
    references = re.compile(r'(?<![\w$.])(\w+)(?=\s*\()|(?<=this::)(\w+)')
    reachable = set(entries)
    pending = list(sorted(entries))
    while pending:
        current = pending.pop()
        for match in references.finditer(mask(parsed[current]['body'])):
            callee = match[1] or match[2]
            if callee in parsed and callee not in reachable:
                reachable.add(callee); pending.append(callee)
    # Template order, not set order: the generated class is the same on every run.
    parsed = {key: method for key, method in parsed.items() if key in reachable}
    drop_unused_parameters(parsed, entries)
    # Operation first, then the block: mapB / validateRulesVElek. PMD MethodNamingConventions
    # ([a-z][a-zA-Z0-9]*) allows no underscore; the operation prefix keeps the second character
    # lowercase for Checkstyle MethodName as well.
    block_name = name(block['class'])
    suffix = block_name[:1].upper() + block_name[1:]
    renamed = {key: key + suffix for key in reachable - entries}
    def rename(text):
        return rewrite(text, references, lambda m: renamed.get(m[1] or m[2], m[0]))
    bodies = {op: dedent(rename(parsed[op]['body'])).strip() for op in sorted(entries)}
    helpers = []
    for key, method in parsed.items():
        if key not in renamed:
            continue
        header = re.sub(r'^\s*(?:public|private)\s+', 'private ', rename(method['header']))
        helpers.append('    ' + header.strip() + '\n' +
                       '\n'.join('        ' + line if line else '' for line in dedent(rename(method['body'])).strip().splitlines())
                       + '\n    }')
    return bodies, '\n\n'.join(helpers)

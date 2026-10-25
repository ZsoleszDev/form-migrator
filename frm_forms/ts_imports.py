"""Angular imports from the same map as Java (java-imports.json): every value that is not a dotted
Java class name is an Angular import (wf-package, @ff/ui, src/app/core/wff).

"ToastService": "app/shared/toast/toast.service" gives `import { ToastService } from 'app/shared/toast/toast.service';`
in every generated component that uses ToastService. The map is authoritative: the name is moved
out of any other import, and the generator's "TODO: importáld" line for it disappears.
"""
from __future__ import annotations

import re

IMPORT = re.compile(r"^import\s*\{([^}]*)\}\s*from\s*'([^']+)';[ \t]*$", re.M)
TODO = re.compile(r"^// TODO: importáld a saját csomagodból: ([^\n]*)\n?", re.M)


def code(text: str) -> str:
    """Code only: string, template-literal and comment contents blanked."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith('//', i):
            j = text.find('\n', i); j = n if j < 0 else j
            out.append(' '); i = j
        elif text.startswith('/*', i):
            j = text.find('*/', i + 2); j = n if j < 0 else j + 2
            out.append(' '); i = j
        elif c in '\'"`':
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == '\\' else 1
            out.append(c + c); i = j + 1
        else:
            out.append(c); i += 1
    return ''.join(out)


def tidy(text: str, mapping: dict) -> tuple[str, dict]:
    """Imports from the map for the names the component uses; report of names still without import."""
    body = IMPORT.sub('', text)
    visible = code(body)
    used = set(re.findall(r'(?<![\w$.])[A-Za-z_$][\w$]*', visible))
    declared = set(re.findall(r'\b(?:class|interface|type|enum|const|let|function)\s+([A-Za-z_$][\w$]*)', visible))
    wanted = {name: path for name, path in mapping.items() if name in used and name not in declared}
    statements = [(m.start(), m.end(), [p.strip() for p in m.group(1).split(',') if p.strip()], m.group(2)) for m in IMPORT.finditer(text)]
    result, last = [], 0
    for start, end, names, path in statements:
        keep = [n for n in names if n.split(' as ')[0].strip() not in wanted or wanted[n.split(' as ')[0].strip()] == path]
        result.append(text[last:start])
        result.append('import { ' + ', '.join(keep) + " } from '" + path + "';" if keep else '\x00')
        last = end
    result.append(text[last:])
    text = re.sub(r'\x00\n?', '', ''.join(result))
    present = {(n.split(' as ')[0].strip(), m.group(2)) for m in IMPORT.finditer(text) for n in m.group(1).split(',') if n.strip()}
    missing = {}
    for name, path in sorted(wanted.items()):
        if (name, path) not in present:
            missing.setdefault(path, []).append(name)
    for path, names in missing.items():
        existing = next((m for m in IMPORT.finditer(text) if m.group(2) == path), None)
        if existing:
            merged = [p.strip() for p in existing.group(1).split(',') if p.strip()] + names
            text = text[:existing.start()] + 'import { ' + ', '.join(merged) + " } from '" + path + "';" + text[existing.end():]
        else:
            imports = list(IMPORT.finditer(text))
            at = imports[-1].end() + 1 if imports else 0
            text = text[:at] + 'import { ' + ', '.join(names) + " } from '" + path + "';\n" + text[at:]
    # TODO lines whose names are all imported now disappear; the rest are reported.
    imported = {n for n, _ in {(n.split(' as ')[0].strip(), m.group(2)) for m in IMPORT.finditer(text) for n in m.group(1).split(',') if n.strip()}}
    without = []
    def todo(match):
        # FormBlock.Structure is the FormBlock import: only the first segment of a dotted name counts.
        names = [n for n in re.findall(r'(?<![.\w$])[A-Z][\w$]*', match.group(1).split('(')[0]) if n not in {'TODO'}]
        rest = [n for n in names if n not in imported]
        without.extend(rest)
        return '' if names and not rest else match.group(0)
    text = TODO.sub(todo, text)
    return text, {'imported_from_map': {n: p for n, p in sorted(wanted.items())}, 'without_import': sorted(set(without))}

"""Generated Java without fully qualified names: `LocalDateTime`, not `java.time.LocalDateTime`.

Only code is touched: string literals, text blocks and comments stay byte-for-byte.
Fully qualified JDK, Spring and Jackson names become simple names with a matching
import; unused single-type imports of these libraries are dropped. Names from
jakarta/javax and company classes get no import: the developer imports the variant
that exists in the project (for example jakarta or javax HttpServletRequest).
"""
from __future__ import annotations

import re

# Libraries every Spring Boot web project has: imports for these are safe to generate.
STANDARD = ('java.', 'org.springframework.', 'com.fasterxml.jackson.')
# Namespaces that differ between projects (Spring 5/6, servlet containers): never imported here.
PROJECT_SPECIFIC = ('jakarta.', 'javax.')
# Simple names the generator writes without a package; the import they need.
KNOWN = {name.rsplit('.', 1)[1]: name for name in (
    'java.util.List', 'java.util.Map', 'java.util.LinkedHashMap', 'java.util.ArrayList', 'java.util.Arrays',
    'java.util.Objects', 'java.util.Locale', 'java.util.function.Supplier', 'java.math.BigDecimal',
    'java.math.MathContext', 'java.time.LocalDate', 'java.time.LocalDateTime', 'java.time.format.DateTimeParseException',
    'java.sql.Types', 'java.sql.SQLException', 'java.sql.ResultSet', 'java.sql.CallableStatement', 'java.sql.Timestamp',
    'java.util.regex.Pattern', 'java.util.regex.Matcher', 'java.net.URI',
    'org.springframework.http.HttpStatus', 'org.springframework.http.HttpMethod', 'org.springframework.http.ResponseEntity',
    'org.springframework.web.server.ResponseStatusException', 'org.springframework.core.ParameterizedTypeReference',
    'org.springframework.web.client.RestTemplate', 'org.springframework.http.HttpEntity',
    'org.springframework.boot.web.client.RestTemplateBuilder', 'java.util.stream.Collectors',
    'org.springframework.web.client.RestClientException',
    'org.springframework.web.client.RestClientResponseException',
    'org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate',
    'org.springframework.jdbc.core.namedparam.MapSqlParameterSource', 'org.springframework.jdbc.core.CallableStatementCallback',
    'org.springframework.dao.DataAccessException', 'com.fasterxml.jackson.annotation.JsonFormat')}
# Annotation-only names: imported only when used as @Name (the word alone may be anything).
ANNOTATIONS = {name.rsplit('.', 1)[1]: name for name in (
    'org.springframework.stereotype.Service', 'org.springframework.transaction.annotation.Transactional',
    'org.springframework.beans.factory.annotation.Value', 'org.springframework.beans.factory.annotation.Autowired')}
# User import map (java-imports.json): authoritative for the names it lists (see java_imports).
IMPORT_MAP: dict = {}
JAVA_LANG = {
    'String', 'Object', 'Integer', 'Long', 'Boolean', 'Double', 'Float', 'Short', 'Byte', 'Character', 'Number', 'Math',
    'System', 'Thread', 'Class', 'Void', 'Iterable', 'Comparable', 'CharSequence', 'StringBuilder', 'Exception',
    'RuntimeException', 'Error', 'Throwable', 'IllegalArgumentException', 'IllegalStateException', 'NullPointerException',
    'NumberFormatException', 'UnsupportedOperationException', 'IndexOutOfBoundsException', 'ClassCastException',
    'ArithmeticException', 'AssertionError', 'Override', 'Deprecated', 'SuppressWarnings', 'FunctionalInterface',
    'SafeVarargs', 'Enum', 'Runnable', 'AutoCloseable', 'Cloneable', 'InterruptedException', 'SecurityException'}
# Names that a generated wildcard import provides.
WILDCARD_NAMES = {'org.springframework.web.bind.annotation': {
    'RestController', 'RequestMapping', 'GetMapping', 'PostMapping', 'PutMapping', 'DeleteMapping', 'PatchMapping',
    'RequestBody', 'RequestParam', 'PathVariable', 'RequestHeader', 'ResponseStatus', 'ResponseBody', 'CrossOrigin'}}


OMIT_PACKAGE = {'on': False}
KEEP_PACKAGE = {'CommonMigrateTools.java'}  # shared helper: its package is configured, not module-specific


def configure(mapping: dict, omit_package: bool = False) -> None:
    """The import map of one generation run; omit_package: module files get no package line (the IDE sets it)."""
    IMPORT_MAP.clear(); IMPORT_MAP.update(mapping or {})
    OMIT_PACKAGE['on'] = bool(omit_package)


def unimported_names(text: str) -> set:
    """Capitalised type names in code that no import, wildcard, java.lang or local declaration covers."""
    code = ' '.join(p for is_code, p in segments(text) if is_code)
    code = re.sub(r'^\s*(?:import|package)\s[^\n]*$', '', code, flags=re.M)
    identifiers = set(re.findall(r'(?<![\w$.])[A-Za-z_$][\w$]*', code))
    imports = list(IMPORT.finditer(text))
    covered = {m.group(2).rsplit('.', 1)[-1] for m in imports if not m.group(3)}
    for m in imports:
        if m.group(3):
            covered |= WILDCARD_NAMES.get(m.group(2), set())
    covered |= set(re.findall(r'\b(?:class|interface|record|enum)\s+(\w+)', code)) | JAVA_LANG
    return {n for n in identifiers if n[:1].isupper() and len(n) > 1 and n.upper() != n and n not in covered}


def report(output) -> dict:
    """From the final files: imports taken from the map, and names still without import."""
    files = sorted((output / 'backend').rglob('*.java'))
    declared, used, without = set(), {}, {}
    for path in files:
        declared |= set(re.findall(r'\b(?:class|interface|record|enum)\s+(\w+)', path.read_text(encoding='utf-8')))
    for path in files:
        text = path.read_text(encoding='utf-8')
        for m in IMPORT.finditer(text):
            name = m.group(2).rsplit('.', 1)[-1]
            if not m.group(1) and not m.group(3) and IMPORT_MAP.get(name) == m.group(2):
                used[name] = m.group(2)
        for name in unimported_names(text) - declared:
            without.setdefault(name, set()).add(path.name)
    return {'imported_from_map': dict(sorted(used.items())),
            'without_import': {n: sorted(f) for n, f in sorted(without.items())}}


FQN = re.compile(r'(?<![\w.])((?:[a-z_][a-z0-9_]*\.)+)([A-Z]\w*)')
IMPORT = re.compile(r'^import\s+(static\s+)?([\w.]+?)(\.\*)?\s*;\s*$', re.M)


def segments(text: str) -> list[tuple[bool, str]]:
    """(is_code, text) pieces: string/char literals, text blocks and comments are not code."""
    out, i, start, n = [], 0, 0, len(text)
    def flush(end, code=True):
        if end > start:
            out.append((code, text[start:end]))
    while i < n:
        if text.startswith('"""', i):
            end = text.find('"""', i + 3)
            while end > 0 and text[end - 1] == '\\':
                end = text.find('"""', end + 1)
            end = n if end < 0 else end + 3
        elif text[i] in '"\'':
            quote, end = text[i], i + 1
            while end < n and text[end] != quote and text[end] != '\n':
                end += 2 if text[end] == '\\' else 1
            end = min(end + 1, n)
        elif text.startswith('//', i):
            end = text.find('\n', i); end = n if end < 0 else end
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2); end = n if end < 0 else end + 2
        else:
            i += 1
            continue
        flush(i); start = i
        flush(end, False); start = i = end
    flush(n)
    return out


def tidy(text: str, origin: str = '', *, mapping=None, omit_package=None, preserve_explicit=False) -> str:
    mapping = IMPORT_MAP if mapping is None else mapping
    omit_package = OMIT_PACKAGE['on'] if omit_package is None else omit_package
    parts = segments(text)
    header_end = next((m.end() for m in [re.search(r'^package\s+[\w.]+\s*;\s*$', text, re.M)] if m), 0)
    imports = {m.group(2): m for m in IMPORT.finditer(text) if not m.group(1) and not m.group(3)}
    imported = {fqn.rsplit('.', 1)[1]: fqn for fqn in imports}
    declared = set(re.findall(r'\b(?:class|interface|record|enum)\s+(\w+)', ''.join(p for code, p in parts if code)))
    needed = {}
    def simplify(match):
        package, cls = match.group(1), match.group(2)
        full = package + cls
        if not full.startswith(STANDARD + PROJECT_SPECIFIC):
            return match.group(0)  # company or module packages stay as written
        owner = imported.get(cls) or needed.get(cls)
        if cls in declared or (owner and owner != full):
            return match.group(0)  # a different class already owns this simple name
        if full.startswith(STANDARD):
            needed[cls] = full
        return match.group(0) if preserve_explicit and full.startswith(PROJECT_SPECIFIC) else cls
    body_parts = []
    for code, piece in parts:
        if code:
            # Import and package lines are rebuilt below; only statements are simplified.
            piece = '\n'.join(line if line.lstrip().startswith(('import ', 'package ')) else FQN.sub(simplify, line)
                              for line in piece.split('\n'))
        body_parts.append((code, piece))
    result = ''.join(p for _, p in body_parts)
    code_only = ' '.join(p for code, p in body_parts if code and not p.lstrip().startswith('import '))
    code_only = re.sub(r'^\s*(?:import|package)\s[^\n]*$', '', code_only, flags=re.M)
    words = set(re.findall(r'\b[A-Z]\w*\b', code_only))
    annotations = set(re.findall(r'@([A-Z]\w*)', code_only))
    for name in words & set(KNOWN):
        if name not in declared and name not in imported and name not in needed:
            needed[name] = KNOWN[name]
    for name in annotations & set(ANNOTATIONS):
        if name not in imported and name not in needed:
            needed[name] = ANNOTATIONS[name]
    # java-imports.json decides where the names it lists come from.
    identifiers = set(re.findall(r'(?<![\w$.])[A-Za-z_$][\w$]*', code_only))
    package = re.search(r'^package\s+([\w.]+)\s*;', text, re.M)
    own_package = package.group(1) if package else ''
    for name in identifiers & set(mapping):
        fqn = mapping[name]
        if name not in declared and fqn.rsplit('.', 1)[0] != own_package and not (preserve_explicit and name in imported):
            needed[name] = fqn
    # Drop project-specific imports (the developer adds the right one) and unused standard ones.
    def keep(match):
        fqn, wildcard, static = match.group(2), match.group(3), match.group(1)
        mapped = mapping.get(fqn.rsplit('.', 1)[-1]) if not static and not wildcard else None
        if preserve_explicit and not static and not wildcard:
            return match.group(0)
        if mapped:
            # The map wins over the generator's own import of the same name.
            return match.group(0) if fqn == mapped and fqn.rsplit('.', 1)[1] in identifiers else ''
        if fqn.startswith(PROJECT_SPECIFIC):
            return ''
        if static or wildcard or not fqn.startswith(STANDARD):
            return match.group(0)
        simple = fqn.rsplit('.', 1)[1]
        return match.group(0) if simple in words or simple in annotations else ''
    result = IMPORT.sub(keep, result)
    present = {m.group(2) for m in IMPORT.finditer(result)}
    new = sorted(fqn for fqn in set(needed.values()) if fqn not in present)
    if new:
        block = ''.join(f'import {fqn};\n' for fqn in new)
        last = list(IMPORT.finditer(result))
        at = last[-1].end() + 1 if last else header_end + 1 if header_end else 0
        result = result[:at] + block + result[at:]
    # Keep the template's import-group separators: collapsing them violates
    # CustomImportOrder even when the template deliberately separates groups.
    result = re.sub(r'\n{3,}(?=import )', '\n\n', result)
    if omit_package and origin not in KEEP_PACKAGE:
        # The files go to project folders the generator does not know: IntelliJ offers the right package.
        result = re.sub(r'^package\s+[\w.]+\s*;[ \t]*\n(?:[ \t]*\n)?', '', result, count=1, flags=re.M)
    return result


LINE_LIMIT = 119  # PMD: at most 120 characters - the generated SQL never reaches it
STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"')


def _units(content: str) -> list[str]:
    """Characters of a literal's content, an escape sequence (\\n, \\", \\u00e9) being one unit."""
    units, i = [], 0
    while i < len(content):
        if content[i] == '\\':
            width = 6 if content[i + 1:i + 2] == 'u' else 2
            units.append(content[i:i + width]); i += width
        else:
            units.append(content[i]); i += 1
    return units


def _split(content: str, first: int, rest: int, tail: int = 0) -> list[str]:
    """Chunks of a literal's content: the first fits `first` columns, the others `rest`, the last one
    together with the `tail` that follows the literal on its line; breaks after a space."""
    units, chunks, current, width = _units(content) + ([None] if tail else []), [], [], first
    size = lambda part: sum(tail if u is None else len(u) for u in part)
    for unit in units:
        current.append(unit)
        if size(current) >= width:
            spaces = [k for k, u in enumerate(current[:-1]) if u == ' ']
            if not spaces and unit is None:
                break  # the tail alone overflows: nothing left to move
            cut = spaces[-1] + 1 if spaces else len(current) - 1
            if cut == 0:
                break
            chunks.append(''.join(current[:cut])); current = current[cut:]; width = rest
    chunks.append(''.join(u for u in current if u is not None))
    return [c for c in chunks if c] or ['']


def wrap_strings(text: str, limit: int = LINE_LIMIT) -> str:
    """Break over-long lines inside their string literals: "a b c" -> "a b " + "c" (same value).

    Runs after the Checkstyle layout (generate.write), so nothing joins the lines again; the
    continuation lines start CONTINUATION deeper than the statement, as the layout expects.
    """
    from .java_style import CONTINUATION
    out = []
    for line in text.split('\n'):
        pending = [line]
        while pending:
            line = pending.pop(0)
            stripped = line.lstrip()
            literals = [m for m in STRING.finditer(line) if m.end() - m.start() > 2]
            if len(line) <= limit or stripped.startswith(('//', '*', '/*')) or not literals:
                out.append(line)
                continue
            literal = max(literals, key=lambda m: m.end() - m.start())
            continued = stripped.startswith('+ ')
            indent = ' ' * (len(line) - len(stripped) + (0 if continued else CONTINUATION))
            head, tail = line[:literal.start()], line[literal.end():]
            chunks = _split(literal.group(0)[1:-1], max(limit - len(head) - 2, 20), max(limit - len(indent) - 4, 20), len(tail))
            if len(chunks) == 1:
                out.append(line)  # no space to break at: left as it is
                continue
            lines = [head + '"' + chunks[0] + '"'] + [indent + '+ "' + c + '"' for c in chunks[1:]]
            lines[-1] += tail
            out.extend(lines[:-1])
            pending.insert(0, lines[-1])
    return '\n'.join(out)


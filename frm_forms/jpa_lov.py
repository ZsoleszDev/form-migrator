"""LOV queries as repository methods (backend_trigger_mode=ai): the record group's SQL as a native @Query.

A RecordGroupQuery is arbitrary Oracle SQL (joins, functions, DECODE): it runs as written, a native query of a
repository of the module, like every other database access of the ServiceImpl. The method returns List<Object[]> (the
company pattern); the column labels for the screen (the LOV keeps its contract: rows by column name) come from the
query's own select list, as Oracle names them: the alias, else the column name, in upper case.
"""
from __future__ import annotations

import re

from .common import jstr
from .plsql import Unsupported, tokenize

JAVA_TYPES = {"text": "String", "number": "BigDecimal", "datetime": "LocalDateTime"}
IDENTIFIER = re.compile(r'[A-Za-z][A-Za-z0-9_$#]*')


def select_labels(sql: str, fallback: list[str] | None = None) -> list[str]:
    """The labels Oracle gives the top-level select list: `kod` -> KOD, `t.nev` -> NEV, `x AS y` / `x y` -> Y.
    An expression without an alias takes the LOV column mapping's name at its position (fallback: the record group's
    columns, when there is one per select item); without that, or for `*`, there is no label: Unsupported."""
    tokens = [t for t in tokenize(sql) if t.kind != 'eof']
    depth, start = 0, None
    for index, token in enumerate(tokens):
        if token.value == '(':
            depth += 1
        elif token.value == ')':
            depth -= 1
        elif depth == 0 and token.value == 'SELECT':
            start = index + 1
            break
    if start is None:
        raise Unsupported('A LOV lekérdezésében nincs SELECT.')
    if start < len(tokens) and tokens[start].value in {'DISTINCT', 'UNIQUE', 'ALL'}:
        start += 1
    items, current, depth = [], [], 0
    for token in tokens[start:]:
        if token.value == '(':
            depth += 1
        elif token.value == ')':
            depth -= 1
        if depth == 0 and token.value == 'FROM':
            break
        if depth == 0 and token.value == ',':
            items.append(current); current = []
            continue
        current.append(token)
    items.append(current)
    labels = []
    for item in items:
        if not item:
            raise Unsupported('A LOV select-listája üres elemet tartalmaz.')
        last, before = item[-1], item[-2] if len(item) > 1 else None
        if last.value == '*':
            raise Unsupported('A LOV lekérdezése *-ot választ: az oszlopneveket a select-listában kell megadni.')
        # a column (kod, t.kod), `expression AS alias`, or `expression alias` (after a name, a literal or a ')')
        named = last.kind == 'id' and (before is None or before.value in {'.', 'AS'} or before.kind in {'id', 'string', 'number'}
                                       or before.value == ')')
        if named:
            labels.append(last.value.upper())
        elif fallback and len(fallback) == len(items) and fallback[len(labels)]:
            labels.append(fallback[len(labels)].upper())
        else:
            raise Unsupported('A LOV select-listájában alias nélküli kifejezés áll ('
                              + ' '.join(t.value for t in item) + '): adj neki aliast.')
    if len(set(labels)) != len(labels):
        raise Unsupported('A LOV select-listájában ismétlődő oszlopnév van.')
    return labels


def repository_method(plan: dict, method: str) -> str:
    """The native @Query method of the LOV in its repository."""
    params = (['@Param("term") String term'] if plan['display_column'] else []) + ['@Param("limit") int limit']
    params += [f'@Param("{b["parameter"]}") {JAVA_TYPES[b["type"]]} {b["parameter"]}' for b in plan['binds']]
    users = ', '.join(plan['users']) or '—'
    return '\n'.join([
        f"    // {plan['name']} LOV ({users}; rekordcsoport: {plan['record_group']}): a rekordcsoport lekérdezése, "
        + ("a beírt szöveggel szűrve." if plan['display_column'] else "szűrés nélkül."),
        f"    @Query(value = {jstr(plan['sql'])}, nativeQuery = true)",
        f"    List<Object[]> {method}({', '.join(params)});"])


def service_method(plan: dict, comment_lines, log1x_open: str, user_type: str, method: str, repository: str | None) -> str:
    """The LOV endpoint: the repository's rows by the select list's labels (LovQuery: no JDBC). A blocked LOV
    (plan blockers, or no repository to hold its query) answers HTTP 501 and calls nothing."""
    info = [f"Forms LOV: {plan['name']} (rekordcsoport: {plan['record_group']}), mező: {', '.join(plan['users']) or '—'}; "
            "a rekordcsoport lekérdezése a repositoryban, a beírt szöveggel szűrve."]
    if plan['blockers'] or not repository:
        info.append('Ok, amiért nem elérhető: ' + '; '.join(plan['blockers']))
        body = '            throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, "Ez a LOV ebben a modulban még nem érhető el.");'
    else:
        # A text bind is never null: Oracle reads '' as NULL, and the parameter stays a VARCHAR for the native query.
        args = (['LovQuery.term(request.term())'] if plan['display_column'] else []) + ['LovQuery.limit(request.limit())']
        args += [(f'LovQuery.text(request.parameters(), {jstr(b["source"])}, "")' if b['type'] == 'text' else
                  f'LovQuery.{b["type"]}(request.parameters(), {jstr(b["source"])})') for b in plan['binds']]
        labels = ', '.join(jstr(label) for label in plan['labels'])
        body = ('            if (request == null) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Hiányzó kérés.");\n'
                f"            var rows = {repository}.{method_name(plan)}({', '.join(args)});\n"
                f"            return new LovResult(LovQuery.rows(rows, {labels}));")
    return (comment_lines('\n'.join(info), '    ') + '\n'
            f'''    @Transactional(readOnly = true)
    @Override
    public LovResult {method}({user_type} user, LovRequest request) throws Exception {{
        {log1x_open}
{body}
        }});
    }}''')


def prepare(plan: dict, tables: dict, blocks: list[dict]) -> dict | None:
    """The repository of the LOV and its labels; a LOV without them gets a blocker (and no repository method)."""
    if plan['blockers']:
        return None
    entry = host(plan, tables, blocks)
    if entry is None:
        plan['blockers'].append('A LOV lekérdezéséhez nincs repository: a modulnak nincs adatbázisblokkja (Domain); '
                                'a lekérdezést kézzel kell egy repositoryba tenni.')
        return None
    from .common import decode_line_escapes
    from .plsql_passthrough import normal_mode_sql
    try:
        plan['labels'] = select_labels(normal_mode_sql(decode_line_escapes(plan['query'])),
                                       [c['column'] for c in plan['columns']])
    except Unsupported as exc:
        plan['blockers'].append(str(exc))
        return None
    used = {JAVA_TYPES[b['type']] for b in plan['binds']} | {'List'}
    entry['extra'].append((repository_method(plan, method_name(plan)), used))
    return entry


def method_name(plan: dict) -> str:
    return 'lov' + plan['class']


def host(plan: dict, tables: dict, blocks: list[dict]) -> dict | None:
    """The repository the LOV query lives in: its FROM table's, else its block's, else the module's first."""
    match = re.search(r'\bFROM\s+([A-Za-z][\w$#]*(?:\.[A-Za-z][\w$#]*)?)', plan.get('query', ''), re.I)
    first = match[1].upper() if match else ''
    if first in tables:
        return tables[first]
    block = next((b for b in blocks if b['name'] == plan['block']), None)
    if block and block['table'].upper() in tables:
        return tables[block['table'].upper()]
    return next(iter(tables.values()), None)



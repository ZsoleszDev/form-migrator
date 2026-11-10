"""The column names of a LOV's record group query, as the screen contract names them (backend_trigger_mode=ai).

A RecordGroupQuery is arbitrary Oracle SQL (joins, functions, DECODE): the Java assistant runs it as written through
the service's EntityManager (ai_frames.lov_frame), and the rows go to the screen by column name - the labels Oracle
gives the query's own select list: the alias, else the column name, in upper case.
"""
from __future__ import annotations

from .plsql import Unsupported, tokenize


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

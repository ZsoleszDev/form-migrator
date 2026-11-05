"""Simple record triggers as Java without the local AI (backend_trigger_mode=ai, data access jpa).

The most common record triggers of a Forms block are small and regular: the key from a sequence (PRE-INSERT), the
description of a code (POST-QUERY, POST-CHANGE: SELECT ... INTO, NO_DATA_FOUND -> NULL), a check with a message
(WHEN-VALIDATE-ITEM: IF ... THEN MESSAGE; RAISE FORM_TRIGGER_FAILURE), the master-detail delete check the Forms
wizard writes (ON-CHECK-DELETE-MASTER: a cursor and its %FOUND). They need no model: their SQL is a generated
repository method (jpa_sql), the rest is translated with Oracle semantics (rules.Compiler, SqlValues: NULL in a
comparison is unknown, '' is NULL), NO_DATA_FOUND / TOO_MANY_ROWS are the repository method's NoResultException /
NonUniqueResultException.

A trigger outside this subset - a value of another block, :GLOBAL, a database routine, a program unit of the form,
a loop, an unknown call or exception - stays the Java assistant's task, unchanged.
"""
from __future__ import annotations

import re

from .common import decode_line_escapes, jstr
from .plsql import Unsupported, parse
from .rules import Compiler

TYPES = {'text': 'String', 'number': 'BigDecimal', 'datetime': 'LocalDateTime'}
EXCEPTIONS = {'NO_DATA_FOUND': 'NoResultException', 'TOO_MANY_ROWS': 'NonUniqueResultException'}
# The ON-CHECK-DELETE-MASTER of the Forms master-detail wizard (any names, CHAR(1) or VARCHAR2 dummy).
DELETE_CHECK = re.compile(
    r"DECLARE\s+(\w+)\s+(?:CHAR|VARCHAR2)\s*(?:\(\s*\d+\s*\))?\s*;\s*CURSOR\s+(\w+)\s+IS\s+(SELECT\s+1\s+FROM\s+[^;]*?)\s*;\s*"
    r"BEGIN\s+OPEN\s+\2\s*;\s*FETCH\s+\2\s+INTO\s+\1\s*;\s*IF\s*\(?\s*\2\s*%\s*FOUND\s*\)?\s*THEN\s*"
    r"MESSAGE\s*\(\s*'((?:[^']|'')*)'\s*\)\s*;\s*CLOSE\s+\2\s*;\s*RAISE\s+FORM_TRIGGER_FAILURE\s*;\s*END\s+IF\s*;\s*"
    r"CLOSE\s+\2\s*;\s*END\s*;?", re.I)


def response(packet: dict, generated: dict) -> tuple[dict | None, str]:
    """The proposal of a hook translated without the model, or (None, why not)."""
    hook = packet.get('hook') or {}
    if packet.get('kind') != 'hook' or hook.get('kind') != 'record':
        return None, 'nem rekordtrigger'
    sources = [s for s in packet.get('sources') or [] if isinstance(s, dict)]
    if len(sources) != 1 or sources[0].get('kind') != 'trigger':
        return None, 'a trigger a form saját programegységét hívja'
    body, reason = translate(decode_line_escapes(sources[0].get('source') or ''), hook, generated)
    if body is None:
        return None, reason
    return {'status': 'proposal', 'body': body, 'imports': [], 'private_methods': '', 'repositories': [], 'dtos': [],
            'summary': 'A(z) ' + packet['method'] + ' Java-változata a migrátortól (determinisztikus, AI-hívás nélkül).',
            'assumptions': [], 'missing_context': [], 'test_cases': []}, ''


def translate(text: str, hook: dict, generated: dict) -> tuple[str | None, str]:
    compiler = HookCompiler(hook, generated)
    try:
        lines = compiler.delete_check(text)
        if lines is None:
            nodes = parse(text, extended=True)
            lines = compiler.statements(nodes, '')
        if compiler.locals:
            lines.insert(0, compiler.holder(''))
    except Unsupported as exc:
        return None, str(exc)
    from .assistant_reply import decode_literal_escapes  # "Túl" -> "Túl": the source is UTF-8
    body = decode_literal_escapes('\n'.join(line for line in lines if line.strip()))
    return body.replace('java.math.BigDecimal', 'BigDecimal'), ''


class HookCompiler(Compiler):
    """rules.Compiler on the hook's record (row.field), with SELECT INTO through the generated repository methods."""

    def __init__(self, hook: dict, generated: dict):
        block = {'name': hook['block'], 'table': hook.get('table') or '',
                 'items': [{'name': f['item'].split('.', 1)[1], 'field': f['field'], 'type': f['type'], 'kind': 'text'}
                           for f in hook.get('fields', [])],
                 'db_items': [{'name': f['item'].split('.', 1)[1], 'column': f['column'], 'type': f['type']}
                              for f in hook.get('fields', []) if f.get('database')]}
        super().__init__(block, {}, ())
        self.calls = generated.get('calls') or {}
        self.depth = 0

    # expressions: SYSDATE as the application server's clock (an Oracle DATE has seconds)
    def expression(self, node: dict) -> tuple[str, str]:
        if node['op'] == 'symbol' and node['name'] == 'SYSDATE':
            return 'LocalDateTime.now().withNano(0)', 'datetime'
        return super().expression(node)

    def statements(self, nodes: list[dict], indent: str) -> list[str]:
        lines = []
        for node in nodes:
            op = node['op']
            if op in {'noop', 'pragma'}:
                continue
            if op == 'block':
                lines += self.guarded(node, indent) if node.get('handlers') else self.statements(node['body'], indent)
            elif op == 'declare':
                lines.append(self.declare(node, indent))
            elif op == 'local_assign':
                local = self.locals.get(node['variable'])
                if local is None:
                    raise Unsupported('Nem deklarált változó vagy csomagváltozó: ' + node['variable'])
                expr, typ = self.expression(node['value'])
                if typ not in {local['type'], 'null'}:
                    raise Unsupported('Implicit hozzárendelési konverzió: ' + typ + ' -> ' + local['type'])
                lines.append(indent + local['java'] + ' = ' + expr + ';')
            elif op == 'assign':
                field = self.ref(node['target'])
                expr, typ = self.expression(node['value'])
                if typ not in {field['type'], 'null'}:
                    raise Unsupported('Implicit hozzárendelési konverzió: ' + typ + ' -> ' + field['type'])
                lines.append(indent + 'row.' + field['field'] + ' = ' + expr + ';')
            elif op == 'if':
                for n, branch in enumerate(node['branches']):
                    expr, typ = self.expression(branch['condition'])
                    if typ not in {'boolean', 'null'}:
                        raise Unsupported('Az IF feltétel nem logikai típusú.')
                    lines.append(indent + ('if' if n == 0 else '} else if') + ' (SqlValues.truth(' + expr + ')) {')
                    lines += self.statements(branch['body'], indent + '    ')
                if node['else']:
                    lines.append(indent + '} else {')
                    lines += self.statements(node['else'], indent + '    ')
                lines.append(indent + '}')
            elif op == 'call' and node['name'] == 'MESSAGE' and len(node['args']) == 1:
                expr, typ = self.expression(node['args'][0])
                if typ not in {'text', 'null'}:
                    raise Unsupported('MESSAGE csak szöveget fogad ebben a részhalmazban.')
                lines.append(indent + 'context.message(' + expr + ');')
            elif op == 'abort':
                lines.append(indent + 'context.abort();')
            elif op == 'select':
                lines += self.select(node, indent)
            else:
                raise Unsupported('Nem támogatott utasítás az AI nélküli fordításban: ' + str(node.get('name', op)))
        return lines

    def guarded(self, node: dict, indent: str) -> list[str]:
        """BEGIN ... EXCEPTION WHEN NO_DATA_FOUND / TOO_MANY_ROWS THEN ... END: the repository method's exceptions."""
        self.depth += 1
        name = 'e' if self.depth == 1 else 'e' + str(self.depth)
        lines = [indent + 'try {'] + self.statements(node['body'], indent + '    ')
        for handler in node['handlers']:
            unknown = [n for n in handler['names'] if n.upper() not in EXCEPTIONS]
            if unknown:
                raise Unsupported('Kivételkezelő: ' + ', '.join(unknown))
            caught = ' | '.join(dict.fromkeys(EXCEPTIONS[n.upper()] for n in handler['names']))
            lines.append(indent + '} catch (' + caught + ' ' + name + ') {')
            lines += self.statements(handler['body'], indent + '    ')
        self.depth -= 1
        return lines + [indent + '}']

    # the SQL: the generated repository methods (jpa_sql)
    def method(self, text: str):
        from .jpa_sql import statement_key
        method = self.calls.get(statement_key(text))
        if method is None:
            raise Unsupported('SQL generált repository-metódus nélkül: ' + ' '.join(text.split()))
        return method

    def call(self, method) -> str:
        types = [p.strip().rsplit(' ', 1)[0] for p in re.search(r'\((.*)\)', method.signature).group(1).split(',') if p.strip()]
        args = []
        for source, typ in zip(method.arguments, types):
            if source.startswith(':'):
                field = self.ref(source[1:])
                actual, java = TYPES[field['type']], 'row.' + field['field']
            elif source in self.locals:
                actual, java = TYPES[self.locals[source]['type']], self.locals[source]['java']
            else:
                raise Unsupported('A lekérdezés értéke nem a rekordé: ' + source)
            if actual != typ:
                raise Unsupported('Eltérő típus a lekérdezésben: ' + source + ' (' + actual + ', az oszlop ' + typ + ')')
            args.append(java)
        return method.variable + '.' + method.name + '(' + ', '.join(args) + ')'

    def target(self, name: str) -> tuple[str, str]:
        """(Java target, Java type) of an INTO target: a field of the record or a local variable."""
        if '.' in name:
            field = self.ref(name)
            return 'row.' + field['field'], TYPES[field['type']]
        if name in self.locals:
            return self.locals[name]['java'], TYPES[self.locals[name]['type']]
        raise Unsupported('Nem támogatott INTO-cél: ' + name)

    def select(self, node: dict, indent: str) -> list[str]:
        method = self.method(node['text'])
        call = self.call(method)
        returns = method.signature.split(' ', 1)[0]
        if method.kind == 'entity':
            if len(node['into']) != len(method.columns):
                raise Unsupported('Az INTO-célok száma eltér a lekérdezett oszlopokétól.')
            entity = 'found' if self.depth == 0 else 'found' + str(self.depth + 1)
            lines = [indent + returns + ' ' + entity + ' = ' + call + ';']
            for target, (field, typ) in zip(node['into'], method.columns):
                java, actual = self.target(target)
                if actual != typ:
                    raise Unsupported('Eltérő típus: ' + target + ' (' + actual + ', az oszlop ' + typ + ')')
                lines.append(indent + java + ' = ' + entity + '.get' + field[:1].upper() + field[1:] + '();')
            return lines
        if method.kind not in {'value', 'count', 'aggregate', 'sequence'} or len(node['into']) != 1:
            raise Unsupported('Nem támogatott lekérdezés az AI nélküli fordításban: ' + method.kind)
        java, actual = self.target(node['into'][0])
        if actual != returns:
            raise Unsupported('Eltérő típus: ' + node['into'][0] + ' (' + actual + ', a lekérdezés ' + returns + ')')
        return [indent + java + ' = ' + call + ';']

    def delete_check(self, text: str) -> list[str] | None:
        """The ON-CHECK-DELETE-MASTER of the Forms wizard: if a detail row exists, the message and the refusal."""
        from .ai_triggers import code_text
        match = DELETE_CHECK.fullmatch(' '.join(code_text(text).split()).strip())
        if not match:
            return None
        method = self.method(match.group(3))
        if method.kind != 'exists':
            return None
        return ['if (' + self.call(method) + ') {', '    context.message(' + jstr(match.group(4).replace("''", "'")) + ');',
                '    context.abort();', '}']

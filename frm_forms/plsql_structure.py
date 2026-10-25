"""The statement structure of a PL/SQL block: lists, IF branches, nested blocks and handlers.

Only as much as the generator needs to edit statements in place: IF / ELSIF / ELSE, BEGIN and
DECLARE blocks with their exception handlers, labels. Loops and CASE statements are opaque
statements; a simple statement ends at its ';'. Offsets point into the scanned text, so callers
replace exact ranges and comments and strings stay untouched.

prune() drops what the Forms emulation leaves empty: NULL statements next to real ones, blocks of
NULL only (BEGIN NULL; END;) and IF NOT TRUE THEN ... END IF (FORM_SUCCESS is always TRUE here).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .plsql import Unsupported


@dataclass
class Node:
    kind: str  # simple | opaque | if | block
    start: int  # token index
    end: int  # token index of the closing ';'
    branches: list = field(default_factory=list)  # if: [(cond_start, cond_end, [nodes])]; else -> cond None
    body: list = field(default_factory=list)  # block statements
    handlers: list = field(default_factory=list)  # block: [[nodes] per WHEN]
    declare: tuple | None = None  # block: (first, last) token index of the declarations
    exception_at: int | None = None  # block: token index of EXCEPTION
    label: bool = False  # <<label>> in front of it: a GOTO target


class Parser:
    def __init__(self, sig, purpose='A PL/SQL'):
        self.sig, self.purpose = sig, purpose

    def word(self, i):
        return self.sig[i][1].upper() if i < len(self.sig) and self.sig[i][0] == 'ident' else (
            self.sig[i][1] if i < len(self.sig) else '')

    def fail(self, i, what):
        near = ' '.join(t[1] for t in self.sig[max(0, i - 2):i + 3])
        raise Unsupported(self.purpose + ': a kód szerkezete nem bontható (' + what + ': ' + near + ').')

    def statements(self, i, stop):
        nodes = []
        while i < len(self.sig) and self.word(i) not in stop:
            node, i = self.statement(i)
            nodes.append(node)
        if i >= len(self.sig):
            self.fail(i - 1, 'lezáratlan utasításlista')
        return nodes, i

    def statement(self, i):
        word = self.word(i)
        if word == '<' and self.word(i + 1) == '<':
            close = i + 2
            while close < len(self.sig) and not (self.word(close) == '>' and self.word(close + 1) == '>'):
                close += 1
            node, end = self.statement(close + 2)
            node.start, node.label = i, True
            return node, end
        if word == 'IF':
            return self.if_statement(i)
        if word in {'BEGIN', 'DECLARE'}:
            return self.block(i)
        return self.opaque(i)

    def opaque(self, i):
        """A statement up to its ';', loops and CASE statements included."""
        stack, depth, k = [], 0, i
        kind = 'simple'
        while k < len(self.sig):
            word = self.word(k)
            if word in {'BEGIN', 'IF', 'LOOP', 'CASE'}:
                stack.append(word)
                if word == 'LOOP' or (word == 'CASE' and k == i):
                    kind = 'opaque'
            elif word == 'END':
                if not stack:
                    self.fail(k, 'felesleges END')
                stack.pop()
                if self.word(k + 1) in {'IF', 'LOOP', 'CASE'}:
                    k += 1
            elif word == '(':
                depth += 1
            elif word == ')':
                depth -= 1
            elif word == ';' and depth == 0 and not stack:
                return Node(kind, i, k), k + 1
            k += 1
        self.fail(i, 'lezáratlan utasítás')

    def condition(self, i, until):
        """The expression from sig[i] to its THEN (a CASE expression inside it has THENs too)."""
        cases, k = 0, i
        while k < len(self.sig):
            word = self.word(k)
            if word == 'CASE':
                cases += 1
            elif word == 'END' and cases:
                cases -= 1
            elif word == until and not cases:
                return k
            k += 1
        self.fail(i, 'THEN nélküli feltétel')

    def if_statement(self, i):
        node = Node('if', i, i)
        keyword = i  # IF or ELSIF
        while True:
            then = self.condition(keyword + 1, 'THEN')
            nodes, k = self.statements(then + 1, {'ELSIF', 'ELSE', 'END'})
            node.branches.append((keyword + 1, then - 1, nodes))
            word = self.word(k)
            if word == 'ELSIF':
                keyword = k
                continue
            if word == 'ELSE':
                nodes, k = self.statements(k + 1, {'END'})
                node.branches.append((None, None, nodes))
            if self.word(k) != 'END' or self.word(k + 1) != 'IF' or self.word(k + 2) != ';':
                self.fail(k, 'END IF hiányzik')
            node.end = k + 2
            return node, k + 3

    def block(self, i):
        from .libraries import declarations
        node = Node('block', i, i)
        k = i
        if self.word(i) == 'DECLARE':
            try:
                k = declarations(self.sig, i + 1)
            except Unsupported as exc:
                self.fail(i, str(exc))
            node.declare = (i + 1, k - 1)
            if self.word(k) != 'BEGIN':
                self.fail(k, 'DECLARE után BEGIN hiányzik')
        node.body, k = self.statements(k + 1, {'END', 'EXCEPTION'})
        if self.word(k) == 'EXCEPTION':
            node.exception_at = k
            k += 1
            while self.word(k) == 'WHEN':
                then = self.condition(k + 1, 'THEN')
                nodes, k = self.statements(then + 1, {'WHEN', 'END'})
                node.handlers.append(nodes)
        if self.word(k) != 'END':
            self.fail(k, 'END hiányzik')
        k += 1
        if self.word(k) != ';':
            k += 1  # END label
        if self.word(k) != ';':
            self.fail(k, 'END utáni ; hiányzik')
        node.end = k
        return node, k + 1


def parse(body: str, purpose='A PL/SQL'):
    """(significant tokens, root block) of a text that is exactly one block."""
    from .plsql_passthrough import scan, significant
    sig = significant(scan(body))
    parser = Parser(sig, purpose)
    if parser.word(0) not in {'BEGIN', 'DECLARE'}:
        raise Unsupported(purpose + ': a kód nem blokk.')
    root, after = parser.block(0)
    if after != len(sig):
        parser.fail(after, 'a blokk után további kód áll')
    return sig, root


def apply(body: str, edits) -> str:
    """Replace (start offset, end offset, text, order) ranges; inserts at one offset keep their order."""
    out = body
    for start, end, text, _ in sorted(edits, key=lambda e: (e[0], e[1], e[3]), reverse=True):
        out = out[:start] + text + out[end:]
    return out


DEAD_CONDITIONS = {'NOT TRUE', 'FALSE', 'NOT (TRUE)', '(FALSE)'}


def prune(statements: str) -> str:
    """A statement list without the emulation's empty statements; unchanged when it cannot be parsed.

    Semantics stay: a NULL statement does nothing, a block of NULL statements without declarations,
    handlers or label does nothing, and IF NOT TRUE never runs its only branch. A list never becomes
    empty: one NULL stays where PL/SQL needs a statement.
    """
    body = 'BEGIN\n' + statements + '\nEND;'
    try:
        sig, root = parse(body)
    except Unsupported:
        return statements

    def text(node):
        return body[sig[node.start][2]:sig[node.end][3]]

    def empty(node):
        if node.label:
            return False
        if node.kind == 'simple':
            return re.fullmatch(r'NULL\s*;', text(node), re.I) is not None
        if node.kind == 'block':
            return node.declare is None and node.exception_at is None and all(empty(n) for n in node.body)
        if node.kind == 'if' and len(node.branches) == 1:
            condition = ' '.join(t[1].upper() for t in sig[node.branches[0][0]:node.branches[0][1] + 1])
            return condition.replace('( ', '(').replace(' )', ')') in DEAD_CONDITIONS
        return False

    edits = []

    def line_range(node):
        start, end = sig[node.start][2], sig[node.end][3]
        left = start
        while left and body[left - 1] in ' \t':
            left -= 1
        right = end
        while right < len(body) and body[right] in ' \t':
            right += 1
        if (left == 0 or body[left - 1] == '\n') and right < len(body) and body[right] == '\n':
            return left, right + 1  # the statement is alone on its lines: they go with it
        return left, end  # within a line: with the spaces in front of it

    def visit(nodes):
        keep = {id(n) for n in nodes if not empty(n)}
        if not keep and nodes:
            first = nodes[0]
            edits.append((sig[first.start][2], sig[first.end][3], 'NULL;', 0))
            for node in nodes[1:]:
                edits.append((*line_range(node), '', 0))
            return
        for node in nodes:
            if id(node) not in keep:
                edits.append((*line_range(node), '', 0))
                continue
            for _, _, branch in node.branches:
                visit(branch)
            if node.kind == 'block':
                visit(node.body)
                for handler in node.handlers:
                    visit(handler)

    visit(root.body)
    if not edits:
        return statements
    pruned = apply(body, edits)
    return pruned[len('BEGIN\n'):-len('\nEND;')]

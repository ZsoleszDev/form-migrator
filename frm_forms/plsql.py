"""Small, fail-closed PL/SQL parser. A whole trigger must parse successfully.

No SQL execution, dynamic evaluation, regex-based partial translation, or LLM code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .common import decode_line_escapes


class Unsupported(Exception):
    pass


def adapter_error(exc: Exception) -> Unsupported:
    """An adapter's refusal as it is; an unexpected error (a bug of the migrator) as a refusal that names its type and
    place, its traceback in the log: one trigger or program unit never stops the migration of the whole form."""
    if isinstance(exc, Unsupported):
        return exc
    import sys
    import traceback
    from .common import failure_place
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
    return Unsupported('Belső hiba a migrátorban (' + type(exc).__name__ + ': ' + ' '.join(str(exc).split())[:200]
                       + failure_place(exc) + '); kézi átültetés. Kérlek, jelezd a hibát a napló végével együtt.')


@dataclass
class Token:
    kind: str
    value: str
    pos: int


TOKEN = re.compile(r"(?P<space>\s+)|(?P<comment>--[^\r\n]*|/\*[\s\S]*?\*/)|(?P<string>'(?:''|[^'])*')|(?P<bind>:[A-Za-z][A-Za-z0-9_$#]*\.[A-Za-z][A-Za-z0-9_$#]*)|(?P<number>\d+(?:\.\d+)?)|(?P<id>[A-Za-z][A-Za-z0-9_$#]*)|(?P<op>:=|<=|>=|<>|!=|\|\||[;(),+*/=<>.-])")
# The extended grammar (parse(..., extended=True)) also knows %TYPE / %ROWTYPE and => named arguments.
TOKEN_EXTENDED = re.compile(TOKEN.pattern.replace("(?P<op>:=|", "(?P<op>=>|:=|").replace("[;(),+*/=<>.-]", "[;(),+*/=<>.%-]"))


def qualified(tokens: list[Token]) -> list[Token]:
    """PKG.PROC / OWNER.PKG.FUNC as one identifier (binds already carry their dot)."""
    folded, i = [], 0
    while i < len(tokens):
        token = tokens[i]
        if token.kind == "id":
            parts = [token.value]
            while i + 2 < len(tokens) and tokens[i + 1].value == "." and tokens[i + 2].kind == "id":
                parts.append(tokens[i + 2].value); i += 2
            token = Token("id", ".".join(parts), token.pos)
        folded.append(token); i += 1
    return folded


def tokenize(source: str, extended: bool = False) -> list[Token]:
    # Forms2XML leaves line breaks as literal "&#10;"; without decoding every
    # multi-line trigger, WHERE and ORDER BY would fail on the first "&".
    source = decode_line_escapes(source)
    if len(source) > 100_000:
        raise Unsupported("A trigger túl nagy a beépített parserhez.")
    result = []
    pos = 0
    pattern = TOKEN_EXTENDED if extended else TOKEN
    while pos < len(source):
        match = pattern.match(source, pos)
        if not match:
            raise Unsupported(f"Nem támogatott token a(z) {pos}. karakternél{where(source, pos)}: {source[pos:pos+25]!r}")
        kind = match.lastgroup
        value = match.group()
        if kind not in {"space", "comment"}:
            result.append(Token(kind, value.upper() if kind in {"id", "bind"} else value, pos))
        pos = match.end()
    result.append(Token("eof", "<EOF>", pos))
    return result


def where(source: str, pos: int) -> str:
    """' (N. sor: <the line>)' of a position in the decoded source, for a readable refusal."""
    if pos < 0 or pos > len(source):
        return ''
    line = source.count('\n', 0, pos) + 1
    start = source.rfind('\n', 0, pos) + 1
    end = source.find('\n', pos)
    text = ' '.join(source[start:end if end >= 0 else len(source)].split())
    return f" ({line}. sor: {text[:80] + ('…' if len(text) > 80 else '')})" if text else f" ({line}. sor)"


class Parser:
    def __init__(self, source: str, extended: bool = False):
        self.source = decode_line_escapes(source)
        self.extended = extended
        self.tokens = qualified(tokenize(source, extended))
        self.i = 0
        self.depth = 0

    def peek(self, value: str) -> bool:
        return self.tokens[self.i].value == value

    def take(self) -> Token:
        token = self.tokens[self.i]
        self.i += 1
        return token

    def accept(self, value: str) -> bool:
        if self.peek(value):
            self.i += 1
            return True
        return False

    def need(self, value: str) -> None:
        if not self.accept(value):
            token = self.tokens[self.i]
            found = "a szöveg vége" if token.value == "<EOF>" else "„" + token.value + "”"
            raise Unsupported(f"Nem értelmezhető PL/SQL: itt „{value}” kellene, de {found} áll{where(self.source, token.pos)}.")

    def refuse(self, message: str, token: Token | None = None) -> Unsupported:
        token = token or self.tokens[max(self.i - 1, 0)]
        return Unsupported(message + where(self.source, token.pos))

    def parse(self) -> list[dict]:
        body = self.statements({"<EOF>"})
        self.need("<EOF>")
        return body

    def statements(self, stop: set[str]) -> list[dict]:
        self.depth += 1
        if self.depth > 32:
            raise Unsupported("Túl mély PL/SQL beágyazás.")
        body = []
        while self.tokens[self.i].value not in stop:
            body.append(self.statement())
            if len(body) > 1000:
                raise Unsupported("Túl sok utasítás.")
        self.depth -= 1
        return body

    def statement(self) -> dict:
        if self.extended and self.accept("DECLARE"):
            declarations = self.declarations()
            self.need("BEGIN")
            block = self.block_rest()
            block["body"][:0] = declarations
            return block
        if self.accept("BEGIN"):
            return self.block_rest()
        return self.simple_statement()

    def block_rest(self) -> dict:
        if True:
            body = self.statements({"END", "EXCEPTION", "<EOF>"})
            handlers = []
            if self.accept("EXCEPTION"):
                # Kept as data: only catalogued framework handlers may be dropped
                # later (rules.strip_framework); flatten() rejects every other one.
                while self.accept("WHEN"):
                    names = [self.take().value]
                    while self.accept("OR"):
                        names.append(self.take().value)
                    self.need("THEN")
                    handlers.append({"names": names, "body": self.statements({"WHEN", "END", "<EOF>"})})
                if not handlers:
                    raise Unsupported("Üres EXCEPTION szakasz.")
            self.need("END")
            if self.extended and self.tokens[self.i].kind == "id" and self.tokens[self.i + 1].value == ";":
                self.take()  # END label;
            self.need(";")
            return {"op": "block", "body": body, "handlers": handlers}

    def declarations(self) -> list[dict]:
        """DECLARE section (extended grammar): variables and constants, user exceptions, PRAGMAs."""
        result = []
        while not self.peek("BEGIN") and not self.peek("<EOF>"):
            token = self.take()
            if token.value == "PRAGMA":
                start = token.pos
                while not self.peek(";") and not self.peek("<EOF>"):
                    self.take()
                self.need(";")
                result.append({"op": "pragma", "text": self.source[start:self.tokens[self.i - 1].pos + 1]})
                continue
            if token.kind != "id" or token.value in {"CURSOR", "PROCEDURE", "FUNCTION", "TYPE", "SUBTYPE"} or "." in token.value:
                raise self.refuse("Nem támogatott deklaráció: " + token.value, token)
            if self.accept("EXCEPTION"):
                self.need(";")
                result.append({"op": "declare", "name": token.value, "type": "EXCEPTION", "constant": False, "value": None})
                continue
            constant = self.accept("CONSTANT")
            kind = self.take()
            if kind.kind != "id":
                raise self.refuse("Nem értelmezhető típus a deklarációban: " + token.value, kind)
            type_name = kind.value
            if self.accept("%"):
                attribute = self.take().value
                if attribute not in {"TYPE", "ROWTYPE"}:
                    raise self.refuse("Nem támogatott típusattribútum: %" + attribute, kind)
                type_name += "%" + attribute
            if self.accept("("):
                while not self.peek(")") and not self.peek("<EOF>"):
                    self.take()
                self.need(")")
            if self.accept("NOT"):
                self.need("NULL")
            value = None
            if self.accept(":=") or self.accept("DEFAULT"):
                value = self.expression()
            self.need(";")
            result.append({"op": "declare", "name": token.value, "type": type_name, "constant": constant, "value": value})
        return result

    def simple_statement(self) -> dict:
        if self.accept("NULL"):
            self.need(";")
            return {"op": "noop"}
        if self.accept("IF"):
            branches = []
            while True:
                condition = self.expression()
                self.need("THEN")
                branches.append({"condition": condition, "body": self.statements({"ELSIF", "ELSE", "END", "<EOF>"})})
                if not self.accept("ELSIF"):
                    break
            otherwise = self.statements({"END", "<EOF>"}) if self.accept("ELSE") else []
            self.need("END")
            self.need("IF")
            self.need(";")
            return {"op": "if", "branches": branches, "else": otherwise}
        token = self.take()
        if token.kind == "bind":
            self.need(":=")
            value = self.expression()
            self.need(";")
            return {"op": "assign", "target": token.value[1:], "value": value}
        if token.value == "RAISE":
            if self.extended and not self.peek("FORM_TRIGGER_FAILURE"):
                name = None if self.peek(";") else self.take().value
                self.need(";")
                return {"op": "raise", "name": name}
            self.need("FORM_TRIGGER_FAILURE")
            self.need(";")
            return {"op": "abort"}
        if self.extended and token.kind == "id" and self.peek(":="):
            self.take()
            value = self.expression()
            self.need(";")
            return {"op": "local_assign", "variable": token.value, "value": value}
        if self.extended and token.value == "SELECT":
            return self.select(token)
        if token.kind != "id":
            raise self.refuse(f"Nem támogatott utasítás: {token.value}", token)
        args = []
        if self.accept("("):
            if not self.peek(")"):
                args.append(self.expression())
                while self.accept(","):
                    args.append(self.expression())
            self.need(")")
        self.need(";")
        return {"op": "call", "name": token.value, "args": args}

    def select(self, token: Token) -> dict:
        """SELECT ... INTO targets FROM ... ; (extended grammar): the SQL text and the INTO targets."""
        start, depth, into, columns_end, rest_start = token.pos, 0, [], None, None
        while not self.peek("<EOF>"):
            current = self.tokens[self.i]
            if current.value == ";" and depth == 0:
                break
            depth += {"(": 1, ")": -1}.get(current.value, 0)
            if current.value == "INTO" and depth == 0 and columns_end is None:
                columns_end = current.pos
                self.take()
                while True:
                    target = self.take()
                    if target.kind not in {"bind", "id"}:
                        raise self.refuse("Nem támogatott INTO-cél: " + target.value, target)
                    into.append(target.value[1:] if target.kind == "bind" else target.value)
                    if not self.accept(","):
                        break
                rest_start = self.tokens[self.i].pos
                continue
            self.take()
        end = self.tokens[self.i].pos
        self.need(";")
        if columns_end is None:
            raise self.refuse("SELECT INTO nélkül a PL/SQL-ben.", token)
        return {"op": "select", "columns": self.source[start + len("SELECT"):columns_end].strip(),
                "into": into, "rest": self.source[rest_start:end].strip(), "text": self.source[start:end].strip()}

    def expression(self, minimum: int = 0) -> dict:
        token = self.take()
        if self.extended and token.value == "CASE":
            left = self.case()
        elif token.value in {"NOT", "+", "-"}:
            left = {"op": "unary", "operator": token.value, "value": self.expression(25 if token.value == "NOT" else 60)}
        elif token.value == "(":
            left = self.expression()
            self.need(")")
        elif token.kind == "bind":
            left = {"op": "ref", "name": token.value[1:]}
        elif token.kind == "string":
            left = {"op": "literal", "type": "text", "value": token.value[1:-1].replace("''", "'") or None}
        elif token.kind == "number":
            left = {"op": "literal", "type": "number", "value": token.value}
        elif token.value in {"NULL", "TRUE", "FALSE"}:
            left = {"op": "literal", "type": "null" if token.value == "NULL" else "boolean", "value": {"NULL": None, "TRUE": True, "FALSE": False}[token.value]}
        elif token.kind == "id":
            if self.accept("("):
                args = []
                if not self.peek(")"):
                    args.append(self.expression())
                    while self.accept(","):
                        args.append(self.expression())
                self.need(")")
                left = {"op": "function", "name": token.value, "args": args}
            else:
                left = {"op": "symbol", "name": token.value}
        else:
            raise self.refuse(f"Nem támogatott kifejezés: {token.value}", token)
        precedence = {"OR": 10, "AND": 20, "=": 30, "<>": 30, "!=": 30, "<": 30, ">": 30, "<=": 30, ">=": 30, "IS": 30, "||": 40, "+": 40, "-": 40, "*": 50, "/": 50}
        while True:
            if self.extended and minimum <= 30 and self.comparison_keyword():
                left = self.comparison(left)
                continue
            if precedence.get(self.tokens[self.i].value, -1) < minimum:
                break
            operator = self.take().value
            if operator == "IS":
                negated = self.accept("NOT")
                self.need("NULL")
                left = {"op": "is_null", "value": left, "negated": negated}
                continue
            right = self.expression(precedence[operator] + 1)
            left = {"op": "binary", "operator": operator, "left": left, "right": right}
        return left

    def comparison_keyword(self) -> bool:
        value, following = self.tokens[self.i].value, self.tokens[self.i + 1].value if self.i + 1 < len(self.tokens) else ""
        return value in {"IN", "LIKE", "BETWEEN"} or (value == "NOT" and following in {"IN", "LIKE", "BETWEEN"})

    def comparison(self, left: dict) -> dict:
        """x [NOT] IN (a, b) | x [NOT] LIKE p | x [NOT] BETWEEN a AND b (extended grammar)."""
        negated = self.accept("NOT")
        operator = self.take().value
        if operator == "IN":
            self.need("(")
            if self.peek("SELECT"):
                raise self.refuse("IN (SELECT ...) allekérdezés a PL/SQL-feltételben.")
            items = [self.expression()]
            while self.accept(","):
                items.append(self.expression())
            self.need(")")
            return {"op": "in", "value": left, "items": items, "negated": negated}
        if operator == "LIKE":
            return {"op": "like", "value": left, "pattern": self.expression(31), "negated": negated}
        low = self.expression(31)
        self.need("AND")
        return {"op": "between", "value": left, "low": low, "high": self.expression(31), "negated": negated}

    def case(self) -> dict:
        """CASE [operand] WHEN .. THEN .. [ELSE ..] END (extended grammar)."""
        operand = None if self.peek("WHEN") else self.expression()
        whens = []
        while self.accept("WHEN"):
            condition = self.expression()
            self.need("THEN")
            whens.append({"when": condition, "then": self.expression()})
        if not whens:
            raise self.refuse("CASE WHEN ág nélkül.")
        otherwise = self.expression() if self.accept("ELSE") else None
        self.need("END")
        return {"op": "case", "operand": operand, "whens": whens, "else": otherwise}


def parse(source: str, extended: bool = False) -> list[dict]:
    """The statements of a trigger. extended: also DECLARE sections, local variables (local_assign), SELECT INTO,
    RAISE <exception>, CASE, [NOT] IN / LIKE / BETWEEN and %TYPE - for the consumers that handle these nodes
    (query_java, the Java compiler); every other consumer keeps the strict grammar."""
    try:
        return Parser(source, extended).parse()
    except (RecursionError, IndexError) as exc:
        raise Unsupported("Hibás vagy túl mély PL/SQL.") from exc


def flatten(body: list[dict]):
    for node in body:
        if node["op"] == "block":
            if node.get("handlers"):
                names = ", ".join(n for h in node["handlers"] for n in h["names"])
                raise Unsupported(f"Saját kivételkezelő (EXCEPTION WHEN {names}): a hibaágat külön kell átültetni.")
            yield from flatten(node["body"])
        else:
            yield node

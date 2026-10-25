"""Checkstyle layout for generated Java (Google-derived rules, 4-space indentation).

Every generated backend .java file passes through here after java_tidy (generate.write),
so CL, DPS and WBS, both backend formats and custom templates get the same layout.
Only the layout changes, never the meaning:

- blocks (NeedBraces, LeftCurly, RightCurly): every if/else/for/while/do body gets braces;
  '{' ends its line; '}' stands alone or continues as '} else', '} catch', '} finally',
  '} while'; empty class, method and statement blocks take two lines;
- one statement per line (OneStatementPerLine); 'case'/'default' labels on their own lines;
- annotations of types, methods and constructors on their own lines (AnnotationLocation);
- an empty line before every class member, except between consecutive fields
  (EmptyLineSeparator, allowNoEmptyLineBetweenFields);
- imports (CustomImportOrder, AvoidStarImport, UnusedImports): grouped and sorted by the
  java_import_order rules (default STATIC###STANDARD_JAVA_PACKAGE###THIRD_PARTY_PACKAGE),
  one empty line between the groups; unused, duplicate and java.lang imports are dropped;
  the generator's wildcard imports become single-type imports of the names used;
- indentation: 4 spaces per block level, 'case' +4; a wrapped line keeps its shape and
  starts at least 8 columns deeper than its statement;
- string and char literals: Unicode escapes become the characters themselves (the files are
  UTF-8); control characters stay escaped (AvoidEscapedUnicodeCharacters);
- spaces around '=', compound assignments, '==', '!=', '&&', '||' and '->' (also in
  annotations: name = "offset"), after ',' and between a keyword and '(' (WhitespaceAround).

Safety net: the code tokens of the result are compared with the input (braces aside); a file
the formatter cannot read with certainty is written unchanged and listed in FAILED.
"""
from __future__ import annotations

import os
import re

from .common import MigrationError

INDENT = 4
CONTINUATION = 8
DEFAULT_IMPORT_ORDER = 'STATIC###STANDARD_JAVA_PACKAGE###THIRD_PARTY_PACKAGE'
IMPORT_RULE = re.compile(r'STATIC|STANDARD_JAVA_PACKAGE|THIRD_PARTY_PACKAGE|SAME_PACKAGE\(([1-9][0-9]?)\)')
STANDARD_JAVA = re.compile(r'(?:java|javax)\.')
# Names the generator's wildcard imports provide: the wildcard becomes single-type imports.
WILDCARDS = {'org.springframework.web.bind.annotation': frozenset((
    'RestController', 'RequestMapping', 'GetMapping', 'PostMapping', 'PutMapping', 'DeleteMapping',
    'PatchMapping', 'RequestBody', 'RequestParam', 'PathVariable', 'RequestHeader', 'ResponseStatus',
    'ResponseBody', 'CrossOrigin', 'ExceptionHandler', 'ControllerAdvice', 'RestControllerAdvice',
    'ModelAttribute', 'CookieValue', 'RequestPart', 'RequestAttribute', 'SessionAttribute',
    'SessionAttributes', 'InitBinder', 'MatrixVariable', 'RequestMethod', 'Mapping', 'ValueConstants'))}
JAVADOC_REFERENCE = re.compile(r'(?:\{@(?:link|linkplain|value)\s+|@(?:see|throws|exception)\s+)([A-Za-z_$][\w$]*)')

SETTINGS = {'enabled': True, 'import_order': DEFAULT_IMPORT_ORDER}
# Member types of the classes formatted in this run ('pkg.Outer' and 'Outer' -> names), so
# that 'import pkg.Outer.*;' in a later file becomes single-type imports of the used names.
MEMBER_TYPES: dict = {}
FAILED: list = []  # files written unchanged in this run: {'file': ..., 'reason': ...}


class Unsupported(Exception):
    """Input the formatter does not rewrite; the file is written unchanged."""


def validate_import_order(value) -> str:
    """Checkstyle CustomImportOrder rules, e.g. STATIC###STANDARD_JAVA_PACKAGE###THIRD_PARTY_PACKAGE."""
    if not isinstance(value, str) or not value.strip():
        raise MigrationError('java_import_order: Checkstyle customImportOrderRules szükséges, '
                             'pl. STATIC###STANDARD_JAVA_PACKAGE###THIRD_PARTY_PACKAGE.')
    rules = [rule.strip() for rule in value.split('###')]
    names = []
    for rule in rules:
        if not IMPORT_RULE.fullmatch(rule):
            raise MigrationError(f'java_import_order: ismeretlen importcsoport: {rule!r} (STATIC, '
                                 'STANDARD_JAVA_PACKAGE, THIRD_PARTY_PACKAGE, SAME_PACKAGE(n)).')
        names.append(rule.split('(')[0])
    if len(set(names)) != len(names):
        raise MigrationError('java_import_order: minden importcsoport csak egyszer szerepelhet.')
    return '###'.join(rules)


def configure(config: dict | None = None) -> None:
    """Settings of one generation run; also forgets the classes learned in the previous run."""
    config = config or {}
    SETTINGS['enabled'] = config.get('java_checkstyle_format', True) is not False
    SETTINGS['import_order'] = config.get('java_import_order') or DEFAULT_IMPORT_ORDER
    MEMBER_TYPES.clear()
    FAILED.clear()


def layout(text: str, origin: str = '', *, settings=None, member_types=None, failures=None) -> str:
    """Checkstyle layout of one Java file; input the formatter cannot read is returned unchanged."""
    settings = SETTINGS if settings is None else settings
    member_types = MEMBER_TYPES if member_types is None else member_types
    failures = FAILED if failures is None else failures
    if not settings['enabled']:
        return text
    try:
        result, learned = _format(text, settings, member_types)
        if _signature(result) != _signature(text):
            raise Unsupported('a formázott kód tokenjei eltérnek az eredetitől')
        member_types.update(learned)
        return result
    except Exception as exc:  # the layout must never stop a migration
        if os.environ.get('FRM_JAVA_STYLE_STRICT') == '1':
            raise
        failures.append({'file': origin, 'reason': f'{type(exc).__name__}: {exc}'})
        return text


# ---------------------------------------------------------------------------- tokens

_TOKEN = re.compile(r'''
    (?P<nl>\r\n|\r|\n)
  | (?P<ws>[ \t\f]+)
  | (?P<lc>//[^\r\n]*)
  | (?P<bc>/\*.*?\*/)
  | (?P<tb>"""[ \t\f]*(?:\r\n|\r|\n)(?:[^"\\]|\\.|"(?!""))*""")
  | (?P<str>"(?:[^"\\\r\n]|\\.)*")
  | (?P<chr>'(?:[^'\\\r\n]|\\.)+')
  | (?P<num>(?:0[xX][0-9a-fA-F_]*(?:\.[0-9a-fA-F_]*)?(?:[pP][+-]?[0-9_]+)?|0[bB][01_]+
            |(?:[0-9][0-9_]*(?:\.[0-9_]*)?|\.[0-9][0-9_]*)(?:[eE][+-]?[0-9_]+)?)[lLfFdD]?)
  | (?P<id>(?:[^\W\d]|\$)[\w$]*)
  | (?P<op>>>>=|<<=|>>=|\.\.\.|->|::|\+\+|--|&&|\|\||==|!=|<=|>=|\+=|-=|\*=|/=|%=|&=|\|=|\^=|<<
          |[-+*/%=<>!~?:;,.(){}\[\]@&|^])
''', re.S | re.X)


class Tok:
    """A token: nl = line breaks before it, ws = spaces before it on its line, lind = indent of its line."""
    __slots__ = ('kind', 'text', 'nl', 'ws', 'col', 'lind', 'lead', 'trail', 'eol')

    def __init__(self, kind, text, nl, ws, col, lind):
        self.kind, self.text, self.nl, self.ws, self.col, self.lind = kind, text, nl, ws, col, lind
        self.lead, self.trail, self.eol = [], [], True


def tokenize(text: str) -> list:
    tokens, pos, end = [], 0, len(text)
    newlines, space, line_start, line_indent, fresh = 0, '', 0, 0, True
    while pos < end:
        match = _TOKEN.match(text, pos)
        if not match:
            raise Unsupported(f'ismeretlen karakter: {text[pos]!r}')
        kind, value = match.lastgroup, match.group()
        if kind == 'nl':
            newlines, space, line_start, fresh = newlines + 1, '', match.end(), True
        elif kind == 'ws':
            space = '' if fresh else space + value
        else:
            col = len(text[line_start:pos].expandtabs(4))
            if fresh:
                line_indent = col
            tokens.append(Tok(kind, value, newlines, '' if fresh else space, col, line_indent))
            newlines, space, fresh = 0, '', False
            last = max(value.rfind('\n'), value.rfind('\r'))
            if last >= 0:
                line_start = pos + last + 1
        pos = match.end()
    return tokens


def attach(tokens: list) -> tuple[list, list]:
    """Code tokens with their comments: trail = on the token's line, lead = before the next token."""
    code, pending, tail = [], [], []

    def flush(following):
        k = 0
        if code:
            while k < len(pending) and pending[k].nl == 0:
                comment = pending[k]
                after = pending[k + 1] if k + 1 < len(pending) else following
                comment.eol = comment.kind == 'lc' or after is None or after.nl > 0
                code[-1].trail.append(comment)
                k += 1
        (following.lead if following is not None else tail).extend(pending[k:])

    for token in tokens:
        if token.kind in ('lc', 'bc'):
            pending.append(token)
        else:
            flush(token)
            pending = []
            code.append(token)
    flush(None)
    return code, tail


def comment_text(comment: Tok, col: int) -> str:
    """The comment moved to column col: its further lines keep their position relative to '/*'."""
    lines = comment.text.split('\n')
    result = [lines[0].rstrip().replace('\t', '    ')]
    for line in lines[1:]:
        line = line.expandtabs(4).rstrip()
        content = line.lstrip(' ')
        result.append(' ' * max(0, col + len(line) - len(content) - comment.col) + content if content else '')
    return '\n'.join(result)


# --------------------------------------------------------------------------- literals

_UNICODE_ESCAPE = re.compile(r'\\+u+[0-9a-fA-F]{4}')
_READ_ESCAPES = {'b': '\b', 't': '\t', 'n': '\n', 'f': '\f', 'r': '\r', 's': ' ', '"': '"', "'": "'", '\\': '\\'}
_WRITE_ESCAPES = {'\\': '\\\\', '\b': '\\b', '\t': '\\t', '\n': '\\n', '\f': '\\f', '\r': '\\r'}


def _literal_value(body: str):
    """The value of a string/char literal body as javac reads it; None if a lone surrogate remains."""
    def unicode(match):
        run = match.group()
        slashes = len(run) - len(run.lstrip('\\'))
        return run if slashes % 2 == 0 else '\\' * (slashes - 1) + chr(int(run[-4:], 16))
    text = _UNICODE_ESCAPE.sub(unicode, body)
    try:
        text = text.encode('utf-16-le', 'surrogatepass').decode('utf-16-le')  # surrogate pairs
    except UnicodeDecodeError:
        return None
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch != '\\':
            out.append(ch)
            i += 1
            continue
        following = text[i + 1:i + 2]
        if following and following in _READ_ESCAPES:
            out.append(_READ_ESCAPES[following])
            i += 2
        elif following and following in '01234567':
            j, limit = i + 1, 3 if following in '0123' else 2
            while j < len(text) and j - i - 1 < limit and text[j] in '01234567':
                j += 1
            out.append(chr(int(text[i + 1:j], 8)))
            i = j
        else:
            raise Unsupported('ismeretlen escape-szekvencia egy literálban')
    return ''.join(out)


def _literal(text: str) -> str:
    """The literal with Unicode escapes written as characters; control characters stay escaped."""
    body = text[1:-1]
    if '\\u' not in body and not any(ord(ch) < 32 or ord(ch) == 127 for ch in body):
        return text
    value = _literal_value(body)
    if value is None:
        return text
    quote, out = text[0], []
    for ch in value:
        code = ord(ch)
        if ch == quote:
            out.append('\\' + ch)
        elif ch in _WRITE_ESCAPES:
            out.append(_WRITE_ESCAPES[ch])
        elif code < 32 or 127 <= code <= 159:
            out.append('\\%03o' % code)
        else:
            out.append(ch)
    return quote + ''.join(out) + quote


# ---------------------------------------------------------------------------- layout

MODIFIERS = frozenset('public protected private static final abstract native synchronized transient '
                      'volatile strictfp default'.split())
TYPE_WORDS = frozenset(('class', 'interface', 'enum'))
KEYWORD_PAREN = frozenset(('if', 'for', 'while', 'switch', 'catch', 'synchronized', 'try'))
SPACED = frozenset('= += -= *= /= %= &= |= ^= <<= >>= >>>= == != && || ->'.split())


class Wrap:
    """Where the wrapped lines of a statement start: their original shape, at least CONTINUATION deeper."""
    __slots__ = ('orig', 'new', 'shift')

    def __init__(self, orig, new):
        self.orig, self.new, self.shift = orig, new, None

    def indent(self, token):
        offset = token.lind - self.orig
        if self.shift is None:  # the first wrapped line decides; the others keep their relative place
            self.shift = max(0, CONTINUATION - offset)
        return self.new + max(CONTINUATION, offset + self.shift)


class Emitter:
    """Writes the code tokens again, line by line, from the block structure."""

    def __init__(self, code):
        self.t, self.n, self.i = code, len(code), 0
        self.lines, self.cur, self.indent = [], None, 0
        self.eol, self.after, self.blank, self.prev = [], [], False, None
        self.nextline = None
        self.top, self.members = [], {}

    # tokens
    def at(self, k=0):
        j = self.i + k
        return self.t[j].text if j < self.n else None

    def tok(self, k=0):
        j = self.i + k
        return self.t[j] if j < self.n else None

    def expect(self, text):
        if self.at() != text:
            raise Unsupported(f'{text!r} helyett {self.at()!r} a(z) {self.i}. tokennél')

    # output lines
    def col(self):
        line = ''.join(self.cur or ())
        return len(line) - line.rfind('\n') - 1

    def end_line(self):
        if self.cur is None:
            return
        line = ''.join(self.cur).rstrip()
        for comment in self.eol:
            line += ' ' + comment_text(comment, len(line) - line.rfind('\n'))
        self.eol = []
        self.lines.append(line)
        self.cur = self.prev = None

    def start_line(self, indent, comment_indent=None):
        self.end_line()
        if self.blank and self.lines and self.lines[-1]:
            self.lines.append('')
        self.blank = False
        if self.after:
            at = indent if comment_indent is None else comment_indent
            self.lines.extend(' ' * at + comment_text(c, at) for c in self.after)
            self.after = []
        self.cur, self.indent, self.prev = [' ' * indent], indent, None

    def fresh(self):
        return self.cur is not None and len(self.cur) == 1

    def put(self, text, space=' '):
        if self.cur is None:
            self.start_line(self.indent)
        self.cur.append(text if self.fresh() else space + text)

    def put_comment(self, comment, space=' '):
        col = self.col() + (0 if self.fresh() else len(space))
        self.put(comment_text(comment, col), space)

    def emit(self, line=None, space=None, comment_indent=None):
        """The current token on a new line (line = its indent, own-line comments above it) or on the open line."""
        token = self.t[self.i]
        if line is not None:
            self.lead_lines(token, line, line if comment_indent is None else comment_indent)
        else:
            for comment in token.lead:
                if comment.nl > 0 or comment.kind == 'lc':
                    self.after.append(comment)  # it cannot stay above: it goes below the open line
                else:
                    self.put_comment(comment, comment.ws or ' ')
            self.put(token.text, self.spacing(token) if space is None else space)
        self.prev = token.text
        for comment in token.trail:
            if comment.eol:
                self.eol.append(comment)
            else:
                self.put_comment(comment, comment.ws or ' ')
        self.i += 1
        return token

    def lead_lines(self, token, indent, comment_indent):
        lead = token.lead
        for k, comment in enumerate(lead):
            if k == 0 or comment.nl > 0:
                if k and comment.nl > 1:
                    self.blank = True
                self.start_line(comment_indent)
                self.put_comment(comment, '')
            else:
                self.put_comment(comment, comment.ws or ' ')
        if not lead or token.nl > 0 or lead[-1].kind == 'lc' or indent != comment_indent:
            if lead and token.nl > 1:
                self.blank = True
            self.start_line(indent, comment_indent)
            self.put(token.text, '')
        else:
            self.put(token.text, token.ws or ' ')

    def spacing(self, token):
        text, prev = token.text, self.prev
        space = ' ' if '\t' in token.ws else token.ws
        if prev is None or self.fresh() or text in (',', ';', ')', ']') or prev in ('(', '['):
            return ''
        if prev in (',', ';') or text in SPACED or prev in SPACED:
            return space or ' '
        if text == '(' and prev in KEYWORD_PAREN:
            return ' '
        return space

    def place(self, line):
        if line is None:
            self.emit()
        else:
            self.emit(line=line)

    # structure
    def run(self):
        previous = None
        while self.i < self.n:
            previous = self.member(0, previous, None, top=True)
        self.end_line()
        self.lines.extend(comment_text(c, 0) for c in self.after)
        self.after = []

    def blank_before(self):
        token = self.tok()
        return (token.lead[0] if token.lead else token).nl > 1

    def skip_prefix(self, j):
        while j < self.n:
            text = self.t[j].text
            if text == '@' and j + 1 < self.n and self.t[j + 1].text != 'interface':
                j = self.skip_annotation(j)
            elif text in MODIFIERS:
                j += 1
            else:
                return j
        return j

    def skip_annotation(self, j):
        j += 2
        while j + 1 < self.n and self.t[j].text == '.':
            j += 2
        if j < self.n and self.t[j].text == '(':
            depth = 0
            while j < self.n:
                text = self.t[j].text
                if text in ('(', '[', '{'):
                    depth += 1
                elif text in (')', ']', '}'):
                    depth -= 1
                    if depth == 0:
                        return j + 1
                j += 1
            raise Unsupported('lezáratlan annotáció')
        return j

    def classify(self):
        j = self.skip_prefix(self.i)
        word = self.t[j].text if j < self.n else None
        if word is None:
            raise Unsupported('váratlan fájlvég')
        if word in TYPE_WORDS or (word == '@' and j + 1 < self.n and self.t[j + 1].text == 'interface'):
            return 'type'
        if word in ('{', ';'):
            return 'init' if word == '{' else 'empty'
        for k in range(j, self.n):
            text = self.t[k].text
            if text == '(':
                return 'method'
            if text in ('=', ';'):
                return 'field'
            if text in ('{', '}'):
                break
        raise Unsupported(f'ismeretlen osztálytag: {word!r}')

    def member(self, indent, previous, owner, top=False):
        kind = self.classify()
        if kind == 'empty':
            if self.cur is None:
                self.emit(line=indent)
            else:
                self.emit(space='')
            return previous
        self.blank = previous is not None and (kind != 'field' or previous != 'field' or self.blank_before())
        if kind == 'type':
            self.type_decl(indent, owner, top)
        elif kind == 'init':
            self.prefix(indent, True)
            self.expect('{')
            self.place(self.nextline) if self.nextline is not None else self.emit(space=' ')
            self.block_rest(indent)
        else:
            self.declaration(indent, kind)
        return kind

    def prefix(self, indent, own_line):
        """Annotations and modifiers; afterwards self.nextline is the indent of a new line or None."""
        self.nextline = indent
        while True:
            text = self.at()
            if text == '@' and self.at(1) != 'interface':
                wrap = Wrap(self.tok().lind, indent)
                self.place(self.nextline)
                self.emit(space='')
                while self.at() == '.':
                    self.emit(space='')
                    self.emit(space='')
                if self.at() == '(':
                    self.emit(space='')
                    self.expr(wrap, (')',))
                    self.emit(space='')
                following = self.tok()
                self.nextline = indent if own_line or (following is not None and following.nl > 0) else None
            elif text in MODIFIERS:
                self.place(self.nextline)
                self.nextline = None
            else:
                return

    def declaration(self, indent, kind):
        self.prefix(indent, kind == 'method')
        wrap = Wrap(self.tok().lind, indent)
        self.expr(wrap, ('{', ';') if kind == 'method' else (';',), first_line=self.nextline)
        if self.at() == ';':
            self.emit(space='')
        else:
            self.expect('{')
            self.emit(space=' ')
            self.block_rest(indent)

    def type_decl(self, indent, owner, top):
        self.prefix(indent, True)
        wrap = Wrap(self.tok().lind, indent)
        name_at = self.i + (2 if self.at() == '@' else 1)
        name = self.t[name_at].text if name_at < self.n else ''
        enum = self.at() == 'enum'
        self.expr(wrap, ('{',), first_line=self.nextline)
        self.expect('{')
        self.emit(space=' ')
        if top:
            self.top.append(name)
        elif owner:
            self.members.setdefault(owner, set()).add(name)
        if enum:
            self.enum_body(indent + INDENT)
        else:
            self.class_body(indent + INDENT, name if top else None)
        self.expect('}')
        self.emit(line=indent, comment_indent=indent + INDENT)

    def class_body(self, indent, owner=None):
        previous = None
        while self.at() != '}':
            if self.at() is None:
                raise Unsupported('lezáratlan osztálytörzs')
            previous = self.member(indent, previous, owner)

    def enum_body(self, indent):
        previous = None
        if self.at() not in (';', '}'):
            self.expr(Wrap(self.tok().lind, indent), (';', '}'), first_line=indent, enum=True)
            previous = 'constants'
        if self.at() == ';':
            self.emit(space='') if previous else self.emit(line=indent)
            previous = 'constants'
            while self.at() != '}':
                if self.at() is None:
                    raise Unsupported('lezáratlan enum')
                previous = self.member(indent, previous, None)

    def class_block(self):
        """The body of an anonymous class or enum constant: '{' ends the open line."""
        outer = self.indent
        self.expect('{')
        if self.at(1) == '}' and not self.tok(1).lead:
            self.emit(space=' ')
            self.emit(space='')
            return
        self.emit(space=' ')
        self.class_body(outer + INDENT)
        self.expect('}')
        self.emit(line=outer, comment_indent=outer + INDENT)

    def lambda_block(self):
        outer = self.indent
        self.expect('{')
        if self.at(1) == '}' and not self.tok(1).lead:
            self.emit(space=' ')
            self.emit(space='')
            return
        self.emit(space=' ')
        self.block_body(outer + INDENT)
        self.expect('}')
        self.emit(line=outer, comment_indent=outer + INDENT)

    def block_rest(self, indent):
        """After '{': the statements one level deeper, then '}' alone at indent."""
        self.block_body(indent + INDENT)
        self.expect('}')
        self.emit(line=indent, comment_indent=indent + INDENT)

    def block_body(self, indent):
        first = True
        while self.at() != '}':
            if self.at() is None:
                raise Unsupported('lezáratlan blokk')
            self.blank = not first and self.blank_before()
            self.statement(indent)
            first = False

    def local_type(self):
        j = self.skip_prefix(self.i)
        return j < self.n and self.t[j].text in TYPE_WORDS

    def statement(self, indent):
        text, token = self.at(), self.tok()
        if text == '{':
            self.emit(line=indent)
            self.block_rest(indent)
        elif text == ';':
            self.emit(line=indent)
        elif text == 'if':
            self.if_statement(indent, False)
        elif text in ('for', 'while') or (text == 'synchronized' and self.at(1) == '('):
            self.emit(line=indent)
            self.header(indent)
            self.body(indent)
        elif text == 'do':
            self.emit(line=indent)
            self.body(indent)
            self.expect('while')
            self.emit(space=' ')
            self.header(indent)
            self.expect(';')
            self.emit(space='')
        elif text == 'try':
            self.try_statement(indent)
        elif text == 'switch':
            self.switch_statement(indent)
        elif text in ('else', 'catch', 'finally', 'case', 'default'):
            raise Unsupported(f'váratlan {text!r}')
        elif self.local_type():
            self.type_decl(indent, None, False)
        elif token.kind == 'id' and self.at(1) == ':':
            raise Unsupported('címkézett utasítás')
        else:
            self.expr(Wrap(token.lind, indent), (';',), first_line=indent)
            self.expect(';')
            self.emit(space='')

    def header(self, indent):
        """'(' ... ')' after a keyword, on the keyword's line; wrapped lines relative to it."""
        wrap = Wrap(self.t[self.i - 1].lind, indent)
        self.expect('(')
        self.emit(space=' ')
        self.expr(wrap, (')',))
        self.expect(')')
        self.emit(space='')

    def body(self, indent):
        """The body of if/else/for/while/do: always a block."""
        if self.at() == '{':
            self.emit(space=' ')
            self.block_rest(indent)
        else:
            self.put('{')
            self.prev = '{'
            self.statement(indent + INDENT)
            self.start_line(indent)
            self.put('}')
            self.prev = '}'

    def if_statement(self, indent, chained):
        if chained:
            self.emit(space=' ')
        else:
            self.emit(line=indent)
        self.header(indent)
        self.body(indent)
        if self.at() == 'else':
            self.emit(space=' ')
            if self.at() == 'if':
                self.if_statement(indent, True)
            else:
                self.body(indent)

    def try_statement(self, indent):
        self.emit(line=indent)
        if self.at() == '(':
            self.header(indent)
        self.expect('{')
        self.emit(space=' ')
        self.block_rest(indent)
        while self.at() in ('catch', 'finally'):
            finally_ = self.at() == 'finally'
            self.emit(space=' ')
            if not finally_:
                self.header(indent)
            self.expect('{')
            self.emit(space=' ')
            self.block_rest(indent)
            if finally_:
                break

    def switch_statement(self, indent):
        self.emit(line=indent)
        self.header(indent)
        self.expect('{')
        self.emit(space=' ')
        inner, first = indent + INDENT, True
        while self.at() != '}':
            text = self.at()
            if text not in ('case', 'default'):
                raise Unsupported('ismeretlen switch-törzs')
            self.blank = not first and self.blank_before()
            first = False
            start = self.tok()
            self.emit(line=inner)
            if text == 'case':
                self.expr(Wrap(start.lind, inner), (':', '->'))
            if self.at() != ':':
                raise Unsupported('switch-szabály (->)')
            self.emit(space='')
            if self.at() == '{':
                self.emit(space=' ')
                self.block_rest(inner)
            statement_first = True
            while self.at() not in ('case', '}') and not (self.at() == 'default' and self.at(1) in (':', '->')):
                if self.at() is None:
                    raise Unsupported('lezáratlan switch')
                self.blank = not statement_first and self.blank_before()
                self.statement(inner + INDENT)
                statement_first = False
        self.emit(line=indent, comment_indent=inner + INDENT)

    def expr(self, wrap, stop, first_line=None, enum=False):
        """Tokens up to (not including) a stop token at depth 0. Line breaks inside stay; lambda
        bodies and anonymous classes become blocks, array initializers stay expressions."""
        stack, new_type, first = [], None, True
        while True:
            token = self.tok()
            if token is None:
                raise Unsupported('váratlan fájlvég egy kifejezésben')
            text = token.text
            if not stack and text in stop:
                return
            if enum and not stack and text == '{':
                self.class_block()
                first = False
                continue
            if first and first_line is not None:
                self.emit(line=first_line)
            elif token.nl > 0 and not first:
                self.emit(line=self.wrap_indent(wrap, stack, text, enum))
            else:
                self.emit()
            first = False
            if token.kind not in ('id', 'op'):
                new_type = None
                continue
            if new_type is not None:
                if text in ('<', '>'):
                    new_type += 1 if text == '<' else -1
                    continue
                if text == '(' and new_type <= 0:
                    stack.append('new(')
                    new_type = None
                    continue
                if not (token.kind == 'id' or text in ('.', ',', '?', '&', '@')):
                    new_type = None
            if text == 'new':
                new_type = 0
            elif text in ('(', '['):
                stack.append(text)
            elif text == '{':
                following = self.tok()
                stack.append(('{', self.indent) if following is not None and following.nl > 0 else '{')
            elif text in (')', ']', '}'):
                if not stack:
                    raise Unsupported('párosítatlan zárójel')
                if stack.pop() == 'new(' and self.at() == '{':
                    self.class_block()
            elif text == '->' and self.at() == '{':
                self.lambda_block()

    def wrap_indent(self, wrap, stack, text, enum):
        for opened in reversed(stack):
            if isinstance(opened, tuple):  # array initializer written over several lines
                return opened[1] if text == '}' and opened is stack[-1] else opened[1] + INDENT
        if enum and not stack and self.prev == ',':
            return wrap.new
        return wrap.indent(self.tok())


# ---------------------------------------------------------------------------- files

def _semicolon(code, start):
    for k in range(start, len(code)):
        if code[k].text == ';':
            return k
    raise Unsupported('lezáratlan package/import')


def _comments(tokens):
    return [c for t in tokens for c in t.lead + t.trail]


def _import_group(static, path, package, rules):
    if static and 'STATIC' in rules:
        return 'STATIC'
    for rule in rules:
        depth = rule[13:-1] if rule.startswith('SAME_PACKAGE(') else None
        if depth and package and path.split('.')[:int(depth)] == package.split('.')[:int(depth)]:
            return rule
    if 'STANDARD_JAVA_PACKAGE' in rules and STANDARD_JAVA.match(path):
        return 'STANDARD_JAVA_PACKAGE'
    return 'THIRD_PARTY_PACKAGE' if 'THIRD_PARTY_PACKAGE' in rules else ''


def _wildcard_names(prefix, member_types):
    if prefix in WILDCARDS:
        return WILDCARDS[prefix]
    if prefix in member_types:
        return member_types[prefix]
    last = prefix.rsplit('.', 1)[-1]
    return member_types.get(last) if last[:1].isupper() else None


def import_lines(imports, package, used, settings=None, member_types=None) -> list:
    """The import lines in CustomImportOrder: groups by the rules, sorted, one empty line between."""
    settings = SETTINGS if settings is None else settings
    member_types = MEMBER_TYPES if member_types is None else member_types
    chosen = {}
    for static, path in imports:
        if path.endswith('.*'):
            names = None if static else _wildcard_names(path[:-2], member_types)
            if names is None:
                chosen[(static, path)] = True  # not known here: stays a wildcard
            else:
                chosen.update(((False, path[:-2] + '.' + n), True) for n in names & used)
            continue
        owner, _, simple = path.rpartition('.')
        if simple not in used or (not static and (owner == 'java.lang' or (package and owner == package))):
            continue
        chosen[(static, path)] = True
    rules = settings['import_order'].split('###')
    groups = {}
    for static, path in chosen:
        groups.setdefault(_import_group(static, path, package, rules), []).append((static, path))
    lines = []
    for group in rules + ['']:
        if group not in groups:
            continue
        if lines:
            lines.append('')
        for static, path in sorted(groups[group], key=lambda item: tuple(item[1].split('.'))):
            lines.append(f'import {"static " if static else ""}{path};')
    return lines


def _format(text: str, settings=None, member_types=None):
    code, tail = attach(tokenize(text))
    for token in code:
        if token.kind in ('str', 'chr'):
            token.text = _literal(token.text)
    head, package, imports, notes, i = [], None, [], [], 0
    blank_after_head = bool(code) and code[0].nl > 1
    if code and code[0].text == 'package':
        head, code[0].lead = code[0].lead, []
        end = _semicolon(code, 0)
        package = ''.join(t.text for t in code[1:end])
        notes += _comments(code[:end + 1])
        i = end + 1
    while i < len(code) and code[i].text == 'import':
        if i == 0:
            head, code[0].lead = code[0].lead, []
        end = _semicolon(code, i)
        static = code[i + 1].text == 'static'
        imports.append((static, ''.join(t.text for t in code[i + 1 + static:end])))
        notes += _comments(code[i:end + 1])
        i = end + 1
    body = code[i:]
    emitter = Emitter(body)
    emitter.run()
    used = {t.text for t in body if t.kind == 'id'}
    for comment in _comments(body) + tail:
        if comment.text.startswith('/**'):
            used |= set(JAVADOC_REFERENCE.findall(comment.text))
    lines = [comment_text(c, 0) for c in head]
    if head and blank_after_head and (package or imports):
        lines.append('')
    if package:
        lines.append(f'package {package};')
    block = import_lines(imports, package, used, settings, member_types)
    if notes or block:
        if lines:
            lines.append('')
        lines += [comment_text(c, 0) for c in notes] + block
    if emitter.lines:
        if lines:
            lines.append('')
        lines += emitter.lines
    lines += [comment_text(c, 0) for c in tail]
    learned = {}
    for name in emitter.top:
        learned[name] = frozenset(emitter.members.get(name, ()))
        if package:
            learned[package + '.' + name] = learned[name]
    return '\n'.join(line.rstrip() for line in lines).strip('\n') + '\n', learned


def _value(token):
    if token.kind in ('str', 'chr'):
        value = _literal_value(token.text[1:-1])
        return token.kind, token.text if value is None else value
    return token.text


def _signature(text: str):
    """Package, code tokens after the imports (braces aside, literals by value) and comments."""
    tokens = tokenize(text)
    code = [t for t in tokens if t.kind not in ('lc', 'bc')]
    comments = sorted(' '.join(t.text.split()) for t in tokens if t.kind in ('lc', 'bc'))
    i, package = 0, None
    if code and code[0].text == 'package':
        end = _semicolon(code, 0)
        package, i = ''.join(t.text for t in code[1:end]), end + 1
    while i < len(code) and code[i].text == 'import':
        i = _semicolon(code, i) + 1
    body, depth = [], 0
    for token in code[i:]:
        if token.kind == 'op' and token.text in ('{', '}'):
            depth += 1 if token.text == '{' else -1
            if depth < 0:
                raise Unsupported('párosítatlan kapcsos zárójel')
            continue
        body.append(_value(token))
    if depth:
        raise Unsupported('párosítatlan kapcsos zárójel')
    return package, body, comments

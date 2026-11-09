"""The SQL of a trigger as repository methods, written by the migrator; the Java assistant only calls them.

backend_trigger_mode=ai, data access jpa. A small local model fails most often on the repository: it writes
EntityManager code into the service, or calls a repository method it never wrote, and every such answer costs a
correction call. So every SQL statement of the task's code that the subset below translates exactly becomes a
@Query method of its table's repository (an interface extending JpaRepository: JPQL over the Domain, @Modifying for
UPDATE/DELETE, a default method for INSERT and SELECT INTO, the sequence as the one native query), with the original
statement in its comment. The prompt lists these methods as the database access of the code and the model writes the service's
logic only; a statement outside the subset is listed with its reason, and the model writes that one method.

The subset: one table, no join, subquery, grouping or hierarchy - what JPQL runs as Oracle would.
- SELECT columns or one aggregate (COUNT, SUM, MIN, MAX) INTO ... FROM t [WHERE]: a default method over the rows of
  its @Query, so no row is NoResultException (NO_DATA_FOUND) and several rows NonUniqueResultException
  (TOO_MANY_ROWS), as in PL/SQL. One column returns its value, several the Domain; COUNT returns a BigDecimal (an
  Oracle NUMBER), an aggregate always one value (null over no row, as in Oracle);
- SELECT seq.NEXTVAL INTO ... FROM DUAL: the sequence value (the one native query the project allows);
- the query of a cursor or of a FOR loop: the entities in a List, ORDER BY kept; a constant select list
  (SELECT 1 ...) is an existence test: true when a row is found (cursor%FOUND);
- INSERT INTO t (every column of t) VALUES (...): a default method that saves a new Domain (an existing key is
  EntityExistsException, the DUP_VAL_ON_INDEX: save would update it). With fewer columns Oracle would fill the
  others with their defaults, which the Domain does not know: outside the subset;
- UPDATE t SET column = value, ... [WHERE] / DELETE FROM t [WHERE]: a @Modifying JPQL @Query, returning the number
  of rows (SQL%ROWCOUNT).
Conditions: AND, OR, NOT, comparisons, IS [NOT] NULL, [NOT] LIKE, [NOT] IN (list), [NOT] BETWEEN, UPPER / LOWER /
TRIM and || on text, between a column and a column, a bind (:BLOCK.ITEM), a PL/SQL variable or a literal. Every
value is a bound parameter: a NULL value matches nothing, as in Oracle. SYSDATE is CURRENT_TIMESTAMP (the
database's clock) in a query, the application server's clock (seconds, like an Oracle DATE) in a new record. A column
of the table that the Domain does not map (a block's Domain maps the block's columns) is refused, never read as a
PL/SQL variable.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .common import decode_line_escapes, jstr, name
from .plsql import Unsupported
from .plsql_passthrough import scan, significant

COMPARE = {'=': 'equal', '<>': 'notEqual', '!=': 'notEqual', '<': 'lessThan', '>': 'greaterThan',
           '<=': 'lessThanOrEqualTo', '>=': 'greaterThanOrEqualTo'}
MIRROR = {'=': '=', '<>': '<>', '!=': '!=', '<': '>', '>': '<', '<=': '>=', '>=': '<='}
TEXT_FUNCTIONS = {'UPPER': 'upper', 'LOWER': 'lower', 'TRIM': 'trim'}
AGGREGATES = {'COUNT', 'SUM', 'MIN', 'MAX'}
# A statement starts after these (its first word is SELECT / INSERT ...).
STARTS = {';', 'BEGIN', 'THEN', 'ELSE', 'LOOP', 'DECLARE', 'EXCEPTION', 'IS', 'AS'}
# Words after a table name that are no alias.
CLAUSES = {'WHERE', 'ORDER', 'GROUP', 'HAVING', 'CONNECT', 'START', 'UNION', 'MINUS', 'INTERSECT', 'FOR', 'SET',
           'VALUES', 'RETURNING', 'RETURN', 'JOIN', 'INNER', 'LEFT', 'RIGHT', 'FULL', 'CROSS', 'NATURAL', 'PARTITION'}
SIMPLE = {'java.math.BigDecimal': 'BigDecimal', 'java.time.LocalDateTime': 'LocalDateTime',
          'java.time.OffsetDateTime': 'OffsetDateTime'}
COMPARABLE = {'String', 'BigDecimal', 'LocalDateTime', 'OffsetDateTime', 'Float', 'Double'}
WIDTH = 96  # the comment lines of a generated method


# What a generated method returns, by kind: (the method's comment, the prompt's line).
NOTES = {
    'value': ('nincs sor: NoResultException (NO_DATA_FOUND); több sor: NonUniqueResultException (TOO_MANY_ROWS)',
              'no row throws NoResultException (NO_DATA_FOUND), several rows NonUniqueResultException (TOO_MANY_ROWS)'),
    'entity': ('a sor entityként (getterekkel); nincs sor: NoResultException (NO_DATA_FOUND); több sor: '
               'NonUniqueResultException (TOO_MANY_ROWS)',
               'the row as the entity (getters); no row throws NoResultException (NO_DATA_FOUND), several rows '
               'NonUniqueResultException (TOO_MANY_ROWS)'),
    'exists': ('igaz, ha van legalább egy sor (a kurzor %FOUND értéke)', "true when a row exists (the cursor's %FOUND)"),
    'list': ('a sorok entityként (getterekkel), a lekérdezés sorrendjében; sor nélkül üres lista',
             "the rows as entities (getters) in the query's order; no row: an empty list"),
    'count': ('mindig egy szám (Oracle NUMBER), sor nélkül 0', 'always one number (Oracle NUMBER), 0 when no row'),
    'aggregate': ('mindig egy érték; sor nélkül null, ahogy az Oracle-ben', 'always one value; null when no row (as in Oracle)'),
    'insert': ('új rekord (save, azonnal íródik); létező kulcsra EntityExistsException (DUP_VAL_ON_INDEX)',
               'inserts the new record; an existing key throws EntityExistsException (DUP_VAL_ON_INDEX)'),
    'update': ('a módosított sorok száma (SQL%ROWCOUNT)', 'the number of updated rows (SQL%ROWCOUNT)'),
    'delete': ('a törölt sorok száma (SQL%ROWCOUNT)', 'the number of deleted rows (SQL%ROWCOUNT)'),
    'sequence': ('a szekvencia következő értéke', 'the next value of the sequence'),
}


class Refused(Exception):
    """The statement is outside the subset; the text says why (for the prompt and the notes)."""


@dataclass
class Found:
    """One SQL statement of the PL/SQL, from its keyword to its ';'."""
    kind: str          # select (SELECT INTO), cursor, loop (FOR r IN (SELECT ...)), insert, update, delete
    tokens: list
    text: str
    cursor: str = ''   # a cursor's name


@dataclass
class Method:
    """A generated repository method."""
    repository: str    # the class (RendelesVevoRepository)
    variable: str      # its field in the service
    name: str
    signature: str     # String findNevByKod(String kod)
    source: str        # the Java method, with its comment
    statement: str     # the Forms SQL, on one line
    kind: str          # NOTES: what the result means (value, entity, exists, list, count ...)
    arguments: tuple = ()  # the value of each parameter in order: a bind (:BLOCK.ITEM) or a PL/SQL variable
    columns: tuple = ()    # an entity SELECT INTO: (entity field, type) of each INTO target, in order


# ---------------------------------------------------------------------- finding the statements

def find(text: str) -> list[Found]:
    """The SQL statements of PL/SQL code (cursor and FOR loop queries included), in order."""
    source = decode_line_escapes(text or '')
    try:
        tokens = significant(scan(source))
    except Unsupported:
        return []
    found, i, n = [], 0, len(tokens)
    while i < n:
        word = tokens[i][1].upper()
        start = i == 0 or tokens[i - 1][1].upper() in STARTS
        if word == 'CURSOR' and start and i + 1 < n:
            at = next((k for k in range(i, n) if tokens[k][1].upper() == 'IS' or tokens[k][1] == ';'), n)
            if at + 1 < n and tokens[at][1].upper() == 'IS' and tokens[at + 1][1].upper() == 'SELECT':
                end = statement_end(tokens, at + 1)
                found.append(Found('cursor', tokens[at + 1:end], text_of(source, tokens, at + 1, end), tokens[i + 1][1].upper()))
                i = end
                continue
        elif word in {'SELECT', 'INSERT', 'UPDATE', 'DELETE'} and start:
            end = statement_end(tokens, i)
            found.append(Found('select' if word == 'SELECT' else word.lower(), tokens[i:end], text_of(source, tokens, i, end)))
            i = end
            continue
        elif word == 'FOR' and start and i + 4 < n and tokens[i + 2][1].upper() == 'IN' and tokens[i + 3][1] == '(' \
                and tokens[i + 4][1].upper() == 'SELECT':
            close = matching(tokens, i + 3)
            if close is not None:
                found.append(Found('loop', tokens[i + 4:close], text_of(source, tokens, i + 4, close)))
                i = close
                continue
        i += 1
    return found


def statement_end(tokens, i):
    depth = 0
    for k in range(i, len(tokens)):
        if tokens[k][1] == '(':
            depth += 1
        elif tokens[k][1] == ')':
            depth -= 1
        elif tokens[k][1] == ';' and depth <= 0:
            return k
    return len(tokens)


def matching(tokens, i):
    depth = 0
    for k in range(i, len(tokens)):
        depth += {'(': 1, ')': -1}.get(tokens[k][1], 0)
        if depth == 0:
            return k
    return None


def text_of(source, tokens, start, end):
    return source[tokens[start][2]:tokens[end - 1][3]] if end > start else ''


def one_line(text: str) -> str:
    return ' '.join(text.split())


# ---------------------------------------------------------------------- parsing the subset

class Parser:
    def __init__(self, tokens):
        self.tokens, self.i = tokens, 0

    def peek(self, *values):
        return self.i < len(self.tokens) and self.tokens[self.i][1].upper() in values

    def kind(self):
        return self.tokens[self.i][0] if self.i < len(self.tokens) else 'end'

    def take(self):
        if self.i >= len(self.tokens):
            raise Refused('váratlanul véget ér')
        self.i += 1
        return self.tokens[self.i - 1]

    def accept(self, *values):
        if self.peek(*values):
            return self.take()[1].upper()
        return None

    def need(self, value):
        if not self.accept(value):
            raise Refused(value + ' kellene' + (', de ' + self.tokens[self.i][1] + ' áll' if self.i < len(self.tokens) else ''))

    def end(self):
        if self.i < len(self.tokens):
            raise Refused('nem támogatott rész: ' + ' '.join(t[1] for t in self.tokens[self.i:self.i + 4]))

    def table(self, columns=False):
        """table [alias]; columns: an INSERT's column list may follow the name."""
        token = self.take()
        if token[0] != 'ident' or token[1].startswith('"'):
            raise Refused('a tábla neve nem egyszerű azonosító: ' + token[1])
        table = token[1].upper()
        if self.accept('.'):
            table += '.' + self.take()[1].upper()
        if self.peek(',', '@') or (self.peek('(') and not columns):
            raise Refused('több tábla, táblafüggvény vagy adatbázis-link')
        alias = None
        if self.kind() == 'ident' and not self.peek(*CLAUSES):
            alias = self.take()[1].upper()
        if self.peek(',') or self.peek('JOIN', 'INNER', 'LEFT', 'RIGHT', 'FULL', 'CROSS', 'NATURAL'):
            raise Refused('több tábla (join)')
        return table, alias

    # SELECT items [INTO targets] FROM table [alias] [WHERE condition] [ORDER BY ...]
    def select(self):
        self.need('SELECT')
        if self.peek('DISTINCT', 'UNIQUE', 'ALL'):
            raise Refused('DISTINCT')
        items = [self.item()]
        while self.accept(','):
            items.append(self.item())
        into = []
        if self.accept('INTO'):
            into.append(self.target())
            while self.accept(','):
                into.append(self.target())
        self.need('FROM')
        table, alias = self.table()
        where = self.condition() if self.accept('WHERE') else None
        order = []
        if self.accept('ORDER'):
            self.need('BY')
            while True:
                column = self.operand()
                direction = self.accept('ASC', 'DESC') or 'ASC'
                if self.peek('NULLS'):
                    raise Refused('ORDER BY ... NULLS FIRST/LAST')
                order.append((column, direction))
                if not self.accept(','):
                    break
        if self.peek('GROUP', 'HAVING', 'CONNECT', 'START', 'UNION', 'MINUS', 'INTERSECT', 'FOR'):
            raise Refused(self.tokens[self.i][1].upper() + ' (csoportosítás, hierarchia, halmazművelet vagy zárolás)')
        self.end()
        return {'op': 'select', 'items': items, 'into': into, 'table': table, 'alias': alias, 'where': where, 'order': order}

    def target(self):
        token = self.take()
        if token[0] == 'bind':
            return token[1]
        if token[0] != 'ident':
            raise Refused('nem támogatott INTO-cél: ' + token[1])
        text = token[1]
        while self.accept('.'):
            text += '.' + self.take()[1]
        return text

    def item(self):
        if self.accept('*'):
            return {'op': 'star'}
        if self.peek(*AGGREGATES) and self.i + 1 < len(self.tokens) and self.tokens[self.i + 1][1] == '(':
            function = self.take()[1].upper()
            self.need('(')
            if self.peek('DISTINCT'):
                raise Refused(function + '(DISTINCT ...)')
            argument = {'op': 'star'} if function == 'COUNT' and self.accept('*') else self.operand()
            self.need(')')
            if self.kind() == 'ident' and not self.peek('INTO', 'FROM'):
                self.take()  # a column alias
            return {'op': 'aggregate', 'function': function, 'value': argument}
        value = self.operand()
        if self.kind() == 'ident' and not self.peek('INTO', 'FROM'):
            self.take()  # a column alias
        return value

    # INSERT INTO table [alias] (columns) VALUES (values)
    def insert(self):
        self.need('INSERT')
        self.need('INTO')
        table, alias = self.table(columns=True)
        if not self.accept('('):
            raise Refused('INSERT oszloplista nélkül')
        columns = [self.operand()]
        while self.accept(','):
            columns.append(self.operand())
        self.need(')')
        if self.peek('SELECT'):
            raise Refused('INSERT ... SELECT')
        self.need('VALUES')
        self.need('(')
        values = [self.value()]
        while self.accept(','):
            values.append(self.value())
        self.need(')')
        if self.peek('RETURNING', 'RETURN'):
            raise Refused('RETURNING')
        self.end()
        if len(columns) != len(values):
            raise Refused('az oszlopok és az értékek száma eltér')
        return {'op': 'insert', 'table': table, 'alias': alias, 'columns': columns, 'values': values}

    # UPDATE table [alias] SET column = value, ... [WHERE condition]
    def update(self):
        self.need('UPDATE')
        table, alias = self.table()
        self.need('SET')
        sets = []
        while True:
            if self.peek('('):
                raise Refused('UPDATE SET (oszlopok) = (SELECT ...)')
            column = self.operand()
            self.need('=')
            sets.append((column, self.value()))
            if not self.accept(','):
                break
        where = self.condition() if self.accept('WHERE') else None
        if self.peek('RETURNING', 'RETURN'):
            raise Refused('RETURNING')
        self.end()
        return {'op': 'update', 'table': table, 'alias': alias, 'sets': sets, 'where': where}

    # DELETE [FROM] table [alias] [WHERE condition]
    def delete(self):
        self.need('DELETE')
        self.accept('FROM')
        table, alias = self.table()
        where = self.condition() if self.accept('WHERE') else None
        if self.peek('RETURNING', 'RETURN'):
            raise Refused('RETURNING')
        self.end()
        return {'op': 'delete', 'table': table, 'alias': alias, 'where': where}

    # conditions
    def condition(self):
        parts = [self.conjunction()]
        while self.accept('OR'):
            parts.append(self.conjunction())
        return parts[0] if len(parts) == 1 else {'op': 'or', 'parts': parts}

    def conjunction(self):
        parts = [self.negation()]
        while self.accept('AND'):
            parts.append(self.negation())
        return parts[0] if len(parts) == 1 else {'op': 'and', 'parts': parts}

    def negation(self):
        if self.accept('NOT'):
            return {'op': 'not', 'value': self.negation()}
        return self.predicate()

    def predicate(self):
        if self.peek('EXISTS'):
            raise Refused('EXISTS (allekérdezés)')
        if self.peek('('):
            mark = self.i
            self.take()
            try:
                inner = self.condition()
                self.need(')')
                return inner
            except Refused:
                self.i = mark  # a parenthesised value, not a condition
        left = self.value()
        if self.accept('IS'):
            negated = bool(self.accept('NOT'))
            self.need('NULL')
            return {'op': 'is_null', 'value': left, 'negated': negated}
        negated = bool(self.accept('NOT'))
        if self.accept('LIKE'):
            pattern = self.value()
            if self.peek('ESCAPE'):
                raise Refused('LIKE ... ESCAPE')
            return {'op': 'like', 'value': left, 'pattern': pattern, 'negated': negated}
        if self.accept('IN'):
            self.need('(')
            if self.peek('SELECT'):
                raise Refused('IN (allekérdezés)')
            items = [self.value()]
            while self.accept(','):
                items.append(self.value())
            self.need(')')
            return {'op': 'in', 'value': left, 'items': items, 'negated': negated}
        if self.accept('BETWEEN'):
            low = self.value()
            self.need('AND')
            return {'op': 'between', 'value': left, 'low': low, 'high': self.value(), 'negated': negated}
        if negated:
            raise Refused('NOT után LIKE, IN vagy BETWEEN kellene')
        token = self.take()
        if token[1] not in COMPARE:
            raise Refused('nem támogatott összehasonlítás: ' + token[1])
        if self.peek('ANY', 'ALL', 'SOME'):
            raise Refused(token[1] + ' ANY/ALL')
        return {'op': 'compare', 'operator': token[1], 'left': left, 'right': self.value()}

    # values: an operand, text concatenated with ||, a number +/- a number
    def value(self):
        left = self.operand()
        while self.peek('||', '+', '-'):
            operator = self.take()[1]
            left = {'op': 'concat' if operator == '||' else 'arithmetic', 'operator': operator,
                    'left': left, 'right': self.operand()}
        if self.peek('*', '/'):
            raise Refused('szorzás, osztás')
        return left

    def operand(self):
        token = self.take()
        kind, text = token[0], token[1]
        if kind == 'bind':
            return {'op': 'input', 'source': text.upper()}
        if kind == 'string':
            if not text.startswith("'"):
                raise Refused('nem támogatott szöveg-literál: ' + text)
            value = text[1:-1].replace("''", "'")
            return {'op': 'literal', 'type': 'text', 'value': value or None}
        if kind == 'number':
            return {'op': 'literal', 'type': 'number', 'value': text}
        if text == '-' and self.kind() == 'number':
            return {'op': 'literal', 'type': 'number', 'value': '-' + self.take()[1]}
        if kind != 'ident' or text.startswith('"'):
            raise Refused('nem támogatott érték: ' + text)
        word = text.upper()
        if word == 'NULL':
            return {'op': 'literal', 'type': 'null', 'value': None}
        if word == 'SYSDATE':
            return {'op': 'sysdate'}
        if word in {'ROWNUM', 'ROWID', 'LEVEL', 'PRIOR', 'USER', 'UID', 'SYSTIMESTAMP', 'CURRENT_DATE', 'CASE'}:
            raise Refused(word)
        if self.peek('('):
            if word not in TEXT_FUNCTIONS:
                raise Refused('SQL-függvény: ' + word)
            self.take()
            argument = self.value()
            self.need(')')
            return {'op': 'function', 'name': word, 'value': argument}
        if self.accept('.'):
            second = self.take()
            if second[0] != 'ident':
                raise Refused('nem támogatott név: ' + text + '.' + second[1])
            if self.peek('.'):
                raise Refused('háromrészes név: ' + text + '.' + second[1])
            if second[1].upper() == 'NEXTVAL':
                return {'op': 'nextval', 'sequence': word}
            if second[1].upper() == 'CURRVAL':
                raise Refused('CURRVAL')
            return {'op': 'name', 'qualifier': word, 'name': second[1].upper()}
        return {'op': 'name', 'qualifier': None, 'name': word}


def parse(found: Found) -> dict:
    parser = Parser(found.tokens)
    kind = found.tokens[0][1].upper() if found.tokens else ''
    if kind == 'SELECT':
        return parser.select()
    if kind == 'INSERT':
        return parser.insert()
    if kind == 'UPDATE':
        return parser.update()
    if kind == 'DELETE':
        return parser.delete()
    raise Refused('nem SQL-utasítás')


# ---------------------------------------------------------------------- Java

def pascal(text: str) -> str:
    return name(text.lower(), 'pascal') or 'X'


def camel(text: str) -> str:
    result = pascal(text)
    return result[:1].lower() + result[1:]


def comment(lines: list[str]) -> list[str]:
    """A Javadoc comment; javac reads \\u escapes even in comments, so a backslash is written out."""
    result = ['    /**']
    for line in lines:
        line = line.replace('\\', '[backslash]').replace('*/', '* /')
        while len(line) > WIDTH:
            cut = line.rfind(' ', 0, WIDTH)
            cut = cut if cut > 20 else WIDTH
            result.append('     * ' + line[:cut].rstrip())
            line = '  ' + line[cut:].lstrip()
        result.append('     * ' + line)
    return result + ['     */']


class Builder:
    """One generated method: the table's Domain, its parameters (bound values), the JPQL of the statement."""

    def __init__(self, descriptor: dict, statement: dict):
        self.d = descriptor
        self.entity = descriptor['entity']
        self.columns = descriptor['columns']  # COLUMN -> field, the columns the Domain maps
        # columns of the table the Domain does not map (a block's Domain maps the block's columns): never a variable
        self.unmapped = {c.upper() for c in descriptor.get('all_columns', ())} - set(self.columns)
        self.types = {f: SIMPLE.get(t, t) for f, t in descriptor['fields'].items()}
        self.statement = statement
        self.table, self.alias = statement['table'], statement['alias']
        self.params = []    # [source, java name, type]
        self.sequences = []  # sequences the method uses (the repository gets their method)
        self.uses_now = False  # an INSERT's SYSDATE (the default method's clock)
        self.where_fields = []

    # names
    def column_field(self, node):
        """The Domain field of a column reference, or None (a PL/SQL variable)."""
        if node['op'] != 'name':
            return None
        qualifier = node['qualifier']
        if qualifier and qualifier not in {self.alias, self.table.rsplit('.', 1)[-1]}:
            raise Refused('ismeretlen tábla-előtag: ' + qualifier)
        field = self.columns.get(node['name'])
        if field is None and node['name'] in self.unmapped:
            raise Refused('a(z) ' + node['name'] + ' oszlopot a ' + self.entity + ' nem képezi le')
        if field is None and qualifier:
            raise Refused('a táblában nincs ' + node['name'] + ' oszlop')
        return field

    def param(self, source: str, typ: str, hint: str) -> str:
        """The JPQL named parameter of a bound value (:name), one per source."""
        for known in self.params:
            if known[0] == source:
                if known[2] != typ:
                    raise Refused(source + ' két különböző típusú oszloppal szerepel')
                return ':' + known[1]
        base = hint if re.fullmatch(r'[a-z][A-Za-z0-9]*', hint) else camel(hint)  # a Domain field is camelCase already
        if base in {'d', 'entity', 'now', 'rows', 'value'}:
            base += 'Param'
        java, counter = base, 2
        while java in {p[1] for p in self.params}:
            java, counter = base + str(counter), counter + 1
        self.params.append([source, java, typ])
        return ':' + java

    # expressions: (JPQL text, type)
    def expression(self, node, expected: str | None = None, hint: str = 'value'):
        op = node['op']
        if op == 'name':
            field = self.column_field(node)
            if field is not None:
                typ = self.types[field]
                if typ == 'byte[]':
                    raise Refused('bináris oszlop')
                return 'd.' + field, typ
            source = node['name']
            if expected is None:
                raise Refused('a(z) ' + source + ' változó típusa nem dönthető el (nincs mellette oszlop)')
            return self.param(source, expected, hint if hint != 'value' else source), expected
        if op == 'input':
            if expected is None:
                raise Refused('a(z) ' + node['source'] + ' típusa nem dönthető el (nincs mellette oszlop)')
            item = node['source'].lstrip(':').split('.')[-1]
            return self.param(node['source'], expected, hint if hint != 'value' else item), expected
        if op == 'literal':
            if node['type'] == 'null':
                raise Refused('NULL érték összehasonlításban vagy kifejezésben')
            typ = expected or ('String' if node['type'] == 'text' else 'BigDecimal')
            if node['type'] == 'text' and typ == 'String':
                return "'" + node['value'].replace("'", "''") + "'", 'String'
            if node['type'] == 'number' and typ == 'BigDecimal':
                return node['value'], 'BigDecimal'
            raise Refused('implicit típuskonverzió (' + node['type'] + ' → ' + typ + ')')
        if op == 'sysdate':
            if expected not in {None, 'LocalDateTime'}:
                raise Refused('SYSDATE nem dátum oszloppal')
            return 'CURRENT_TIMESTAMP', 'LocalDateTime'  # the database's clock, like SYSDATE
        if op == 'function':
            value, typ = self.expression(node['value'], 'String', hint)
            if typ != 'String':
                raise Refused(node['name'] + ' nem szövegen')
            return f'{node["name"]}({value})', 'String'
        if op == 'concat':
            left, lt = self.expression(node['left'], 'String', hint)
            right, rt = self.expression(node['right'], 'String', hint)
            if lt != 'String' or rt != 'String':
                raise Refused('|| nem szövegen (implicit konverzió)')
            return f'CONCAT({left}, {right})', 'String'
        if op == 'arithmetic':
            left, lt = self.expression(node['left'], expected, hint)
            right, rt = self.expression(node['right'], lt, hint)
            if lt != 'BigDecimal' or rt != 'BigDecimal':
                raise Refused('aritmetika nem NUMBER oszlopon')
            return f'({left} {node["operator"]} {right})', 'BigDecimal'
        if op == 'nextval':
            raise Refused('szekvencia a feltételben')
        raise Refused('nem támogatott érték: ' + op)

    def pair(self, left_node, right_node):
        """Two sides of a comparison: the column side decides the other's type."""
        left_field = self.field_of(left_node)
        right_field = self.field_of(right_node)
        if left_field is None and right_field is None:
            if not self.typed(left_node) and not self.typed(right_node):
                raise Refused('az összehasonlításban nincs oszlop')
        if left_field is not None or self.typed(left_node):
            left, lt = self.expression(left_node, None, left_field or 'value')
            right, rt = self.expression(right_node, lt, left_field or 'value')
        else:
            right, rt = self.expression(right_node, None, right_field or 'value')
            left, lt = self.expression(left_node, rt, right_field or 'value')
        if lt != rt:
            raise Refused('eltérő típusok összehasonlítása (' + lt + ', ' + rt + ')')
        for field in (left_field, right_field):
            if field and field not in self.where_fields:
                self.where_fields.append(field)
        return left, right, lt

    def field_of(self, node):
        """The field of a column, also inside UPPER(...) or ||."""
        if node['op'] == 'name':
            return self.column_field(node)
        if node['op'] in {'function'}:
            return self.field_of(node['value'])
        if node['op'] in {'concat', 'arithmetic'}:
            return self.field_of(node['left']) or self.field_of(node['right'])
        return None

    def typed(self, node):
        """A value whose type is known without a column: a literal, SYSDATE, a text function."""
        return node['op'] in {'literal', 'sysdate', 'function', 'concat'} and not (node['op'] == 'literal' and node['type'] == 'null')

    def condition(self, node) -> str:
        op = node['op']
        if op in {'and', 'or'}:
            return '(' + f' {op.upper()} '.join(self.condition(p) for p in node['parts']) + ')'
        if op == 'not':
            return f'NOT ({self.condition(node["value"])})'
        if op == 'compare':
            if node['right']['op'] == 'name' and self.column_field(node['right']) is not None \
                    and self.column_field(node['left']) is None:
                node = dict(node, operator=MIRROR[node['operator']], left=node['right'], right=node['left'])
            left, right, typ = self.pair(node['left'], node['right'])
            if node['operator'] not in {'=', '<>', '!='} and typ not in COMPARABLE:
                raise Refused('nem rendezhető típus: ' + typ)
            return f'{left} {"<>" if node["operator"] == "!=" else node["operator"]} {right}'
        if op == 'is_null':
            field = self.field_of(node['value'])
            if field is None:
                raise Refused('IS NULL nem oszlopon')
            value, _ = self.expression(node['value'], None, field)
            if field not in self.where_fields:
                self.where_fields.append(field)
            return f'{value} IS {"NOT " if node["negated"] else ""}NULL'
        if op == 'like':
            left, right, typ = self.pair(node['value'], node['pattern'])
            if typ != 'String':
                raise Refused('LIKE nem szövegen')
            return f'{left} {"NOT " if node["negated"] else ""}LIKE {right}'
        if op == 'in':
            field = self.field_of(node['value'])
            if field is None:
                raise Refused('IN nem oszlopon')
            value, typ = self.expression(node['value'], None, field)
            items = [self.expression(item, typ, field)[0] for item in node['items']]
            if field not in self.where_fields:
                self.where_fields.append(field)
            return f'{value} {"NOT " if node["negated"] else ""}IN (' + ', '.join(items) + ')'
        if op == 'between':
            field = self.field_of(node['value'])
            if field is None:
                raise Refused('BETWEEN nem oszlopon')
            value, typ = self.expression(node['value'], None, field)
            if typ not in COMPARABLE:
                raise Refused('nem rendezhető típus: ' + typ)
            low = self.expression(node['low'], typ, field + 'From')[0]
            high = self.expression(node['high'], typ, field + 'To')[0]
            if field not in self.where_fields:
                self.where_fields.append(field)
            return f'{value} {"NOT " if node["negated"] else ""}BETWEEN {low} AND {high}'
        raise Refused('nem támogatott feltétel: ' + op)

    # the method
    def where(self):
        return self.condition(self.statement['where']) if self.statement.get('where') else None

    def signature_params(self):
        """The parameters of the @Query method (@Param = the JPQL name)."""
        return ', '.join(f'@Param("{java}") {typ} {java}' for source, java, typ in self.params)

    def plain_params(self):
        """The same parameters without annotations: a default method's, and the prompt's signature."""
        return ', '.join(f'{typ} {java}' for source, java, typ in self.params)

    def arguments(self):
        return ', '.join(java for source, java, typ in self.params)


def single(returns: str, method: str, builder: 'Builder') -> list[str]:
    """SELECT INTO: the rows of the @Query method <method>Rows, one row as PL/SQL wants it."""
    return ['', f'    default {returns} {method}({builder.plain_params()}) {{',
            f'        List<{returns}> rows = {method}Rows({builder.arguments()});',
            '        if (rows.isEmpty()) {',
            '            throw new NoResultException("NO_DATA_FOUND");',
            '        }',
            '        if (rows.size() > 1) {',
            '            throw new NonUniqueResultException("TOO_MANY_ROWS");',
            '        }',
            '        return rows.get(0);',
            '    }']


def by(fields: list[str]) -> str:
    if not fields:
        return ''
    shown = fields[:3]
    return 'By' + 'And'.join(f[:1].upper() + f[1:] for f in shown) + ('Etc' if len(fields) > 3 else '')


def literal(text: str, indent: str = '            ') -> str:
    """A JPQL text as a Java string, in pieces of at most WIDTH characters (+ on the next lines)."""
    pieces, rest = [], text
    while len(rest) > WIDTH:
        cut = rest.rfind(' ', 0, WIDTH)
        cut = cut if cut > 20 else WIDTH
        pieces.append(rest[:cut + 1])
        rest = rest[cut + 1:]
    pieces.append(rest)
    return ('\n' + indent + '+ ').join(jstr(piece) for piece in pieces)


def query(jpql: str, modifying: bool = False) -> list[str]:
    """The annotation lines of a JPQL method (an UPDATE / DELETE flushes before and clears after, so the entities this
    transaction holds stay true to the database)."""
    lines = ['    @Modifying(flushAutomatically = true, clearAutomatically = true)'] if modifying else []
    return lines + [f'    @Query({literal(jpql)})']


def select_method(builder: Builder, found: Found) -> tuple[str, str, list[str], str]:
    """(name, return type, member lines, NOTES kind) of a SELECT / cursor / loop query."""
    statement = builder.statement
    entity = builder.entity
    where = builder.where()
    items = statement['items']
    constant = all(i['op'] == 'literal' for i in items)
    listing = found.kind in {'cursor', 'loop'}
    order = []
    for column, direction in statement['order']:
        field = builder.column_field(column) if column['op'] == 'name' else None
        if field is None:
            raise Refused('ORDER BY nem oszlopon')
        order.append(f'd.{field} {"DESC" if direction == "DESC" else "ASC"}')
    tail = (' WHERE ' + where if where else '') + (' ORDER BY ' + ', '.join(order) if order else '')
    params = builder.signature_params()
    if listing and constant:
        # SELECT 1 FROM ... in a cursor: is there a row (cursor%FOUND)?
        name_ = 'exists' + (by(builder.where_fields) or 'Any')
        lines = query(f'SELECT CASE WHEN COUNT(d) > 0 THEN true ELSE false END FROM {entity} d' + (' WHERE ' + where if where else ''))
        return name_, 'boolean', lines + [f'    boolean {name_}({params});'], 'exists'
    if listing:
        if not all(i['op'] in {'star', 'name'} and (i['op'] == 'star' or builder.column_field(i) is not None) for i in items):
            raise Refused('a kurzor kifejezést is lekérdez (csak oszlopok támogatottak)')
        name_ = 'findAll' + by(builder.where_fields)
        return name_, f'List<{entity}>', query(f'SELECT d FROM {entity} d' + tail) + [f'    List<{entity}> {name_}({params});'], 'list'
    if not statement['into']:
        raise Refused('SELECT INTO nélkül')
    if len(items) == 1 and items[0]['op'] == 'aggregate':
        aggregate = items[0]
        function, value = aggregate['function'], aggregate['value']
        if function == 'COUNT':
            target = 'COUNT(d)' if value['op'] == 'star' else None
            if target is None:
                field = builder.column_field(value)
                if field is None:
                    raise Refused('COUNT nem oszlopon')
                target = f'COUNT(d.{field})'
            name_ = 'count' + (by(builder.where_fields) or 'All')
            lines = query(f'SELECT {target} FROM {entity} d' + (' WHERE ' + where if where else ''))
            lines += [f'    long {name_}Value({params});', '',
                      f'    default BigDecimal {name_}({builder.plain_params()}) {{',
                      f'        return BigDecimal.valueOf({name_}Value({builder.arguments()}));', '    }']
            return name_, 'BigDecimal', lines, 'count'
        field = builder.column_field(value)
        if field is None:
            raise Refused(function + ' nem oszlopon')
        typ = builder.types[field]
        if function == 'SUM' and typ != 'BigDecimal':
            raise Refused('SUM nem NUMBER oszlopon')
        if typ not in COMPARABLE:
            raise Refused(function + ': nem rendezhető típus')
        name_ = function.lower() + pascal_field(field) + by(builder.where_fields)
        lines = query(f'SELECT {function}(d.{field}) FROM {entity} d' + (' WHERE ' + where if where else ''))
        return name_, typ, lines + [f'    {typ} {name_}({params});'], 'aggregate'
    if any(i['op'] not in {'name', 'star'} for i in items) or any(i['op'] == 'name' and builder.column_field(i) is None for i in items):
        raise Refused('a lekérdezés kifejezést is ad vissza (csak oszlopok, egy aggregátum vagy NEXTVAL támogatott)')
    single_column = len(items) == 1 and items[0]['op'] == 'name'
    if single_column:
        field = builder.column_field(items[0])
        typ = builder.types[field]
        name_ = 'find' + pascal_field(field) + by(builder.where_fields)
        lines = query(f'SELECT d.{field} FROM {entity} d' + tail) + [f'    List<{typ}> {name_}Rows({params});']
        return name_, typ, lines + single(typ, name_, builder), 'value'
    name_ = 'find' + (by(builder.where_fields) or entity_short(builder))
    lines = query(f'SELECT d FROM {entity} d' + tail) + [f'    List<{entity}> {name_}Rows({params});']
    return name_, entity, lines + single(entity, name_, builder), 'entity'


def pascal_field(field: str) -> str:
    return field[:1].upper() + field[1:]


def entity_short(builder: Builder) -> str:
    return pascal(builder.table.rsplit('.', 1)[-1])


def write_method(builder: Builder, found: Found, sequences: dict) -> tuple[str, str, list[str], str, str]:
    """(name, return type, member lines, NOTES kind, parameters) of an INSERT / UPDATE / DELETE."""
    statement, entity = builder.statement, builder.entity
    if statement['op'] == 'insert':
        fields = []
        for column in statement['columns']:
            field = builder.column_field(column) if column['op'] == 'name' else None
            if field is None:
                raise Refused('az INSERT oszloplistájában nem oszlop áll')
            fields.append(field)
        missing = sorted(set(builder.columns.values()) - set(fields))
        if missing or builder.unmapped:
            raise Refused('az INSERT nem ad értéket minden oszlopnak (' + ', '.join(missing or sorted(builder.unmapped))
                          + '): az Oracle a kihagyottakba az alapértéküket írná, a Domain nem ismeri őket')
        if len(set(fields)) != len(fields):
            raise Refused('az INSERT egy oszlopot többször ír')
        sets = []
        for field, value in zip(fields, statement['values']):
            typ = builder.types[field]
            setter = 'entity.set' + pascal_field(field)
            if value['op'] == 'literal' and value['type'] == 'null':
                continue  # a new entity's field is null already
            if value['op'] == 'nextval':
                if typ != 'BigDecimal':
                    raise Refused('szekvencia nem NUMBER oszlopba')
                sets.append(f'        {setter}({sequences.setdefault(value["sequence"], sequence_name(value["sequence"]))}());')
                continue
            sets.append(f'        {setter}({plain_value(builder, value, typ, field)});')
        params = builder.plain_params()
        key = builder.d.get('primary_key') or []
        key_args = ', '.join('entity.get' + pascal_field(k) + '()' for k in key)
        exists = (key_args if len(key) == 1 else f'new {entity}.Key({key_args})') if key else ''
        name_ = 'insert' + entity_short(builder)
        lines = [f'    default void {name_}({params}) {{']
        if builder.uses_now:
            lines.append('        LocalDateTime now = LocalDateTime.now().withNano(0);  // Oracle SYSDATE: másodpercre')
        lines += [f'        {entity} entity = new {entity}();'] + sets
        if exists:
            # save() of an existing key would update that row: DUP_VAL_ON_INDEX, like the INSERT
            lines += [f'        if (existsById({exists})) {{', '            throw new EntityExistsException("DUP_VAL_ON_INDEX");', '        }']
        lines += ['        saveAndFlush(entity);', '    }']
        return name_, 'void', lines, 'insert', params
    if statement['op'] == 'update':
        assignments, set_fields = [], []
        for column, value in statement['sets']:
            field = builder.column_field(column) if column['op'] == 'name' else None
            if field is None:
                raise Refused('az UPDATE SET bal oldalán nem oszlop áll')
            typ = builder.types[field]
            set_fields.append(field)
            if value['op'] == 'literal' and value['type'] == 'null':
                assignments.append(f'd.{field} = NULL')
                continue
            if value['op'] == 'nextval':
                raise Refused('szekvencia az UPDATE-ben')
            expression, vt = builder.expression(value, typ, field)
            if vt != typ:
                raise Refused('eltérő típusú érték (' + vt + ' → ' + typ + ')')
            assignments.append(f'd.{field} = {expression}')
        where = builder.where()
        name_ = 'update' + 'And'.join(pascal_field(f) for f in set_fields[:3]) + ('Etc' if len(set_fields) > 3 else '') + by(builder.where_fields)
        lines = query(f'UPDATE {entity} d SET ' + ', '.join(assignments) + (' WHERE ' + where if where else ''), modifying=True)
        return name_, 'int', lines + [f'    int {name_}({builder.signature_params()});'], 'update', builder.plain_params()
    where = builder.where()
    name_ = 'delete' + (by(builder.where_fields) or 'All')
    lines = query(f'DELETE FROM {entity} d' + (' WHERE ' + where if where else ''), modifying=True)
    return name_, 'int', lines + [f'    int {name_}({builder.signature_params()});'], 'delete', builder.plain_params()


def plain_value(builder: Builder, node: dict, typ: str, field: str) -> str:
    """A value set on a new entity: a parameter of the method, a literal, SYSDATE."""
    op = node['op']
    if op in {'input', 'name'}:
        if op == 'name' and builder.column_field(node) is not None:
            raise Refused('oszlop az INSERT értékei között')
        source = node['source'] if op == 'input' else node['name']
        for known in builder.params:
            if known[0] == source:
                if known[2] != typ:
                    raise Refused(source + ' két különböző típusú oszlopba kerül')
                return known[1]
        java = camel(field)
        while java in {p[1] for p in builder.params} or java in {'entity', 'now'}:
            java += '2'
        builder.params.append([source, java, typ])
        return java
    if op == 'literal':
        if node['type'] == 'text' and typ == 'String':
            return jstr(node['value'])
        if node['type'] == 'number' and typ == 'BigDecimal':
            return f'new BigDecimal({jstr(node["value"])})'
        raise Refused('implicit típuskonverzió (' + node['type'] + ' → ' + typ + ')')
    if op == 'sysdate':
        if typ != 'LocalDateTime':
            raise Refused('SYSDATE nem dátum oszlopba')
        builder.uses_now = True
        return 'now'
    raise Refused('az INSERT értéke kifejezés (csak változó, mező, literál, SYSDATE, NEXTVAL támogatott)')


def sequence_name(sequence: str) -> str:
    return 'next' + pascal(sequence.rsplit('.', 1)[-1])


def sequence_method(sequence: str) -> tuple[str, str]:
    """The sequence's next value: the one native query (jpa_crud writes the same method for a block's key)."""
    method = sequence_name(sequence)
    lines = comment(['Forms-SQL: SELECT ' + sequence + '.NEXTVAL FROM DUAL', NOTES['sequence'][0][:1].upper() + NOTES['sequence'][0][1:] + '.'])
    lines += [f'    @Query(value = {jstr("SELECT " + sequence + ".NEXTVAL FROM DUAL")}, nativeQuery = true)',
              f'    BigDecimal {method}();']
    return method, '\n'.join(lines)


# ---------------------------------------------------------------------- the task's methods

def descriptor_of(table: str, persistence: list[dict]) -> dict | None:
    table = table.upper()
    exact = [p for p in persistence if p['table'].upper() == table]
    if exact:
        return exact[0]
    bare = [p for p in persistence if p['table'].upper().rsplit('.', 1)[-1] == table.rsplit('.', 1)[-1]]
    return bare[0] if len(bare) == 1 else None


def translate(found: Found, persistence: list[dict], host: dict | None) -> list[tuple]:
    """[(descriptor, name, signature, method source, statement, kind, arguments, columns)] of one statement (the first
    is the statement's own method, a sequence it uses may follow); Refused when outside the subset."""
    statement_text = one_line(found.text)
    if not found.tokens:
        raise Refused('üres utasítás')
    if found.tokens[0][1].upper() == 'SELECT':
        try:
            tree = Parser(found.tokens).select()
        except Refused:
            tree = None
        if tree and tree['table'].rsplit('.', 1)[-1] == 'DUAL':
            # SELECT seq.NEXTVAL INTO x FROM DUAL: the sequence value, in the repository of the code's table.
            if len(tree['items']) == 1 and tree['items'][0]['op'] == 'nextval' and tree['into'] and tree['where'] is None:
                if host is None:
                    raise Refused('a szekvenciához nincs repository (a kód nem használ táblát)')
                method, source = sequence_method(tree['items'][0]['sequence'])
                return [(host, method, f'BigDecimal {method}()', source, statement_text, 'sequence', (), ())]
            raise Refused(DUAL)
    tree = parse(found)
    descriptor = descriptor_of(tree['table'], persistence)
    if descriptor is None:
        raise Refused('a(z) ' + tree['table'] + ' táblához nincs entity (táblaszerkezet)')
    builder = Builder(descriptor, tree)
    sequences = {}
    if tree['op'] == 'select':
        method, returns, lines, kind = select_method(builder, found)
    else:
        method, returns, lines, kind, _ = write_method(builder, found, sequences)
    note = NOTES[kind][0]
    arguments = tuple(source for source, _, _ in builder.params)
    columns = (tuple((field, builder.types[field]) for field in (builder.column_field(i) for i in tree['items']))
               if kind == 'entity' and tree['op'] == 'select' and all(i['op'] == 'name' for i in tree['items']) else ())
    head = comment(['Forms-SQL: ' + statement_text, note[:1].upper() + note[1:] + '.'])
    source = '\n'.join(head + lines)
    result = [(descriptor, method, f'{returns} {method}({builder.plain_params()})', source, statement_text, kind, arguments, columns)]
    for sequence in sequences:
        name_, text = sequence_method(sequence)
        result.append((descriptor, name_, f'BigDecimal {name_}()', text, 'SELECT ' + sequence + '.NEXTVAL FROM DUAL', 'sequence',
                       (), ()))
    return result


# SELECT ... FROM DUAL without a sequence: Java logic (SYSDATE, a computed value), no repository method.
DUAL = 'FROM DUAL: nem adatbázis-tábla, a Java-kód része'


def code_texts(packet: dict) -> list[str]:
    """The PL/SQL of the task: the trigger(s) and the form's own units it calls (a package: the called members)."""
    sources = [s for s in packet.get('sources') or [] if isinstance(s, dict)]
    called = {c['name'].upper() for s in sources for c in s.get('calls', []) if isinstance(c, dict) and c.get('name')}
    texts = []
    for s in sources:
        text = decode_line_escapes(s.get('source') or '')
        if s.get('kind') == 'programunit' and re.match(r'\s*PACKAGE\b', text, re.I):
            members = [c.split('.', 1)[1] for c in called if c.split('.', 1)[0] == str(s.get('name', '')).upper() and '.' in c]
            texts += [m for m in (package_member(text, member) for member in members) if m]
            continue
        texts.append(text)
    return texts


def package_member(text: str, member: str) -> str | None:
    """PROCEDURE/FUNCTION member ... END member; of a package body, or None."""
    match = re.search(r'\b(?:PROCEDURE|FUNCTION)\s+' + re.escape(member) + r'\b(?:(?!;).)*?\b(?:IS|AS)\b', text, re.I | re.S)
    if not match:
        return None
    end = re.search(r'\bEND\s+' + re.escape(member) + r'\s*;', text[match.end():], re.I)
    return text[match.start():match.end() + end.end()] if end else None


def attach(packet: dict, persistence: list[dict], own_table: str | None = None) -> dict:
    """The generated methods of the task's SQL, added to the repositories (descriptor existing_repository / files).

    Returns {'methods': [Method], 'refused': [{'statement', 'reason'}], 'calls': {statement key: Method}} (the key:
    statement_key of the statement text). A statement that repeats an earlier one reuses its method; a method already
    in the repository file the same way is reused, a name taken by another method gets a number."""
    # A sequence (SELECT seq.NEXTVAL FROM DUAL) has no table: the code's own table hosts it, else the first one.
    host = (descriptor_of(own_table, persistence) if own_table else None) or (persistence[0] if persistence else None)
    methods, refused, by_statement, calls = [], [], {}, {}
    for text in code_texts(packet):
        for found in find(text):
            key = statement_key(found.text)
            if key in by_statement:
                continue
            try:
                parts = translate(found, persistence, host)
            except Refused as exc:
                if str(exc) != DUAL:
                    refused.append({'statement': one_line(found.text), 'reason': str(exc)})
                by_statement[key] = None
                continue
            names = []
            for descriptor, method, signature, source, statement, kind, arguments, columns in parts:
                final, new = place(descriptor, method, source)
                if final != method:
                    source = renamed(source, method, final)
                    signature = signature.replace(' ' + method + '(', ' ' + final + '(')
                if new:
                    repository_path = 'backend/DPS/' + descriptor['repository'] + '.java'
                    for holder in (descriptor, descriptor['files']):
                        key_ = 'existing_repository' if holder is descriptor else repository_path
                        current = holder[key_]
                        at = current.rstrip().rfind('}')
                        updated = current[:at].rstrip('\n') + '\n\n' + source + '\n' + current[at:]
                        holder[key_] = updated
                methods.append(Method(descriptor['repository'], descriptor['variable'], final, signature, source, statement, kind,
                                      arguments, columns))
                names.append(final)
                calls.setdefault(key, methods[-1])  # the statement's own method (a sequence it uses comes after it)
            by_statement[key] = names
    for descriptor in persistence:
        if any(m.repository == descriptor['repository'] for m in methods):
            path = 'backend/DPS/' + descriptor['repository'] + '.java'
            descriptor['existing_repository'] = with_imports(descriptor['existing_repository'], descriptor['namespace'])
            descriptor['files'][path] = descriptor['existing_repository']
    unique, seen = [], set()
    for method in methods:
        if (method.repository, method.name) not in seen:
            seen.add((method.repository, method.name))
            unique.append(method)
    return {'methods': unique, 'refused': refused, 'calls': calls}


def statement_key(text: str) -> str:
    """A statement's text as attach() keys it (a trigger parser's SELECT text finds its method by it)."""
    return one_line(text).upper()


def renamed(source: str, method: str, final: str) -> str:
    """A generated method under another name: its helper members too (<name>Rows, <name>Value)."""
    return re.sub(r'\b' + re.escape(method) + r'(Rows|Value)?\(', lambda m: final + (m.group(1) or '') + '(', source)


def members(source: str) -> dict[str, list[str]]:
    """Name -> the member texts (whitespace-normalized, comments left out) of a repository or of a generated method."""
    from .java_scan import methods_by_name
    return methods_by_name(re.sub(r'/\*[\s\S]*?\*/|//[^\n]*', '', source))


def place(descriptor: dict, method: str, source: str) -> tuple[str, bool]:
    """The method's name in the repository and whether it is new: the same method already there is reused."""
    existing = members(descriptor['existing_repository'])
    candidate, counter = method, 2
    while True:
        own = members(renamed(source, method, candidate))
        if all(name in existing and texts[0] in existing[name] for name, texts in own.items()):
            return candidate, False
        if not set(own) & set(existing):
            return candidate, True
        candidate, counter = method + str(counter), counter + 1


# The JDK and Spring Data names of the generated methods (the JPA ones: jpa_assistant.persistence_imports).
JDK_IMPORTS = {'BigDecimal': 'java.math.BigDecimal', 'LocalDateTime': 'java.time.LocalDateTime',
               'OffsetDateTime': 'java.time.OffsetDateTime', 'List': 'java.util.List', 'Optional': 'java.util.Optional',
               'Query': 'org.springframework.data.jpa.repository.Query',
               'Modifying': 'org.springframework.data.jpa.repository.Modifying',
               'Param': 'org.springframework.data.repository.query.Param'}


def with_imports(source: str, namespace: str) -> str:
    """The imports of what the repository's methods use (javax or jakarta persistence, as the project's)."""
    from .backend_assistant import ensure_imports
    from .jpa_assistant import persistence_imports
    return ensure_imports(source, {**JDK_IMPORTS, **persistence_imports(namespace)})


def prompt_lines(generated: dict) -> list[str]:
    """The prompt's lines of the generated methods (assistant_context.build)."""
    lines = []
    for method in generated['methods']:
        returns, call = method.signature.split(' ', 1)
        lines += [f'- {method.variable}.{call} returns {returns}', f'  SQL: {method.statement}',
                  f'  result: {NOTES[method.kind][1]}']
    return lines

"""Closed SQL expression compiler for Forms block queries; no raw SQL passthrough.

Only known columns, typed Forms-item binds and a small pure SQL subset are emitted.
Unknown runtime contexts, SQL functions, subqueries and partial parses stay blocked.
"""
from __future__ import annotations

from .common import unique_names
from .forms_context import NORMAL_MODE, normal_mode_expression
from .plsql import Parser, Token, Unsupported, tokenize
from .xmlmodel import get


class QueryParser(Parser):
    def __init__(self, source):
        super().__init__(source)
        # Fold static qualified identifiers from tokens (never inside literals).
        folded, i = [], 0
        while i < len(self.tokens):
            t = self.tokens[i]
            if t.kind == 'id':
                parts = [t.value]
                while i + 2 < len(self.tokens) and self.tokens[i+1].value == '.' and self.tokens[i+2].kind == 'id':
                    parts.append(self.tokens[i+2].value); i += 2
                t = Token('id', '.'.join(parts), t.pos)
            folded.append(t); i += 1
        self.tokens = folded

    def expression(self, minimum=0):
        # The base expression grammar is shared with PL/SQL; special predicates
        # are handled here without expanding the executable PL/SQL subset.
        t = self.take()
        if t.value in {'NOT', '+', '-'}:
            left = {'op': 'unary', 'operator': t.value, 'value': self.expression(25 if t.value == 'NOT' else 60)}
        elif t.value == '(':
            left = self.expression(); self.need(')')
        elif t.kind == 'bind':
            left = {'op': 'ref', 'name': t.value[1:]}
        elif t.kind == 'string':
            left = {'op': 'literal', 'type': 'text', 'value': t.value[1:-1].replace("''", "'") or None}
        elif t.kind == 'number':
            left = {'op': 'literal', 'type': 'number', 'value': t.value}
        elif t.value == 'NULL':
            left = {'op': 'literal', 'type': 'null', 'value': None}
        elif t.kind == 'id':
            if self.accept('('):
                args = []
                if not self.peek(')'):
                    args.append(self.expression())
                    while self.accept(','): args.append(self.expression())
                self.need(')'); left = {'op': 'function', 'name': t.value, 'args': args}
            else: left = {'op': 'symbol', 'name': t.value}
        else: raise Unsupported('Nem támogatott SQL-kifejezés: '+t.value)
        precedence = {'OR':10, 'AND':20, '=':30, '<>':30, '!=':30, '<':30, '>':30, '<=':30, '>=':30,
                      'IS':30, 'LIKE':30, 'IN':30, 'BETWEEN':30, 'NOT':30, '||':40, '+':40, '-':40, '*':50, '/':50}
        while precedence.get(self.tokens[self.i].value, -1) >= minimum:
            op = self.take().value
            negated = op == 'NOT'
            if negated:
                op = self.take().value
                if op not in {'LIKE', 'IN', 'BETWEEN'}: raise Unsupported('SQL NOT után LIKE/IN/BETWEEN szükséges.')
            if op == 'IS':
                negated = self.accept('NOT'); self.need('NULL')
                left = {'op':'is_null', 'value':left, 'negated':negated}
            elif op == 'IN':
                self.need('('); args = [self.expression(31)]
                while self.accept(','): args.append(self.expression(31))
                self.need(')'); left = {'op':'in', 'value':left, 'args':args, 'negated':negated}
            elif op == 'BETWEEN':
                lo = self.expression(31); self.need('AND'); hi = self.expression(31)
                left = {'op':'between', 'value':left, 'low':lo, 'high':hi, 'negated':negated}
            else:
                right = self.expression(precedence[op]+1)
                left = {'op':'binary', 'operator':op, 'left':left, 'right':right}
                if negated: left = {'op':'unary', 'operator':'NOT', 'value':left}
        return left


class QueryCompiler:
    def __init__(self, model, block):
        self.block = block
        self.binds = {}
        self.fields = {b['name']+'.'+i['name']: i for b in model['blocks'] for i in b['items'] if i['kind'] != 'button'}
        self.columns = {i['column']: i for i in block['db_items']}
        self.qualifiers = {block['table'], block['table'].rsplit('.', 1)[-1]}
        alias = get(block['properties'], 'Alias').upper().strip()
        if alias: self.qualifiers.add(alias)

    @staticmethod
    def compatible(*types):
        return len(set(types)-{'null'}) <= 1

    def expression(self, n):
        if normal_mode_expression(n):
            return "'" + NORMAL_MODE + "'", 'text'
        op = n['op']
        if op == 'literal':
            if n['value'] is None: return 'NULL', 'null'
            if n['type'] == 'text': return "'"+n['value'].replace("'", "''")+"'", 'text'
            return n['value'], n['type']
        if op == 'symbol':
            parts = n['name'].rsplit('.', 1)
            if len(parts) == 2 and parts[0] not in self.qualifiers:
                raise Unsupported('Ismeretlen SQL-minősítő: '+parts[0])
            col = parts[-1]
            if col not in self.columns: raise Unsupported('Nem leképezett SQL-oszlop: '+n['name'])
            return col, self.columns[col]['type']
        if op == 'ref':
            ref = n['name']
            if ref.split('.')[0] in {'SYSTEM', 'GLOBAL', 'PARAMETER'} or ref not in self.fields:
                raise Unsupported('Nem igazolt vagy szervercontextet igénylő bind: :'+ref)
            field = self.fields[ref]
            if field['type'] not in {'text', 'number', 'datetime'}:
                raise Unsupported('Nem támogatott bindtípus: '+ref)
            if ref not in self.binds: self.binds[ref] = {'source':ref, 'parameter':'q'+str(len(self.binds)), 'item':field}
            return ':'+self.binds[ref]['parameter'], field['type']
        if op == 'is_null':
            val, _ = self.expression(n['value'])
            return '('+val+(' IS NOT NULL)' if n['negated'] else ' IS NULL)'), 'boolean'
        if op == 'unary':
            val, typ = self.expression(n['value']); operator = n['operator']
            if operator == 'NOT' and typ == 'boolean': return '(NOT '+val+')', 'boolean'
            if operator in {'+', '-'} and typ in {'number','null'}: return '('+operator+val+')', 'number'
        if op in {'in','between'}:
            value, typ = self.expression(n['value'])
            args = [self.expression(a) for a in (n['args'] if op == 'in' else [n['low'],n['high']])]
            if not self.compatible(typ, *(t for _,t in args)) or typ == 'boolean':
                raise Unsupported('Implicit SQL-típuskonverzió a tartomány/listafeltételben.')
            tail = '('+', '.join(s for s,_ in args)+')' if op == 'in' else ' AND '.join(s for s,_ in args)
            return '('+value+(' NOT ' if n['negated'] else ' ')+op.upper()+' '+tail+')', 'boolean'
        if op == 'binary':
            left, lt = self.expression(n['left']); right, rt = self.expression(n['right']); operator = n['operator']
            typ = None
            if operator in {'AND','OR'} and lt == rt == 'boolean': typ = 'boolean'
            elif operator in {'=','<>','!=','<','>','<=','>='} and self.compatible(lt,rt) and 'boolean' not in {lt,rt}: typ='boolean'
            elif operator == 'LIKE' and {lt,rt} <= {'text','null'}: typ='boolean'
            elif operator in {'+','-','*','/'} and {lt,rt} <= {'number','null'}: typ='number'
            elif operator == '||' and {lt,rt} <= {'text','null'}: typ='text'
            if typ: return '('+left+' '+operator+' '+right+')', typ
        if op == 'function':
            args = [self.expression(a) for a in n['args']]; fun = n['name']; typ = None
            if fun == 'NVL' and len(args)==2 and self.compatible(*(t for _,t in args)):
                typ = next((t for _,t in args if t!='null'),'null')
            elif fun in {'UPPER','LOWER','TRIM','LENGTH'} and len(args)==1 and args[0][1] in {'text','null'}:
                typ = 'number' if fun=='LENGTH' else 'text'
            elif fun == 'ABS' and len(args)==1 and args[0][1] in {'number','null'}: typ='number'
            if typ and typ != 'boolean': return fun+'('+', '.join(s for s,_ in args)+')',typ
        raise Unsupported('Nem támogatott SQL-szemantika: '+str(n.get('name',op)))

    def predicate(self, source):
        if not source.strip(): return ''
        parser = QueryParser(source)
        node = parser.expression(); parser.need('<EOF>')
        sql, typ = self.expression(node)
        if typ != 'boolean': raise Unsupported('A WHERE nem logikai feltétel.')
        return sql

    def order(self, source):
        if not source.strip(): return ''
        parser = QueryParser(source); result = []
        while True:
            token = parser.take()
            if token.kind != 'id': raise Unsupported('ORDER BY: csak ismert oszlop engedélyezett.')
            column, _ = self.expression({'op':'symbol','name':token.value})
            if parser.peek('ASC') or parser.peek('DESC'): column += ' '+parser.take().value
            if parser.accept('NULLS'):
                if not (parser.peek('FIRST') or parser.peek('LAST')): raise Unsupported('NULLS FIRST/LAST szükséges.')
                column += ' NULLS '+parser.take().value
            result.append(column)
            if not parser.accept(','): break
        parser.need('<EOF>')
        return ', '.join(result)


def clause_body(source, *keywords):
    """The clause without its leading keyword(s): Designer writes 'WHERE (...)' and 'ORDER BY x' into
    WhereClause/OrderByClause, Forms accepts both forms. Comments become a space (SQL is appended after)."""
    from .common import decode_line_escapes
    from .plsql_passthrough import scan
    text = decode_line_escapes(source or '')
    tokens = [t for t in scan(text)]
    sig = [t for t in tokens if t[0] not in {'ws', 'comment'}]
    if keywords and len(sig) >= len(keywords) and [t[1].upper() for t in sig[:len(keywords)]] == list(keywords):
        start = sig[len(keywords) - 1][3]
        tokens = [t for t in tokens if t[2] >= start]
    return ''.join(' ' if t[0] == 'comment' else t[1] for t in tokens).strip()


class RawClause:
    """The Forms WHERE / ORDER BY text as Oracle runs it, with the Forms binds as typed JDBC parameters.

    Forms appends the clause unchanged to SELECT ... FROM table, so the original SQL is the
    specification: subqueries, unmapped columns, SQL functions and SYSDATE all stay. Only what does
    not exist in the database is replaced: :BLOCK.ITEM, :GLOBAL.X and :PARAMETER.X become named
    parameters of the search request; SYSTEM.MODE is NORMAL; other :SYSTEM values are refused.
    The text comes from the reviewed form, never from a request, so no request value reaches SQL text.
    """

    def __init__(self, model, block, binds):
        self.block = block
        self.fields = {b['name'] + '.' + i['name']: i for b in model['blocks'] for i in b['items'] if i['kind'] != 'button'}
        self.binds = binds  # shared with the compiled part: source -> bind

    def bind(self, token):
        ref = token[1:].upper()
        head = ref.split('.')[0]
        if '.' not in ref:
            ref = self.block['name'] + '.' + ref
            if ref not in self.fields:
                raise Unsupported('Blokk nélküli, nem egyértelmű bind a feltételben: ' + token)
        if head == 'SYSTEM':
            raise Unsupported('Forms rendszerváltozó a feltételben (' + token + '): a kérésben nincs megfelelője.')
        if head in {'GLOBAL', 'PARAMETER'}:
            item = {'name': ref, 'kind': 'text', 'type': 'text', 'required': False, 'max_length': None,
                    'context': head.lower()}
        else:
            if ref not in self.fields:
                raise Unsupported('Ismeretlen mező a feltételben: ' + token)
            item = self.fields[ref]
            if item['type'] not in {'text', 'number', 'datetime'}:
                raise Unsupported('Nem támogatott bindtípus: ' + ref)
        if ref not in self.binds:
            self.binds[ref] = {'source': ref, 'parameter': 'q' + str(len(self.binds)), 'item': item}
        return ':' + self.binds[ref]['parameter']

    def sql(self, source, *keywords):
        from .plsql_passthrough import normal_mode_sql, scan
        text = clause_body(source, *keywords)
        if not text:
            return ''
        out, depth = [], 0
        for kind, value, *_ in scan(normal_mode_sql(text)):
            if kind in {'ws', 'comment'}:
                if out and out[-1] != ' ':
                    out.append(' ')  # one space; string literals are single tokens and stay intact
                continue
            if kind == 'bind':
                out.append(self.bind(value))
                continue
            if kind == 'op' and value == ';':
                raise Unsupported('Pontosvessző a feltételben: nem egyetlen SQL-feltétel.')
            if kind == 'op' and value in {'(', ')'}:
                depth += 1 if value == '(' else -1
                if depth < 0:
                    raise Unsupported('Kiegyensúlyozatlan zárójelek a feltételben.')
            out.append(value)
        if depth:
            raise Unsupported('Kiegyensúlyozatlan zárójelek a feltételben.')
        return ''.join(out).strip()


def prepare_queries(model):
    """Upgrade only understood query blockers. Filtered writes remain guarded."""
    for b in model['blocks']:
        if not b['database']: continue
        where = clause_body(get(b['properties'], 'WhereClause', 'DefaultWhere'), 'WHERE')
        relation_where = b.get('relation_where','')
        effective_where = '('+where+') AND ('+relation_where+')' if where.strip() and relation_where else where or relation_where
        order = clause_body(get(b['properties'], 'OrderByClause'), 'ORDER', 'BY')
        if not effective_where.strip() and not order.strip(): continue
        plan = {'status':'review', 'where_source':where, 'relation_where':relation_where, 'order_source':order, 'binds':[], 'where_sql':'', 'order_sql':''}
        b['query_plan'] = plan
        try:
            if any(i['owner']==b['name'] and i['code'] in {'SQL_MAPPING','NO_COLUMNS','QUERY_SOURCE'} for i in model['issues']):
                raise Unsupported('A lekérdezési adatforrás/oszlopok leképezése nem igazolt.')
            compiler = QueryCompiler(model,b)
            try:
                where_sql = compiler.predicate(effective_where); order_sql = compiler.order(order)
                plan['translation'] = 'compiled'
            except (Unsupported, RecursionError, IndexError) as closed:
                # Not in the closed SQL subset: the original clause runs in Oracle, as in Forms.
                compiler.binds = {}
                raw = RawClause(model, b, compiler.binds)
                where_sql = raw.sql(effective_where)
                order_sql = raw.sql(order)
                plan['translation'] = 'oracle'
                plan['closed_reason'] = str(closed) or 'Túl mély vagy hibás SQL.'
            binds = list(compiler.binds.values())
            names = unique_names([v['source'] for v in binds])
            for bind in binds:
                bind['field'] = names[bind['source']]
                # A search criterion's nullability is controlled by the SQL,
                # not by Required on a data-entry item.
                bind['item'] = {**bind['item'], 'field':bind['field'], 'required':False}
            plan.update(status='compiled', where_sql=where_sql, order_sql=order_sql, binds=binds)
            for issue in model['issues']:
                if issue['owner'] != b['name']: continue
                if issue['code'] == 'QUERY_FILTER':
                    # backend_live: writes go by primary key like in Forms; the row filter is a review note.
                    issue['scope']='review' if model.get('options', {}).get('backend_live') else 'write'
                    issue['detail']=('A WHERE olvasási SQL-re lefordítva' if plan['translation'] == 'compiled' else
                                     'Az eredeti WHERE az Oracle-ben fut (a Forms-bindek típusos paraméterek)') + \
                                    '; írás előtt a rekordszűrést és szervercontextet külön ellenőrizni kell.'
                elif issue['code'] == 'ORDER_BY':
                    issue['scope']='review'
                    issue['detail']=('Az eredeti ORDER BY ismert oszlopokkal átültetve: ' if plan['translation'] == 'compiled' else
                                     'Az eredeti ORDER BY az Oracle-ben fut: ')+order_sql
            if binds:
                # Only the parameterless list is affected; writes are guarded by QUERY_FILTER.
                model['issues'].append({'code':'QUERY_BIND_REQUIRED','owner':b['name'],'scope':'read',
                    'detail':'A paraméter nélküli lista tiltott. A típusos search'+b['class']+' metódusnak add át: '+', '.join(v['source'] for v in binds)})
        except (Unsupported, RecursionError, IndexError) as exc:
            plan['reason']=str(exc) or 'Túl mély vagy hibás SQL.'
            if relation_where:
                model['issues'].append({'code':'RELATION_QUERY','owner':b['name'],'scope':'all','detail':plan['reason']})


def prepare_relations(model):
    """Only unambiguous acyclic equality joins become read criteria; DML stays closed."""
    blocks = {b['name']:b for b in model['blocks']}
    relations = model.get('relation_contexts',[])
    candidates = {}; incoming={}; graph={}
    for relation in relations:
        try:
            master=blocks.get(relation['master']); detail=blocks.get(relation['detail'])
            if not master or not detail or not master['database'] or not detail['database'] or master is detail:
                raise Unsupported('Nem egyértelmű adatbázisos master/detail blokkok.')
            if relation['parent_block'] and relation['parent_block'] != master['name']:
                raise Unsupported('A deklarált master eltér a szülő blokktól.')
            if get(relation['properties'],'RelationType').lower() not in {'','join'}:
                raise Unsupported('Csak Join típusú reláció képezhető le.')
            incoming[detail['name']]=incoming.get(detail['name'],0)+1
            graph.setdefault(master['name'],set()).add(detail['name'])
            parser=QueryParser(relation['join']); ast=parser.expression(); parser.need('<EOF>')
            def side(node, block):
                if node['op']!='symbol' or '.' not in node['name']: return None
                qualifier,col=node['name'].rsplit('.',1)
                qualifiers={block['name'],block['table'],block['table'].rsplit('.',1)[-1],get(block['properties'],'Alias').upper()}
                if qualifier not in qualifiers: return None
                found=[i for i in block['db_items'] if col==i['column']]
                return found[0] if len(found)==1 else None
            def pairs(node):
                if node['op']=='binary' and node['operator']=='AND': return pairs(node['left'])+pairs(node['right'])
                if node['op']!='binary' or node['operator']!='=': raise Unsupported('Csak AND-del összekötött egyenlőségrelációk támogatottak.')
                a,b=node['left'],node['right']
                options=[]
                for left,right in [(a,b),(b,a)]:
                    m,d=side(left,master),side(right,detail)
                    if m and d and m['type']==d['type']: options.append((m,d))
                if len(options)!=1: raise Unsupported('A reláció oszlopai/típusai nem azonosíthatók egyértelműen.')
                return options
            mapping=pairs(ast)
            where=' AND '.join(d['column']+' = :'+master['name']+'.'+m['name'] for m,d in mapping)
            relation['mapping']=[{'master_item':master['name']+'.'+m['name'],'detail_column':d['column']} for m,d in mapping]
            relation['status']='compiled'
            candidates[detail['name']]=where
        except (Unsupported, RecursionError, IndexError) as exc:
            relation['status']='review'; relation['reason']=str(exc) or 'Hibás reláció.'
    def cyclic(node, path=()):
        return node in path or len(path)>32 or any(cyclic(child,(*path,node)) for child in graph.get(node,()))
    bad_graph=any(count>1 for count in incoming.values()) or any(cyclic(node) for node in graph)
    if bad_graph:
        for r in relations: r.update(status='review',reason='Több masterhez kötött vagy ciklikus relációháló külön adaptert igényel.')
    if relations and all(r.get('status')=='compiled' for r in relations):
        for detail,where in candidates.items(): blocks[detail]['relation_where']=where
        # Relational reads now retain all equality keys. Transactional writes,
        # cascades, deferred coordination and master existence need host review.
        live = bool(model.get('options', {}).get('backend_live'))
        for issue in model['issues']:
            if issue['code']=='MASTER_DETAIL':
                # The relation's own triggers (ON-CHECK-DELETE-MASTER, cascading PRE-DELETE) run with the
                # generated delete; the detail key comes from the master (relation mapping) when saving.
                issue['scope']='review' if live else 'write'
                issue['detail']+=' Az egyenlőségkulcsos olvasás generálva; az összehangolt mentés/törlés ellenőrzendő.'


def query_capabilities(model, block):
    plan=block.get('query_plan',{})
    form_owner='@FORM:'+model['name']
    gate=model.get('module_gate',{})
    # Module-wide review requirements are the MODULE_REVIEWED gate, not search blockers.
    relevant=[i for i in model['issues'] if i['owner'] in {form_owner, block['name']}
              and i['scope'] in {'all','read'} and i['code']!='QUERY_BIND_REQUIRED'
              and not (i['owner']==form_owner and i['code'] in gate.get('codes',()))]
    specific=list(dict.fromkeys(i['detail'] for i in relevant))
    ready=bool(block['database'] and block['query_allowed'] and plan.get('status')=='compiled'
               and plan.get('binds') and not specific)
    block.setdefault('blockers',{})['search']=specific
    block['ready_search']=ready
    block['can_search']=ready and (not gate.get('required') or bool(model.get('options',{}).get('backend_live')))
    block['search_blockers']=list(gate.get('reasons',[]))+specific

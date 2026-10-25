"""Inline only fully parsed, parameterless local procedures with no declarations."""
from __future__ import annotations

from .common import decode_line_escapes, digest
from .plsql import parse, tokenize, Unsupported
from .xmlmodel import get, canonical


class LocalProcedures:
    def __init__(self, model):
        self.units = {}
        self.parsed = {}
        for unit in model['program_units']:
            key = get(unit, 'Name').upper()
            self.units.setdefault(key, []).append(unit)

    def body(self, key):
        if key in self.parsed: return self.parsed[key]
        units = self.units[key]
        if len(units) != 1: raise Unsupported('Nem egyértelmű helyi program unit: '+key)
        unit = units[0]
        kind = canonical(get(unit,'ProgramUnitType'))
        if kind not in {'','procedure'}: raise Unsupported('Csak helyi procedure illeszthető be: '+key)
        raw = get(unit,'ProgramUnitText')
        # Token positions index the decoded text; slice that, never the raw export.
        source = decode_line_escapes(raw)
        ts = tokenize(source)
        if len(ts)<7 or ts[0].value != 'PROCEDURE' or ts[1].kind != 'id' or ts[1].value != key:
            raise Unsupported('Nem teljes/egyező procedure deklaráció: '+key)
        at = 2
        if ts[at].value == '(':
            at += 1
            if ts[at].value != ')': raise Unsupported('Paraméteres procedure külön szerződést igényel: '+key)
            at += 1
        if ts[at].value not in {'IS','AS'} or ts[at+1].value != 'BEGIN':
            raise Unsupported('Deklarációs/állapotfüggő procedure: '+key)
        body = source[ts[at+1].pos:]
        if ts[-4].value == 'END' and ts[-3].value == key and ts[-2].value == ';':
            body = source[ts[at+1].pos:ts[-3].pos] + source[ts[-2].pos:]
        ast = parse(body)  # all statements including the ending must parse
        result = (ast, {'name':key, 'sha256':digest(raw), 'source':raw})
        self.parsed[key] = result
        return result

    def expand(self, ast):
        used = {}; count = 0
        def walk(nodes, stack=()):
            nonlocal count
            result=[]
            for original in nodes:
                count += 1
                if count > 1000 or len(stack)>16: raise Unsupported('Túl nagy/mély helyi procedure-híváslánc.')
                n=dict(original)
                if n['op']=='call' and n['name'] in self.units:
                    key=n['name']
                    if n['args']: raise Unsupported('Paraméteres helyi hívás: '+key)
                    if key in stack: raise Unsupported('Rekurzív helyi híváslánc: '+' -> '.join((*stack,key)))
                    body,evidence=self.body(key)
                    used[key]=evidence
                    result.extend(walk(body,(*stack,key)))
                    continue
                if n['op']=='block': n['body']=walk(n['body'],stack)
                elif n['op']=='if':
                    n['branches']=[{**branch,'body':walk(branch['body'],stack)} for branch in n['branches']]
                    n['else']=walk(n['else'],stack)
                result.append(n)
            return result
        expanded=walk(ast)
        return expanded, [used[k] for k in sorted(used)]

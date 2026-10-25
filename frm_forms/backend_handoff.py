"""Deterministic developer handoff: concrete endpoints, dependencies and blockers."""
from __future__ import annotations

import hashlib
import json
import json

from .common import name, write_json
from .generate import write
from .report import markdown_cell
from .rules import BLOCKING_SCOPES


NEXT = {
    'UNSUPPORTED_TRIGGER':'A megadott trigger és teljes híváslánca alapján implementáld/teszteld a ServiceImpl műveletet.',
    'QUERY_FILTER':'Ellenőrizd a WHERE teljes SQL-jét, bindjeit és a sor-/tenant-jogosultságokat a ServiceImpl-ben.',
    'QUERY_BIND_REQUIRED':'A paraméter nélküli listázás helyett kösd be a típusos keresési végpontot a felsorolt Forms-mezőkből.',
    'ORDER_BY':'Az eredeti rendezést ültesd át az SQL-be; ismeretlen kifejezést ne hagyj el.',
    'MASTER_DETAIL':'Kösd össze a kiválasztott master rekord kulcsait a detail keresési kritériumaival; a többblokkos mentés/cascade külön feladat.',
    'DYNAMIC_LOV':'A LOV lekérdezése generált végponton fut (lásd: LOV-végpontok); a frontend host-adapterében kösd be, és íráskor a választott értéket szerveroldalon is ellenőrizd (Validate from List).',
    'NO_PRIMARY_KEY':'A schema.json primary_key mezőjében add meg az adatbázisból igazolt kulcsoszlopokat.',
    'INHERITANCE_BACKEND_REVIEW':'Ellenőrizd az OLB-ből örökölt üzleti kódot és a hiányzó külső forrásokat, majd állítsd true-ra a ServiceImpl MODULE_REVIEWED kapcsolóját.',
    'INHERITANCE':'Modulszintű ellenőrzés: az öröklési lánc után a ServiceImpl MODULE_REVIEWED kapcsolója oldja fel a kész műveleteket.',
    'SCAFFOLD_REVIEW_REQUIRED':'A képernyőváz mód tiltását a ServiceImpl és a gazdaalkalmazás integrációjának ellenőrzése után a MODULE_REVIEWED kapcsoló oldja fel (egy helyen, nem metódusonként).',
    'RUNTIME_BLOCK_PROPERTY':'A futásidőben állított blokk-property (WHERE, rendezés, DML-cél vagy engedély) feltételét vidd át a ServiceImpl-be (jogosultsági feltételt a projekt jogosultságkezelése kezel).',
    'WRITE_APPROVAL':'Igazold a kulcsokat, DML-célt, DB-triggereket és tranzakciót, majd állítsd be a schema.json writable értékét. Ez önmagában más tiltást nem old fel.',
}


def write_handoff(model, discovery, output, config, module):
    cls=name(module,'pascal')
    code_index={}
    for code in discovery['code']:
        code_index.setdefault((code['owner'],code['name']),[]).append(code)
    queries=[]
    for b in model['blocks']:
        endpoints=b.get('endpoint_plan') or {}
        if not b['database'] or not (endpoints.get('list') or endpoints.get('search')): continue
        plan=b.get('query_plan',{})
        sql=None
        if plan.get('status')!='review' and b['db_items'] and not any(i['owner']==b['name'] and i['code']=='SQL_MAPPING' for i in model['issues']):
            order=plan.get('order_sql') or ', '.join(i['column'] for i in b['pk']) or b['db_items'][0]['column']
            from .generate import column_sql, table_alias
            sql='SELECT '+', '.join(column_sql(i) for i in b['db_items'])+' FROM '+b['table']+table_alias(b, b['table'])
            if plan.get('where_sql'): sql += ' WHERE '+plan['where_sql']
            sql += ' ORDER BY '+order+' OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY'
        search=plan.get('status')=='compiled' and bool(plan.get('binds'))
        method=('search' if search else 'list')+b['class']
        query={'block':b['name'],'method':method,'status':plan.get('status','plain_table'),
               'enabled':b.get('can_search',False) if search else b['can_read'],
               'ready_after_module_review':bool(b.get('ready_search' if search else 'ready_read')) and not (b.get('can_search',False) if search else b['can_read']),
               'path':config['api_prefix'].rstrip('/')+'/'+module+'/'+b['key']+'/'+('query/search' if search else config['endpoint_names']['list']),
               'http':'POST' if search else 'GET', 'sql':sql,
               'source_where':plan.get('where_source',''), 'source_order':plan.get('order_source',''),
               'relation_where':plan.get('relation_where',''), 'reason':plan.get('reason'),
               'parameters':[{'source':v['source'],'field':v['field'],'type':v['item']['type'],'jdbc_parameter':v['parameter']} for v in plan.get('binds',[])],
               'blockers':b.get('search_blockers',[]) if search else b['read_blockers']}
        if search:
            query['request_example']={'criteria':{v['field']:None for v in plan['binds']},'offset':0,'limit':50}
            query['example_notice']='Replace nulls with actual filter/master values. This is a wiring example, not permission to execute.'
        queries.append(query)
    tasks=[]; seen=set()
    writes=lambda b: any((b.get('endpoint_plan') or {}).get(op) for op in ('create','update','delete'))
    issues = list(model['issues']) + [
        {'code':'WRITE_APPROVAL','owner':b['name'],'scope':'write',
         'detail':'A schema.json writable beállítása nem engedélyez írást.'}
        for b in model['blocks'] if b['database'] and not b['writable'] and writes(b)]
    by_name={b['name']:b for b in model['blocks']}
    def generated(issue):
        # A single-operation reason for an endpoint that is not generated (e.g. KEY-DELREC
        # of a DeleteAllowed=false block) is no backend task.
        b=by_name.get(issue['owner'])
        if b is None or issue['scope'] not in {'read','create','update','delete'}: return True
        plan=b.get('endpoint_plan') or {}
        return bool(plan.get('list') or plan.get('search')) if issue['scope']=='read' else bool(plan.get(issue['scope']))
    for issue in issues:
        # Screen-only work (frontend) and notes (review) are not backend tasks.
        if issue['scope'] not in BLOCKING_SCOPES or not generated(issue): continue
        source = json.dumps([issue['code'],issue['owner'],issue['detail'],model['source_sha256']],ensure_ascii=False)
        task_id=hashlib.sha256(source.encode()).hexdigest()[:16]
        if task_id in seen: continue
        seen.add(task_id)
        tasks.append({'id':task_id,**issue,
                      'next_step':NEXT.get(issue['code'],'A forrásból igazold a mappinget/működést; ezután egészítsd ki a ServiceImpl-et és a célzott tesztet.'),
                      'target':'backend/DPS/'+cls+'ServiceImpl.java', 'source':'analysis/source.xml'})
    triggers=[]
    for tr in model['triggers']:
        related=code_index.get((tr['owner'],tr['event']),[])
        triggers.append({'id':tr['id'],'status':tr['status'],'reason':tr.get('reason'),
                         'backend_action':tr.get('backend_action'),
                         'framework_calls':tr.get('framework_calls',[]),
                         'inlined_units':[{'name':u['name'],'sha256':u['sha256']} for u in tr.get('inlined_program_units',[])],
                         'sources':['analysis/discovery/'+c['source_file'] for c in related],
                         'calls':[{'name':call['name'],'kind':call['kind'],'targets':call['target_ids']} for c in related for call in c['calls']]})
    company_contract = None
    contract_path = output/'analysis/cl-contract.json'
    if config.get('AWU_AZON') and contract_path.exists():
        company_contract = json.loads(contract_path.read_text(encoding='utf-8'))
        endpoints = {endpoint['method']: endpoint for endpoint in company_contract['endpoints']}
        for query in queries:
            endpoint = endpoints.get(query['method'])
            if endpoint:
                query.update(path=None, relative_path=endpoint['relative_path'],
                             path_expression=company_contract['wbs_base_path_expression'] + ' + ' + cls + 'Constants.' + endpoint['path_constant'])
    data={'version':1,'queries':queries,'relations':model.get('relation_contexts',[]),'triggers':triggers,
          'module_gate':model.get('module_gate',{'required':False}),
          'skipped_actions':model.get('skipped_actions', []),
          'tasks':tasks,'ai':model.get('ai',{}),'notice':'Generated plans are not a functional equivalence percentage; disabled endpoints stay disabled.',
          **({'company_contract': company_contract} if company_contract else {})}
    write_json(output/'analysis/backend-handoff.json',data)
    # Values derived from the XML/schema only; never guess a key or enable DML.
    schema={'blocks':{b['name']:{'table':b['table'],'primary_key':[i['column'] for i in b['pk']], 'writable':False}
                      for b in model['blocks'] if b['database'] and any((b.get('endpoint_plan') or {}).values())}}
    write_json(output/'analysis/backend-schema.example.json',schema)
    lines=['# Backend bekötési terv', '', 'A forrás és a generált kód alapján készült. A hiányzó runtime-viselkedés nem válik automatikusan kész megoldássá.', '']
    gate=model.get('module_gate',{})
    if gate.get('required'):
        lines += ['## Modulszintű ellenőrzés', '',
                  'Minden generált CRUD-műveletre vonatkozik; a kész műveleteket a `'+cls+'ServiceImpl.MODULE_REVIEWED` kapcsoló élesíti:', '',
                  *['- '+markdown_cell(r) for r in gate['reasons']], '']
    lines += ['## Lekérdezések', '', '| Blokk | Metódus | HTTP útvonal | Futás |', '|---|---|---|---|']
    for q in queries:
        state='engedélyezett' if q['enabled'] else 'kész, MODULE_REVIEWED-re vár' if q['ready_after_module_review'] else 'tiltott / ellenőrizendő'
        lines.append('| '+' | '.join(markdown_cell(v) for v in [q['block'],q['method'],q['http']+' '+(q['path'] or q['path_expression']),state])+' |')
    for q in queries:
        if q['parameters']:
            lines += ['', '### '+markdown_cell(q['method']), '', '| Forms-forrás | criteria mező | típus | JDBC bind |','|---|---|---|---|']
            lines += ['| '+' | '.join(markdown_cell(p[k]) for k in ['source','field','type','jdbc_parameter'])+' |' for p in q['parameters']]
            lines += ['', 'Kérésváz (a null értékeket a tényleges keresőmezőkből/kijelölt master rekordból töltsd):','', '```json',json.dumps(q['request_example'],ensure_ascii=False,indent=2),'```']
    lov_plans=model.get('lov_plans',[])
    if lov_plans:
        base=config['api_prefix'].rstrip('/')+'/'+module
        gated=bool(model.get('module_gate',{}).get('required'))
        lines += ['', '## LOV-végpontok', '',
                  'A RecordGroupQuery változatlanul fut; kérés: `{"term": "beg", "parameters": {"BLOKK.MEZŐ": "érték"}, "limit": 50}`. '
                  'A válasz sorai oszlopnév szerint érkeznek; a visszaírandó mezők a LOV oszlop-leképezései.', '',
                  '| LOV | HTTP útvonal | Paraméterek | Futás |', '|---|---|---|---|']
        for l in lov_plans:
            state='tiltott: '+'; '.join(l['blockers']) if l['blockers'] else 'kész, MODULE_REVIEWED-re vár' if gated else 'engedélyezett'
            path = base+'/lov/'+l['key']
            if company_contract:
                endpoint = endpoints['lov' + l['class']]
                path = company_contract['wbs_base_path_expression'] + ' + ' + cls + 'Constants.' + endpoint['path_constant']
            lines.append('| '+' | '.join(markdown_cell(v) for v in [l['name'], 'POST '+path,
                                                                     ', '.join(b['source'] for b in l['binds']) or '—', state])+' |')
    if model.get('skipped_actions'):
        lines += ['', '## Backend végpont nélkül kezelt triggerek', '',
                  'A teljes forrás megmarad az elemzésben. A szűrés a trigger tartalmán alapul, nem a gomb nevén.', '',
                  '| Objektum | Besorolás | Indok |', '|---|---|---|']
        lines += ['| ' + ' | '.join(markdown_cell(a[k]) for k in ('owner', 'category', 'reason')) + ' |'
                  for a in model['skipped_actions']]
    inputs = []
    for tr in model['triggers']:
        plan = (tr.get('query_action') or {}).get('prepared') or tr.get('passthrough') or {}
        inputs += [(tr['owner'] + ' / ' + tr['event'], i) for i in plan.get('inputs', [])]
    init = model.get('init_plan') or {}
    inputs += [('FORM / indítás (' + ', '.join(init.get('triggers', [])) + ')', i) for i in (init.get('plan') or {}).get('inputs', [])]
    for event, entry in (model.get('commit_plan') or {}).items():
        inputs += [('FORM / ' + event + ' (mentés)', i) for i in (entry.get('plan') or {}).get('inputs', [])]
    from . import java_variables
    given = [(owner, i, java_variables.lookup(i['variable'])) for owner, i in inputs if java_variables.lookup(i['variable'])]
    inputs = [(owner, i) for owner, i in inputs if not java_variables.lookup(i['variable'])]
    if inputs:
        lines += ['', '## Fejlesztői bemenetek', '',
                  'A kód ezeket az értékeket a migrált felületen nem kapja meg. A generált Java-metódus elején mindegyik egy '
                  '`null` kezdőértékű változó `// TODO` megjegyzéssel: add át neki a megfelelő értéket (például a bejelentkezett '
                  'felhasználóból, egy konfigurációból vagy a kérésből). Addig a kód `null` értékkel fut. Ami több formban '
                  'is ugyanaz, azt a java-variables.json-ba írva a generátor tölti ki.', '',
                  '| Trigger | Forms-hivatkozás | Java-változó | Ok |', '|---|---|---|---|']
        lines += ['| ' + ' | '.join(markdown_cell(v) for v in [owner, ':' + i['source'], i['variable'], i['reason']]) + ' |'
                  for owner, i in inputs]
    if given:
        lines += ['', '## Fejlesztői bemenetek a java-variables.json-ból', '',
                  'Ezeknek a változóknak az értékét a java-variables.json adja; a szükséges import és @Autowired mező a '
                  'DPS ServiceImpl-be került.', '',
                  '| Trigger | Forms-hivatkozás | Java-változó | Érték |', '|---|---|---|---|']
        lines += ['| ' + ' | '.join(markdown_cell(v) for v in [owner, ':' + i['source'], i['variable'], entry['value']]) + ' |'
                  for owner, i, entry in given]
    java_queries = [(tr['owner'], 'Java-lekérdezés', 'egyezés-ellenőrzés: ' + str(tr['query_action']['java']['equivalence']['checked'])
                     + ' eset egyezik') for tr in model['triggers'] if (tr.get('query_action') or {}).get('java')]
    fallbacks = [(tr['owner'], 'PL/SQL-adapter' if tr.get('query_action') else 'PL/SQL az adatbázisban' if tr.get('passthrough')
                  else 'kézi átültetés', tr['query_java_reason'])
                 for tr in model['triggers'] if tr.get('query_java_reason') and 'SET_BLOCK_PROPERTY' in str(tr.get('source', '')).upper()]
    if java_queries or fallbacks:
        lines += ['', '## Lekérdezőgombok', '',
                  'A DEFAULT_WHERE-t építő gombokból olvasható Java-lekérdezés lesz. Ha nem sikerül, az ok itt áll (a forrás sorával), '
                  'és a gombot a PL/SQL-adapter vagy az adatbázisban futó eredeti kód viszi.', '',
                  '| Gomb | Futás | Ok / ellenőrzés |', '|---|---|---|']
        lines += ['| ' + ' | '.join(markdown_cell(v) for v in row) + ' |' for row in java_queries + fallbacks]
    lines += ['', '## Konkrét teendők', '', '| Objektum | Ok | Folytatás |', '|---|---|---|']
    lines += ['| '+' | '.join(markdown_cell(t[k]) for k in ['owner','detail','next_step'])+' |' for t in tasks]
    lines += ['', 'A teljes, géppel is feldolgozható terv: [backend-handoff.json](analysis/backend-handoff.json).',
              'Kiinduló schema (minden írás tiltva): [backend-schema.example.json](analysis/backend-schema.example.json). Ellenőrizd adatbázis-metaadatokkal; hiányzó kulcsot nem talál ki.',
              'Triggerek és hívásláncok: [form-map.json](analysis/discovery/form-map.json). Az AI külön javaslat, nem módosít tiltást vagy kódot.','']
    write(output/'BACKEND_TASKS.md','\n'.join(lines))

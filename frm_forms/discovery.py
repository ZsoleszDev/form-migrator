"""Read-only Forms map. Lexical evidence is never executable PL/SQL translation."""
from __future__ import annotations
from bisect import bisect_right
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
from .common import write_json
from .xmlmodel import props, tag, get, package_part

FORMS_BUILTINS=set('''GO_ITEM GO_BLOCK NEXT_ITEM PREVIOUS_ITEM NEXT_RECORD PREVIOUS_RECORD
 FIRST_RECORD LAST_RECORD EXECUTE_QUERY ENTER_QUERY CLEAR_BLOCK CLEAR_FORM COMMIT_FORM
 EXIT_FORM CALL_FORM OPEN_FORM NEW_FORM DO_KEY EXECUTE_TRIGGER SET_ITEM_PROPERTY
 SET_BLOCK_PROPERTY SET_WINDOW_PROPERTY SET_CANVAS_PROPERTY SET_FORM_PROPERTY
 SET_RECORD_PROPERTY SET_LOV_PROPERTY SET_ALERT_PROPERTY GET_ITEM_PROPERTY
 GET_BLOCK_PROPERTY GET_FORM_PROPERTY GET_WINDOW_PROPERTY GET_APPLICATION_PROPERTY
 FIND_ITEM FIND_BLOCK FIND_WINDOW FIND_CANVAS FIND_LOV FIND_ALERT SHOW_ALERT MESSAGE
 SYNCHRONIZE LIST_VALUES SHOW_LOV CREATE_PARAMETER_LIST GET_PARAMETER_LIST
 DESTROY_PARAMETER_LIST ADD_PARAMETER DELETE_PARAMETER COPY NAME_IN PAUSE
 RAISE_FORM_TRIGGER_FAILURE SET_APPLICATION_PROPERTY SET_MENU_ITEM_PROPERTY
 REPLACE_MENU HIDE_WINDOW SHOW_WINDOW HIDE_VIEW SHOW_VIEW SET_VIEW_PROPERTY
 CREATE_TIMER DELETE_TIMER FIND_TIMER POPULATE_GROUP CREATE_GROUP_FROM_QUERY
 FIND_GROUP DELETE_GROUP ADD_LIST_ELEMENT DELETE_LIST_ELEMENT CLEAR_LIST
 FORM_SUCCESS FORM_FAILURE DEFAULT_VALUE VALIDATE POST LOCK_RECORD
 GET_RECORD_PROPERTY GET_GROUP_NUMBER_CELL GET_GROUP_CHAR_CELL GET_GROUP_DATE_CELL'''.split())
SQL_FUNCTIONS=set('''NVL NVL2 COALESCE NULLIF UPPER LOWER TRIM LTRIM RTRIM SUBSTR INSTR LENGTH
 REPLACE TRANSLATE TO_CHAR TO_DATE TO_NUMBER TO_TIMESTAMP ROUND TRUNC ABS MOD SIGN CEIL
 FLOOR DECODE COUNT SUM AVG MIN MAX SYSDATE SYSTIMESTAMP USER SQLERRM SQLCODE
 DBMS_ERROR_CODE DBMS_ERROR_TEXT CHR ASCII LPAD RPAD CONCAT'''.split())
KEYWORDS=set('''BEGIN END IF THEN ELSE ELSIF LOOP FOR WHILE RETURN RAISE EXCEPTION WHEN
 OTHERS NULL DECLARE IS AS IN OUT AND OR NOT LIKE BETWEEN EXISTS CASE SELECT FROM
 INTO WHERE HAVING GROUP ORDER BY JOIN ON INSERT UPDATE DELETE MERGE VALUES SET
 COMMIT ROLLBACK EXECUTE IMMEDIATE CURSOR OPEN CLOSE FETCH PROCEDURE FUNCTION PACKAGE
 BODY TYPE SUBTYPE RECORD TABLE INDEX OF CONSTANT DEFAULT NUMBER VARCHAR VARCHAR2 CHAR
 NCHAR NVARCHAR2 DATE TIMESTAMP BOOLEAN PLS_INTEGER INTEGER INT BINARY_INTEGER FLOAT
 REAL DOUBLE PRECISION RAW CLOB BLOB LONG PRAGMA NOCOPY TRUE FALSE DISTINCT ALL UNION
 INTERSECT MINUS CONNECT START WITH PRIOR LEVEL BULK COLLECT LIMIT USING SAVEPOINT
 GOTO EXIT CONTINUE AUTHID RESULT_CACHE DETERMINISTIC PARALLEL_ENABLE PIPELINED
 RETURNING EXCEPTION_INIT'''.split())
IDENT=r'[A-Za-z_$#][A-Za-z0-9_$#]*'
TOKEN=re.compile(rf':{IDENT}(?:\.{IDENT})*|{IDENT}(?:\.{IDENT})*|:=|=>|<=|>=|<>|!=|\|\||[^\s]')

def source_view(source):
    def replace(m):
        v=m.group(1); n=int(v[1:],16) if v[:1].lower()=='x' else int(v)
        return chr(n) if n in {9,10,13} else m.group()
    view=re.sub(r'&#(x[0-9a-fA-F]+|[0-9]+);',replace,source)
    return view,view!=source

def lex(source):
    """Mask comments/literals, preserving offsets and never executing input."""
    masked=list(source); warnings=[]; i=0
    while i<len(source):
        end=None; literal=False
        if source.startswith('--',i):
            ends=[p for p in [source.find('\n',i),source.find('\r',i)] if p>=0]
            end=min(ends) if ends else len(source)
        elif source.startswith('/*',i):
            close=source.find('*/',i+2); end=close+2 if close>=0 else len(source)
            if close<0:warnings.append('Unterminated block comment')
        elif source[i:i+2].lower()=="q'" and i+2<len(source):
            opening=source[i+2]; closing={'[':']','{':'}','(':')','<':'>'}.get(opening,opening)
            close=source.find(closing+"'",i+3);end=close+2 if close>=0 else len(source);literal=True
            if close<0:warnings.append('Unterminated q literal')
        elif source[i] in "'\"":
            quote=source[i];j=i+1;literal=True
            while j<len(source):
                if source[j]==quote:
                    if j+1<len(source) and source[j+1]==quote:j+=2;continue
                    break
                j+=1
            end=min(j+1,len(source))
            if j>=len(source):warnings.append('Unterminated literal/quoted identifier')
            if quote=='"':warnings.append('Quoted identifier: call resolution is incomplete')
        if end is None:i+=1;continue
        for p in range(i,end):
            if masked[p] not in '\r\n':masked[p]=' '
        if literal:masked[i]='?'
        i=end
    return [(m.group().upper(),m.start(),m.end()) for m in TOKEN.finditer(''.join(masked))],sorted(set(warnings))

def scan(source):
    view,decoded=source_view(source);tokens,warnings=lex(view)
    lines=[m.start() for m in re.finditer('\n',view)]
    def line(pos):return bisect_right(lines,pos)+1
    binds=[];calls=[];sql=[];matching={};stack=[]
    for n,t in enumerate(tokens):
        if t[0]=='(':stack.append(n)
        elif t[0]==')' and stack:matching[stack.pop()]=n
    for n,(word,start,end) in enumerate(tokens):
        prev=tokens[n-1][0] if n else '';nxt=tokens[n+1][0] if n+1<len(tokens) else ''
        if word.startswith(':') and word!=':=':
            binds.append({'name':word[1:],'line':line(start),'usage':'assignment-target' if nxt==':=' else 'reference',
                          'parameter_direction':'unknown; signature/SQL context required'});continue
        if word in {'SELECT','INSERT','UPDATE','DELETE','MERGE'} and prev not in {'.','FOR','ON','BEFORE','AFTER'}:
            last=next((k for k in range(n,len(tokens)) if tokens[k][0]==';'),len(tokens)-1)
            sql.append({'kind':word,'line':line(start),'text':view[start:tokens[last][2]],'execution':'not-executed'})
        if not re.fullmatch(IDENT+r'(?:\.'+IDENT+r')*',word) or word in KEYWORDS:continue
        if prev in {'PROCEDURE','FUNCTION','CURSOR','END','TYPE','SUBTYPE','RAISE','GOTO','TABLE','FROM','JOIN','INTO','UPDATE'}:continue
        args=[];call_end=end
        if nxt=='(':
            closing=matching.get(n+1)
            if closing is None:warnings.append('Unbalanced call parentheses: '+word);continue
            depth=0;at=tokens[n+1][2]
            for k in range(n+2,closing):
                v,a,b=tokens[k]
                if v=='(':depth+=1
                elif v==')':depth-=1
                elif v==',' and depth==0:args.append(view[at:a].strip());at=b
            tail=view[at:tokens[closing][1]].strip()
            if tail or args:args.append(tail)
            call_end=tokens[closing][2]
        elif nxt==';' and prev in {'','BEGIN','THEN','ELSE','LOOP',';'}:pass
        else:continue
        from .forms_runtime import builtin
        kind='forms_builtin' if word in FORMS_BUILTINS or builtin(word) else 'sql_builtin' if word in SQL_FUNCTIONS else 'unresolved'
        calls.append({'name':word,'kind':kind,'line':line(start),'arguments':args,'text':view[start:call_end],
                      'target_ids':[],'signature_verified':False})
    dynamic=any(tokens[n][0]=='EXECUTE' and tokens[n+1][0]=='IMMEDIATE' for n in range(len(tokens)-1))
    if dynamic:warnings.append('Dynamic SQL: runtime statement cannot be resolved statically')
    if decoded:warnings.append('Numeric whitespace entities decoded only in analysis view; original source retained')
    return {'source_view':view,'analysis_view_decoded':decoded,'calls':calls,'binds':binds,'sql':sql,'dynamic_sql':dynamic,
            'warnings':sorted(set(warnings)),'analysis_kind':'lexical-evidence-not-a-complete-PLSQL-parser'}

def build_map(documents, catalog=None):
    """catalog: the framework catalog; its Forms-runtime routines (calendar.event) are no database candidates."""
    from .framework import runtime_call
    runtime=(lambda n:runtime_call(n,catalog)) if catalog else (lambda n:None)
    objects=[];units=[];file_records=[]
    for filename,root in documents:
        file_records.append({'filename':filename});pending=[(root,None,'',None,None,None)]
        while pending:
            element,parent,path,block,item,module_kind=pending.pop();p=props(element);kind=tag(element)
            if kind in {'formmodule','objectlibrary','menumodule'}:module_kind=kind
            label=get(p,'Name','TriggerName');obj_path=path+'/'+kind+(':'+label if label else '')
            if kind=='programunit' and package_part(p):obj_path+='[package-'+package_part(p)+']'
            oid='o'+str(len(objects));current_block=label if kind=='block' else block
            current_item=label if kind=='item' else (None if kind=='block' else item)
            objects.append({'id':oid,'file':filename,'kind':kind,'name':label,'path':obj_path,'parent_id':parent,
                            'block':current_block,'item':current_item,'module_kind':module_kind,'properties':dict(p)})
            source_key={'trigger':'triggertext','programunit':'programunittext','recordgroup':'recordgroupquery','menuitem':'commandtext'}.get(kind)
            if source_key:
                source=p.get(source_key,p.get('text',element.text or '') if kind=='trigger' else '')
                owner=((current_block or '@LIBRARY')+'.'+current_item) if current_item else (current_block or label)
                code={'id':oid,'file':filename,'kind':kind,'module_kind':module_kind,'name':label,'owner':owner,
                      'block':current_block,'item':current_item,'path':obj_path,'source':source,
                      'sha256':hashlib.sha256(source.encode()).hexdigest(),**scan(source)}
                code['source_file']='sources/'+oid+'-'+code['sha256'][:12]+'.sql'
                code['view_file']='sources/'+oid+'-'+code['sha256'][:12]+'.view.sql' if code['analysis_view_decoded'] else code['source_file']
                if kind=='programunit':
                    code.update(program_unit_type=get(p,'ProgramUnitType') or None,package_part=package_part(p),parent_id=parent)
                units.append(code)
            children=[e for e in element if tag(e) not in {'property','propertyvalue','triggertext','programunittext','recordgroupquery'}]
            pending.extend((e,oid,obj_path,current_block,current_item,module_kind) for e in reversed(children))
    local=defaultdict(list);triggers=defaultdict(list)
    for code in units:
        if code['kind']=='programunit':local[(code['file'],code['name'].upper())].append(code['id'])
        if code['kind']=='trigger':triggers[(code['file'],code['name'].upper())].append(code['id'])
    by_id={c['id']:c for c in units};edges=[]
    for code in units:
        for call in code['calls']:
            candidates=local.get((code['file'],call['name']),[]) or local.get((code['file'],call['name'].split('.')[0]),[])
            if not candidates and call['name'] in {'EXECUTE_TRIGGER','DO_KEY'} and call['arguments']:
                arg=call['arguments'][0]
                if re.fullmatch(r"'[^']*'",arg):
                    event=arg[1:-1].upper()
                    if call['name']=='DO_KEY':
                        from .forms_keys import KEY_EVENTS
                        event=KEY_EVENTS.get(event)
                    call['event_target_candidates']=triggers.get((code['file'],event),[])
            if candidates:
                pair=[by_id[c] for c in candidates]
                if ('.' in call['name'] and len(pair)==2 and {c.get('package_part') for c in pair}=={'spec','body'}
                        and pair[0]['parent_id']==pair[1]['parent_id']):
                    # Both complete source units are review evidence. This is
                    # not member/signature resolution or executable translation.
                    call['kind']='local_package'
                    call['resolution_scope']='package-sources-only; member and signature unverified'
                else:call['kind']='local_program_unit' if len(candidates)==1 else 'ambiguous_local_program_unit'
                call['target_ids']=candidates
            elif call['kind']=='unresolved' and runtime(call['name']):call['kind']='forms_runtime'
            elif call['kind']=='unresolved':call['kind']='external_candidate' if '.' in call['name'] else 'unresolved'
            edges.append({'from':code['id'],'to':call['target_ids'],'name':call['name'],'kind':call['kind'],'line':call['line']})
    actions=[]
    for code in units:
        if code['kind']!='trigger' or code['module_kind']!='formmodule' or code['name'].upper()!='WHEN-BUTTON-PRESSED':continue
        reachable=[];seen=set();pending=[code['id']]
        while pending:
            cid=pending.pop(0)
            if cid in seen:continue
            seen.add(cid);reachable.append(cid)
            pending.extend(t for c in by_id[cid]['calls'] if c['kind'] in {'local_program_unit','local_package'} for t in c['target_ids'] if t not in seen)
        event_candidates=[]
        pending=[t for cid in reachable for c in by_id[cid]['calls'] for t in c.get('event_target_candidates',[])]
        while pending:
            cid=pending.pop(0)
            if cid in seen:continue
            seen.add(cid);event_candidates.append(cid)
            pending.extend(t for c in by_id[cid]['calls']
                           for t in c['target_ids']+c.get('event_target_candidates',[]) if t not in seen)
        actions.append({'id':code['id'],'owner':code['owner'],'block':code['block'],'item':code['item'],'event':code['name'],
                        'trigger_id':code['id'],'reachable_code':reachable,'binds':sorted({b['name'] for cid in reachable for b in by_id[cid]['binds']}),
                        'event_code_candidates':event_candidates,
                        'external_calls':sorted({c['name'] for cid in reachable for c in by_id[cid]['calls'] if c['kind'] in {'external_candidate','unresolved','ambiguous_local_program_unit'}}),
                        'status':'review-required','execution':'disabled'})
    dependencies=[]
    for obj in objects:
        p=obj['properties']
        if p.get('parentfilename') or p.get('parentname') or p.get('parentmodule'):
            dependencies.append({'object_id':obj['id'],'kind':'inheritance','file':p.get('parentfilename',''),
                                 'module':p.get('parentmodule',''),'name':p.get('parentname','')})
        if obj['kind']=='attachedlibrary':dependencies.append({'object_id':obj['id'],'kind':'attached_library','file':p.get('librarylocation','') or obj['name'],'name':obj['name']})
    warnings=['A hívástérkép lexikai leltár, nem teljes PL/SQL fordító. A lehetséges hívások nem bizonyítják a futási sorrendet vagy a DB-eljárások létezését, szignatúráját és paraméterirányát.']
    if any(c['analysis_view_decoded'] for c in units):warnings.append('Numerikus sortörés-entitások maradtak a PL/SQL-ben. Csak az elemzési nézet oldja fel őket; az eredeti forrás megmarad.')
    if dependencies:warnings.append('Csak a megadott XML-ek tartalma ismert; a hiányzó könyvtári kód nem rekonstruálható biztosan.')
    if any(c['kind']=='local_package' for u in units for c in u['calls']):
        warnings.append('A helyi package-hívásoknál a teljes specifikáció és törzs ellenőrzendő forrásjelölt. A csomagon belüli tag, túlterhelés és szignatúra nincs feloldva; a felsorolt bindek/hívások más taghoz is tartozhatnak.')
    return {'map_version':1,'files':file_records,'counts':dict(sorted(Counter(o['kind'] for o in objects).items())),
            'objects':objects,'code':units,'call_edges':edges,'actions':actions,'dependencies':dependencies,'warnings':warnings}

def write_map(data,output:Path):
    output.mkdir(parents=True,exist_ok=True);write_json(output/'form-map.json',data)
    for code in data['code']:
        p=output/code['source_file'];p.parent.mkdir(exist_ok=True);p.write_text(code['source'],encoding='utf-8')
        if code['analysis_view_decoded']:(output/code['view_file']).write_text(code['source_view'],encoding='utf-8')
    def cell(v):return str(v).replace('|','\\|').replace('\n',' ').replace('\r',' ')
    lines=['# Forms modultérkép','',*['- '+w for w in data['warnings']],'','| Elem | Darab |','|---|---:|',
           *[f'| {k} | {v} |' for k,v in data['counts'].items()],'','## Gombesemények és helyi hívásláncok','',
           '| Tulajdonos | Helyi források | Bindek | Külső/ismeretlen hívások |','|---|---|---|---|']
    codes={c['id']:c for c in data['code']}
    for a in data['actions']:
        lines.append('| '+' | '.join(cell(v) for v in [a['owner'],', '.join(codes[c]['name'] for c in a['reachable_code']),', '.join(a['binds']),', '.join(a['external_calls'])])+' |')
    lines+=['','Teljes kereshető modultérkép: [form-explorer.html](form-explorer.html). Eredeti kódok: sources/. A .view.sql fájl csak elemzési nézet.','']
    (output/'form-map.md').write_text('\n'.join(lines),encoding='utf-8')
    template=(Path(__file__).parent/'data/form-explorer.html').read_text(encoding='utf-8')
    payload=json.dumps(data,ensure_ascii=True,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    (output/'form-explorer.html').write_text(template.replace('__FORM_MAP_JSON__',payload),encoding='utf-8')

"""Resolved XML -> versioned UI contract. No Java IR mutation, no generated code here."""
from __future__ import annotations
from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import copy
import hashlib
import json
import re
from pathlib import Path
from .common import MigrationError, name, write_json
from .xmlmodel import canonical
from .ui_types import classify
from .ui_config import widget_map
from .ui_inventory import SUPPORTED, AUDIT, INHERITANCE, NON_UI_ELEMENTS, collect
from .presentation import output_name

MISSING=object()
BOOLEAN={'true':True,'yes':True,'1':True,'false':False,'no':False,'0':False}
ALIASES={
    'DatabaseDataBlock':('DatabaseDataBlock','DatabaseBlock'),
    'MasterDataBlock':('MasterDataBlock','MasterBlock'), 'DetailDataBlock':('DetailDataBlock','DetailBlock'),
    'DeleteRecordBehavior':('DeleteRecordBehavior','DeleteRecord'),
    'MaximumLength':('MaximumLength','MaxLength'), 'CheckedValue':('CheckedValue','CheckBoxCheckedValue'),
    'UncheckedValue':('UncheckedValue','CheckBoxUncheckedValue'), 'InitialValue':('InitialValue','InitializeValue'),
    'NumberOfRecordsDisplayed':('NumberOfRecordsDisplayed','RecordsDisplayCount'),
    'Visible':('Visible','Displayed'), 'Value':('Value','ListItemValue','RadioButtonValue'),
    'Label':('Label','ListItemLabel'),
}

class UiRejected(MigrationError):
    def __init__(self,model):
        import json
        self.model=model
        super().__init__(json.dumps({'ui_model_version':model['schema_version'],'status':'rejected',
                                     'issues':[i for i in model['issues'] if i['severity']=='error'],
                                     'hint':'Nincs generált forrás. --analysis-only: teljes diagnosztika külön célmappába.'},ensure_ascii=False,sort_keys=True))

class Reader:
    def __init__(self,builder,element):
        self.b=builder; self.element=element; self.kind=canonical(element.tag)
        self.p=dict(element.attrib); self.used=set()
        self.record=copy.deepcopy(builder.resolution.metadata.get(id(element), {'path':self.kind,'properties':{}}))
        self.provenance={k:v for k,v in self.record['properties'].items() if v.get('source')!='default'}
        for original,target in builder.config.get('property_aliases',{}).get(self.kind,{}).items():
            if original in self.p:
                if target in self.p and self.p[target]!=self.p[original]: builder.issue('PROPERTY_ALIAS_CONFLICT',self.owner,'Ellentmondó property alias: '+original)
                else:
                    self.p[target]=self.p[original]
                    if original in self.provenance: self.provenance[target]=copy.deepcopy(self.provenance[original])
                self.used.add(original)
    @property
    def owner(self): return self.record['path']
    def read(self,key,default=None):
        aliases=[canonical(k) for k in ALIASES.get(key,(key,))]
        found=[k for k in aliases if k in self.p]
        self.used.update(found)
        if len({self.p[k] for k in found})>1:
            self.b.issue('PROPERTY_ALIAS_CONFLICT',self.owner,'Eltérő értékek: '+', '.join(found))
        if found: return self.p[found[0]]
        ck=canonical(key)
        self.provenance.setdefault(ck, {'value':default,'source':'default','origin':None,'basis':'UI contract fallback, not an Oracle default'})
        return default
    def boolean(self,key,default=False):
        value=self.read(key,default)
        if type(value) is bool: return value
        if str(value).casefold() not in BOOLEAN:
            self.b.issue('INVALID_BOOLEAN',self.owner,key+' értéke nem boolean: '+str(value)); return default
        return BOOLEAN[str(value).casefold()]
    def decimal(self,key,default=None):
        value=self.read(key,default)
        if value is None or value=='': return None
        try:
            if len(str(value))>260: raise InvalidOperation
            result=Decimal(str(value))
            if not result.is_finite(): raise InvalidOperation
            if result.as_tuple().exponent < -130 or result.as_tuple().exponent > 125 or abs(result)>Decimal('1e125'): raise InvalidOperation
            return result
        except InvalidOperation:
            self.b.issue('INVALID_NUMBER',self.owner,key+' értéke nem véges decimális szám: '+str(value)); return None
    def integer(self,key,default=None,min_value=None):
        value=self.decimal(key,default)
        if value is None: return default
        if value!=value.to_integral_value() or (min_value is not None and value<min_value):
            self.b.issue('INVALID_INTEGER',self.owner,key+' hibás egész érték: '+str(value)); return default
        return int(value)
    def finish(self):
        for key in sorted(self.p):
            if key not in SUPPORTED.get(self.kind,set()) and key not in self.used:
                reason=self.b.config.get('ignored_properties',{}).get(self.kind,{}).get(key)
                self.b.issue('IGNORED_PROPERTY' if reason else 'UNMAPPED_ATTRIBUTE',self.owner,
                             self.kind+'.'+key+': '+(reason or 'Nincs jóváhagyott leképezés; inventory és property_aliases segítségével ellenőrizd.'),
                             severity='review' if reason else 'error')
        return {key:self.provenance[key] for key in sorted(self.provenance)}


def decimal_text(value):
    if value is None: return None
    return format(value,'f')

class Builder:
    def __init__(self,resolution,config,module,backend_model=None):
        self.resolution=resolution; self.config=config; self.module=module
        self.backend=backend_model or {}; self.issues=[]; self.issue_keys=set(); self.i18n={}; self.endpoints=[]; self.readers={}
        self.non_ui=Counter()
        self.widgets=widget_map(config)
    def issue(self,code,owner,detail,severity='error',**extra):
        issue={'code':code,'owner':owner,'detail':detail,'severity':severity,**extra}
        key=json.dumps(issue,sort_keys=True,ensure_ascii=False)
        if key not in self.issue_keys:
            self.issue_keys.add(key); self.issues.append(issue)
    def reader(self,element):
        if id(element) not in self.readers: self.readers[id(element)]=Reader(self,element)
        return self.readers[id(element)]
    def text(self,key,value,allow_empty=False):
        if value is None or (value=='' and not allow_empty): return None
        if key in self.i18n and self.i18n[key]!=value: self.issue('I18N_KEY_COLLISION',key,'Eltérő szövegek azonos kulcson.')
        self.i18n[key]=value; return key
    def texts(self,r,prefix):
        return {canonical(k):self.text(prefix+'.'+canonical(k),r.read(k)) for k in ['Prompt','Label','Hint','ToolTip']}
    def coordinates(self,form):
        children=[e for e in form.element if canonical(e.tag)=='coordinate']
        if len(children)>1:
            self.issue('DUPLICATE_COORDINATES',form.owner,'Több Coordinate objektum szerepel a modulban.')
        if children:
            coordinate=self.reader(children[0])
            for key in ['CoordinateSystem','RealUnit','CharacterCellWidth','CharacterCellHeight']:
                ck=canonical(key)
                if ck in form.p and ck in coordinate.p and form.p[ck]!=coordinate.p[ck]:
                    self.issue('CONFLICTING_COORDINATES',form.owner,'FormModule és Coordinate eltér: '+key)
            if 'coordinatesystem' not in form.p: form=coordinate
        system=form.read('CoordinateSystem')
        if canonical(str(system or '')) not in {'real','character'}:
            self.issue('COORDINATE_SYSTEM_REQUIRED',form.owner,'CoordinateSystem kötelező: Real vagy Character; pixelt nem feltételezünk.')
            return {'system':'unsupported','unit':None,'units_per_inch':None}
        if canonical(system)=='character':
            width=form.decimal('CharacterCellWidth'); height=form.decimal('CharacterCellHeight')
            if width is not None and width<=0 or height is not None and height<=0:
                self.issue('INVALID_CHARACTER_CELL',form.owner,'A megadott karakterméretek csak pozitívak lehetnek.')
            return {'system':'character','unit':'character-cell','units_per_inch':None,
                    'cell_width':decimal_text(width),'cell_height':decimal_text(height)}
        unit=form.read('RealUnit'); factors={'inch':'1','inches':'1','centimeter':'2.54','centimeters':'2.54',
                                             'millimeter':'25.4','millimeters':'25.4','point':'72','points':'72','decipoint':'720','decipoints':'720','pixel':None,'pixels':None}
        if canonical(str(unit or '')) not in factors:
            self.issue('REAL_UNIT_REQUIRED',form.owner,'RealUnit kötelező és csak explicit támogatott egység fogadható el: '+str(unit))
        return {'system':'real','unit':unit,'units_per_inch':factors.get(canonical(str(unit or '')))}
    def options(self,element,prefix,widget):
        result=[]; seen=set()
        for child in element:
            if canonical(child.tag) not in {'listitemelement','radiobutton'}: continue
            r=self.reader(child); val=r.read('Value'); label=r.read('Label')
            if val is None or label is None: self.issue('INVALID_OPTION',r.owner,'Minden opcióhoz explicit Label és Value szükséges.'); continue
            if val in seen: self.issue('DUPLICATE_OPTION',r.owner,'Ismétlődő opcióérték: '+val)
            seen.add(val)
            result.append({'value':val,'label_key':self.text(prefix+'.option'+str(len(result)),label,allow_empty=True),
                           'enabled':r.boolean('Enabled',True),'visible':r.boolean('Visible',True)})
            r.finish()
        if widget in {'select','multiselect','radio'} and not result: self.issue('MISSING_OPTIONS',prefix,'Statikus listához / rádiócsoporthoz nincsenek opciók.')
        return result
    def validations(self,r,widget):
        maximum=r.integer('MaximumLength',None,1); fixed=r.read('FixedLength',False)
        fixed_length=None
        if str(fixed).casefold() in BOOLEAN:
            if BOOLEAN[str(fixed).casefold()]: fixed_length=maximum
            if BOOLEAN[str(fixed).casefold()] and maximum is None: self.issue('FIXED_LENGTH_REQUIRES_LENGTH',r.owner,'FixedLength=true mellé MaximumLength kell.')
        elif fixed not in (False,None,''):
            self.issue('INVALID_FIXED_LENGTH',r.owner,'FixedLength boolean szükséges; nem hossz érték.')
        low=r.decimal('LowestAllowedValue'); high=r.decimal('HighestAllowedValue')
        if low is not None and high is not None and low>high: self.issue('INVALID_RANGE',r.owner,'LowestAllowedValue > HighestAllowedValue.')
        precision=r.integer('Precision',None,1); scale=r.integer('Scale',None)
        if precision is not None and (precision>38 or (scale is not None and (scale<0 or scale>precision))):
            self.issue('UNSUPPORTED_PRECISION',r.owner,'Támogatott NUMBER tartomány: 1..38 precision és 0..precision scale.')
        if precision is None and scale is not None: self.issue('MISSING_PRECISION',r.owner,'Scale mellé Precision szükséges.')
        case=r.read('CaseRestriction','Mixed')
        cases={'mixed':'mixed','uppercase':'upper','lowercase':'lower','upper':'upper','lower':'lower'}
        if canonical(case) not in cases: self.issue('UNSUPPORTED_CASE_RESTRICTION',r.owner,'Ismeretlen CaseRestriction: '+case)
        mask=r.read('FormatMask','')
        date_format=None
        if mask:
            normalized=mask.upper()
            date_masks={'YYYY-MM-DD':'yy-mm-dd','DD.MM.YYYY':'dd.mm.yy','DD/MM/YYYY':'dd/mm/yy','YYYY.MM.DD':'yy.mm.dd',
                        'YYYY-MM-DD HH24:MI:SS':'yy-mm-dd','DD.MM.YYYY HH24:MI:SS':'dd.mm.yy'}
            if widget in {'date','datetime'} and normalized in date_masks: date_format=date_masks[normalized]
            else: self.issue('UNSUPPORTED_FORMAT_MASK',r.owner,'FormatMask pontos formázási szemantikája nincs támogatva: '+mask)
        if (low is not None or high is not None or precision is not None) and widget!='number':
            self.issue('NON_NUMERIC_RANGE',r.owner,'Numerikus határ/precision nem numerikus widgeten nem képezhető le.')
        return {'required':r.boolean('Required'), 'maximum_length':maximum,'fixed_length':fixed_length,
                'minimum':decimal_text(low),'maximum':decimal_text(high),'precision':precision,'scale':scale,
                'format_mask':mask,'date_format':date_format,'case':cases.get(canonical(case),'mixed')}
    def item(self,element,block,prefix,index):
        r=self.reader(element); original=r.read('Name')
        if not original: self.issue('UNNAMED_ITEM',r.owner,'Item.Name kötelező.'); original='invalid'+str(index)
        key=name(original); owner=block+'.'+original
        typ=classify(r.p,[c.tag for c in element]); widget=typ['widget']; dtype=canonical(r.read('DataType','Char'))
        if widget=='text':
            if r.boolean('ConcealData'): widget='password'
            elif r.boolean('MultiLine'): widget='textarea'
            elif dtype in {'number','integer','int'}: widget='number'
            elif dtype in {'date','datetime','timestamp'}:
                mask=r.read('FormatMask','').upper(); widget='date' if mask and not any(x in mask for x in ['HH','MI','SS']) else 'datetime'
            elif dtype not in {'char','character','varchar','varchar2','alpha'}:
                self.issue('UNSUPPORTED_DATA_TYPE',owner,'Text Item DataType: '+dtype)
        if widget=='select':
            style=canonical(r.read('ListStyle','Poplist'))
            if style not in {'poplist','tlist','combolist','combo'}: self.issue('UNSUPPORTED_LIST_STYLE',owner,'ListStyle: '+style)
            if r.boolean('MultiSelection'): widget='multiselect'
            # A combo permits an arbitrary typed value, unlike a closed dropdown.
            if style in {'combolist','combo'}:
                self.issue('UNSUPPORTED_LIST_STYLE',owner,'Combo List szabad szöveg + lista szemantikához ellenőrzött adapter szükséges.')
        lov=r.read('LOVName','')
        if lov:
            if widget not in {'text','number','select'}: self.issue('INVALID_LOV_WIDGET',owner,'LOVName nem kapcsolható ehhez a widgethez: '+widget)
            widget='autocomplete'
        if widget=='unsupported': self.issue('UNSUPPORTED_ITEM',owner,typ['reason'],item=original,canvas=r.read('CanvasName',''),item_type=typ['item_type'])
        elif typ['type_source']=='inferred': self.issue('INFERRED_ITEM_TYPE',owner,typ['reason'],severity='review')
        validation=self.validations(r,widget)
        representation='decimal-string' if widget=='number' else 'string'
        if widget=='number' and validation['precision'] is not None and validation['precision']<=15 and validation['scale']==0:
            representation='safe-integer'
        if widget=='number' and representation=='safe-integer' and any(value is not None and abs(Decimal(value))>9007199254740991 for value in [validation['minimum'],validation['maximum']]):
            self.issue('UNSAFE_NUMBER_BOUND',owner,'inputNumber minimum/maximum túl nagy a JS safe integer tartományhoz.')
        if widget=='number' and representation=='decimal-string': self.issue('EXACT_DECIMAL_TEXT',owner,'A NUMBER szöveges decimálisként marad pontos; JS Number konverzió nincs.',severity='review')
        if widget in {'date','datetime'}: representation='date'
        if widget=='checkbox': representation='boolean'
        if widget=='multiselect': representation='string-array'
        checked=r.read('CheckedValue') if widget=='checkbox' else None; unchecked=r.read('UncheckedValue') if widget=='checkbox' else None
        if widget=='checkbox' and (checked is None or unchecked is None or checked==unchecked): self.issue('INVALID_CHECKBOX_MAPPING',owner,'Eltérő, explicit CheckedValue és UncheckedValue szükséges.')
        other=r.read('MappingOfOtherValues','Not Allowed')
        other_values={'notallowed':'reject','checked':'checked','unchecked':'unchecked'}
        if canonical(other) not in other_values: self.issue('UNSUPPORTED_OTHER_VALUES',owner,'MappingOfOtherValues: '+str(other))
        if widget!='checkbox' and canonical(other) not in {'notallowed'}: self.issue('INVALID_OTHER_VALUES',owner,'MappingOfOtherValues csak checkboxon értelmezett.')
        initial=r.read('InitialValue')
        if initial and initial.startswith("'"):
            if re.fullmatch(r"'(?:[^']|'')*'", initial): initial=initial[1:-1].replace("''","'")
            else: self.issue('INVALID_INITIAL_LITERAL',owner,'Érvénytelen idézett InitialValue konstans.')
        if initial is not None and (re.match(r'\s*(:|=)',initial) or initial.upper() in {'SYSDATE','SYSTEM_DATE','USER'}): self.issue('DYNAMIC_INITIAL_VALUE',owner,'Dinamikus InitialValue nem hajtható végre a böngészőben: '+initial)
        if widget=='checkbox' and initial not in {None,'',checked,unchecked} and other_values.get(canonical(other))=='reject': self.issue('INVALID_CHECKBOX_INITIAL',owner,'Az InitialValue kívül esik a checkbox értékkészletén.')
        if widget=='number' and initial not in {None,''}:
            try:
                value=Decimal(initial)
                if not value.is_finite() or (representation=='safe-integer' and (value!=value.to_integral_value() or abs(value)>9007199254740991)): raise InvalidOperation
            except InvalidOperation: self.issue('INVALID_NUMBER_INITIAL',owner,'Érvénytelen numerikus InitialValue: '+initial)
        if widget in {'date','datetime'} and initial:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?)?', initial):
                self.issue('UNSUPPORTED_DATE_INITIAL',owner,'ISO helyi dátum kell, időzóna és töredékmásodperc nélkül.')
            import datetime
            try: datetime.datetime.fromisoformat(initial)
            except ValueError: self.issue('UNSUPPORTED_DATE_INITIAL',owner,'Csak ISO formájú statikus dátum InitialValue támogatott.')
        options=self.options(element,prefix+'.'+key,widget)
        if any(not option['enabled'] for option in options):
            self.issue('UNSUPPORTED_DISABLED_OPTION',owner,'Az opciószintű tiltás céges FormBlock szerződése nem ismert; nem hagyjuk figyelmen kívül.')
        if widget in {'select','radio'} and initial not in {None,''} and initial not in {o['value'] for o in options}: self.issue('INVALID_OPTION_INITIAL',owner,'InitialValue nincs az opciók között.')
        if widget=='multiselect' and initial: self.issue('MULTISELECT_INITIAL',owner,'A többértékű InitialValue XML-kódolásához explicit adapter kell.')
        text_keys=self.texts(r,prefix+'.'+key)
        if not (text_keys['prompt'] or text_keys['label']): text_keys['label']=self.text(prefix+'.'+key+'.label',original)
        x=r.decimal('XPosition'); y=r.decimal('YPosition'); width=r.decimal('Width'); height=r.decimal('Height')
        if (x is None)!=(y is None): self.issue('INCOMPLETE_COORDINATES',owner,'XPosition és YPosition együtt szükséges.')
        if width is not None and width<=0 or height is not None and height<=0: self.issue('INVALID_GEOMETRY',owner,'Width és Height csak pozitív lehet.')
        layout={'x':decimal_text(x),'y':decimal_text(y),'width':decimal_text(width),'height':decimal_text(height),'order':index,'col':self.config['layout_columns'],'col_before':0,'col_after':0,'row':index}
        allowed={k:r.boolean(v,True) for k,v in [('query','QueryAllowed'),('insert','InsertAllowed'),('update','UpdateAllowed')]}
        result={'name':original,'key':key,'owner':owner,'item_type':typ['item_type'],'widget':widget,'type_source':typ['type_source'],
                'item_type_property_source':r.provenance.get('itemtype',{}).get('source'), 'inference_reason':typ['reason'] if typ['type_source']=='inferred' else None,
                'data_type':dtype,'representation':representation,'text_keys':text_keys,'canvas':r.read('CanvasName',''),'tab_page':r.read('TabPageName',''),
                'visible':r.boolean('Visible',True),'enabled':r.boolean('Enabled',True),'readonly':widget=='display' or r.boolean('ReadOnly'),
                'allowed_operations':allowed,'validation':validation,'initial_value':initial,'options':options,
                'checkbox':{'checked':checked,'unchecked':unchecked,'other_values':other_values.get(canonical(other),'reject')} if widget=='checkbox' else None,
                'lov':{'name':lov,'endpoint_id':None,'return_mappings':[]} if lov else None,
                'layout':layout,'properties':r.finish()}
        if r.boolean('Iconic') or r.read('IconName',''):
            self.issue('BUTTON_ICON_TODO',owner,'TODO: ellenőrzött Oracle icon → céges ikon megfeleltetés szükséges.',severity='review')
        if widget=='image': self.issue('IMAGE_ADAPTER_TODO',owner,self.widgets['widgets']['image'].get('adapter','TODO: image adapter'),severity='review')
        return result
    def layout(self,items,canvas):
        visible=[i for i in items if i['visible']]
        positioned=[i for i in visible if i['layout']['x'] is not None and i['layout']['y'] is not None]
        if positioned and len(positioned)!=len(visible): self.issue('MIXED_LAYOUT',canvas,'Egy régión belül csak teljes koordinátás vagy teljesen koordináta nélküli folyó elrendezés támogatott.')
        if not positioned:
            if visible: self.issue('FLOW_LAYOUT',canvas,'Nincs koordináta: explicit XML-sorrend, teljes szélesség; pixelértéket nem feltételezünk.',severity='review')
            return {'mode':'xml-flow','grid_columns':self.config['layout_columns'],'source_columns':1}
        if any(i['layout']['width'] is None for i in positioned): self.issue('MISSING_WIDTH',canvas,'Arányos rácshoz minden megjelenő item Width értéke kell.')
        positioned.sort(key=lambda i:(Decimal(i['layout']['y']),Decimal(i['layout']['x']),i['layout']['order']))
        rows=sorted({i['layout']['y'] for i in positioned},key=Decimal)
        minx=min(Decimal(i['layout']['x']) for i in positioned)
        maxx=max(Decimal(i['layout']['x'])+Decimal(i['layout']['width'] or '1') for i in positioned)
        extent=maxx-minx; columns=self.config['layout_columns']; previous={}
        for order,item in enumerate(positioned):
            l=item['layout']; row=rows.index(l['y']); x=Decimal(l['x']); w=Decimal(l['width'] or '1')
            end=previous.get(row,minx)
            if x<end: self.issue('OVERLAPPING_ITEMS',item['owner'],'Átfedő itemek ugyanabban az olvasási sorban; ellenőrzött elrendezés szükséges.')
            before=max(0,int(((x-end)/extent*columns).quantize(Decimal(1),rounding=ROUND_HALF_UP)))
            col=max(1,int((w/extent*columns).quantize(Decimal(1),rounding=ROUND_HALF_UP)))
            l.update({'order':order,'row':row,'col':min(columns,col),'col_before':before})
            previous[row]=x+w
        # Quantization must not wrap a source row into a different row silently.
        for row in range(len(rows)):
            members=[i for i in positioned if i['layout']['row']==row]
            total=sum(i['layout']['col']+i['layout']['col_before'] for i in members)
            while total>columns:
                largest=max(members,key=lambda i:i['layout']['col'])
                if largest['layout']['col']<=1:
                    self.issue('GRID_OVERFLOW',canvas,'Több forrásoszlop, mint layout_columns.'); break
                largest['layout']['col']-=1; total-=1
            if members: members[-1]['layout']['col_after']=max(0,columns-total)
        items.sort(key=lambda i:(i['layout']['row'],i['layout']['order']))
        return {'mode':'relative-grid','grid_columns':columns,'source_columns':max(Counter(i['layout']['row'] for i in positioned).values()),
                'source_extent':decimal_text(extent),'origin_x':decimal_text(minx),'unit':self.coordinate['unit']}
    def run(self):
        root=self.resolution.root
        forms=[e for e in root.iter() if canonical(e.tag)=='formmodule']; form=self.reader(forms[0])
        original=form.read('Name'); title=form.read('Title',original); module_name=output_name({'name':original,'title':title})
        prefix=module_name; self.coordinate=self.coordinates(form)
        canvases=[]; tabs={}; canvas_nodes={}
        for element in forms[0].iter():
            kind=canonical(element.tag)
            if kind=='canvas':
                r=self.reader(element); cname=r.read('Name'); canvas_nodes[cname]=element
                if canonical(r.read('CanvasType','Content')) not in {'content','stacked','tab','horizontaltoolbar','verticaltoolbar'}:
                    self.issue('UNSUPPORTED_CANVAS_TYPE',r.owner,'Ismeretlen CanvasType.')
                canvases.append({'name':cname,'type':r.read('CanvasType','Content'),'visible':r.boolean('Visible',True),
                                 'width':decimal_text(r.decimal('Width')),'height':decimal_text(r.decimal('Height')),'tabs':[],'properties':r.finish()})
                for child in element:
                    if canonical(child.tag)=='tabpage':
                        tr=self.reader(child); tname=tr.read('Name'); tk=self.texts(tr,prefix+'.canvas.'+name(cname)+'.'+name(tname))
                        if not(tk['prompt'] or tk['label']): tk['label']=self.text(prefix+'.canvas.'+name(cname)+'.'+name(tname)+'.label',tname)
                        tabs[(cname,tname)]={'name':tname,'text_keys':tk,'visible':tr.boolean('Visible',True),'enabled':tr.boolean('Enabled',True),'properties':tr.finish()}
                        canvases[-1]['tabs'].append(tabs[(cname,tname)])
        blocks=[]; known_block_keys=set()
        legacy={b['name']:b for b in self.backend.get('blocks',[])}
        for element in forms[0]:
            if canonical(element.tag)!='block': continue
            r=self.reader(element); bname=r.read('Name'); key=name(bname or '')
            if not bname: self.issue('UNNAMED_BLOCK',r.owner,'Block.Name szükséges.')
            if key in known_block_keys: self.issue('IDENTIFIER_COLLISION',r.owner,'Normalizált blokkazonosító ütközik: '+key)
            known_block_keys.add(key)
            source=r.read('QueryDataSourceName',''); db=r.boolean('DatabaseDataBlock',bool(source))
            count=r.integer('NumberOfRecordsDisplayed',1,1)
            mode='control' if not source else ('table' if count>1 else 'form')
            block={'name':bname,'key':key,'mode':mode,'records_displayed':count,'data_source':{'name':source or None,'type':r.read('QueryDataSourceType','Table') if source else None,'database_block':db},
                   'allowed_operations':{k:r.boolean(v,True) for k,v in [('query','QueryAllowed'),('insert','InsertAllowed'),('update','UpdateAllowed'),('delete','DeleteAllowed')]},
                   'items':[],'regions':[],'properties':r.finish()}
            used=set()
            for i,child in enumerate(e for e in element if canonical(e.tag)=='item'):
                item=self.item(child,bname,prefix+'.'+key,i)
                if item['key'] in used: self.issue('IDENTIFIER_COLLISION',item['owner'],'Normalizált mezőazonosító ütközik: '+item['key'])
                used.add(item['key']); block['items'].append(item)
            grouped={}
            for item in block['items']:
                c,t=item['canvas'],item['tab_page']
                if c and c not in canvas_nodes: self.issue('UNKNOWN_CANVAS',item['owner'],'CanvasName nem található a feloldott modulban: '+c)
                if t and (c,t) not in tabs: self.issue('UNKNOWN_TAB_PAGE',item['owner'],'TabPageName nem található a canvason: '+c+'/'+t)
                grouped.setdefault((c,t),[]).append(item)
            for i,((c,t),items) in enumerate(grouped.items()):
                layout=self.layout(items,bname+'/'+c+'/'+t)
                block['regions'].append({'id':key+'Region'+str(i),'canvas':c,'tab_page':t,'items':[item['key'] for item in items],'layout':layout})
            blocks.append(block)
            b=legacy.get(bname,{})
            plan=b.get('endpoint_plan')
            for op,suffix in [('read','list'),('create','create'),('update','update'),('delete','delete')]:
                if not source: continue
                method={'read':'GET','create':'POST','update':'PUT','delete':'DELETE'}[op]
                tail=self.config['endpoint_names'][suffix]; capability='can_'+op
                if plan is not None:
                    # Declare exactly the endpoints the backend generates.
                    if op=='read' and plan.get('search'): method,tail,capability='POST','query/search','can_search'
                    elif not plan.get('list' if op=='read' else op): continue
                self.endpoints.append({'id':key+'_'+op,'kind':'block','block':bname,'item':None,'method':method,
                                       'path':self.config['api_prefix'].rstrip('/')+'/'+self.module+'/'+b.get('key',name(bname,'kebab'))+'/'+tail,
                                       'implemented':bool(b.get(capability)),'declaration_only':False,'sql_file':None,'record_group':None,'query_parameters':[]})
        if self.config['emit_imports'] and any(b['mode']=='table' for b in blocks) and (not self.config['table_import_path'] or not self.config['table_symbol']):
            self.issue('TABLE_IMPORT_REQUIRED',form.owner,'emit_imports=true és p-table esetén table_import_path és table_symbol szükséges; az Optimus importot a host adja meg.')
        self.lovs(forms[0],blocks)
        parents={id(child):parent for parent in forms[0].iter() for child in parent}
        relations=[]; triggers=[]
        for element in forms[0].iter():
            kind=canonical(element.tag); r=self.reader(element)
            if kind=='relation':
                p={k:r.read(k) for k in ['Name','MasterDataBlock','DetailDataBlock','JoinCondition','DeleteRecordBehavior','Deferred','AutoQuery']}
                if not p['MasterDataBlock']:
                    parent=parents.get(id(element))
                    while parent is not None and canonical(parent.tag)!='block': parent=parents.get(id(parent))
                    if parent is not None: p['MasterDataBlock']=self.reader(parent).read('Name')
                relations.append({'name':p['Name'],'master':p['MasterDataBlock'],'detail':p['DetailDataBlock'],'join':p['JoinCondition'],'properties':r.finish()})
                if p['MasterDataBlock'] not in {b['name'] for b in blocks} or p['DetailDataBlock'] not in {b['name'] for b in blocks}: self.issue('INVALID_RELATION',r.owner,'Ismeretlen master/detail blokk.')
                self.issue('RELATION_ACTION_TODO',r.owner,'TODO: master-detail betöltés a host action adapterben; nincs generált LOV/backend-módosítás.',severity='review')
            if kind=='trigger':
                event=r.read('Name',r.read('TriggerName','')); body=r.read('TriggerText',r.read('Text',''))
                text=body.upper(); ui=any(x in text for x in ['GO_ITEM','GO_BLOCK','SET_ITEM_PROPERTY','MESSAGE','SHOW_ALERT'])
                server=any(x in text for x in ['SELECT ','INSERT ','UPDATE ','DELETE ','COMMIT','ROLLBACK',':='])
                target='both' if ui and server else 'frontend' if ui else 'backend' if server else 'manual'
                if text.strip().strip(';')=='NULL': target='none'
                # An inherited body that lives only in an unsupplied OLB is absent,
                # not empty. Classifying it would be classifying a blank string.
                gap=r.record.get('unresolved')
                unresolved=bool(gap) and not body.strip()
                if unresolved: target='manual'
                triggers.append({'id':r.owner,'event':event,'target':target,'source':body,'sha256':hashlib.sha256(body.encode()).hexdigest(),
                                 'inherited_unresolved':unresolved,'properties':r.finish()})
                if unresolved:
                    self.issue('INHERITED_TRIGGER_UNRESOLVED',r.owner,'Az örökölt trigger törzse a hiányzó '+str(gap.get('filename'))+' OLB-ben van; nem generálunk rá kódot és nem találgatjuk a tartalmát.',severity='review',filename=gap.get('filename'))
                elif target!='none': self.issue('TRIGGER_ACTION_TODO',r.owner,'TODO: '+target+' besorolás statikus függőségi jelzés; a trigger nem kerül automatikusan végrehajtásra.',severity='review')
            if kind in NON_UI_ELEMENTS:
                # Counted once per kind below; one review line per object group
                # entry would bury the findings that need a decision.
                self.non_ui[kind]+=1
                continue
            if kind not in SUPPORTED and kind not in {'property','propertyvalue','triggertext','recordgroupquery','programunittext'}:
                self.issue('UNMAPPED_ELEMENT',r.owner,'Nem támogatott XML-elemtípus: '+kind)
            r.finish()
        for kind in sorted(self.non_ui):
            self.issue('NON_UI_ELEMENT',form.owner,kind+': '+str(self.non_ui[kind])+' elem megőrizve, de nem jelenik meg a felületen. '
                                                                                    'A Forms objektumcsoport csak Builder-szintű csoportosítás, nincs UI- vagy üzleti jelentése.',severity='review')
        for gap in self.resolution.gaps:
            where=gap['filename'] or 'nincs megnevezett fájl'
            hint=(' Exportáld: frmf2xml USE_PROPERTY_IDS=NO DUMP=ALL OVERWRITE=YES "'+gap['filename']+'", majd --olb "'+gap['required_export']+'".') if gap['required_export'] else ''
            self.issue('INHERITANCE_UNVERIFIED',form.owner,'Feloldatlan öröklés ('+where+'): '+str(gap['affected_items'])+' item és '
                       +str(gap['affected_triggers'])+' trigger származása nem igazolható. Ok: '+', '.join(gap['reasons'])+'. '
                                                                                                                           'A DUMP=ALL export effektív property-i megmaradnak; a csak a szülőben létező triggertörzsek nem.'+hint,
                       severity='review',**({'filename':gap['filename']} if gap['filename'] else {}))
        self.issues.sort(key=lambda i:(i['owner'],i['code'],i['detail']))
        # Rendering contract travels with the model; renderer never consults XML.
        rendering={k:copy.deepcopy(self.config[k]) for k in ['emit_imports','html_selectors','form_block_structure_type','layout_columns','optimus_import_path','optimus_form_block_symbol','form_block_type_import_path','environment_import_path','table_import_path','table_symbol']}
        rendering['widget_map']=self.widgets; rendering['angular_major']=22
        result={'schema_version':'1.0.0','module':{'name':original,'key':module_name,'class_name':name(module_name,'pascal')+'Component',
                                                   'selector':self.config['angular_selector_prefix']+'-'+name(module_name,'kebab'),'title_key':self.text(prefix+'.title',title),'coordinates':self.coordinate,'properties':form.finish()},
                'canvases':canvases,'blocks':blocks,'relations':relations,'endpoints':self.endpoints,'triggers':triggers,
                'i18n':dict(sorted(self.i18n.items())),'issues':self.issues,'rendering':rendering,
                'inheritance':{'resolved_links':len(self.resolution.resolved_links),
                               'unresolved':copy.deepcopy(self.resolution.gaps)}}
        return result
    def lovs(self,form,blocks):
        lovs={}; groups={}
        for element in form.iter():
            kind=canonical(element.tag)
            if kind in {'lov','recordgroup'}:
                r=self.reader(element); key=r.read('Name'); target=lovs if kind=='lov' else groups
                if key in target: self.issue('DUPLICATE_LOOKUP',r.owner,'Ismétlődő LOV/record group: '+str(key))
                target[key]=element
        for block in blocks:
            for item in block['items']:
                if not item['lov']: continue
                lov=lovs.get(item['lov']['name'])
                if lov is None: self.issue('MISSING_LOV',item['owner'],'LOV nem található: '+item['lov']['name']); continue
                r=self.reader(lov); rg=r.read('RecordGroupName'); group=groups.get(rg)
                if group is None: self.issue('MISSING_RECORD_GROUP',item['owner'],'RecordGroupName nem található: '+str(rg)); continue
                gr=self.reader(group); sql=gr.read('RecordGroupQuery','')
                if not sql.strip(): self.issue('MISSING_RECORD_GROUP_QUERY',item['owner'],'LOV SQL hiányzik.'); continue
                mappings=[]
                for child in lov:
                    if canonical(child.tag)=='lovcolumnmapping':
                        cr=self.reader(child); column=cr.read('ColumnName'); ret=cr.read('ReturnItem')
                        mappings.append({'column':column or '', 'return_item':ret,'title_key':self.text(self.module+'.lov.'+name(rg)+'.column'+str(len(mappings))+'.title',cr.read('Title')), 'display_width':cr.read('DisplayWidth')})
                if any(not mapping['column'] for mapping in mappings): self.issue('MISSING_LOV_COLUMN',item['owner'],'LOVColumnMapping.ColumnName kötelező.')
                if not mappings: self.issue('MISSING_LOV_MAPPING',item['owner'],'LOVColumnMapping nélkül nem ismert a visszatérő érték/oszlop.')
                if len({mapping['return_item'] for mapping in mappings if mapping['return_item']}) != sum(bool(mapping['return_item']) for mapping in mappings):
                    self.issue('DUPLICATE_LOV_RETURN',item['owner'],'Több LOV-oszlop ugyanarra a ReturnItem-re mutat.')
                known={i['owner'] for b in blocks for i in b['items']}
                for mapping in mappings:
                    if mapping['return_item'] and mapping['return_item'] not in known: self.issue('INVALID_LOV_RETURN_ITEM',item['owner'],'ReturnItem nem ismert: '+mapping['return_item'])
                endpoint_id=block['key']+'_'+item['key']+'_lov'
                sql_path='analysis/record-groups/'+name(rg)+'-'+hashlib.sha256(rg.encode()).hexdigest()[:8]+'.sql'
                item['lov'].update({'endpoint_id':endpoint_id,'return_mappings':mappings,'record_group':rg,'query':sql,'sql_file':sql_path})
                self.endpoints.append({'id':endpoint_id,'kind':'lov','block':block['name'],'item':item['name'],'method':'GET',
                                       'path':None,'implemented':False,'declaration_only':True,'sql_file':sql_path,'record_group':rg,
                                       'query_parameters':sorted(set(re.findall(r'(?<!:):([A-Za-z][A-Za-z0-9_$.]*)',sql)))})
                self.issue('LOV_ENDPOINT_TODO',item['owner'],'TODO: a LOV endpoint URL-jét és a query/return mapping adapterét a host adja meg. Az SQL csak analysis fájl.',severity='review')


def build_ui_model(resolution,config,module,backend_model=None):
    with localcontext() as context:
        context.prec=300
        model=Builder(resolution,config,module,backend_model).run()
    from .ui_schema import validate_ui_model
    validate_ui_model(model)
    return model


def require_supported(model):
    if any(i['severity']=='error' for i in model['issues']): raise UiRejected(model)
    if model['module']['coordinates']['system']=='unsupported' or any(i['widget']=='unsupported' for b in model['blocks'] for i in b['items']):
        raise MigrationError('UI_MODEL: unsupported állapot nem generálható akkor sem, ha az issue-listát kézzel eltávolították.')


def write_analysis(model,output):
    write_json(output/'analysis/ui-model.json',model)
    for block in model['blocks']:
        for item in block['items']:
            if item['lov'] and item['lov'].get('sql_file'):
                target=output/item['lov']['sql_file']; target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text(item['lov']['query']+'\n',encoding='utf-8')

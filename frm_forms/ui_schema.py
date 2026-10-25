"""Offline validator for the exact JSON Schema vocabulary shipped with this package.
No downloaded schemas, no optional validation, and unknown schema keywords fail.
"""
import json
import math
import re
from .common import MigrationError
from .ui_config import DATA

KEYWORDS={'$schema','$id','$defs','$ref','type','enum','const','properties','required','additionalProperties','items','anyOf','pattern','minimum'}

def validate_schema(value,schema,root=None,path='$',depth=0):
    if root is None: root=schema
    if depth>128: raise MigrationError('UI_SCHEMA: mélységlimit: '+path)
    if set(schema)-KEYWORDS: raise MigrationError('UI_SCHEMA: nem implementált séma-kulcsszó: '+str(set(schema)-KEYWORDS))
    def fail(message): raise MigrationError('UI_SCHEMA: '+path+': '+message)
    if '$ref' in schema:
        ref=schema['$ref']
        if not ref.startswith('#/$defs/') or ref[8:] not in root['$defs']: fail('ismeretlen helyi sémahivatkozás')
        return validate_schema(value,root['$defs'][ref[8:]],root,path,depth+1)
    if 'anyOf' in schema:
        for choice in schema['anyOf']:
            try: validate_schema(value,choice,root,path,depth+1); break
            except MigrationError: pass
        else: fail('egyik alternatív séma sem teljesül')
    types=schema.get('type',[]); types=[types] if isinstance(types,str) else types
    test={'object':isinstance(value,dict),'array':isinstance(value,list),'string':isinstance(value,str),
          'integer':type(value) is int,'number':type(value) in {int,float} and math.isfinite(value),
          'boolean':type(value) is bool,'null':value is None}
    if types and not any(test.get(t,False) for t in types): fail('elvárt típus: '+str(types))
    if 'enum' in schema and not any(type(value) is type(x) and value==x for x in schema['enum']): fail('nem engedélyezett enum érték: '+str(value))
    if 'const' in schema and (type(value) is not type(schema['const']) or value!=schema['const']): fail('eltérő konstans/verzió')
    if isinstance(value,str) and 'pattern' in schema and not re.search(schema['pattern'],value): fail('hibás azonosító/formátum')
    if 'minimum' in schema and type(value) in {int,float} and value<schema['minimum']: fail('minimum alatti érték')
    if isinstance(value,dict):
        for key in schema.get('required',[]):
            if key not in value: fail('kötelező mező hiányzik: '+key)
        for key,item in value.items():
            spec=schema.get('properties',{}).get(key,schema.get('additionalProperties',True))
            if spec is False: fail('nem engedélyezett mező: '+key)
            if isinstance(spec,dict): validate_schema(item,spec,root,path+'.'+key,depth+1)
    if isinstance(value,list) and 'items' in schema:
        for index,item in enumerate(value): validate_schema(item,schema['items'],root,path+'['+str(index)+']',depth+1)


def validate_ui_model(model):
    schema=json.loads((DATA/'ui-model.schema.json').read_text(encoding='utf-8'))
    validate_schema(model,schema)
    def reject(text): raise MigrationError('UI_MODEL_REFERENCE: '+text)
    endpoints=[e['id'] for e in model['endpoints']]
    if len(endpoints)!=len(set(endpoints)): reject('ismétlődő endpoint azonosító')
    for block in model['blocks']:
        keys=[i['key'] for i in block['items']]
        # Invalid XML is represented by error issues in analysis-only mode.
        if len(keys)!=len(set(keys)) and not any(i['code']=='IDENTIFIER_COLLISION' for i in model['issues']): reject('ismétlődő item kulcs')
        for region in block['regions']:
            if set(region['items'])-set(keys): reject('ismeretlen region item')
        for item in block['items']:
            for key in item['text_keys'].values():
                if key is not None and key not in model['i18n']: reject('hiányzó i18n: '+key)
            if item['lov'] and item['lov'].get('sql_file'):
                if not re.fullmatch(r'analysis/record-groups/[A-Za-z0-9]+-[a-f0-9]{8}\.sql',item['lov']['sql_file']): reject('nem biztonságos SQL fájlnév')
                if item['lov']['endpoint_id'] not in endpoints: reject('hiányzó LOV endpoint')
    rendering=model['rendering']
    for key in ['optimus_import_path','form_block_type_import_path','environment_import_path','table_import_path']:
        if rendering[key] and not re.fullmatch(r'[@A-Za-z0-9_./-]+',rendering[key]): reject('hibás import path')
    if rendering['optimus_form_block_symbol'] and not re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$]*',rendering['optimus_form_block_symbol']): reject('hibás Optimus symbol')
    if rendering['table_symbol'] and not re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$]*',rendering['table_symbol']): reject('hibás table symbol')
    for entry in rendering['widget_map']['widgets'].values():
        if entry['type'] is not None and not re.fullmatch(r'[A-Za-z][A-Za-z0-9]*',entry['type']): reject('hibás widget type')

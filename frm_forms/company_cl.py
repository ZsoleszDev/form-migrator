"""Company CL source format shared with the company DPS/WBS generator.

An AWU identifier opts in; the legacy generator remains usable without one.
Only supplied private contracts are assumed. Unknown transport helpers are
reported explicitly instead of changing the HTTP verb or guessing a method.
"""
from __future__ import annotations

import re
from pathlib import Path

from .common import jstr
from .generate import JAVA_TYPES, row_declarations, write


DTO_IMPORTS = '''import lombok.Data;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.EqualsAndHashCode;
'''
DTO_ANNOTATIONS = '''@Data
@Getter
@NoArgsConstructor
@EqualsAndHashCode(onlyExplicitlyIncluded = true)'''


from .java_imports import cl_package


def company_imports(config, symbols, missing):
    result = []
    for symbol in sorted(set(symbols)):
        if '.' in symbol:
            continue
        from .java_tidy import IMPORT_MAP
        matches = [i for i in config['java_company_imports'] if i.endswith('.' + symbol) or i.endswith('.*')]
        if symbol in IMPORT_MAP:
            matches = [IMPORT_MAP[symbol]]  # java-imports.json is authoritative
        if matches:
            result.extend(matches)
        else:
            missing.add(symbol)  # reported; the developer imports it from the project
    return ''.join('import ' + i + ';\n' for i in dict.fromkeys(result))


def generate(output: Path, config, package, cls, blocks, ops):
    """Emit CL files and pass their exact type/route contract to DPS/WBS generation."""
    folder = output / 'backend' / 'CL'
    missing = set()
    wrappers = {
        'RowResult': [('T', 'row'), ('List<String>', 'messages')],
        'PageResult': [('List<T>', 'rows'), ('List<String>', 'messages')],
        'UpdateRequest': [('T', 'original'), ('T', 'value')],
        'SearchRequest': [('T', 'criteria'), ('int', 'offset'), ('int', 'limit')],
        'LovRequest': [('String', 'term'), ('Map<String, String>', 'parameters'), ('Integer', 'limit')],
        'LovResult': [('List<Map<String, Object>>', 'rows')],
        'ActionRequest': [('Map<String, Map<String, String>>', 'blocks'), ('Map<String, String>', 'parameters')],
        'ActionResult': [('Map<String, Map<String, String>>', 'blocks'), ('List<String>', 'messages'),
                         ('List<List<String>>', 'commands'), ('Map<String, String>', 'globals')],
        'QueryActionRequest': [('Map<String, Map<String, String>>', 'blocks'), ('Map<String, String>', 'parameters'),
                               ('int', 'offset'), ('int', 'limit')],
    }
    commit = next((o for o in ops if o['op'] == 'commit'), None)
    if commit:
        # Forms COMMIT_FORM (commit_chain): one changes/result field per block.
        from .commit_chain import field
        wrappers['BlockChanges'] = [('List<T>', 'inserted'), ('List<UpdateRequest<T>>', 'updated'), ('List<T>', 'deleted')]
        from .commit_chain import request_fields
        wrappers['CommitRequest'] = request_fields(commit)
        wrappers['CommitResult'] = ([('Map<String, Map<String, String>>', 'blocks'), ('List<String>', 'messages'),
                                     ('List<List<String>>', 'commands'), ('Map<String, String>', 'globals')]
                                    + [(f"List<{b['class']}Row>", 'rows' + field(b)) for b in commit['blocks']])
    used = ' '.join(o['returns'] + ' ' + o['args'] for o in ops)
    needed = {key for key in wrappers if re.search(r'\b' + key + r'\b', used)}
    if any(o['op'] == 'update' for o in ops) or commit:
        needed.add('UpdateRequest')
    if commit:
        needed.add('BlockChanges')
    names = {key: cls + key + 'Dto' for key in sorted(needed)}
    allocated = set(names.values())
    for b in blocks:
        candidates = [(b['class'] + 'Row', cls + b['class'])]
        if b['endpoint_plan'].get('search'):
            candidates.append((b['class'] + 'Criteria', cls + b['class'] + 'Filter'))
        for source, stem in candidates:
            candidate, index = stem + 'Dto', 2
            while candidate in allocated:
                candidate = stem + str(index) + 'Dto'
                index += 1
            allocated.add(candidate)
            names[source] = candidate

    def remap(value):
        return re.sub(r'\b[A-Za-z_$][A-Za-z0-9_$]*\b', lambda m: names.get(m[0], m[0]), value)

    def imports_for(symbols):
        return company_imports(config, symbols, missing)

    def emit(class_name, imports, body):
        write(folder / (class_name + '.java'), f'package {cl_package(config, package)};\n\n{imports}\n{body}\n')

    def dto(class_name, fields, declarations=None, generic=''):
        imports = DTO_IMPORTS
        types = ' '.join(t for t, _ in fields)
        imports += ''.join('import java.util.' + t + ';\n' for t in ('List', 'Map') if re.search(r'\b' + t + r'\b', types))
        declarations = declarations if declarations is not None else '\n'.join(f'    private {t} {n};' for t, n in fields)
        constructor = ''
        if fields:
            arguments = ', '.join(f'{t} {n}' for t, n in fields)
            assignments = '\n'.join(f'        this.{n} = {n};' for _, n in fields)
            constructor = f'\n\n    public {class_name}({arguments}) {{\n{assignments}\n    }}'
        emit(class_name, imports, f'{DTO_ANNOTATIONS}\npublic class {class_name}{generic} {{\n\n{declarations}{constructor}\n\n}}')

    from .compact_backend import row_fields
    for b in blocks:
        fields = row_fields(b)  # the ROWID of a block without primary key is part of the record too
        dto(names[b['class'] + 'Row'], [(JAVA_TYPES[i['type']], i['field']) for i in fields],
            row_declarations(fields).replace('    public ', '    private '))
        if b['endpoint_plan'].get('search'):
            fields = [v['item'] for v in b['query_plan']['binds']]
            dto(names[b['class'] + 'Criteria'], [(JAVA_TYPES[i['type']], i['field']) for i in fields],
                row_declarations(fields).replace('    public ', '    private '))
    for key in sorted(needed):
        fields = [(remap(t), n) for t, n in wrappers[key]]  # Row and wrapper names of the company DTOs
        dto(names[key], fields, generic='<T>' if key in {'RowResult', 'PageResult', 'UpdateRequest', 'SearchRequest', 'BlockChanges'} else '')
    (folder / (cls + 'Dtos.java')).unlink()

    constants = [f'    public static final String NAME = WebMenuLeaf.Path.AWU_{config["AWU_AZON"]};',
                 f'    public static final String PATH = WebMenuLeaf.FullPath.AWU_{config["AWU_AZON"]};',
                 '    public static final String AUTH = ConstantsBase.AUTH_HAS_ACCESS_PREFIX + PATH + ConstantsBase.AUTH_HAS_ACCESS_SUFFIX;']
    methods, pointers, implementations, endpoints = [], [], [], []
    user_type = config['java_user_type']
    for op in ops:
        method = op['method']
        name_constant = op['name_constant']
        path_constant = name_constant.removesuffix('_NAME') + '_PATH'
        operation_name = op.get('api_name', method.lower())
        constants += ['', f'    public static final String {name_constant} = {jstr(operation_name)};',
                      f'    public static final String {path_constant} = ConstantsBase.PD + {name_constant};']
        returns = f'RestResponseDto<{remap(op["returns"])}>'
        arguments = user_type + ' user' + (', ' + remap(op['args']) if op['args'] else '')
        signature = f'ResponseEntity<{returns}> {method}({arguments})'
        methods.append('    ' + signature + ';')
        pointer = 'ptrResponse' + method[0].upper() + method[1:]
        pointers.append(f'''    public ParameterizedTypeReference<{returns}> {pointer}() {{
        return new ParameterizedTypeReference<>() {{
        }};
    }}''')
        uri = f'getModulePath() + {cls}Constants.{path_constant}'
        helper = config['java_cl_http_helpers'][op['http'].upper()]
        if not helper:
            message = f'{op["http"].upper()} helper nincs beállítva: java_cl_http_helpers.{op["http"].upper()}'
            body = '        throw new UnsupportedOperationException(' + jstr(message) + ');'
        elif op['http'] == 'Get':
            if op['op'] == 'list':
                uri += ' + "?offset=" + offset + "&limit=" + limit'
            body = f'        return {helper}({uri}, user, {pointer}());'
        else:
            payload = f'new {names["UpdateRequest"]}<>(original, value)' if op['op'] == 'update' else op['call']
            body = f'        return {helper}({uri},\n            getHttpEntity({payload}, user), {pointer}());'
        implementations.append(f'    @Override\n    public {signature} {{\n{body}\n    }}')
        endpoints.append({'method': method, 'http': op['http'].upper(), 'name_constant': name_constant,
                          'path_constant': path_constant, 'relative_path': '/' + operation_name,
                          'legacy_relative_path': op['path'], 'return_type': returns,
                          'transport_helper': helper or None, 'transport_ready': bool(helper)})
    emit(cls + 'Constants', 'import lombok.AccessLevel;\nimport lombok.NoArgsConstructor;\n' +
         imports_for(['WebMenuLeaf', 'ConstantsBase']),
         '@NoArgsConstructor(access = AccessLevel.PRIVATE)\npublic class ' + cls + 'Constants {\n' + '\n'.join(constants) + '\n}')
    common = 'import java.util.List;\nimport org.springframework.http.ResponseEntity;\n' + imports_for(['RestResponseDto', user_type])
    emit(cls + 'RestClient', common + imports_for(['RestClient']),
         f'public interface {cls}RestClient extends RestClient {{\n\n' + '\n\n'.join(methods) + '''

}''')
    emit(cls + 'RestClientImpl', common + 'import org.springframework.core.ParameterizedTypeReference;\nimport org.springframework.stereotype.Component;\n' +
         imports_for(['DataProviderServiceRestClientBase']),
         f'@Component\npublic class {cls}RestClientImpl extends DataProviderServiceRestClientBase implements {cls}RestClient {{\n\n' +
         '\n\n'.join(pointers) + f'\n\n    public String getModulePath() {{\n        return {cls}Constants.PATH;\n    }}\n\n' +
         '\n\n'.join(implementations) + '\n\n}')
    return {'style': 'company-cl-v1', 'AWU_AZON': config['AWU_AZON'],
            'missing_company_imports': sorted(missing), 'dto_types': names, 'endpoints': endpoints}

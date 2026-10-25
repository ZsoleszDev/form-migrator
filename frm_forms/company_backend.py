"""Company controller/service hierarchy; database logic stays in DPS ServiceImpl."""
import re

from .common import jstr, write_json
from .company_cl import company_imports
from .generate import write
from .java_dto import bean_source


def generate(output, config, package, cls, blocks, ops, contract):
    from .java_imports import cl_package, layer_package
    cl = cl_package(config, package)  # the module's CL package (cl_package setting)
    names = contract['dto_types']
    missing = set(contract['missing_company_imports'])
    user_type = config['java_user_type']
    contracts = {o['method']: o for o in contract['endpoints']}

    def types(text):
        return re.sub(r'\b[A-Za-z_$][A-Za-z0-9_$]*\b', lambda m: names.get(m[0], m[0]), text)

    def imports(symbols):
        return company_imports(config, symbols, missing)

    dto_imports = ''.join(f'import {cl}.{value};\n' for value in sorted(set(names.values())))
    constants_import = f'import {cl}.{cls}Constants;\n'
    payload_imports = dto_imports + 'import java.util.List;\n' + imports([user_type])
    response_imports = payload_imports + 'import org.springframework.http.ResponseEntity;\n' + imports(['RestResponseDto'])
    logging = 'import lombok.extern.slf4j.XSlf4j;\n'
    mapping_imports = 'import org.springframework.web.bind.annotation.*;\n'
    # HttpServletRequest: jakarta or javax, depending on the project - the developer imports it.
    security_imports = 'import org.springframework.security.core.Authentication;\n'

    def emit(layer, suffix, body, extra):
        write(output / 'backend' / layer / (cls + suffix + '.java'),
              f'package {layer_package(config, package, layer)};\n\n{extra}\n{body}\n')

    # Convert only Java identifiers/accesses. SQL, text blocks and comments are masked.
    service_file = output / 'backend' / 'DPS' / (cls + 'ServiceImpl.java')
    source = service_file.read_text(encoding='utf-8')
    # The compact <Module>Dtos imports (wildcard, or single-type after the Checkstyle layout) give way to
    # the separate CL DTO classes.
    source = re.sub(rf'^import {re.escape(cl)}\.{re.escape(cls)}Dtos(?:\.\*|\.\w+);\n', '', source, flags=re.M)
    anchor = re.search(r'^import ', source, re.M) or re.search(r'^package [^\n]*\n\n?', source, re.M)
    at = (anchor.start() if anchor.group().startswith('import') else anchor.end()) if anchor else 0
    source = source[:at] + dto_imports + source[at:]
    source = bean_source(source, names, blocks)
    # The DPS ServiceImpl extends the module's own ServiceBase (getModuleName); the company base class and its
    # imports move there, the layout step drops the imports the ServiceImpl no longer uses.
    source, declarations = re.subn(rf'^public class {cls}ServiceImpl extends [^{{\n]+? implements {cls}Service \{{',
                                   f'public class {cls}ServiceImpl extends {cls}ServiceBase {{', source, count=1, flags=re.M)
    if not declarations:
        raise ValueError('DPS ServiceImpl: az osztálydeklaráció nem a várt alakú.')
    required = re.findall(r'[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*', config['java_service_base_dps'])
    for line in imports([user_type]).splitlines():
        if line and line not in source:
            source = source.replace('\n\n', '\n\n' + line + '\n', 1)
    write(service_file, source)
    emit('DPS', 'ServiceBase', f'''public abstract class {cls}ServiceBase extends {config['java_service_base_dps']} implements {cls}Service {{

    @Override
    public String getModuleName() {{
        return {cls}Constants.NAME;
    }}

}}''', constants_import + imports(required))

    dps_interface, dps_impl, service_methods = [], [], []
    wbs_interface, wbs_impl, wbs_service, wbs_calls = [], [], [], []
    for op in ops:
        endpoint = contracts[op['method']]
        method = op['method']
        payload = types(op['returns'])
        returns = f'ResponseEntity<RestResponseDto<{payload}>>'
        args = types(op['args'])
        service_args = user_type + ' user' + (', ' + args if args else '')
        # Controller update payload is one object; service still receives original/value.
        controller_args = types(op['controller_args'])
        plain_args = re.sub(r'@\w+(?:\([^)]*\))?\s*', '', controller_args)
        dps_args = user_type + ' user' + (', ' + plain_args if plain_args else '')
        wbs_args = 'Authentication authentication, HttpServletRequest request' + (', ' + plain_args if plain_args else '')
        # The update DTO cannot also be named request in WBS: HttpServletRequest owns that name.
        if op['op'] == 'update':
            wbs_args = wbs_args.removesuffix(' request') + ' updateRequest'
        dps_interface.append(f'    {returns} {method}({dps_args}) throws Exception;')
        wbs_interface.append(f'    {returns} {method}({wbs_args}) throws Exception;')
        service_methods.append(f'    {payload} {method}({service_args}) throws Exception;')
        wbs_service.append(f'    {returns} {method}({service_args}) throws Exception;')

        # Avoid the same request name collision for search/LOV/action DTOs as well.
        wbs_controller_args = controller_args
        if re.search(r'\brequest$', plain_args):
            body_arg = 'updateRequest' if op['op'] == 'update' else 'payload'
            wbs_controller_args = re.sub(r'\brequest$', body_arg, controller_args)
            if op['op'] != 'update':
                wbs_interface[-1] = wbs_interface[-1].replace(plain_args + ')', re.sub(r'\brequest$', body_arg, plain_args) + ')')
        else:
            body_arg = ''
        dps_call = 'request.getOriginal(), request.getValue()' if op['op'] == 'update' else op['call']
        wbs_call = 'updateRequest.getOriginal(), updateRequest.getValue()' if op['op'] == 'update' else body_arg or op['call']
        dps_action = f'RestHelper.successResponse(service.{method}(user{", " + dps_call if dps_call else ""}))'
        if op['op'] == 'update':
            dps_action = ('{\n                if (request == null) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Hiányzó kérés.");\n'
                          '                return ' + dps_action + ';\n            }')
        elif op['op'] == 'create':
            dps_action = ('{\n                var response = ' + dps_action + ';\n'
                                                                              '                return ResponseEntity.status(HttpStatus.CREATED).headers(response.getHeaders()).body(response.getBody());\n            }')
        dps_parameters = f'@RequestHeader(ConstantsBase.USER_REQUEST_HEADER_PARAM) {user_type} user'
        if controller_args:
            dps_parameters += ',\n        ' + controller_args
        mapping = f'    @{op["http"]}Mapping({cls}Constants.{endpoint["path_constant"]})'
        dps_impl.append(f'''    @Override
{mapping}
    public {returns} {method}(
        {dps_parameters}
    ) throws Exception {{
        return log1x(log, {cls}Constants.{op['name_constant']}, user,
            null, () -> {dps_action});
    }}''')
        wbs_parameters = 'Authentication authentication, HttpServletRequest request'
        if wbs_controller_args:
            wbs_parameters += ',\n        ' + wbs_controller_args
        null_check = ('                    if (updateRequest == null) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Hiányzó kérés.");\n'
                      if op['op'] == 'update' else '')
        wbs_impl.append(f'''    @Override
{mapping}
    public {returns} {method}(
        {wbs_parameters}
    ) {{
        {user_type} user = WbsUserParcel.getUser(authentication, request);
        return log0x(log, {cls}Constants.{op['name_constant']}, user,
            null, () -> {{
                try {{
{null_check}                    return service.{method}(user{', ' + wbs_call if wbs_call else ''});
                }} catch (RuntimeException e) {{
                    throw e;
                }} catch (Exception e) {{
                    throw new RuntimeException(e);
                }}
            }});
    }}''')
        block = ((op['action']['block'] or '@FORM') if op['op'] == 'action' else op['lov']['block'] if op['op'] == 'lov'
                 else '@FORM' if op['op'] == 'commit' else op['block']['name'])
        wbs_calls.append(f'''    @Override
    public {returns} {method}({service_args}) {{
        return log0x(log, {cls}Constants.{op['name_constant']}, user,
            null, () -> {{
                return getResponseDto(restClient.{method}(user{', ' + op['call'] if op['call'] else ''}), null);
            }});
    }}''')
        # From here every route consumer uses the CL operation names and paths.
        op['constant'], op['path'] = endpoint['path_constant'], endpoint['relative_path']

    join = lambda methods: '\n\n'.join(methods)
    emit('DPS', 'Controller', f'''public interface {cls}Controller<S extends ModuleService> extends ModuleController<S> {{

{join(dps_interface)}

}}''', response_imports + imports(['ModuleService', 'ModuleController']))
    emit('DPS', 'ControllerBase', f'''abstract class {cls}ControllerBase<
    S extends ModuleService
    > extends ModuleControllerBase<DpsLogHelper, S> implements {cls}Controller<S> {{

    @Override
    public String getModulePath() {{
        return {cls}Constants.PATH;
    }}

}}''', constants_import + imports(['ModuleService', 'ModuleControllerBase', 'DpsLogHelper']))
    emit('DPS', 'ControllerImpl', f'''@XSlf4j
@RestController
@RequestMapping(DPSConstants.SERVICE_API_VERSIONED_PATH + {cls}Constants.PATH)
public class {cls}ControllerImpl extends {cls}ControllerBase<{cls}ServiceImpl> {{

{join(dps_impl)}

}}''', response_imports + constants_import + logging + mapping_imports +
         'import org.springframework.http.HttpStatus;\nimport org.springframework.web.server.ResponseStatusException;\n' +
         imports(['DPSConstants', 'ConstantsBase', 'RestHelper']))
    emit('DPS', 'Service', f'''public interface {cls}Service extends ModuleService {{

{join(service_methods)}

}}''', payload_imports + imports(['ModuleService']))

    emit('WBS', 'Controller', f'''public interface {cls}Controller<S extends WbsService> extends WbsController<S> {{

{join(wbs_interface)}

}}''', response_imports + security_imports + imports(['WbsService', 'WbsController']))
    emit('WBS', 'ControllerBase', f'''abstract class {cls}ControllerBase<S extends WbsService> extends WbsControllerBase<S> implements {cls}Controller<S> {{

    @Override
    public String getModulePath() {{
        return {cls}Constants.NAME;
    }}

}}''', constants_import + imports(['WbsService', 'WbsControllerBase']))
    emit('WBS', 'ControllerImpl', f'''@XSlf4j
@RestController
@DependsOn("wbsInfoService")
@PreAuthorize(ConstantsBase.AUTH_HAS_GLOBAL_ACCESS)
@RequestMapping(WbsControllerBase.SERVICE_API_VERSIONED_PATH + {cls}Constants.PATH)
public class {cls}ControllerImpl extends {cls}ControllerBase<{cls}ServiceImpl> {{

{join(wbs_impl)}

}}''', response_imports + security_imports + constants_import + logging + mapping_imports +
         'import org.springframework.context.annotation.DependsOn;\nimport org.springframework.security.access.prepost.PreAuthorize;\n'
         'import org.springframework.http.HttpStatus;\nimport org.springframework.web.server.ResponseStatusException;\n' +
         imports(['WbsControllerBase', 'ConstantsBase', 'WbsUserParcel']))
    emit('WBS', 'Service', f'''public interface {cls}Service<
    R extends RestClient
    > extends WbsService<R> {{

{join(wbs_service)}

}}''', response_imports + imports(['RestClient', 'WbsService']))
    emit('WBS', 'ServiceBase', f'''abstract class {cls}ServiceBase<
    R extends RestClient
    > extends WbsServiceBase<R> implements {cls}Service<R> {{

    @Override
    public String getModuleName() {{
        return {cls}Constants.NAME;
    }}

}}''', constants_import + imports(['RestClient', 'WbsServiceBase']))
    emit('WBS', 'ServiceImpl', f'''@XSlf4j
@Service
public class {cls}ServiceImpl extends {cls}ServiceBase<{cls}RestClientImpl> {{

{join(wbs_calls)}

}}''', response_imports + constants_import + logging + 'import org.springframework.stereotype.Service;\n' +
         f'import {cl}.{cls}RestClientImpl;\n')

    contract.update(version=2, backend_style='company-backend-v1', backend_contract_ready=True,
                    integration_ready=False, pending_layers=['frontend API response envelope and resolved WBS module path'],
                    missing_company_imports=sorted(missing),
                    host_configuration_ready=not missing and all(e['transport_ready'] for e in contract['endpoints']),
                    dps_base_path_expression='DPSConstants.SERVICE_API_VERSIONED_PATH + ' + cls + 'Constants.PATH',
                    wbs_base_path_expression='WbsControllerBase.SERVICE_API_VERSIONED_PATH + ' + cls + 'Constants.PATH')
    write_json(output / 'analysis/cl-contract.json', contract)
    write(output / 'CL_INTEGRATION.md', f'''# Céges CL/DPS/WBS

AWU_AZON: `{config['AWU_AZON']}`. A CL és a DPS/WBS szerződése egymáshoz illesztett.
A DPS: ModuleController/ModuleService, külön ControllerBase, UserDto request header,
log1x és RestHelper.successResponse. A WBS: WbsController/WbsService, ControllerBase
és ServiceBase, WbsUserParcel, log0x és getResponseDto. A RestResponseDto egyszer,
a DPS controllerben készül; a WBS a céges válaszkezelést használja.

A SQL/PLSQL közvetlenül a DPS ServiceImpl-ben marad; a privát DTO-mezőkhöz getter/setter
hívások készülnek. A tranzakciók és a validációk megmaradnak. Jogosultságot a generált kód nem
ellenőriz: a hozzáférést a projekt saját jogosultságkezelése szabályozza.
A ServiceImpl/ControllerImpl CREATE_ONCE: régi kézi fájlokat a generátor nem ír felül.

Hiányzó céges importok (`java_company_imports`): {', '.join(sorted(missing)) or 'nincs'}.
A PUT/DELETE segédnevek a `java_cl_http_helpers` beállításban adhatók meg; amíg üresek,
az érintett kliensmetódusok kifejezett kivételt dobnak. Ezek listája: `analysis/cl-contract.json`.
A céges alaposztályok szolgáltatás-/kliensinjektálását, naplózását és URL-kezelését használjuk.

A frontendhez a `RestResponseDto` tényleges JSON-mezői és a WBS menüútvonalának értéke
még nem ismert. A `--screen` képernyő alapból `events` módban készül; a hostadapternek a riportban
megadott relatív műveletútvonalakat kell meghívnia és kibontania a céges válaszburkolót.
Az `integration_ready: false` erre és a hostillesztésre utal, nem hiányzó DPS/WBS osztályokra.
''')
    return contract

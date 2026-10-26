"""Cross-layer contracts and lossless conversion of generated DPS DTO accesses."""
from java_support import trigger_mode
import json
import re
import unittest
import xml.etree.ElementTree as ET

import test_company_cl as cl
import test_compact_backend as compact
import test_forms_runtime as runtime
from frm_forms.common import MigrationError
from frm_forms.java_dto import bean_source
from frm_forms.service_inline import JAVA_NONCODE, mask
import os

setUpModule, tearDownModule = trigger_mode('plsql')


os.environ.setdefault('FRM_JAVA_IMPORT_MAP', '-')  # tests never read a developer's own java-imports.json
os.environ.setdefault('FRM_JAVA_VARIABLE_MAP', '-')  # nor a developer's own java-variables.json


def signature(source, method):
    # A declaration, not a call inside another method body (e.g. the commit chain calls createB).
    pattern = r'(?m)^    (?:public )?(\S[^\n]*?)\s+' + re.escape(method) + r'\(([\s\S]*?)\)\s*(?:throws Exception\s*)?[;{]'
    matches = list(re.finditer(pattern, source))
    if len(matches) != 1:
        raise AssertionError((method, len(matches)))
    returns, args = matches[0].groups()
    args = re.sub(r'@\w+(?:\([^)]*\))?\s*', '', args)
    # Generic types may have commas: split only at the outer parameter level.
    fields, begin, depth = [], 0, 0
    for index, char in enumerate(args + ','):
        if char == '<': depth += 1
        elif char == '>': depth -= 1
        elif char == ',' and depth == 0:
            arg = args[begin:index].strip()
            if arg:
                fields.append(tuple(arg.rsplit(None, 1)))
            begin = index + 1
    return returns, fields


class CompanyBackendTests(unittest.TestCase):
    setUp = cl.CompanyClTests.setUp
    generate = cl.CompanyClTests.generate
    report = cl.CompanyClTests.report

    def source(self, out, layer, suffix):
        return (out / 'backend' / layer / ('Rt' + suffix + '.java')).read_text()

    def test_dps_service_impl_extends_the_module_service_base(self):
        out, _ = self.generate()
        base = self.source(out, 'DPS', 'ServiceBase')
        self.assertIn('public abstract class RtServiceBase extends ModuleServiceBase<DpsLogHelper> implements RtService {', base)
        self.assertIn('public String getModuleName() {\n        return RtConstants.NAME;', base)
        self.assertRegex(base, r'import [\w.]+\.ModuleServiceBase;')
        self.assertRegex(base, r'import [\w.]+\.DpsLogHelper;')
        service = self.source(out, 'DPS', 'ServiceImpl')
        self.assertIn('@XSlf4j\n@Service\npublic class RtServiceImpl extends RtServiceBase {', service)
        self.assertNotRegex(service, r'import [\w.]+\.ModuleServiceBase;')  # moved to the base
        plan = json.loads((out / 'analysis/backend-plan.json').read_text())
        self.assertIn('RtServiceBase.java', plan['files']['DPS'])

    def test_dps_hierarchy_and_request_header_contract(self):
        out, _ = self.generate()
        interface = self.source(out, 'DPS', 'Controller')
        base = self.source(out, 'DPS', 'ControllerBase')
        controller = self.source(out, 'DPS', 'ControllerImpl')
        self.assertIn('RtController<S extends ModuleService> extends ModuleController<S>', interface)
        self.assertNotIn('@GetMapping', interface)
        self.assertIn('extends ModuleControllerBase<DpsLogHelper, S> implements RtController<S>', base)
        self.assertIn('return RtConstants.PATH;', base)
        self.assertIn('@RequestMapping(DPSConstants.SERVICE_API_VERSIONED_PATH + RtConstants.PATH)', controller)
        self.assertIn('extends RtControllerBase<RtServiceImpl>', controller)
        count = len(self.report(out)['endpoints'])
        self.assertEqual(controller.count('@RequestHeader(ConstantsBase.USER_REQUEST_HEADER_PARAM) UserDto user'), count)
        self.assertEqual(controller.count('RestHelper.successResponse(service.'), count)
        self.assertEqual(controller.count('return log1x(log,'), count)
        self.assertNotIn('getUser()', controller)
        self.assertIn('interface RtService extends ModuleService', self.source(out, 'DPS', 'Service'))
        self.assertNotIn('RestResponseDto', self.source(out, 'DPS', 'Service'))

    def test_wbs_hierarchy_security_and_single_response_wrapping(self):
        out, _ = self.generate()
        controller = self.source(out, 'WBS', 'ControllerImpl')
        self.assertIn('RtController<S extends WbsService> extends WbsController<S>', self.source(out, 'WBS', 'Controller'))
        base = self.source(out, 'WBS', 'ControllerBase')
        self.assertIn('extends WbsControllerBase<S> implements RtController<S>', base)
        self.assertIn('return RtConstants.NAME;', base)
        for annotation in ('@XSlf4j', '@RestController', '@DependsOn("wbsInfoService")',
                           '@PreAuthorize(ConstantsBase.AUTH_HAS_GLOBAL_ACCESS)',
                           '@RequestMapping(WbsControllerBase.SERVICE_API_VERSIONED_PATH + RtConstants.PATH)'):
            self.assertIn(annotation, controller)
        self.assertIn('UserDto user = WbsUserParcel.getUser(authentication, request);', controller)
        self.assertIn('extends WbsService<R>', self.source(out, 'WBS', 'Service'))
        base = self.source(out, 'WBS', 'ServiceBase')
        self.assertIn('extends WbsServiceBase<R> implements RtService<R>', base)
        self.assertIn('public String getModuleName()', base)
        self.assertIn('return RtConstants.NAME;', base)
        service = self.source(out, 'WBS', 'ServiceImpl')
        self.assertIn('extends RtServiceBase<RtRestClientImpl>', service)
        count = len(self.report(out)['endpoints'])
        self.assertEqual(service.count('return getResponseDto(restClient.'), count)
        self.assertEqual(service.count('return log0x(log,'), count)
        self.assertEqual(controller.count('return log0x(log,'), count)
        self.assertNotIn('RestHelper.successResponse', controller + service)
        self.assertNotIn('RestClient.Builder', service)
        self.assertNotIn('new RtRestClientImpl', service)
        self.assertIn('catch (RuntimeException e) { throw e;', ' '.join(controller.split()))

    def test_all_signatures_and_routes_match_across_cl_dps_and_wbs(self):
        for label, raw in [('crud', runtime.fixture()), ('search_lov', compact.multiple_blocks())]:
            with self.subTest(label=label):
                out, _ = self.generate(label, raw=raw)
                report = self.report(out)
                constants = self.source(out, 'CL', 'Constants')
                for endpoint in report['endpoints']:
                    method = endpoint['method']
                    client = signature(self.source(out, 'CL', 'RestClient'), method)
                    self.assertEqual(client, signature(self.source(out, 'WBS', 'Service'), method))
                    self.assertEqual(client, signature(self.source(out, 'WBS', 'ServiceImpl'), method))
                    for layer in ('DPS', 'WBS'):
                        declared = signature(self.source(out, layer, 'Controller'), method)
                        implemented = signature(self.source(out, layer, 'ControllerImpl'), method)
                        self.assertEqual(declared, implemented, (layer, method))
                        self.assertEqual(declared[0], client[0])
                        parameter_names = [name for _, name in declared[1]]
                        self.assertEqual(len(set(parameter_names)), len(parameter_names), (layer, method))
                        mapping = '@' + endpoint['http'].title() + 'Mapping(RtConstants.' + endpoint['path_constant'] + ')'
                        self.assertIn(mapping, self.source(out, layer, 'ControllerImpl'))
                    dps = signature(self.source(out, 'DPS', 'Service'), method)
                    self.assertEqual(dps, signature(self.source(out, 'DPS', 'ServiceImpl'), method))
                    self.assertEqual(dps[1], client[1])
                    self.assertEqual('ResponseEntity<RestResponseDto<' + dps[0] + '>>', client[0])
                    self.assertIn('String ' + endpoint['path_constant'] + ' = ', constants)

    def test_update_uses_one_body_dto_and_preserves_original_and_value(self):
        out, _ = self.generate()
        dps = self.source(out, 'DPS', 'ControllerImpl')
        wbs = self.source(out, 'WBS', 'ControllerImpl')
        self.assertIn('@RequestBody RtUpdateRequestDto<RtBDto> request', dps)
        self.assertIn('service.updateB(user, request.getOriginal(), request.getValue())', dps)
        self.assertIn('@RequestBody RtUpdateRequestDto<RtBDto> updateRequest', wbs)
        self.assertIn('service.updateB(user, updateRequest.getOriginal(), updateRequest.getValue())', wbs)
        self.assertIn('if (updateRequest == null)', wbs)
        self.assertIn('HttpStatus.CREATED).headers(response.getHeaders()).body(response.getBody())', dps)

    def test_dps_getters_setters_and_transactions_are_retained_without_access_hook(self):
        out, _ = self.generate(config={'backend_live': True})
        source = self.source(out, 'DPS', 'ServiceImpl')
        visible = mask(source)
        for code in ('row.setId(', 'row.getId()', 'row.setName(', 'row.getName()', 'row.setD(',
                     'request.getBlocks()', 'request.getParameters()',
                     'NamedParameterJdbcTemplate jdbc', 'FormsChecks.throwIfAny(errors)', '@Transactional('):
            self.assertIn(code, source)
        self.assertNotRegex(visible, r'\b(?:row|original|current|key)\.(?:id|name|d)\b')
        self.assertNotIn('RtDtos.*', source)
        self.assertNotIn(' BRow ', source)
        # Authorization is the project's own concern: no Access bean, no check calls.
        for layer in ('DPS', 'WBS'):
            self.assertNotIn('access', self.source(out, layer, 'ServiceImpl').lower().replace('dataaccess', ''))
        self.assertNotIn('interface Access', self.source(out, 'CL', 'RestClient'))

    def test_all_sql_text_and_comments_survive_for_multiple_blocks_and_lovs(self):
        raw = compact.multiple_blocks()
        legacy, _ = self.generate('legacy', config={'AWU_AZON': ''}, raw=raw)
        company, _ = self.generate('company', raw=raw)
        def merged(text):  # long SQL is wrapped by line length; the concatenated value must survive
            parts = []
            for token in JAVA_NONCODE.findall(text):
                if token.startswith('"') and parts and parts[-1].startswith('"'):
                    parts[-1] = parts[-1][:-1] + token[1:]
                else:
                    parts.append(token)
            return parts
        self.assertEqual(merged(self.source(legacy, 'DPS', 'ServiceImpl')), merged(self.source(company, 'DPS', 'ServiceImpl')))
        self.assertIn('criteria.getFiltersId()', self.source(company, 'DPS', 'ServiceImpl'))
        self.assertIn('request.getLimit()', self.source(company, 'DPS', 'ServiceImpl'))

    def test_old_cl_only_regeneration_is_rejected_and_current_style_preserves_custom_code(self):
        out, _ = self.generate()
        service = out / 'backend/DPS/RtServiceImpl.java'
        marker = '\n// Host customisation remains here.\n'
        service.write_text(service.read_text() + marker)
        self.generate(extra=('--regenerate',))
        self.assertTrue(service.read_text().endswith(marker))
        contract = out / 'analysis/cl-contract.json'
        old = self.report(out)
        old.pop('backend_style')
        contract.write_text(json.dumps(old))
        _, error = self.generate(extra=('--regenerate',), expected=1)
        self.assertIn('DPS/WBS szerződése megváltozott', error)
        self.assertTrue(service.read_text().endswith(marker))

    def test_reports_and_frontend_use_relative_company_routes_and_explicit_adapter(self):
        out, _ = self.generate(raw=compact.multiple_blocks())
        plan = json.loads((out / 'analysis/backend-plan.json').read_text())
        report = self.report(out)
        self.assertTrue(plan['backend_contract_ready'])
        self.assertTrue(plan['api']['requires_host_adapter'])
        self.assertEqual(plan['api']['base_path'], '')
        self.assertEqual(plan['api']['response_envelope'], 'RestResponseDto')
        self.assertIn('RtControllerBase.java', plan['files']['DPS'])
        self.assertIn('RtServiceBase.java', plan['files']['WBS'])
        for endpoint in report['endpoints']:
            self.assertEqual(plan['api']['paths'][endpoint['path_constant']], endpoint['relative_path'])
        for path in (out / 'frontend').rglob('*.ts'):
            source = path.read_text()
            if 'backendMode:' in source:
                self.assertIn("backendMode: 'http' | 'events' = 'events'", source)
                self.assertNotIn('/api/forms/rt/', source)
        handoff = json.loads((out / 'analysis/backend-handoff.json').read_text())
        for query in handoff['queries']:
            self.assertIsNone(query['path'])
            self.assertIn('RtConstants.PATH + RtConstants.', query['path_expression'])

    def test_qualified_service_base_is_not_treated_as_missing_package_imports(self):
        out, _ = self.generate(config={'java_service_base_dps': 'hu.company.common.ModuleServiceBase<hu.company.common.DpsLogHelper>'})
        self.assertEqual(self.report(out)['missing_company_imports'], [])
        self.assertIn('extends hu.company.common.ModuleServiceBase<hu.company.common.DpsLogHelper> implements RtService',
                      self.source(out, 'DPS', 'ServiceBase'))
        self.assertIn('public class RtServiceImpl extends RtServiceBase {', self.source(out, 'DPS', 'ServiceImpl'))

    def test_action_only_and_empty_modules_need_no_row_dtos(self):
        form = ET.fromstring(runtime.fixture())
        form.find('Block').set('DatabaseDataBlock', 'false')
        out, _ = self.generate('action_only', raw=ET.tostring(form))
        self.assertNotIn('RtBDto', self.source(out, 'DPS', 'ServiceImpl'))
        self.assertIn('request.getBlocks()', self.source(out, 'DPS', 'ServiceImpl'))
        form = ET.Element('FormModule', Name='EMPTY')
        ET.SubElement(form, 'Canvas', Name='MAIN', CanvasType='Content')
        block = ET.SubElement(form, 'Block', Name='CONTROL', DatabaseDataBlock='false')
        ET.SubElement(block, 'Item', Name='TEXT', DataType='Char', CanvasName='MAIN')
        out, _ = self.generate('empty', raw=ET.tostring(form))
        self.assertEqual(self.report(out)['endpoints'], [])
        self.assertTrue((out / 'backend/WBS/RtServiceBase.java').exists())


class JavaDtoConversionTests(unittest.TestCase):
    blocks = [{'items': [{'field': 'id', 'kind': 'number'}, {'field': 'name', 'kind': 'text'}],
               'query_plan': {'binds': [{'field': 'filtersId'}]}}]

    def convert(self, source):
        return bean_source(source, {'BRow': 'RtBDto', 'SearchRequest': 'RtSearchRequestDto'}, self.blocks)

    def test_strings_comments_text_blocks_and_runtime_record_accessors_are_untouched(self):
        source = '''// row.id = 1; BRow request.limit()
String sql = "row.name = 'BRow'; request.blocks()";
String plsql = """
BEGIN :B.ID := 'row.id; BRow'; END;
""";
BRow row = new BRow();
if (row.id == null) row.id = read(";", other(a, b));
row.name = clean(row.name);
row.id = read() // keep the closing parenthesis outside this comment
;
var value = p.value();
var messages = context.messages();
var criteria = request.criteria();
var limit = request.limit();
var key = criteria.filtersId;
'''
        result = self.convert(source)
        self.assertEqual(JAVA_NONCODE.findall(source), JAVA_NONCODE.findall(result))
        self.assertIn('RtBDto row = new RtBDto();', result)
        self.assertIn('if (row.getId() == null) row.setId(read(";", other(a, b)));', result)
        self.assertIn('row.setName(clean(row.getName()));', result)
        self.assertIn('row.setId(read() // keep the closing parenthesis outside this comment\n);', result)
        self.assertIn('p.value()', result)
        self.assertIn('context.messages()', result)
        self.assertIn('request.getCriteria()', result)
        self.assertIn('criteria.getFiltersId()', result)

    def test_unknown_field_mutations_fail_explicitly(self):
        for code in ('row.id += amount;', '++row.id;', 'row.id++;', 'row.id = other.id = 1;', 'row.id = read()',
                     'row /* keep this */ .id = 1;', 'row.id /* keep this */ = 1;'):
            # A chained assignment to a known receiver is rejected before output.
            code = code.replace('other.id =', 'current.id =')
            with self.subTest(code=code), self.assertRaises(MigrationError):
                self.convert(code)

    def test_similarly_named_methods_are_not_fields(self):
        source = 'row.name(); request.get(); key.id(); row.identifier = 1;'
        self.assertEqual(self.convert(source), source)


if __name__ == '__main__':
    unittest.main()

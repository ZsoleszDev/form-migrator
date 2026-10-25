"""Company CL contract, lossless payloads, opt-in and regeneration boundaries."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

import test_forms_runtime as runtime
import test_compact_backend as compact
from java_support import COMPANY_IMPORTS
from frm_forms.cli import DEFAULTS, main
from frm_forms.common import MigrationError
from frm_forms.contracts import validate_awu_azon, validate_company_config
import os

os.environ.setdefault('FRM_JAVA_IMPORT_MAP', '-')  # tests never read a developer's own java-imports.json
os.environ.setdefault('FRM_JAVA_VARIABLE_MAP', '-')  # nor a developer's own java-variables.json


CL_IMPORTS = COMPANY_IMPORTS + ['hu.company.common.' + symbol for symbol in
                                ('RestClient', 'RestResponseDto', 'DataProviderServiceRestClientBase', 'WebMenuLeaf', 'ConstantsBase',
                                 'ModuleService', 'ModuleController', 'DPSConstants', 'RestHelper',
                                 'WbsService', 'WbsController', 'WbsControllerBase', 'WbsServiceBase', 'WbsUserParcel')]


class CompanyClTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def generate(self, label='out', config=None, raw=None, extra=(), expected=0):
        source = self.root / 'input.xml'
        source.write_bytes(raw if raw is not None else runtime.fixture())
        settings = self.root / (label + '.json')
        settings.write_text(json.dumps({'AWU_AZON': '00123', 'java_company_imports': CL_IMPORTS, **(config or {})}))
        out = self.root / label
        errors = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
            status = main(['migrate', str(source), '--module', 'rt', '--screen', '--config', str(settings), '--out', str(out), *extra])
        self.assertEqual(status, expected, errors.getvalue())
        return out, errors.getvalue()

    def read(self, out, file):
        return (out / 'backend' / 'CL' / file).read_text()

    def report(self, out):
        return json.loads((out / 'analysis' / 'cl-contract.json').read_text())

    def test_constants_and_private_dtos_follow_company_sample_without_data_loss(self):
        out, _ = self.generate()
        constants = self.read(out, 'RtConstants.java')
        self.assertIn('@NoArgsConstructor(access = AccessLevel.PRIVATE)\npublic class RtConstants', constants)
        self.assertIn('NAME = WebMenuLeaf.Path.AWU_00123;', constants)
        self.assertIn('PATH = WebMenuLeaf.FullPath.AWU_00123;', constants)
        self.assertIn('ConstantsBase.AUTH_HAS_ACCESS_PREFIX + PATH + ConstantsBase.AUTH_HAS_ACCESS_SUFFIX', constants)
        self.assertIn('LIST_B_NAME = "listb";', constants)
        self.assertIn('LIST_B_PATH = ConstantsBase.PD + LIST_B_NAME;', constants)
        dto = self.read(out, 'RtBDto.java')
        for annotation in ('@Data', '@Getter', '@NoArgsConstructor', '@EqualsAndHashCode(onlyExplicitlyIncluded = true)'):
            self.assertIn(annotation, dto)
        for field in ('BigDecimal id', 'LocalDateTime d', 'String name'):
            self.assertIn('private ' + field + ';', dto)
        self.assertIn('public RtBDto(BigDecimal id, LocalDateTime d, String name)', dto)
        self.assertIn('this.name = name;', dto)
        self.assertIn('JsonFormat.Shape.STRING', dto)
        self.assertIn('yyyy-MM-dd', dto)
        self.assertNotIn('jakarta', dto)  # MaximumLength=40 is checked in the DPS service (FormsChecks)
        self.assertNotIn('private String save', dto)
        self.assertFalse((out / 'backend/CL/RtDtos.java').exists())
        self.assertFalse(list((out / 'backend/CL').glob('*Calendar*')))
        page = self.read(out, 'RtPageResultDto.java')
        self.assertIn('private List<T> rows;', page)
        self.assertIn('private List<String> messages;', page)

    def test_client_user_envelope_helpers_pagination_and_single_slash(self):
        out, _ = self.generate()
        interface = self.read(out, 'RtRestClient.java')
        self.assertIn('extends RestClient', interface)
        self.assertIn('ResponseEntity<RestResponseDto<RtPageResultDto<RtBDto>>> listB(UserDto user, int offset, int limit);', interface)
        impl = self.read(out, 'RtRestClientImpl.java')
        self.assertIn('@Component\npublic class RtRestClientImpl extends DataProviderServiceRestClientBase implements RtRestClient', impl)
        self.assertIn('public ParameterizedTypeReference<RestResponseDto<RtPageResultDto<RtBDto>>> ptrResponseListB()', impl)
        self.assertIn('return RtConstants.PATH;', impl)
        self.assertIn('exGetEntity(getModulePath() + RtConstants.LIST_B_PATH + "?offset=" + offset + "&limit=" + limit, user, ptrResponseListB())', impl)
        self.assertIn('exPostEntity(getModulePath() + RtConstants.CREATE_B_PATH, getHttpEntity(row, user), ptrResponseCreateB())',
                      ' '.join(impl.split()))
        self.assertNotIn('ConstantsBase.PD', impl)
        self.assertNotIn('org.springframework.web.client.RestClient', impl)
        self.assertEqual(self.report(out)['missing_company_imports'], [])

    def test_unknown_put_delete_helpers_are_explicit_and_never_replaced_with_post(self):
        out, _ = self.generate()
        report = self.report(out)
        self.assertEqual({e['http'] for e in report['endpoints'] if not e['transport_ready']}, {'PUT', 'DELETE'})
        impl = self.read(out, 'RtRestClientImpl.java')
        self.assertEqual(impl.count('throw new UnsupportedOperationException'), 2)
        self.assertNotIn('exPutEntity', impl)
        configured, _ = self.generate('configured', {'java_cl_http_helpers': {'PUT': 'putPayload', 'DELETE': 'deletePayload'}})
        impl = self.read(configured, 'RtRestClientImpl.java')
        self.assertIn('putPayload(getModulePath() + RtConstants.UPDATE_B_PATH,', impl)
        self.assertIn('getHttpEntity(new RtUpdateRequestDto<>(original, value), user)', impl)
        self.assertIn('deletePayload(getModulePath() + RtConstants.DELETE_B_PATH,', impl)
        self.assertIn('getHttpEntity(original, user)', impl)
        self.assertNotIn('throw new UnsupportedOperationException', impl)

    def test_search_lov_and_action_payloads_survive_as_separate_dtos(self):
        out, _ = self.generate(raw=compact.multiple_blocks())
        client = self.read(out, 'RtRestClient.java')
        self.assertIn('RtSearchRequestDto<RtDetailFilterDto> request', client)
        criteria = self.read(out, 'RtDetailFilterDto.java')
        self.assertIn('private BigDecimal filtersId;', criteria)
        search = self.read(out, 'RtSearchRequestDto.java')
        for declaration in ('private T criteria;', 'private int offset;', 'private int limit;'):
            self.assertIn(declaration, search)
        self.assertIn('private Map<String, Map<String, String>> blocks;', self.read(out, 'RtActionRequestDto.java'))
        self.assertIn('private Map<String, String> parameters;', self.read(out, 'RtLovRequestDto.java'))
        self.assertIn('private List<Map<String, Object>> rows;', self.read(out, 'RtLovResultDto.java'))

    def test_company_backend_preserves_sql_plsql_and_documents_frontend_adapter(self):
        from frm_forms.service_inline import JAVA_NONCODE
        legacy, _ = self.generate('legacy', {'AWU_AZON': ''})
        company, _ = self.generate('company')
        old_service = (legacy / 'backend/DPS/RtServiceImpl.java').read_text()
        new_service = (company / 'backend/DPS/RtServiceImpl.java').read_text()
        self.assertEqual(JAVA_NONCODE.findall(old_service), JAVA_NONCODE.findall(new_service))
        self.assertEqual((legacy / 'analysis/backend-evidence.md').read_bytes(),
                         (company / 'analysis/backend-evidence.md').read_bytes())
        report = self.report(company)
        self.assertFalse(report['integration_ready'])
        self.assertTrue(report['backend_contract_ready'])
        self.assertNotIn('DPS', report['pending_layers'])
        self.assertIn('backend szerződései egymáshoz illesztettek', (company / 'INTEGRATION.md').read_text())
        self.assertFalse((legacy / 'analysis/cl-contract.json').exists())

    def test_regeneration_rejects_profile_change_without_touching_existing_output(self):
        out, _ = self.generate(config={'AWU_AZON': ''})
        original = self.read(out, 'RtConstants.java')
        _, error = self.generate(extra=('--regenerate',), expected=1)
        self.assertIn('CL-formátum megváltozott', error)
        self.assertEqual(original, self.read(out, 'RtConstants.java'))

    def test_dto_names_cannot_overwrite_wrapper_dtos(self):
        form = ET.fromstring(runtime.fixture(buttons={}, startup=''))
        form.find('Block').set('Name', 'ROW_RESULT')
        out, _ = self.generate(raw=ET.tostring(form))
        names = self.report(out)['dto_types']
        self.assertNotEqual(names['RowResult'], names['RowResultRow'])
        self.assertIn('private List<String> messages;', self.read(out, names['RowResult'] + '.java'))
        self.assertIn('private BigDecimal id;', self.read(out, names['RowResultRow'] + '.java'))

    def test_cli_awu_override_and_missing_import_report(self):
        out, _ = self.generate(config={'java_company_imports': []}, extra=('--awu-azon', '42'))
        self.assertEqual(self.report(out)['AWU_AZON'], '42')
        self.assertTrue({'RestClient', 'RestResponseDto', 'DataProviderServiceRestClientBase', 'WebMenuLeaf', 'ConstantsBase', 'UserDto',
                         'ModuleService', 'ModuleControllerBase', 'WbsUserParcel', 'WbsServiceBase'} <= set(self.report(out)['missing_company_imports']))
        self.assertIn('AWU_42', self.read(out, 'RtConstants.java'))

    def test_cli_batch_does_not_assign_one_awu_identifier_to_multiple_forms(self):
        self.generate()
        other = self.root / 'other.xml'
        other.write_bytes(runtime.fixture())
        out = self.root / 'batch'
        errors = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
            code = main(['batch', str(self.root / 'input.xml'), str(other), '--out', str(out), '--config', str(self.root / 'out.json')])
        self.assertEqual(code, 1)
        self.assertIn('BATCH_AWU_AZON', errors.getvalue())
        self.assertFalse(out.exists())


class CompanyClValidationTests(unittest.TestCase):
    def test_web_options_forward_exact_identifier_and_reject_invalid_values(self):
        try:
            from frm_forms.web.models import MigrationOptions
            from pydantic import ValidationError
        except ImportError:
            self.skipTest('A webes opciómodell ellenőrzéséhez Pydantic 2 szükséges.')
        for value in ('00123', '9' * 30, 123):
            self.assertEqual(MigrationOptions(AWU_AZON=value).engine_overrides()['AWU_AZON'], str(value))
        self.assertEqual(MigrationOptions().AWU_AZON, '')
        for value in ('AWU_123', False, -123, '1;bad', None):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                MigrationOptions(AWU_AZON=value)

    def test_identifier_keeps_digits_without_numeric_precision_loss(self):
        for raw, expected in [(' 00123 ', '00123'), (12, '12'), ('9' * 30, '9' * 30), ('', '')]:
            self.assertEqual(validate_awu_azon(raw), expected)
        for raw in ('AWU_12', '-1', '1.5', '1e4', '1;bad', '１', True, None, '1' * 31, 1.5, []):
            with self.subTest(raw=raw), self.assertRaises(MigrationError):
                validate_awu_azon(raw)

    def test_helper_validation_preserves_defaults_and_rejects_code(self):
        config = copy.deepcopy(DEFAULTS)
        config['java_cl_http_helpers'] = {'PUT': 'putPayload'}
        validate_company_config(config)
        self.assertEqual(config['java_cl_http_helpers']['POST'], 'exPostEntity')
        for invalid in ({'PATCH': 'foo'}, {'PUT': 'foo();'}, {'POST': ''}, {'PUT': 'class'}, [], None):
            config = copy.deepcopy(DEFAULTS)
            config['java_cl_http_helpers'] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(MigrationError):
                validate_company_config(config)


if __name__ == '__main__':
    unittest.main()

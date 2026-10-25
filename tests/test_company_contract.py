import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from frm_forms.cli import main

ROOT = Path(__file__).resolve().parents[1]


class CompanyContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def generate(self, config=None):
        output = self.root / 'generated'
        args = ['migrate', str(ROOT / 'examples/customer_fmb.xml'), '--out', str(output), '--module', 'customer', '--schema', str(ROOT / 'examples/schema.json')]
        if config is not None:
            path = self.root / 'config.json'; path.write_text(json.dumps(config)); args += ['--config', str(path)]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = main(args)
        return result, output

    def test_multifile_host_contract_no_imports_or_old_directives(self):
        code, output = self.generate()
        self.assertEqual(code, 0)
        root = output / 'frontend/ugyfelek'
        for filename in ['component.ts','component.html','component.scss','model.ts','form-structure.ts','endpoints.ts','actions.ts','i18n/ugyfelek.hu.json']:
            self.assertTrue((root/filename).is_file(), filename)
        source = '\n'.join(p.read_text() for p in root.rglob('*.ts'))
        self.assertNotRegex(source, r'(?m)^\s*import\s|\*ng(?:If|For|Switch)')
        self.assertIn('protected formStructure: FormBlock.Structure[]', source)
        self.assertIn('(formGroupGenerated)="onFormGroupGenerated($event)"', source)
        self.assertIn('maxLenght:', source)
        self.assertIn('CREATE_ONCE', (root/'component.ts').read_text())
        self.assertNotIn('Név', source)

    def test_layers_own_the_correct_responsibilities(self):
        _, output = self.generate()
        backend = output / 'backend'
        self.assertEqual({p.name for p in backend.iterdir()}, {'CL', 'DPS', 'WBS'})
        for file in ['CustomerDtos.java', 'CustomerConstants.java', 'CustomerRestClient.java', 'CustomerRestClientImpl.java']:
            self.assertTrue((backend / 'CL' / file).is_file(), file)
        for layer, names in [('DPS', ['Controller', 'ControllerBase', 'ControllerImpl', 'Service', 'ServiceImpl']),
                             ('WBS', ['Controller', 'ControllerBase', 'ControllerImpl', 'Service', 'ServiceImpl'])]:
            for suffix in names:
                self.assertTrue((backend / layer / ('Customer' + suffix + '.java')).is_file())
        self.assertIn('insertRow(row)', (backend / 'DPS/CustomerServiceImpl.java').read_text())
        wbs = '\n'.join(p.read_text() for p in (backend / 'WBS').glob('*.java'))
        self.assertNotIn('import hu.company.features.customer.dps.', wbs)
        self.assertNotIn('jdbc', wbs.lower())
        self.assertIn('client.updateCustomer(original, value)', wbs)
        for layer in ['DPS', 'WBS']:
            self.assertIn('@GetMapping(CustomerConstants.CUSTOMER_LIST_PATH)', (backend / layer / 'CustomerController.java').read_text())

    def test_custom_urls_selectors_and_endpoint_names_share_one_configuration(self):
        code, output = self.generate({'wbs_base_url': 'http://localhost:8080/company/', 'dps_base_url': 'http://localhost:8081/internal/',
            'html_selectors': {'form_block': 'company-form'}, 'endpoint_names': {'list': 'fetchdata'}, 'form_block_structure_type': 'CompanyForm.Structure'})
        self.assertEqual(code, 0)
        source = '\n'.join(p.read_text() for p in (output / 'frontend').rglob('*.ts'))
        self.assertIn('<company-form ', source)
        self.assertIn('CompanyForm.Structure[]', source)
        self.assertIn('environment.baseUrl', source)
        self.assertNotIn('http://localhost:8080/company', source)
        self.assertIn('/customer/fetchdata', source)
        self.assertIn('CUSTOMER_LIST_PATH = "/customer/fetchdata"', (output / 'backend/CL/CustomerConstants.java').read_text())
        self.assertIn('"http://localhost:8081/internal"', (output / 'backend/WBS/CustomerServiceImpl.java').read_text())

    def test_blank_addresses_do_not_invent_endpoints(self):
        _, output = self.generate()
        source = '\n'.join(p.read_text() for p in (output / 'frontend').rglob('*.ts'))
        self.assertIn('environment.baseUrl', source)
        self.assertNotIn('http://localhost', source)
        self.assertIn('GENERATED_DPS_URL = ""', (output / 'backend/WBS/CustomerServiceImpl.java').read_text())

    def test_invalid_host_configuration_is_rejected_before_output(self):
        cases = [
            {'html_selectors': {'form_block': 'script'}}, {'html_selectors': {'button': 'bad onclick="x"'}},
            {'wbs_base_url': 'javascript:alert(1)'}, {'dps_base_url': 'http://name:secret@localhost:8000'},
            {'wbs_base_url': 'http://localhost:bad'}, {'endpoint_names': {'list': 'create'}},
            {'form_block_structure_type': 'Type[];run()'}, {'form_block_types': {'unknown': 'bad'}},
        ]
        for config in cases:
            with self.subTest(config=config):
                code, output = self.generate(config)
                self.assertEqual(code, 1)
                self.assertFalse(output.exists())


if __name__ == '__main__': unittest.main()

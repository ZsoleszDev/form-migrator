"""PMD method names, stable HTTP contracts and the shared helper's web option."""
import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from frm_forms.action_scaffold import action_plan
from frm_forms.cli import DEFAULTS, configuration, main
from frm_forms.common import MigrationError, name
from frm_forms.contracts import validate_common_migrate_tools_package
from frm_forms.discovery import build_map
from frm_forms.service_inline import mask
from java_support import COMPANY_IMPORTS, write_stubs
from test_company_cl import CL_IMPORTS
from test_forms_runtime import fixture


# PMD MethodNamingConventions ([a-z][a-zA-Z0-9]*) and Checkstyle MethodName (lowercase second character).
PMD_METHOD = re.compile(r'^[a-z][a-z0-9][a-zA-Z0-9]*$')
# Both implementation methods and unmodified interface signatures. Constructors
# have no return type and are excluded. Literals/comments are masked first.
DECLARATION = re.compile(
    r'(?m)^[ \t]*(?:(?:public|private|protected|abstract|static|final|default)[ \t]+)*'
    r'(?!(?:return|throw|new|public|private|protected|abstract|static|final|default)\b)'
    r'(?:<[^\n;{}]+>[ \t]+)?[A-Za-z_$][\w.$]*(?:<[^\n;{}]+>)?(?:\[\])*[ \t]+(\w+)[ \t]*\(')


def button_form(owners):
    form = ET.Element('FormModule', Name='NAMES')
    blocks = {}
    for block_name, item_name in owners:
        if block_name not in blocks:
            blocks[block_name] = ET.SubElement(form, 'Block', Name=block_name, DatabaseDataBlock='false')
        button = ET.SubElement(blocks[block_name], 'Item', Name=item_name, ItemType='Push Button')
        ET.SubElement(button, 'Trigger', Name='WHEN-BUTTON-PRESSED', TriggerText='BEGIN pkg.run; END;')
    return form


def planned(form, filename='input.xml'):
    discovery = build_map([(filename, form)])
    actions, _ = action_plan(discovery, DEFAULTS, 'names')
    return actions, discovery


class MethodNameTests(unittest.TestCase):
    def test_reported_name_is_readable_without_a_hash(self):
        actions, _ = planned(button_form([('V_ELEK_ADLAP', 'UBI_ILIMBI_KOD2')]))
        self.assertEqual(actions[0]['method_name'], 'onVElekAdlapUbiIlimbiKod2')
        self.assertRegex(actions[0]['method_name'], PMD_METHOD)

    def test_short_numeric_and_reserved_owners_have_valid_methods(self):
        actions, _ = planned(button_form([('V', 'X'), ('1', '2'), ('CLASS', 'DEFAULT'), ('_', '_')]))
        for action in actions:
            self.assertRegex(action['method_name'], PMD_METHOD)

    def test_collisions_reserve_natural_numbered_names_and_ignore_input_order(self):
        owners = [('B', 'A_B'), ('B', 'A$B'), ('B', 'A_B2'), ('B_A', 'B'), ('C', 'SAVE')]
        first, _ = planned(button_form(owners), 'original_fmb.xml')
        second, _ = planned(button_form(list(reversed(owners))), 'input.xml')
        names = {a['owner']: a['method_name'] for a in first}
        self.assertEqual(names, {a['owner']: a['method_name'] for a in second})
        self.assertEqual(names['B.A_B2'], 'onBAB2')
        self.assertEqual(names['C.SAVE'], 'onCSave')
        self.assertEqual(len({n.lower() for n in names.values()}), len(names))
        for method in names.values():
            self.assertRegex(method, PMD_METHOD)

    def test_http_identifiers_and_source_evidence_remain_unchanged(self):
        actions, discovery = planned(button_form([('V_ELEK_ADLAP', 'UBI_ILIMBI_KOD2')]))
        action, code = actions[0], discovery['code'][0]
        suffix = hashlib.sha256(code['path'].encode()).hexdigest()[:8]
        self.assertEqual(action['key'], name(action['owner'], 'kebab') + '-' + suffix)
        self.assertEqual(action['path'], '/api/forms/names/actions/' + action['key'])
        self.assertEqual(action['api_name'], (name(action['owner']) + 'Action' + suffix).lower())
        self.assertEqual(action['source_sha256'], code['sha256'])


class GeneratedContractTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def generate(self, label='out', config=None, module='rt', raw=None):
        form = ET.fromstring(fixture(buttons={}))
        block = ET.SubElement(form, 'Block', Name='V_ELEK_ADLAP', DatabaseDataBlock='false')
        button = ET.SubElement(block, 'Item', Name='UBI_ILIMBI_KOD2', ItemType='Push Button', CanvasName='MAIN')
        ET.SubElement(button, 'Trigger', Name='WHEN-BUTTON-PRESSED',
                      TriggerText="BEGIN UPDATE T_B SET NAME = 'vElekAdlapUbiIlimbiKod2Action116a87a8' WHERE ID = :B.ID; END;")
        source = self.root / 'input.xml'
        source.write_bytes(raw or ET.tostring(form))
        settings = self.root / (label + '.json')
        settings.write_text(json.dumps({'java_company_imports': CL_IMPORTS, 'backend_live': True, **(config or {})}))
        output = self.root / label
        errors = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
            code = main(['migrate', str(source), '--module', module, '--screen', '--config', str(settings), '--out', str(output)])
        self.assertEqual(code, 0, errors.getvalue())
        return output

    def test_all_methods_follow_pmd_and_cross_layer_calls_share_the_name(self):
        for awu in ('', '00123'):
            with self.subTest(AWU_AZON=awu):
                output = self.generate('out' + awu, {'AWU_AZON': awu})
                method = 'onVElekAdlapUbiIlimbiKod2'
                checked = 0
                for path in output.glob('backend/**/*.java'):
                    source = path.read_text()
                    for generated in DECLARATION.findall(mask(source)):
                        self.assertRegex(generated, PMD_METHOD, str(path))
                        checked += 1
                    if path.stem in {'RtRestClient', 'RtRestClientImpl', 'RtController', 'RtControllerImpl', 'RtService', 'RtServiceImpl'}:
                        self.assertIn(method + '(', source, str(path))
                self.assertGreater(checked, 70)
                service = (output / 'backend/DPS/RtServiceImpl.java').read_text()
                self.assertIn('(rs, rowNum) -> mapB(rs)', ' '.join(service.split()))
                self.assertIn("SET NAME = 'vElekAdlapUbiIlimbiKod2Action116a87a8'", service)
                actions = json.loads((output / 'analysis/action-plan.json').read_text())['actions']
                action = next(a for a in actions if a['method_name'] == method)
                plan = json.loads((output / 'analysis/backend-plan.json').read_text())
                endpoint = next(e for e in plan['endpoints'] if e['method'] == method)
                self.assertTrue(endpoint['implemented'])
                if awu:
                    contract = json.loads((output / 'analysis/cl-contract.json').read_text())
                    route = next(e for e in contract['endpoints'] if e['method'] == method)
                    self.assertEqual(route['relative_path'], '/' + action['api_name'])
                    self.assertEqual(plan['api']['paths'][route['path_constant']], route['relative_path'])
                    constants = (output / 'backend/CL/RtConstants.java').read_text()
                    self.assertIn(route['name_constant'] + ' = "' + action['api_name'] + '";', constants)

    def test_custom_helper_package_reaches_every_profile_without_duplication(self):
        for awu in ('', '00123'):
            output = self.generate('custom' + awu, {'AWU_AZON': awu, 'common_migrate_tools_package': 'hu.ceg.shared.cl'})
            tools = (output / 'backend/CL/CommonMigrateTools.java').read_text()
            self.assertTrue(tools.startswith('package hu.ceg.shared.cl;'))
            service = (output / 'backend/DPS/RtServiceImpl.java').read_text()
            self.assertIn('import hu.ceg.shared.cl.CommonMigrateTools.DbCalls;', service)
            self.assertIn('import hu.ceg.shared.cl.CommonMigrateTools.FormsChecks;', service)
            self.assertNotIn('hu.company.features.cl.CommonMigrateTools.', service)
            self.assertNotIn('static final class DbCalls', service)
            self.assertEqual(len(list(output.rglob('CommonMigrateTools.java'))), 1)

    def test_empty_helper_package_tracks_java_base_package(self):
        output = self.generate(config={'java_package': 'hu.ceg.features', 'common_migrate_tools_package': ''})
        self.assertTrue((output / 'backend/CL/CommonMigrateTools.java').read_text().startswith('package hu.ceg.features.cl;'))
        self.assertIn('import hu.ceg.features.cl.CommonMigrateTools.DbCalls;',
                      (output / 'backend/DPS/RtServiceImpl.java').read_text())

    @unittest.skipUnless(shutil.which('java'), 'Java 11+ compiler required')
    def test_two_modules_compile_against_one_configured_shared_helper(self):
        config = {'java_company_imports': COMPANY_IMPORTS, 'common_migrate_tools_package': 'hu.ceg.shared.cl'}
        outputs = [self.generate('first', config), self.generate('second', config, module='other')]
        sources = list(outputs[0].glob('backend/**/*.java'))
        sources += [p for p in outputs[1].glob('backend/**/*.java') if p.name != 'CommonMigrateTools.java']
        sources += write_stubs(self.root / 'stubs')
        args = self.root / 'sources.args'
        args.write_text('\n'.join('"' + str(p).replace(chr(92), '/') + '"' for p in sources))
        result = subprocess.run(['java', 'com.sun.tools.javac.Main', '--release', '11', '-encoding', 'UTF-8',
                                 '-d', str(self.root / 'classes'), '@' + str(args)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class CommonToolsOptionTests(unittest.TestCase):
    def test_package_validation_accepts_defaults_and_rejects_imports_paths_and_keywords(self):
        for value, expected in [('', ''), ('  ', ''), (' hu.ceg.shared.cl ', 'hu.ceg.shared.cl'), ('hu.ceg2.common_tools', 'hu.ceg2.common_tools')]:
            self.assertEqual(validate_common_migrate_tools_package(value), expected)
        for value in (None, 12, False, [], 'shared', 'hu.ceg.class', 'hu.ceg.1bad', 'hu/ceg/cl',
                      'hu.ceg.CommonMigrateTools', 'import hu.ceg.cl;', 'hu.ceg.*', 'hu..ceg', 'hu.' + 'a' * 200):
            with self.subTest(value=value), self.assertRaises(MigrationError):
                validate_common_migrate_tools_package(value)

    def test_cli_rejects_a_reserved_package_before_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'config.json'
            config.write_text(json.dumps({'common_migrate_tools_package': 'hu.ceg.class'}))
            with self.assertRaisesRegex(MigrationError, 'common_migrate_tools_package'):
                configuration(argparse.Namespace(config=config, java_package=None, max_ai_calls=None, screen=True))

    def test_web_options_round_trip_and_server_defaults_preserve_the_package(self):
        try:
            from frm_forms.web.models import MigrationOptions
            from frm_forms.web.settings import Settings
            from pydantic import ValidationError
        except ImportError:
            self.skipTest('Pydantic 2 required')
        settings = Settings(data_dir=Path('.'), engine_config={'common_migrate_tools_package': 'hu.ceg.shared.cl'})
        defaults = settings.defaults()
        self.assertEqual(defaults.common_migrate_tools_package, 'hu.ceg.shared.cl')
        options = MigrationOptions.model_validate_json(defaults.model_dump_json())
        self.assertEqual(options.engine_overrides()['common_migrate_tools_package'], 'hu.ceg.shared.cl')
        self.assertEqual(MigrationOptions().common_migrate_tools_package, '')
        for value in (None, 'hu.ceg.class', 'hu.ceg.CommonMigrateTools', 'hu/ceg/cl'):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                MigrationOptions(common_migrate_tools_package=value)

    def test_web_option_is_used_by_the_generator(self):
        try:
            from frm_forms.web.models import MigrationOptions
        except ImportError:
            self.skipTest('Pydantic 2 required')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, config, output = root / 'input.xml', root / 'config.json', root / 'out'
            source.write_bytes(fixture())
            options = MigrationOptions(common_migrate_tools_package='hu.ceg.web.shared', AWU_AZON='123')
            config.write_text(json.dumps(options.engine_overrides()))
            errors = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
                code = main(['migrate', str(source), '--screen', '--config', str(config), '--out', str(output)])
            self.assertEqual(code, 0, errors.getvalue())
            helper = output / 'backend/CL/CommonMigrateTools.java'
            self.assertTrue(helper.read_text().startswith('package hu.ceg.web.shared;'))
            service = next(output.glob('backend/DPS/*ServiceImpl.java')).read_text()
            self.assertIn('import hu.ceg.web.shared.CommonMigrateTools.DbCalls;', service)


if __name__ == '__main__':
    unittest.main()

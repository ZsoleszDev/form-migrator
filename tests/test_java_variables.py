"""4.25: java-variables.json - the value of a variable the generated Java creates itself.

An entry names a variable of the DPS ServiceImpl methods (a developer input, a screen value of a Java query button):

    {"variableName": "ibuKod", "variableValue": "commonService.Details(param)",
     "autowired": "commonService", "import": "hu.company.pelda.CommonService"}

Where the generator declares ibuKod, it writes `String ibuKod = commonService.Details(param);`; the ServiceImpl gets
the import and, at the top of the class, `@Autowired private CommonService commonService;`.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock

from java_support import COMPANY_IMPORTS, write_stubs
from frm_forms import java_variables
from frm_forms.cli import main
from frm_forms.common import MigrationError
import test_developer_inputs as inputs
import test_query_java as tq

EXAMPLE = {'variableName': 'ibuKod', 'variableValue': 'commonService.Details(param)', 'autowired': 'commonService',
           'import': 'hu.company.pelda.CommonService'}
# Values that compile against the test stub (hu.company.pelda.CommonService.Details(String)).
VARIABLES = [
    {'variableName': 'xxYy', 'variableValue': 'commonService.Details("XX.YY");', 'autowired': 'commonService',
     'import': 'hu.company.pelda.CommonService'},
    {'_megjegyzés': 'ugyanaz a szolgáltatás: egy mező, egy import', 'variableName': 'globalFelhasznalo',
     'variableValue': 'commonService.Details("FELHASZNALO")', 'autowired': 'commonService', 'import': ['hu.company.pelda.CommonService']},
    {'variableName': 'nemHasznalt', 'variableValue': 'otherService.value()', 'autowired': 'otherService', 'import': 'hu.company.pelda.OtherService'}]


def migrate(root: Path, xml: bytes, variables, config=None, label='out') -> tuple[int, str, Path]:
    (root / (label + '.xml')).write_bytes(xml)
    (root / 'vars.json').write_text(json.dumps(variables), encoding='utf-8')
    (root / 'config.json').write_text(json.dumps({'backend_live': True, 'java_company_imports': COMPANY_IMPORTS, 'java_import_map': '-',
                                                  'java_variable_map': 'vars.json', **(config or {})}))
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        code = main(['migrate', str(root / (label + '.xml')), '--module', 'query', '--screen', '--config', str(root / 'config.json'),
                     '--out', str(root / label)])
    return code, err.getvalue(), root / label


def javac(root: Path, out: Path) -> subprocess.CompletedProcess | None:
    if not shutil.which('java'):
        return None
    sources = list(out.glob('backend/**/*.java')) + write_stubs(root / 'stubs')
    (root / 'sources.args').write_text('\n'.join('"' + str(p).replace(chr(92), '/') + '"' for p in sources))
    run = subprocess.run(['java', 'com.sun.tools.javac.Main', '--release', '11', '-encoding', 'UTF-8', '-d', str(root / 'classes'),
                          '@' + str(root / 'sources.args')], capture_output=True, text=True, timeout=300)
    return None if 'Could not find or load main class' in run.stderr else run


class MapFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, data, name='v.json') -> str:
        path = self.root / name
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding='utf-8')
        return str(path)

    def test_the_example_of_the_request(self):
        entry = java_variables.parse(EXAMPLE)['ibuKod']
        self.assertEqual(entry, {'name': 'ibuKod', 'value': 'commonService.Details(param)', 'autowired': 'commonService',
                                 'type': 'CommonService', 'imports': ['hu.company.pelda.CommonService']})

    def test_the_accepted_forms(self):
        for data in ([EXAMPLE], EXAMPLE, {'_leírás': 'x', 'variables': [EXAMPLE]}):
            self.assertEqual(list(java_variables.load({'java_variable_map': self.write(data)})), ['ibuKod'])
        plain = java_variables.parse([{'variableName': 'kod', 'variableValue': ' "A"; '}])['kod']
        self.assertEqual((plain['value'], plain['autowired'], plain['type'], plain['imports']), ('"A"', None, None, []))
        self.assertEqual(java_variables.parse({**EXAMPLE, 'import': 'import hu.company.pelda.CommonService;'})['ibuKod']['imports'],
                         ['hu.company.pelda.CommonService'])

    def test_the_default_file_of_the_migrator_has_no_entries(self):
        default = json.loads(java_variables.default_path().read_text(encoding='utf-8'))
        self.assertEqual(default['variables'], [])
        self.assertEqual(list(java_variables.parse(default['_példa'])), ['ibuKod'])  # the example of the request
        with unittest.mock.patch.dict(os.environ, {'FRM_JAVA_VARIABLE_MAP': ''}):
            self.assertEqual(java_variables.load({}), {})

    def test_off_and_environment(self):
        self.assertEqual(java_variables.load({'java_variable_map': '-'}), {})
        with unittest.mock.patch.dict(os.environ, {'FRM_JAVA_VARIABLE_MAP': self.write([EXAMPLE], 'env.json')}):
            self.assertEqual(list(java_variables.load({})), ['ibuKod'])

    def test_wrong_maps_are_refused(self):
        bad = [
            ({**EXAMPLE, 'variableName': 'ibu-kod'}, 'variableName'),
            ({**EXAMPLE, 'variableName': 'class'}, 'variableName'),
            ({**EXAMPLE, 'variableValue': ''}, 'variableValue'),
            ({**EXAMPLE, 'variableValue': 'a(\n)'}, 'variableValue'),
            ({**EXAMPLE, 'autowired': 'common service'}, 'autowired'),
            ({**EXAMPLE, 'autowired': 'jdbc'}, 'autowired'),
            ({**EXAMPLE, 'import': 'CommonService'}, 'import'),
            ({**EXAMPLE, 'import': ['hu.company.X', 3]}, 'import'),
            ({**EXAMPLE, 'imports': 'hu.company.X'}, 'ismeretlen kulcs'),
            ([EXAMPLE, EXAMPLE], 'kétszer'),
            ({'valtozok': [EXAMPLE]}, 'ismeretlen kulcs'),
            ('"ibuKod"', 'lista szükséges'),
            ('{"variables": [', 'hibás JSON')]
        for data, message in bad:
            with self.subTest(data=data), self.assertRaises(MigrationError) as raised:
                java_variables.load({'java_variable_map': self.write(data)})
            self.assertIn('JAVA_VARIABLE_MAP', str(raised.exception))
            self.assertIn(message, str(raised.exception))
        with self.assertRaises(MigrationError):
            java_variables.load({'java_variable_map': str(self.root / 'nincs.json')})


class DeveloperInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        code, err, cls.out = migrate(cls.root, inputs.form(), VARIABLES, {'backend_trigger_mode': 'plsql'})
        assert code == 0, err
        cls.service = (cls.out / 'backend/DPS/QueryServiceImpl.java').read_text(encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_the_variable_gets_its_value(self):
        method = self.service[self.service.index('public ActionResult onT1PbMent('):]
        self.assertIn('            // :XX.YY (ismeretlen mező, nincs a formban): az érték a java-variables.json-ból.\n'
                      '            String xxYy = commonService.Details("XX.YY");\n', method)
        self.assertIn('DbCalls.in(xxYy, Types.VARCHAR)', method)
        self.assertIn('String systemLastQuery = null;', method)  # not in the map: TODO, null
        trigger = self.service[self.service.index('private void trigger'):]
        self.assertIn('String globalFelhasznalo = commonService.Details("FELHASZNALO");', trigger)
        self.assertIn('String t1Col1 = null;', trigger)

    def test_the_import_and_the_autowired_field_once(self):
        self.assertEqual(self.service.count('import hu.company.pelda.CommonService;\n'), 1)
        self.assertIn('import org.springframework.beans.factory.annotation.Autowired;\n', self.service)
        top = self.service[self.service.index('public class QueryServiceImpl'):]
        self.assertTrue(top.split('\n', 1)[1].startswith('    @Autowired\n    private CommonService commonService;\n'), top[:300])
        self.assertEqual(self.service.count('@Autowired'), 1)
        self.assertNotIn('OtherService', self.service)  # an entry no method uses adds nothing

    def test_the_reports(self):
        self.assertIn('Fejlesztői bemenet (a metódus elején, null; TODO): :SYSTEM.LAST_QUERY -> systemLastQuery. A kód ezekkel fut; '
                      'add át nekik a megfelelő értéket. Fejlesztői bemenet a java-variables.json-ból: :XX.YY -> xxYy.', self.service)
        tasks = (self.out / 'BACKEND_TASKS.md').read_text(encoding='utf-8')
        self.assertIn('| T1.PB_MENT / WHEN-BUTTON-PRESSED | :SYSTEM.LAST_QUERY | systemLastQuery |', tasks)
        self.assertNotIn('| T1.PB_MENT / WHEN-BUTTON-PRESSED | :XX.YY | xxYy | ismeretlen', tasks)
        self.assertIn('## Fejlesztői bemenetek a java-variables.json-ból', tasks)
        self.assertIn('| T1.PB_MENT / WHEN-BUTTON-PRESSED | :XX.YY | xxYy | commonService.Details("XX.YY") |', tasks)
        plan = json.loads((self.out / 'analysis/backend-plan.json').read_text(encoding='utf-8'))
        endpoint = next(e for e in plan['endpoints'] if e.get('owner') == 'T1.PB_MENT')
        self.assertEqual(endpoint['developer_inputs'][0]['value'], 'commonService.Details("XX.YY")')
        self.assertNotIn('value', endpoint['developer_inputs'][1])
        coverage = json.loads((self.out / 'analysis/runtime-coverage.json').read_text(encoding='utf-8'))
        row = next(r for r in coverage['triggers'] if r['owner'] == 'T1.PB_MENT')
        self.assertIn('Fejlesztői bemenet: :SYSTEM.LAST_QUERY (a Java-metódusban TODO változó, értékét a fejlesztő adja át).', row['gaps'])
        used = json.loads((self.out / 'analysis/java-variables.json').read_text(encoding='utf-8'))
        self.assertEqual({v['variableName']: v['used_in'] for v in used['variables']},
                         {'xxYy': ['backend/DPS/QueryServiceImpl.java'], 'globalFelhasznalo': ['backend/DPS/QueryServiceImpl.java'],
                          'nemHasznalt': []})

    def test_the_map_belongs_to_the_run(self):
        self.assertEqual(java_variables.ACTIVE, {})

    def test_the_generated_java_compiles(self):
        run = javac(self.root, self.out)
        if run is None:
            self.skipTest('JDK (javac) required')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


class OtherPlacesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_a_value_of_a_java_query_button(self):
        builder = tq.BUILDER.replace('COL8 = :T1.col5', 'COL8 = :IBU_KOD')
        code, err, out = migrate(self.root, tq.form(builder), [{**EXAMPLE, 'variableValue': 'commonService.Details("IBU_KOD")'}])
        self.assertEqual(code, 0, err)
        service = (out / 'backend/DPS/QueryServiceImpl.java').read_text(encoding='utf-8')
        self.assertIn('            // :IBU_KOD: az érték a java-variables.json-ból.\n'
                      '            String ibuKod = commonService.Details("IBU_KOD");\n', service)
        self.assertNotIn('// TODO: :IBU_KOD', service)
        self.assertIn('// Érték a java-variables.json-ból: :IBU_KOD -> ibuKod.', service)
        self.assertIn('    @Autowired\n    private CommonService commonService;\n', service)
        self.assertIn('import hu.company.pelda.CommonService;\n', service)
        run = javac(self.root, out)
        if run is not None:
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_the_company_format_keeps_the_field(self):
        code, err, out = migrate(self.root, inputs.form(), VARIABLES, {'AWU_AZON': '00123'})
        self.assertEqual(code, 0, err)
        service = (out / 'backend/DPS/QueryServiceImpl.java').read_text(encoding='utf-8')
        self.assertIn('public class QueryServiceImpl extends QueryServiceBase {\n    @Autowired\n    private CommonService commonService;\n',
                      service)
        self.assertIn('String xxYy = commonService.Details("XX.YY");', service)
        self.assertIn('import hu.company.pelda.CommonService;\n', service)

    def test_a_wrong_map_stops_the_migration_with_its_reason(self):
        code, err, _ = migrate(self.root, inputs.form(), [{**EXAMPLE, 'variableName': '1kod'}])
        self.assertEqual(code, 1)
        self.assertIn('HIBA: JAVA_VARIABLE_MAP: a variableName Java-változónév legyen', err)
        self.assertEqual(java_variables.ACTIVE, {})

    def test_the_setting_must_be_a_path(self):
        (self.root / 'in.xml').write_bytes(inputs.form())
        (self.root / 'config.json').write_text(json.dumps({'java_variable_map': ['x']}))
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = main(['migrate', str(self.root / 'in.xml'), '--module', 'query', '--config', str(self.root / 'config.json'),
                         '--out', str(self.root / 'out')])
        self.assertEqual(code, 1)
        self.assertIn('java_variable_map: a java-variables.json útvonala szükséges', err.getvalue())


if __name__ == '__main__':
    unittest.main()

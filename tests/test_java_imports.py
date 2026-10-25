"""java-imports.json: simple class name -> import location, read at every generation."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from frm_forms import java_imports, java_tidy
from frm_forms.cli import main
from frm_forms.common import MigrationError

HEADSTART = Path(__file__).with_name('golden') / 'headstart' / 'input.xml'
MAP = {'RestResponseDto': 'hu.ff.xy.cl.modules.RestResponseDto', 'UserDto': 'hu.ff.xy.cl.modules.UserDto',
       'HttpServletRequest': 'javax.servlet.http.HttpServletRequest', 'Unused': 'hu.ff.xy.cl.modules.Unused'}


class JavaImportMapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(java_tidy.configure, {})

    def write_map(self, data, name='java-imports.json'):
        path = self.root / name
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def test_load_accepts_flat_or_wrapped_maps_and_names_bad_entries(self):
        self.assertEqual(java_imports.load({'java_import_map': str(self.write_map({'_leírás': 'x', **MAP}))}), MAP)
        self.assertEqual(java_imports.load({'java_import_map': str(self.write_map({'imports': MAP}, 'w.json'))}), MAP)
        for bad in ({'RestResponseDto': 'hu.ff.xy.Other'}, {'Rest Dto': 'hu.x.Rest'}, {'UserDto': 'user dto'}, ['list']):
            with self.subTest(bad=bad), self.assertRaises(MigrationError):
                java_imports.load({'java_import_map': str(self.write_map(bad, 'bad.json'))})
        broken = self.root / 'broken.json'; broken.write_text('{', encoding='utf-8')
        with self.assertRaisesRegex(MigrationError, 'hibás JSON'):
            java_imports.load({'java_import_map': str(broken)})
        with self.assertRaisesRegex(MigrationError, 'nem található'):
            java_imports.load({'java_import_map': str(self.root / 'missing.json')})

    def test_every_value_that_is_not_a_java_class_name_is_an_angular_import(self):
        path = self.write_map({'WFF': 'wf-package', 'Ui': '@ff/ui', 'ServiceBase': 'src/app/core/base/service-base',
                               'SocketIo': 'socket.io', 'UserDto': 'hu.ff.xy.cl.modules.UserDto',
                               'Both': {'java': 'hu.ff.Both', 'ts': 'both-package'}}, 'mixed.json')
        config = {'java_import_map': str(path)}
        self.assertEqual(java_imports.load(config), {'UserDto': 'hu.ff.xy.cl.modules.UserDto', 'Both': 'hu.ff.Both'})
        self.assertEqual(java_imports.load_ts(config), {'WFF': 'wf-package', 'Ui': '@ff/ui', 'ServiceBase': 'src/app/core/base/service-base',
                                                        'SocketIo': 'socket.io', 'Both': 'both-package'})
        result, _ = __import__('frm_forms.ts_imports', fromlist=['tidy']).tidy(
            "import { Component } from '@angular/core';\n\nexport class A { f() { WFF.err('Hiba', null); } }\n", java_imports.load_ts(config))
        self.assertIn("import { WFF } from 'wf-package';", result)
        with self.assertRaisesRegex(MigrationError, 'ugyanazzal a névvel'):  # a Java class name with a typo stays an error
            java_imports.load({'java_import_map': str(self.write_map({'UserDto': 'hu.ff.xy.UserDTO'}, 'typo.json'))})

    def test_used_names_are_imported_from_the_map_which_wins_over_generated_imports(self):
        java_tidy.configure(MAP)
        text = ('package hu.a.b;\n\nimport hu.company.common.UserDto;\nimport jakarta.servlet.http.HttpServletRequest;\n\n'
                'public class X {\n    public RestResponseDto<String> call(UserDto user, HttpServletRequest request) { return null; }\n}\n')
        result = java_tidy.tidy(text)
        for fqn in ('hu.ff.xy.cl.modules.RestResponseDto', 'hu.ff.xy.cl.modules.UserDto', 'javax.servlet.http.HttpServletRequest'):
            self.assertIn('import ' + fqn + ';', result)
        for absent in ('hu.company.common.UserDto', 'jakarta.', 'Unused'):
            self.assertNotIn(absent, result)

    def test_same_package_and_locally_declared_names_get_no_import(self):
        java_tidy.configure({'Helper': 'hu.a.b.Helper', 'UserDto': 'hu.ff.xy.cl.modules.UserDto'})
        result = java_tidy.tidy('package hu.a.b;\n\npublic class X {\n    static class UserDto {}\n    Helper h; UserDto u;\n}\n')
        self.assertNotIn('import ', result)

    def generate(self, label, map_path):
        config = self.root / (label + '.json')
        config.write_text(json.dumps({'java_import_map': str(map_path)}), encoding='utf-8')
        out = self.root / label
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['migrate', str(HEADSTART), '--screen', '--module', 'teszt', '--awu-azon', '1234',
                                   '--config', str(config), '--out', str(out)]), 0)
        return out

    def test_generation_reads_the_map_every_time_and_reports_names_without_import(self):
        path = self.write_map(MAP)
        out = self.generate('first', path)
        wbs = (out / 'backend/WBS/TesztServiceImpl.java').read_text(encoding='utf-8')
        self.assertIn('import hu.ff.xy.cl.modules.RestResponseDto;', wbs)
        self.assertIn('import hu.ff.xy.cl.modules.UserDto;', wbs)
        report = json.loads((out / 'analysis/java-imports.json').read_text(encoding='utf-8'))
        self.assertEqual(report['imported_from_map']['RestResponseDto'], 'hu.ff.xy.cl.modules.RestResponseDto')
        self.assertIn('ModuleService', report['without_import'])  # not in the map yet: listed to add
        self.assertNotIn('RestResponseDto', report['without_import'])
        self.write_map({**MAP, 'RestResponseDto': 'hu.ff.uj.RestResponseDto'})  # edited: the next generation follows
        wbs = (self.generate('second', path) / 'backend/WBS/TesztServiceImpl.java').read_text(encoding='utf-8')
        self.assertIn('import hu.ff.uj.RestResponseDto;', wbs)
        self.assertNotIn('hu.ff.xy.cl.modules.RestResponseDto', wbs)


if __name__ == '__main__':
    unittest.main()

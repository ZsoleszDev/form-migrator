"""cl_package: where the module's CL files go; module Java files without package line (IDE sets it)."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from frm_forms.cli import main

os.environ.setdefault('FRM_JAVA_IMPORT_MAP', '-')  # tests never read a developer's own java-imports.json
os.environ.setdefault('FRM_JAVA_VARIABLE_MAP', '-')  # nor a developer's own java-variables.json

HEADSTART = Path(__file__).with_name('golden') / 'headstart' / 'input.xml'
CL = 'hu.company.cl.pages.modules.xymodul'


class ClPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def generate(self, label, config, *extra, expect=0):
        path = self.root / (label + '.json')
        path.write_text(json.dumps({'java_import_map': '-', **config}), encoding='utf-8')
        out, err = self.root / label, io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = main(['migrate', str(HEADSTART), '--screen', '--module', 'teszt', '--config', str(path), '--out', str(out), *extra])
        self.assertEqual(code, expect, err.getvalue())
        return out, err.getvalue()

    def java(self, out):
        return {p.relative_to(out / 'backend').as_posix(): p.read_text(encoding='utf-8') for p in (out / 'backend').rglob('*.java')}

    def test_dps_and_wbs_import_the_module_cl_classes_from_the_given_package(self):
        files = self.java(self.generate('plain', {'cl_package': CL, 'java_empty_package': True})[0])
        self.assertIn(f'import {CL}.TesztConstants;', files['DPS/TesztServiceImpl.java'])
        # Single-type imports of the used DTO classes (Checkstyle AvoidStarImport), no wildcard.
        self.assertIn(f'import {CL}.TesztDtos.', files['DPS/TesztServiceImpl.java'])
        self.assertNotIn('.*;', files['DPS/TesztServiceImpl.java'])
        self.assertIn(f'import {CL}.TesztRestClientImpl;', files['WBS/TesztServiceImpl.java'])
        # Module files: no package line (IntelliJ offers the right one); the shared helper keeps its package.
        for name, text in files.items():
            self.assertEqual(text.lstrip().startswith('package '), name == 'CL/CommonMigrateTools.java', name)

    def test_company_mode_and_the_module_placeholder(self):
        files = self.java(self.generate('awu', {'cl_package': 'hu.company.cl.pages.modules.{module}'}, '--awu-azon', '1234')[0])
        imports = ''.join(text for name, text in files.items() if name.startswith(('DPS/', 'WBS/')))
        self.assertIn('import hu.company.cl.pages.modules.teszt.TesztConstants;', imports)
        self.assertIn('import hu.company.cl.pages.modules.teszt.TesztAitDto;', imports)
        self.assertTrue(files['CL/TesztConstants.java'].startswith('package hu.company.cl.pages.modules.teszt;'))

    def test_default_keeps_the_previous_packages_and_bad_values_are_refused(self):
        files = self.java(self.generate('default', {})[0])
        self.assertIn('import hu.company.features.teszt.cl.TesztConstants;', files['DPS/TesztServiceImpl.java'])
        self.assertTrue(files['DPS/TesztServiceImpl.java'].startswith('package hu.company.features.teszt.dps;'))
        for bad in ('Hu.Company.Cl', 'modul', 'hu.company.{modul}'):
            with self.subTest(bad=bad):
                _, err = self.generate('bad', {'cl_package': bad}, expect=1)
                self.assertIn('cl_package', err)


if __name__ == '__main__':
    unittest.main()

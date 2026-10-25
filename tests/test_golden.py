"""Golden outputs of real forms: every generator change becomes a reviewable diff.

A case is a folder: tests/golden/<eset>/input.xml, optionally with
  golden.json   {"args": [...migrate arguments...], "files": [...output globs...]}
  schema.json / rules.json / config.json   passed as --schema / --rules / --config
  expected/     the accepted output (created by the update mode)

Accept an intended change (then review the diff in version control):
  FRM_UPDATE_GOLDEN=1 python -m unittest tests.test_golden
"""
import contextlib
import difflib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from frm_forms.cli import main

os.environ.setdefault('FRM_JAVA_IMPORT_MAP', '-')  # tests never read a developer's own java-imports.json
os.environ.setdefault('FRM_JAVA_VARIABLE_MAP', '-')  # nor a developer's own java-variables.json

GOLDEN = Path(__file__).with_name('golden')
DEFAULT_ARGS = ['--screen', '--module', 'golden']
DEFAULT_FILES = ['backend/**/*.java', 'frontend/**/*.ts', 'frontend/**/*.md', 'analysis/backend-plan.json',
                 'analysis/backend-evidence.md', 'migration-report.md', 'BACKEND_TASKS.md']
UPDATE = os.environ.get('FRM_UPDATE_GOLDEN') == '1'


def cases():
    return sorted(d for d in GOLDEN.iterdir() if (d / 'input.xml').is_file()) if GOLDEN.is_dir() else []


class GoldenTests(unittest.TestCase):
    def test_golden_cases(self):
        if not cases():
            self.skipTest('Nincs golden eset (tests/golden/<eset>/input.xml).')
        for case in cases():
            with self.subTest(case=case.name):
                self.check(case)

    def check(self, case: Path):
        settings = json.loads((case / 'golden.json').read_text(encoding='utf-8')) if (case / 'golden.json').is_file() else {}
        args = settings.get('args', DEFAULT_ARGS)
        extra = []
        for side in ('schema', 'rules', 'config'):
            if (case / (side + '.json')).is_file():
                extra += ['--' + side, str(case / (side + '.json'))]
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / 'out'
            errors = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
                code = main(['migrate', str(case / 'input.xml'), '--out', str(out), *args, *extra])
            self.assertIn(code, (0, 3), errors.getvalue())
            actual = {p.relative_to(out).as_posix(): p for pattern in settings.get('files', DEFAULT_FILES)
                      for p in out.glob(pattern) if p.is_file()}
            expected_root = case / 'expected'
            if UPDATE:
                shutil.rmtree(expected_root, ignore_errors=True)
                for relative, path in actual.items():
                    target = expected_root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
                return
            expected = ({p.relative_to(expected_root).as_posix(): p for p in expected_root.rglob('*') if p.is_file()}
                        if expected_root.is_dir() else {})
            self.assertEqual(sorted(actual), sorted(expected),
                             case.name + ': a kimeneti fájlok listája változott. Szándékos változásnál: FRM_UPDATE_GOLDEN=1')
            for relative in sorted(actual):
                new = actual[relative].read_text(encoding='utf-8')
                old = expected[relative].read_text(encoding='utf-8')
                if new != old:
                    diff = list(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                                     'expected/' + relative, 'actual/' + relative))
                    self.fail(f'{case.name}: {relative} eltér (szándékos változásnál: FRM_UPDATE_GOLDEN=1)\n' + ''.join(diff[:80]))


if __name__ == '__main__':
    unittest.main()

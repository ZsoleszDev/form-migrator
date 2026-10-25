import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from frm_forms.cli import main
from java_support import COMPANY_IMPORTS, write_stubs

ROOT = Path(__file__).resolve().parents[1]


class JavaTests(unittest.TestCase):
    def test_generated_java_compiles_and_rule_service_semantics(self):
        if not shutil.which('java'):
            self.skipTest('Java 17+ szükséges a generált Java ellenőrzéséhez.')
        probe = subprocess.run(['java', 'com.sun.tools.javac.Main', '-version'], capture_output=True, text=True)
        if probe.returncode:
            self.skipTest('A java elérhető, de a jdk.compiler modul nincs telepítve.')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            out = root / 'customer'
            company = root / 'company.json'  # the log1x / UserDto company classes of java_support
            company.write_text(json.dumps({'java_company_imports': COMPANY_IMPORTS}))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['migrate', str(ROOT / 'examples/customer_fmb.xml'), '--out', str(out), '--module', 'customer', '--schema', str(ROOT / 'examples/schema.json'), '--config', str(company)]), 0)
                self.assertEqual(main(['migrate', str(ROOT / 'examples/review_fmb.xml'), '--out', str(root / 'review'), '--module', 'review', '--config', str(company)]), 0)
            stubs = write_stubs(root / 'test-api-stubs')
            smoke = root / 'RuntimeSmoke.java'
            smoke.write_text((ROOT / 'tests/RuntimeSmoke.java.txt').read_text(encoding='utf-8'), encoding='utf-8')
            memory = root / 'MemoryCustomerJdbc.java'
            memory.write_text((ROOT / 'tests/MemoryCustomerJdbc.java.txt').read_text(encoding='utf-8'), encoding='utf-8')
            # Two modules, one shared CommonMigrateTools - as in a real project.
            sources = list(out.rglob('*.java')) + [p for p in (root / 'review').rglob('*.java') if p.name != 'CommonMigrateTools.java'] + stubs + [smoke, memory]
            classes = root / 'classes'
            # Use javac's argfile to avoid Windows command-line length limits.
            args_file = root / 'sources.args'
            args_file.write_text('\n'.join('"' + str(p).replace('\\', '/') + '"' for p in sources), encoding='utf-8')
            build = subprocess.run(['java', 'com.sun.tools.javac.Main', '-encoding', 'UTF-8', '--release', '11', '-d', str(classes), '@' + str(args_file)], capture_output=True, text=True, timeout=60)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run(['java', '-cp', str(classes), 'hu.company.features.customer.dps.RuntimeSmoke'], capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn('runtime assertions passed', run.stdout)


if __name__ == '__main__': unittest.main()

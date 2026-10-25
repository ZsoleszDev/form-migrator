"""4.14: attached PL/SQL libraries (.pld) - the units the form reaches compile like its own program units."""
import java_support  # noqa: F401  (legacy trigger mode for these engine tests)
import contextlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest

from frm_forms.cli import main
from frm_forms.common import MigrationError
from frm_forms.framework import load
from frm_forms.libraries import attach, decode, library_name, load_libraries, parse_pld

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/felmeres_replika_fmb.xml'
PLD = ROOT / 'tests/fixtures/anklib.pld'

PACKAGE = """.attach LIBRARY QMSLIB65 END NOCONDENSE
/* Copyright */
PACKAGE ank_jog IS
  g_x CONSTANT NUMBER := CASE WHEN 1 = 1 THEN 1 ELSE 2 END;
  FUNCTION irhat RETURN BOOLEAN;
END ank_jog;

PACKAGE BODY ank_jog IS
  v_cnt NUMBER;
  FUNCTION irhat RETURN BOOLEAN IS
    PROCEDURE inner IS BEGIN NULL; END;
  BEGIN
    FOR r IN (SELECT 1 FROM dual) LOOP
      IF r IS NULL THEN NULL; END IF;
    END LOOP;
    RETURN TRUE;
  END irhat;
BEGIN
  v_cnt := 0;
END ank_jog;
/
CREATE OR REPLACE PROCEDURE naplo_init IS
BEGIN
  message('x;y');  -- END;
END;
FUNCTION f(a NUMBER) RETURN NUMBER IS BEGIN RETURN CASE a WHEN 1 THEN 2 END; END f;
"""


def form_with_library(name='ANKLIB'):
    """The survey replica form with an attached library (the replica itself attaches none)."""
    text = FIXTURE.read_text(encoding='utf-8')
    anchor = '  <ProgramUnit Name="BLOKK_FRISSIT"'
    return text.replace(anchor, f'  <AttachedLibrary Name="{name}" LibraryLocation="C:\\forms\\{name.lower()}.pll"/>\n' + anchor)


def model(units=(), libraries=('ANKLIB',), triggers=()):
    return {'name': 'F', 'libraries': [{'name': n} for n in libraries],
            'program_units': [{'name': n, 'programunittype': k, 'programunittext': t} for n, k, t in units],
            'triggers': [{'id': 'F:' + str(i), 'source': s} for i, s in enumerate(triggers)], 'issues': []}


class PldParserTests(unittest.TestCase):
    def test_units_packages_and_attached_libraries(self):
        parsed = parse_pld(PACKAGE, 'ANKLIB')
        self.assertEqual(parsed['attached'], ['QMSLIB65'])
        self.assertEqual(sorted(parsed['units']), ['ANK_JOG', 'F', 'NAPLO_INIT'])
        package = parsed['units']['ANK_JOG']
        self.assertEqual(package['kind'], 'package')
        self.assertTrue(package['spec'].startswith('PACKAGE ank_jog IS') and package['spec'].endswith('END ank_jog;'))
        self.assertTrue(package['body'].startswith('PACKAGE BODY') and package['body'].endswith('v_cnt := 0;\nEND ank_jog;'))
        # CREATE OR REPLACE is dropped: the unit becomes a local declaration of an anonymous block.
        self.assertTrue(parsed['units']['NAPLO_INIT']['text'].startswith('PROCEDURE naplo_init IS'))
        self.assertIn("-- END;\nEND;", parsed['units']['NAPLO_INIT']['text'])
        self.assertEqual(parsed['units']['F']['kind'], 'function')

    def test_rejects_what_is_not_a_program_unit_with_the_line(self):
        with self.assertRaisesRegex(MigrationError, r'3\. sorban nem programegység'):
            parse_pld('PROCEDURE a IS BEGIN NULL; END;\n\nDECLARE x NUMBER; BEGIN NULL; END;', 'X')
        with self.assertRaisesRegex(MigrationError, 'lezáratlan BEGIN'):
            parse_pld('PROCEDURE p IS BEGIN NULL;', 'X')
        with self.assertRaisesRegex(MigrationError, 'ismétlődő programegység'):
            parse_pld('PROCEDURE p IS BEGIN NULL; END;\nPROCEDURE p IS BEGIN NULL; END;', 'X')

    def test_names_and_code_pages(self):
        self.assertEqual(library_name(Path('C:\\forms\\qmslib65.pll')), 'QMSLIB65')
        self.assertEqual(decode('árvíztűrő'.encode('cp1250')), 'árvíztűrő')
        self.assertEqual(decode('árvíztűrő'.encode('utf-8')), 'árvíztűrő')


class AttachTests(unittest.TestCase):
    def libraries(self, text=None, name='ANKLIB'):
        parsed = parse_pld(text or PLD.read_text(encoding='utf-8'), name)
        return [{**parsed, 'file': name.lower() + '.pld', 'sha256': '0'}]

    def test_only_reachable_units_are_added_transitively(self):
        m = model(triggers=["BEGIN ank_ctrl.set_title('X'); END;"],
                  units=[('P', 'Procedure', 'PROCEDURE p IS BEGIN naplo_init(1); END;')])
        report = attach(m, self.libraries(), load({}))
        added = {(u['name'], u['programunittype']) for u in m['program_units'] if u.get('frm_library')}
        self.assertEqual(added, {('ANK_CTRL', 'Package Spec'), ('ANK_CTRL', 'Package Body'), ('NAPLO_INIT', 'Procedure')})
        self.assertNotIn('NEM_HASZNALT', json.dumps(report['libraries'][0]['used_units']))
        self.assertEqual(report['scope'], ['ANKLIB', 'QMSLIB65'])
        self.assertEqual(report['missing'], ['QMSLIB65'])

    def test_form_unit_wins_and_catalogued_framework_stays_plumbing(self):
        text = PLD.read_text(encoding='utf-8') + "\nPROCEDURE qms$event_form(p VARCHAR2) IS BEGIN NULL; END;\n"
        m = model(triggers=["naplo_init('A'); qms$event_form('X');"],
                  units=[('NAPLO_INIT', 'Procedure', 'PROCEDURE naplo_init(p VARCHAR2) IS BEGIN NULL; END;')])
        attach(m, self.libraries(text), load({}))
        self.assertEqual([u.get('frm_library') for u in m['program_units']], [None])

    def test_library_of_another_form_is_out_of_scope(self):
        m = model(libraries=(), triggers=["naplo_init('A');"])
        report = attach(m, self.libraries(), load({}))
        self.assertFalse(report['libraries'][0]['in_scope'])
        self.assertEqual(len(m['program_units']), 0)

    def test_load_rejects_duplicates_and_unknown_files(self):
        with tempfile.TemporaryDirectory() as temp:
            first, second = Path(temp, 'a', 'lib.pld'), Path(temp, 'b', 'LIB.pld')
            for path in (first, second):
                path.parent.mkdir()
                path.write_text('PROCEDURE p IS BEGIN NULL; END;', encoding='utf-8')
            with self.assertRaisesRegex(MigrationError, 'kétszer'):
                load_libraries([first, second], {})
            with self.assertRaisesRegex(MigrationError, r'\.pld'):
                load_libraries([Path(temp, 'x.txt')], {})
            with self.assertRaisesRegex(MigrationError, 'frmcmp'):
                load_libraries([self.pll(Path(temp))], {}, Path(temp) / 'stage')  # no Forms Compiler here

    @staticmethod
    def pll(folder):
        path = folder / 'bin.pll'
        path.write_bytes(b'\x00\x01binary')
        return path


class LibraryMigrationTests(unittest.TestCase):
    def run_migration(self, *extra, source=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        folder = Path(temp.name)
        form = folder / 'rendeles_fmb.xml'
        form.write_text(source or form_with_library(), encoding='utf-8')
        out = folder / 'out'
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(['migrate', str(form), '--screen', '--module', 'pelda', '--out', str(out), '--ai', 'off', *extra])
        self.assertIn(code, (0, 3))
        return out

    def test_library_units_are_inlined_and_unused_ones_stay_out(self):
        out = self.run_migration('--pld', str(PLD))
        service = (out / 'backend/DPS/PeldaServiceImpl.java').read_text(encoding='utf-8')
        self.assertIn('PlsqlUnits.ANK_CTRL_UI', service)
        self.assertIn('NAPLO_INIT (procedure, csatolt könyvtár: ANKLIB)', service)
        self.assertIn("frm_cmd('SET_WINDOW_PROPERTY', 'FORMS_MDI_WINDOW', 'TITLE', p_title);", service)
        self.assertNotIn('NEM_HASZNALT', service.upper())
        # ANK_MENU is in no given library: the database still resolves it.
        self.assertIn('Az adatbázis oldja fel futáskor: ANK_MENU.INIT. A formhoz csatolt könyvtár(ak): QMSLIB65', service)
        report = json.loads((out / 'analysis/libraries.json').read_text(encoding='utf-8'))
        self.assertEqual(report['libraries'][0]['used_units'], ['ANK_CTRL', 'ANK_JOG', 'NAPLO_INIT'])
        issues = json.loads((out / 'analysis/form.ir.json').read_text(encoding='utf-8'))['issues']
        self.assertTrue(any('beolvasva (PLD): ANKLIB' in i['detail'] for i in issues if i['code'] == 'ATTACHED_LIBRARY'))

    def test_without_the_library_the_calls_go_to_the_database_as_before(self):
        out = self.run_migration()
        service = (out / 'backend/DPS/PeldaServiceImpl.java').read_text(encoding='utf-8')
        self.assertNotIn('csatolt könyvtár: ANKLIB', service)
        self.assertIn('ANK_CTRL.SET_TITLE', service)
        report = json.loads((out / 'analysis/libraries.json').read_text(encoding='utf-8'))
        self.assertEqual(report['missing'], ['ANKLIB'])


try:
    from fastapi.testclient import TestClient
    from frm_forms.web.app import create_app
    from frm_forms.web.settings import Settings
    WEB_AVAILABLE = True
except ImportError:
    WEB_AVAILABLE = False


@unittest.skipUnless(WEB_AVAILABLE, 'Web API tesztekhez: pip install -r requirements-test.txt')
class LibraryUploadTests(unittest.TestCase):
    HEADERS = {'Origin': 'http://localhost:4200', 'X-Frm-Client': 'local-ui'}

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.settings = Settings(data_dir=Path(temp.name) / 'data')
        self.client = TestClient(create_app(self.settings), base_url='http://localhost:8000')
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def post(self, library_name='anklib.pld'):
        files = [('file', ('rendeles_fmb.xml', form_with_library().encode('utf-8'), 'application/xml')),
                 ('pld_files', (library_name, PLD.read_bytes(), 'text/plain'))]
        return self.client.post('/api/jobs', files=files, headers=self.HEADERS,
                                data={'options': json.dumps({'module': 'pelda', 'generation_mode': 'screen',
                                                             'screen_window_selection': 'all'})})

    def test_uploaded_library_reaches_the_generator(self):
        response = self.post()
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()['id']
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = self.client.get('/api/jobs/' + job_id).json()
            if job['status'] in {'completed', 'failed', 'cancelled', 'interrupted', 'needs_input'}:
                break
            time.sleep(0.05)
        self.assertEqual(job['status'], 'completed', job)
        report = json.loads((self.settings.data_dir / 'jobs' / job_id / 'module/analysis/libraries.json').read_text())
        self.assertEqual(report['libraries'][0]['used_units'], ['ANK_CTRL', 'ANK_JOG', 'NAPLO_INIT'])

    def test_library_file_name_is_checked(self):
        self.assertEqual(self.post('anklib.txt').status_code, 400)


if __name__ == '__main__':
    unittest.main()

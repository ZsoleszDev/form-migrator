"""4.24.1: a large form never stops the whole migration, and a failure says what and where.

- Deep code (a value built of 1500 || pieces) is a deep syntax tree: the migration runs on a thread with a large
  stack, json.dumps of form.ir.json and the parsers do not stop with RecursionError.
- An unexpected error of one trigger's adapter (a bug of the migrator) is that trigger's reason, not the end of the
  run; its traceback goes to the log.
- A failed web job's error names the exception and its place (the log tab is hidden in the UI: the overview shows
  the end of the log under the error).
"""
import java_support  # noqa: F401  (legacy trigger mode for these engine tests)
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from frm_forms.cli import main
from frm_forms.common import failure_report, run_deep
from frm_forms.web import worker
from frm_forms.web.jobs import failure_message
import test_query_actions as qa


def long_concatenation_form(pieces=1500) -> bytes:
    root = ET.fromstring(qa.fixture())
    controls = next(b for b in root.find('FormModule').findall('Block') if b.get('Name') == 'T1')
    item = ET.SubElement(controls, 'Item', Name='PB_HOSSZU', ItemType='Push Button', CanvasName='C', XPosition='300',
                         YPosition='60', Width='80', Height='20', Label='Hosszú')
    ET.SubElement(item, 'Trigger', Name='WHEN-BUTTON-PRESSED',
                  TriggerText=':T1.COL1 := ' + ' || '.join("'x%d'" % i for i in range(pieces)) + ';')
    return ET.tostring(root)


def migrate(root: Path, xml: bytes) -> tuple[int, str]:
    (root / 'in.xml').write_bytes(xml)
    (root / 'config.json').write_text(json.dumps({'backend_live': True, 'java_import_map': '-'}))
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        code = main(['migrate', str(root / 'in.xml'), '--module', 'robust', '--screen', '--config', str(root / 'config.json'),
                     '--out', str(root / 'out')])
    return code, err.getvalue()


class DeepCodeTests(unittest.TestCase):
    def test_a_very_long_concatenation_does_not_stop_the_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            code, err = migrate(root, long_concatenation_form())
            self.assertEqual(code, 0, err[-2000:])
            model = json.loads((root / 'out/analysis/form.ir.json').read_text(encoding='utf-8'))
            trigger = next(t for t in model['triggers'] if t['owner'] == 'T1.PB_HOSSZU')
            self.assertEqual(trigger['status'], 'converted')
            self.assertNotIn('_cache', model)  # the per-run cache is never written out

    def test_run_deep_returns_the_value_and_raises_the_error(self):
        def depth(n):
            return 0 if n == 0 else 1 + depth(n - 1)
        self.assertEqual(run_deep(depth, 20000), 20000)
        with self.assertRaises(KeyError):
            run_deep(lambda: {}['nincs'])


class AdapterSafetyNetTests(unittest.TestCase):
    def test_a_bug_of_one_adapter_is_that_trigger_s_reason(self):
        with tempfile.TemporaryDirectory() as temp, patch('frm_forms.query_java.plan', side_effect=KeyError('X')):
            root = Path(temp)
            code, err = migrate(root, qa.fixture())
            self.assertEqual(code, 0)
            model = json.loads((root / 'out/analysis/form.ir.json').read_text(encoding='utf-8'))
            trigger = next(t for t in model['triggers'] if t['event'] == 'WHEN-BUTTON-PRESSED')
            self.assertTrue(trigger['query_java_reason'].startswith("Belső hiba a migrátorban (KeyError: 'X'"),
                            trigger['query_java_reason'])
            self.assertIn('Traceback', err)  # the details for the log
            self.assertIsNotNone(trigger.get('query_action'))  # the PL/SQL query adapter took over


class FailureMessageTests(unittest.TestCase):
    def test_the_worker_names_the_exception_and_its_place(self):
        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp)
            (job / 'job.json').write_text(json.dumps({'source_type': '.xml', 'options': {
                'module': 'demo', 'ai_mode': 'off', 'strict': False}}))
            err = io.StringIO()

            def broken(args, on_progress):
                return {}['ITEM_TYPE']
            with patch('frm_forms.web.worker.migration', side_effect=broken), contextlib.redirect_stderr(err):
                self.assertEqual(worker.run(job), 1)
            log = err.getvalue()
            self.assertIn('Traceback (most recent call last)', log)
            message = failure_message(log, 1)
            self.assertTrue(message.startswith("Váratlan belső hiba (KeyError): 'ITEM_TYPE' (hely: "), message)

    def test_messages_without_a_hiba_line(self):
        traceback = 'Traceback (most recent call last):\n  File "x.py", line 1\nRecursionError: maximum recursion depth exceeded\n'
        self.assertTrue(failure_message(traceback, 1).startswith('Váratlan belső hiba: RecursionError: maximum recursion'))
        self.assertIn('SIGKILL (valószínűleg elfogyott a memória)', failure_message('', -9))
        self.assertIn('verem-túlcsordulás', failure_message('', 3221225725))
        self.assertIn('verem-túlcsordulás', failure_message('', -1073741571))
        self.assertIn('kilépési kód 2', failure_message('valami\n', 2))
        self.assertEqual(failure_message('x\nHIBA: Nincs ilyen fájl\n', 1), 'Nincs ilyen fájl')

    def test_an_expected_failure_stays_short(self):
        self.assertEqual(failure_report(FileNotFoundError('nincs.fmb')), 'HIBA: nincs.fmb')
        try:
            raise RecursionError('maximum recursion depth exceeded')
        except RecursionError as exc:
            report = failure_report(exc)
        self.assertIn('HIBA: Váratlan belső hiba (RecursionError): maximum recursion depth exceeded (hely: ', report)
        self.assertTrue(report.rstrip().endswith('A forrásban túl mélyen beágyazott szerkezet van.'))


if __name__ == '__main__':
    unittest.main()

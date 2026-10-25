import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from frm_forms.web.file_io import atomic_json
from frm_forms.web.worker import run


def locked():
    error = PermissionError('Windows sharing conflict')
    error.winerror = 5
    return error


class WindowsProgressTests(unittest.TestCase):
    def test_transient_lock(self):
        real_replace = os.replace
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'progress.json'
            atomic_json(path, {'phase': 'old'})
            calls = []
            def replace(source, target):
                calls.append(source)
                if len(calls) < 3:
                    self.assertEqual(json.loads(path.read_text()), {'phase': 'old'})
                    raise locked()
                real_replace(source, target)
            with patch('frm_forms.web.file_io.os.replace', side_effect=replace), patch('frm_forms.web.file_io.time.sleep'):
                atomic_json(path, {'phase': 'new'})
            self.assertEqual(json.loads(path.read_text()), {'phase': 'new'})
            self.assertEqual(len(calls), 3)
            self.assertEqual(list(Path(folder).glob('*.tmp')), [])

    def test_persistent_lock_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'job.json'
            atomic_json(path, {'status': 'old'})
            with patch('frm_forms.web.file_io.os.replace', side_effect=locked()) as replace, patch('frm_forms.web.file_io.time.sleep'):
                with self.assertRaises(PermissionError):
                    atomic_json(path, {'status': 'new'})
            self.assertEqual(replace.call_count, 7)
            self.assertEqual(json.loads(path.read_text()), {'status': 'old'})
            self.assertEqual(list(Path(folder).glob('*.tmp')), [])

    def test_progress_failure_does_not_abort(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'job.json').write_text(json.dumps({'source_type': '.xml', 'options': {'module': 'demo', 'ai_mode': 'off', 'strict': False}}))
            def migrate(args, on_progress):
                on_progress('parse')
                on_progress('generate')
                return 3
            with patch('frm_forms.web.worker.migration', side_effect=migrate), patch('frm_forms.web.worker.atomic_json', side_effect=locked()), patch('sys.stderr'):
                self.assertEqual(run(root), 3)

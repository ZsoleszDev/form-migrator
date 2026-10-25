import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from frm_forms.common import MigrationError
from frm_forms.exporter import export_fmb
from frm_forms.ollama import advise, validate_advice


class FakeResponse(io.BytesIO):
    pass


class FakeOpener:
    def __init__(self, response=None, error=None):
        self.requests = []
        self.response = response
        self.error = error

    def open(self, request, timeout):
        self.requests.append(json.loads(request.data))
        if self.error: raise self.error
        return FakeResponse(json.dumps(self.response).encode())


class OllamaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)
        self.response = {'done': True, 'response': json.dumps({'target': 'backend', 'summary': 'Külső eljárás.', 'steps': ['Adapter szükséges.']}), 'prompt_eval_count': 123, 'eval_count': 20}

    def model(self, count=1):
        return {'blocks': [{'name': 'B', 'items': [{'name': 'ID', 'type': 'number'}]}], 'triggers': [{'status': 'review', 'source': f'UNKNOWN{i}(:B.ID);', 'event': 'PRE-INSERT', 'owner': 'B'} for i in range(count)]}

    def test_budget_cache_offline_and_no_auto_promotion(self):
        client = FakeOpener(self.response)
        model = self.model(3)
        with patch('frm_forms.ollama.opener', return_value=client):
            stats = advise(model, 'assist', {'max_ai_calls': 1}, self.cache)
        self.assertEqual((stats['attempted_calls'], stats['skipped_budget']), (1, 2))
        self.assertEqual(model['triggers'][0]['status'], 'review')
        self.assertFalse(client.requests[0]['stream'])
        self.assertEqual(client.requests[0]['options']['num_ctx'], 2048)
        with patch('frm_forms.ollama.opener', side_effect=AssertionError('cached mode network')):
            cached = advise(self.model(), 'cached', {'max_ai_calls': 1}, self.cache)
        self.assertEqual(cached['cache_hits'], 1)

    def test_failed_request_consumes_budget_no_retry(self):
        client = FakeOpener(error=TimeoutError('mock timeout'))
        with patch('frm_forms.ollama.opener', return_value=client):
            stats = advise(self.model(2), 'assist', {'max_ai_calls': 1}, self.cache)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(stats['failures'], 1)

    def test_invalid_json_and_truncated_response_never_cached(self):
        for response in [{'done': True, 'response': 'not json'}, {'done': True, 'done_reason': 'length', 'response': self.response['response']}]:
            with patch('frm_forms.ollama.opener', return_value=FakeOpener(response)):
                stats = advise(self.model(), 'assist', {}, self.cache)
            self.assertEqual(stats['failures'], 1)
        self.assertEqual(list(self.cache.glob('*.json')), [])

    def test_long_source_is_skipped_not_truncated(self):
        model = self.model()
        model['triggers'][0]['source'] = 'x' * 1201
        with patch('frm_forms.ollama.opener', side_effect=AssertionError('oversized source request')):
            stats = advise(model, 'assist', {}, self.cache)
        self.assertEqual(stats['skipped_size'], 1)

    def test_cache_salt_invalidates_previous_model_revision(self):
        with patch('frm_forms.ollama.opener', return_value=FakeOpener(self.response)):
            advise(self.model(), 'assist', {}, self.cache)
        stats = advise(self.model(), 'cached', {'ai_cache_salt': '2'}, self.cache)
        self.assertEqual(stats['cache_misses'], 1)

    def test_response_schema_rejects_code_keys(self):
        with self.assertRaises(ValueError): validate_advice({'target': 'backend', 'summary': 'x', 'steps': [], 'java': 'arbitrary code'})

    def test_default_budget_is_one_and_families_share_only_advice(self):
        import copy
        model=self.model(2)
        model['triggers'] += [copy.deepcopy(model['triggers'][0]) for _ in range(5)]
        client=FakeOpener(self.response)
        with patch('frm_forms.ollama.opener',return_value=client):
            stats=advise(model,'assist',{},self.cache)
        self.assertEqual(stats['attempted_calls'],1);self.assertEqual(stats['unique_candidates'],2)
        self.assertEqual(stats['reused_advice'],5)
        self.assertTrue(all(tr['status']=='review' for tr in model['triggers']))
        self.assertLessEqual(len(client.requests[0]['prompt'].encode()),3200)

    def test_backend_priority_and_small_complete_dependency(self):
        model=self.model(2)
        model['triggers'][0]['event']='WHEN-BUTTON-PRESSED'
        model['triggers'][1]['source']='CHECK_IT(:B.ID);'
        source='PROCEDURE CHECK_IT(ID NUMBER) IS BEGIN UNKNOWN(ID); END;'
        model['program_units']=[{'name':'CHECK_IT','programunittext':source}]
        client=FakeOpener(self.response)
        with patch('frm_forms.ollama.opener',return_value=client): stats=advise(model,'assist',{},self.cache)
        self.assertEqual(stats['attempted_calls'],1)
        self.assertIn(source,client.requests[0]['prompt'])
        self.assertEqual(model['triggers'][0]['ai_status'],'skipped_budget')

    def test_failed_identical_family_is_not_retried(self):
        import copy
        model=self.model();model['triggers']*=8
        model['triggers']=[copy.deepcopy(t) for t in model['triggers']]
        client=FakeOpener(error=TimeoutError('offline'))
        with patch('frm_forms.ollama.opener',return_value=client):stats=advise(model,'assist',{'max_ai_calls':5},self.cache)
        self.assertEqual(len(client.requests),1);self.assertEqual(stats['failures'],1)

    def test_large_local_unit_is_omitted_whole(self):
        model=self.model();model['triggers'][0]['source']='LARGE;'
        model['program_units']=[{'name':'LARGE','programunittext':'PROCEDURE LARGE IS BEGIN '+('NULL;'*400)+' END;'}]
        client=FakeOpener(self.response)
        with patch('frm_forms.ollama.opener',return_value=client):advise(model,'assist',{},self.cache)
        self.assertIn('omitted_local_dependencies',client.requests[0]['prompt'])
        self.assertNotIn('PROCEDURE LARGE',client.requests[0]['prompt'])


class ExportTests(unittest.TestCase):
    def test_export_command_uses_literal_argument_and_expected_cwd(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'name with spaces & literal.fmb'
            source.write_bytes(b'\x00FMB\x01')
            script = root / 'export_stub.py'
            script.write_text("import pathlib, sys\np=pathlib.Path(sys.argv[1])\nassert p.read_bytes() == b'\\x00FMB\\x01'\npathlib.Path(p.stem+'_fmb.xml').write_text('<Module/>')\n")
            output = export_fmb(source, root / 'export', {'export_command': [sys.executable, str(script), '{input}']})
            self.assertEqual(output.read_text(), '<Module/>')
            self.assertTrue((output.parent / 'export.log').exists())

    def test_export_success_without_xml_is_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'input.fmb'
            source.write_bytes(b'binary')
            with self.assertRaises(MigrationError):
                export_fmb(source, root / 'export', {'export_command': [sys.executable, '-c', 'pass', '{input}']})

    def test_missing_oracle_binary_actionable_error(self):
        with patch('frm_forms.exporter.shutil.which', return_value=None), tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(MigrationError, 'FMB bináris'):
                export_fmb(Path(temp) / 'x.fmb', Path(temp) / 'export', {})


if __name__ == '__main__': unittest.main()

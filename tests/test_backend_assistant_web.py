"""Real local API, worker, mocked Ollama, proposal ZIP and reviewed apply."""
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from test_backend_assistant import answer, Client
from test_manual_action_source import FORM

try:
    from fastapi.testclient import TestClient
    from frm_forms.web.app import create_app
    from frm_forms.web.settings import Settings
    WEB = True
except ImportError:
    WEB = False


@unittest.skipUnless(WEB, 'pip install -r requirements-test.txt')
class AssistantWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.settings = Settings(data_dir=Path(self.temp.name), folder_dialog=False)
        self.app = create_app(self.settings)
        self.client = TestClient(self.app, base_url='http://localhost')
        self.client.__enter__(); self.addCleanup(self.client.__exit__, None, None, None)
        submitted = self.client.post('/api/jobs', headers={'X-Frm-Client':'local-ui'},
            files={'file':('form.xml',FORM.encode(),'application/xml')},
            data={'options':json.dumps({'module':'kezi','screen_window_selection':'all'})})
        self.assertEqual(submitted.status_code, 202, submitted.text)
        self.job = submitted.json()['id']
        for _ in range(150):
            job = self.client.get('/api/jobs/' + self.job).json()
            if job['status'] in {'completed','failed','needs_input'}: break
            time.sleep(.02)
        self.assertEqual(job['status'], 'completed', job)
        self.base = '/api/jobs/' + self.job + '/assistant'
        self.index = self.client.get(self.base).json()
        self.task = next(t for t in self.index['tasks'] if t['method']=='onCgnvW011PbLekerdezes')

    def run_proposal(self):
        with patch('frm_forms.ollama.opener', return_value=Client(answer())):
            started = self.client.post(self.base, headers={'X-Frm-Client':'local-ui'},
                json={'task_id':self.task['id'],'num_ctx':32768})
            self.assertEqual(started.status_code, 202, started.text)
            run_id = started.json()['id']
            for _ in range(150):
                run = self.client.get(self.base + '/runs/' + run_id).json()
                if run['status'] != 'running': break
                time.sleep(.02)
        self.assertEqual(run['status'], 'completed', run)
        return run['result']

    def test_ui_and_new_routes_guard_proposals_and_apply(self):
        page = self.client.get('/assistant?job=' + self.job, follow_redirects=False)
        self.assertEqual(page.status_code, 307)
        self.assertEqual(page.headers['location'], '/?view=assistant&job=' + self.job)
        root = self.client.get('/')
        self.assertEqual(root.status_code, 200)
        self.assertIn('<app-migrator>', root.text)
        self.assertNotIn('backend-assistant.js', root.text)
        self.assertEqual(self.client.get('/assistant.js').status_code, 404)
        rejected = self.client.post(self.base, json={'task_id':self.task['id']})
        self.assertEqual(rejected.status_code, 403)
        result = self.run_proposal()
        proposal_url = self.base + '/proposals/' + result['id']
        self.assertEqual(self.client.get(proposal_url).json()['status'], 'proposal')
        archive = self.client.get(proposal_url + '/download')
        self.assertEqual(archive.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(archive.content)) as z:
            self.assertIn('REVIEW.md', z.namelist())
            self.assertTrue(any(p.endswith('.java.txt.patch') for p in z.namelist()))
        self.assertEqual(self.client.post(proposal_url + '/apply', headers={'X-Frm-Client':'local-ui'}, json={'reviewed':False}).status_code, 422)
        applied = self.client.post(proposal_url + '/apply', headers={'X-Frm-Client':'local-ui'}, json={'reviewed':True})
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertTrue(applied.json()['applied'])
        archive = self.client.get('/api/jobs/' + self.job + '/download?kind=backend')
        with zipfile.ZipFile(io.BytesIO(archive.content)) as z:
            service = z.read('backend/DPS/KeziServiceImpl.java').decode()
            self.assertIn('List.of("minta")', service)
        self.assertIn('import java.util.List;', service)
        index = self.client.get(self.base).json()
        self.assertTrue(next(p for p in index['proposals'] if p['id']==result['id'])['applied'])

    def test_parallel_runs_and_deletion_are_rejected_while_busy(self):
        waiting, release = threading.Event(), threading.Event()
        class SlowClient(Client):
            def open(self, request, timeout):
                waiting.set(); release.wait(3)
                return super().open(request, timeout)
        with patch('frm_forms.ollama.opener',return_value=SlowClient(answer())):
            started = self.client.post(self.base, headers={'X-Frm-Client':'local-ui'},json={'task_id':self.task['id'],'num_ctx':32768})
            self.assertEqual(started.status_code, 202)
            self.assertTrue(waiting.wait(2))
            restored = self.client.get(self.base).json()['active_run']
            self.assertEqual(restored, {'id': started.json()['id'], 'status': 'running', 'task_id': self.task['id'],
                                        'exchange_id': started.json()['exchange_id']})
            # While the model works, the request that was sent is already readable.
            log = self.client.get(self.base + '/exchanges/' + restored['exchange_id'])
            self.assertEqual(log.status_code, 200, log.text)
            self.assertEqual(log.json()['status'], 'running')
            self.assertIn('onCgnvW011PbLekerdezes', log.json()['prompt']['user'])
            second = self.client.post(self.base, headers={'X-Frm-Client':'local-ui'},json={'task_id':self.task['id']})
            self.assertEqual(second.status_code, 409)
            deleted = self.client.delete('/api/jobs/'+self.job, headers={'X-Frm-Client':'local-ui'})
            self.assertEqual(deleted.status_code, 409)
            release.set()
            for _ in range(150):
                state = self.client.get(self.base + '/runs/' + started.json()['id']).json()
                if state['status'] != 'running': break
                time.sleep(.02)
            self.assertEqual(state['status'], 'completed', state)

    def test_failed_run_with_empty_exception_has_a_reason_in_api_and_log(self):
        with self.assertLogs('frm_forms.web.assistant', level='ERROR') as messages:
            with patch('frm_forms.web.assistant.engine.propose', side_effect=TimeoutError()):
                started = self.client.post(self.base, headers={'X-Frm-Client':'local-ui'}, json={'task_id':self.task['id']})
                self.assertEqual(started.status_code, 202, started.text)
                for _ in range(150):
                    state = self.client.get(self.base + '/runs/' + started.json()['id']).json()
                    if state['status'] != 'running': break
                    time.sleep(.02)
        self.assertEqual(state['status'], 'failed', state)
        self.assertEqual(state['error'], 'TimeoutError')
        self.assertTrue(any('TimeoutError' in line for line in messages.output))
        self.assertEqual(self.client.get(self.base).json()['recent_run']['error'], 'TimeoutError')

    def test_ai_log_routes_show_prompt_answer_and_preview(self):
        result = self.run_proposal()
        listed = self.client.get(self.base + '/exchanges', params={'task_id': self.task['id']}).json()['exchanges']
        self.assertEqual([item['id'] for item in listed], [result['exchange_id']])
        self.assertEqual(listed[0]['proposal_id'], result['id'])
        log = self.client.get(self.base + '/exchanges/' + result['exchange_id']).json()
        self.assertEqual(log['status'], 'completed')
        self.assertEqual(json.loads(log['attempts'][0]['raw'])['status'], 'proposal')
        self.assertIn('```java\npublic ActionResult onCgnvW011PbLekerdezes', log['prompt']['user'])
        self.assertEqual(self.client.post(self.base + '/prompt', json={'task_id': self.task['id']}).status_code, 403)
        preview = self.client.post(self.base + '/prompt', headers={'X-Frm-Client': 'local-ui'},
                                   json={'task_id': self.task['id'], 'num_ctx': 32768})
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()['user'], log['prompt']['user'])
        self.assertEqual(self.client.get(self.base + '/exchanges/zz').status_code, 422)
        self.assertEqual(self.client.get(self.base + '/exchanges/' + '0' * 20).status_code, 404)
        page = self.client.get('/ai-log?job=' + self.job)
        self.assertEqual(page.status_code, 200)
        self.assertIn('AI-napló', page.text)
        self.assertIn("connect-src 'self'", page.headers['content-security-policy'])
        self.assertEqual(self.client.get('/api/jobs/' + self.job + '/assistant/nincs-ilyen').status_code, 404)

    def test_rejected_answers_are_explained_with_a_link_to_the_log(self):
        with patch('frm_forms.ollama.opener', return_value=Client('Nem tudom.', 'Most sem tudom.')):
            started = self.client.post(self.base, headers={'X-Frm-Client': 'local-ui'},
                                       json={'task_id': self.task['id'], 'attempts': 2})
            self.assertEqual(started.status_code, 202, started.text)
            for _ in range(250):
                state = self.client.get(self.base + '/runs/' + started.json()['id']).json()
                if state['status'] != 'running': break
                time.sleep(.02)
        self.assertEqual(state['status'], 'failed', state)
        self.assertIn('/ai-log?job=' + self.job + '&exchange=' + started.json()['exchange_id'], state['error'])
        self.assertEqual(self.client.get(self.base).json()['recent_run']['exchange_id'], started.json()['exchange_id'])
        log = self.client.get(self.base + '/exchanges/' + started.json()['exchange_id']).json()
        self.assertEqual([a['raw'] for a in log['attempts']], ['Nem tudom.', 'Most sem tudom.'])
        self.assertEqual(log['status'], 'failed')


if __name__ == '__main__': unittest.main()

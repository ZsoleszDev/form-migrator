"""Binding editor API: local write guard, output ZIP, AI context and retry."""
import io
import json
from pathlib import Path
import unittest
import zipfile

import test_schema_workbench_web as fixture
from frm_forms.common import read_json
from java_support import trigger_mode

setUpModule, tearDownModule = trigger_mode('ai')


@unittest.skipUnless(fixture.WEB, 'web dependencies required')
class BindingWebTests(unittest.TestCase):
    setUp = fixture.SchemaWebTests.setUp
    wait_for_job = fixture.SchemaWebTests.wait_for_job

    def test_guard_save_download_context_and_retry(self):
        sheet = self.client.get(self.base + '/bindings').json()
        row = next(r for r in sheet['rows'] if r['source'] == 'CTRL.ID')
        request = {'revision': sheet['revision'], 'bindings': {row['id']: {'kind': 'parameter', 'source': 'recordId'}}}
        old_index = self.client.get(self.base).json()
        self.assertEqual(self.client.post(self.base + '/bindings', json=request).status_code, 403)
        saved = self.client.post(self.base + '/bindings', headers=fixture.HEADERS, json=request)
        self.assertEqual(saved.status_code, 200, saved.text)
        changed = next(r for r in saved.json()['rows'] if r['id'] == row['id'])
        self.assertEqual(changed['status'], 'host_required')
        self.assertEqual(self.client.post(self.base + '/bindings', headers=fixture.HEADERS, json=request).status_code, 409)
        index = self.client.get(self.base).json()
        self.assertNotEqual(old_index['tasks'][0]['fingerprint'], index['tasks'][0]['fingerprint'])
        output = self.settings.data_dir / 'jobs' / self.job / 'module'
        packet = read_json(output / 'analysis/backend-assistant/tasks' / (index['tasks'][0]['id'] + '.json'))
        self.assertTrue(any(r.get('binding', {}).get('source') == 'recordId' for r in packet['frontend_inputs']['rows'] if r.get('binding')))
        exported = self.client.get(self.base + '/bindings/download')
        self.assertEqual(exported.json()['screen_input_bindings']['bindings'], request['bindings'])
        archive = self.client.get('/api/jobs/' + self.job + '/download?kind=frontend')
        with zipfile.ZipFile(io.BytesIO(archive.content)) as z:
            component = next(n for n in z.namelist() if n.endswith('.component.ts'))
            self.assertIn(b"this.migrationParameters['recordId']", z.read(component))
        retry = self.client.post('/api/jobs/' + self.job + '/retry', headers=fixture.HEADERS)
        self.assertEqual(retry.status_code, 202, retry.text)
        new_id = retry.json()['id']; self.wait_for_job(new_id)
        after = self.client.get('/api/jobs/' + new_id + '/assistant/bindings').json()
        self.assertEqual(after['bindings'], request['bindings'])
        self.assertFalse(after['file_changed'])

    def test_invalid_and_busy_requests_do_not_write(self):
        sheet = self.client.get(self.base + '/bindings').json()
        request = {'revision': sheet['revision'], 'bindings': {sheet['rows'][0]['id']: {'kind': 'field', 'source': 'UNKNOWN.CODE'}}}
        self.assertEqual(self.client.post(self.base + '/bindings', headers=fixture.HEADERS, json=request).status_code, 422)
        engine = self.app.state.assistant
        with engine.lock:
            engine.runs['test'] = {'job_id': self.job}; engine.active = 'test'
        try:
            self.assertEqual(self.client.post(self.base + '/bindings', headers=fixture.HEADERS, json=request).status_code, 409)
        finally:
            with engine.lock: engine.active = None
        self.assertEqual(self.client.get(self.base + '/bindings').json(), sheet)

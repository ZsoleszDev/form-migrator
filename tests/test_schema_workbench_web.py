"""Real upload/worker, table editor API, persistence, export and retry."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from java_support import trigger_mode
from test_schema_workbench import FORM, columns

try:
    from fastapi.testclient import TestClient
    from frm_forms.web.app import create_app
    from frm_forms.web.settings import Settings
    WEB = True
except ImportError:
    WEB = False

setUpModule, tearDownModule = trigger_mode('ai')


HEADERS = {'X-Frm-Client': 'local-ui'}


@unittest.skipUnless(WEB, 'pip install -r requirements-test.txt')
class SchemaWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.settings = Settings(data_dir=Path(self.temp.name), folder_dialog=False)
        self.app = create_app(self.settings)
        self.client = TestClient(self.app, base_url='http://localhost')
        self.client.__enter__(); self.addCleanup(self.client.__exit__, None, None, None)
        submitted = self.client.post('/api/jobs', headers=HEADERS,
            files={'file': ('form.xml', FORM.encode(), 'application/xml')},
            data={'options': json.dumps({'module': 'lookup', 'screen_window_selection': 'all'})})
        self.assertEqual(submitted.status_code, 202, submitted.text)
        self.job = submitted.json()['id']
        self.wait_for_job(self.job)
        self.base = '/api/jobs/' + self.job + '/assistant'

    def wait_for_job(self, job_id):
        for _ in range(200):
            job = self.client.get('/api/jobs/' + job_id).json()
            if job['status'] in {'completed', 'failed', 'needs_input'}: break
            time.sleep(.02)
        self.assertEqual(job['status'], 'completed', job)

    def request(self):
        sheet = self.client.get(self.base + '/schema').json()
        return {'table': 'APP.ACCOUNT', 'revision': sheet['revision'],
                'columns': columns(), 'primary_key': ['ID']}

    def test_missing_table_is_empty_and_stops_ai_before_any_request(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        index = self.client.get(self.base).json()
        table = index['schema']['tables'][0]
        self.assertEqual(table['table'], 'APP.ACCOUNT')
        self.assertIsNone(table['definition'])
        self.assertEqual(table['referenced_columns'], ['ID', 'NAME'])
        with patch('frm_forms.ollama.opener', side_effect=AssertionError('No schema, no model call')):
            started = self.client.post(self.base, headers=HEADERS,
                                       json={'task_id': index['tasks'][0]['id']})
        self.assertEqual(started.status_code, 409)
        self.assertIn('APP.ACCOUNT', started.json()['detail'])

    def test_save_export_index_and_retry_keep_the_same_structure(self):
        request = self.request()
        saved = self.client.post(self.base + '/schema', headers=HEADERS, json=request)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()['pending'], [])
        index = self.client.get(self.base).json()
        self.assertEqual(index['tasks'][0]['pending_tables'], [])
        exported = self.client.get(self.base + '/schema/download')
        self.assertIn('schema.json', exported.headers['content-disposition'])
        definition = exported.json()['tables']['APP.ACCOUNT']
        self.assertIs(definition['columns']['ID']['nullable'], False)
        self.assertEqual(definition['primary_key'], ['ID'])
        retried = self.client.post('/api/jobs/' + self.job + '/retry', headers=HEADERS)
        self.assertEqual(retried.status_code, 202, retried.text)
        new_id = retried.json()['id']
        self.assertNotEqual(new_id, self.job)
        self.wait_for_job(new_id)
        sheet = self.client.get('/api/jobs/' + new_id + '/assistant/schema').json()
        self.assertEqual(sheet['pending'], [])
        self.assertEqual(sheet['tables'][0]['definition'], definition)
        # A guarded update must not silently replace another browser's edit.
        stale = self.client.post(self.base + '/schema', headers=HEADERS, json=request)
        self.assertEqual(stale.status_code, 409, stale.text)

    def test_invalid_metadata_and_unguarded_requests_do_not_change_schema(self):
        original = self.client.get(self.base + '/schema').json()
        request = self.request()
        self.assertEqual(self.client.post(self.base + '/schema', json=request).status_code, 403)
        for value in (None, 1, 'false', True):
            request['columns'][0]['nullable'] = value
            rejected = self.client.post(self.base + '/schema', headers=HEADERS, json=request)
            self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertEqual(self.client.get(self.base + '/schema').json(), original)

    def test_csv_preview_populates_the_editor_then_normal_save_persists(self):
        before = self.client.get(self.base + '/schema').json()
        request = {'table': 'APP.ACCOUNT', 'revision': before['revision'],
                   'text': 'ID,NUMBER,22,20,0,N\nNAME,VARCHAR2,30,,,Y'}
        preview = self.client.post(self.base + '/schema/import', headers=HEADERS, json=request)
        self.assertEqual(preview.status_code, 200, preview.text)
        columns = preview.json()['columns']
        self.assertEqual(columns[0]['precision'], 20)
        self.assertIsNone(columns[0]['length'])
        self.assertEqual(self.client.get(self.base + '/schema').json(), before)
        saved = self.client.post(self.base + '/schema', headers=HEADERS,
            json={'table': request['table'], 'revision': request['revision'], 'columns': columns, 'primary_key': ['ID']})
        self.assertEqual(saved.status_code, 200, saved.text)
        exported = self.client.get(self.base + '/schema/download').json()['tables']['APP.ACCOUNT']
        self.assertEqual(exported['columns']['NAME']['length_semantics'], 'BYTE')
        self.assertEqual(exported['primary_key'], ['ID'])
        self.assertEqual(self.client.get(self.base).json()['tasks'][0]['pending_tables'], [])

    def test_csv_preview_rejects_invalid_stale_and_unguarded_requests_without_writes(self):
        before = self.client.get(self.base + '/schema').json()
        request = {'table': 'APP.ACCOUNT', 'revision': before['revision'], 'text': 'ID,NUMBER,22,20,0,N'}
        self.assertEqual(self.client.post(self.base + '/schema/import', json=request).status_code, 403)
        for changes, status in (({'text': 'ID,NUMBER,22,20,0,Y\nID,NUMBER,22,20,0,N'}, 422),
                                ({'revision': '0' * 64}, 409), ({'table': 'SYS.DUAL'}, 422)):
            rejected = self.client.post(self.base + '/schema/import', headers=HEADERS, json=request | changes)
            self.assertEqual(rejected.status_code, status, rejected.text)
        self.assertEqual(self.client.get(self.base + '/schema').json(), before)

    def test_headerless_extended_csv_preview_save_and_export(self):
        before = self.client.get(self.base + '/schema').json()
        request = {'table': 'APP.ACCOUNT', 'revision': before['revision'],
                   'text': 'ID,NUMBER,22,20,0,N,0,\nNAME,VARCHAR2,120,,,Y,30,C\nOSZLOP_NEVE,VARCHAR2,40,,,N,40,B'}
        preview = self.client.post(self.base + '/schema/import', headers=HEADERS, json=request)
        self.assertEqual(preview.status_code, 200, preview.text)
        rows = preview.json()['columns']
        self.assertEqual((rows[2]['name'], rows[2]['length'], rows[2]['length_semantics']), ('OSZLOP_NEVE', 40, 'BYTE'))
        self.assertEqual(self.client.get(self.base + '/schema').json(), before)
        saved = self.client.post(self.base + '/schema', headers=HEADERS,
            json={'table': request['table'], 'revision': request['revision'], 'columns': rows, 'primary_key': ['ID']})
        self.assertEqual(saved.status_code, 200, saved.text)
        definition = self.client.get(self.base + '/schema/download').json()['tables']['APP.ACCOUNT']
        self.assertEqual(definition['columns']['NAME']['char_length'], 30)
        self.assertEqual(definition['columns']['OSZLOP_NEVE']['data_length'], 40)
        self.assertEqual(definition['columns']['OSZLOP_NEVE']['length_semantics'], 'BYTE')
        self.assertIs(definition['columns']['OSZLOP_NEVE']['nullable'], False)

    def test_invalid_extended_csv_does_not_change_saved_schema(self):
        before = self.client.get(self.base + '/schema').json()
        request = {'table': 'APP.ACCOUNT', 'revision': before['revision'],
                   'text': 'ID,NUMBER,22,20,0,N,0,\nNAME,VARCHAR2,120,,,Y,30,INVALID'}
        rejected = self.client.post(self.base + '/schema/import', headers=HEADERS, json=request)
        self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertIn('CHAR_USED', rejected.json()['detail'])
        self.assertEqual(self.client.get(self.base + '/schema').json(), before)


if __name__ == '__main__':
    unittest.main()

import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

try:
    from fastapi.testclient import TestClient
    from frm_forms.web.app import create_app
    from frm_forms.web.settings import Settings
    WEB_AVAILABLE = True
except ImportError:
    WEB_AVAILABLE = False

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'http://localhost:4200'
HEADERS = {'Origin': ORIGIN, 'Authorization': 'Bearer synthetic-test-token', 'X-Frm-Client': 'local-ui'}


@unittest.skipUnless(WEB_AVAILABLE, 'Web API dependencies required')
class FrontendIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def client(self, **options):
        return TestClient(create_app(Settings(data_dir=self.root/'data', **options)), base_url='http://localhost:8000')

    def preflight(self, client, path='/api/health', origin=ORIGIN, method='GET', headers='authorization,x-frm-client,content-type'):
        return client.options(path, headers={'Origin': origin, 'Access-Control-Request-Method': method,
                                            'Access-Control-Request-Headers': headers})

    def test_company_bearer_preflight_and_actual_gets(self):
        with self.client() as client:
            for path in ['/api/health', '/api/defaults', '/api/jobs']:
                response = self.preflight(client, path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers['access-control-allow-origin'], ORIGIN)
                self.assertIn('authorization', response.headers['access-control-allow-headers'].lower())
                response = client.get(path, headers=HEADERS)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers['access-control-allow-origin'], ORIGIN)
                self.assertNotIn('access-control-allow-credentials', response.headers)
            defaults = client.get('/api/defaults', headers=HEADERS).json()
            self.assertIn('http://localhost:4200', defaults['cors_origins'])
            self.assertIn(ORIGIN, defaults['cors_origins'])
            self.assertIn('authorization', defaults['cors_headers'])
            self.assertEqual(defaults['client_contract']['authorization'], 'accepted_but_not_validated')

    def test_rejections_explain_origin_header_and_method_without_logging_token(self):
        with self.client() as client:
            for options, reason in [({'origin': 'https://foreign.example'}, 'origin'),
                                    ({'headers': 'authorization,x-company-id'}, 'headers'),
                                    ({'method': 'PATCH'}, 'method')]:
                with self.assertLogs('uvicorn.error', level='WARNING') as logs:
                    response = self.preflight(client, **options)
                self.assertEqual(response.status_code, 400)
                self.assertIn('Disallowed CORS '+reason, response.text)
                self.assertIn('Disallowed CORS '+reason, '\n'.join(logs.output))
                self.assertNotIn('synthetic-test-token', '\n'.join(logs.output))
            response = client.get('/api/health', headers={**HEADERS, 'Origin': 'https://foreign.example'})
            self.assertEqual(response.status_code, 403)
            self.assertNotIn('access-control-allow-origin', response.headers)
            response = client.post('/api/jobs', headers={'Origin': ORIGIN, 'Authorization': HEADERS['Authorization']})
            self.assertEqual(response.status_code, 403)  # A Bearer header is not authentication or the mutation marker.
            self.assertIn('X-Frm-Client', response.json()['detail'])

    def test_environment_config_reaches_both_cors_and_request_guard(self):
        origin = 'https://frontend.example:8443'
        with patch.dict(os.environ, {'FRM_WORK_DIR': str(self.root/'data'), 'FRM_CORS_ORIGINS': ' '+origin+' ',
                                    'FRM_CORS_HEADERS': 'X-Company-Id, X-Request-Id',
                                    'FRM_CORS_ALLOW_CREDENTIALS': 'true'}, clear=True):
            settings = Settings.from_env()
        with TestClient(create_app(settings), base_url='http://localhost:8000') as client:
            response = self.preflight(client, origin=origin, headers='Authorization,X-Company-ID,x-request-id,x-frm-client')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers['access-control-allow-origin'], origin)
            self.assertEqual(response.headers['access-control-allow-credentials'], 'true')
            response = client.get('/api/defaults', headers={**HEADERS, 'Origin': origin})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['access-control-allow-credentials'], 'true')
            self.assertIn('x-company-id', response.json()['cors_headers'])
            self.assertTrue(response.json()['cors_allow_credentials'])
            response = client.post('/api/jobs', headers={**HEADERS, 'Origin': origin})
            self.assertEqual(response.status_code, 422)  # Reaches validation; not rejected by the local guard.

    def test_invalid_environment_is_rejected_before_serving(self):
        for key, value in [('FRM_CORS_ORIGINS', '*'), ('FRM_CORS_ORIGINS', 'http://localhost:4200/migrator'),
                           ('FRM_CORS_ORIGINS', 'http://localhost:4200/'), ('FRM_CORS_ORIGINS', 'null'),
                           ('FRM_CORS_ORIGINS', 'http://user:pass@localhost:4200'),
                           ('FRM_CORS_HEADERS', '*'), ('FRM_CORS_HEADERS', 'Authorization: Bearer token'),
                           ('FRM_CORS_HEADERS', 'X-Test\r\nInjected'), ('FRM_CORS_ALLOW_CREDENTIALS', 'yes')]:
            with self.subTest(key=key, value=value), patch.dict(os.environ, {key: value}, clear=True):
                with self.assertRaises(ValueError):
                    Settings.from_env()

    def test_bearer_upload_generation_download_and_delete(self):
        with self.client() as client:
            self.assertEqual(self.preflight(client, '/api/jobs', method='POST').status_code, 200)
            response = client.post('/api/jobs', headers=HEADERS,
                files={'file': ('customer_fmb.xml', (ROOT/'examples/customer_fmb.xml').read_bytes(), 'application/xml')},
                data={'options': json.dumps({'module': 'companyIntegration', 'ai_mode': 'off'})})
            self.assertEqual(response.status_code, 202, response.text)
            job_id = response.json()['id']
            deadline = time.monotonic()+20
            while True:
                response = client.get('/api/jobs/'+job_id, headers=HEADERS)
                self.assertEqual(response.status_code, 200, response.text)
                job = response.json()
                if job['status'] in {'completed', 'failed', 'cancelled', 'interrupted'}:
                    break
                self.assertLess(time.monotonic(), deadline, job)
                time.sleep(0.03)
            self.assertEqual(job['status'], 'completed', job)
            self.assertEqual(job['summary']['ai']['attempted_calls'], 0)
            response = client.get('/api/jobs/'+job_id+'/download?kind=backend', headers=HEADERS)
            self.assertEqual(response.status_code, 200)
            self.assertIn('Content-Disposition', response.headers['access-control-expose-headers'])
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                self.assertIsNone(archive.testzip())
                for layer, expected in [('CL', 4), ('DPS', 4), ('WBS', 4)]:
                    self.assertEqual(sum(name.startswith('backend/'+layer+'/') and name.endswith('.java') for name in archive.namelist()), expected)
            for file in (self.root/'data/jobs'/job_id).rglob('*'):
                if file.is_file():
                    self.assertNotIn(b'synthetic-test-token', file.read_bytes(), str(file))
            self.assertEqual(self.preflight(client, '/api/jobs/'+job_id, method='DELETE').status_code, 200)
            self.assertIn(client.delete('/api/jobs/'+job_id, headers=HEADERS).status_code, {200, 204})


if __name__ == '__main__':
    unittest.main()

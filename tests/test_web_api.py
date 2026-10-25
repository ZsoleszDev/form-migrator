import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from fastapi.testclient import TestClient
    from frm_forms.web.app import create_app
    from frm_forms.web.settings import Settings
    WEB_AVAILABLE = True
except ImportError:
    WEB_AVAILABLE = False

ROOT = Path(__file__).resolve().parents[1]
HEADERS = {'Origin': 'http://localhost:4200', 'X-Frm-Client': 'local-ui'}


@unittest.skipUnless(WEB_AVAILABLE, 'Web API tesztekhez: pip install -r requirements-test.txt')
class WebTests(unittest.TestCase):
    def test_object_group_type_and_package_pair_upload_completes(self):
        response = self.submit(options={'module': 'groupTypes'}, sample='object-group-types_fmb.xml', schema=False)
        self.assertEqual(response.status_code, 202, response.text)
        job = self.wait_job(response.json()['id'])
        self.assertEqual(job['status'], 'completed', job)
        module = self.settings.data_dir / 'jobs' / job['id'] / 'module'
        data = json.loads((module / 'analysis/discovery/form-map.json').read_text())
        self.assertEqual(data['counts']['objectgroupchild'], 3)
        self.assertEqual(data['counts']['programunit'], 3)
        self.assertTrue((module / 'frontend/groupTypes/groupTypes.component.ts').is_file())
        self.assertEqual((module / 'analysis/source.xml').read_bytes(), (ROOT / 'examples/object-group-types_fmb.xml').read_bytes())

    def test_package_spec_body_upload_completes_and_keeps_both_sources(self):
        response = self.submit(options={'module': 'packagePair'}, sample='package-pair_fmb.xml', schema=False)
        self.assertEqual(response.status_code, 202, response.text)
        job = self.wait_job(response.json()['id'])
        self.assertEqual(job['status'], 'completed', job)
        module = self.settings.data_dir / 'jobs' / job['id'] / 'module'
        data = json.loads((module / 'analysis/discovery/form-map.json').read_text())
        units = [code for code in data['code'] if code['kind'] == 'programunit']
        self.assertEqual([code['program_unit_type'] for code in units], ['Package Spec', 'Package Body'])
        self.assertTrue((module / 'frontend/packagePair/packagePair.component.ts').is_file())
        actions = next(module.glob('backend/DPS/*ServiceImpl.java')).read_text()
        self.assertIn('PACKAGE WEB_UTIL IS', actions)
        self.assertIn('PACKAGE BODY WEB_UTIL IS', actions)
        self.assertIn('HttpStatus.NOT_IMPLEMENTED', actions)

    def test_window_selection_setting_and_dialog_upload(self):
        from test_screen_windows import simple_windows
        response = self.submit(options={'module': 'windows', 'screen_primary_window': 'RIGHT'}, schema=False, source=simple_windows())
        self.assertEqual(response.status_code, 202, response.text)
        job = self.wait_job(response.json()['id']); self.assertEqual(job['status'], 'completed', job)
        module = self.settings.data_dir / 'jobs' / job['id'] / 'module'
        plan = json.loads((module / 'analysis/screen-plan.json').read_text())
        self.assertEqual(next(w['name'] for w in plan['windows'] if w['role'] == 'main'), 'RIGHT')
        self.assertEqual((module / 'frontend/windows/windows.component.ts').read_text().count('<p-dialog'), 1)

    def test_screen_override_upload_is_applied_and_retry_preserves_it(self):
        first = self.submit(sample='screen-layout_fmb.xml', schema=False)
        job = self.wait_job(first.json()['id'])
        self.assertEqual(job['status'], 'completed', job)
        template = self.settings.data_dir / 'jobs' / job['id'] / 'module/analysis/screen-overrides.template.json'
        rules = json.loads(template.read_text())
        rules['items']['FIELDS.NOTE'].update(reason='Ellenőrzött felirat.', set={'label': 'Fejlesztői megjegyzés'})
        response = self.client.post('/api/jobs', headers=HEADERS,
            files={'file': ('screen-layout_fmb.xml', (ROOT / 'examples/screen-layout_fmb.xml').read_bytes(), 'application/xml'),
                   'screen_overrides_file': ('review.json', json.dumps(rules), 'application/json')},
            data={'options': json.dumps({'module': 'overrides', 'screen_row_tolerance': 0.2, 'screen_preserve_gaps': False})})
        self.assertEqual(response.status_code, 202, response.text)
        job = self.wait_job(response.json()['id']); self.assertEqual(job['status'], 'completed', job)
        module = self.settings.data_dir / 'jobs' / job['id'] / 'module'
        self.assertIn('Fejlesztői megjegyzés', (module / 'frontend/overrides/overrides.component.ts').read_text())
        plan = json.loads((module / 'analysis/screen-plan.json').read_text())
        self.assertEqual(plan['layout_settings'], {'columns': 12, 'row_tolerance': 0.2, 'preserve_gaps': False})
        retry = self.client.post('/api/jobs/' + job['id'] + '/retry', headers=HEADERS)
        self.assertEqual(retry.status_code, 202, retry.text)
        repeated = self.wait_job(retry.json()['id']); self.assertEqual(repeated['status'], 'completed', repeated)
        applied = self.settings.data_dir / 'jobs' / repeated['id'] / 'module/analysis/screen-overrides.applied.json'
        self.assertEqual(json.loads(applied.read_text()), rules)

    def test_screen_override_upload_rejects_invalid_rules_and_stale_sources(self):
        source = (ROOT / 'examples/screen-layout_fmb.xml').read_bytes()
        empty = {'version': 1, 'items': {}}
        for options, rules in [({'generation_mode': 'strict'}, empty), ({}, {'version': 1, 'items': [], 'unknown': True})]:
            response = self.client.post('/api/jobs', headers=HEADERS,
                files={'file': ('screen-layout_fmb.xml', source, 'application/xml'),
                       'screen_overrides_file': ('review.json', json.dumps(rules), 'application/json')},
                data={'options': json.dumps(options)})
            self.assertEqual(response.status_code, 422, response.text)
        stale = {'version': 1, 'form_name': 'SCREEN_LAYOUT', 'items': {
            'FIELDS.NOTE': {'reason': 'Régi döntés', 'source_fingerprint': '0' * 64, 'set': {'label': 'Régi'}}}}
        response = self.client.post('/api/jobs', headers=HEADERS,
            files={'file': ('screen-layout_fmb.xml', source, 'application/xml'),
                   'screen_overrides_file': ('review.json', json.dumps(stale), 'application/json')})
        self.assertEqual(response.status_code, 202, response.text)
        job = self.wait_job(response.json()['id']); self.assertEqual(job['status'], 'failed', job)
        log = self.client.get('/api/jobs/' + job['id'] + '/logs').json()['text']
        self.assertIn('SCREEN_OVERRIDE_STALE', log)
        self.assertFalse((self.settings.data_dir / 'jobs' / job['id'] / 'module').exists())

    def test_default_screen_upload_and_download(self):
        response = self.submit(options={'module': 'screenDemo'}, sample='ui-features_fmb.xml', schema=False)
        self.assertEqual(response.status_code, 202, response.text)
        job = self.wait_job(response.json()['id'])
        self.assertEqual(job['status'], 'completed', job)
        self.assertEqual(job['summary']['generation_mode'], 'screen')
        self.assertEqual(job['summary']['readable_blocks'], 0)
        module = self.settings.data_dir / 'jobs' / job['id'] / 'module'
        self.assertEqual(len(list((module / 'frontend').rglob('*.ts'))), 1)
        self.assertTrue((module / 'frontend/screenDemo/MIGRATION_NOTES.md').is_file())
        code = (module / 'frontend/screenDemo/screenDemo.component.ts').read_text()
        self.assertIn('@openng/optimus-ui/tabs', code)
        self.assertNotIn('primeng', code)

    def test_scaffold_upload_keeps_review_and_downloadable_map(self):
        from test_discovery_scaffold import fixture
        response = self.submit(options={'module':'test','generation_mode':'scaffold'}, schema=False, source=fixture(True))
        self.assertEqual(response.status_code,202,response.text)
        job = self.wait_job(response.json()['id'])
        self.assertEqual(job['status'],'completed',job); self.assertTrue(job['review_required'])
        self.assertEqual(job['summary']['generation_mode'],'scaffold'); self.assertEqual(job['summary']['readable_blocks'],0)
        preview = self.client.get(f"/api/jobs/{job['id']}/file",params={'path':'analysis/issues-summary.json'}).json()
        self.assertFalse(preview['truncated']); self.assertGreater(json.loads(preview['text'])['total'],0)
        download = self.client.get(f"/api/jobs/{job['id']}/download")
        self.assertEqual(download.status_code,200)
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            self.assertTrue(any(p.endswith('/form-explorer.html') for p in archive.namelist()))
            self.assertTrue(any(p.endswith('ServiceImpl.java') for p in archive.namelist()))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(data_dir=self.root / 'data')
        self.client = TestClient(create_app(self.settings), base_url='http://localhost:8000')
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def submit(self, options=None, sample='customer_fmb.xml', schema=True, source=None, filename=None):
        files = {'file': (filename or sample, source if source is not None else (ROOT / 'examples' / sample).read_bytes(), 'application/octet-stream')}
        if schema:
            files['schema_file'] = ('schema.json', (ROOT / 'examples/schema.json').read_bytes(), 'application/json')
        return self.client.post('/api/jobs', files=files, data={'options': json.dumps(options or {'module': 'customer'})}, headers=HEADERS)

    def wait_job(self, job_id, states=None, timeout=12):
        states = states or {'completed', 'failed', 'cancelled', 'interrupted'}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.client.get('/api/jobs/' + job_id)
            self.assertEqual(response.status_code, 200)
            job = response.json()
            if job['status'] in states:
                return job
            time.sleep(0.03)
        self.fail('A feladat nem ért célállapotba: ' + str(job))

    def test_cors_preflight_exact_localhost_origin_and_headers(self):
        response = self.client.options('/api/jobs', headers={'Origin': 'http://localhost:4200', 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'X-Frm-Client, Content-Type'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['access-control-allow-origin'], 'http://localhost:4200')
        self.assertIn('x-frm-client', response.headers['access-control-allow-headers'].lower())
        response = self.client.get('/api/health', headers={'Origin': 'http://localhost:4200'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['local_only'])
        self.assertIn('Content-Disposition', response.headers['access-control-expose-headers'])

    def test_olb_mmb_upload_and_unknown_companion_rejection(self):
        files=[('file',('checkbox_fmb.xml',(ROOT/'examples/inheritance/checkbox_fmb.xml').read_bytes(),'application/xml')),
               ('olb_files',('base_olb.xml',(ROOT/'examples/inheritance/base_olb.xml').read_bytes(),'application/xml')),
               ('olb_files',('qmsolb65_olb.xml',(ROOT/'examples/inheritance/qmsolb65_olb.xml').read_bytes(),'application/xml')),
               ('mmb_file',('navigation_mmb.xml',(ROOT/'examples/companions/navigation_mmb.xml').read_bytes(),'application/xml'))]
        response=self.client.post('/api/jobs',files=files,headers=HEADERS)
        self.assertEqual(response.status_code,202,response.text)
        job=self.wait_job(response.json()['id']);self.assertEqual(job['status'],'completed',job)
        model=self.client.get(f"/api/jobs/{job['id']}/file",params={'path':'analysis/ui-model.json'}).json()
        data=json.loads(model['text']);self.assertEqual(data['blocks'][0]['items'][0]['widget'],'checkbox')
        for filename in ['../base_olb.xml','wrong.xml']:
            invalid=[files[0],('olb_files',(filename,b'<ObjectLibrary Name="X"/>','application/xml'))]
            response=self.client.post('/api/jobs',files=invalid,headers=HEADERS);self.assertEqual(response.status_code,400,response.text)
        response=self.client.post('/api/jobs',files=[files[0],files[1],files[1]],headers=HEADERS)
        self.assertEqual(response.status_code,400,response.text)

    def test_repeated_http_jobs_produce_identical_three_archives(self):
        archives=[]
        for _ in range(2):
            response=self.submit();self.assertEqual(response.status_code,202)
            job=self.wait_job(response.json()['id']);self.assertEqual(job['status'],'completed',job)
            archives.append({kind:self.client.get(f"/api/jobs/{job['id']}/download",params={'kind':kind}).content for kind in ['all','frontend','backend']})
        self.assertEqual(archives[0],archives[1])

    def test_foreign_origin_simple_form_and_dns_rebinding_rejected(self):
        response = self.client.post('/api/jobs', data={}, headers={'Origin': 'https://example.org'})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('access-control-allow-origin', response.headers)
        response = self.client.post('/api/jobs', data={}, headers={'Origin': 'http://localhost:4200'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get('/api/health', headers={'Host': 'untrusted.example'}).status_code, 400)

    def test_real_upload_subprocess_generation_preview_three_downloads_delete(self):
        response = self.submit()
        self.assertEqual(response.status_code, 202, response.text)
        job = self.wait_job(response.json()['id'])
        self.assertEqual(job['status'], 'completed', job)
        self.assertTrue(job['review_required'])  # frontend trigger/layout integration is explicit review
        self.assertEqual(job['summary']['converted_triggers'], 5)
        self.assertEqual(job['download_name'], 'ugyfelek')
        self.assertEqual(job['summary']['ai']['attempted_calls'], 0)
        job_id = job['id']
        files = self.client.get(f'/api/jobs/{job_id}/files').json()['files']
        ts = next(f['path'] for f in files if f['path'].endswith('.component.ts'))
        preview = self.client.get(f'/api/jobs/{job_id}/file', params={'path': ts})
        self.assertIn('standalone: true', preview.json()['text'])
        self.assertEqual(self.client.get(f'/api/jobs/{job_id}/file', params={'path': '../../inputs/config.json'}).status_code, 404)
        for kind in ['all', 'frontend', 'backend']:
            response = self.client.get(f'/api/jobs/{job_id}/download', params={'kind': kind}, headers={'Origin': 'http://localhost:4200'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['access-control-allow-origin'], 'http://localhost:4200')
            self.assertIn('.zip', response.headers['content-disposition'])
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                self.assertIsNone(archive.testzip())
                if kind != 'all': self.assertTrue(all(name.startswith(kind + '/') for name in archive.namelist()))
        self.assertEqual(self.client.delete(f'/api/jobs/{job_id}', headers=HEADERS).status_code, 200)
        self.assertEqual(self.client.get(f'/api/jobs/{job_id}').status_code, 404)

    def test_strict_review_exit_three_still_downloads(self):
        response = self.submit({'module': 'review', 'strict': True}, sample='review_fmb.xml', schema=False)
        job = self.wait_job(response.json()['id'])
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(job['exit_code'], 3)
        self.assertTrue(job['review_required'])
        self.assertEqual(job['summary']['review_triggers'], 2)
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}/download").status_code, 200)

    def test_untrusted_export_command_and_invalid_options_rejected(self):
        for options in [{'export_command': ['anything']}, {'max_ai_calls': 99}, {'java_package': 'hu.class'}, {'module': '../../escape'}]:
            response = self.submit(options)
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get('/api/jobs').json()['jobs'], [])

    def test_invalid_files_size_and_json_are_rejected(self):
        self.assertEqual(self.submit(source=b'', filename='empty.xml').status_code, 400)
        self.assertEqual(self.submit(source=b'x', filename='x.exe').status_code, 400)
        self.settings.max_upload_bytes = 64
        self.assertEqual(self.submit().status_code, 413)
        self.settings.max_upload_bytes = 33554432
        response = self.client.post('/api/jobs', files={'file': ('a.xml', (ROOT / 'examples/customer_fmb.xml').read_bytes()), 'schema_file': ('s.json', b'{invalid')}, headers=HEADERS)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get('/api/jobs').json()['jobs'], [])

    def test_fmb_export_written_next_to_the_input_is_found(self):
        # Forms2XML with an absolute input path writes <name>_fmb.xml beside the FMB, not in the export folder.
        script = self.root / 'export_next.py'
        script.write_text('from pathlib import Path\nimport sys\np=Path(sys.argv[-1])\n(p.parent / (p.stem + "_fmb.xml")).write_bytes(Path('
                          + repr(str(ROOT / 'examples/customer_fmb.xml')) + ').read_bytes())\n')
        self.settings.engine_config['export_command'] = [sys.executable, str(script), '{input}']
        job = self.wait_job(self.submit(source=b'synthetic-fmb', filename='CUSTOMER.fmb').json()['id'])
        self.assertEqual(job['status'], 'completed', job)

    def test_ambiguous_main_window_is_asked_and_the_answer_continues_the_job(self):
        from test_screen_windows import simple_windows
        response = self.submit(options={'module': 'windows'}, schema=False, source=simple_windows())
        self.assertEqual(response.status_code, 202, response.text)
        job = self.wait_job(response.json()['id'], {'needs_input', 'completed', 'failed'})
        self.assertEqual(job['status'], 'needs_input', job)
        # First: which windows to generate (web default 'ask'), each with a script-free preview.
        self.assertEqual(job['question']['kind'], 'windows')
        self.assertEqual([c['name'] for c in job['question']['choices']], ['LEFT', 'RIGHT'])
        self.assertTrue(all('<script' not in c['preview'].lower() for c in job['question']['choices']))
        for wrong in ({'screen_windows': []}, {'screen_windows': ['NOPE']}, {'screen_primary_window': 'RIGHT'}):
            refused = self.client.post(f"/api/jobs/{job['id']}/answer", json=wrong, headers=HEADERS)
            self.assertEqual(refused.status_code, 422, refused.text)
        chosen = self.client.post(f"/api/jobs/{job['id']}/answer", json={'screen_windows': ['LEFT', 'RIGHT']}, headers=HEADERS)
        self.assertEqual(chosen.status_code, 202, chosen.text)
        job = self.wait_job(job['id'], {'needs_input', 'completed', 'failed'})
        self.assertEqual((job['status'], job['options']['screen_windows']), ('needs_input', ['LEFT', 'RIGHT']), job)
        # Then the main window, as before.
        self.assertEqual(job['question']['kind'], 'primary_window')
        self.assertEqual([c['name'] for c in job['question']['choices']], ['LEFT', 'RIGHT'])
        self.assertEqual(self.client.get('/api/jobs').json()['jobs'][0]['status'], 'needs_input')
        bad = self.client.post(f"/api/jobs/{job['id']}/answer", json={'screen_primary_window': 'NOPE'}, headers=HEADERS)
        self.assertEqual(bad.status_code, 422, bad.text)
        unknown_key = self.client.post(f"/api/jobs/{job['id']}/answer", json={'window': 'RIGHT'}, headers=HEADERS)
        self.assertEqual(unknown_key.status_code, 422)
        answered = self.client.post(f"/api/jobs/{job['id']}/answer", json={'screen_primary_window': 'RIGHT'}, headers=HEADERS)
        self.assertEqual(answered.status_code, 202, answered.text)
        job = self.wait_job(job['id'])
        self.assertEqual((job['status'], job['options']['screen_primary_window']), ('completed', 'RIGHT'), job)
        module = self.settings.data_dir / 'jobs' / job['id'] / 'module'
        plan = json.loads((module / 'analysis/screen-plan.json').read_text())
        self.assertEqual(next(w['name'] for w in plan['windows'] if w['role'] == 'main'), 'RIGHT')
        again = self.client.post(f"/api/jobs/{job['id']}/answer", json={'screen_primary_window': 'LEFT'}, headers=HEADERS)
        self.assertEqual(again.status_code, 409)
        preview = self.client.get(f"/api/jobs/{job['id']}/preview")
        self.assertEqual(preview.status_code, 200)
        self.assertIn("default-src 'none'", preview.headers['content-security-policy'])
        self.assertEqual(preview.text.count('class="window"'), 2)
        self.assertNotIn('<script', preview.text.lower())

    def test_batch_groups_jobs_with_one_report_and_one_download(self):
        def upload(batch):
            files = {'file': ('customer_fmb.xml', (ROOT / 'examples/customer_fmb.xml').read_bytes(), 'application/xml')}
            return self.client.post('/api/jobs', files=files, data={'options': json.dumps({}), 'batch': batch}, headers=HEADERS)
        ids = []
        for _ in range(2):
            response = upload('b1test')
            self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual(response.json()['batch'], 'b1test')
            ids.append(response.json()['id'])
        for job_id in ids:
            self.assertEqual(self.wait_job(job_id)['status'], 'completed')
        report = self.client.get('/api/batches/b1test').json()
        self.assertEqual((report['total'], report['counts']['completed'], report['done']), (2, 2, True))
        self.assertEqual(report['report']['totals']['forms'], 2)
        self.assertIn('## Mit érdemes először javítani', report['markdown'])
        archive = self.client.get('/api/batches/b1test/download')
        self.assertEqual(archive.status_code, 200)
        names = zipfile.ZipFile(io.BytesIO(archive.content)).namelist()
        self.assertIn('PORTFOLIO_HU.md', names)
        self.assertEqual(sum(name.startswith('modulok/') for name in names), 2)
        self.assertEqual(upload('rossz azonosító').status_code, 422)
        self.assertEqual(self.client.get('/api/batches/ismeretlen').status_code, 404)

    def test_dictionary_sections_in_schema_upload_are_accepted(self):
        schema = {'blocks': {}, 'tables': {'APP.NEM_LETEZO': {'primary_key': ['ID'], 'columns': {}}},
                  'procedures': {'PKG.RUN': {'kind': 'procedure', 'arguments': []}}}
        files = {'file': ('customer_fmb.xml', (ROOT / 'examples/customer_fmb.xml').read_bytes(), 'application/xml'),
                 'schema_file': ('schema.json', json.dumps(schema).encode(), 'application/json')}
        response = self.client.post('/api/jobs', files=files, data={'options': json.dumps({'module': 'customer'})}, headers=HEADERS)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(self.wait_job(response.json()['id'])['status'], 'completed')

    def test_real_fmb_adapter_through_configured_exporter(self):
        script = self.root / 'export.py'
        script.write_text('from pathlib import Path\nimport sys\np=Path(sys.argv[-1])\nassert p.read_bytes() == b"synthetic-fmb"\nPath(p.stem + "_fmb.xml").write_bytes(Path(' + repr(str(ROOT / 'examples/customer_fmb.xml')) + ').read_bytes())\n')
        self.settings.engine_config['export_command'] = [sys.executable, str(script), '{input}']
        response = self.submit(source=b'synthetic-fmb', filename='CUSTOMER.fmb')
        job = self.wait_job(response.json()['id'])
        self.assertEqual(job['status'], 'completed', job)
        self.assertEqual(job['summary']['converted_triggers'], 5)

    def test_cancel_running_and_queued_job_and_cache_busy_guard(self):
        script = self.root / 'slow.py'
        script.write_text('import time\ntime.sleep(20)\n')
        self.settings.engine_config['export_command'] = [sys.executable, str(script), '{input}']
        first = self.submit(source=b'fmb', filename='slow.fmb').json()
        self.wait_job(first['id'], {'running'})
        second = self.submit(source=b'fmb', filename='queued.fmb').json()
        self.assertEqual(second['status'], 'queued')
        self.assertEqual(self.client.delete('/api/cache', headers=HEADERS).status_code, 409)
        self.assertEqual(self.client.delete(f"/api/jobs/{first['id']}", headers=HEADERS).status_code, 409)
        response = self.client.post(f"/api/jobs/{second['id']}/cancel", headers=HEADERS)
        self.assertEqual(response.json()['status'], 'cancelled')
        self.client.post(f"/api/jobs/{first['id']}/cancel", headers=HEADERS)
        self.assertEqual(self.wait_job(first['id'], timeout=7)['status'], 'cancelled')
        self.assertEqual(self.client.delete('/api/cache', headers=HEADERS).status_code, 200)

    def test_engine_failure_reports_error_and_has_no_download(self):
        response = self.submit(source=b'<wrong>broken', filename='bad.xml', schema=False)
        job = self.wait_job(response.json()['id'])
        self.assertEqual(job['status'], 'failed')
        self.assertIn('XML', job['error'])
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}/download").status_code, 409)
        retry = self.client.post(f"/api/jobs/{job['id']}/retry", headers=HEADERS)
        self.assertEqual(retry.status_code, 202)
        self.assertNotEqual(retry.json()['id'], job['id'])

    def test_persistence_restart_recovery_and_process_lock(self):
        response = self.submit()
        job = self.wait_job(response.json()['id'])
        with self.assertRaisesRegex(RuntimeError, 'másik FRM'):
            with TestClient(create_app(self.settings), base_url='http://localhost:8000'):
                pass
        other_dir = self.root / 'restart-data'
        folder = other_dir / 'jobs' / job['id']
        folder.mkdir(parents=True)
        job['status'] = 'running'
        (folder / 'job.json').write_text(json.dumps(job))
        with TestClient(create_app(Settings(data_dir=other_dir)), base_url='http://localhost:8000') as client:
            restored = client.get('/api/jobs').json()['jobs'][0]
            self.assertEqual(restored['status'], 'interrupted')

    def test_ai_mode_budget_and_cache_through_real_local_http(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(inner):
                requests.append(json.loads(inner.rfile.read(int(inner.headers['Content-Length']))))
                raw = json.dumps({'done': True, 'response': json.dumps({'target': 'backend', 'summary': 'Ellenőrzés szükséges.', 'steps': ['A saját eljárást át kell ültetni.']}), 'prompt_eval_count': 80, 'eval_count': 30}).encode()
                inner.send_response(200); inner.send_header('Content-Length', str(len(raw))); inner.end_headers(); inner.wfile.write(raw)
            def log_message(inner, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.settings.ollama_url = 'http://127.0.0.1:1'
        options = {'module': 'review', 'ai_mode': 'assist', 'max_ai_calls': 1, 'ollama_model': 'mock-local-model',
                   'ollama_url': f'http://127.0.0.1:{server.server_port}'}
        response = self.submit(options, sample='review_fmb.xml', schema=False)
        job = self.wait_job(response.json()['id'])
        self.assertEqual(job['status'], 'completed', job)
        self.assertEqual(job['summary']['ai']['attempted_calls'], 1)
        self.assertEqual(requests[0]['model'], 'mock-local-model')
        options['ai_mode'] = 'cached'
        response = self.submit(options, sample='review_fmb.xml', schema=False)
        job = self.wait_job(response.json()['id'])
        self.assertEqual(job['summary']['ai']['cache_hits'], 1)
        self.assertEqual(job['summary']['ai']['attempted_calls'], 0)
        self.assertEqual(len(requests), 1)


if __name__ == '__main__': unittest.main()

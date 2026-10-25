"""Real generation, request execution, persistence and conflict protection."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from frm_forms import input_bindings as bindings
from frm_forms.cli import main
from frm_forms.common import MigrationError, read_json
from screen_support import screen_method, ANGULAR_CLI_OPTIONS, with_company_imports
from test_schema_workbench import FORM as BASE

FORM = BASE.replace('WHERE a.ID = :CTRL.ID',
    'WHERE a.ID = :CTRL.ID AND a.NAME = :GLOBAL.FILTER AND a.NAME = :PARAMETER.NAME AND a.ID = :HIDDEN.ID')
FORM = FORM.replace('</Block><Canvas', '</Block><Block Name="HIDDEN" DatabaseDataBlock="false">'
    '<Item Name="ID" ItemType="Text Item" DataType="Char"/></Block><Canvas')


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'form.xml'; self.source.write_text(FORM, encoding='utf-8')
        self.output = self.root / 'out'
        self.generate(self.output)

    def generate(self, output, config=None):
        args = ['migrate', str(self.source), '--screen', '--module', 'lookup', '--out', str(output)]
        if config: args += ['--config', str(config)]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(args), 0)

    def mapping(self):
        sheet = bindings.sheet(self.output)
        rows = {r['source']: r for r in sheet['rows']}
        config = {'version': 1, 'bindings': {
            rows['GLOBAL.FILTER']['id']: {'kind': 'field', 'source': 'CTRL.NAME'},
            rows['PARAMETER.NAME']['id']: {'kind': 'parameter', 'source': 'customerName'},
            rows['HIDDEN.ID']['id']: {'kind': 'manual', 'note': 'A kiválasztott ügyfél szerveroldali azonosítója.'}}}
        return sheet, config

    def test_missing_sources_are_reported(self):
        sheet, _ = self.mapping()
        self.assertFalse(sheet['file_changed'])
        rows = {r['source']: r for r in sheet['rows']}
        for source in ('GLOBAL.FILTER', 'PARAMETER.NAME', 'HIDDEN.ID'):
            self.assertEqual(rows[source]['status'], 'missing')
        self.assertEqual(rows['CTRL.ID']['status'], 'connected')
        self.assertNotIn('HIDDEN.ID', [s['owner'] for s in sheet['sources']])

    def test_save_updates_actual_code_and_config_round_trips(self):
        sheet, config = self.mapping()
        saved = bindings.save(self.output, config, sheet['revision'])
        rows = {r['source']: r for r in saved['rows']}
        self.assertEqual(rows['GLOBAL.FILTER']['status'], 'connected')
        self.assertEqual(rows['PARAMETER.NAME']['status'], 'host_required')
        self.assertEqual(rows['HIDDEN.ID']['status'], 'manual')
        source = (self.output / saved['target']).read_text()
        self.assertIn("this.migrationParameters['customerName'] ?? null", source)
        self.assertIn(bindings.marked(rows['GLOBAL.FILTER']['id'], rows['GLOBAL.FILTER']['expression']), source)
        exported = self.root / 'config.json'; exported.write_text(json.dumps({bindings.CONFIG: config}))
        self.generate(self.root / 'again', exported)
        regenerated = bindings.sheet(self.root / 'again')
        self.assertEqual(saved['rows'], regenerated['rows'])
        self.assertEqual(source, (self.root / 'again' / regenerated['target']).read_text())

    def test_generated_request_runs_in_node_and_preserves_string_values(self):
        if not shutil.which('node'): self.skipTest('Node required')
        sheet, config = self.mapping(); bindings.save(self.output, config, sheet['revision'])
        members = [screen_method(self.output, name) for name in ('onRunClick', 'migrationInputText')]
        self.assertNotIn(None, members)
        script = self.root / 'binding-request.ts'
        script.write_text('''import assert from 'node:assert/strict';
const sent = [];
class Screen {
  forms = {ctrl: {getRawValue() { return {id: '12345678901234567890.25', name: 'Ügyfél'}; }}};
  migrationParameters = {customerName: 'Külső név'};
  actionOnctrlrun(body: unknown) { sent.push(body); return {subscribe() {}}; }
__MEMBERS__
}
const screen = new Screen(); screen.onRunClick();
assert.deepEqual(sent[0], {blocks:{CTRL:{NAME:'Ügyfél',ID:'12345678901234567890.25'},HIDDEN:{ID:null}},
  parameters:{'GLOBAL.FILTER':'Ügyfél','PARAMETER.NAME':'Külső név'}});
assert.equal(screen.migrationInputText(true, 'I', 'N'), 'I');
assert.equal(screen.migrationInputText(false, 'I', 'N'), 'N');
assert.equal(screen.migrationInputText(new Date(2026, 0, 2, 3, 4, 5, 6)), '2026-01-02T03:04:05.006');
delete screen.forms.ctrl; delete screen.migrationParameters.customerName;
screen.onRunClick(); assert.equal(sent[1].blocks.CTRL.ID, null);
assert.equal(sent[1].parameters['PARAMETER.NAME'], null);
'''.replace('__MEMBERS__', '\n'.join(members)))
        run = subprocess.run(['node', '--experimental-strip-types', '--no-warnings', str(script)], capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_typescript_strict_after_binding_edits(self):
        tsc = os.environ.get('FRM_TSC') or shutil.which('tsc')
        if not tsc: self.skipTest('FRM_TSC or tsc required')
        sheet, config = self.mapping(); bindings.save(self.output, config, sheet['revision'])
        work = self.root / 'ts'; shutil.copytree(self.output / 'frontend', work / 'frontend')
        stubs = Path(__file__).parent / 'ts_stubs'
        shutil.copy(stubs / 'company.ts', work / 'company.ts')
        files = with_company_imports(work / 'frontend')
        (work / 'tsconfig.json').write_text(json.dumps({'compilerOptions': {
            **ANGULAR_CLI_OPTIONS, 'noUnusedLocals': True, 'useDefineForClassFields': True, 'noEmit': True},
            'files': [str(stubs.resolve() / 'host.d.ts')] + files}))
        run = subprocess.run([tsc, '-p', str(work / 'tsconfig.json')], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_stale_revision_and_edited_component_never_overwrite(self):
        sheet, config = self.mapping(); saved = bindings.save(self.output, config, sheet['revision'])
        with self.assertRaises(bindings.BindingConflict): bindings.save(self.output, config, sheet['revision'])
        target = self.output / saved['target']; target.write_text(target.read_text() + '\n// user edit\n')
        before = target.read_bytes()
        changed = bindings.sheet(self.output)
        self.assertTrue(changed['file_changed'])
        with self.assertRaises(bindings.BindingConflict): bindings.save(self.output, config, changed['revision'])
        self.assertEqual(before, target.read_bytes())

    def test_invalid_field_system_and_uncalled_sources_are_rejected(self):
        sheet, config = self.mapping()
        key = next(r['id'] for r in sheet['rows'] if r['source'] == 'GLOBAL.FILTER')
        config['bindings'][key] = {'kind': 'field', 'source': 'this.evil()'}
        before = (self.output / sheet['target']).read_bytes()
        with self.assertRaises(MigrationError): bindings.save(self.output, config, sheet['revision'])
        self.assertEqual(before, (self.output / sheet['target']).read_bytes())
        row = bindings.slot('onRun', 'CTRL.RUN', 'SYSTEM.CURRENT_FORM', 'parameters.SYSTEM.CURRENT_FORM', None, 'string')
        with self.assertRaises(MigrationError): bindings.selected_expression(row, {'kind': 'parameter', 'source': 'user'}, [])
        row.update(source='GLOBAL.FILTER', automatic=False)
        with self.assertRaises(MigrationError): bindings.selected_expression(row, {'kind': 'parameter', 'source': 'filter'}, [])
        row.update(automatic=True, timing='constructor')
        with self.assertRaises(MigrationError): bindings.selected_expression(row, {'kind': 'parameter', 'source': 'filter'}, [])

    def test_regeneration_preserves_bindings_but_reports_changed_contracts(self):
        sheet, config = self.mapping(); saved = bindings.save(self.output, config, sheet['revision'])
        from frm_forms.ui_report import update_manifest
        update_manifest(self.output)
        before = (self.output / saved['target']).read_bytes()
        self.source.write_text(FORM.replace(':PARAMETER.NAME', ':PARAMETER.OTHER'))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(['migrate', str(self.source), '--screen', '--module', 'lookup', '--out', str(self.output), '--regenerate'])
        self.assertEqual(code, 0)
        self.assertEqual(before, (self.output / saved['target']).read_bytes())
        after = bindings.sheet(self.output)
        self.assertTrue(after['contract_changed'])
        self.assertEqual(after['bindings'], config['bindings'])
        self.assertTrue(any(r['source'] == 'PARAMETER.OTHER' for r in after['fresh_requirements']))
        with self.assertRaises(bindings.BindingConflict): bindings.save(self.output, config, after['revision'])

    def test_failed_multi_file_write_rolls_back_code_and_config(self):
        sheet, config = self.mapping()
        paths = [self.output / sheet['target'], self.output / bindings.REPORT, self.output / 'analysis/effective-config.json']
        before = {p: p.read_bytes() for p in paths}
        original = Path.replace
        def fail(path, target):
            if target == self.output / bindings.REPORT: raise OSError('disk full')
            return original(path, target)
        with patch.object(Path, 'replace', fail):
            with self.assertRaises(OSError): bindings.save(self.output, config, sheet['revision'])
        self.assertEqual(before, {p: p.read_bytes() for p in paths})

    def test_manual_trigger_candidates_include_local_program_unit_binds(self):
        from test_manual_action_source import FORM as manual
        self.source.write_text(manual)
        self.generate(self.root / 'manual')
        sheet = bindings.sheet(self.root / 'manual')
        row = next(r for r in sheet['rows'] if r['owner'] == 'CGNV$W01_1.PB_LEKERDEZES' and r['source'] == 'V_ELEK_ASD.UBI_ASD_KOD')
        self.assertTrue(row['inferred'])
        self.assertEqual(row['status'], 'review')

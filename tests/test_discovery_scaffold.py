import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from frm_forms.cli import main
from frm_forms.common import MigrationError
from frm_forms.discovery import scan, build_map, write_map
from frm_forms.rules import analyze
from frm_forms.xmlmodel import parse_xml
from java_support import COMPANY_IMPORTS, write_stubs


def fixture(extra_property=False):
    form = ET.Element('FormModule', Name='TEST', Title='Test')
    ET.SubElement(form, 'Coordinate', CoordinateSystem='Real', RealUnit='Inch')
    ET.SubElement(form, 'Canvas', Name='MAIN', CanvasType='Content', Width='200', Height='100')
    control = ET.SubElement(form, 'Block', Name='CALENDAR', DatabaseDataBlock='false')
    for label in ['A', 'B']:
        ET.SubElement(control, 'Item', Name=label, ItemType='Text Item', DatabaseItem='true', ColumnName='', CanvasName='MAIN')
    block = ET.SubElement(form, 'Block', Name='B', DatabaseDataBlock='true', QueryDataSourceName='T')
    ET.SubElement(block, 'Item', Name='ID', ItemType='Text Item', DataType='Number', PrimaryKey='true', ColumnName='ID', CanvasName='MAIN')
    ET.SubElement(block, 'Item', Name='VALUE', ItemType='Text Item', ColumnName='VALUE', CanvasName='MAIN')
    button = ET.SubElement(block, 'Item', Name='QUERY', ItemType='Push Button', CanvasName='MAIN')
    ET.SubElement(button, 'Trigger', Name='WHEN-BUTTON-PRESSED', TriggerText='BEGIN start_query; END;')
    ET.SubElement(form, 'ProgramUnit', Name='START_QUERY', ProgramUnitText='PROCEDURE start_query IS BEGIN other_query; pkg.load(p_id => :B.ID, p_out => :B.VALUE); END;')
    ET.SubElement(form, 'ProgramUnit', Name='OTHER_QUERY', ProgramUnitText='PROCEDURE other_query IS BEGIN start_query; SELECT X INTO :B.VALUE FROM T WHERE ID = :B.ID; END;')
    if extra_property: block.set('UnrecognizedBehavior', 'NeedsReview')
    return ET.tostring(form, encoding='utf-8')


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def source(self, raw=None):
        path = self.root / 'test_fmb.xml'; path.write_bytes(raw or fixture()); return path

    def migrate(self, source=None, *args):
        source = source or self.source()
        out = self.root / ('result' + str(len(list(self.root.glob('result*')))))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            status = main(['migrate', str(source), '--module', 'test', '--out', str(out), '--ai', 'off', *args])
        return status, out

    def test_lexer_excludes_comments_literals_and_declarations(self):
        result = scan("""PROCEDURE declared(x IN NUMBER) IS
BEGIN
-- fake(:SECRET.VALUE)
x := q'[also_fake(:SECRET.VALUE)]';
x := 'still_fake()';
pkg.real(:B.ID, NVL(:B.VALUE, 0)); no_args;
:B.VALUE := 1;
END;""")
        self.assertEqual([c['name'] for c in result['calls']], ['PKG.REAL', 'NVL', 'NO_ARGS'])
        self.assertEqual(result['calls'][0]['line'], 6)
        self.assertEqual(result['binds'][-1]['usage'], 'assignment-target')
        self.assertNotIn('SECRET.VALUE', [b['name'] for b in result['binds']])

    def test_transitive_graph_handles_cycles_and_keeps_sql_binds(self):
        data = build_map([('test_fmb.xml', ET.fromstring(fixture()))])
        action = data['actions'][0]
        self.assertEqual(len(action['reachable_code']), 3)
        self.assertEqual(action['external_calls'], ['PKG.LOAD'])
        self.assertEqual(action['binds'], ['B.ID', 'B.VALUE'])
        self.assertEqual(sum(len(c['sql']) for c in data['code']), 1)
        self.assertEqual(action['execution'], 'disabled')

    def test_numeric_whitespace_only_analysis_view(self):
        raw = '-- comment&#10;BEGIN pkg.run(:B.ID); END;'
        form = ET.Element('FormModule', Name='X')
        ET.SubElement(form, 'Trigger', Name='WHEN-BUTTON-PRESSED', TriggerText=raw)
        data = build_map([('x.xml', form)]); write_map(data, self.root / 'map')
        code = data['code'][0]
        self.assertEqual(code['source'], raw)
        self.assertEqual(code['calls'][0]['name'], 'PKG.RUN')
        self.assertEqual((self.root/'map'/code['source_file']).read_text(), raw)
        self.assertIn('\nBEGIN', (self.root/'map'/code['view_file']).read_text())

    def test_dynamic_sql_and_trigger_candidates_remain_uncertain(self):
        form = ET.Element('FormModule', Name='X')
        ET.SubElement(form, 'Trigger', Name='KEY-EXEQRY', TriggerText='BEGIN NULL; END;')
        ET.SubElement(form, 'Trigger', Name='WHEN-BUTTON-PRESSED', TriggerText="BEGIN EXECUTE IMMEDIATE 'select * from t'; EXECUTE_TRIGGER('KEY-EXEQRY'); END;")
        data = build_map([('x.xml', form)]); code = data['code'][1]
        self.assertTrue(code['dynamic_sql'])
        call = next(c for c in code['calls'] if c['name'] == 'EXECUTE_TRIGGER')
        self.assertEqual(call['event_target_candidates'], [data['code'][0]['id']])
        self.assertFalse(call['signature_verified'])
        self.assertEqual(len(data['actions'][0]['reachable_code']), 1)

    def test_library_standalone_item_is_not_form_action(self):
        library = ET.fromstring('<ObjectLibrary Name="LIB"><Item Name="BUTTON"><Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="BEGIN foo; END;"/></Item></ObjectLibrary>')
        data = build_map([('lib_olb.xml', library)])
        self.assertEqual(data['actions'], [])
        self.assertEqual(data['code'][0]['owner'], '@LIBRARY.BUTTON')

    def test_offline_html_escapes_embedded_script(self):
        form = ET.Element('FormModule', Name='</script><script>alert(1)</script>')
        data = build_map([('x.xml', form)]); write_map(data, self.root)
        html = (self.root/'form-explorer.html').read_text()
        self.assertNotIn('</script><script>alert(1)</script>', html)
        self.assertIn("connect-src 'none'", html)
        self.assertNotIn('innerHTML', html)

    def test_control_block_items_never_become_db_columns(self):
        model = parse_xml(self.source()); analyze(model, {}, {})
        control = model['blocks'][0]
        self.assertEqual(control['db_items'], [])
        self.assertFalse(any(i['database'] for i in control['items']))

    def test_actual_db_duplicate_remains_error_even_in_scaffold(self):
        raw = fixture().replace(b'ColumnName="VALUE"', b'ColumnName="ID"')
        path = self.source(raw)
        with self.assertRaisesRegex(MigrationError, 'ID.*ID.*VALUE'):
            analyze(parse_xml(path), {}, {})
        status, out = self.migrate(path, '--scaffold')
        self.assertEqual(status, 1); self.assertFalse(out.exists())

    def test_blank_db_columns_become_sql_mapping_issues(self):
        raw = fixture().replace(b'ColumnName="VALUE"', b'ColumnName=""').replace(b'ColumnName="ID"', b'ColumnName=""')
        model = parse_xml(self.source(raw)); analyze(model, {}, {})
        self.assertTrue(any(i['code']=='SQL_MAPPING' for i in model['issues']))
        self.assertFalse(model['blocks'][1]['can_read'])

    def test_strict_refuses_but_scaffold_preserves_issues_and_guards(self):
        path = self.source(fixture(True)); status, out = self.migrate(path)
        self.assertEqual(status, 1); self.assertFalse(out.exists())
        status, out = self.migrate(path, '--scaffold')
        self.assertEqual(status, 0)
        info = json.loads((out/'analysis/summary.json').read_text())
        self.assertEqual(info['readable_blocks'], 0); self.assertEqual(info['writable_blocks'], 0)
        self.assertGreater(info['frontend_error_issues'], 0)
        self.assertEqual(info['ai']['attempted_calls'], 0)
        self.assertIn('UnrecognizedBehavior'.lower(), (out/'analysis/ui-model.json').read_text().lower())
        base = next(out.glob('backend/DPS/*ServiceImpl.java')).read_text()
        # The original Forms code is evidence, not commented-out code in the ServiceImpl.
        self.assertIn('pkg.load(p_id => :B.ID', (out/'analysis/backend-evidence.md').read_text()); self.assertIn('HttpStatus.NOT_IMPLEMENTED', base)
        structure = next(out.glob('frontend/*/review-structure.ts')).read_text()
        self.assertIn('"disabled": true', structure); self.assertIn('"startValue": null', structure)
        plan = json.loads((out/'analysis/action-plan.json').read_text())['actions'][0]
        self.assertEqual(plan['method'], 'POST'); self.assertFalse(plan['implemented'])

    def test_unsupported_widget_is_placeholder(self):
        raw = fixture().replace(b'Name="VALUE" ItemType="Text Item"', b'Name="VALUE" ItemType="Unknown Widget"')
        status, out = self.migrate(self.source(raw), '--scaffold'); self.assertEqual(status, 0)
        structure = next(out.glob('frontend/*/review-structure.ts')).read_text()
        self.assertIn('"widget": "unsupported"', structure)
        self.assertNotIn('"formControlName": "value"', structure)

    def test_analysis_only_contains_map_without_generated_source(self):
        status, out = self.migrate(self.source(fixture(True)), '--analysis-only')
        self.assertEqual(status, 3); self.assertTrue((out/'analysis/discovery/form-explorer.html').exists())
        self.assertFalse((out/'frontend').exists()); self.assertFalse((out/'backend').exists())

    def test_regenerate_preserves_implementation_and_forbids_mode_change(self):
        path = self.source(); status, out = self.migrate(path, '--scaffold'); self.assertEqual(status, 0)
        implementation = next(out.glob('backend/DPS/*ServiceImpl.java'))
        custom = implementation.read_bytes() + b'\n// reviewed custom implementation\n'; implementation.write_bytes(custom)
        args = ['migrate', str(path), '--out', str(out), '--module', 'test', '--regenerate']
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(args + ['--scaffold']), 0)
            self.assertEqual(main(args), 1)
        self.assertEqual(implementation.read_bytes(), custom)

    def test_issue_preview_counts_full_diagnostics(self):
        _, out = self.migrate(self.source(fixture(True)), '--scaffold')
        full = json.loads((out/'analysis/issues.json').read_text())
        preview = json.loads((out/'analysis/issues-summary.json').read_text())
        self.assertEqual(preview['total'], len(full))
        self.assertEqual(sum(g['count'] for g in preview['groups']), len(full))
        self.assertLessEqual(len(preview['issues']), 100)

    def test_generated_actions_compile_access_checked_before_review_or_denial(self):
        if not shutil.which('java'): self.skipTest('Java 17+ required')
        raw = fixture().replace(b'BEGIN start_query;', b'BEGIN start_query; -- \\u000a break comment')
        company = self.root/'company.json'; company.write_text(json.dumps({'java_company_imports': COMPANY_IMPORTS}))  # log1x, UserDto stubs
        status, out = self.migrate(self.source(raw), '--scaffold', '--config', str(company)); self.assertEqual(status, 0)
        method = json.loads((out/'analysis/action-plan.json').read_text())['actions'][0]['method_name']
        smoke = self.root/'ActionSmoke.java'
        smoke.write_text('''package hu.company.features.test.dps;
import hu.company.features.test.cl.TestDtos.*;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;
public class ActionSmoke {
 public static void main(String[] args) {
  int[] checks={0};
  var service=new TestServiceImpl(new org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate(), null, (block,operation)->{ checks[0]++; if(!block.equals("B")) throw new AssertionError(); });
  try { service.METHOD(new ActionRequest(java.util.Map.of(), java.util.Map.of())); throw new AssertionError("Expected 501"); }
  catch(ResponseStatusException ex) { if(ex.status!=HttpStatus.NOT_IMPLEMENTED || checks[0]!=1) throw new AssertionError(); }
  var denied=new TestServiceImpl(new org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate(), null, (b,o)->{throw new IllegalStateException("denied");});
  try {denied.METHOD(null); throw new AssertionError();} catch(IllegalStateException expected) {}
  var reviewed=new TestServiceImpl(new org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate(), null, (b,o)->{checks[0]++;}) {
   @Override protected ActionResult METHODReviewed(ActionRequest request) { return new ActionResult(java.util.Map.of(), java.util.List.of("reviewed")); }
  };
  if(!reviewed.METHOD(new ActionRequest(java.util.Map.of(), java.util.Map.of())).messages().get(0).equals("reviewed") || checks[0]!=2) throw new AssertionError();
  System.out.println("action access and 501 assertions passed");
 }
}'''.replace('METHOD', method))
        sources = list(out.glob('backend/**/*.java')) + write_stubs(self.root/'stubs') + [smoke]
        argfile = self.root/'sources.args'; argfile.write_text('\n'.join('"'+str(p).replace('\\','/')+'"' for p in sources))
        classes = self.root/'classes'
        build = subprocess.run(['java','com.sun.tools.javac.Main','-encoding','UTF-8','--release','17','-d',str(classes),'@'+str(argfile)],capture_output=True,text=True,timeout=60)
        self.assertEqual(build.returncode,0,build.stdout+build.stderr)
        run = subprocess.run(['java','-cp',str(classes),'hu.company.features.test.dps.ActionSmoke'],capture_output=True,text=True,timeout=15)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)

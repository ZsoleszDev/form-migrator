"""Database routines of the Forms code: the Java assistant calls generated, typed methods instead of guessing them.

A trigger like PRE-INSERT `kod_pkg.check_insert(:B.KOD, :B.DATUM, :B.NEV)` keeps running in Oracle. With its reviewed
signature (schema.json procedures) the migrator offers `String kodPkgCheckInsert(String pKod, LocalDateTime pDatum)`;
an applied proposal that calls it gets the method (DbCalls on the service's NamedParameterJdbcTemplate) and compiles.
"""
import contextlib
import io
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch

from frm_forms import backend_assistant as engine, db_routines
from frm_forms.cli import main
from frm_forms.rules import procedure_signatures
from java_support import COMPANY_IMPORTS, trigger_mode
from test_ai_triggers import compile_module, method_text
from test_backend_assistant import Client

setUpModule, tearDownModule = trigger_mode('ai')

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / 'tests/golden/dbcall'
SERVICE = 'backend/DPS/DbcallServiceImpl.java'
SIGNATURE = {'kind': 'procedure', 'arguments': [
    {'name': 'P_ID', 'mode': 'IN', 'type': 'number', 'default': False},
    {'name': 'P_NEV', 'mode': 'OUT', 'type': 'text', 'default': False},
    {'name': 'P_DATUM', 'mode': 'IN OUT', 'type': 'datetime', 'default': False},
    {'name': 'P_FORCE', 'mode': 'IN', 'type': 'text', 'default': True}]}


ACTION_ONLY = '''<Module><FormModule Name="NEVKERES"><Coordinate CoordinateSystem="Real" RealUnit="Pixel"/>
 <Block Name="CTRL" DatabaseDataBlock="false">
  <Item ItemType="Text Item" Name="KOD" DataType="Char" MaximumLength="10" CanvasName="C" XPosition="10" YPosition="10" Width="80" Height="20"/>
  <Item ItemType="Text Item" Name="NEV" DataType="Char" MaximumLength="80" CanvasName="C" XPosition="10" YPosition="40" Width="200" Height="20"/>
  <Item ItemType="Push Button" Name="BTN" Label="Név" CanvasName="C" XPosition="10" YPosition="70" Width="80" Height="20">
   <Trigger Name="WHEN-BUTTON-PRESSED" TriggerText=":CTRL.NEV := kod_pkg.get_nev(:CTRL.KOD);"/>
  </Item>
 </Block><Canvas Name="C" CanvasType="Content" WindowName="W"/><Window Name="W"/>
</FormModule></Module>'''


def calls(*entries):
    return [{'calls': [{'name': name, 'kind': kind, 'arguments': list(arguments)} for name, kind, arguments in entries]}]


class RoutinePlanTests(unittest.TestCase):
    def test_positional_named_and_out_parameters(self):
        methods, refused = db_routines.plan(calls(
            ('PKG.LOAD', 'external_candidate', ['p_id => :B.ID', 'p_nev => :B.NEV', 'p_datum => :B.D']),
            ('PKG.LOAD', 'external_candidate', [':B.ID', ':B.NEV', ':B.D', "'I'"])), {'APP.PKG.LOAD': SIGNATURE})
        self.assertEqual(refused, [])
        named, positional = methods
        # by name: the omitted P_FORCE keeps its database default
        self.assertIn('"BEGIN PKG.LOAD(P_ID => ?, P_NEV => ?, P_DATUM => ?); END;"', named['source'])
        self.assertIn('DbCalls.inOut(pDatum, Types.TIMESTAMP)', named['source'])
        self.assertEqual(named['header'], 'private PkgLoadOut pkgLoad(BigDecimal pId, LocalDateTime pDatum)')
        self.assertIn('static final class PkgLoadOut', named['source'])
        self.assertIn('"{call PKG.LOAD(?, ?, ?, ?)}"', positional['source'])
        self.assertEqual(positional['header'], 'private PkgLoad2Out pkgLoad2(BigDecimal pId, LocalDateTime pDatum, String pForce)')

    def test_what_has_no_method_is_named_with_the_reason(self):
        signature = dict(SIGNATURE, arguments=SIGNATURE['arguments'][:2])
        methods, refused = db_routines.plan(calls(
            ('PKG.LOAD', 'external_candidate', [':B.ID']),           # P_NEV (OUT) has no default
            ('PKG.UNKNOWN', 'external_candidate', []),
            ('DBMS_OUTPUT.PUT_LINE', 'external_candidate', ["'x'"]),  # Oracle-supplied
            ('QMS$EVENT_FORM', 'unresolved', ["'x'"]),                # framework plumbing
            ('ADD_MONTHS', 'unresolved', ['d', '1'])), {'PKG.LOAD': signature},
            {'call_prefixes': ('qms$',), 'runtime_calls': ()})
        self.assertEqual(methods, [])
        self.assertEqual(refused, ['PKG.LOAD: the call omits a parameter without a default', 'PKG.UNKNOWN: no reviewed signature'])


class RoutineAssistantTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        config = self.root / 'config.json'
        config.write_text(json.dumps({'java_company_imports': COMPANY_IMPORTS, 'backend_trigger_mode': 'ai'}), encoding='utf-8')
        self.output = self.root / 'out'
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['migrate', str(GOLDEN / 'input.xml'), '--schema', str(GOLDEN / 'schema.json'),
                                   '--module', 'dbcall', '--config', str(config), '--out', str(self.output)]), 0)
        self.tasks = {t['method']: t for t in engine.prepare_workspace(self.output)['tasks']}

    def packet(self, method):
        return json.loads((self.output / engine.AREA / 'tasks' / (self.tasks[method]['id'] + '.json')).read_text(encoding='utf-8'))

    def test_the_prompt_offers_the_routine_methods_and_names_the_rest(self):
        insert = engine.preview(self.output, self.tasks['triggerPreInsertB']['id'], engine.Options())['user']
        self.assertIn('- String kodPkgCheckInsert(String pKod, LocalDateTime pDatum) = KOD_PKG.CHECK_INSERT(P_KOD, P_DATUM, '
                      'OUT P_NEV): returns P_NEV', insert)
        self.assertIn('call those methods (the only allowed PL/SQL calls)', insert)
        query = engine.preview(self.output, self.tasks['triggerPostQueryB']['id'], engine.Options())['user']
        self.assertIn('- String kodPkgGetNev(String pKod) = KOD_PKG.GET_NEV(P_KOD)', query)
        delete = engine.preview(self.output, self.tasks['triggerPreDeleteB']['id'], engine.Options())['user']
        self.assertIn('Called database routines without a method: UNDOCUMENTED_PKG.PURGE: no reviewed signature.', delete)
        self.assertNotIn('Database routine methods', delete)
        self.assertEqual(self.packet('triggerPreDeleteB')['external_calls'], ['UNDOCUMENTED_PKG.PURGE'])  # hooks too

    def test_an_applied_proposal_gets_the_routine_method_and_compiles(self):
        answer = """```java
// SERVICE
private void triggerPreInsertB(BRow row, RuleContext context) {
    row.nev = kodPkgCheckInsert(row.kod, row.datum);
}
```"""
        client = Client(answer)
        with patch('frm_forms.ollama.opener', return_value=client):
            result = engine.propose(self.output, self.tasks['triggerPreInsertB']['id'], engine.Options())
        self.assertEqual(result['status'], 'proposal', result)
        engine.apply_proposal(self.output, result['id'], reviewed=True)
        service = (self.output / SERVICE).read_text(encoding='utf-8')
        self.assertIn('row.nev = kodPkgCheckInsert(row.kod, row.datum);', method_text(service, 'triggerPreInsertB'))
        routine = re.sub(r'"\s*\+\s*"', '', method_text(service, 'kodPkgCheckInsert'))  # the formatter wraps long literals
        self.assertIn('DbCalls.call(jdbc, "{call KOD_PKG.CHECK_INSERT(?, ?, ?)}"', routine)
        self.assertEqual(service.count('private String kodPkgCheckInsert('), 1)
        if shutil.which('java'):
            work = self.root / 'compile'
            compile_module(self, self.output, work)

    def test_an_action_only_module_gets_the_jdbc_template_with_its_first_routine(self):
        form = self.root / 'nevkeres.xml'
        form.write_text(ACTION_ONLY, encoding='utf-8')
        schema = self.root / 'procedures.json'
        schema.write_text(json.dumps({'procedures': json.loads((GOLDEN / 'schema.json').read_text())['procedures']}))
        config = self.root / 'config.json'
        output = self.root / 'nevkeres'
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['migrate', str(form), '--schema', str(schema), '--module', 'nevkeres', '--screen',
                                   '--config', str(config), '--out', str(output)]), 0)
        service_path = output / 'backend/DPS/NevkeresServiceImpl.java'
        self.assertNotIn('NamedParameterJdbcTemplate', service_path.read_text(encoding='utf-8'))
        task = next(t for t in engine.prepare_workspace(output)['tasks'] if t['method'] == 'onCtrlBtn')
        answer = """```java
// SERVICE
public ActionResult onCtrlBtn(UserDto user, ActionRequest request) throws Exception {
    return log1x(log, NevkeresConstants.ON_CTRL_BTN_NAME, user, null, () -> {
        var blocks = new java.util.LinkedHashMap<String, java.util.Map<String, String>>();
        String nev = kodPkgGetNev(request.blocks().get("CTRL").get("KOD"));
        blocks.put("CTRL", java.util.Map.of("NEV", nev == null ? "" : nev));
        return new ActionResult(blocks, java.util.List.of());
    });
}
```"""
        with patch('frm_forms.ollama.opener', return_value=Client(answer)):
            result = engine.propose(output, task['id'], engine.Options())
        self.assertEqual(result['status'], 'proposal', result)
        engine.apply_proposal(output, result['id'], reviewed=True)
        service = service_path.read_text(encoding='utf-8')
        self.assertIn('private NamedParameterJdbcTemplate jdbc;', service)
        self.assertIn('private String kodPkgGetNev(String pKod) {', service)
        if shutil.which('java'):
            compile_module(self, output, self.root / 'compile-nevkeres')

    def test_a_model_that_writes_the_routine_itself_is_corrected(self):
        guessed = """```java
// SERVICE
private void triggerPreInsertB(BRow row, RuleContext context) {
    row.nev = kodPkgCheckInsert(row.kod, row.datum);
}

private String kodPkgCheckInsert(String kod, java.time.LocalDateTime datum) {
    return kod;
}
```"""
        fixed = """```java
// SERVICE
private void triggerPreInsertB(BRow row, RuleContext context) {
    row.nev = kodPkgCheckInsert(row.kod, row.datum);
}
```"""
        client = Client(guessed, fixed)
        with patch('frm_forms.ollama.opener', return_value=client):
            result = engine.propose(self.output, self.tasks['triggerPreInsertB']['id'], engine.Options(attempts=2))
        self.assertEqual(result['calls'], 2)
        self.assertIn('Do not write kodPkgCheckInsert', client.requests[1]['messages'][-1]['content'])


if __name__ == '__main__':
    unittest.main()

"""Simple buttons and DML hooks without the local AI (action_java, hook_java.with_dml).

A button that reads screen values, checks them, asks once (SHOW_ALERT), runs its SQL through the generated repository
methods and writes a value back needs no model: the migrator translates it with Oracle semantics and answers the
screen's ActionResult contract. Whatever is outside the subset stays the Java assistant's task.

The jpa data access of the Java assistant (`backend-assist --data-access jpa`); the default, "service", writes
everything in the service through the EntityManager (test_ai_triggers).
"""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from frm_forms import action_java, backend_assistant as engine, hook_java, jpa_sql
from frm_forms.plsql import Unsupported
from java_support import trigger_mode
from test_jpa_sql import compile_java, migrate

setUpModule, tearDownModule = trigger_mode('ai')

ITEMS = {'CTRL.KOD': 'text', 'CTRL.DB': 'number', 'RENDELES.ID': 'number', 'RENDELES.STATUSZ': 'text',
         'RENDELES.DATUM': 'datetime'}


def method(name, signature, statement, kind, arguments, repository='RendelesRepository'):
    return jpa_sql.Method(repository, repository[:1].lower() + repository[1:], name, signature, '', statement, kind,
                          tuple(arguments))


UPDATE = method('updateStatuszById', 'int updateStatuszById(BigDecimal id)', "UPDATE rendeles SET statusz = 'L' WHERE id = :RENDELES.ID",
                'update', [':RENDELES.ID'])
DELETE = method('deleteByRendelesId', 'int deleteByRendelesId(BigDecimal rendelesId)',
                'DELETE FROM rendeles_tetel WHERE rendeles_id = :RENDELES.ID', 'delete', [':RENDELES.ID'],
                'RendelesTetelRepository')
GENERATED = {'calls': {jpa_sql.statement_key(m.statement): m for m in (UPDATE, DELETE)}, 'methods': [UPDATE, DELETE]}


def translate(code):
    return action_java.translate(code, ITEMS, GENERATED, {})


class TranslateTests(unittest.TestCase):
    def test_checks_sql_written_values_and_commands(self):
        body = translate("BEGIN\n  IF :RENDELES.ID IS NULL THEN\n    MESSAGE('Válasszon rendelést.');\n    RAISE FORM_TRIGGER_FAILURE;\n"
                         "  END IF;\n  UPDATE rendeles SET statusz = 'L' WHERE id = :RENDELES.ID;\n  :RENDELES.STATUSZ := 'L';\n"
                         "  MESSAGE('Lezárva', NO_ACKNOWLEDGE);\n  SYNCHRONIZE;\n  GO_BLOCK('rendeles');\n  EXECUTE_QUERY;\nEND;")
        self.assertEqual(body, '\n'.join([
            'var values = request.blocks() == null ? Map.<String, Map<String, String>>of() : request.blocks();',
            'var screen = new Object() {',
            '    BigDecimal rendelesId = PlsqlValues.number(values, "RENDELES", "ID");',
            '    String rendelesStatusz = PlsqlValues.text(values, "RENDELES", "STATUSZ");',
            '};',
            'var context = new RuleContext();',
            'List<List<String>> commands = new ArrayList<>();',
            'if (SqlValues.truth(SqlValues.isNull(screen.rendelesId))) {',
            '    context.message("Válasszon rendelést.");',
            '    context.abort();',
            '}',
            'rendelesRepository.updateStatuszById(screen.rendelesId);',
            'screen.rendelesStatusz = "L";',
            'context.message("Lezárva");',
            'commands.add(List.of("GO_BLOCK", "RENDELES"));',
            'commands.add(List.of("EXECUTE_QUERY"));',
            'Map<String, Map<String, String>> blocks = new LinkedHashMap<>();',
            'PlsqlValues.put(blocks, "RENDELES", "STATUSZ", screen.rendelesStatusz);',
            'return new ActionResult(blocks, context.messages(), commands, Map.of());']))

    def test_one_alert_asked_before_anything_happens(self):
        code = ("DECLARE\n  al_id ALERT;\n  al_button NUMBER;\nBEGIN\n  IF :RENDELES.ID IS NULL THEN\n    MESSAGE('Nincs rendelés');\n"
                "    RAISE FORM_TRIGGER_FAILURE;\n  END IF;\n  al_id := FIND_ALERT('KERDES');\n"
                "  SET_ALERT_PROPERTY(al_id, ALERT_MESSAGE_TEXT, 'Törli a(z) ' || :CTRL.KOD || ' rendelés tételeit?');\n"
                "  al_button := SHOW_ALERT(al_id);\n  IF al_button = ALERT_BUTTON1 THEN\n"
                "    DELETE FROM rendeles_tetel WHERE rendeles_id = :RENDELES.ID;\n  END IF;\nEND;")
        body = translate(code)
        # the check that stops the button runs first (it runs again with the answer), then the question
        self.assertLess(body.index('context.abort();'), body.index('String alertAnswer'))
        self.assertIn('if (alertAnswer == null) {\n    return new ActionResult(Map.of(), List.of(), List.of(List.of("SHOW_ALERT", '
                      '"KERDES", SqlValues.concat(SqlValues.concat("Törli a(z) ", ', body)
        self.assertIn('local.alButton = new BigDecimal(alertAnswer);\n'
                      'if (SqlValues.truth(SqlValues.compare(local.alButton, new BigDecimal("1"), "="))) {\n'
                      '    rendelesTetelRepository.deleteByRendelesId(screen.rendelesId);\n}', body)
        inline = translate("BEGIN\n  IF SHOW_ALERT('BIZTOS') = ALERT_BUTTON2 THEN\n    RAISE FORM_TRIGGER_FAILURE;\n  END IF;\n"
                           "  UPDATE rendeles SET statusz = 'L' WHERE id = :RENDELES.ID;\nEND;")
        self.assertIn('List.of(List.of("SHOW_ALERT", "BIZTOS"))', inline)
        self.assertIn('if (SqlValues.truth(SqlValues.compare(new BigDecimal(alertAnswer), new BigDecimal("2"), "="))) {', inline)

    def test_what_stays_the_assistants(self):
        refused = {
            "BEGIN\n  MESSAGE('x');\n  IF SHOW_ALERT('A') = ALERT_BUTTON1 THEN NULL; END IF;\nEND;": 'A SHOW_ALERT előtt már történik',
            "BEGIN\n  IF SHOW_ALERT('A') = ALERT_BUTTON1 THEN NULL; END IF;\n  IF SHOW_ALERT('B') = ALERT_BUTTON1 THEN NULL; END IF;\nEND;":
                'Egynél több SHOW_ALERT',
            "BEGIN\n  COMMIT_FORM;\n  MESSAGE('kész');\nEND;": 'COMMIT_FORM csak a gomb utolsó utasításaként',
            "BEGIN\n  :GLOBAL.X := 'a';\nEND;": 'Nem a képernyő mezője',
            "BEGIN\n  naplo_pkg.ir(:CTRL.KOD);\nEND;": 'Nem támogatott hívás',
            "BEGIN\n  UPDATE rendeles SET statusz = 'X';\nEND;": 'SQL generált repository-metódus nélkül',
            "BEGIN\n  :CTRL.DB := :CTRL.KOD;\nEND;": 'Implicit hozzárendelési konverzió',
        }
        for code, reason in refused.items():
            with self.subTest(code=code):
                with self.assertRaises(Unsupported) as caught:
                    translate(code)
                self.assertIn(reason, str(caught.exception))
        self.assertIn('commands.add(List.of("COMMIT_FORM"));', translate("BEGIN\n  :CTRL.KOD := 'A';\n  COMMIT_FORM;\nEND;"))


class HookDmlTests(unittest.TestCase):
    def test_a_hook_runs_its_dml_through_the_generated_method(self):
        hook = {'block': 'RENDELES', 'table': 'RENDELES', 'fields': [
            {'item': 'RENDELES.ID', 'field': 'id', 'type': 'number', 'column': 'ID', 'database': True}]}
        body, reason = hook_java.translate('BEGIN\n  DELETE FROM rendeles_tetel\n   WHERE rendeles_id = :RENDELES.ID;\nEND;', hook, GENERATED)
        self.assertEqual((body, reason), ('rendelesTetelRepository.deleteByRendelesId(row.id);', ''))
        body, reason = hook_java.translate("BEGIN\n  UPDATE rendeles SET statusz = 'Z';\nEND;", hook, GENERATED)
        self.assertIsNone(body)
        self.assertIn('SQL generált repository-metódus nélkül', reason)


class ReplicaTests(unittest.TestCase):
    """The replica form: its confirm-and-close button needs no model, the others stay the model's."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.out = migrate(self.root)
        self.options = engine.Options(data_access="jpa", num_ctx=16384)

    def tearDown(self):
        self.temp.cleanup()

    def tasks(self):
        return {t['method']: t for t in engine.prepare_workspace(self.out)['tasks']}

    def test_the_confirm_button_is_translated_applied_and_compiles(self):
        tasks = self.tasks()
        self.assertTrue(engine.preview(self.out, tasks['onCtrlPbLezar']['id'], self.options)['deterministic'])
        with patch('frm_forms.ollama.opener', side_effect=AssertionError('no model call')):
            result = engine.propose(self.out, tasks['onCtrlPbLezar']['id'], self.options)
        self.assertEqual((result['calls'], result['deterministic'], result['status']), (0, True, 'proposal'))
        body = result['response']['body']
        self.assertIn('return new ActionResult(Map.of(), List.of(), List.of(List.of("SHOW_ALERT", "KERDES", '
                      '"Biztosan lezárja a rendelést?")), Map.of());', body)
        self.assertIn('rendelesRepository.updateStatuszById(screen.rendelesId);\n    screen.rendelesStatusz = "L";\n'
                      '} else {\n    context.abort();\n}', body)
        engine.apply_proposal(self.out, result['id'], reviewed=True)
        # A form package procedure and a DO_KEY key trigger: outside the subset, the model's as before.
        for name in ('onCtrlPbUjraszamol', 'onCtrlPbMent'):
            with self.subTest(method=name):
                self.assertFalse(engine.preview(self.out, tasks[name]['id'], self.options)['deterministic'])
        compile_java(self, self.root, list((self.out / 'backend').rglob('*.java')))

    def test_the_usual_slips_of_a_model_answer_are_repaired_and_compile(self):
        """row.getX(), an entity named RendelesEntity, findById/save: one call, no correction round."""
        from test_backend_assistant import Client
        answer = ('''```java
// SERVICE
private void triggerOnUpdateRendeles(RendelesRow row, RuleContext context) {
    RendelesEntity record = rendelesRepository.findById(row.getId()).orElse(null);
    if (record == null) {
        context.message("Nincs ilyen rendelés.");
        context.abort();
    }
    record.setStatusz(row.getStatusz());
    record.setOsszeg(row.getOsszeg());
    rendelesRepository.save(record);
}
```
''')
        client = Client(answer)
        with patch('frm_forms.ollama.opener', return_value=client):
            result = engine.propose(self.out, self.tasks()['triggerOnUpdateRendeles']['id'], self.options)
        self.assertEqual((result['status'], result['calls'], len(client.requests)), ('proposal', 1, 1))
        notes = ' '.join(result['extraction']['notes'])
        self.assertIn('RendelesEntity -> RendelesDomain', notes)  # the company names the entity <Table>Domain
        self.assertIn('a rekord mezői (id, osszeg, statusz) getter/setter helyett', notes)
        self.assertIn('RendelesDomain record = rendelesRepository.findById(row.id).orElse(null);',
                      result['response']['body'])
        engine.apply_proposal(self.out, result['id'], reviewed=True)
        repository = (self.out / 'backend/DPS/RendelesRepository.java').read_text(encoding='utf-8')
        # findById and save are JpaRepository's: nothing is written for them
        self.assertIn('public interface RendelesRepository extends JpaRepository<RendelesDomain, BigDecimal> {', repository)
        self.assertNotIn('findById(', repository)
        compile_java(self, self.root, list((self.out / 'backend').rglob('*.java')))


if __name__ == '__main__':
    unittest.main()

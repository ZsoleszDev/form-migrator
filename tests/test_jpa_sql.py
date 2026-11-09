"""The SQL of a trigger as generated repository methods (jpa_sql), and simple record triggers without the AI (hook_java).

A small local model fails most often on the repository (EntityManager code in the service, a call of a repository
method it never wrote), and each such answer costs a correction call. So the migrator writes the repository
methods of the SQL it can translate exactly (Criteria API, persist, the sequence query; the Forms SQL in their
comment), the prompt offers them and asks for the service only; a simple record trigger needs no model at all.
"""
import contextlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from frm_forms import backend_assistant as engine, jpa_assistant, jpa_sql, schema_workbench as schema
from frm_forms.cli import main
from frm_forms.common import MigrationError
from java_support import COMPANY_IMPORTS, trigger_mode, write_jpa_stubs, write_stubs
from test_backend_assistant import Client

setUpModule, tearDownModule = trigger_mode('ai')

ROOT = Path(__file__).resolve().parents[1]
REPLICA = ROOT / 'tests' / 'fixtures' / 'felmeres_replika_fmb.xml'


def number(name, precision, nullable=True):
    return {'name': name, 'data_type': 'NUMBER', 'nullable': nullable, 'precision': precision, 'scale': 0}


def text(name, length, nullable=True):
    return {'name': name, 'data_type': 'VARCHAR2', 'nullable': nullable, 'length': length, 'length_semantics': 'CHAR'}


TABLES = {'RENDELES': ([number('ID', 10, False), text('VEVO', 20), {'name': 'DATUM', 'data_type': 'DATE', 'nullable': True},
                        text('STATUSZ', 1), number('OSSZEG', 12)], ['ID']),
          'RENDELES_TETEL': ([number('ID', 10, False), number('RENDELES_ID', 10), text('CIKK', 30), number('MENNYISEG', 10),
                              text('AKTIV', 1)], ['ID']),
          'VEVO': ([text('KOD', 20, False), text('NEV', 80)], ['KOD'])}


def schema_tables():
    """The tables as schema.json keeps them (jpa_assistant.plan reads this form)."""
    result = {}
    for table, (columns, key) in TABLES.items():
        result[table] = {'primary_key': key, 'columns': {c['name']: {k: v for k, v in c.items() if k != 'name'} for c in columns}}
    return result


def persistence(root: Path) -> list[dict]:
    packet = {'module': 'rendeles', 'blocks': [], 'table_requirements': [{'table': t} for t in TABLES],
              'schema': {'tables': schema_tables()}, 'config': {}, 'service_package_source': 'package hu.company.rendeles.dps;\n'}
    return jpa_assistant.plan(packet, root)


def attach(code: str, root: Path, own: str | None = 'RENDELES', descriptors=None):
    descriptors = descriptors if descriptors is not None else persistence(root)
    packet = {'sources': [{'kind': 'trigger', 'name': 'T', 'source': code, 'calls': []}]}
    return jpa_sql.attach(packet, descriptors, own), descriptors


def compile_java(test, root: Path, sources: list[Path]) -> None:
    if not shutil.which('java'):
        return
    stubs = write_stubs(root / 'stubs') + write_jpa_stubs(root / 'stubs')
    args = root / 'sources.args'
    args.write_text('\n'.join('"' + str(p) + '"' for p in sources + stubs))
    built = subprocess.run(['java', 'com.sun.tools.javac.Main', '--release', '11', '-encoding', 'UTF-8', '-d', str(root / 'classes'),
                            '@' + str(args)], capture_output=True, text=True, timeout=120)
    test.assertEqual(built.returncode, 0, built.stdout + built.stderr)


class FindTests(unittest.TestCase):
    def test_statements_cursors_and_loops_in_order(self):
        code = ("-- SELECT x INTO y FROM comment;\nBEGIN\n  v := 'UPDATE t SET a = 1';\n"
                "  SELECT nev INTO :B.NEV FROM vevo WHERE kod = :B.KOD;\n  UPDATE t SET a = 1;\n"
                "  FOR r IN (SELECT a FROM t ORDER BY a) LOOP NULL; END LOOP;\n  DELETE FROM t WHERE a = 2;\n"
                "  INSERT INTO t (a) VALUES (3);\nEND;\nDECLARE\n  CURSOR c (p NUMBER) IS SELECT 1 FROM t WHERE a = p;\nBEGIN NULL; END;")
        found = jpa_sql.find(code)
        self.assertEqual([f.kind for f in found], ['select', 'update', 'loop', 'delete', 'insert', 'cursor'])
        self.assertEqual(found[2].text, 'SELECT a FROM t ORDER BY a')
        self.assertEqual(found[5].cursor, 'C')


class TranslateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def method(self, code, own='RENDELES'):
        result, _ = attach(code, self.root, own)
        self.assertEqual(result['refused'], [])
        return result['methods']

    def refused(self, code):
        result, _ = attach(code, self.root)
        self.assertEqual(result['methods'], [])
        return result['refused'][0]['reason']

    def test_select_into_is_a_value_with_the_pl_sql_exceptions(self):
        [method] = self.method('SELECT nev INTO :RENDELES.VEVO_NEV FROM vevo WHERE kod = :RENDELES.VEVO;')
        self.assertEqual((method.repository, method.signature), ('RendelesVevoRepository', 'String findNevByKod(String kod)'))
        self.assertEqual(method.arguments, (':RENDELES.VEVO',))
        self.assertIn(' * Forms-SQL: SELECT nev INTO :RENDELES.VEVO_NEV FROM vevo WHERE kod = :RENDELES.VEVO', method.source)
        self.assertIn('Nincs sor: NoResultException (NO_DATA_FOUND)', method.source)
        self.assertIn('query.where(cb.equal(root.<String>get("kod"), kodValue));', method.source)
        self.assertIn('.setParameter(kodValue, kod)\n                .getSingleResult();', method.source)

    def test_aggregates_existence_and_lists(self):
        count, total, last = self.method(
            "SELECT COUNT(*) INTO v FROM rendeles_tetel WHERE rendeles_id = :RENDELES.ID AND aktiv = 'I';\n"
            'SELECT SUM(mennyiseg) INTO v FROM rendeles_tetel WHERE rendeles_id = :RENDELES.ID;\n'
            'SELECT MAX(vevo) INTO v FROM rendeles;')
        self.assertEqual(count.signature, 'BigDecimal countByRendelesIdAndAktiv(BigDecimal rendelesId)')
        self.assertIn('return BigDecimal.valueOf(entityManager.createQuery(query)', count.source)
        self.assertEqual(total.signature, 'BigDecimal sumMennyisegByRendelesId(BigDecimal rendelesId)')
        self.assertIn('query.select(cb.greatest(root.<String>get("vevo")));', last.source)  # MAX of a text: greatest
        exists, rows = self.method('DECLARE CURSOR c IS SELECT 1 FROM rendeles_tetel t WHERE t.rendeles_id = :RENDELES.ID;\n'
                                   'BEGIN NULL; END;\nFOR r IN (SELECT cikk FROM rendeles_tetel WHERE UPPER(cikk) LIKE '
                                   "UPPER(:CTRL.SZURO) || '%' ORDER BY cikk DESC) LOOP NULL; END LOOP;")
        self.assertEqual((exists.signature, exists.kind), ('boolean existsByRendelesId(BigDecimal rendelesId)', 'exists'))
        self.assertIn('.setMaxResults(1)', exists.source)
        self.assertEqual(rows.signature, 'List<RendelesRendelesTetelEntity> findAllByCikk(String cikk)')
        self.assertIn('cb.like(cb.upper(root.<String>get("cikk")), cb.concat(cb.upper(cikkValue), cb.literal("%")))', rows.source)
        self.assertIn('query.orderBy(cb.desc(root.get("cikk")));', rows.source)

    def test_writes(self):
        insert, sequence = self.method('INSERT INTO rendeles (id, vevo, datum, statusz, osszeg) '
                                       "VALUES (rendeles_seq.NEXTVAL, :RENDELES.VEVO, SYSDATE, 'U', NULL);")
        self.assertEqual(insert.signature, 'void insertRendeles(String vevo)')
        for line in ('entity.setId(nextRendelesSeq());', 'entity.setVevo(vevo);', 'entity.setDatum(now);',
                     'entity.setStatusz("U");', 'entityManager.persist(entity);'):
            self.assertIn(line, insert.source)
        self.assertNotIn('setOsszeg', insert.source)  # NULL: a new entity's field is null already
        self.assertEqual(sequence.signature, 'BigDecimal nextRendelesSeq()')
        update, delete = self.method('UPDATE rendeles SET statusz = NULL, osszeg = osszeg + :CTRL.EV WHERE id = :RENDELES.ID;\n'
                                     "DELETE FROM rendeles_tetel WHERE rendeles_id = :RENDELES.ID AND aktiv IN ('N', 'X');")
        self.assertEqual(update.signature, 'int updateStatuszAndOsszegById(BigDecimal osszeg, BigDecimal id)')
        self.assertIn('update.set(root.<String>get("statusz"), cb.nullLiteral(String.class));', update.source)
        self.assertIn('cb.sum(root.<BigDecimal>get("osszeg"), osszegValue)', update.source)
        self.assertEqual(delete.signature, 'int deleteByRendelesIdAndAktiv(BigDecimal rendelesId)')
        self.assertIn('root.<String>get("aktiv").in(cb.literal("N"), cb.literal("X"))', delete.source)
        [sequence] = self.method('SELECT tetel_seq.NEXTVAL INTO :TETEL.ID FROM dual;', own='RENDELES_TETEL')
        self.assertEqual((sequence.repository, sequence.signature), ('RendelesRendelesTetelRepository', 'BigDecimal nextTetelSeq()'))
        self.assertIn('createNativeQuery("SELECT TETEL_SEQ.NEXTVAL FROM DUAL")', sequence.source)

    def test_what_the_subset_does_not_cover_is_named(self):
        for code, reason in (('SELECT r.nev INTO x FROM vevo r, rendeles s WHERE r.kod = s.vevo;', 'több tábla'),
                             ('SELECT DISTINCT vevo INTO x FROM rendeles;', 'DISTINCT'),
                             ('SELECT COUNT(*) INTO x FROM rendeles GROUP BY vevo;', 'GROUP'),
                             ('DELETE FROM rendeles WHERE vevo IN (SELECT kod FROM vevo);', 'allekérdezés'),
                             ("UPDATE rendeles SET statusz = 'L' WHERE osszeg || 'x' = :B.X || 'x';", 'implicit konverzió'),
                             ('SELECT NVL(osszeg, 0) INTO x FROM rendeles WHERE id = 1;', 'NVL'),
                             ("INSERT INTO rendeles (id, vevo) VALUES (1, 'V');", 'alapértéküket'),
                             ('UPDATE rendeles SET statusz = :B.S WHERE id = 1 RETURNING osszeg INTO :B.O;', 'RETURNING')):
            with self.subTest(code=code):
                self.assertIn(reason, self.refused(code))
        # A SELECT FROM DUAL without a sequence is Java (SYSDATE ...): no method and nothing to list.
        result, _ = attach('SELECT SYSDATE INTO :B.D FROM dual;', self.root)
        self.assertEqual((result['methods'], result['refused']), ([], []))

    def test_methods_are_reused_and_never_overwrite_another(self):
        code = 'SELECT nev INTO :RENDELES.VEVO_NEV FROM vevo WHERE kod = :RENDELES.VEVO;'
        result, descriptors = attach(code + '\n' + code, self.root)
        self.assertEqual(len(result['methods']), 1)  # the same statement twice: one method
        vevo = next(d for d in descriptors if d['table'] == 'VEVO')
        self.assertEqual(vevo['existing_repository'].count('public String findNevByKod('), 1)
        # A later task finds the method in the repository file: reused as it is.
        again, _ = attach(code, self.root, descriptors=descriptors)
        self.assertEqual(again['methods'][0].name, 'findNevByKod')
        self.assertEqual(vevo['existing_repository'].count('public String findNevByKod('), 1)
        # Another statement with the same name: a numbered method next to it.
        other, _ = attach("SELECT nev INTO :RENDELES.VEVO_NEV FROM vevo WHERE kod = 'X';", self.root, descriptors=descriptors)
        self.assertEqual(other['methods'][0].signature, 'String findNevByKod2()')

    def test_the_generated_repositories_compile(self):
        result, descriptors = attach(
            'SELECT nev INTO :RENDELES.VEVO_NEV FROM vevo WHERE kod = :RENDELES.VEVO;\n'
            "SELECT id, vevo INTO :RENDELES.ID, :RENDELES.VEVO FROM rendeles WHERE id BETWEEN :CTRL.TOL AND :CTRL.IG;\n"
            'SELECT COUNT(*) INTO v FROM rendeles_tetel WHERE rendeles_id = :RENDELES.ID AND mennyiseg > 10;\n'
            'SELECT SUM(mennyiseg) INTO v FROM rendeles_tetel WHERE rendeles_id = :RENDELES.ID;\n'
            "SELECT MIN(datum) INTO v FROM rendeles WHERE statusz <> 'L' AND datum <= SYSDATE;\n"
            'DECLARE CURSOR c IS SELECT 1 FROM rendeles_tetel t WHERE t.rendeles_id = :RENDELES.ID; BEGIN NULL; END;\n'
            "FOR r IN (SELECT * FROM rendeles_tetel WHERE NOT (aktiv = 'N' OR cikk IS NULL) ORDER BY cikk) LOOP NULL; END LOOP;\n"
            "INSERT INTO rendeles (id, vevo, datum, statusz, osszeg) VALUES (rendeles_seq.NEXTVAL, :B.V, SYSDATE, 'U', 0);\n"
            "UPDATE rendeles SET osszeg = osszeg - 1, datum = SYSDATE WHERE vevo LIKE :B.V || '%';\n"
            'DELETE FROM rendeles_tetel WHERE rendeles_id = :RENDELES.ID;', self.root)
        self.assertEqual(result['refused'], [])
        self.assertEqual(len(result['methods']), 11)
        sources = []
        for descriptor in descriptors:
            for path, source in descriptor['files'].items():
                target = self.root / 'src' / Path(path).name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding='utf-8')
                sources.append(target)
        compile_java(self, self.root, sources)


def model_path():
    """The model's way for a button the migrator translates itself (action_java): the tests of the AI path."""
    return patch('frm_forms.action_java.response', return_value=(None, 'a modell útját teszteljük'))


def migrate(root: Path) -> Path:
    config = root / 'config.json'
    config.write_text(json.dumps({'java_company_imports': COMPANY_IMPORTS, 'backend_trigger_mode': 'ai', 'backend_live': True,
                                  'screen_window_selection': 'all', 'screen_primary_window_auto': True}))
    out = root / 'rendeles'
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert main(['migrate', str(REPLICA), '--out', str(out), '--screen', '--module', 'rendeles', '--config', str(config)]) == 0
    for table, (columns, key) in TABLES.items():
        schema.save_table(out, table, columns, key, schema.schema_sheet(out)['revision'])
    return out


class AssistantTests(unittest.TestCase):
    """The replica form with its tables: what the Java assistant gets, and what needs no model at all."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.out = migrate(self.root)
        self.options = engine.Options(num_ctx=16384)

    def tearDown(self):
        self.temp.cleanup()

    def tasks(self):
        return {t['method']: t for t in engine.prepare_workspace(self.out)['tasks']}

    def test_the_prompt_offers_the_generated_methods_and_asks_for_the_service_only(self):
        tasks = self.tasks()
        lezar = engine.preview(self.out, tasks['onCtrlPbLezar']['id'], self.options)
        self.assertTrue(lezar['deterministic'])  # the migrator writes this button (action_java); the prompt is the fallback
        self.assertIn('2. Database access only through the generated repository methods listed below', lezar['user'])
        self.assertIn("- rendelesRendelesRepository.updateStatuszById(BigDecimal id) returns int\n"
                      "  SQL: UPDATE rendeles SET statusz = 'L' WHERE id = :RENDELES.ID", lezar['user'])
        self.assertNotIn('// REPOSITORY', lezar['user'])
        self.assertNotIn('UPDATE / INSERT / DELETE of a table: a repository method', lezar['user'])
        # The || trick of ON-UPDATE is outside the subset: the model writes that one method.
        update = engine.preview(self.out, tasks['triggerOnUpdateRendeles']['id'], self.options)
        self.assertIn('SQL without a generated method (write one public method for each, in the repository of its table):\n'
                      '- UPDATE rendeles r SET r.statusz = :RENDELES.STATUSZ', update['user'])
        self.assertIn('// REPOSITORY RendelesRendelesRepository', update['user'])

    def test_simple_record_triggers_need_no_model(self):
        expected = {
            'triggerPostChangeVevoRendeles': 'try {\n    row.vevoNev = rendelesVevoRepository.findNevByKod(row.vevo);\n'
                                             '} catch (NoResultException e) {\n    row.vevoNev = null;\n}',
            'triggerPreInsertTetel': 'row.id = rendelesRendelesTetelRepository.nextTetelSeq();',
            'triggerWhenValidateItemMennyisegTetel': 'if (SqlValues.truth(SqlValues.compare(row.mennyiseg, new BigDecimal("1000"), ">"))) {\n'
                                                     '    context.message("Túl nagy mennyiség.");\n    context.abort();\n}',
            'triggerOnCheckDeleteMasterRendeles': 'if (rendelesRendelesTetelRepository.existsByRendelesId(row.id)) {\n'
                                                  '    context.message("A rendeléshez tételek tartoznak.");\n    context.abort();\n}'}
        self.assertTrue(engine.preview(self.out, self.tasks()['triggerPreInsertTetel']['id'], self.options)['deterministic'])
        for method, body in expected.items():
            with self.subTest(method=method), patch('frm_forms.ollama.opener', side_effect=AssertionError('no model call')):
                result = engine.propose(self.out, self.tasks()[method]['id'], self.options)
                self.assertEqual((result['calls'], result['deterministic'], result['status']), (0, True, 'proposal'))
                self.assertEqual(result['response']['body'], body)
                engine.apply_proposal(self.out, result['id'], reviewed=True)
        # ON-INSERT calls a database routine, ON-UPDATE has SQL outside the subset: the model's, as before.
        for method in ('triggerOnInsertRendeles', 'triggerOnUpdateRendeles'):
            with self.subTest(method=method), patch('frm_forms.ollama.opener', side_effect=AssertionError('model')):
                with self.assertRaises(AssertionError):
                    engine.propose(self.out, self.tasks()[method]['id'], self.options)
        service = (self.out / 'backend/DPS/RendelesServiceImpl.java').read_text(encoding='utf-8')
        self.assertIn('import javax.persistence.NoResultException;', service)
        hook = service[service.index('private void triggerPostChangeVevoRendeles('):]
        hook = hook[:hook.index('\n    }\n')]
        self.assertIn('//region Eredeti Forms-kód: RENDELES.VEVO / POST-CHANGE', hook)  # the original code stays
        self.assertIn('// Bemenet/kimenet: a rekord', hook)
        self.assertNotIn('AI-ra vár', hook)  # written: it waits for nothing
        self.assertIn('private RendelesVevoRepository rendelesVevoRepository;', service)
        repository = (self.out / 'backend/DPS/RendelesRendelesTetelRepository.java').read_text(encoding='utf-8')
        self.assertIn('public boolean existsByRendelesId(BigDecimal rendelesId) {', repository)
        self.assertIn('public BigDecimal nextTetelSeq() {', repository)
        compile_java(self, self.root, list((self.out / 'backend').rglob('*.java')))

    def test_the_model_calls_a_generated_method(self):
        answer = '''```java
// SERVICE
public ActionResult onCtrlPbLezar(UserDto user, ActionRequest request) throws Exception {
    return log1x(log, RendelesConstants.ON_CTRL_PB_LEZAR_NAME, user, null, () -> {
        Map<String, String> answers = request.parameters();
        if (answers == null || answers.get("FRM.ALERTS") == null) {
            return new ActionResult(Map.of(), List.of(), List.of(List.of("SHOW_ALERT", "KERDES", "Biztosan lezárja a rendelést?")), Map.of());
        }
        if (!"1".equals(answers.get("FRM.ALERTS"))) {
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "A művelet megszakítva.");
        }
        rendelesRendelesRepository.updateStatuszById(PlsqlValues.number(request.blocks(), "RENDELES", "ID"));
        Map<String, Map<String, String>> blocks = new LinkedHashMap<>();
        PlsqlValues.put(blocks, "RENDELES", "STATUSZ", "L");
        return new ActionResult(blocks, List.of(), List.of(), Map.of());
    });
}
```
'''
        client = Client(answer)
        with patch('frm_forms.ollama.opener', return_value=client), model_path():
            result = engine.propose(self.out, self.tasks()['onCtrlPbLezar']['id'], self.options)
        self.assertEqual((result['status'], result['calls'], result['deterministic']), ('proposal', 1, False))
        engine.apply_proposal(self.out, result['id'], reviewed=True)
        repository = (self.out / 'backend/DPS/RendelesRendelesRepository.java').read_text(encoding='utf-8')
        self.assertIn('public int updateStatuszById(BigDecimal id) {', repository)
        compile_java(self, self.root, list((self.out / 'backend').rglob('*.java')))

    def test_entity_manager_in_the_service_is_still_refused_and_kept_as_a_draft(self):
        answer = '''```java
// SERVICE
public ActionResult onCtrlPbLezar(UserDto user, ActionRequest request) throws Exception {
    return log1x(log, RendelesConstants.ON_CTRL_PB_LEZAR_NAME, user, null, () -> {
        entityManager.createQuery("UPDATE x").executeUpdate();
        return new ActionResult(Map.of(), List.of(), List.of(), Map.of());
    });
}
```
'''
        with patch('frm_forms.ollama.opener', return_value=Client(answer)), model_path():
            result = engine.propose(self.out, self.tasks()['onCtrlPbLezar']['id'], engine.Options(num_ctx=16384, attempts=1))
        # The call is not lost: a draft with the problem, which can be read but not applied.
        self.assertEqual((result['status'], result['calls'], result['validation']['structural_checks']), ('draft', 1, 'failed'))
        self.assertIn('Az AI utolsó válaszának hibája: A service közvetlenül EntityManagert használ', result['extraction']['warnings'][0])
        draft = (self.out / engine.AREA / 'proposals' / result['id'] / result['files'][0]['candidate']).read_text(encoding='utf-8')
        self.assertIn('entityManager.createQuery("UPDATE x").executeUpdate();', draft)
        with self.assertRaisesRegex(MigrationError, 'nem alkalmazható'):
            engine.apply_proposal(self.out, result['id'], reviewed=True)
        # Not cached: the next run asks the model again.
        with patch('frm_forms.ollama.opener', return_value=Client(answer)) as opener, model_path():
            engine.propose(self.out, self.tasks()['onCtrlPbLezar']['id'], engine.Options(num_ctx=16384, attempts=1))
        self.assertEqual(opener.call_count, 1)


if __name__ == '__main__':
    unittest.main()

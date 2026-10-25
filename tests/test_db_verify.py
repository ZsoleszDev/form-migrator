"""4.14: verify-db compiles the generated SQL and PL/SQL in the target database, never runs it.

migrate writes analysis/db-statements.json (every text the module sends to Oracle); verify-db parses
each with DBMS_SQL.PARSE and reports the errors with the line they point at. A fake connection stands
in for Oracle here: it records what was sent and fails the statements a test names.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from java_support import COMPANY_IMPORTS
from frm_forms import db_verify
from frm_forms.cli import main
from frm_forms.common import MigrationError

ROOT = Path(__file__).resolve().parents[1]
REPLICA = ROOT / 'tests' / 'fixtures' / 'felmeres_replika_fmb.xml'


class OracleError:
    def __init__(self, message):
        self.message = message


class FakeCursor:
    def __init__(self, failing):
        self.failing, self.sent, self.sizes = failing, [], []

    def setinputsizes(self, **sizes):
        self.sizes.append(sizes)

    def execute(self, sql, **binds):
        assert sql == db_verify.PARSE_BLOCK  # only the PARSE block ever runs
        self.sent.append(binds['stmt'])
        for marker, message in self.failing.items():
            if marker in binds['stmt']:
                raise RuntimeError(OracleError(message))


class FakeConnection:
    def __init__(self, failing=None):
        self.cursor_ = FakeCursor(failing or {})
        self.calls = []

    def cursor(self):
        return self.cursor_

    def rollback(self):
        self.calls.append('rollback')

    def close(self):
        self.calls.append('close')

    def commit(self):  # never called
        self.calls.append('commit')


class HelperTests(unittest.TestCase):
    def test_positional_binds_get_names_outside_strings_and_comments(self):
        self.assertEqual(db_verify.named_binds("BEGIN x := ?; y := '?'; -- ?\n z := ?; /* ? */ END;"),
                         "BEGIN x := :b1; y := '?'; -- ?\n z := :b2; /* ? */ END;")

    def test_only_queries_dml_and_anonymous_blocks_reach_the_database(self):
        cursor = FakeCursor({})
        for sql in ('CREATE TABLE t (a NUMBER)', 'drop table t', 'ALTER SESSION SET x = 1', 'GRANT SELECT ON t TO u',
                    'TRUNCATE TABLE t', '-- csak megjegyzés', ''):
            self.assertIn('Nem ellenőrizhető', db_verify.check(cursor, sql))
        self.assertEqual(cursor.sent, [])  # PARSE would run DDL at once: nothing was sent
        for sql in ('SELECT 1 FROM dual', ' with a as (select 1 x from dual) select x from a', 'DELETE FROM t',
                    'MERGE INTO t USING s ON (1 = 1) WHEN MATCHED THEN UPDATE SET a = 1', 'DECLARE x NUMBER; BEGIN NULL; END;',
                    '/* x */ BEGIN NULL; END;'):
            self.assertIsNone(db_verify.check(cursor, sql))
        self.assertEqual(len(cursor.sent), 6)

    def test_the_error_and_the_line_it_points_at(self):
        message = 'ORA-06550: line 2, column 8:\nPLS-00201: identifier \'Y\' must be declared'
        cursor = FakeCursor({'y;': message})
        sql = 'BEGIN\n  x := y;\nEND;'
        self.assertEqual(db_verify.check(cursor, sql), message)
        self.assertEqual(db_verify.excerpt(sql, message), 'x := y;')
        self.assertIsNone(db_verify.excerpt(sql, 'ORA-00942: table or view does not exist'))
        self.assertIsNone(db_verify.excerpt(sql, 'ORA-06550: line 9, column 1:'))

    def test_large_texts_go_as_clob(self):
        cursor = FakeCursor({})
        db_verify.check(cursor, 'BEGIN NULL; END;', clob='CLOB')
        self.assertEqual(cursor.sizes, [{'stmt': 'CLOB'}])


class ReplicaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('FRM_JAVA_IMPORT_MAP', '-')
        os.environ.setdefault('FRM_JAVA_VARIABLE_MAP', '-')  # nor a developer's own java-variables.json
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        config = cls.root / 'config.json'
        config.write_text(json.dumps({'java_company_imports': COMPANY_IMPORTS, 'backend_live': True,
                                      'screen_window_selection': 'all', 'screen_primary_window_auto': True}))
        cls.out = cls.root / 'out'
        with contextlib.redirect_stdout(io.StringIO()):
            assert main(['migrate', str(REPLICA), '--out', str(cls.out), '--screen', '--module', 'rendeles',
                         '--config', str(config)]) == 0
        cls.plan = json.loads((cls.out / 'analysis/db-statements.json').read_text(encoding='utf-8'))
        cls.service = (cls.out / 'backend/DPS/RendelesServiceImpl.java').read_text(encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def sources(self):
        return {s['source']: s for s in self.plan['statements']}

    def test_every_text_the_module_sends_is_collected(self):
        sources = self.sources()
        for source in ('CTRL.PB_KERES / WHEN-BUTTON-PRESSED', 'CTRL.PB_MENT / WHEN-BUTTON-PRESSED', 'RENDELES:ON-INSERT',
                       'TETEL.MENNYISEG:WHEN-VALIDATE-ITEM', 'LOV LOV_STATUSZ', 'RENDELES lista', 'TETEL beszúrás'):
            self.assertIn(source, sources)
        self.assertEqual(self.plan['module_class'], 'Rendeles')
        self.assertEqual([s['id'] for s in self.plan['statements']], list(range(1, len(self.plan['statements']) + 1)))
        self.assertTrue(all(db_verify.first_word(s['sql']) in db_verify.ALLOWED for s in self.plan['statements']))

    def test_only_what_the_service_runs(self):
        # ON-INSERT replaces the INSERT of RENDELES: its generated INSERT is not run, so it is not checked either
        self.assertNotIn('RENDELES beszúrás', self.sources())
        keres = self.sources()['CTRL.PB_KERES / WHEN-BUTTON-PRESSED']['sql']
        self.assertIn('frm_cmd', keres)  # the whole block, the shared FormsPlsql helpers included
        self.assertIn('?', keres)

    def test_cli_writes_the_reports_and_fails_on_errors(self):
        connection = FakeConnection({'statusz_kodtar': 'ORA-00942: table or view does not exist'})
        report = self.root / 'DB.md'
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {'FRM_TEST_DB_PW': 'titok'}), \
                mock.patch.object(db_verify, 'connect_oracle', return_value=connection) as connect, \
                contextlib.redirect_stdout(stdout):
            code = main(['verify-db', str(self.out), '--dsn', 'db:1521/ORCL', '--user', 'app', '--password-env', 'FRM_TEST_DB_PW',
                         '--report', str(report)])
        self.assertEqual(code, 3)
        connect.assert_called_once_with('db:1521/ORCL', 'app', 'titok')
        self.assertEqual(connection.calls, ['rollback', 'close'])  # nothing committed
        self.assertEqual(len(connection.cursor_.sent), len(self.plan['statements']))
        self.assertTrue(all(db_verify.named_binds(sql) == sql for sql in connection.cursor_.sent))  # no ? left
        verified = json.loads((self.out / 'analysis/db-verify.json').read_text(encoding='utf-8'))
        self.assertEqual((verified['checked'], verified['failed']), (len(self.plan['statements']), 1))
        failed = [r for r in verified['results'] if not r['ok']]
        self.assertEqual(failed[0]['source'], 'LOV LOV_STATUSZ')
        text = report.read_text(encoding='utf-8')
        self.assertIn('hibás: 1', text)
        self.assertIn('**LOV LOV_STATUSZ** (sql): ORA-00942', text)
        self.assertEqual(json.loads(stdout.getvalue())['failed'], 1)
        self.assertNotIn('titok', text + stdout.getvalue())

    def test_plsql_binds_are_named_before_parsing(self):
        connection = FakeConnection()
        summary = db_verify.verify([self.out], connection)
        self.assertEqual(summary['failed'], 0)
        plsql = [db_verify.named_binds(s['sql']) for s in self.plan['statements'] if s['kind'] == 'plsql']
        self.assertTrue(plsql and all(sql in connection.cursor_.sent for sql in plsql))
        self.assertTrue(any(':b1' in sql for sql in connection.cursor_.sent))

    def test_a_batch_folder_is_walked_and_a_clean_run_exits_zero(self):
        connection = FakeConnection()
        with mock.patch.dict(os.environ, {'FRM_DB_PASSWORD': 'x'}), \
                mock.patch.object(db_verify, 'connect_oracle', return_value=connection), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['verify-db', str(self.root), '--dsn', 'd', '--user', 'u']), 0)
        self.assertIn('hibás: 0', (self.root / 'DB_VERIFY_HU.md').read_text(encoding='utf-8'))

    def test_the_password_comes_from_the_environment_only(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(db_verify, 'connect_oracle') as connect:
            with self.assertRaises(MigrationError):
                db_verify.run(mock.Mock(password_env='FRM_DB_PASSWORD', outputs=[self.out], dsn='d', user='u', report=None))
        connect.assert_not_called()

    def test_folders_without_statements_are_an_error(self):
        with self.assertRaises(MigrationError):
            db_verify.module_folders([self.root / 'config.json', self.out / 'backend'])


if __name__ == '__main__':
    unittest.main()

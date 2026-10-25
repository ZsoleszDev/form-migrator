"""Table evidence, explicit metadata, optimistic edits and AI schema gating."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from frm_forms import backend_assistant as assistant
from frm_forms import schema_workbench as schema
from frm_forms.cli import main
from frm_forms.common import MigrationError, read_json, write_json
from frm_forms.dictionary import references
from test_backend_assistant import Client, answer
from java_support import trigger_mode

setUpModule, tearDownModule = trigger_mode('ai')


FORM = '''<Module><FormModule Name="LOOKUP" Title="Táblafelismerés">
<Coordinate CoordinateSystem="Real" RealUnit="Pixel"/>
<Block Name="CTRL" DatabaseDataBlock="false">
 <Item Name="ID" ItemType="Text Item" DataType="Number" CanvasName="C" XPosition="10" YPosition="10" Width="80" Height="20"/>
 <Item Name="NAME" ItemType="Text Item" DataType="Char" MaximumLength="100" CanvasName="C" XPosition="10" YPosition="40" Width="150" Height="20"/>
 <Item Name="RUN" ItemType="Push Button" Label="Keresés" CanvasName="C" XPosition="10" YPosition="70" Width="80" Height="20">
  <Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="BEGIN SELECT a.NAME INTO :CTRL.NAME FROM APP.ACCOUNT a WHERE a.ID = :CTRL.ID; END;"/>
 </Item>
</Block><Canvas Name="C" CanvasType="Content" WindowName="W"/><Window Name="W"/></FormModule></Module>'''


def columns():
    return [{'name': 'ID', 'data_type': 'NUMBER', 'nullable': False, 'precision': 10, 'scale': 0},
            {'name': 'NAME', 'data_type': 'VARCHAR2', 'nullable': True, 'length': 100, 'length_semantics': 'CHAR'}]


class TableReferenceTests(unittest.TestCase):
    def names(self, code):
        return {r['table'] for r in schema.table_references(code)['tables']}

    def test_select_join_comma_and_insert_update_delete_merge(self):
        code = '''SELECT a.ID, b.NAME FROM APP.A a JOIN APP.B b ON b.ID=a.ID, EXTRA e WHERE e.ID=a.ID;
INSERT INTO APP.T (ID, NAME) VALUES (:B.ID, :B.NAME);
UPDATE APP.U SET NAME=:B.NAME WHERE ID=:B.ID;
DELETE FROM APP.D WHERE ID=:B.ID;
MERGE INTO APP.M m USING APP.S s ON(m.ID=s.ID) WHEN MATCHED THEN UPDATE SET m.NAME=s.NAME;'''
        result = schema.table_references(code)
        self.assertEqual(self.names(code), {'APP.A', 'APP.B', 'EXTRA', 'APP.T', 'APP.U', 'APP.D', 'APP.M', 'APP.S'})
        inserted = next(r for r in result['tables'] if r['table'] == 'APP.T')
        self.assertEqual(inserted['columns'], ['ID', 'NAME'])
        self.assertEqual(next(r for r in result['tables'] if r['table'] == 'APP.B')['columns'], ['ID', 'NAME'])

    def test_literals_comments_binds_and_for_update_are_not_tables(self):
        code = """-- SELECT * FROM SECRET
/* JOIN PRIVATE_TABLE */
v := q'[UPDATE HIDDEN SET X=1]';
v := 'DELETE FROM NOT_A_TABLE';
SELECT r.ID INTO :BLOCK.ID FROM REAL_TABLE r FOR UPDATE;
"""
        self.assertEqual(self.names(code), {'REAL_TABLE'})

    def test_cte_aliases_are_scoped_and_nested_subqueries_are_followed(self):
        code = '''WITH first_cte AS (SELECT ID FROM APP.BASE), second_cte AS (SELECT ID FROM first_cte)
SELECT * FROM second_cte s JOIN (SELECT * FROM APP.DETAIL) d ON d.ID=s.ID;
SELECT * FROM first_cte;'''
        self.assertEqual(self.names(code), {'APP.BASE', 'APP.DETAIL', 'FIRST_CTE'})

    def test_extract_trim_and_table_functions_do_not_create_false_objects(self):
        code = "SELECT EXTRACT(YEAR FROM created), TRIM(BOTH 'x' FROM name) FROM APP.EVENTS; SELECT * FROM TABLE(pkg.rows());"
        self.assertEqual(self.names(code), {'APP.EVENTS'})
        self.assertTrue(schema.table_references(code)['warnings'])

    def test_dynamic_sql_and_remote_or_quoted_identifiers_remain_diagnostics(self):
        code = """EXECUTE IMMEDIATE 'SELECT * FROM HIDDEN'; SELECT * FROM APP.REMOTE@LINK;
SELECT * FROM "MixedTable"; SELECT * FROM APP."MixedTable";"""
        result = schema.table_references(code)
        self.assertEqual(result['tables'], [])
        self.assertGreaterEqual(len(result['warnings']), 3)

    def test_whitespace_around_owner_never_becomes_a_fake_owner_table(self):
        code = 'SELECT a.ID FROM APP . ACCOUNT a; INSERT INTO APP . HISTORY (ID) VALUES (1);'
        refs = schema.table_references(code)['tables']
        self.assertEqual({r['table'] for r in refs}, {'APP.ACCOUNT', 'APP.HISTORY'})
        self.assertTrue(all(r['columns'] == ['ID'] for r in refs))
        self.assertEqual(self.names('SELECT * FROM APP . REMOTE @ LINK;'), set())

    def test_type_anchors_have_evidence_not_automatic_database_resolution(self):
        refs = schema.table_references('v APP.ACCOUNT.NAME%TYPE; r cur%ROWTYPE;')
        self.assertEqual({r['table'] for r in refs['tables']}, {'APP.ACCOUNT', 'CUR'})
        self.assertEqual(next(r for r in refs['tables'] if r['table'] == 'APP.ACCOUNT')['columns'], ['NAME'])

    def test_nested_aliases_do_not_mix_columns_between_query_scopes(self):
        refs = schema.table_references('''SELECT a.ID FROM APP.A a WHERE EXISTS
            (SELECT a.OTHER FROM APP.B a WHERE a.OTHER=1);''')['tables']
        self.assertEqual(next(r for r in refs if r['table'] == 'APP.A')['columns'], ['ID'])
        self.assertEqual(next(r for r in refs if r['table'] == 'APP.B')['columns'], ['OTHER'])
        refs = schema.table_references('''SELECT a.ID FROM APP.A a WHERE EXISTS
            (SELECT a.DERIVED_COLUMN FROM (SELECT b.ID FROM APP.B b) a);''')['tables']
        self.assertEqual(next(r for r in refs if r['table'] == 'APP.A')['columns'], ['ID'])
        self.assertEqual(next(r for r in refs if r['table'] == 'APP.B')['columns'], ['ID'])


class TableValidationTests(unittest.TestCase):
    def test_nullable_is_explicit_and_primary_key_cannot_be_nullable(self):
        for value in (None, 0, 1, 'false'):
            rows = columns(); rows[0]['nullable'] = value
            with self.subTest(value=value), self.assertRaises(MigrationError):
                schema.validate_table('APP.A', rows, ['ID'])
        rows = columns(); rows[0]['nullable'] = True
        with self.assertRaises(MigrationError):
            schema.validate_table('APP.A', rows, ['ID'])

    def test_character_and_byte_lengths_are_kept_distinct(self):
        chars = schema.validate_table('APP.A', columns(), ['ID'])
        self.assertEqual(chars['columns']['NAME']['char_length'], 100)
        rows = columns(); rows[1]['length_semantics'] = 'BYTE'
        byte = schema.validate_table('APP.A', rows, ['ID'])
        self.assertEqual(byte['columns']['NAME']['data_length'], 100)
        self.assertNotIn('max_length', byte['columns']['NAME'])
        rows[1]['length_semantics'] = ''
        with self.assertRaises(MigrationError):
            schema.validate_table('APP.A', rows, ['ID'])

    def test_negative_scale_and_timestamp_fractional_precision(self):
        rows = columns(); rows[0]['scale'] = -2
        rows.append({'name': 'CREATED', 'data_type': 'TIMESTAMP', 'nullable': False, 'precision': 9})
        result = schema.validate_table('APP.A', rows, ['ID'])
        self.assertEqual(result['columns']['ID']['scale'], -2)
        self.assertEqual(result['columns']['CREATED']['datetime_precision'], 9)
        rows[-1]['precision'] = 10
        with self.assertRaises(MigrationError):
            schema.validate_table('APP.A', rows, ['ID'])

    def test_duplicates_bad_keys_invalid_types_and_no_columns_are_rejected(self):
        bads = [([], []), (columns() + [columns()[0]], ['ID']), (columns(), ['MISSING']), (columns(), ['ID', 'ID'])]
        for rows, keys in bads:
            with self.subTest(keys=keys), self.assertRaises(MigrationError):
                schema.validate_table('APP.A', rows, keys)
        for table in ('APP.A;DELETE', '../A', '"Quoted"', 'A@LINK'):
            with self.subTest(table=table), self.assertRaises(MigrationError):
                schema.validate_table(table, columns(), ['ID'])
        rows = columns(); rows[0]['data_type'] = 'CUSTOM_TYPE'
        with self.assertRaises(MigrationError):
            schema.validate_table('A', rows, ['ID'])

    def test_lob_and_timezone_types_are_not_mapped_to_string_or_localdatetime(self):
        result = schema.validate_table('A', [
            {'name': 'CONTENT', 'data_type': 'CLOB', 'nullable': True},
            {'name': 'AT', 'data_type': 'TIMESTAMP WITH TIME ZONE', 'nullable': False}], [])
        self.assertEqual(result['columns']['CONTENT']['type'], 'unsupported')
        self.assertEqual(result['columns']['AT']['type'], 'unsupported')


class SchemaWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.out = self.root / 'output'
        (self.root / 'form.xml').write_text(FORM, encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            status = main(['migrate', str(self.root / 'form.xml'), '--screen', '--module', 'lookup', '--out', str(self.out)])
        self.assertEqual(status, 0)
        self.task = assistant.prepare_workspace(self.out)['tasks'][0]

    def save(self, rows=None):
        sheet = schema.schema_sheet(self.out)
        return schema.save_table(self.out, 'APP.ACCOUNT', rows or columns(), ['ID'], sheet['revision'])

    def test_trigger_only_table_is_requested_without_ai_network(self):
        sheet = schema.schema_sheet(self.out)
        self.assertEqual(sheet['pending'], ['APP.ACCOUNT'])
        self.assertIsNone(sheet['tables'][0]['definition'])
        self.assertEqual(sheet['tables'][0]['referenced_columns'], ['ID', 'NAME'])
        with patch('frm_forms.ollama.opener', side_effect=AssertionError('must ask for schema')), self.assertRaisesRegex(MigrationError, 'táblaszerkezet'):
            assistant.propose(self.out, self.task['id'], assistant.Options())

    def test_saved_schema_reaches_model_and_trigger_only_repository(self):
        self.assertEqual(self.save()['pending'], [])
        index = assistant.prepare_workspace(self.out)
        packet = read_json(self.out / assistant.AREA / 'tasks' / (self.task['id'] + '.json'))
        self.assertIs(packet['schema']['tables']['APP.ACCOUNT']['columns']['ID']['nullable'], False)
        self.assertNotEqual(index['tasks'][0]['fingerprint'], self.task['fingerprint'])
        client = Client(answer())
        with patch('frm_forms.ollama.opener', return_value=client):
            proposal = assistant.propose(self.out, self.task['id'], assistant.Options(data_access='jdbc', num_ctx=32768))
        self.assertEqual(len(proposal['files']), 3)
        self.assertIn('LookupAppAccountRepository', client.requests[0]['messages'][1]['content'])
        self.assertEqual(read_json(self.out / 'analysis/schema.json')['tables']['APP.ACCOUNT']['primary_key'], ['ID'])

    def test_stale_revision_and_changed_schema_prevent_overwrite(self):
        revision = schema.schema_sheet(self.out)['revision']
        self.save()
        with self.assertRaises(schema.SchemaConflict):
            schema.save_table(self.out, 'APP.ACCOUNT', columns(), ['ID'], revision)
        client = Client(answer())
        with patch('frm_forms.ollama.opener', return_value=client):
            proposal = assistant.propose(self.out, self.task['id'], assistant.Options(num_ctx=32768))
        rows = columns(); rows[1]['length'] = 200; self.save(rows)
        with self.assertRaisesRegex(MigrationError, 'megváltozott'):
            assistant.apply_proposal(self.out, proposal['id'], True)

    def test_missing_referenced_column_keeps_task_waiting(self):
        self.save(columns()[:1])
        sheet = schema.schema_sheet(self.out)
        self.assertEqual(sheet['pending'], ['APP.ACCOUNT'])
        self.assertIn('NAME', ' '.join(sheet['tables'][0]['reasons']))

    def test_regeneration_preserves_schema_and_dictionary_finds_trigger_table(self):
        self.save()
        self.assertIn('APP.ACCOUNT', references([self.out], ())[0])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            status = main(['migrate', str(self.root / 'form.xml'), '--screen', '--module', 'lookup', '--out', str(self.out), '--regenerate'])
        self.assertEqual(status, 0)
        self.assertEqual(schema.schema_sheet(self.out)['pending'], [])

    def test_new_imported_schema_conflicts_with_old_manual_definition(self):
        self.save()
        path = self.out / 'analysis/effective-config.json'; effective = read_json(path)
        effective['metadata']['tables'] = {'APP.ACCOUNT': {
            'columns': {'OTHER': {'type': 'text', 'data_type': 'VARCHAR2', 'nullable': True}}, 'primary_key': []}}
        write_json(path, effective)
        sheet = schema.schema_sheet(self.out)
        self.assertTrue(sheet['tables'][0]['conflict'])
        self.assertEqual(sheet['pending'], ['APP.ACCOUNT'])

    def test_record_type_anchor_does_not_request_fake_table(self):
        sheet = schema.schema_sheet(self.out, [{'source': 'v rec.FIELD%TYPE;', 'owner': 'TEST'}], [])
        self.assertEqual(sheet['tables'], [])
        self.assertTrue(sheet['warnings'])

    def test_io_failure_restores_previous_metadata(self):
        self.save()
        original = read_json(self.out / assistant.AREA / 'schema-overrides.json')
        real = schema.atomic_json
        def failing(path, value):
            if path.name == 'schema.json':
                raise PermissionError('simulated Windows sharing lock')
            return real(path, value)
        rows = columns(); rows[1]['length'] = 200
        with patch('frm_forms.schema_workbench.atomic_json', side_effect=failing), self.assertRaises(PermissionError):
            self.save(rows)
        self.assertEqual(read_json(self.out / assistant.AREA / 'schema-overrides.json'), original)


if __name__ == '__main__':
    unittest.main()

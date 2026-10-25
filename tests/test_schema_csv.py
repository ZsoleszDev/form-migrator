"""Real CSV exports: type fidelity, unknown metadata and atomic rejection."""
from pathlib import Path
import unittest

from frm_forms import schema_workbench as schema
from frm_forms.common import MigrationError
from frm_forms.backend_assistant import data_models


SAMPLE = 'OSZLOP_NEVE1,NUMBER,22,20,0,N\nOSZLOP_NEVE2,NUMBER,22,20,0,N\nOSZLOP_NEVE3,VARCHAR2,30,,,Y'
HEADER = 'COLUMN_NAME,DATA_TYPE,DATA_LENGTH,DATA_PRECISION,DATA_SCALE,NULLABLE'


class CsvTests(unittest.TestCase):
    def parse(self, text):
        return schema.parse_table_csv(text, 'APP.TARGET')

    def test_exact_user_export_populates_types_and_does_not_infer_pk(self):
        result = self.parse(SAMPLE)
        number, _, varchar = result['columns']
        self.assertEqual((number['precision'], number['scale'], number['length']), (20, 0, None))
        self.assertFalse(number['nullable'])
        self.assertEqual((varchar['length'], varchar['length_semantics'], varchar['nullable']), (30, 'BYTE', True))
        self.assertNotIn('pk', number)
        self.assertTrue(any('kulcs' in warning for warning in result['warnings']))
        self.assertTrue(any('CHAR/BYTE' in warning for warning in result['warnings']))

    def test_headers_bom_quotes_crlf_and_blank_lines(self):
        result = self.parse('\ufeff' + HEADER + '\r\n\r\n"ID","NUMBER",22,20,0,N\r\n')
        self.assertEqual(len(result['columns']), 1)
        self.assertEqual(result['columns'][0]['name'], 'ID')
        self.assertEqual(result['columns'][0]['precision'], 20)

    def test_exact_headerless_eight_column_user_export(self):
        result = self.parse('OSZLOP_NEVE,VARCHAR2,40,,,N,40,B')
        column = result['columns'][0]
        self.assertEqual(column['name'], 'OSZLOP_NEVE')
        self.assertEqual((column['data_type'], column['length'], column['length_semantics']), ('VARCHAR2', 40, 'BYTE'))
        self.assertFalse(column['nullable'])
        self.assertNotIn('pk', column)
        self.assertFalse(any('CHAR/BYTE' in warning for warning in result['warnings']))

    def test_headerless_extended_exports_preserve_character_and_numeric_metadata(self):
        sample = ('\ufeff"ID",NUMBER,22,20,0,N,0,\r\n'
                  'NAME,VARCHAR2,120,,,Y,30,C\r\n'
                  'NATIONAL,NVARCHAR2,60,,,Y,30,C\r\n'
                  'CREATED,TIMESTAMP(6),11,,6,Y,0,NULL\r\n')
        for delimiter in (',', ';', '\t'):
            with self.subTest(delimiter=delimiter):
                result = self.parse(sample.replace(',', delimiter))
                number, varchar, national, timestamp = result['columns']
                self.assertEqual((number['precision'], number['scale'], number['length']), (20, 0, None))
                self.assertEqual((varchar['length'], varchar['length_semantics']), (30, 'CHAR'))
                self.assertEqual((national['length'], national['length_semantics']), (30, 'CHAR'))
                self.assertEqual((timestamp['data_type'], timestamp['precision']), ('TIMESTAMP', 6))
                definition = schema.validate_table('APP.TARGET', result['columns'], [])
                self.assertEqual(definition['columns']['NAME']['char_length'], 30)

    def test_extended_exports_reject_invalid_metadata_and_mixed_row_widths(self):
        bad_rows = ['ID,NUMBER,22,20,0,N,APP,TARGET', 'ID,NUMBER,22,20,0,N,20,B',
                    'NAME,VARCHAR2,40,,,N,40,X', 'NAME,VARCHAR2,40,,,N,-1,B',
                    'NAME,VARCHAR2,120,,,N,,C', 'NAME,VARCHAR2,40,,,N,40',
                    'NAME,VARCHAR2,40,,,N,40,B,EXTRA', 'NAME,VARCHAR2,40,,,N']
        for row in bad_rows:
            with self.subTest(row=row), self.assertRaises(MigrationError):
                self.parse('OK,VARCHAR2,40,,,N,40,B\n' + row)
        with self.assertRaisesRegex(MigrationError, '6 mező szükséges, 8 érkezett'):
            self.parse('OK,NUMBER,22,20,0,N\nNAME,VARCHAR2,40,,,N,40,B')

    def test_bad_headerless_field_count_explains_both_supported_formats(self):
        with self.assertRaisesRegex(MigrationError, '6 vagy 8 mező szükséges, 7 érkezett'):
            self.parse('NAME,VARCHAR2,40,,,N,40')

    def test_semicolon_and_tab_delimiters(self):
        for delimiter in (';', '\t'):
            with self.subTest(delimiter=delimiter):
                rows = self.parse(SAMPLE.replace(',', delimiter))['columns']
                self.assertEqual(len(rows), 3)
                self.assertEqual(rows[2]['data_type'], 'VARCHAR2')

    def test_reordered_extended_headers_keep_character_semantics(self):
        result = self.parse('NULLABLE,DATA_TYPE,COLUMN_NAME,CHAR_USED,CHAR_LENGTH,DATA_LENGTH,DATA_SCALE,DATA_PRECISION\nY,VARCHAR2,NAME,C,30,120,,')
        column = result['columns'][0]
        self.assertEqual((column['length'], column['length_semantics']), (30, 'CHAR'))
        definition = schema.validate_table('APP.TARGET', result['columns'], [])
        self.assertEqual(definition['columns']['NAME']['char_length'], 30)
        self.assertFalse(any('CHAR/BYTE' in warning for warning in result['warnings']))

    def test_national_character_length_is_not_guessed_from_bytes(self):
        with self.assertRaisesRegex(MigrationError, 'CHAR_LENGTH'):
            self.parse('N,NVARCHAR2,60,,,Y')
        result = self.parse(HEADER + ',CHAR_LENGTH,CHAR_USED\nN,NVARCHAR2,60,,,Y,30,C')
        self.assertEqual(result['columns'][0]['length'], 30)

    def test_timestamp_fractional_precision_and_number_negative_scale(self):
        result = self.parse('CREATED,TIMESTAMP(6),11,,6,Y\nROUND_TO_HUNDRED,NUMBER,22,10,-2,N')
        stamp, number = result['columns']
        self.assertEqual((stamp['data_type'], stamp['precision'], stamp['scale'], stamp['length']), ('TIMESTAMP', 6, None, None))
        self.assertEqual(number['scale'], -2)

    def test_owner_and_table_headers_are_checked(self):
        prefix = HEADER + ',OWNER,TABLE_NAME\n'
        self.assertEqual(len(self.parse(prefix + 'ID,NUMBER,22,10,0,N,APP,TARGET')['columns']), 1)
        for row in ('ID,NUMBER,22,10,0,N,OTHER,TARGET', 'ID,NUMBER,22,10,0,N,APP,WRONG'):
            with self.subTest(row=row), self.assertRaisesRegex(MigrationError, 'másik táblához'):
                self.parse(prefix + row)
        with self.assertRaisesRegex(MigrationError, 'több séma'):
            schema.parse_table_csv(prefix + 'ID,NUMBER,22,10,0,N,A,TARGET\nOTHER,NUMBER,22,10,0,N,B,TARGET', 'TARGET')

    def test_duplicate_columns_reject_mixed_owner_six_column_exports(self):
        with self.assertRaisesRegex(MigrationError, 'ismétlődő'):
            self.parse('ID,NUMBER,22,10,0,N\nid,NUMBER,22,10,0,N')

    def test_invalid_rows_reject_the_entire_export(self):
        bad_rows = ['X,NUMBER,22,20,0,MAYBE', 'X,NUMBER,22,3.5,0,N', 'X,NUMBER,22,39,0,N',
                    'X,NUMBER,22,20,128,N', 'X,UNKNOWN,22,,,N', 'X,VARCHAR2,0,,,Y',
                    'X,NUMBER,22,20,0', 'X,NUMBER,22,20,0,N,EXTRA', 'X,NUMBER,22,20,0,"N']
        for row in bad_rows:
            with self.subTest(row=row), self.assertRaises(MigrationError):
                self.parse('OK,NUMBER,22,20,0,N\n' + row)

    def test_empty_header_only_overlarge_and_unknown_headers_are_rejected(self):
        for text in ('', ' \n\t', ',,,,,', HEADER, HEADER + ',GUESS\nX,NUMBER,22,10,0,N,Y',
                     'X' * 200_000, 'X' * 1_000_001, '\n'.join(f'X{i},NUMBER,22,10,0,N' for i in range(1001))):
            with self.subTest(size=len(text)), self.assertRaises(MigrationError):
                self.parse(text)


class OracleDualTests(unittest.TestCase):
    def test_qualified_unqualified_and_spaced_dual_are_not_user_dependencies(self):
        code = 'SELECT DUMMY FROM DUAL; SELECT DUMMY FROM SYS.DUAL; SELECT DUMMY FROM SYS . DUAL; v SYS.DUAL.DUMMY%TYPE;'
        self.assertEqual(schema.table_references(code)['tables'], [])
        self.assertEqual([r['table'] for r in schema.table_references('SELECT ID FROM APP.DUAL')['tables']], ['APP.DUAL'])

    def test_old_metadata_and_forms_blocks_do_not_make_dual_pending(self):
        sheet = schema.schema_sheet(Path('.'), codes=[], blocks=[{'name': 'B', 'table': 'SYS.DUAL', 'database': True}],
            bundle=({'tables': {'SYS.DUAL': {}, 'DUAL': {}}}, {}, []))
        self.assertEqual(sheet['tables'], [])
        self.assertEqual(sheet['pending'], [])

    def test_dual_never_gets_a_jdbc_repository(self):
        files, notes = data_models({'blocks': [{'table': 'SYS.DUAL', 'database': True}], 'schema': {}})
        self.assertEqual((files, notes), ({}, []))


if __name__ == '__main__':
    unittest.main()

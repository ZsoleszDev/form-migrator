import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from frm_forms.cli import main
from frm_forms.common import MigrationError, digest, unique_names
from frm_forms.generate import initial_values
from frm_forms.plsql import flatten, parse, Unsupported
from frm_forms.rules import analyze, finalize_capabilities
from frm_forms.xmlmodel import parse_xml

ROOT = Path(__file__).resolve().parents[1]


def plan(path, schema=None, replacements=None):
    model = parse_xml(path)
    analyze(model, schema or {}, replacements or {})
    initial_values(model)
    finalize_capabilities(model)
    return model


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def xml(self, body, form='F'):
        path = self.dir / 'input.xml'
        path.write_text('<Module><FormModule Name="' + form + '">' + body + '</FormModule></Module>', encoding='utf-8')
        return path

    def block(self, trigger='', extra='', item=''):
        return '<Block Name="B" DatabaseDataBlock="true" QueryDataSourceName="T" ' + extra + '><Item Name="ID" DataType="Number" PrimaryKey="true"/>' + item + trigger + '</Block>'

    def test_sample_end_to_end_and_archive(self):
        out = self.dir / 'generated'
        with patch('frm_forms.ollama.opener', side_effect=AssertionError('AI off must not use network')), contextlib.redirect_stdout(io.StringIO()):
            code = main(['migrate', str(ROOT / 'examples/customer_fmb.xml'), '--out', str(out), '--module', 'customer', '--schema', str(ROOT / 'examples/schema.json'), '--zip', '--strict'])
        self.assertEqual(code, 0)
        info = json.loads((out / 'analysis/summary.json').read_text())
        self.assertEqual((info['converted_triggers'], info['review_triggers']), (5, 0))
        self.assertEqual(info['ai']['attempted_calls'], 0)
        java = out / 'backend/DPS'
        repo = (java / 'CustomerServiceImpl.java').read_text()
        self.assertIn('WHERE ID = :id', repo)
        self.assertIn('FOR UPDATE', repo)
        self.assertIn('CUSTOMERS_SEQ.NEXTVAL', repo)
        self.assertNotIn('@SpringBootApplication', '\n'.join(p.read_text() for p in java.glob('*.java')))
        self.assertTrue((out / 'frontend/ugyfelek/component.ts').is_file())
        self.assertFalse((out / 'frontend/package.json').exists())
        with zipfile.ZipFile(str(out) + '.zip') as archive:
            self.assertIsNone(archive.testzip())
            self.assertTrue(all(n.startswith('generated/') and '..' not in n.split('/') for n in archive.namelist()))

    def test_existing_output_is_never_overwritten(self):
        out = self.dir / 'out'
        out.mkdir()
        sentinel = out / 'manual.java'
        sentinel.write_text('manual edit')
        with contextlib.redirect_stderr(io.StringIO()):
            code = main(['migrate', str(ROOT / 'examples/customer_fmb.xml'), '--out', str(out)])
        self.assertEqual(code, 1)
        self.assertEqual(sentinel.read_text(), 'manual edit')

    def test_namespace_external_dtd_no_network_and_cdata(self):
        model = parse_xml(ROOT / 'examples/customer_fmb.xml')
        self.assertEqual(model['name'], 'CUSTOMER_FORM')
        trigger = next(t for t in model['triggers'] if t['item'] == 'NAME')
        self.assertIn('RAISE FORM_TRIGGER_FAILURE', trigger['source'])
        self.assertEqual(model['blocks'][0]['items'][5]['options'][0]['value'], 'NEW')

    def test_properties_as_children_supported(self):
        p = self.xml('<Blocks><Block><Property Name="Name" Value="B"/><Property Name="DatabaseDataBlock" Value="false"/><Items><Item><Property Name="Name" Value="X"/><Property Name="Required" Value="true"/></Item></Items></Block></Blocks>')
        self.assertTrue(parse_xml(p)['blocks'][0]['items'][0]['required'])

    def test_entity_rejected_in_utf16(self):
        p = self.dir / 'entity.xml'
        p.write_bytes('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY x "boom">]><FormModule/>'.encode('utf-16'))
        with self.assertRaises(MigrationError): parse_xml(p)

    def test_no_primary_key_guessing_rowid_like_forms(self):
        # No column is guessed to be the key: like Forms, the ROWID identifies the record.
        p = self.xml('<Block Name="B" QueryDataSourceName="T"><Item Name="ID" DataType="Number"/></Block>')
        model = plan(p, {'blocks': {'B': {'writable': True}}})
        b = model['blocks'][0]
        self.assertTrue(b['can_read']); self.assertTrue(b['can_update']); self.assertTrue(b['can_create'])
        self.assertEqual([i['column'] for i in b['pk']], ['ROWID'])
        self.assertFalse(next(i for i in b['items'] if i['name'] == 'ID')['primary_key'])
        self.assertFalse(any(i['code'] == 'NO_PRIMARY_KEY' for i in model['issues']))

    def test_composite_primary_key(self):
        p = self.xml(self.block(item='<Item Name="TENANT" DataType="Number"/>'))
        model = plan(p, {'blocks': {'B': {'writable': True, 'primary_key': ['TENANT', 'ID']}}})
        self.assertEqual([i['column'] for i in model['blocks'][0]['pk']], ['TENANT', 'ID'])

    def test_partial_translation_is_rejected(self):
        p = self.xml(self.block('<Trigger Name="PRE-UPDATE" TriggerText=":B.ID := 1; UNKNOWN_PROCEDURE();"/>'))
        model = plan(p, {'blocks': {'B': {'writable': True}}})
        self.assertEqual(model['triggers'][0]['status'], 'review')
        self.assertNotIn('java', model['triggers'][0])
        self.assertFalse(model['blocks'][0]['can_update'])

    def test_where_filter_is_never_dropped(self):
        p = self.xml(self.block(extra='WhereClause="TENANT = :GLOBAL.TENANT"'))
        model = plan(p, {'blocks': {'B': {'writable': True}}})
        self.assertFalse(model['blocks'][0]['can_read'])
        self.assertFalse(model['blocks'][0]['can_delete'])

    def test_unknown_button_only_blocks_button(self):
        p = self.xml(self.block(item='<Item Name="BTN" ItemType="Push Button"><Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="COMMIT_FORM;"/></Item>'))
        model = plan(p, {'blocks': {'B': {'writable': True}}})
        # COMMIT_FORM of a button: a screen command (the screen's save chain), never a database COMMIT.
        self.assertEqual(model['triggers'][0]['passthrough']['commands'], ['COMMIT_FORM'])
        self.assertTrue(model['blocks'][0]['can_read'])
        self.assertTrue(model['blocks'][0]['can_create'])

    def test_form_trigger_does_not_block_data(self):
        p = self.xml('<Trigger Name="PRE-FORM" TriggerText="company_auth.check_access;"/>' + self.block())
        model = plan(p, {'blocks': {'B': {'writable': True}}})
        self.assertTrue(model['blocks'][0]['can_read'])

    def test_same_form_and_block_name_does_not_spread_block_issues(self):
        # A Forms built-in never runs in the database (an unknown DB routine now would: Oracle resolves it).
        p = self.xml(self.block('<Trigger Name="PRE-INSERT" TriggerText="GO_ITEM(&apos;B.ID&apos;);"/>') + '<Block Name="SECOND" QueryDataSourceName="T2"><Item Name="ID" DataType="Number" PrimaryKey="true"/></Block>', form='B')
        model = plan(p, {'blocks': {'B': {'writable': True}, 'SECOND': {'writable': True}}})
        self.assertFalse(model['blocks'][0]['can_create'])
        self.assertTrue(model['blocks'][1]['can_create'])

    def test_cross_block_reference_is_not_guessed(self):
        # 4.22: the value is not guessed either - it is a developer input of the generated method (null, TODO)
        p = self.xml(self.block('<Trigger Name="PRE-INSERT" TriggerText=":B.ID := :OTHER.ID;"/>'))
        trigger = plan(p)['triggers'][0]
        self.assertEqual(trigger['status'], 'converted')
        self.assertEqual([(i['source'], i['variable']) for i in trigger['passthrough']['inputs']], [('OTHER.ID', 'otherId')])
        self.assertIn('String otherId = null;', trigger['java'])

    def test_post_query_cannot_corrupt_database_snapshot(self):
        p = self.xml(self.block('<Trigger Name="POST-QUERY" TriggerText=":B.ID := 1;"/>'))
        model = plan(p)
        self.assertFalse(model['blocks'][0]['can_read'])

    def test_replacement_is_exact_and_revalidated(self):
        source = 'company_validate();'
        p = self.xml(self.block(f'<Trigger Name="PRE-INSERT" TriggerText="{source}"/>'))
        rules = {'replacements': {digest(source): {'source': 'NULL;', 'reason': 'Test-only behavior replacement'}}}
        model = plan(p, replacements=rules)
        self.assertEqual(model['triggers'][0]['status'], 'converted')
        rules['replacements'][digest(source)]['source'] = "go_item('B.ID');"  # revalidated: a Forms built-in cannot run
        self.assertEqual(plan(p, replacements=rules)['triggers'][0]['status'], 'review')

    def test_sql_identifier_injection_blocked(self):
        p = self.xml(self.block())
        model = plan(p, {'blocks': {'B': {'table': 'T; DROP TABLE X', 'writable': True}}})
        self.assertFalse(model['blocks'][0]['can_read'])

    def test_dynamic_default_blocks_writes(self):
        p = self.xml(self.block(item='<Item Name="D" DataType="Date" InitialValue="SYSDATE"/>'))
        model = plan(p, {'blocks': {'B': {'writable': True}}})
        self.assertTrue(model['blocks'][0]['can_read'])
        self.assertFalse(model['blocks'][0]['can_create'])

    def test_bad_metadata_fails_early(self):
        p = self.xml(self.block())
        with self.assertRaises(MigrationError): plan(p, {'blocks': {'B': {'primary_key': ['UNKNOWN']}}})
        with self.assertRaises(MigrationError): plan(p, {'blocks': {'B': {'writable': 'false'}}})

    def test_no_empty_trigger_success(self):
        p = self.xml(self.block('<Trigger Name="PRE-INSERT"/>'))
        self.assertEqual(plan(p)['triggers'][0]['status'], 'review')

    def test_strict_mode_still_writes_reviewable_output(self):
        out = self.dir / 'review'
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(['migrate', str(ROOT / 'examples/review_fmb.xml'), '--out', str(out), '--strict'])
        self.assertEqual(code, 3)
        self.assertTrue((out / 'migration-report.md').is_file())

    def test_names_unique_and_safe(self):
        mapped = unique_names(['ORDER_ID', 'orderId', 'CLASS', '123', 'ÁRVÍZ', 'ARVIZ'])
        self.assertEqual(len(set(v.lower() for v in mapped.values())), 6)
        self.assertEqual(mapped['CLASS'], 'classValue')


class ParserTests(unittest.TestCase):
    def test_escaped_string_and_comments(self):
        ast = parse("-- IF THEN\nBEGIN MESSAGE('O''Brien; END IF;'); /* comment */ NULL; END;")
        self.assertEqual(ast[0]['body'][0]['args'][0]['value'], "O'Brien; END IF;")

    def test_precedence_not_binds_less_tightly_than_comparison(self):
        node = parse('IF NOT :B.ID = 1 OR :B.ID = 2 AND :B.ID IS NOT NULL THEN NULL; END IF;')[0]['branches'][0]['condition']
        self.assertEqual(node['operator'], 'OR')
        self.assertEqual(node['left']['operator'], 'NOT')
        self.assertEqual(node['left']['value']['operator'], '=')
        self.assertEqual(node['right']['operator'], 'AND')

    def test_empty_string_becomes_null(self):
        self.assertIsNone(parse(":B.X := '';")[0]['value']['value'])

    def test_declare_sql_and_exception_are_not_partially_parsed(self):
        for source in ['DECLARE x NUMBER; BEGIN NULL; END;', 'SELECT ID INTO :B.ID FROM X;']:
            with self.subTest(source=source), self.assertRaises(Unsupported): parse(source)
        # An EXCEPTION section is kept as data, never dropped: every consumer
        # flattens the body, and flatten refuses an own (non-framework) handler.
        ast = parse('BEGIN NULL; EXCEPTION WHEN OTHERS THEN NULL; END;')
        self.assertEqual(ast[0]['handlers'][0]['names'], ['OTHERS'])
        with self.assertRaises(Unsupported): list(flatten(ast))

    def test_long_comments_and_nested_if(self):
        body = parse('BEGIN IF :B.ID > 1 THEN IF :B.ID < 10 THEN NULL; END IF; ELSIF :B.ID = 0 THEN NULL; ELSE NULL; END IF; END;')
        self.assertEqual(len(body[0]['body'][0]['branches']), 2)


if __name__ == '__main__': unittest.main()

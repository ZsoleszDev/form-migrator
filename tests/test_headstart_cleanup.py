"""Headstart/Designer exports: encoded line breaks, trigger scoping, needless endpoints, spacers."""
import contextlib
import io
import json
import re
from pathlib import Path
import tempfile
import unittest

from frm_forms.cli import main
from frm_forms.generate import initial_values
from frm_forms.plsql import Unsupported, parse
from frm_forms.rules import analyze, finalize_capabilities
from frm_forms.xmlmodel import parse_xml

NL = '&amp;#10;'  # Forms2XML writes a line break inside an attribute like this.


def trig(name, *lines):
    return f'<Trigger Name="{name}" TriggerText="{NL.join(lines)}"/>'


def hs(event, scope='form'):
    return trig(event, f'/* QMS$FORM_{event.replace("-", "_")} */', 'BEGIN', f"  qms$event_{scope}(&apos;{event}&apos;);", 'END;')


def headstart_form(post_query=True, extra_items='', l_ures_2_triggers=''):
    post = trig('POST-QUERY', 'BEGIN', '  :AIT.AIT_MEGJ := UPPER(:AIT.AIT_MEGJ);', 'END;') if post_query else ''
    return f'''<?xml version="1.0" encoding="UTF-8" ?>
<Module version="101020002" xmlns="http://xmlns.oracle.com/Forms">
 <FormModule Name="FRM_ANK_TESZT" Title="Feldolgozatlan adatlapok" FirstNavigationBlock="V_ELEK_ADLAP">
  <Coordinate CoordinateSystem="Real" RealUnit="Pixel"/>
  {hs('PRE-FORM')}{hs('WHEN-NEW-FORM-INSTANCE')}{hs('ON-ERROR')}{hs('ON-MESSAGE')}{hs('KEY-EXIT')}{hs('KEY-CLRFRM')}{hs('POST-FORMS-COMMIT')}
  <Block Name="V_ELEK_ADLAP" DatabaseDataBlock="false" RecordsDisplayCount="1">
   <Item Name="UBI_INPTIP_KOD" ItemType="Text Item" DataType="Char" MaximumLength="10" Prompt="Adatlap típus" CanvasName="P1" XPosition="10" YPosition="10" Width="80" Height="20"/>
   <Item Name="UBI_INPTIP_KOD_NEV" ItemType="Display Item" DataType="Char" MaximumLength="80" Prompt="Megnevezés" CanvasName="P1" XPosition="100" YPosition="10" Width="300" Height="20"/>
   <Item Name="L_URES_1" ItemType="Display Item" DataType="Char" MaximumLength="1" CanvasName="P1" XPosition="10" YPosition="40" Width="390" Height="20" DatabaseItem="false"/>
   <Item Name="DATUM_TOL" ItemType="Text Item" DataType="Date" Prompt="Dátum" CanvasName="P1" XPosition="10" YPosition="70" Width="100" Height="20"/>
   <Item Name="L_URES_2" ItemType="Text Item" DataType="Char" MaximumLength="1" CanvasName="P1" XPosition="120" YPosition="70" Width="60" Height="20" Enabled="false">{l_ures_2_triggers}</Item>
   <Item Name="DATUM_IG" ItemType="Text Item" DataType="Date" Prompt="-" CanvasName="P1" XPosition="190" YPosition="70" Width="100" Height="20"/>
  </Block>
  <Block Name="AIT" DatabaseDataBlock="true" QueryDataSourceType="Table" QueryDataSourceName="ANK_ADLAP_ITEMS" RecordsDisplayCount="15"
         InsertAllowed="false" DeleteAllowed="false" UpdateAllowed="true" QueryAllowed="true"
         WhereClause="AIT_STATUS = &apos;F&apos;{NL}AND AIT_TIPUS = :V_ELEK_ADLAP.UBI_INPTIP_KOD" OrderByClause="AIT_KULCS{NL}DESC">
   <Item Name="AIT_KULCS" ItemType="Text Item" DataType="Number" ColumnName="AIT_KULCS" PrimaryKey="true" Prompt="Kulcs" CanvasName="P1" XPosition="10" YPosition="200" Width="80" Height="20"/>
   <Item Name="AIT_TIPUS" ItemType="Text Item" DataType="Char" MaximumLength="10" ColumnName="AIT_TIPUS" Prompt="Típus" CanvasName="P1" XPosition="100" YPosition="200" Width="80" Height="20"/>
   <Item Name="AIT_STATUS" ItemType="Text Item" DataType="Char" MaximumLength="1" ColumnName="AIT_STATUS" Prompt="Státusz" CanvasName="P1" XPosition="190" YPosition="200" Width="40" Height="20"/>
   <Item Name="AIT_MEGJ" ItemType="Text Item" DataType="Char" MaximumLength="200" ColumnName="AIT_MEGJ" Prompt="Megjegyzés" CanvasName="P1" XPosition="240" YPosition="200" Width="200" Height="20"/>
   <Item Name="L_URES_3" ItemType="Display Item" DataType="Char" CanvasName="P1" XPosition="450" YPosition="200" Width="20" Height="20" DatabaseItem="false"/>
   {extra_items}
   {trig('WHEN-NEW-RECORD-INSTANCE', 'BEGIN', "  qms$event_block(&apos;WHEN-NEW-RECORD-INSTANCE&apos;);", 'END;')}
   {post}
  </Block>
  <Block Name="CALENDAR" DatabaseDataBlock="true" RecordsDisplayCount="1">
   <Item Name="DAY1" ItemType="Text Item" DataType="Char" MaximumLength="2"/>
   {trig('WHEN-NEW-BLOCK-INSTANCE', 'BEGIN', '  qms$calendar.init;', 'END;')}
  </Block>
  <Block Name="QMS$TRANS_ERRORS" DatabaseDataBlock="true" RecordsDisplayCount="10">
   <Item Name="MSG_TEXT" ItemType="Text Item" DataType="Char" MaximumLength="2000"/>
  </Block>
  <Canvas Name="P1" CanvasType="Content" WindowName="WINDOW" Width="700" Height="700"/>
  <Window Name="WINDOW" Title="Adatlapok"/>
 </FormModule>
</Module>
'''


class HeadstartCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def model(self, xml, schema=None):
        path = self.root / 'input.xml'
        path.write_text(xml, encoding='utf-8')
        model = parse_xml(path); analyze(model, schema or {}, {}); initial_values(model); finalize_capabilities(model)
        return model

    def plain(self, body, form='F'):
        return '<Module><FormModule Name="' + form + '">' + body + '</FormModule></Module>'

    def block(self, name='B', extra='', item='', triggers=''):
        return (f'<Block Name="{name}" DatabaseDataBlock="true" QueryDataSourceName="T_{name}" {extra}>'
                '<Item Name="ID" DataType="Number" PrimaryKey="true"/><Item Name="CODE" DataType="Char" MaximumLength="10"/>'
                + item + triggers + '</Block>')

    def generate(self, xml, extra=(), label='out'):
        path = self.root / (label + '.xml'); path.write_text(xml, encoding='utf-8')
        out = self.root / label
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['migrate', str(path), '--screen', '--module', 'teszt', '--out', str(out), *extra]), 0)
        return out

    # 1. Encoded line breaks
    def test_encoded_line_breaks_parse_but_other_references_stay_rejected(self):
        self.assertEqual(parse('BEGIN&#10;  :B.X := 1;&#13;&#10;END;')[0]['body'][0]['target'], 'B.X')
        self.assertEqual(parse('BEGIN&#x0A;NULL;&#x0a;END;')[0]['op'], 'block')
        with self.assertRaises(Unsupported): parse(':B.X := 1 &#38; 2;')

    def test_encoded_where_order_by_and_program_unit_compile(self):
        model = self.model(headstart_form())
        ait = next(b for b in model['blocks'] if b['name'] == 'AIT')
        self.assertEqual(ait['query_plan']['status'], 'compiled')
        self.assertEqual(ait['query_plan']['order_sql'], 'AIT_KULCS DESC')
        unit = ('<ProgramUnit Name="FORMAT_LINE" ProgramUnitType="Procedure" ProgramUnitText="PROCEDURE FORMAT_LINE IS'
                + NL + 'BEGIN' + NL + '  :B.LABEL := UPPER(:B.CODE);' + NL + 'END;"/>')
        xml = self.plain(self.block(item='<Item Name="LABEL" DataType="Char" DatabaseItem="false"/>',
                                    triggers='<Trigger Name="POST-QUERY" TriggerText="FORMAT_LINE;"/>') + unit)
        trigger = self.model(xml)['triggers'][0]
        self.assertEqual(trigger['status'], 'converted')
        self.assertEqual(trigger['inlined_program_units'][0]['name'], 'FORMAT_LINE')

    # 2. Trigger scoping
    def test_framework_and_screen_triggers_do_not_block_endpoints(self):
        model = self.model(headstart_form())
        by_event = {t['event']: t for t in model['triggers'] if not t['block']}
        self.assertTrue(all(t['status'] == 'framework' for t in by_event.values()))
        self.assertFalse([i for i in model['issues'] if i['code'] == 'UNSUPPORTED_TRIGGER' and i['owner'].startswith('@FORM')])
        xml = self.plain('<Trigger Name="KEY-EXIT" TriggerText="exit_form;"/>'
                         '<Trigger Name="WHEN-NEW-FORM-INSTANCE" TriggerText="qms$event_form(\'X\'); go_block(\'B\'); execute_query;"/>'
                         + self.block(triggers='<Trigger Name="WHEN-NEW-RECORD-INSTANCE" TriggerText=":B.CODE := \'X\';"/>'))
        b = self.model(xml, {'blocks': {'B': {'writable': True}}})['blocks'][0]
        self.assertTrue(b['can_read']); self.assertTrue(b['can_create']); self.assertTrue(b['can_update'])
        model = self.model(xml)
        scopes = {i['detail'].split(':')[1]: i['scope'] for i in model['issues'] if i['code'] == 'UNSUPPORTED_TRIGGER'}
        # Screen work stays visible for the frontend, but disables no endpoint; the start-up code is the init endpoint.
        self.assertEqual(scopes, {'KEY-EXIT': 'frontend', 'WHEN-NEW-RECORD-INSTANCE': 'frontend'})
        self.assertEqual(model['init_plan']['passthrough']['commands'], ['GO_BLOCK', 'EXECUTE_QUERY'])

    def test_data_key_override_disables_only_the_screen_control(self):
        # A key trigger runs on the Forms key only: the endpoint stays, the screen drops the control.
        xml = self.plain(self.block(triggers='<Trigger Name="KEY-DELREC" TriggerText="message(\'Nem törölhető\');"/>'))
        model = self.model(xml, {'blocks': {'B': {'writable': True}}})
        b = model['blocks'][0]
        self.assertTrue(b['can_delete']); self.assertTrue(b['can_update']); self.assertTrue(b['can_read'])
        self.assertEqual(b['ui_disabled_operations'], {'delete': 'B:KEY-DELREC'})
        self.assertTrue(any(i['code'] == 'KEY_DISABLES_OPERATION' and i['scope'] == 'review' for i in model['issues']))
        xml = self.plain(self.block(triggers='<Trigger Name="KEY-DELREC" TriggerText="delete_record;"/>'))
        b = self.model(xml, {'blocks': {'B': {'writable': True}}})['blocks'][0]
        self.assertTrue(b['can_delete']); self.assertNotIn('ui_disabled_operations', b)
        xml = self.plain(self.block(triggers='<Trigger Name="KEY-DELREC" TriggerText="ceg_lib.torol;"/>'))
        model = self.model(xml, {'blocks': {'B': {'writable': True}}})
        self.assertNotIn('ui_disabled_operations', model['blocks'][0])
        self.assertTrue(any(i['code'] == 'KEY_TRIGGER_WRAPPER' for i in model['issues']))

    def test_startup_code_with_own_logic_does_not_block_data(self):
        # Start-up code prepares the screen; access to the module is the host's permission.
        xml = self.plain('<Trigger Name="WHEN-NEW-FORM-INSTANCE" TriggerText="qms$event_form(\'X\'); company_auth.check_access;"/>' + self.block())
        self.assertTrue(self.model(xml)['blocks'][0]['can_read'])
        stop = self.plain('<Trigger Name="PRE-FORM" TriggerText="IF NOT company_auth.ok THEN exit_form; END IF;"/>' + self.block())
        model = self.model(stop)
        self.assertTrue(model['blocks'][0]['can_read'])
        self.assertTrue(any(i['code'] == 'STARTUP_ACCESS' and 'EXIT_FORM' in i['detail'] for i in model['issues']))

    def test_runtime_block_property_blocks_the_right_block(self):
        button = ('<Block Name="CTRL" DatabaseDataBlock="false"><Item Name="BTN" ItemType="Push Button">'
                  '<Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="%s"/></Item></Block>')
        literal = self.plain(button % "set_block_property(&apos;B&apos;, DEFAULT_WHERE, &apos;CODE = 1&apos;);"
                             + self.block() + self.block('C'))
        b, c = [x for x in self.model(literal)['blocks'] if x['database']]
        self.assertFalse(b['can_read']); self.assertTrue(c['can_read'])
        self.assertTrue(any('DEFAULT_WHERE' in r for r in b['blockers']['read']))
        self.assertFalse(any('DEFAULT_WHERE' in r for r in b['blockers'].get('update', [])))  # 4.24: queries only
        # 4.24: a button that queries with it is a Java query button; the block's own query keeps the form's WHERE
        query = self.plain(button % "set_block_property(&apos;B&apos;, DEFAULT_WHERE, &apos;CODE = 1&apos;); go_block(&apos;B&apos;); execute_query;"
                           + self.block() + self.block('C'))
        b = [x for x in self.model(query)['blocks'] if x['database']][0]
        self.assertTrue(b['can_read'])
        handle = self.plain(button % ("blk := find_block(&apos;C&apos;);" + NL + "set_block_property(blk, ORDER_BY, &apos;CODE&apos;);")
                            + self.block() + self.block('C'))
        b, c = [x for x in self.model(handle)['blocks'] if x['database']]
        self.assertTrue(b['can_read']); self.assertFalse(c['can_read'])
        unknown = self.plain(button % "set_block_property(:SYSTEM.CURSOR_BLOCK, DEFAULT_WHERE, &apos;1=1&apos;);" + self.block() + self.block('C'))
        self.assertFalse(any(x['can_read'] for x in self.model(unknown)['blocks']))

    def test_runtime_enabled_operation_keeps_a_blocked_endpoint(self):
        xml = self.plain(self.block(extra='InsertAllowed="false"',
                                    triggers='<Trigger Name="WHEN-NEW-BLOCK-INSTANCE" TriggerText="set_block_property(&apos;B&apos;, INSERT_ALLOWED, PROPERTY_TRUE);"/>'))
        b = self.model(xml, {'blocks': {'B': {'writable': True}}})['blocks'][0]
        self.assertTrue(b['endpoint_plan']['create']); self.assertFalse(b['can_create'])
        self.assertTrue(any('INSERT_ALLOWED' in r for r in b['blockers']['create']))
        self.assertTrue(b['can_update'])

    # 3. Needless endpoints
    def test_only_allowed_endpoints_of_real_tables_are_generated(self):
        out = self.generate(headstart_form())
        service = (out / 'backend/DPS/TesztServiceImpl.java').read_text(encoding='utf-8')
        self.assertFalse((out / 'backend/DPS/TesztData.java').exists())
        methods = sorted(re.findall(r'\n    public [^\n(]* (\w+)\(UserDto user', service))
        self.assertEqual(methods, ['commitForm', 'searchAit', 'updateAit'])
        for absent in ['Calendar', 'QmsTransErrors', 'createAit', 'deleteAit', 'listAit', 'insertRow', 'deleteRow', 'selectPage']:
            self.assertNotIn(absent, service)
        self.assertLessEqual(service.count('Tiltás oka'), 4)
        self.assertEqual(service.count('Migrációs váz'), 1)  # module-level reason once, not per method
        self.assertIn('static final boolean MODULE_REVIEWED = false;', service)
        plan = json.loads((out / 'analysis/backend-plan.json').read_text(encoding='utf-8'))
        self.assertEqual({s['block'] for s in plan['skipped_blocks']}, {'CALENDAR', 'QMS$TRANS_ERRORS'})
        self.assertEqual(sorted(s['operation'] for s in plan['skipped_operations'] if s['block'] == 'AIT'), ['create', 'delete', 'list'])
        schema = json.loads((out / 'analysis/backend-schema.example.json').read_text(encoding='utf-8'))
        self.assertEqual(set(schema['blocks']), {'AIT'})

    def test_clean_operation_waits_only_for_the_module_switch(self):
        out = self.generate(headstart_form(post_query=False))
        service = (out / 'backend/DPS/TesztServiceImpl.java').read_text(encoding='utf-8')
        # the one switch in the ServiceImpl: the guard's operations follow MODULE_REVIEWED
        self.assertRegex(' '.join(service.split()), r'MODULE_REVIEWED \? Set\.of\([^)]*"search"[^)]*\) : Set\.of\(\)')
        self.assertIn('a MODULE_REVIEWED kapcsolóval engedélyezhető', service)
        plan = json.loads((out / 'analysis/backend-plan.json').read_text(encoding='utf-8'))
        search = next(e for e in plan['endpoints'] if e.get('operation') == 'search')
        self.assertEqual((search['implemented'], search['ready_after_module_review']), (False, True))

    # 4. Spacers
    def test_catalogued_spacers_become_empty_elements(self):
        out = self.generate(headstart_form())
        source = (out / 'frontend/teszt/teszt.component.ts').read_text(encoding='utf-8')
        self.assertIn("{ type: 'label', ownId: 'V_ELEK_ADLAP.L_URES_1', labelText: '', col: '12' }", source)
        self.assertIn("ownId: 'V_ELEK_ADLAP.L_URES_2', labelText: '', col: '2' }", source)
        self.assertNotIn('lUres', source)  # no form control, validator or table column
        plan = json.loads((out / 'analysis/screen-plan.json').read_text(encoding='utf-8'))
        self.assertEqual([s['owner'] for s in plan['spacers']], ['V_ELEK_ADLAP.L_URES_1', 'V_ELEK_ADLAP.L_URES_2', 'AIT.L_URES_3'])
        self.assertIn('## Térköz-mezők', (out / 'frontend/teszt/MIGRATION_NOTES.md').read_text(encoding='utf-8'))
        config = self.root / 'config.json'; config.write_text(json.dumps({'screen_spacer_type': 'divider'}))
        source = (self.generate(headstart_form(), ['--config', str(config)], 'divider') / 'frontend/teszt/teszt.component.ts').read_text(encoding='utf-8')
        self.assertIn("{ type: 'divider', ownId: 'V_ELEK_ADLAP.L_URES_1'", source)

    def test_spacer_with_behaviour_is_still_empty_space_and_reported(self):
        # The naming convention decides: behaviour (e.g. inherited triggers) is reported, not rendered.
        xml = headstart_form(l_ures_2_triggers=trig('WHEN-VALIDATE-ITEM', 'BEGIN', '  NULL;', 'END;'),
                             extra_items='<Item Name="L_URES_4" ItemType="Text Item" DataType="Char" ColumnName="L_URES_4" '
                                         'CanvasName="P1" XPosition="480" YPosition="200" Width="20" Height="20"/>')
        out = self.generate(xml)
        source = (out / 'frontend/teszt/teszt.component.ts').read_text(encoding='utf-8')
        self.assertIn("{ type: 'label', ownId: 'V_ELEK_ADLAP.L_URES_2', labelText: '', col: '2' }", source)
        self.assertNotIn('lUres', source)
        notices = {n['owner']: n['detail'] for n in json.loads((out / 'analysis/screen-plan.json').read_text(encoding='utf-8'))['notices']
                   if n['code'] == 'SPACER_BEHAVIOUR'}
        self.assertIn('WHEN-VALIDATE-ITEM', notices['V_ELEK_ADLAP.L_URES_2'])
        self.assertIn('adatbázis-mező', notices['AIT.L_URES_4'])

if __name__ == '__main__':
    unittest.main()

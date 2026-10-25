"""Headstart calendar buttons need no endpoint; CALL_FORM buttons become Angular Router navigation."""
from java_support import trigger_mode
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from frm_forms.cli import main

setUpModule, tearDownModule = trigger_mode('plsql')


os.environ.setdefault('FRM_JAVA_IMPORT_MAP', '-')  # tests never read a developer's own java-imports.json
os.environ.setdefault('FRM_JAVA_VARIABLE_MAP', '-')  # nor a developer's own java-variables.json

FORM = '<Module><FormModule Name="XYMODUL" Title="Adatlapok"><Coordinate CoordinateSystem="Real" RealUnit="Pixel"/>\n <Trigger Name="KEY-LISTVAL" TriggerText="/* CGAP$OLES_SEQUENCE_BEFORE */&amp;#10;begin&amp;#10;   qms$calendar.key_listval;&amp;#10;end;"/>\n <Block Name="V_CX_ADLAP" DatabaseDataBlock="false">\n  <Trigger Name="KEY-LISTVAL" TriggerText="/* CGAP$OLES_SEQUENCE_BEFORE */&amp;#10;begin&amp;#10;   qms$calendar.key_listval;&amp;#10;end;"/>\n  <Item Name="UBI_XY_KOD" ItemType="Text Item" DataType="Char" MaximumLength="10" Prompt="Típus" CanvasName="C" XPosition="10" YPosition="10" Width="80" Height="20"/>\n  <Item Name="UBI_ASD_KOD" ItemType="Text Item" DataType="Date" Prompt="Dátum" CanvasName="C" XPosition="100" YPosition="10" Width="100" Height="20">\n   <Trigger Name="KEY-LISTVAL" TriggerText="/* CGAP$OLES_SEQUENCE_BEFORE */&amp;#10;begin&amp;#10;   qms$calendar.key_listval;&amp;#10;end;"/></Item>\n  <Item Name="UBI_ASD_KOD2" ItemType="Push Button" Label="..." CanvasName="C" XPosition="205" YPosition="10" Width="20" Height="20">\n   <Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="/* CGLY$WHEN_BUTTON_PRESSED */&amp;#10;BEGIN&amp;#10;  go_item(\'V_CX_ADLAP.UBI_ASD_KOD\');&amp;#10;  do_key(\'List_values\');&amp;#10;  copy (\'0\', \'GLOBAL.save_mouse_record\');&amp;#10;END;"/></Item>\n </Block>\n <Block Name="AIT" DatabaseDataBlock="true" QueryDataSourceName="ANK_ADLAP_ITEMS" RecordsDisplayCount="5">\n  <Item Name="AIT_KULCS" ItemType="Text Item" DataType="Number" PrimaryKey="true" ColumnName="AIT_KULCS" Prompt="Kulcs" CanvasName="C" XPosition="10" YPosition="60" Width="80" Height="20"/>\n  <Item Name="AIT_TIPUS" ItemType="Text Item" DataType="Char" MaximumLength="10" ColumnName="AIT_TIPUS" Prompt="Típus" CanvasName="C" XPosition="100" YPosition="60" Width="80" Height="20"/>\n  <Item Name="AIT_STATUS" ItemType="Text Item" DataType="Char" MaximumLength="1" ColumnName="AIT_STATUS" Prompt="Státusz" CanvasName="C" XPosition="190" YPosition="60" Width="40" Height="20"/>\n </Block>\n <Block Name="CGNV$W01_1" DatabaseDataBlock="false">\n  <Item Name="PB_LEKERDEZES" ItemType="Push Button" Label="Lekérdezés" CanvasName="C" XPosition="10" YPosition="200" Width="80" Height="22">\n   <Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="/* CGAP$OLES_SEQUENCE_BEFORE */&amp;#10;begin&amp;#10;   qms$event_item(\'WHEN-BUTTON-PRESSED\');&amp;#10;end;&amp;#10;Begin&amp;#10; If :V_CX_ADLAP.UBI_XY_KOD is not null then&amp;#10;    lekerdezesi_feltetelek;&amp;#10; else&amp;#10;    wuzenet(\'Adatlap kiválasztása nem történt meg!\');&amp;#10; end if;&amp;#10;End;"/></Item>\n  <Item Name="PB_RESZLETEK" ItemType="Push Button" Label="Részletek" CanvasName="C" XPosition="100" YPosition="200" Width="80" Height="22">\n   <Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="/* CGAP$OLES_SEQUENCE_BEFORE */&amp;#10;begin&amp;#10;   qms$event_item(\'WHEN-BUTTON-PRESSED\');&amp;#10;end;&amp;#10;BEGIN&amp;#10;rogzitoform_hivasa;&amp;#10;END;"/></Item>\n </Block>\n <ProgramUnit Name="LEKERDEZESI_FELTETELEK" ProgramUnitType="Procedure" ProgramUnitText="PROCEDURE lekerdezesi_feltetelek IS&amp;#10; lek_sql varchar2(1000);&amp;#10;BEGIN&amp;#10; lek_sql := \'AIT_STATUS = ;F;\';&amp;#10; IF :V_CX_ADLAP.UBI_XY_KOD IS NOT NULL THEN&amp;#10;   lek_sql := lek_sql || \' AND AIT_TIPUS = :V_CX_ADLAP.UBI_XY_KOD\';&amp;#10; END IF;&amp;#10; SELECT REPLACE(lek_sql, \';\', CHR(39)) INTO lek_sql FROM DUAL;&amp;#10; SET_BLOCK_PROPERTY(\'AIT\', DEFAULT_WHERE, lek_sql);&amp;#10; GO_BLOCK(\'AIT\');&amp;#10; EXECUTE_QUERY();&amp;#10;END;"/>\n <ProgramUnit Name="ROGZITOFORM_HIVASA" ProgramUnitType="Procedure" ProgramUnitText="PROCEDURE rogzitoform_hivasa IS&amp;#10;  pl     PARAMLIST;&amp;#10;  v_kod  varchar2(50);&amp;#10;BEGIN&amp;#10;  v_kod := :V_CX_ADLAP.UBI_XY_KOD;&amp;#10;  pl := GET_PARAMETER_LIST(\'ROGZITO_PARAMS\');&amp;#10;  IF NOT ID_NULL(pl) THEN&amp;#10;    DESTROY_PARAMETER_LIST(pl);&amp;#10;  END IF;&amp;#10;  pl := CREATE_PARAMETER_LIST(\'ROGZITO_PARAMS\');&amp;#10;  ADD_PARAMETER(pl, \'P_KOD\', TEXT_PARAMETER, v_kod);&amp;#10;  CALL_FORM(\'ROGZITO\', NO_HIDE, DO_REPLACE, NO_QUERY_ONLY, pl);&amp;#10;END;"/>\n <Canvas Name="C" CanvasType="Content" WindowName="W"/><Window Name="W" Title="Adatlapok"/>\n</FormModule></Module>\n'

REAL = '<Module><FormModule Name="XYMODUL" Title="Adatlapok"><Coordinate CoordinateSystem="Real" RealUnit="Pixel"/>\n <Trigger Name="KEY-LISTVAL" TriggerText="/* CGAP$OLES_SEQUENCE_BEFORE */&amp;#10;begin&amp;#10;   qms$calendar.key_listval;&amp;#10;end;"/>\n <Block Name="V_CX_ADLAP" DatabaseDataBlock="false">\n  <Trigger Name="KEY-LISTVAL" TriggerText="/* CGAP$OLES_SEQUENCE_BEFORE */&amp;#10;begin&amp;#10;   qms$calendar.key_listval;&amp;#10;end;"/>\n  <Item Name="UBI_XY_KOD" ItemType="Text Item" DataType="Char" MaximumLength="10" Prompt="Típus" CanvasName="C" XPosition="10" YPosition="10" Width="80" Height="20"/>\n  <Item Name="UBI_ASD_KOD" ItemType="Text Item" DataType="Date" Prompt="Dátum" CanvasName="C" XPosition="100" YPosition="10" Width="100" Height="20">\n   <Trigger Name="KEY-LISTVAL" TriggerText="/* CGAP$OLES_SEQUENCE_BEFORE */&amp;#10;begin&amp;#10;   qms$calendar.key_listval;&amp;#10;end;"/></Item>\n  <Item Name="UBI_ASD_KOD2" ItemType="Push Button" Label="..." CanvasName="C" XPosition="205" YPosition="10" Width="20" Height="20">\n   <Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="/* CGLY$WHEN_BUTTON_PRESSED */&amp;#10;BEGIN&amp;#10;  go_item(\'V_CX_ADLAP.UBI_ASD_KOD\');&amp;#10;  do_key(\'List_values\');&amp;#10;  copy (\'0\', \'GLOBAL.save_mouse_record\');&amp;#10;END;"/></Item>\n </Block>\n <Block Name="AIT" DatabaseDataBlock="true" QueryDataSourceName="ANK_ADLAP_ITEMS" RecordsDisplayCount="5">\n  <Item Name="AIT_KULCS" ItemType="Text Item" DataType="Number" PrimaryKey="true" ColumnName="AIT_KULCS" Prompt="Kulcs" CanvasName="C" XPosition="10" YPosition="60" Width="80" Height="20"/>\n  <Item Name="AIT_TIPUS" ItemType="Text Item" DataType="Char" MaximumLength="10" ColumnName="AIT_TIPUS" Prompt="Típus" CanvasName="C" XPosition="100" YPosition="60" Width="80" Height="20"/>\n  <Item Name="AIT_STATUS" ItemType="Text Item" DataType="Char" MaximumLength="1" ColumnName="AIT_STATUS" Prompt="Státusz" CanvasName="C" XPosition="190" YPosition="60" Width="40" Height="20"/>\n </Block>\n <Block Name="CGNV$W01_1" DatabaseDataBlock="false">\n  <Item Name="PB_LEKERDEZES" ItemType="Push Button" Label="Lekérdezés" CanvasName="C" XPosition="10" YPosition="200" Width="80" Height="22">\n   <Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="/* CGAP$OLES_SEQUENCE_BEFORE */&amp;#10;begin&amp;#10;   qms$event_item(\'WHEN-BUTTON-PRESSED\');&amp;#10;end;&amp;#10;Begin&amp;#10; If :V_CX_ADLAP.UBI_XY_KOD is not null then&amp;#10;    lekerdezesi_feltetelek;&amp;#10; else&amp;#10;    wuzenet(\'Adatlap kiválasztása nem történt meg!\');&amp;#10; end if;&amp;#10;End;"/></Item>\n  <Item Name="PB_RESZLETEK" ItemType="Push Button" Label="Részletek" CanvasName="C" XPosition="100" YPosition="200" Width="80" Height="22">\n   <Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="/* CGAP$OLES_SEQUENCE_BEFORE */&amp;#10;begin&amp;#10;   qms$event_item(\'WHEN-BUTTON-PRESSED\');&amp;#10;end;&amp;#10;BEGIN&amp;#10;rogzitoform_hivasa;&amp;#10;END;"/></Item>\n </Block>\n <ProgramUnit Name="LEKERDEZESI_FELTETELEK" ProgramUnitType="Procedure" ProgramUnitText="PROCEDURE lekerdezesi_feltetelek IS&amp;#10; lek_sql varchar2(1000);&amp;#10;BEGIN&amp;#10; lek_sql := \'AIT_STATUS = ;F;\';&amp;#10; IF :V_CX_ADLAP.UBI_XY_KOD IS NOT NULL THEN&amp;#10;   lek_sql := lek_sql || \' AND AIT_TIPUS = :V_CX_ADLAP.UBI_XY_KOD\';&amp;#10; END IF;&amp;#10; SELECT REPLACE(lek_sql, \';\', CHR(39)) INTO lek_sql FROM DUAL;&amp;#10; SET_BLOCK_PROPERTY(\'AIT\', DEFAULT_WHERE, lek_sql);&amp;#10; GO_BLOCK(\'AIT\');&amp;#10; EXECUTE_QUERY();&amp;#10;END;"/>\n <ProgramUnit Name="ROGZITOFORM_HIVASA" ProgramUnitType="Procedure" ProgramUnitText="PROCEDURE rogzitoform_hivasa IS&amp;#10;  pl PARAMLIST;&amp;#10;  v_form varchar2(50);&amp;#10;BEGIN&amp;#10;  IF :V_CX_ADLAP.UBI_XY_KOD = \'A\' THEN&amp;#10;    v_form := \'ROGZITO_A\';&amp;#10;  ELSE&amp;#10;    v_form := \'ROGZITO_B\';&amp;#10;  END IF;&amp;#10;  pl := CREATE_PARAMETER_LIST(\'P\');&amp;#10;  ADD_PARAMETER(pl, \'P_KULCS\', TEXT_PARAMETER, TO_CHAR(:AIT.AIT_KULCS));&amp;#10;  CALL_FORM(v_form, NO_HIDE, DO_REPLACE, NO_QUERY_ONLY, pl);&amp;#10;  DESTROY_PARAMETER_LIST(pl);&amp;#10;END;"/>\n<ProgramUnit Name="WUZENET" ProgramUnitType="Procedure" ProgramUnitText="PROCEDURE wuzenet(p_szoveg IN VARCHAR2) IS&amp;#10;  al NUMBER;&amp;#10;BEGIN&amp;#10;  set_alert_property(\'UZENET\', ALERT_MESSAGE_TEXT, p_szoveg);&amp;#10;  al := show_alert(\'UZENET\');&amp;#10;END;"/>\n <Canvas Name="C" CanvasType="Content" WindowName="W"/><Window Name="W" Title="Adatlapok"/>\n</FormModule></Module>\n'


class FormCallsAndOpenersTests(unittest.TestCase):
    def generate(self, form=FORM, **config):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / 'xymodul_fmb.xml').write_text(form, encoding='utf-8')
        (root / 'config.json').write_text(json.dumps({'backend_live': True, **config}), encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['migrate', str(root / 'xymodul_fmb.xml'), '--screen', '--module', 'xymodul',
                                   '--config', str(root / 'config.json'), '--out', str(root / 'out')]), 0)
        out = self.out = root / 'out'
        plan = json.loads((out / 'analysis/backend-plan.json').read_text(encoding='utf-8'))
        screen = json.loads((out / 'analysis/screen-plan.json').read_text(encoding='utf-8'))
        component = (out / 'frontend/xymodul/xymodul.component.ts').read_text(encoding='utf-8')
        return plan, screen, component

    def test_calendar_button_with_key_listval_on_every_level_is_folded_without_endpoint(self):
        plan, screen, _ = self.generate()
        owners = [e['owner'] for e in plan['endpoints'] if e.get('operation') == 'action']
        self.assertNotIn('V_CX_ADLAP.UBI_ASD_KOD2', owners)
        buttons = [i['owner'] for s in screen['sections'] for i in s['items'] if i['widget'] == 'button']
        self.assertNotIn('V_CX_ADLAP.UBI_ASD_KOD2', buttons)  # the date field's own picker opens the calendar
        self.assertIn('CGNV$W01_1.PB_LEKERDEZES', owners)  # the DEFAULT_WHERE query button keeps its endpoint

    def test_call_form_button_navigates_with_its_parameters_and_has_no_endpoint(self):
        plan, screen, component = self.generate()
        self.assertNotIn('CGNV$W01_1.PB_RESZLETEK', [e['owner'] for e in plan['endpoints'] if e.get('operation') == 'action'])
        self.assertEqual(screen['navigations']['CGNV$W01_1.PB_RESZLETEK'],
                         {'form': 'ROGZITO', 'call': 'CALL_FORM', 'route': '/rogzito',
                          'params': [{'name': 'P_KOD', 'block': 'V_CX_ADLAP', 'key': 'ubiXyKod'}]})
        # the button's own method navigates with ServiceBase's router, the parameter from the screen (4.27: as it is)
        self.assertIn('extends ServiceBase', component)
        self.assertIn("  // CGNV$W01_1.PB_RESZLETEK: Forms CALL_FORM('ROGZITO'): navigáció.\n  protected onPbReszletekClick(): void {\n"
                      "    const vCxAdlap = this.forms['vCxAdlap']?.getRawValue() ?? {};\n"
                      "    void this.router.navigate(['/rogzito'], { queryParams: { P_KOD: vCxAdlap.ubiXyKod } });\n  }",
                      component)
        _, screen, _ = self.generate(form_routes={'rogzito': '/pages/modules/rogzito'})
        self.assertEqual(screen['navigations']['CGNV$W01_1.PB_RESZLETEK']['route'], '/pages/modules/rogzito')

    def test_a_form_call_with_its_own_logic_is_a_todo_without_endpoint(self):
        # The target form chosen by a code (and a SELECT): the developer writes the navigation (4.27: TODO in the button).
        form = REAL.replace("v_form := ;ROGZITO_A;;", "SELECT max(f) INTO v_form FROM rogzito_t;".replace(';', ';'))
        for variant in (REAL, FORM.replace("v_kod := :V_CX_ADLAP.UBI_XY_KOD;", "SELECT max(kod) INTO v_kod FROM rogzito_t;")):
            plan, screen, component = self.generate(variant)
            self.assertNotIn('CGNV$W01_1.PB_RESZLETEK', [e['owner'] for e in plan['endpoints'] if e.get('operation') == 'action'])
            self.assertEqual(screen['manual_navigations'], ['CGNV$W01_1.PB_RESZLETEK'])
            method = component[component.index('  protected onPbReszletekClick(): void {'):]
            method = method[:method.index('\n  }\n') + 4]
            self.assertIn("    // TODO: összetett formhívás: a célt és a paramétereket kézzel kell megírni, például "
                          "void this.router.navigate(['/<útvonal>'], { queryParams: { ... } });\n", method)
            self.assertIn('    //#region Eredeti Forms-kód\n', method); self.assertIn('    //#endregion\n', method)
            self.assertIn('// PROCEDURE rogzitoform_hivasa IS', method)
            self.assertNotIn('navigate' + 'CgnvW011PbReszletek', component)
        self.assertEqual(form, form)

    def test_a_form_call_that_also_writes_data_stays_a_backend_task(self):
        form = REAL.replace("  pl := CREATE_PARAMETER_LIST(", "  INSERT INTO naplo(kod) VALUES (:V_CX_ADLAP.UBI_XY_KOD);&amp;#10;  pl := CREATE_PARAMETER_LIST(")
        plan, screen, _ = self.generate(form)
        self.assertEqual([e.get('runs') for e in plan['endpoints'] if e.get('owner') == 'CGNV$W01_1.PB_RESZLETEK'], ['manual'])
        self.assertNotIn('CGNV$W01_1.PB_RESZLETEK', screen.get('manual_navigations') or {})

    def test_query_button_with_a_local_message_procedure_is_recognised(self):
        runs = lambda plan: [e.get('runs') for e in plan['endpoints'] if e.get('owner') == 'CGNV$W01_1.PB_LEKERDEZES']
        plan, _, _ = self.generate(REAL)
        self.assertEqual(runs(plan), ['java-query'])  # 4.23: the SQL request only, in Java
        logging = REAL.replace("  al := show_alert(", "  INSERT INTO naplo(szoveg) VALUES (p_szoveg);&amp;#10;  al := show_alert(")
        plan, _, _ = self.generate(logging)  # WUZENET is not interpreted: a message, and a TODO for its own logic
        self.assertEqual(runs(plan), ['java-query'])
        service = (self.out / 'backend/DPS/XymodulServiceImpl.java').read_text(encoding='utf-8')
        self.assertIn('TODO: a(z) WUZENET itt csak üzenet, de a Formsban mást is csinál (az eredeti kódja a regionban).', service)
        self.assertIn('INSERT INTO naplo(szoveg) VALUES (p_szoveg);', service)  # in the region
        # the PL/SQL query adapter (query_action_mode: plsql) keeps the strict reading
        plan, _, _ = self.generate(REAL, query_action_mode='plsql')
        self.assertEqual(runs(plan), ['plsql-query'])
        plan, _, _ = self.generate(logging, query_action_mode='plsql')
        self.assertEqual(runs(plan), ['manual'])

if __name__ == '__main__':
    unittest.main()

"""A button that is not ported yet keeps its Forms code, as comments, where the developer ports it."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from frm_forms.cli import main

os.environ.setdefault('FRM_JAVA_IMPORT_MAP', '-')  # tests never read a developer's own java-imports.json
os.environ.setdefault('FRM_JAVA_VARIABLE_MAP', '-')  # nor a developer's own java-variables.json

FORM = '<Module><FormModule Name="KEZI" Title="Kézi gombok"><Coordinate CoordinateSystem="Real" RealUnit="Pixel"/>\n <Block Name="V_ELEK_ASD" DatabaseDataBlock="false">\n  <Item Name="UBI_ASD_KOD" ItemType="Text Item" DataType="Char" MaximumLength="10" Prompt="Kód" CanvasName="C" XPosition="10" YPosition="10" Width="80" Height="20">\n   <Trigger Name="KEY-LISTVAL" TriggerText="IF :V_ELEK_ASD.UBI_ASD_KOD IS NULL THEN&amp;#10;  set_item_property(\'V_ELEK_ASD.UBI_ASD_KOD\', REQUIRED, PROPERTY_TRUE);&amp;#10;END IF;&amp;#10;list_values;"/></Item>\n  <Item Name="UBI_ASD_KOD2" ItemType="Push Button" Label="..." CanvasName="C" XPosition="100" YPosition="10" Width="20" Height="20">\n   <Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="go_item(\'V_ELEK_ASD.UBI_ASD_KOD\');&amp;#10;do_key(\'LIST_VALUES\');"/></Item>\n </Block>\n <Block Name="CGNV$W01_1" DatabaseDataBlock="false">\n  <Item Name="PB_LEKERDEZES" ItemType="Push Button" Label="Lekérdezés" CanvasName="C" XPosition="10" YPosition="40" Width="80" Height="22">\n   <Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="IF get_item_property(\'V_ELEK_ASD.UBI_ASD_KOD\', ENABLED) = \'TRUE\' THEN&amp;#10;  lekerdezesi_feltetelek;&amp;#10;END IF;"/></Item>\n  <Item Name="PB_RESZLETEK" ItemType="Push Button" Label="Részletek" CanvasName="C" XPosition="100" YPosition="40" Width="80" Height="22">\n   <Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="rogzitoform_hivasa(:V_ELEK_ASD.UBI_ASD_KOD);"/></Item>\n </Block>\n <ProgramUnit Name="LEKERDEZESI_FELTETELEK" ProgramUnitType="Procedure" ProgramUnitText="PROCEDURE lekerdezesi_feltetelek IS&amp;#10;  lek_sql VARCHAR2(2000) := \'1=1\';&amp;#10;BEGIN&amp;#10;  IF :V_ELEK_ASD.UBI_ASD_KOD IS NOT NULL THEN&amp;#10;    lek_sql := lek_sql || \' AND kod = \' || :V_ELEK_ASD.UBI_ASD_KOD;&amp;#10;  END IF;&amp;#10;  set_block_property(\'V_ELEK_ASD\', DEFAULT_WHERE, lek_sql);&amp;#10;  go_block(\'V_ELEK_ASD\');&amp;#10;  execute_query;&amp;#10;END;"/>\n <ProgramUnit Name="ROGZITOFORM_HIVASA" ProgramUnitType="Procedure" ProgramUnitText="PROCEDURE rogzitoform_hivasa(p_kod VARCHAR2) IS&amp;#10;  pl PARAMLIST;&amp;#10;BEGIN&amp;#10;  pl := create_parameter_list(\'P\');&amp;#10;  add_parameter(pl, \'KOD\', TEXT_PARAMETER, p_kod);&amp;#10;  call_form(\'ROGZITO\', NO_HIDE, DO_REPLACE, NO_QUERY_ONLY, pl);&amp;#10;  destroy_parameter_list(pl);&amp;#10;END;"/>\n <Canvas Name="C" CanvasType="Content" WindowName="W"/><Window Name="W" Title="Kézi"/>\n</FormModule></Module>\n'


class ManualActionSourceTests(unittest.TestCase):
    def test_trigger_local_units_and_do_key_target_are_commented_in_the_method(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'kezi_fmb.xml').write_text(FORM, encoding='utf-8')
            (root / 'config.json').write_text(json.dumps({'backend_live': True}), encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['migrate', str(root / 'kezi_fmb.xml'), '--screen', '--module', 'kezi',
                                       '--config', str(root / 'config.json'), '--out', str(root / 'out')]), 0)
            service = (root / 'out/backend/DPS/KeziServiceImpl.java').read_text(encoding='utf-8')
        methods = {m.split('(')[0]: m for m in service.split('public ActionResult ')[1:]}
        lov = methods['onVElekAsdUbiAsdKod2']
        self.assertIn("// do_key('LIST_VALUES');", lov)
        self.assertIn('DO_KEY által indított key-trigger (jelölt)', lov)
        self.assertIn('//   set_item_property(', lov)  # the KEY-LISTVAL business logic
        self.assertIn('// PROCEDURE lekerdezesi_feltetelek IS', methods['onCgnvW011PbLekerdezes'])
        self.assertNotIn('onCgnvW011PbReszletek', methods)  # a form call is Angular navigation, not a backend endpoint
        for method in methods.values():
            if 'Eredeti Forms-kód kiindulásnak' in method:  # still clearly not implemented
                self.assertLess(method.index('Eredeti Forms-kód'), method.index('HttpStatus.NOT_IMPLEMENTED'))


if __name__ == '__main__':
    unittest.main()

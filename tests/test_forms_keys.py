"""DO_KEY fallback must preserve the form's key overrides and list opener logic."""
import java_support  # noqa: F401  (legacy trigger mode for these engine tests)
import json
import unittest
import xml.etree.ElementTree as ET

from frm_forms.forms_keys import screen_context
from frm_forms.framework import action_steps, load
import test_forms_runtime as runtime


class FormsKeyTests(unittest.TestCase):
    setUp = runtime.FormsRuntimeTests.setUp
    generate = runtime.FormsRuntimeTests.generate
    read = runtime.FormsRuntimeTests.read

    def form(self, key=None, level='item', routine=None, calendar=False):
        root = ET.fromstring(runtime.fixture(buttons={'LOOKUP': "go_item('B.NAME'); do_key('LIST_VALUES');"}, startup=''))
        block = next(b for b in root.findall('Block') if b.get('Name') == 'B')
        name = next(i for i in block.findall('Item') if i.get('Name') == 'NAME')
        name.set('LOVName', 'NAMES')
        if calendar:
            name.set('DataType', 'Date')
        ET.SubElement(root, 'LOV', Name='NAMES', RecordGroupName='NAMES_RG')
        ET.SubElement(root, 'RecordGroup', Name='NAMES_RG', RecordGroupQuery='SELECT NAME FROM T_B')
        if key is not None:
            owner = {'form': root, 'block': block, 'item': name}[level]
            ET.SubElement(owner, 'Trigger', Name='KEY-LISTVAL', TriggerText=key)
        if routine:
            ET.SubElement(root, 'ProgramUnit', Name='DO_KEY', ProgramUnitType='Procedure', ProgramUnitText=routine)
        return root

    def test_unoverridden_list_key_is_frontend_and_the_button_folds_into_its_lov(self):
        root = self.form()
        out = self.generate(ET.tostring(root))
        plan = self.read(out, 'backend-plan.json')
        self.assertFalse([e for e in plan['endpoints'] if e['operation'] == 'action'])
        self.assertEqual(next(a for a in plan['skipped_actions'] if a['owner'] == 'B.LOOKUP')['category'], 'frontend')
        screen = self.read(out, 'screen-plan.json')
        self.assertTrue(any(b['owner'] == 'B.LOOKUP' for b in screen['folded_buttons']))

    def test_form_block_and_item_key_logic_cannot_be_discarded_as_a_list_opener(self):
        for level in ('form', 'block', 'item'):
            with self.subTest(level=level):
                root = self.form("BEGIN audit_pkg.opened(:B.ID); list_values; END;", level=level)
                out = self.generate(ET.tostring(root), label=level)
                plan = self.read(out, 'backend-plan.json')
                action = next(e for e in plan['endpoints'] if e.get('owner') == 'B.LOOKUP')
                self.assertFalse(action['implemented'])
                self.assertIn("DO_KEY('LIST_VALUES')", action['adapter_diagnostics']['frontend'])
                self.assertIn('KEY-LISTVAL', action['adapter_diagnostics']['frontend'])
                screen = self.read(out, 'screen-plan.json')
                self.assertFalse(screen['folded_buttons'])
                button = next(a for a in screen['actions'] if a['owner'] == 'B.LOOKUP')
                self.assertIsNone(button['steps'])
                self.assertTrue(any(n['code'] == 'LIST_BUTTON_KEPT' and 'KEY-LISTVAL' in n['detail'] for n in screen['notices']))

    def test_unknown_current_item_checks_all_keys_in_the_selected_block(self):
        context = screen_context(self.form('NULL;'))
        catalog = load({})
        self.assertIsNone(action_steps("go_block('B'); do_key('LIST_VALUES');", catalog, context, 'OTHER', 'X'))
        self.assertIsNotNone(action_steps("go_item('B.D'); do_key('LIST_VALUES');", catalog, context, 'OTHER', 'X'))
        self.assertIsNone(action_steps("go_item('B.D'); next_item; do_key('LIST_VALUES');", catalog, context))
        self.assertIsNone(action_steps("do_key('LIST_VALUES');", catalog, context))

    def test_local_do_key_program_unit_is_not_treated_as_a_builtin(self):
        root = self.form(routine='PROCEDURE do_key(p VARCHAR2) IS BEGIN INSERT INTO audit_log VALUES(p); END;')
        context = screen_context(root)
        self.assertIsNone(action_steps("go_item('B.NAME'); do_key('LIST_VALUES');", load({}), context, 'B', 'LOOKUP'))
        out = self.generate(ET.tostring(root))
        screen = self.read(out, 'screen-plan.json')
        self.assertFalse(screen['folded_buttons'])
        self.assertTrue(any('helyi programegység' in n['detail'] for n in screen['notices'] if n['code'] == 'LIST_BUTTON_KEPT'))

    def test_known_calendar_key_keeps_native_picker_and_no_backend_action(self):
        root = self.form('qms$calendar.key_listval;', calendar=True)
        out = self.generate(ET.tostring(root))
        plan = self.read(out, 'backend-plan.json')
        self.assertFalse([e for e in plan['endpoints'] if e['operation'] == 'action'])
        screen = self.read(out, 'screen-plan.json')
        self.assertTrue(any(b['owner'] == 'B.LOOKUP' for b in screen['folded_buttons']))
        target = next(i for s in screen['sections'] for i in s['items'] if i['owner'] == 'B.NAME')
        self.assertEqual(target['widget'], 'date')

    def test_discovery_uses_real_key_names_and_keeps_event_sources_as_candidates(self):
        root = self.form('log_lookup; list_values;')
        ET.SubElement(root, 'ProgramUnit', Name='LOG_LOOKUP', ProgramUnitType='Procedure',
                      ProgramUnitText='PROCEDURE log_lookup IS BEGIN INSERT INTO audit_log VALUES (:B.ID); END;')
        out = self.generate(ET.tostring(root))
        mapped = json.loads((out / 'analysis/discovery/form-map.json').read_text())
        codes = {c['id']: c for c in mapped['code']}
        button = next(a for a in mapped['actions'] if a['owner'] == 'B.LOOKUP')
        calls = codes[button['trigger_id']]['calls']
        key = next(c for c in calls if c['name'] == 'DO_KEY')
        self.assertEqual([codes[c]['name'] for c in key['event_target_candidates']], ['KEY-LISTVAL'])
        candidates = {codes[c]['name'] for c in button['event_code_candidates']}
        self.assertEqual(candidates, {'KEY-LISTVAL', 'LOG_LOOKUP'})
        self.assertNotIn('LOG_LOOKUP', {codes[c]['name'] for c in button['reachable_code']})
        evidence = (out / 'analysis/backend-evidence.md').read_text()
        self.assertIn('KEY/EVENT SOURCE CANDIDATE', evidence)
        self.assertIn('INSERT INTO audit_log', evidence)


if __name__ == '__main__':
    unittest.main()

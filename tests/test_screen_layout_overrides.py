import contextlib
import copy
import io
import json
from pathlib import Path
import unittest
from xml.etree import ElementTree as ET

from frm_forms.cli import main
from frm_forms.common import MigrationError
from frm_forms.screen_layout import spans
from frm_forms.screen_overrides import fingerprint, validate
import test_screen

ROOT = test_screen.ROOT


class LayoutOverrideTests(unittest.TestCase):
    setUp = test_screen.ScreenTests.setUp
    generate = test_screen.ScreenTests.generate

    def sample(self, rules=None, label='out', **config):
        if rules is not None: config['screen_overrides'] = rules
        return self.generate(ROOT / 'examples/screen-layout_fmb.xml', config, label=label)

    def rules(self):
        out, _, _ = self.sample(label='base')
        return json.loads((out / 'analysis/screen-overrides.template.json').read_text())

    def activate(self, rules, owner, **edits):
        rules['items'][owner].update(reason='Fejlesztő által ellenőrizve.', set=edits)
        return rules

    def rejected(self, rules, code, xml=None, **config):
        source = ROOT / 'examples/screen-layout_fmb.xml'
        if xml is not None:
            source = self.root / 'changed.xml'; source.write_text(xml, encoding='utf-8')
        path = self.root / 'rules.json'; path.write_text(json.dumps(rules))
        profile = self.root / 'profile.json'; profile.write_text(json.dumps(config))
        out = self.root / 'rejected'
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            result = main(['migrate', str(source), '--screen', '--frontend-only', '--out', str(out),
                           '--screen-overrides', str(path), '--config', str(profile)])
        self.assertEqual(result, 1, err.getvalue())
        self.assertIn(code, err.getvalue()); self.assertFalse(out.exists())

    def test_nearby_fields_gap_and_lov_relationship(self):
        # Source whitespace becomes colBefore only on request; by default fields fill the row.
        _, p, code = self.sample(label='gaps', screen_preserve_gaps=True)
        inner = next(s for s in p['sections'] if s['frame'] == 'INNER')
        self.assertEqual([i['row'] for i in inner['items']], [0, 0, 0, 1])
        self.assertEqual(inner['items'][2]['col_before'], 1)
        for row in {i['row'] for i in inner['items']}:
            self.assertEqual(sum(i['col'] + i['col_before'] + i['col_after'] for i in inner['items'] if i['row'] == row), 12)
        self.assertIn("colBefore: '1'", code)
        self.assertEqual(p['lookup_groups'][0]['buttons'], ['FIELDS.LOOKUP'])
        self.assertEqual(p['lookup_groups'][0]['returns'], ['FIELDS.NAME'])
        _, p, _ = self.sample(label='exact', screen_row_tolerance=0)
        inner = next(s for s in p['sections'] if s['frame'] == 'INNER')
        self.assertEqual([i['row'] for i in inner['items']], [0, 1, 2, 3])
        _, p, _ = self.sample(label='no-gap')
        self.assertFalse(any(i['col_before'] for s in p['sections'] for i in s['items']))

    def test_rows_do_not_chain_merge_overlap_or_use_placeholder_height(self):
        def field(name, x, y, h=20):
            return dict(owner=name, x=x, y=y, width=30, height=h, order=x, widget='text', positioned=True, layout_override={})
        items = spans([field('A', 0, 0), field('B', 50, 4), field('C', 100, 8), field('D', 0, 30)], 12)
        self.assertEqual({i['owner']: i['row'] for i in items}, {'A': 0, 'B': 0, 'C': 1, 'D': 2})
        for pair in [[field('A', 0, 0), field('B', 20, 2)], [field('A', 0, 0, 1), field('B', 50, .1, 1)]]:
            self.assertEqual([i['row'] for i in spans(pair, 12)], [0, 1])

    def test_nested_frames_and_full_containment(self):
        _, p, code = self.sample()
        frames = {g['name']: g for g in p['graphics']}
        self.assertEqual(frames['INNER']['parent'], 'OUTER')
        self.assertEqual(frames['HELP']['status'], 'rendered')
        self.assertEqual(next(s for s in p['sections'] if any(i['owner'] == 'FIELDS.EDGE' for i in s['items']))['frame'], '')
        template = code.split('template: `', 1)[1].split('`,', 1)[0]
        outer = template.index('<p-fieldset'); inner = template.index('<p-fieldset', outer + 1)
        inner_end = template.index('</p-fieldset>', inner)
        outer_end = template.index('</p-fieldset>', inner_end + 1)
        self.assertLess(outer, inner); self.assertLess(inner_end, outer_end)
        for owner in ['CODE', 'LOOKUP', 'NAME', 'ACTIVE', 'NOTE', 'EDGE']:
            self.assertEqual(code.count("ownId: 'FIELDS." + owner + "'"), 1)

    def test_reviewed_widget_is_in_model_notes_and_applied_snapshot(self):
        rules = self.activate(self.rules(), 'FIELDS.ACTIVE', widget='checkbox', label='Aktív állapot')
        out, p, code = self.sample(rules, label='reviewed', screen_infer_widgets=False)
        item = next(i for s in p['sections'] for i in s['items'] if i['owner'] == 'FIELDS.ACTIVE')
        self.assertEqual((item['widget'], item['type_source']), ('checkbox', 'override'))
        ui = json.loads((out / 'analysis/ui-model.json').read_text())
        i = next(i for b in ui['blocks'] for i in b['items'] if i['owner'] == 'FIELDS.ACTIVE')
        self.assertEqual(i['type_source'], 'override'); self.assertIn('ellenőrizve', i['inference_reason'])
        self.assertTrue(i['validation']['required']); self.assertIn("'FIELDS.ACTIVE': [FrmValidators.checked]", code)
        self.assertIn('Aktív állapot', code)
        notes = (out / 'frontend/testScreen/MIGRATION_NOTES.md').read_text()
        self.assertIn('Fejlesztő által ellenőrizve.', notes)
        self.assertEqual(json.loads((out / 'analysis/screen-overrides.applied.json').read_text()), rules)
        strict = json.loads((out / 'analysis/ui-model-strict.json').read_text())
        i = next(i for b in strict['blocks'] for i in b['items'] if i['owner'] == 'FIELDS.ACTIVE')
        self.assertEqual(i['widget'], 'text'); self.assertEqual(i['type_source'], 'explicit')

    def test_manual_group_row_order_and_spans_are_applied(self):
        rules = self.rules(); rules['groups']['search'] = {'label': 'Kód szerinti keresés'}
        for owner, order, col in [('FIELDS.CODE', 1, 3), ('FIELDS.LOOKUP', 0, 1), ('FIELDS.NAME', 2, 6)]:
            self.activate(rules, owner, group='search', row=0, order=order, col=col, col_before=1 if order == 2 else 0)
        _, p, code = self.sample(rules, label='manual')
        section = next(s for s in p['sections'] if s['group'] == 'search')
        self.assertEqual([i['owner'] for i in section['items']], ['FIELDS.LOOKUP', 'FIELDS.CODE', 'FIELDS.NAME'])
        self.assertEqual([i['col'] for i in section['items']], [1, 3, 6])
        self.assertEqual(section['items'][-1]['col_after'], 1)
        self.assertIn('Kód szerinti keresés', code)

    def test_stale_source_trigger_or_block_context_is_rejected(self):
        rules = self.activate(self.rules(), 'FIELDS.LOOKUP', label='Keresés')
        xml = (ROOT / 'examples/screen-layout_fmb.xml').read_text()
        for changed in [xml.replace('TriggerText="NULL;"', 'TriggerText="DO_SEARCH;"'),
                        xml.replace('Width="24"', 'Width="25"'),
                        xml.replace('<Block Name="FIELDS">', '<Block Name="FIELDS" UpdateAllowed="false">')]:
            self.rejected(rules, 'SCREEN_OVERRIDE_STALE', changed)

    def test_unknown_wrong_form_hidden_and_ineffective_rules_rejected(self):
        base = self.rules()
        rules = self.activate(copy.deepcopy(base), 'FIELDS.CODE', label='Kód')
        rules['form_name'] = 'OTHER'; self.rejected(rules, 'SCREEN_OVERRIDE_FORM')
        rules['form_name'] = 'SCREEN_LAYOUT'; rules['items']['FIELDS.UNKNOWN'] = rules['items'].pop('FIELDS.CODE')
        self.rejected(rules, 'SCREEN_OVERRIDE_UNKNOWN_ITEM')
        self.rejected(self.activate(copy.deepcopy(base), 'FIELDS.CODE', widget='text'), 'SCREEN_OVERRIDE_WIDGET')
        self.rejected(self.activate(copy.deepcopy(base), 'FIELDS.NOTE', widget='checkbox'), 'SCREEN_OVERRIDE_WIDGET')
        self.rejected(self.activate(copy.deepcopy(base), 'FIELDS.NOTE', widget='image', col=4), 'SCREEN_OVERRIDE_PLACEHOLDER')
        self.rejected(self.activate(copy.deepcopy(base), 'FIELDS.LOOKUP', readonly=True), 'SCREEN_OVERRIDE_READONLY')
        self.rejected(self.activate(copy.deepcopy(base), 'FIELDS.NAME', readonly=False), 'SCREEN_OVERRIDE_READONLY')
        xml = (ROOT / 'examples/screen-layout_fmb.xml').read_text().replace('Name="NOTE" ItemType', 'Name="NOTE" Visible="false" ItemType')
        node = ET.fromstring(xml); block = node.find('Block'); item = next(i for i in block if i.get('Name') == 'NOTE')
        rules = self.activate(copy.deepcopy(base), 'FIELDS.NOTE', label='Rejtett')
        rules['items']['FIELDS.NOTE']['source_fingerprint'] = fingerprint(item, block)
        self.rejected(rules, 'SCREEN_OVERRIDE_NOT_RENDERED', xml)

    def test_override_schema_and_grid_overflow_fail_before_publication(self):
        base = self.rules()
        for edits in [{'col': 0}, {'col': True}, {'col': 13}, {'row': -1}, {'widget': 'madeup'}, {'group': 'missing'}, {'validator': False}]:
            self.rejected(self.activate(copy.deepcopy(base), 'FIELDS.CODE', **edits), 'SCREEN_OVERRIDES')
        rules = self.activate(copy.deepcopy(base), 'FIELDS.CODE', col=12, row=0)
        self.activate(rules, 'FIELDS.NAME', col=12, row=0)
        self.rejected(rules, 'SCREEN_LAYOUT_OVERFLOW')
        for value in [None, [], {'version': 1, 'items': []}]:
            with self.assertRaises(MigrationError): validate(value)

    def test_fingerprint_ignores_serialization_and_editor_metadata(self):
        first = ET.fromstring('<Block Name="B"><Item Name="A" Width="20"><Trigger Name="T" TriggerText="NULL;"/></Item></Block>')
        second = ET.fromstring('<Block Name="B">\n <Item DirtyInfo="x" Width="20" Name="A">\n <Trigger TriggerText="NULL;" Name="T"/>\n </Item>\n</Block>')
        self.assertEqual(fingerprint(first[0], first), fingerprint(second[0], second))
        second[0][0].set('TriggerText', 'CHANGED;')
        self.assertNotEqual(fingerprint(first[0], first), fingerprint(second[0], second))

    def test_inactive_template_does_not_reject_source_updates(self):
        rules = self.rules()
        xml = (ROOT / 'examples/screen-layout_fmb.xml').read_text().replace('TriggerText="NULL;"', 'TriggerText="OTHER;"')
        _, p, _ = self.generate(xml, {'screen_overrides': rules}, label='changed')
        self.assertEqual(p['overrides'], [])

    def test_cli_override_file_and_regeneration_keep_user_changes(self):
        rules = self.activate(self.rules(), 'FIELDS.NOTE', label='Új megjegyzés')
        path = self.root / 'review.json'; path.write_text(json.dumps(rules))
        out, _, _ = self.generate(ROOT / 'examples/screen-layout_fmb.xml', extra=['--screen-overrides', str(path)])
        component = out / 'frontend/testScreen/testScreen.component.ts'
        component.write_text('// USER EDIT\n' + component.read_text())
        self.generate(ROOT / 'examples/screen-layout_fmb.xml', extra=['--screen-overrides', str(path), '--regenerate'])
        self.assertTrue(component.read_text().startswith('// USER EDIT'))

    def test_table_order_and_unsupported_form_properties(self):
        xml = '<FormModule Name="T" CoordinateSystem="Real" RealUnit="Pixel"><Block Name="B" RecordsDisplayCount="5"><Item Name="A" ItemType="Text Item"/><Item Name="Z" ItemType="Text Item"/></Block></FormModule>'
        out, _, _ = self.generate(xml, label='table-base')
        rules = json.loads((out / 'analysis/screen-overrides.template.json').read_text())
        self.activate(rules, 'B.A', order=10); self.activate(rules, 'B.Z', order=0)
        _, plan, code = self.generate(xml, {'screen_overrides': rules}, label='table')
        self.assertEqual([i['owner'] for i in plan['sections'][0]['items']], ['B.Z', 'B.A'])
        for edits in [{'row': 0}, {'col': 2}, {'col_before': 1}, {'readonly': True}, {'enabled': False}]:
            bad = copy.deepcopy(rules); self.activate(bad, 'B.A', **edits)
            self.rejected(bad, 'SCREEN_OVERRIDE_TABLE', xml)


if __name__ == '__main__': unittest.main()

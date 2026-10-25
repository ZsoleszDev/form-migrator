"""A package spec/body pair is one name, two distinct Forms objects."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from frm_forms.cli import main
from frm_forms.discovery import build_map
from frm_forms.inheritance import resolve_inputs
from frm_forms.inputs import InputIssues, load_inputs
from frm_forms.xmlmodel import get, props, tag


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / 'examples/package-pair_fmb.xml'


def unit(kind=None, body='', name='WEB_UTIL', **attrs):
    node = ET.Element('ProgramUnit', Name=name, **attrs)
    if kind is not None:
        node.set('ProgramUnitType', kind)
    if body:
        node.set('ProgramUnitText', body)
    return node


class ProgramUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, root):
        path = self.root / name
        path.write_bytes(ET.tostring(root, encoding='utf-8'))
        return path

    def form(self, *units):
        root = ET.fromstring(SAMPLE.read_bytes())
        form = root.find('FormModule')
        for child in list(form):
            if tag(child) == 'programunit':
                form.remove(child)
        form.extend(units)
        return self.write('input.xml', root)

    def library(self, *units):
        root = ET.Element('ObjectLibrary', Name='LIB')
        tab = ET.SubElement(root, 'ObjectLibraryTab', Name='T')
        tab.extend(units)
        return self.write('lib_olb.xml', root)

    def test_screen_generation_preserves_both_package_sources(self):
        out = self.root / 'screen'
        error = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(error):
            result = main(['migrate', str(SAMPLE), '--screen', '--frontend-only',
                           '--module', 'packagePair', '--out', str(out), '--ai', 'off'])
        self.assertEqual(result, 0, error.getvalue())
        self.assertEqual((out / 'analysis/source.xml').read_bytes(), SAMPLE.read_bytes())
        self.assertTrue((out / 'frontend/packagePair/packagePair.component.ts').is_file())
        effective = ET.fromstring((out / 'analysis/effective-source.xml').read_bytes())
        units = [props(e) for e in effective.iter() if tag(e) == 'programunit']
        self.assertEqual([get(p, 'ProgramUnitType') for p in units], ['Package Spec', 'Package Body'])
        self.assertIn('PROCEDURE RUN;', units[0]['programunittext'])
        self.assertIn(":FILTER.VALUE := 'package body';", units[1]['programunittext'])
        data = json.loads((out / 'analysis/discovery/form-map.json').read_text())
        for code in data['code']:
            self.assertEqual((out / 'analysis/discovery' / code['source_file']).read_text(), code['source'])
        self.assertEqual(data['actions'][0]['execution'], 'disabled')

    def test_explicit_pair_is_case_insensitive_and_accepts_property_elements(self):
        spec = unit(' PACKAGE SPEC ', 'PACKAGE WEB_UTIL IS END;', name='web_util')
        body = unit(None, 'PACKAGE BODY WEB_UTIL IS END;')
        ET.SubElement(body, 'Property', Name='ProgramUnitType', Value='Package Body')
        for pair in ((spec, body), (body, spec)):
            with self.subTest(first=pair[0].get('Name')):
                result = resolve_inputs(load_inputs(self.form(*pair)))
                self.assertEqual(sum(tag(e) == 'programunit' for e in result.root.iter()), 2)

    def test_only_one_explicit_spec_body_pair_may_share_a_name(self):
        combinations = [
            ('Package Spec', 'Package Spec'), ('Package Body', 'Package Body'),
            ('Procedure', 'Function'), ('Package Spec', 'Procedure'),
            ('Package Body', None), (None, None), ('Unknown Spec', 'Package Body'),
            ('Package Spec', 'Package Body', 'Package Body'),
            ('Package Spec', 'Package Body', 'Procedure'),
        ]
        for kinds in combinations:
            with self.subTest(kinds=kinds):
                # Source text must never serve as a type-inference bypass.
                source = self.form(*(unit(k, 'PACKAGE BODY WEB_UTIL IS END;') for k in kinds))
                with self.assertRaises(InputIssues) as error:
                    resolve_inputs(load_inputs(source))
                issue = error.exception.issues[0]
                self.assertEqual(issue['code'], 'DUPLICATE_CHILD')
                self.assertEqual(issue['child'], 'WEB_UTIL')
                self.assertIn('declarations', issue)

    def test_explicit_olb_links_select_the_corresponding_package_part(self):
        library = self.library(unit('Package Spec', 'spec from OLB'), unit('Package Body', 'body from OLB'))
        source = self.form(*(unit(k, ParentFilename='lib.olb', ParentModule='LIB', ParentName='WEB_UTIL')
                             for k in ('Package Spec', 'Package Body')))
        result = resolve_inputs(load_inputs(source, [library]), strict=True)
        units = [props(e) for e in result.root.iter() if tag(e) == 'programunit']
        self.assertEqual([p['programunittext'] for p in units], ['spec from OLB', 'body from OLB'])
        records = [r for r in result.form_objects if r['kind'] == 'programunit']
        self.assertTrue(all(r['properties']['programunittext']['source'] == 'inherited' for r in records))
        self.assertFalse(result.unresolved_links)
        self.assertEqual(len(result.resolved_links), 2)

    def test_missing_or_mismatched_inherited_part_never_selects_the_other_part(self):
        library = self.library(unit('Package Body', 'body from OLB'))
        source = self.form(unit('Package Spec', ParentFilename='lib.olb', ParentName='WEB_UTIL'))
        with self.assertRaisesRegex(InputIssues, 'PROGRAM_UNIT_TYPE_MISMATCH'):
            resolve_inputs(load_inputs(source, [library]), strict=True)
        result = resolve_inputs(load_inputs(source, [library]))
        self.assertIn('PROGRAM_UNIT_TYPE_MISMATCH', [r['reason'] for r in result.unresolved_links])
        self.assertFalse(any('programunittext' in props(e) for e in result.root.iter() if tag(e) == 'programunit'))
        library = self.library(unit('Package Spec', 'spec'), unit('Package Body', 'body'))
        source = self.form(unit(ParentFilename='lib.olb', ParentName='WEB_UTIL'))
        with self.assertRaisesRegex(InputIssues, 'AMBIGUOUS_PARENT_OBJECT'):
            resolve_inputs(load_inputs(source, [library]), strict=True)

    def test_local_module_reference_uses_the_package_part(self):
        source = self.form(unit('Package Spec', 'spec'), unit('Package Body', 'body'),
                           unit('Package Body', name='LOCAL_COPY', ParentModule='PACKAGE_PAIR', ParentName='WEB_UTIL'))
        result = resolve_inputs(load_inputs(source), strict=True)
        copied = next(props(e) for e in result.root.iter() if get(props(e), 'Name') == 'LOCAL_COPY')
        self.assertEqual(copied['programunittext'], 'body')

    def test_rejected_duplicate_does_not_publish_partial_output(self):
        source = self.form(unit('Package Body', 'first body'), unit('Package Body', 'second body'))
        out = self.root / 'rejected'
        error = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(error):
            result = main(['migrate', str(source), '--screen', '--out', str(out), '--zip'])
        self.assertEqual(result, 1)
        self.assertIn('DUPLICATE_CHILD', error.getvalue())
        self.assertFalse(out.exists())
        self.assertFalse(out.with_suffix('.zip').exists())

    def test_ambiguous_olb_candidates_of_the_same_part_are_still_rejected(self):
        library = self.library(unit('Package Spec', 'spec'), unit('Package Body', 'body'))
        root = ET.fromstring(library.read_bytes())
        tab = ET.SubElement(root, 'ObjectLibraryTab', Name='OTHER')
        tab.append(unit('Package Body', 'other body'))
        self.write('lib_olb.xml', root)
        source = self.form(unit('Package Body', ParentFilename='lib.olb', ParentName='WEB_UTIL'))
        with self.assertRaisesRegex(InputIssues, 'AMBIGUOUS_PARENT_OBJECT'):
            resolve_inputs(load_inputs(source, [library]), strict=True)

    def test_container_inheritance_merges_package_parts_separately(self):
        parent = ET.Element('ObjectGroup', Name='BASE')
        parent.extend([unit('Package Spec', 'spec'), unit('Package Body', 'body')])
        library = self.library(parent)
        child = ET.Element('ObjectGroup', Name='LOCAL', ParentFilename='lib.olb', ParentName='BASE')
        child.extend([unit('Package Body', 'local body'), unit('Package Spec', 'local spec')])
        source = self.form(child)
        result = resolve_inputs(load_inputs(source, [library]), strict=True)
        units = [props(e) for e in result.root.iter() if tag(e) == 'programunit']
        self.assertEqual([p['programunittext'] for p in units], ['local spec', 'local body'])
        self.assertEqual(len(units), 2)

    def test_inheritance_cannot_create_a_conflicting_third_program_unit(self):
        parent = ET.Element('ObjectGroup', Name='BASE')
        parent.extend([unit('Package Spec', 'spec'), unit('Package Body', 'body')])
        library = self.library(parent)
        child = ET.Element('ObjectGroup', Name='LOCAL', ParentFilename='lib.olb', ParentName='BASE')
        child.append(unit('Procedure', 'procedure'))
        with self.assertRaisesRegex(InputIssues, 'DUPLICATE_CHILD'):
            resolve_inputs(load_inputs(self.form(child), [library]), strict=True)

    def test_package_call_preserves_both_sources_as_review_candidates(self):
        data = build_map([('input.xml', ET.fromstring(SAMPLE.read_bytes()))])
        units = [c for c in data['code'] if c['kind'] == 'programunit']
        self.assertEqual({c['program_unit_type'] for c in units}, {'Package Spec', 'Package Body'})
        self.assertEqual(len({c['path'] for c in units}), 2)
        action = data['actions'][0]
        trigger = next(c for c in data['code'] if c['id'] == action['trigger_id'])
        call = next(c for c in trigger['calls'] if c['name'] == 'WEB_UTIL.RUN')
        self.assertEqual(call['kind'], 'local_package')
        self.assertFalse(call['signature_verified'])
        self.assertEqual(set(call['target_ids']), {c['id'] for c in units})
        self.assertEqual(set(action['reachable_code']), {trigger['id'], *(c['id'] for c in units)})
        self.assertEqual(action['binds'], ['FILTER.VALUE'])
        self.assertEqual(action['external_calls'], [])
        self.assertEqual(action['execution'], 'disabled')

    def test_discovery_does_not_combine_package_parts_from_different_containers(self):
        root = ET.fromstring(SAMPLE.read_bytes())
        form = root.find('FormModule')
        body = form.findall('ProgramUnit')[1]
        form.remove(body)
        ET.SubElement(form, 'ObjectGroup', Name='OTHER').append(body)
        data = build_map([('input.xml', root)])
        call = next(c for u in data['code'] if u['kind'] == 'trigger' for c in u['calls'])
        self.assertEqual(call['kind'], 'ambiguous_local_program_unit')


if __name__ == '__main__':
    unittest.main()

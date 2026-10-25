import json
import tempfile
import unittest
from pathlib import Path
from frm_forms.ui_types import classify, ITEM_TYPES
from frm_forms.ui_inventory import inventory
from frm_forms.common import MigrationError

class TypeTests(unittest.TestCase):
    def test_every_explicit_item_type(self):
        for item, widget in ITEM_TYPES.items():
            with self.subTest(item=item):
                self.assertEqual(classify({'ItemType':item})['widget'], widget)
    def test_inference_priority_and_origin(self):
        for props,tags,widget in [({'CheckedValue':'1','UncheckedValue':'0'}, [], 'checkbox'), ({},['RadioButton'],'radio'), ({'ListStyle':'Poplist'},[],'select'), ({'ConcealData':'true'},[],'password')]:
            result=classify(props,tags); self.assertEqual(result['widget'],widget); self.assertEqual(result['type_source'],'inferred')
        self.assertEqual(classify({'ItemType':'Push Button','CheckedValue':'1','UncheckedValue':'0'})['widget'],'button')
    def test_missing_unknown_conflicting_fail_closed(self):
        for props,tags in [({},[]),({'ItemType':'Magic'},[]),({'ConcealData':'false'},[]),({'CheckedValue':'1','UncheckedValue':'0'},['RadioButton'])]:
            self.assertEqual(classify(props,tags)['widget'],'unsupported')

class InventoryTests(unittest.TestCase):
    def test_names_counts_examples_and_unused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'x.xml'; source.write_text('<FormModule><Item ItemType="Text Item" Magic="a"/><Item Magic="b"/><Property Name="VersionOddity" Value="z"/></FormModule>')
            data=inventory([source],root/'out'); row=next(x for x in data['attributes'] if x['attribute']=='Magic')
            self.assertEqual(row['count'],2); self.assertEqual(row['examples'],['a','b']); self.assertIn(row,data['unused_attributes'])
            self.assertEqual(data['property_element_names'],[{'name':'VersionOddity','count':1}])
            first=(root/'out/inventory.json').read_bytes(); inventory([source],root/'out2'); self.assertEqual(first,(root/'out2/inventory.json').read_bytes())
    def test_invalid_xml_never_creates_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'x.xml'; source.write_text('<broken>')
            with self.assertRaises(MigrationError): inventory([source],root/'out')
            self.assertFalse((root/'out').exists())

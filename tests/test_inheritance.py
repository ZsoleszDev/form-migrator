import contextlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from frm_forms.cli import main
from frm_forms.inheritance import resolve_inputs
from frm_forms.inputs import InputIssues, load_inputs
from frm_forms.xmlmodel import parse_xml


class InheritanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.libraries = []

    def write(self, filename, content):
        path = self.root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def olb(self, filename, body, module=None):
        path = self.write(filename + "_olb.xml", f'<Module><ObjectLibrary Name="{module or filename.upper()}"><ObjectLibraryTab Name="T">{body}</ObjectLibraryTab></ObjectLibrary></Module>')
        self.libraries.append(path)
        return path

    def form(self, body, block_attrs='DatabaseDataBlock="false"'):
        return self.write("screen_fmb.xml", f'<Module><FormModule Name="SCREEN" Title="Teszt" CoordinateSystem="Character"><Block Name="FILTER" {block_attrs}>{body}</Block></FormModule></Module>')

    def item(self, attrs="", body="", name="FIELD", parent="BASE", library="base"):
        return f'<Item Name="{name}" ParentFilename="{library}.olb" ParentName="{parent}" {attrs}>{body}</Item>'

    def resolve(self, source, strict=False):
        result = resolve_inputs(load_inputs(source, self.libraries, strict_inheritance=strict), strict)
        model = parse_xml(source, resolved_root=result.root, property_metadata=result.metadata)
        return result, model

    def gap(self, source, reason):
        """Default mode: the module still builds, the gap is reported."""
        result, _ = self.resolve(source)
        reasons = {link["reason"] for link in result.unresolved_links}
        self.assertIn(reason, reasons)
        return next(entry for entry in result.gaps if reason in entry["reasons"])

    def reject(self, source, code, strict=True):
        destination = self.root / "out"
        arguments = ["migrate", str(source), "--out", str(destination), "--zip"]
        if strict:
            arguments.append("--strict-inheritance")
        for library in self.libraries:
            arguments += ["--olb", str(library)]
        err = io.StringIO()
        with patch("frm_forms.cli.generate_java") as java, patch("frm_forms.cli.generate_angular") as angular, patch("frm_forms.cli.advise") as ai:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                result = main(arguments)
            self.assertEqual(result, 1, err.getvalue())
            java.assert_not_called()
            angular.assert_not_called()
            ai.assert_not_called()
        self.assertFalse(destination.exists())
        self.assertFalse(destination.with_suffix(".zip").exists())
        self.assertFalse(list(self.root.glob(".frm-stage-*")))
        issues = json.loads(err.getvalue().removeprefix("HIBA: "))["issues"]
        self.assertIn(code, [issue["code"] for issue in issues])
        return next(issue for issue in issues if issue["code"] == code)

    def test_explicit_false_zero_and_empty_override_inherited_values(self):
        self.olb("base", '<Item Name="BASE" ItemType="Text Item" Required="true" Hint="Parent hint" Width="500"/>')
        _, model = self.resolve(self.form(self.item('Required="false" Hint="" Width="0"')))
        item = model["blocks"][0]["items"][0]
        self.assertFalse(item["required"])
        self.assertEqual(item["properties"]["hint"], "")
        self.assertEqual(item["properties"]["width"], "0")
        p = item["property_provenance"]
        for key in ("required", "hint", "width", "name"):
            self.assertEqual(p[key]["source"], "explicit")
        self.assertEqual(p["itemtype"]["source"], "inherited")
        self.assertEqual(p["itemtype"]["origin"]["file"], "base_olb.xml")

    def test_three_level_chain_keeps_ultimate_origin_and_each_override(self):
        self.olb("base", '<Item Name="BASE" ItemType="Check Box" DataType="Char" Required="true" Hint="Base" CheckedValue="Y" UncheckedValue="N"/>')
        self.olb("middle", self.item('Hint="Middle"', name="MIDDLE"))
        self.olb("top", self.item('CheckedValue="1" UncheckedValue="0"', name="TOP", library="middle", parent="MIDDLE"))
        result, model = self.resolve(self.form(self.item('Label="Local"', library="top", parent="TOP")))
        item = model["blocks"][0]["items"][0]
        self.assertEqual((item["kind"], item["checked_value"], item["unchecked_value"]), ("checkbox", "1", "0"))
        self.assertTrue(item["required"])
        p = item["property_provenance"]
        self.assertEqual(p["itemtype"]["origin"]["file"], "base_olb.xml")
        self.assertEqual([entry["file"] for entry in p["itemtype"]["via"]], ["middle_olb.xml", "top_olb.xml", "screen_fmb.xml"])
        self.assertEqual(p["hint"]["origin"]["file"], "middle_olb.xml")
        self.assertEqual(p["checkedvalue"]["origin"]["file"], "top_olb.xml")
        self.assertEqual(p["label"]["source"], "explicit")
        self.assertEqual(len(result.resolved_links), 3)

    def test_default_sources_are_actual_parser_fallbacks_not_injected_properties(self):
        self.olb("base", '<Item Name="BASE" ItemType="Text Item"/>')
        result, model = self.resolve(self.form(self.item()))
        item = model["blocks"][0]["items"][0]
        self.assertNotIn("visible", item["properties"])
        record = item["property_provenance"]["visible"]
        self.assertEqual((record["source"], record["value"], record["origin"]), ("default", "yes", None))
        self.assertEqual(record["basis"], "existing-generator-fallback")
        item_record = next(record for record in result.form_objects if record["kind"] == "item")
        self.assertEqual(item_record["properties"]["visible"], record)
        self.assertTrue(set(item["properties"]) <= set(item["property_provenance"]))

    def test_empty_inherited_trigger_gets_unchanged_body_and_source(self):
        body = "BEGIN\n  MESSAGE('Hello');\nEND;"
        self.olb("base", f'<Item Name="BASE" ItemType="Text Item"><Trigger Name="WHEN-NEW-ITEM-INSTANCE"><TriggerText><![CDATA[{body}]]></TriggerText></Trigger></Item>')
        source = self.form(self.item(body='<Trigger Name="WHEN-NEW-ITEM-INSTANCE" SubclassSubObject="true" TriggerText=""/>'))
        _, model = self.resolve(source)
        trigger = model["triggers"][0]
        self.assertEqual(trigger["source"], body)
        self.assertEqual(trigger["property_provenance"]["triggertext"]["source"], "inherited")
        self.assertEqual(trigger["property_provenance"]["triggertext"]["origin"]["file"], "base_olb.xml")

    def test_trigger_body_can_be_attribute_property_child_or_direct_cdata(self):
        for number, markup in enumerate([
            '<Trigger Name="T" TriggerText="NULL;"/>',
            '<Trigger Name="T"><Property Name="TriggerText" Value="NULL;"/></Trigger>',
            '<Trigger Name="T"><TriggerText>NULL;</TriggerText></Trigger>',
            '<Trigger Name="T"><![CDATA[NULL;]]></Trigger>',
        ]):
            with self.subTest(markup=markup):
                self.libraries.clear()
                self.olb("base", '<Item Name="BASE" ItemType="Text Item">' + markup + '</Item>')
                _, model = self.resolve(self.form(self.item(body='<Trigger Name="T" SubclassSubObject="true"/>')))
                self.assertEqual(model["triggers"][0]["source"], "NULL;")

    def test_explicit_trigger_body_overrides_parent_even_with_subclass_flag(self):
        self.olb("base", '<Item Name="BASE" ItemType="Text Item"><Trigger Name="T" TriggerText="PARENT();" ExecuteHierarchy="Override"/></Item>')
        _, model = self.resolve(self.form(self.item(body='<Trigger Name="T" SubclassSubObject="true" Text="CHILD();" ExecuteHierarchy="Before"/>')))
        tr = model["triggers"][0]
        self.assertEqual(tr["source"], "CHILD();")
        self.assertEqual(tr["property_provenance"]["triggertext"]["source"], "explicit")
        self.assertEqual(tr["properties"]["executehierarchy"], "Before")

    def test_trigger_chain_and_absent_child_trigger_are_inherited(self):
        self.olb("base", '<Item Name="BASE" ItemType="Text Item"><Trigger Name="T" TriggerText="NULL;"/></Item>')
        self.olb("middle", self.item(name="MIDDLE", body='<Trigger Name="T" SubclassSubObject="true"/>'))
        _, model = self.resolve(self.form(self.item(library="middle", parent="MIDDLE")))
        self.assertEqual(model["triggers"][0]["source"], "NULL;")
        self.assertEqual(model["triggers"][0]["property_provenance"]["triggertext"]["source"], "inherited")

    def test_inherited_block_children_merge_by_name_keep_parent_order_and_add_locals(self):
        self.olb("base", '<Block Name="BASE" DatabaseDataBlock="false"><Item Name="FIRST" ItemType="Text Item" Required="true"/><Item Name="SECOND" ItemType="Push Button"/></Block>')
        source = self.form('<Item Name="FIRST" SubclassSubObject="true" Required="false"/><Item Name="THIRD" ItemType="Display Item"/>',
                           'ParentFilename="base.olb" ParentName="BASE"')
        _, model = self.resolve(source)
        items = model["blocks"][0]["items"]
        self.assertEqual([item["name"] for item in items], ["FIRST", "SECOND", "THIRD"])
        self.assertFalse(items[0]["required"])
        self.assertEqual(items[0]["property_provenance"]["itemtype"]["source"], "inherited")
        self.assertEqual(items[1]["kind"], "button")

    def test_named_subobject_can_be_referenced_through_its_resolved_container(self):
        self.olb("zbase", '<Block Name="B"><Item Name="I" ItemType="Text Item" Required="true"/></Block>')
        self.olb("derived", '<Block Name="DERIVED" ParentFilename="zbase.olb" ParentName="B"><Item Name="I" SubclassSubObject="true" Required="false"/></Block>')
        _, model = self.resolve(self.form(self.item(library="derived", parent="I")))
        item = model["blocks"][0]["items"][0]
        self.assertEqual(item["kind"], "text")
        self.assertFalse(item["required"])
        self.assertEqual(item["property_provenance"]["itemtype"]["origin"]["file"], "zbase_olb.xml")

    def test_two_templates_may_have_same_event_without_global_trigger_matching(self):
        self.olb("base", '<Item Name="BASE" ItemType="Text Item"><Trigger Name="T" TriggerText="FIRST();"/></Item><Item Name="OTHER" ItemType="Text Item"><Trigger Name="T" TriggerText="SECOND();"/></Item>')
        _, model = self.resolve(self.form(self.item(body='<Trigger Name="T" SubclassSubObject="true"/>')))
        self.assertEqual(model["triggers"][0]["source"], "FIRST();")

    def test_unmatched_subclass_trigger_cannot_borrow_another_items_body(self):
        self.olb("base", '<Item Name="BASE" ItemType="Text Item"/><Item Name="OTHER" ItemType="Text Item"><Trigger Name="T" TriggerText="NULL;"/></Item>')
        source = self.form(self.item(body='<Trigger Name="T" SubclassSubObject="true"/>'))
        _, model = self.resolve(source)
        self.assertEqual(model["triggers"][0]["source"].strip(), "")
        self.reject(source, "MISSING_PARENT_SUBOBJECT")

    def test_disagreeing_attribute_aliases_cannot_shadow_explicit_value(self):
        self.olb("base", '<Item Name="BASE" ItemType="Check Box" CheckBoxCheckedValue="Y"/>')
        self.reject(self.form(self.item('CheckedValue="1"')), "CONFLICTING_PROPERTY_ALIASES")

    def test_property_provenance_is_saved_for_every_effective_attribute(self):
        self.olb("base", '<Item Name="BASE" ItemType="Text Item" Required="true"/>')
        result, _ = self.resolve(self.form(self.item()))
        out = self.root / "audit"
        result.write(out)
        data = json.loads((out / "analysis/inheritance.json").read_text())
        catalog = json.loads((out / "analysis/olb-catalog.json").read_text())
        self.assertEqual(catalog["key"], ["file", "object_name"])
        self.assertTrue(any(template["file"] == "base.olb" and template["object_name"] == "BASE" for template in catalog["templates"]))
        for element in result.root.iter():
            metadata = result.metadata[id(element)]
            for key, value in element.attrib.items():
                self.assertEqual(metadata["properties"][key]["value"], value)
                self.assertIn(metadata["properties"][key]["source"], {"explicit", "inherited", "default"})
        self.assertEqual(data["inheritance_version"], 1)
        self.assertTrue((out / "analysis/effective-source.xml").read_bytes().startswith(b"<?xml"))

    def test_source_inventory_is_not_replaced_by_normalized_effective_inventory(self):
        self.olb("base", '<Item Name="BASE" ItemType="Text Item"><Trigger Name="T" TriggerText="NULL;"/></Item>')
        source = self.form(self.item(body='<Property Name="Required" Value="true"/>'))
        _, model = self.resolve(source)
        self.assertEqual(model["inventory"]["property"], 1)
        self.assertNotIn("trigger", model["inventory"])
        self.assertNotIn("property", model["effective_inventory"])
        self.assertEqual(model["effective_inventory"]["trigger"], 1)

    def test_resolved_xml_without_parentmodule_does_not_approve_backend_operations(self):
        self.olb("base", '<Item Name="BASE" ItemType="Text Item" DataType="Number" ColumnName="ID" DatabaseItem="true" PrimaryKey="true"/>')
        source = self.form(self.item(name="ID"), 'DatabaseDataBlock="true" QueryDataSourceName="RECORDS"')
        schema = self.write("schema.json", json.dumps({"blocks": {"FILTER": {"writable": True, "primary_key": ["ID"]}}}))
        out = self.root / "module"
        args = ["migrate", str(source), "--olb", str(self.libraries[0]), "--schema", str(schema), "--out", str(out)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 0)
        model = json.loads((out / "analysis/form.ir.json").read_text())
        self.assertTrue(any(issue["code"] == "INHERITANCE_BACKEND_REVIEW" for issue in model["issues"]))
        self.assertTrue(all(not model["blocks"][0]["can_" + op] for op in ("read", "create", "update", "delete")))

    def test_unused_library_chain_does_not_change_form_backend_or_frontend(self):
        self.olb("base", '<Item Name="BASE" ItemType="Text Item"/>')
        self.olb("derived", self.item(name="DERIVED"))
        source = self.form('<Item Name="ID" ItemType="Text Item" DataType="Number" PrimaryKey="true"/>',
                           'DatabaseDataBlock="true" QueryDataSourceName="RECORDS"')
        generated = []
        for name, libraries in [("plain", []), ("extra", self.libraries)]:
            out = self.root / name
            args = ["migrate", str(source), "--out", str(out)]
            for library in libraries:
                args += ["--olb", str(library)]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
            generated.append({p.relative_to(out).as_posix(): p.read_bytes() for directory in ("backend", "frontend")
                              for p in (out / directory).rglob("*") if p.is_file()})
        self.assertEqual(generated[0], generated[1])

    def test_same_library_module_qualified_parent_is_resolved(self):
        self.olb("base", '<Item Name="ROOT" ItemType="Check Box"/><Item Name="BASE" ParentModule="BASE" ParentName="ROOT"/>')
        _, model = self.resolve(self.form(self.item()))
        self.assertEqual(model["blocks"][0]["items"][0]["kind"], "checkbox")

    def test_unknown_properties_are_preserved_with_sources_and_original_names(self):
        self.olb("base", '<Item Name="BASE" ItemType="Text Item" VendorExtension="opaque"/>')
        _, model = self.resolve(self.form(self.item('UnfamiliarFlag="0"')))
        p = model["blocks"][0]["items"][0]["property_provenance"]
        self.assertEqual(p["vendorextension"]["value"], "opaque")
        self.assertEqual(p["vendorextension"]["source"], "inherited")
        self.assertEqual(p["vendorextension"]["origin"]["property"], "VendorExtension")
        self.assertEqual(p["unfamiliarflag"]["source"], "explicit")

    def test_namespaces_and_property_elements_take_part_in_overrides(self):
        library = self.write("base_olb.xml", '<x:Module xmlns:x="urn:forms"><x:ObjectLibrary Name="BASE"><x:Item Name="BASE"><x:Property Name="ItemType" Value="Text Item"/><x:Property Name="Required" Value="true"/></x:Item></x:ObjectLibrary></x:Module>')
        self.libraries.append(library)
        _, model = self.resolve(self.form(self.item(body='<Property Name="Required" Value="false"/>')))
        item = model["blocks"][0]["items"][0]
        self.assertFalse(item["required"])
        self.assertEqual(item["property_provenance"]["required"]["origin"]["representation"], "property-element")

    def test_cross_library_cycle_reports_chain_and_publishes_nothing(self):
        self.olb("base", self.item(name="BASE", library="other", parent="OTHER"))
        self.olb("other", self.item(name="OTHER"))
        issue = self.reject(self.form(self.item()), "INHERITANCE_CYCLE")
        self.assertEqual(issue["chain"][0], issue["chain"][-1])
        self.assertEqual({step["file"] for step in issue["chain"]}, {"base_olb.xml", "other_olb.xml"})

    def test_self_cycle_is_rejected(self):
        self.olb("base", self.item(name="BASE"))
        self.reject(self.form(self.item()), "INHERITANCE_CYCLE")

    def test_missing_parent_object_and_module_mismatch_are_reported_then_rejected(self):
        self.olb("base", '<Item Name="OTHER" ItemType="Text Item"/>')
        for body, code in [(self.item(), "MISSING_PARENT_OBJECT"),
                           (self.item('ParentModule="WRONG"', parent="OTHER"), "PARENT_MODULE_MISMATCH")]:
            with self.subTest(code=code):
                self.assertEqual(self.gap(self.form(body), code)["affected_items"], 1)
                self.reject(self.form(body), code)

    def test_ambiguous_catalog_name_and_wrong_parent_kind_never_pick_a_candidate(self):
        self.olb("base", '<Block Name="B1"><Item Name="BASE" ItemType="Text Item"/></Block><Block Name="B2"><Item Name="BASE" ItemType="Text Item"/></Block>')
        self.gap(self.form(self.item()), "AMBIGUOUS_PARENT_OBJECT")
        issue = self.reject(self.form(self.item()), "AMBIGUOUS_PARENT_OBJECT")
        self.assertEqual(len(issue["candidates"]), 2)
        self.gap(self.form(self.item(parent="B1")), "PARENT_TYPE_MISMATCH")
        self.reject(self.form(self.item(parent="B1")), "PARENT_TYPE_MISMATCH")

    def test_incomplete_link_and_legacy_link_never_guess_a_parent(self):
        self.olb("base", '<Item Name="BASE" ItemType="Text Item"/>')
        for body, code in [('<Item Name="FIELD" ParentModule="BASE" ParentName="BASE"/>', "INCOMPLETE_PARENT_REFERENCE"),
                           ('<Item Name="FIELD" ParentFilename="base.olb"/>', "INCOMPLETE_PARENT_REFERENCE"),
                           ('<Item Name="FIELD" SubclassModule="BASE" SubclassObjectName="BASE"/>', "UNSUPPORTED_INHERITANCE_LINK")]:
            with self.subTest(code=code):
                self.gap(self.form(body), code)
                self.reject(self.form(body), code)

    def test_module_qualified_self_reference_resolves_inside_the_same_form(self):
        """Headstart forms carry ParentModule = their own name after localisation."""
        body = ('<Item Name="TEMPLATE" ItemType="Check Box" Hint="Sablon"/>'
                '<Item Name="FIELD" ParentModule="SCREEN" ParentName="TEMPLATE"/>')
        _, model = self.resolve(self.form(body))
        field = next(i for i in model["blocks"][0]["items"] if i["name"] == "FIELD")
        self.assertEqual(field["kind"], "checkbox")
        self.assertEqual(field["property_provenance"]["itemtype"]["source"], "inherited")

    def test_empty_trigger_without_subclass_flag_cannot_silently_inherit(self):
        self.olb("base", '<Item Name="BASE" ItemType="Text Item"><Trigger Name="T" TriggerText="NULL;"/></Item>')
        self.reject(self.form(self.item(body='<Trigger Name="T"/>')), "EMPTY_TRIGGER_OVERRIDE")

    def test_empty_inherited_trigger_body_is_reported_then_rejected(self):
        self.olb("base", '<Item Name="BASE" ItemType="Text Item"><Trigger Name="T"/></Item>')
        source = self.form(self.item(body='<Trigger Name="T" SubclassSubObject="true"/>'))
        self.assertGreaterEqual(self.gap(source, "MISSING_INHERITED_TRIGGER_BODY")["affected_triggers"], 1)
        self.reject(source, "MISSING_INHERITED_TRIGGER_BODY")

    def test_subobject_absent_from_a_resolved_parent_is_named_precisely(self):
        """The parent is in hand and lacks this member: not a parentless orphan."""
        self.olb("base", '<Item Name="BASE" ItemType="Text Item"><Trigger Name="T" TriggerText="NULL;"/></Item>')
        source = self.form(self.item(body='<Trigger Name="ABSENT" SubclassSubObject="true"/>'))
        self.assertGreaterEqual(self.gap(source, "MISSING_PARENT_SUBOBJECT")["affected_triggers"], 1)
        _, model = self.resolve(source)
        absent = next(t for t in model["triggers"] if t["event"] == "ABSENT")
        self.assertEqual(absent["source"].strip(), "")
        self.reject(source, "MISSING_PARENT_SUBOBJECT")

    def test_orphan_subclass_is_rejected(self):
        self.reject(self.form('<Item Name="FIELD" ItemType="Text Item" SubclassSubObject="true"/>'), "ORPHAN_SUBCLASS")

    def test_conflicting_property_encodings_and_duplicate_child_names_are_rejected(self):
        self.olb("base", '<Item Name="BASE" ItemType="Text Item"/>')
        self.reject(self.form(self.item('Required="true"', '<Property Name="Required" Value="false"/>')), "DUPLICATE_PROPERTY")
        self.reject(self.form(self.item(body='<Trigger Name="T" TriggerText="NULL;"/><Trigger Name="T" TriggerText="NULL;"/>')), "DUPLICATE_CHILD")

    def test_inherited_item_type_must_be_known_even_for_whole_inherited_block(self):
        self.olb("base", '<Block Name="BASE"><Item Name="I"/></Block>')
        self.reject(self.form('', 'ParentFilename="base.olb" ParentName="BASE"'), "UNSUPPORTED_ITEM")

    def test_inherited_anonymous_options_are_preserved_but_positional_merge_is_rejected(self):
        self.olb("base", '<Item Name="BASE" ItemType="List Item"><ListItemElement Label="One" Value="1"/><ListItemElement Label="Two" Value="2"/></Item>')
        _, model = self.resolve(self.form(self.item()))
        self.assertEqual(model["blocks"][0]["items"][0]["options"], [{"label": "One", "value": "1"}, {"label": "Two", "value": "2"}])
        self.reject(self.form(self.item(body='<ListItemElement Label="Changed" Value="1"/>')), "AMBIGUOUS_CHILD_COLLECTION")

    def test_depth_limit_has_actionable_failure(self):
        self.olb("base", '<Item Name="ROOT" ItemType="Text Item"/><Item Name="BASE" ParentFilename="base.olb" ParentName="ROOT"/>')
        with patch("frm_forms.inheritance.MAX_DEPTH", 3):
            self.reject(self.form(self.item()), "INHERITANCE_DEPTH_LIMIT")

    def test_qualified_menu_inheritance_is_preserved_without_generating_menu_ui(self):
        self.olb("base", '<Menu Name="BASE" Label="Inherited menu"/>')
        source = self.form('<Item Name="FIELD" ItemType="Text Item"/>')
        menu = self.write("nav_mmb.xml", '<MenuModule Name="NAV"><Menu Name="MENU" ParentFilename="base.olb" ParentName="BASE"/></MenuModule>')
        result = resolve_inputs(load_inputs(source, self.libraries, menu))
        menu_record = next(record for document in result.documents if document["file"] == "nav_mmb.xml" for record in document["objects"] if record["kind"] == "menu")
        self.assertEqual(menu_record["properties"]["label"]["source"], "inherited")
        self.assertEqual(menu_record["properties"]["label"]["value"], "Inherited menu")

    def test_repeated_runs_with_different_library_order_are_byte_identical(self):
        self.olb("base", '<Item Name="BASE" ItemType="Text Item"><Trigger Name="T" TriggerText="NULL;"/></Item>')
        self.olb("middle", self.item(name="MIDDLE", body='<Trigger Name="T" SubclassSubObject="true"/>'))
        source = self.form(self.item(library="middle", parent="MIDDLE"))
        archives = []
        for folder, libraries in [("one", self.libraries), ("two", list(reversed(self.libraries)))]:
            relocated = self.root / folder
            relocated.mkdir()
            for file in [source, *libraries]:
                shutil.copyfile(file, relocated / file.name)
            args = ["migrate", str(relocated / source.name), "--out", str(relocated / "module"), "--zip"]
            for file in libraries:
                args += ["--olb", str(relocated / file.name)]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
            archives.append((relocated / "module.zip").read_bytes())
        self.assertEqual(archives[0], archives[1])


if __name__ == "__main__":
    unittest.main()

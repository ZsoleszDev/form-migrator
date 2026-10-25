import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

from frm_forms.cli import main
from frm_forms.inputs import InputIssues, load_inputs
from frm_forms.inheritance import resolve_inputs
from frm_forms.xmlmodel import props, tag


ROOT = Path(__file__).resolve().parents[1]
FORM = '''<Module><FormModule Name="SAMPLE" Title="Minta" CoordinateSystem="Character">
<Block Name="FILTER" DatabaseDataBlock="false">
<Item Name="QUERY" ItemType="Text Item" DataType="Char" DatabaseItem="false"/>
</Block></FormModule></Module>'''
CHECKBOX = '''<FormModule Name="CHECKBOX" Title="Checkbox" CoordinateSystem="Real" RealUnit="Decipoint"><Block Name="FILTER">
<Item Name="UBI_P01" Height="219" XPosition="4200" QueryAllowed="false"
 ParentModule="QMSOLB65" DatabaseItem="false" Width="1420" Required="true"
 YPosition="455" Label="Kézi rögzített" Hint="Kézi rögzített adatlapok."
 InitializeValue="0" ParentName="CGSO$CHECK_BOX" CheckedValue="1"
 UncheckedValue="0" TabPageName="" CanvasName="CG$PAGE_1"
 ParentFilename="qmsolb65.olb" FormatMask="">
  <Trigger Name="WHEN-NEW-ITEM-INSTANCE" SubclassSubObject="true"/>
</Item></Block><Canvas Name="CG$PAGE_1"/></FormModule>'''


class InputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.form = self.write("sample_fmb.xml", FORM)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def library(self, filename="base_olb.xml", body="", name="BASE"):
        return self.write(filename, f'<Module><ObjectLibrary Name="{name}"><ObjectLibraryTab Name="T">{body}</ObjectLibraryTab></ObjectLibrary></Module>')

    def run_cli(self, *options, source=None, out="result"):
        destination = self.root / out
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["migrate", str(source or self.form), "--out", str(destination), *map(str, options)])
        return code, destination, stdout.getvalue(), stderr.getvalue()

    def rejected(self, expected, *options, source=None, out="result"):
        code, destination, stdout, stderr = self.run_cli("--zip", *options, source=source, out=out)
        self.assertEqual(code, 1, stderr)
        self.assertEqual(stdout, "")
        self.assertFalse(destination.exists())
        self.assertFalse(destination.with_suffix(".zip").exists())
        self.assertEqual(list(self.root.glob(".frm-stage-*")), [])
        data = json.loads(stderr.removeprefix("HIBA: "))
        self.assertIn(expected, [issue["code"] for issue in data["issues"]])
        return data["issues"]

    def test_repeatable_olb_and_single_mmb_are_validated_and_archived(self):
        alpha, zeta = self.library("alpha_olb.xml"), self.library("zeta_olb.xml")
        menu = self.write("navigation_mmb.xml", '<Module><MenuModule Name="NAV"><Menu Name="MAIN"/></MenuModule></Module>')
        code, output, _, stderr = self.run_cli("--olb", zeta, "--olb", alpha, "--mmb", menu, "--zip")
        self.assertEqual(code, 0, stderr)
        inventory = json.loads((output / "analysis/inputs.json").read_text())
        self.assertEqual([file["kind"] for file in inventory["files"]], ["form", "olb", "olb", "mmb"])
        self.assertEqual([file["identity"] for file in inventory["files"][1:]], ["alpha.olb", "zeta.olb", "navigation.mmb"])
        self.assertEqual(inventory["companion_processing"], "validated-preserved-and-inheritance-resolved")
        for record in inventory["files"]:
            contents = (output / record["archive_path"]).read_bytes()
            self.assertEqual(record["sha256"], hashlib.sha256(contents).hexdigest())
        self.assertEqual((output / "analysis/inputs/mmb/navigation_mmb.xml").read_bytes(), menu.read_bytes())
        self.assertTrue(list((output / "frontend").rglob("*.ts")))
        for layer in ("CL", "DPS", "WBS"):
            self.assertTrue(list((output / "backend" / layer).glob("*.java")))
        with zipfile.ZipFile(output.with_suffix(".zip")) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn("result/analysis/inputs/olb/alpha_olb.xml", archive.namelist())

    def test_without_companions_existing_generation_still_works(self):
        code, output, _, stderr = self.run_cli()
        self.assertEqual(code, 0, stderr)
        data = json.loads((output / "analysis/inputs.json").read_text())
        self.assertEqual(len(data["files"]), 1)
        self.assertEqual(data["olb_references"], [])
        self.assertEqual((output / "analysis/source.xml").read_bytes(), self.form.read_bytes())

    def test_filename_matching_is_case_insensitive_and_handles_windows_paths(self):
        source = self.write("checkbox_fmb.xml", CHECKBOX.replace('qmsolb65.olb', r'C:\ORACLE\LIB\QMSOLB65.OLB'))
        # ObjectLibrary.Name is deliberately different: filename is the identity.
        library = self.library("QmSoLb65_OLB.XML", name="UNRELATED_INTERNAL_NAME")
        inputs = load_inputs(source, [library])
        self.assertTrue(inputs.references[0]["provided"])
        self.assertEqual(inputs.references[0]["identity"], "qmsolb65.olb")
        self.assertEqual(inputs.references[0]["filename"], "QMSOLB65.OLB")
        self.assertIn("PARENT_MODULE_MISMATCH", {gap["reason"] for gap in resolve_inputs(inputs).unresolved_links})
        with self.assertRaisesRegex(InputIssues, "PARENT_MODULE_MISMATCH"):
            resolve_inputs(load_inputs(source, [library], strict_inheritance=True), True)

    def test_user_checkbox_generates_without_the_library_and_reports_the_gap(self):
        """A DUMP=ALL form carries effective properties; only provenance is lost."""
        source = self.write("checkbox_fmb.xml", CHECKBOX)
        code, output, _, stderr = self.run_cli(source=source)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(list((output / "frontend").rglob("*.ts")))
        model = json.loads((output / "analysis/ui-model.json").read_text())
        item = model["blocks"][0]["items"][0]
        self.assertEqual(item["widget"], "checkbox")
        gap = model["inheritance"]["unresolved"][0]
        self.assertEqual(gap["filename"], "qmsolb65.olb")
        self.assertEqual(gap["required_export"], "qmsolb65_olb.xml")
        self.assertEqual((gap["affected_items"], gap["affected_triggers"]), (1, 1))
        trigger = model["triggers"][0]
        self.assertTrue(trigger["inherited_unresolved"])
        self.assertEqual(trigger["source"], "")
        codes = {issue["code"]: issue for issue in model["issues"]}
        self.assertEqual(codes["INHERITANCE_UNVERIFIED"]["severity"], "review")
        self.assertEqual(codes["INHERITED_TRIGGER_UNRESOLVED"]["severity"], "review")
        self.assertIn("qmsolb65_olb.xml", (output / "migration-report.md").read_text())
        recorded = json.loads((output / "analysis/inputs.json").read_text())["missing_libraries"]
        self.assertEqual([entry["filename"] for entry in recorded], ["qmsolb65.olb"])

    def test_strict_inheritance_rejects_the_same_input_before_generation(self):
        source = self.write("checkbox_fmb.xml", CHECKBOX)
        with patch("frm_forms.cli.generate_java") as java, patch("frm_forms.cli.generate_angular") as angular, patch("frm_forms.cli.advise") as ai:
            issues = self.rejected("MISSING_OLB", "--strict-inheritance", source=source)
            for tool in (java, angular, ai):
                tool.assert_not_called()
        issue = issues[0]
        self.assertEqual(issue["filename"], "qmsolb65.olb")
        self.assertEqual(issue["required_export"], "qmsolb65_olb.xml")
        self.assertEqual(issue["reference_count"], 1)
        self.assertIn("UBI_P01", issue["references"][0]["owner"])
        self.assertIn("--olb", issue["detail"])

    def test_supplied_library_resolves_properties_and_trigger(self):
        source = self.write("checkbox_fmb.xml", CHECKBOX)
        library = self.library("qmsolb65_olb.xml", '<Item Name="CGSO$CHECK_BOX" ItemType="Check Box"><Trigger Name="WHEN-NEW-ITEM-INSTANCE" TriggerText="NULL;"/></Item>', name="QMSOLB65")
        code, output, _, stderr = self.run_cli("--olb", library, source=source)
        self.assertEqual(code, 0, stderr)
        ir = json.loads((output / "analysis/form.ir.json").read_text())
        item = ir["blocks"][0]["items"][0]
        self.assertEqual(item["kind"], "checkbox")
        self.assertEqual(item["property_provenance"]["itemtype"]["source"], "inherited")
        self.assertEqual(ir["triggers"][0]["source"], "NULL;")

    def test_inherited_trigger_without_parent_filename_is_not_silently_dropped(self):
        """No companion is named here, so there is nothing to be lenient about."""
        source = self.write("trigger_fmb.xml", FORM.replace('</FormModule>', '<Trigger Name="PRE-FORM" SubclassSubObject="true"/></FormModule>'))
        self.rejected("ORPHAN_SUBCLASS", source=source)
        self.rejected("ORPHAN_SUBCLASS", "--strict-inheritance", source=source, out="strict")

    def test_all_missing_libraries_include_form_olb_and_mmb_references(self):
        source = self.write("dependency_fmb.xml", FORM.replace('<Item Name="QUERY"', '<Item ParentFilename="absent.olb" Name="QUERY"'))
        library = self.library(body='<Item Name="ONE" ParentFilename="deeper.olb"/><Item Name="TWO" ParentFilename="ABSENT.OLB"/>')
        menu = self.write("nav_mmb.xml", '<MenuModule Name="NAV"><Menu Name="M" ParentFilename="menus.olb"/></MenuModule>')
        issues = self.rejected("MISSING_OLB", "--strict-inheritance", "--olb", library, "--mmb", menu, source=source)
        by_name = {issue["filename"]: issue for issue in issues}
        self.assertEqual(set(by_name), {"absent.olb", "deeper.olb", "menus.olb"})
        self.assertEqual(by_name["absent.olb"]["reference_count"], 2)
        self.assertEqual(by_name["menus.olb"]["references"][0]["source"], "nav_mmb.xml")

    def test_parent_filename_property_and_namespaces_are_supported(self):
        form = self.write("property_fmb.xml", '<f:Module xmlns:f="urn:forms"><f:FormModule Name="X"><f:Block Name="B"><f:Item Name="I"><f:Property Name="ParentFilename" Value="base.olb"/></f:Item></f:Block></f:FormModule></f:Module>')
        library = self.write("base_olb.xml", '<f:Module xmlns:f="urn:forms"><f:ObjectLibrary Name="BASE"/></f:Module>')
        self.assertTrue(load_inputs(form, [library]).references[0]["provided"])

    def test_missing_library_is_not_loaded_implicitly_from_neighbouring_files(self):
        self.library("qmsolb65_olb.xml")
        source = self.write("checkbox_fmb.xml", CHECKBOX)
        self.rejected("MISSING_OLB", "--strict-inheritance", source=source)

    def test_internal_module_name_cannot_match_a_different_filename(self):
        source = self.write("checkbox_fmb.xml", CHECKBOX)
        wrong_file = self.library("renamed_olb.xml", name="QMSOLB65")
        self.rejected("MISSING_OLB", "--strict-inheritance", "--olb", wrong_file, source=source)

    def test_object_group_entries_are_pointers_not_definitions(self):
        """One group may list two different objects that share a name."""
        group = ('<ObjectGroup Name="QMSSO$MODULE">'
                 '<ObjectGroupChild Name="QMS$ENABLE_LIST_LAMP" ObjectType="Program Unit"/>'
                 '<ObjectGroupChild Name="QMS$ENABLE_LIST_LAMP" ObjectType="Visual Attribute"/>'
                 '<ObjectGroupChild Name="QMS$OTHER" ObjectType="Block"/>'
                 '<ObjectGroupChild Name="QMS$OTHER" ObjectType="Block"/>'
                 '</ObjectGroup>')
        source = self.write("group_fmb.xml", FORM.replace("</FormModule>", group + "</FormModule>"))
        code, output, _, stderr = self.run_cli(source=source)
        self.assertEqual(code, 0, stderr)
        model = json.loads((output / "analysis/ui-model.json").read_text())
        counts = {issue["detail"].split(":")[0]: issue for issue in model["issues"] if issue["code"] == "NON_UI_ELEMENT"}
        self.assertEqual(set(counts), {"objectgroup", "objectgroupchild"})
        self.assertEqual([issue["severity"] for issue in counts.values()], ["review", "review"])
        self.assertIn("4 elem", counts["objectgroupchild"]["detail"])

    def test_object_group_entries_pointing_at_different_objects_stay_ambiguous(self):
        group = ('<ObjectGroup Name="G"><ObjectGroupChild Name="DUP" ObjectType="Block" ObjectName="ALPHA"/>'
                 '<ObjectGroupChild Name="DUP" ObjectType="Block" ObjectName="BETA"/></ObjectGroup>')
        source = self.write("ambiguous_fmb.xml", FORM.replace("</FormModule>", group + "</FormModule>"))
        issues = self.rejected("DUPLICATE_CHILD", source=source)
        self.assertEqual(issues[0]["differing_properties"], ["objectname"])

    def test_object_group_type_aliases_preserve_distinct_references(self):
        aliases = ("Type", "ObjectType", "ChildObjectType", "ObjectGroupChildType")
        for first in aliases:
            for second in aliases:
                with self.subTest(first=first, second=second):
                    group = (f'<ObjectGroup Name="QMSSO$MODULE">'
                             f'<ObjectGroupChild Name="QMS$ENABLE_LIST_LAMP" {first}="Program Unit"/>'
                             f'<ObjectGroupChild Name="QMS$ENABLE_LIST_LAMP" {second}="Visual Attribute"/>'
                             '</ObjectGroup>')
                    source = self.write('type_fmb.xml', FORM.replace('</FormModule>', group + '</FormModule>'))
                    result = resolve_inputs(load_inputs(source))
                    refs = [props(e) for e in result.root.iter() if tag(e) == 'objectgroupchild']
                    self.assertEqual(len(refs), 2)
                    self.assertEqual(refs[0][first.lower()], 'Program Unit')
                    self.assertEqual(refs[1][second.lower()], 'Visual Attribute')

    def test_object_group_property_element_and_equivalent_aliases_are_preserved(self):
        group = '''<ObjectGroup Name="G">
          <ObjectGroupChild Name="REF" Type="Program Unit" ObjectType="ProgramUnit"/>
          <ObjectGroupChild Name="REF"><Property Name="Type" Value="Program Unit"/></ObjectGroupChild>
          <ObjectGroupChild Name="REF" ChildObjectType="Program Unit"/>
          <ObjectGroupChild Name="REF" Type="Visual Attribute"/>
        </ObjectGroup>'''
        source = self.write('aliases_fmb.xml', FORM.replace('</FormModule>', group + '</FormModule>'))
        result = resolve_inputs(load_inputs(source))
        refs = [props(e) for e in result.root.iter() if tag(e) == 'objectgroupchild']
        self.assertEqual(len(refs), 4)
        self.assertEqual(refs[0]['objecttype'], 'ProgramUnit')
        self.assertEqual(refs[1]['type'], 'Program Unit')
        self.assertEqual(refs[2]['childobjecttype'], 'Program Unit')

    def test_object_group_conflicting_type_aliases_are_rejected(self):
        group = '<ObjectGroup Name="G"><ObjectGroupChild Name="REF" Type="Program Unit" ObjectType="Block"/></ObjectGroup>'
        source = self.write('conflict_fmb.xml', FORM.replace('</FormModule>', group + '</FormModule>'))
        issues = self.rejected('CONFLICTING_REFERENCE_TYPE', source=source)
        self.assertEqual(issues[0]['values'], {'objecttype': 'Block', 'type': 'Program Unit'})

    def test_object_group_same_type_different_target_is_rejected_with_mixed_aliases(self):
        group = ('<ObjectGroup Name="G"><ObjectGroupChild Name="REF" Type="Block" ObjectName="ALPHA"/>'
                 '<ObjectGroupChild Name="REF" ObjectType="Block" ObjectName="BETA"/></ObjectGroup>')
        source = self.write('target_fmb.xml', FORM.replace('</FormModule>', group + '</FormModule>'))
        issues = self.rejected('DUPLICATE_CHILD', source=source)
        self.assertIn('objectname', issues[0]['differing_properties'])

    def test_object_group_inheritance_matches_type_aliases_without_losing_other_references(self):
        library = self.library(body='''<ObjectGroup Name="BASE">
          <ObjectGroupChild Name="REF" Type="Program Unit" ObjectName="ORIGINAL"/>
          <ObjectGroupChild Name="REF" Type="Visual Attribute" ObjectName="STYLE"/>
        </ObjectGroup>''')
        group = '''<ObjectGroup Name="LOCAL" ParentFilename="base.olb" ParentName="BASE">
          <ObjectGroupChild Name="REF" ObjectType="Program Unit" ObjectName="LOCAL_TARGET"/>
        </ObjectGroup>'''
        source = self.write('inherit_fmb.xml', FORM.replace('</FormModule>', group + '</FormModule>'))
        result = resolve_inputs(load_inputs(source, [library]), strict=True)
        refs = [props(e) for e in result.root.iter() if tag(e) == 'objectgroupchild']
        self.assertEqual(len(refs), 2)
        self.assertEqual([ref['objectname'] for ref in refs], ['LOCAL_TARGET', 'STYLE'])
        self.assertEqual(refs[0]['type'], refs[0]['objecttype'])
        self.assertFalse(result.unresolved_links)

    def test_combined_object_group_and_package_pair_generates_screen(self):
        source = ROOT / 'examples/object-group-types_fmb.xml'
        code, output, _, stderr = self.run_cli('--screen', '--frontend-only', '--module', 'groupTypes', source=source)
        self.assertEqual(code, 0, stderr)
        self.assertEqual((output / 'analysis/source.xml').read_bytes(), source.read_bytes())
        effective = ET.fromstring((output / 'analysis/effective-source.xml').read_bytes())
        self.assertEqual(sum(tag(e) == 'objectgroupchild' for e in effective.iter()), 3)
        self.assertEqual(sum(tag(e) == 'programunit' for e in effective.iter()), 3)
        self.assertTrue((output / 'frontend/groupTypes/groupTypes.component.ts').is_file())

    def test_duplicate_named_items_remain_rejected(self):
        source = self.write("dup_fmb.xml", FORM.replace("</Block>", '<Item Name="QUERY" ItemType="Text Item"/></Block>'))
        self.rejected("DUPLICATE_CHILD", source=source)

    def test_duplicate_library_identity_is_rejected_including_different_directories(self):
        a = self.library("one/base_olb.xml")
        b = self.library("two/BASE_OLB.XML")
        self.rejected("DUPLICATE_OLB", "--olb", a, "--olb", b)

    def test_missing_wrongly_named_and_binary_companion_files_are_rejected(self):
        cases = [
            (self.root / "missing_olb.xml", "INVALID_INPUT_FILE"),
            (self.write("base.olb", "binary"), "INVALID_INPUT_FILE"),
            (self.write("base.xml", '<ObjectLibrary Name="BASE"/>'), "INVALID_EXPORT_FILENAME"),
        ]
        for path, expected in cases:
            with self.subTest(path=path.name):
                self.rejected(expected, "--olb", path)

    def test_malformed_wrong_type_and_multiple_module_inputs_are_rejected(self):
        for text, expected in [
            ("<broken>", "INVALID_XML"),
            ('<MenuModule Name="M"/>', "WRONG_MODULE_TYPE"),
            ('<Module><ObjectLibrary Name="A"/><ObjectLibrary Name="B"/></Module>', "WRONG_MODULE_TYPE"),
        ]:
            with self.subTest(text=text):
                library = self.write("bad_olb.xml", text)
                self.rejected(expected, "--olb", library)
        menu = self.write("bad_mmb.xml", '<ObjectLibrary Name="BASE"/>')
        self.rejected("WRONG_MODULE_TYPE", "--mmb", menu)

    def test_custom_entities_in_utf16_and_oversized_companions_are_rejected(self):
        library = self.root / "bad_olb.xml"
        library.write_bytes('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY x "boom">]><ObjectLibrary Name="B"/>'.encode("utf-16"))
        self.rejected("XML_ENTITY", "--olb", library)
        with patch("frm_forms.inputs.MAX_XML_BYTES", len(FORM.encode()) + 5):
            library.write_bytes(b" " * (len(FORM.encode()) + 6))
            self.rejected("XML_TOO_LARGE", "--olb", library)

    def test_external_doctype_is_accepted_without_loading_it(self):
        library = self.write("base_olb.xml", '<!DOCTYPE ObjectLibrary SYSTEM "https://invalid.example/no-fetch.dtd"><ObjectLibrary Name="BASE"/>')
        self.assertEqual(load_inputs(self.form, [library]).documents[1].module_name, "BASE")

    def test_non_olb_parent_filename_is_rejected_instead_of_guessed(self):
        source = self.write("checkbox_fmb.xml", CHECKBOX.replace('qmsolb65.olb', 'shared.fmb'))
        self.rejected("UNSUPPORTED_PARENT_FILE", source=source)

    def test_repeated_mmb_argument_is_not_silently_overwritten(self):
        menu = self.write("nav_mmb.xml", '<MenuModule Name="NAV"/>')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            self.run_cli("--mmb", menu, "--mmb", menu)
        self.assertEqual(raised.exception.code, 2)
        self.assertFalse((self.root / "result").exists())

    def test_identical_bytes_across_runs_relocated_inputs_and_reordered_flags(self):
        alpha, zeta = self.library("alpha_olb.xml"), self.library("zeta_olb.xml")
        archives = []
        for folder, libraries in [("first", [zeta, alpha]), ("second", [alpha, zeta])]:
            source_dir = self.root / folder / "source"
            source_dir.mkdir(parents=True)
            for source in (self.form, alpha, zeta):
                shutil.copyfile(source, source_dir / source.name)
            options = [part for library in libraries for part in ("--olb", source_dir / library.name)]
            code, output, _, stderr = self.run_cli(*options, "--zip", source=source_dir / self.form.name, out=folder + "/module")
            self.assertEqual(code, 0, stderr)
            archives.append(output.with_suffix(".zip").read_bytes())
        self.assertEqual(archives[0], archives[1])

    def test_companions_do_not_change_any_generated_java_or_angular_source(self):
        library = self.library()
        menu = self.write("nav_mmb.xml", '<MenuModule Name="NAV"/>')
        outputs = []
        for out, arguments in [("plain", []), ("companions", ["--olb", library, "--mmb", menu])]:
            code, output, _, stderr = self.run_cli(*arguments, "--schema", ROOT / "examples/schema.json",
                                                   source=ROOT / "examples/customer_fmb.xml", out=out)
            self.assertEqual(code, 0, stderr)
            outputs.append({file.relative_to(output).as_posix(): file.read_bytes()
                            for folder in ("backend", "frontend") for file in (output / folder).rglob("*") if file.is_file()})
        self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()

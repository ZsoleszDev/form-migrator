import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from frm_forms.cli import main
from frm_forms.angular_ui import generate
from frm_forms.ui_schema import validate_ui_model
from frm_forms.common import MigrationError
ROOT=Path(__file__).resolve().parents[1]

class UiPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def run_ui(self,items='<Item Name="FIELD" ItemType="Text Item"/>', *, attrs='', block='', extra='', config=None, args=(), label='out'):
        src=self.root/'form_fmb.xml';src.write_text(f'<FormModule Name="FORM" Title="Teszt" CoordinateSystem="Real" RealUnit="Decipoint" {attrs}><Block Name="B" {block}>{items}</Block>{extra}</FormModule>')
        argv=['migrate',str(src),'--out',str(self.root/label),*args]
        if config:
            p=self.root/'config.json';p.write_text(json.dumps(config));argv+=['--config',str(p)]
        err=io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(err): code=main(argv)
        out=self.root/label
        model=json.loads((out/'analysis/ui-model.json').read_text()) if out.exists() and (out/'analysis/ui-model.json').exists() else None
        return code,out,model,err.getvalue()
    def good(self,*args,**kwargs):
        result=self.run_ui(*args,**kwargs);self.assertEqual(result[0],0,result[3]);return result[1:3]
    def bad(self,expected,*args,**kwargs):
        code,out,_,err=self.run_ui(*args,**kwargs);self.assertEqual(code,1,err);self.assertFalse(out.exists());self.assertIn(expected,err)
    def test_3_inference_comment_and_checkbox_required_mapping_tooltip(self):
        out,m=self.good('<Item Name="UBI_P01" CheckedValue="1" UncheckedValue="0" Required="true" InitializeValue="0" Hint="Kézi rögzített adatlapok."/>')
        item=m['blocks'][0]['items'][0]
        self.assertEqual(item['widget'],'checkbox');self.assertTrue(item['validation']['required']);self.assertEqual(item['initial_value'],'0')
        self.assertEqual(item['checkbox']['unchecked'],'0');self.assertEqual(m['i18n'][item['text_keys']['hint']],'Kézi rögzített adatlapok.')
        self.assertIn('type_source=inferred',(out/'frontend/teszt/blocks/b/form-structure.ts').read_text())
        self.assertIn('tooltipData:',(out/'frontend/teszt/form-structure.ts').read_text())
    def test_3_ambiguous_rejects_no_partial_output(self):
        self.bad('UNSUPPORTED_ITEM','<Item Name="F" CheckedValue="1" UncheckedValue="0"><RadioButton Label="x" Value="1"/></Item>',args=['--zip'])
        self.assertFalse((self.root/'out.zip').exists())
    def test_4_unknown_property_reject_and_explicit_alias(self):
        self.bad('UNMAPPED_ATTRIBUTE','<Item Name="F" ItemType="Text Item" VersionMax="10"/>')
        _,m=self.good('<Item Name="F" ItemType="Text Item" VersionMax="10"/>',config={'property_aliases':{'Item':{'VersionMax':'MaximumLength'}}})
        self.assertEqual(m['blocks'][0]['items'][0]['validation']['maximum_length'],10)
    def test_5_all_validation_fields_and_data_map(self):
        _,m=self.good('<Item Name="F" ItemType="Text Item" Required="true" MaximumLength="5" FixedLength="true" CaseRestriction="Upper"/><Item Name="N" ItemType="Text Item" DataType="Number" Precision="8" Scale="0" LowestAllowedValue="0" HighestAllowedValue="999"/><Item Name="D" ItemType="Text Item" DataType="Date" FormatMask="YYYY-MM-DD"/>')
        f,n,d=m['blocks'][0]['items'];self.assertEqual(f['validation']['fixed_length'],5);self.assertEqual(n['representation'],'safe-integer');self.assertEqual(n['validation']['maximum'],'999');self.assertEqual(d['widget'],'date')
        self.assertEqual(m['rendering']['widget_map']['widgets']['checkbox']['type'],'checkBox')
    def test_5_unsupported_validation_never_silently_disappears(self):
        self.bad('UNSUPPORTED_FORMAT_MASK','<Item Name="F" ItemType="Text Item" FormatMask="999G999D99"/>')
    def test_5_invalid_widget_map(self):
        p=self.root/'map.json';p.write_text('{"version":1,"contract":"x","widgets":{}}')
        self.bad('WIDGET_MAP',config={'widget_map':str(p)})
    def test_6_coordinates_grid_canvas_tab_and_table(self):
        items='<Item Name="RIGHT" ItemType="Push Button" CanvasName="C" TabPageName="T" XPosition="300" YPosition="0" Width="300" Height="20"/><Item Name="LEFT" ItemType="Text Item" CanvasName="C" TabPageName="T" XPosition="0" YPosition="0" Width="300" Height="20"/>'
        out,m=self.good(items,block='QueryDataSourceName="DATA" NumberOfRecordsDisplayed="5"',extra='<Canvas Name="C"><TabPage Name="T" Label="Adatok"/></Canvas>')
        b=m['blocks'][0];self.assertEqual(b['mode'],'table');self.assertEqual(b['regions'][0]['items'],['left','right']);self.assertEqual(b['items'][0]['layout']['col'],6)
        self.assertEqual(m['module']['coordinates']['units_per_inch'],'720');self.assertEqual(b['regions'][0]['layout']['source_columns'],2)
        self.assertIn('<p-table ',(out/'frontend/teszt/blocks/b.component.ts').read_text())
    def test_6_invalid_record_count_geometry_and_coordinate(self):
        self.bad('INVALID_INTEGER',block='NumberOfRecordsDisplayed="0"')
        self.bad('INCOMPLETE_COORDINATES','<Item Name="F" ItemType="Text Item" XPosition="0"/>')
        src=self.root/'missing.xml';src.write_text('<FormModule Name="X"><Block Name="B"><Item Name="X" ItemType="Text Item"/></Block></FormModule>')
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()): code=main(['migrate',str(src),'--out',str(self.root/'missing')])
        self.assertEqual(code,1);self.assertFalse((self.root/'missing').exists())
    def test_7_options_and_other_mapping(self):
        _,m=self.good('<Item Name="S" ItemType="List Item"><ListItemElement Label="Egy" Value="1"/><ListItemElement Label="Kettő" Value="2"/></Item><Item Name="R" ItemType="Radio Group"><RadioButton Label="Igen" RadioButtonValue="Y"/><RadioButton Label="Nem" RadioButtonValue="N"/></Item><Item Name="C" ItemType="Check Box" CheckedValue="1" UncheckedValue="0" MappingOfOtherValues="Unchecked"/>')
        s,r,c=m['blocks'][0]['items'];self.assertEqual(len(s['options']),2);self.assertEqual(r['options'][0]['value'],'Y');self.assertEqual(c['checkbox']['other_values'],'unchecked')
    def test_7_lov_declaration_and_original_sql_no_backend_implementation(self):
        out,m=self.good('<Item Name="S" ItemType="Text Item" LOVName="LOOK"/>',extra='<LOV Name="LOOK" RecordGroupName="RG"><LOVColumnMapping Name="M" ColumnName="CODE" ReturnItem="B.S" Title="Kód"/></LOV><RecordGroup Name="RG" RecordGroupQuery="SELECT CODE FROM T WHERE X=:B.S"/>')
        lov=m['blocks'][0]['items'][0]['lov'];self.assertEqual(m['blocks'][0]['items'][0]['widget'],'autocomplete')
        self.assertEqual((out/lov['sql_file']).read_text(),'SELECT CODE FROM T WHERE X=:B.S\n');self.assertIsNone(m['endpoints'][0]['path']);self.assertTrue(m['endpoints'][0]['declaration_only'])
        # Default: one generated LOV endpoint runs the unchanged query with the Forms bind as a typed parameter.
        service=(out/'backend/DPS/TesztServiceImpl.java').read_text()
        self.assertIn('SELECT CODE FROM T WHERE X=:p0',service);self.assertIn('{"p0", "B.S", "text"}',service)
    def test_7_lov_backend_endpoints_can_be_switched_off(self):
        out,_=self.good('<Item Name="S" ItemType="Text Item" LOVName="LOOK"/>',extra='<LOV Name="LOOK" RecordGroupName="RG"><LOVColumnMapping Name="M" ColumnName="CODE" ReturnItem="B.S" Title="Kód"/></LOV><RecordGroup Name="RG" RecordGroupQuery="SELECT CODE FROM T WHERE X=:B.S"/>',config={'backend_lov_endpoints':False})
        self.assertFalse(any('SELECT CODE FROM T' in p.read_text() for p in (out/'backend').rglob('*.java')))
    def test_7_missing_lov_and_duplicate_options_fail(self):
        self.bad('MISSING_LOV','<Item Name="S" ItemType="Text Item" LOVName="MISSING"/>')
        self.bad('DUPLICATE_OPTION','<Item Name="S" ItemType="List Item"><ListItemElement Label="A" Value="1"/><ListItemElement Label="B" Value="1"/></Item>')
    def test_8_regeneration_preserves_shells_overwrites_managed_files(self):
        out,_=self.good();shell=out/'frontend/teszt/component.ts';html=out/'frontend/teszt/component.html';struct=out/'frontend/teszt/form-structure.ts'
        shell.write_text('// custom TS\n');html.write_text('<!-- custom -->\n');struct.write_text('// stale\n')
        with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(main(['migrate',str(self.root/'form_fmb.xml'),'--out',str(out),'--regenerate']),0)
        self.assertEqual(shell.read_text(),'// custom TS\n');self.assertEqual(html.read_text(),'<!-- custom -->\n');self.assertNotEqual(struct.read_text(),'// stale\n')
        manifest=json.loads((out/'generated-files.json').read_text());self.assertEqual(next(f for f in manifest['files'] if f['path'].endswith('/component.ts'))['overwrite_policy'],'create_once')
    def test_8_failed_regeneration_keeps_every_previous_byte(self):
        out,_=self.good();before={str(p.relative_to(out)):p.read_bytes() for p in out.rglob('*') if p.is_file()}
        src=self.root/'form_fmb.xml';src.write_text('<FormModule Name="X"/>')
        with contextlib.redirect_stderr(io.StringIO()):self.assertEqual(main(['migrate',str(src),'--out',str(out),'--regenerate']),1)
        self.assertEqual(before,{str(p.relative_to(out)):p.read_bytes() for p in out.rglob('*') if p.is_file()})
    def test_9_renderer_only_uses_validated_model(self):
        out,m=self.good();(self.root/'form_fmb.xml').unlink()
        generate(m,self.root/'independent')
        self.assertEqual((out/'frontend/teszt/component.ts').read_bytes(),(self.root/'independent/frontend/teszt/component.ts').read_bytes())
        invalid=copy.deepcopy(m);invalid['schema_version']='999';
        with self.assertRaises(MigrationError):generate(invalid,self.root/'bad')
        self.assertFalse((self.root/'bad').exists())
    def test_9_schema_rejects_wrong_nested_types_and_unknown_keys(self):
        _,m=self.good();m['blocks'][0]['items'][0]['validation']['required']='yes'
        with self.assertRaises(MigrationError):validate_ui_model(m)
        m['blocks'][0]['items'][0]['validation']['required']=False;m['surprise']=1
        with self.assertRaises(MigrationError):validate_ui_model(m)
    def test_10_rejected_analysis_names_unsupported_canvas_and_no_source(self):
        code,out,m,err=self.run_ui('<Item Name="TREE" ItemType="Hierarchical Tree" CanvasName="MAIN"/>',extra='<Canvas Name="MAIN"/>',args=['--analysis-only'])
        self.assertEqual(code,3,err);self.assertFalse((out/'frontend').exists());self.assertFalse((out/'backend').exists())
        report=(out/'migration-report.md').read_text();self.assertIn('B.TREE',report);self.assertIn('MAIN',report)
    def test_10_report_inference_counts_and_endpoint_list(self):
        out,m=self.good('<Item Name="C" CheckedValue="1" UncheckedValue="0"/>',block='QueryDataSourceName="T"')
        report=(out/'migration-report.md').read_text();self.assertIn('heurisztikából: 1/1',report);self.assertIn('b_read',report)
    def test_same_input_byte_identical_and_reject_before_java_ai(self):
        out,m=self.good();first={str(p.relative_to(out)):p.read_bytes() for p in out.rglob('*') if p.is_file()}
        other,_=self.good(label='other');self.assertEqual(first,{str(p.relative_to(other)):p.read_bytes() for p in other.rglob('*') if p.is_file()})
        with patch('frm_forms.cli.generate_java') as java, patch('frm_forms.cli.advise') as ai:
            self.bad('UNSUPPORTED_ITEM','<Item Name="F"/>',label='bad')
            java.assert_not_called();ai.assert_not_called()

    def test_strict_inheritance_rejects_transitive_missing_olb(self):
        lib=self.root/'provided_olb.xml';lib.write_text('<ObjectLibrary Name="PROVIDED"><Item Name="BASE" ParentFilename="missing.olb" ParentName="ROOT"/></ObjectLibrary>')
        code,out,model,err=self.run_ui('<Item Name="F" ParentFilename="provided.olb" ParentName="BASE"/>',
                                       args=['--olb',str(lib),'--analysis-only','--strict-inheritance'])
        self.assertEqual(code,3,err);self.assertIsNone(model);self.assertFalse((out/'frontend').exists())
        data=json.loads((out/'analysis/input-issues.json').read_text());issue=data['issues'][0]
        self.assertEqual(issue['filename'],'missing.olb');self.assertEqual(issue['affected_items'],1)
        self.assertIn('missing.olb',(out/'migration-report.md').read_text())

    def test_table_import_cannot_be_guessed(self):
        config={'emit_imports':True,'optimus_import_path':'@test/ui','optimus_form_block_symbol':'TestForm',
                'form_block_type_import_path':'@test/types','environment_import_path':'@test/env'}
        self.bad('TABLE_IMPORT_REQUIRED',block='QueryDataSourceName="T" NumberOfRecordsDisplayed="2"',config=config)

    def test_character_and_real_units_same_relative_widths(self):
        items='<Item Name="L" ItemType="Text Item" XPosition="0" YPosition="0" Width="10"/><Item Name="R" ItemType="Text Item" XPosition="10" YPosition="0" Width="10"/>'
        out,m=self.good(items);src=self.root/'form_fmb.xml'
        src.write_text(src.read_text().replace('CoordinateSystem="Real" RealUnit="Decipoint"','CoordinateSystem="Character" CharacterCellWidth="8" CharacterCellHeight="16"'))
        with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(main(['migrate',str(src),'--out',str(self.root/'char')]),0)
        other=json.loads((self.root/'char/analysis/ui-model.json').read_text());self.assertEqual(other['module']['coordinates']['system'],'character')
        self.assertEqual([i['layout']['col'] for i in m['blocks'][0]['items']],[i['layout']['col'] for i in other['blocks'][0]['items']])

    def test_image_remains_explicit_adapter_todo(self):
        out,m=self.good('<Item Name="PHOTO" ItemType="Image"/>')
        self.assertEqual(m['blocks'][0]['items'][0]['widget'],'image')
        self.assertTrue(any(i['code']=='IMAGE_ADAPTER_TODO' for i in m['issues']))

    def test_bad_inventory_and_invalid_schema_do_not_publish(self):
        broken=self.root/'bad.xml';broken.write_text('<bad>')
        with contextlib.redirect_stderr(io.StringIO()):self.assertEqual(main(['inventory',str(broken),'--out',str(self.root/'inventory')]),1)
        self.assertFalse((self.root/'inventory').exists())

    def test_regeneration_archive_failure_rolls_back_folder_and_zip(self):
        from frm_forms.publication import publish
        old=self.root/'old';old.mkdir();(old/'manual').write_text('previous')
        archive=self.root/'old.zip';archive.write_bytes(b'old zip')
        stage=self.root/'stage';stage.mkdir();bundle=stage/'bundle';bundle.mkdir();(bundle/'new').write_text('new')
        with self.assertRaises(FileNotFoundError):publish(bundle,old,True,stage/'missing.zip',archive)
        self.assertEqual((old/'manual').read_text(),'previous');self.assertEqual(archive.read_bytes(),b'old zip');self.assertFalse((old/'new').exists())

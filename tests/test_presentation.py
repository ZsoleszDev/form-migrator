import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from frm_forms.cli import main
from frm_forms.presentation import output_name
ROOT=Path(__file__).resolve().parents[1]

class PresentationTests(unittest.TestCase):
    def generate(self,config=None,extra=()):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); root=Path(temp.name)
        args=['migrate',str(ROOT/'examples/vasarlo-lekerdezo_fmb.xml'),'--out',str(root/'out'),*extra]
        if config:
            p=root/'config.json';p.write_text(json.dumps(config));args+=['--config',str(p)]
        with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(main(args),0)
        return root/'out', json.loads((root/'out/analysis/ui-model.json').read_text())
    def test_title_controls_folder_class_selector(self):
        out,model=self.generate();src=(out/'frontend/vasarloLekerdezo/component.ts').read_text()
        self.assertIn('class VasarloLekerdezoComponent',src); self.assertIn('app-vasarlo-lekerdezo',src)
        self.assertEqual(model['module']['key'],'vasarloLekerdezo')
    def test_api_override_does_not_change_frontend_title(self):
        out,model=self.generate(extra=['--module','legacy'])
        self.assertTrue((out/'frontend/vasarloLekerdezo/component.ts').exists())
        self.assertTrue(all(e['path'].startswith('/api/forms/legacy/') for e in model['endpoints']))
    def test_no_calendar_name_guess_discards_source_items(self):
        _,model=self.generate();b=next(b for b in model['blocks'] if b['name']=='CALENDAR')
        self.assertEqual(len(b['items']),42);self.assertTrue(all(i['widget']=='button' for i in b['items']))
    def test_p_table_and_modern_control_flow(self):
        out,model=self.generate();src=(out/'frontend/vasarloLekerdezo/blocks/results.component.ts').read_text()
        self.assertIn('<p-table [value]="state.rows"',src);self.assertIn('@for (item of items; track item.key)',src)
        self.assertEqual(next(b for b in model['blocks'] if b['name']=='RESULTS')['mode'],'table')
    def test_same_contract_selector_is_editable(self):
        out,_=self.generate(config={'html_selectors':{'table':'company-table','form_block':'company-form'}})
        src=(out/'frontend/vasarloLekerdezo/blocks/results.component.ts').read_text()
        self.assertIn('<company-table ',src);self.assertIn('<company-form ',src)
    def test_host_contract_i18n_and_widgets(self):
        out,model=self.generate();items=model['blocks'][0]['items'];kinds={i['widget'] for i in items}
        self.assertTrue({'checkbox','select','textarea','password','datetime','button'}<=kinds)
        src='\n'.join(p.read_text() for p in (out/'frontend').rglob('*.ts'))
        self.assertNotRegex(src,r'(?m)^import |\*ng(?:If|For)'); self.assertIn('type_source=inferred',src)
        self.assertIn('environment.baseUrl',src);self.assertNotIn('Vásárló neve',src)
    def test_safe_title_normalization(self):
        self.assertEqual(output_name({'title':'Vásárló lekérdező','name':'OLD'}),'vasarloLekerdezo')
        self.assertEqual(output_name({'title':'class','name':'OLD'}),'classValue')
        self.assertLessEqual(len(output_name({'title':'Hosszú név '*100,'name':'OLD'})),80)

"""Portfolio run, Designer exception handlers, DB routine calls, data dictionary, LOV endpoints."""
from java_support import trigger_mode
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from frm_forms.backend_lovs import lov_plans
from frm_forms.cli import main
from frm_forms.dictionary import dictionary_import, dictionary_sql
from frm_forms import framework
from frm_forms.plsql import Unsupported, flatten, parse
from frm_forms.portfolio import normalize
from frm_forms.rules import analyze, strip_framework
from frm_forms.xmlmodel import parse_xml

setUpModule, tearDownModule = trigger_mode('plsql')


NL = '&amp;#10;'
CATALOG = framework.load({})


def form(body, name='F'):
    return ('<Module><FormModule Name="' + name + '"><Coordinate CoordinateSystem="Real" RealUnit="Pixel"/>' + body
            + '<Canvas Name="P" CanvasType="Content" WindowName="W"/><Window Name="W"/></FormModule></Module>')


def block(triggers='', extra_items=''):
    return ('<Block Name="B" DatabaseDataBlock="true" QueryDataSourceName="T_B">'
            '<Item ItemType="Text Item" Name="ID" DataType="Number" PrimaryKey="true"/>'
            '<Item ItemType="Text Item" Name="KOD" DataType="Char" MaximumLength="10"/>'
            '<Item ItemType="Text Item" Name="NEV" DataType="Char" MaximumLength="80" DatabaseItem="false"/>'
            + extra_items + triggers + '</Block>')


SIGNATURES = {'KOD_PKG.GET_NEV': {'kind': 'function', 'returns': 'text', 'arguments': [{'name': 'P_KOD', 'mode': 'IN', 'type': 'text'}]},
              'KOD_PKG.CHECK': {'kind': 'procedure', 'arguments': [
                  {'name': 'P_KOD', 'mode': 'IN', 'type': 'text'}, {'name': 'P_NEV', 'mode': 'OUT', 'type': 'text'},
                  {'name': 'P_FORCE', 'mode': 'IN', 'type': 'text', 'default': True}]}}


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def model(self, xml, schema=None):
        path = self.root / 'input.xml'; path.write_text(xml, encoding='utf-8')
        model = parse_xml(path); analyze(model, schema or {}, {})
        return model

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    # Designer exception sections and qualified names
    def test_framework_exception_handler_is_dropped_but_a_swallowing_one_is_refused(self):
        ast = parse('BEGIN pkg.proc(:B.X); EXCEPTION WHEN OTHERS THEN cgte$other_exceptions; END;')
        self.assertEqual(ast[0]['body'][0]['name'], 'PKG.PROC')
        with self.assertRaisesRegex(Unsupported, 'Saját kivételkezelő'):
            list(flatten(ast))
        stripped, removed = strip_framework(ast, CATALOG)
        self.assertEqual(stripped[0]['handlers'], [])
        self.assertTrue(any('exception when others' in r for r in removed))
        rethrow, _ = strip_framework(parse('BEGIN NULL; EXCEPTION WHEN NO_DATA_FOUND THEN RAISE FORM_TRIGGER_FAILURE; END;'), CATALOG)
        self.assertEqual(rethrow[0]['handlers'], [])
        swallow, _ = strip_framework(parse('BEGIN NULL; EXCEPTION WHEN OTHERS THEN NULL; END;'), CATALOG)
        self.assertEqual(len(swallow[0]['handlers']), 1)

    # DB routine calls
    def test_reviewed_signatures_compile_db_procedures_and_functions(self):
        triggers = ('<Trigger Name="POST-QUERY" TriggerText="BEGIN' + NL + ':B.NEV := kod_pkg.get_nev(:B.KOD);' + NL
                    + 'EXCEPTION WHEN OTHERS THEN cgte$other_exceptions; END;"/>'
                      '<Trigger Name="PRE-INSERT" TriggerText="kod_pkg.check(:B.KOD, :B.NEV);"/>')
        model = self.model(form(block(triggers)), {'blocks': {'B': {'writable': True}}, 'procedures': SIGNATURES})
        post, pre = model['triggers']
        self.assertEqual((post['status'], pre['status']), ('converted', 'converted'))
        self.assertIn('DbCalls.function(jdbc, "{? = call KOD_PKG.GET_NEV(?)}", java.sql.Types.VARCHAR', post['java'])
        self.assertIn('{call KOD_PKG.CHECK(?, ?)}', pre['java'])  # the defaulted third parameter is omitted
        self.assertIn('row.nev = (String) out[1];', pre['java'])
        self.assertTrue(model['blocks'][0]['can_create'])

    def test_db_calls_without_signature_or_with_wrong_shape_stay_blocked(self):
        # other_pkg.run without signature runs through the database now (test_plsql_and_states).
        cases = {'kod_pkg.check(:B.KOD);': 'nem egyezik',
                 "go_item('B.KOD');": 'Forms beépített hívás',
                 "kod_pkg.check(:B.KOD, 'x');": 'csak blokkmező'}
        for source, reason in cases.items():
            with self.subTest(source=source):
                trigger = self.model(form(block('<Trigger Name="PRE-INSERT" TriggerText="' + source.replace("'", '&apos;') + '"/>')),
                                     {'procedures': SIGNATURES})['triggers'][0]
                self.assertEqual(trigger['status'], 'review')
                self.assertIn(reason, trigger['reason'])

    def test_out_parameter_counts_as_a_write_for_the_post_query_guard(self):
        trigger = self.model(form(block('<Trigger Name="POST-QUERY" TriggerText="kod_pkg.check(:B.KOD, :B.KOD);"/>')),
                             {'procedures': SIGNATURES})['triggers'][0]
        self.assertIn('POST-QUERY nem írhat adatbázismezőt', trigger['reason'])

    # Data dictionary
    def test_dictionary_sql_and_import_round_trip(self):
        sql, skipped = dictionary_sql({'T_B', 'APP.T_C', 'bad name'}, {'KOD_PKG.GET_NEV', 'APP.PKG.RUN', 'STANDALONE'})
        self.assertIn("c.table_name IN ('T_B')", sql)
        self.assertIn("(c.owner, c.table_name) IN (('APP', 'T_C'))", sql)
        self.assertIn("(a.owner = 'KOD_PKG' AND a.package_name IS NULL AND a.object_name = 'GET_NEV')", sql)
        self.assertEqual(skipped, ['bad name'])
        export = '\n'.join(['COL|APP|T_B|ID|NUMBER|22|10|0|N|1', 'COL|APP|T_B|KOD|VARCHAR2|10|||Y|2', 'PK|APP|T_B|ID|1',
                            'ARG|APP|KOD_PKG|GET_NEV|0|0|-|OUT|VARCHAR2|N|0', 'ARG|APP|KOD_PKG|GET_NEV|0|1|P_KOD|IN|VARCHAR2|N|0',
                            'ARG|APP|PKG|OVER|1|1|P|IN|NUMBER|N|0', 'ARG|APP|PKG|OVER|2|1|P|IN|VARCHAR2|N|0',
                            'ARG|APP|-|REFRESH|0|1|-|IN||N|0', 'ARG|OTHER|-|REFRESH|0|1|-|IN||N|0', 'noise line'])
        imported, report = dictionary_import(export)
        self.assertEqual(imported['tables']['APP.T_B']['primary_key'], ['ID'])
        self.assertEqual(imported['procedures']['KOD_PKG.GET_NEV']['returns'], 'text')
        self.assertEqual(imported['procedures']['APP.REFRESH']['arguments'], [])
        self.assertEqual((report['overloaded'], report['ambiguous']), (['APP.PKG.OVER'], ['REFRESH']))
        self.assertNotIn('REFRESH', imported['procedures'])
        (self.root / 'dictionary.txt').write_text(export, encoding='utf-8')
        (self.root / 'reviewed.json').write_text(json.dumps({'blocks': {'B': {'writable': True}},
                                                             'procedures': {'KOD_PKG.GET_NEV': SIGNATURES['KOD_PKG.GET_NEV']}}))
        code, _, _ = self.run_cli('dictionary-import', str(self.root / 'dictionary.txt'), '--out', str(self.root / 'schema.json'),
                                  '--merge', str(self.root / 'reviewed.json'))
        schema = json.loads((self.root / 'schema.json').read_text(encoding='utf-8'))
        self.assertEqual(code, 0)
        self.assertEqual(schema['blocks'], {'B': {'writable': True}})
        self.assertNotIn('source', schema['procedures']['KOD_PKG.GET_NEV'])  # the reviewed entry wins

    def test_dictionary_key_and_columns_are_used_only_when_the_form_lacks_them(self):
        tables = {'APP.T_B': {'primary_key': ['ID'], 'columns': {'ID': {}, 'KOD': {}}}}
        model = self.model(form(block().replace(' PrimaryKey="true"', '')), {'tables': tables})
        b = model['blocks'][0]
        self.assertEqual(([i['column'] for i in b['pk']], b['primary_key_source']), (['ID'], 'dictionary'))
        self.assertFalse([i for i in model['issues'] if i['code'] == 'NO_PRIMARY_KEY'])
        wrong = self.model(form(block(extra_items='<Item ItemType="Text Item" Name="ELIRT" DataType="Char"/>')), {'tables': tables})
        self.assertTrue(any(i['code'] == 'SQL_MAPPING' and 'ELIRT' in i['detail'] for i in wrong['issues']))

    # LOV endpoints
    def test_lov_plans_rewrite_binds_and_refuse_server_context(self):
        body = ('<Block Name="F" DatabaseDataBlock="false">'
                '<Item ItemType="Text Item" Name="ORSZAG" DataType="Char" LOVName="ORSZAGOK"/>'
                '<Item ItemType="Text Item" Name="VAROS" DataType="Char" LOVName="VAROSOK"/>'
                '<Item ItemType="Text Item" Name="EV" DataType="Number" LOVName="EVEK"/>'
                '<Item ItemType="Text Item" Name="NAP" DataType="Date" LOVName="CAL">'
                '<Trigger Name="KEY-LISTVAL" TriggerText="qms$calendar.key_listval;"/></Item></Block>'
                '<LOV Name="ORSZAGOK" RecordGroupName="RG_O"><LOVColumnMapping ColumnName="KOD" DisplayWidth="0"/>'
                '<LOVColumnMapping ColumnName="NEV" DisplayWidth="100"/></LOV>'
                '<LOV Name="VAROSOK" RecordGroupName="RG_V"/><LOV Name="EVEK" RecordGroupName="RG_E"/><LOV Name="CAL" RecordGroupName="RG_C"/>'
                '<RecordGroup Name="RG_O" RecordGroupQuery="SELECT KOD, NEV FROM ORSZAG -- aktív' + NL + 'ORDER BY NEV"/>'
                                                                                                         '<RecordGroup Name="RG_V" RecordGroupQuery="SELECT NEV FROM VAROS WHERE ORSZAG = :F.ORSZAG AND NEV &lt;&gt; &apos;:F.VAROS&apos;"/>'
                                                                                                         '<RecordGroup Name="RG_E" RecordGroupQuery="SELECT EV FROM EVEK WHERE CEG = :GLOBAL.CEG"/>'
                                                                                                         '<RecordGroup Name="RG_C" RecordGroupQuery="SELECT SYSDATE FROM DUAL"/>')
        path = self.root / 'lov.xml'; path.write_text(form(body), encoding='utf-8')
        code, _, err = self.run_cli('migrate', str(path), '--screen', '--module', 'lovtest', '--out', str(self.root / 'out'))
        self.assertEqual(code, 0, err)
        model = json.loads((self.root / 'out/analysis/form.ir.json').read_text(encoding='utf-8'))
        discovery = json.loads((self.root / 'out/analysis/discovery/form-map.json').read_text(encoding='utf-8'))
        plans = {p['name']: p for p in lov_plans(model, discovery, CATALOG)}
        self.assertNotIn('CAL', plans)  # replaced by the native date picker
        self.assertEqual(plans['ORSZAGOK']['display_column'], 'NEV')
        self.assertIn('-- aktív\nORDER BY NEV\n) lov', plans['ORSZAGOK']['sql'])
        self.assertIn("ORSZAG = :p0 AND NEV <> ':F.VAROS'", plans['VAROSOK']['sql'])
        self.assertEqual(plans['VAROSOK']['binds'], [{'parameter': 'p0', 'source': 'F.ORSZAG', 'type': 'text'}])
        self.assertIn(':GLOBAL.CEG', plans['EVEK']['blockers'][0])
        service = (self.root / 'out/backend/DPS/LovtestServiceImpl.java').read_text(encoding='utf-8')
        self.assertIn('public LovResult lovOrszagok(UserDto user, LovRequest request) throws Exception', service)
        flat = ' '.join(service.split())  # Checkstyle layout: braces and one statement per line
        self.assertIn('if (!MODULE_REVIEWED) { throw', flat)
        self.assertIn('if (!false) { throw', flat)  # EVEK: blocked by its own reason
        self.assertIn('LOV_VAROSOK_PATH = "/lov/varosok"', (self.root / 'out/backend/CL/LovtestConstants.java').read_text(encoding='utf-8'))

    # Portfolio
    def test_batch_ranks_reasons_across_forms_and_survives_a_broken_form(self):
        corpus = self.root / 'corpus'; corpus.mkdir()
        trigger = '<Trigger Name="PRE-INSERT" TriggerText="go_item(&apos;B.KOD&apos;); other_pkg.run(:B.KOD);"/>'
        for name in ('EGY', 'KETTO'):
            (corpus / (name + '_fmb.xml')).write_text(form(block(trigger), name), encoding='utf-8')
        (corpus / 'ROSSZ_fmb.xml').write_text('<Module><FormModule Name="R"', encoding='utf-8')
        (corpus / 'lib_olb.xml').write_text('<Module><ObjectLibrary Name="L"/></Module>', encoding='utf-8')
        out = self.root / 'portfolio'
        code, _, _ = self.run_cli('batch', str(corpus), '--out', str(out))
        self.assertEqual(code, 2)  # one form failed, the rest is still generated and reported
        report = json.loads((out / 'portfolio.json').read_text(encoding='utf-8'))
        self.assertEqual((report['totals']['forms'], report['totals']['failed']), (3, 1))
        top = next(r for r in report['endpoint_blockers'] if 'Forms beépített hívás: GO_ITEM' in r['reason'])
        self.assertEqual(len(top['forms']), 2)
        self.assertEqual(report['external_routines'][0]['routine'], 'OTHER_PKG.RUN')
        self.assertIn('INVALID_XML', next(f for f in report['forms'] if f['status'] == 'failed')['error'])
        self.assertTrue((out / 'portfolio-forms.csv').read_text(encoding='utf-8').startswith('\ufefffolder;form;status'))
        self.assertIn('## Mit érdemes először javítani', (out / 'PORTFOLIO_HU.md').read_text(encoding='utf-8'))
        code, _, _ = self.run_cli('dictionary-sql', str(out), '--out', str(self.root / 'dictionary.sql'))
        self.assertEqual(code, 0)
        self.assertIn("a.object_name = 'RUN'", (self.root / 'dictionary.sql').read_text(encoding='utf-8'))

    def test_reason_normalization_groups_messages(self):
        self.assertEqual(normalize("B:PRE-INSERT: Nem támogatott token a(z) 12. karakternél: '&#10;BEGIN x'"),
                         "Nem támogatott token a(z) N. karakternél: '&'…")
        self.assertEqual(normalize('Írás nincs engedélyezve a schema.json-ban (writable: true).'),
                         'Írás nincs engedélyezve a schema.json-ban (writable: true).')
        self.assertEqual(normalize("Másik blokk: :GLOBAL.X és 'szöveg' (3 db)"), "Másik blokk: :<bind> és '…' (N db)")


if __name__ == '__main__':
    unittest.main()

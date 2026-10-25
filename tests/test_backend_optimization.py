import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from frm_forms.cli import main
from frm_forms.xmlmodel import parse_xml
from frm_forms.rules import analyze
from frm_forms.common import MigrationError
from frm_forms.backend_queries import QueryCompiler
from frm_forms.plsql import Unsupported
from java_support import COMPANY_IMPORTS, write_stubs

ROOT=Path(__file__).resolve().parents[1]


class BackendOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def source(self): return ET.parse(ROOT/'examples/backend-query_fmb.xml').getroot()

    def model(self, root=None):
        path=self.root/'input.xml'
        path.write_bytes(ET.tostring(root if root is not None else self.source()))
        model=parse_xml(path); analyze(model,{},{}); return model

    def generate(self, extra=()):
        out=self.root/'out'
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            result=main(['migrate',str(ROOT/'examples/backend-query_fmb.xml'),'--module','queryDemo','--out',str(out),*extra])
        self.assertEqual(result,0)
        return out

    def test_bound_search_and_relation_preserve_constraints(self):
        m=self.model(); header,lines,_=m['blocks']
        self.assertTrue(header['can_read']); self.assertFalse(lines['can_read']); self.assertTrue(lines['can_search'])
        self.assertFalse(any(b['can_update'] or b['can_delete'] for b in m['blocks']))
        q=lines['query_plan']
        self.assertIn('HEADER_ID = :q2',q['where_sql'])
        self.assertIn('AMOUNT >= :q0',q['where_sql'])
        self.assertIn("CODE LIKE NVL(:q1, '%')",q['where_sql'])
        self.assertEqual(q['order_sql'],'AMOUNT DESC, ID ASC NULLS LAST')
        self.assertEqual([v['source'] for v in q['binds']],['FILTERS.MIN_AMOUNT','FILTERS.PREFIX','HEADER.ID'])
        self.assertEqual(m['relation_contexts'][0]['master_source'],'parent_block')

    def test_no_partial_query_translation_or_unknown_context(self):
        for bad in ["ID = 1; DELETE FROM T_LINES", "ID IN (SELECT ID FROM SECRET)", "PKG.MUTATE(ID) = 1", "ID = :GLOBAL.ID", "ID = :SYSTEM.CURSOR_RECORD", "ID = :PARAMETER.ID", "ID = :UNKNOWN.ID", "ID = '1'", "UNKNOWN = 1", 'OTHER.ID=1', "ID = 1 FOR UPDATE", "ID=1 /*unterminated", "ID = 1 UNION SELECT 1 FROM DUAL"]:
            with self.subTest(bad=bad):
                root=self.source(); root.find('.//Block[@Name="LINES"]').set('WhereClause',bad)
                m=self.model(root); b=m['blocks'][1]
                self.assertEqual(b['query_plan']['status'],'review');self.assertFalse(b['can_search']);self.assertFalse(b['can_read'])

    def test_sql_subset_quotes_ranges_null_and_negation(self):
        m=self.model(); compiler=QueryCompiler(m,m['blocks'][1])
        for sql in ["AMOUNT BETWEEN 1.25 AND 10", "ID NOT IN (1,2,NULL)", "NOT (ID = 1 OR ID IS NULL)", "CODE = 'x''; DROP TABLE T'", "CODE NOT LIKE '%:SYSTEM.X%'", "TRIM(CODE) IS NOT NULL AND ABS(AMOUNT) > 0"]:
            with self.subTest(sql=sql): self.assertTrue(compiler.predicate(sql))
        self.assertEqual(compiler.binds,{})
        with self.assertRaises(Unsupported): compiler.order('ID, PKG.F(ID)')

    def test_null_bind_is_never_replaced_with_unfiltered_list(self):
        out=self.generate()
        source=(out/'backend/DPS/QueryDemoServiceImpl.java').read_text()
        self.assertIn('case "read" -> false;',source)
        self.assertIn('case "search": enabled = true; break;',source)
        self.assertIn('HEADER_ID = :q2',source)
        self.assertIn('p.addValue("q2", criteria.headerId, Types.NUMERIC)',source)
        self.assertIn('request.criteria() == null',source)
        self.assertIn('new java.math.BigDecimal("1.25")',source)
        self.assertIn('HELYI PROCEDURE FORMAT_LINE',source)
        dto=(out/'backend/CL/QueryDemoDtos.java').read_text()
        self.assertIn('class LinesCriteria',dto);self.assertNotIn('class FiltersRow',dto)
        self.assertIn('BigDecimal filtersMinAmount',dto)
        for layer,count in [('CL',4),('DPS',5),('WBS',5)]:self.assertEqual(len(list((out/'backend'/layer).glob('*.java'))),count)
        handoff=json.loads((out/'analysis/backend-handoff.json').read_text())
        q=handoff['queries'][1]
        self.assertEqual(q['path'],'/api/forms/queryDemo/lines/query/search');self.assertTrue(q['enabled'])
        self.assertIn('headerId',q['request_example']['criteria'])

    def test_all_layers_with_search_compile(self):
        if not shutil.which('java'):self.skipTest('Java compiler required')
        company=self.root/'company.json';company.write_text(json.dumps({'java_company_imports':COMPANY_IMPORTS}))  # log1x, UserDto stubs
        out=self.generate(['--config',str(company)]);sources=list(out.glob('backend/**/*.java'))+write_stubs(self.root/'stubs')
        args=self.root/'java.args';args.write_text('\n'.join('"'+str(p)+'"' for p in sources))
        result=subprocess.run(['java','com.sun.tools.javac.Main','--release','17','-encoding','UTF-8','-d',str(self.root/'classes'),'@'+str(args)],capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_screen_mode_keeps_search_blocked(self):
        out=self.generate(['--screen'])
        source=(out/'backend/DPS/QueryDemoServiceImpl.java').read_text()
        self.assertNotIn('case "search": enabled = true; break;',source)
        handoff=json.loads((out/'analysis/backend-handoff.json').read_text())
        self.assertFalse(any(q['enabled'] for q in handoff['queries']))

    def test_bad_relation_never_unlocks_master_or_detail(self):
        for join in ['T_HEADERS.ID = T_LINES.UNKNOWN', 'T_HEADERS.ID > T_LINES.HEADER_ID', 'T_HEADERS.ID = T_LINES.HEADER_ID OR 1=1']:
            root=self.source();root.find('.//Relation').set('JoinCondition',join)
            m=self.model(root)
            self.assertFalse(any(b['can_read'] or b['can_search'] for b in m['blocks']))

    def test_multi_master_and_cycles_stay_blocked(self):
        for cycle in [True,False]:
            root=self.source()
            if cycle:
                ET.SubElement(root.find('.//Block[@Name="LINES"]'),'Relation',Name='BACK',DetailBlock='HEADER',JoinCondition='T_LINES.HEADER_ID = T_HEADERS.ID')
            else:
                ET.SubElement(root.find('.//Block[@Name="HEADER"]'),'Relation',Name='DUP',DetailBlock='LINES',JoinCondition='T_HEADERS.ID = T_LINES.HEADER_ID')
            m=self.model(root)
            self.assertFalse(any(b['can_read'] or b['can_search'] for b in m['blocks']))

    def test_backend_aliases_conflicts_and_procedure_dml(self):
        root=self.source();root.find('.//Block[@Name="HEADER"]').set('DatabaseDataBlock','false')
        with self.assertRaisesRegex(MigrationError,'CONFLICTING_PROPERTY_ALIASES'):self.model(root)
        root=self.source();root.find('.//Block[@Name="LINES"]').set('DMLDataType','Procedure')
        m=self.model(root);self.assertFalse(m['blocks'][1]['can_search']);self.assertIn('DML_TYPE',{i['code'] for i in m['issues']})
        root=self.source();root.find('.//Block[@Name="LINES"]').set('DMLDataTargetName','OTHER')
        with self.assertRaisesRegex(MigrationError,'CONFLICTING_PROPERTY_ALIASES'):self.model(root)

    def test_local_procedure_is_fully_compiled_and_preserved(self):
        m=self.model();tr=m['triggers'][0]
        self.assertEqual(tr['status'],'converted');self.assertIn('SqlValues.upper(row.code)',tr['java'])
        self.assertEqual(tr['inlined_program_units'][0]['name'],'FORMAT_LINE')
        self.assertIn('FORMAT_LINE;',tr['source'])

    def test_local_procedure_refuses_parameters_declarations_cycles_and_sql(self):
        for body in ["PROCEDURE FORMAT_LINE(X NUMBER) IS BEGIN NULL; END;", "PROCEDURE FORMAT_LINE IS X NUMBER; BEGIN NULL; END;", "PROCEDURE FORMAT_LINE IS BEGIN FORMAT_LINE; END;", "PROCEDURE FORMAT_LINE IS BEGIN :LINES.LABEL := 'x'; DELETE FROM T; END;", "PROCEDURE WRONG IS BEGIN NULL; END;"]:
            root=self.source();root.find('.//ProgramUnit').set('ProgramUnitText',body)
            m=self.model(root);self.assertEqual(m['triggers'][0]['status'],'review');self.assertFalse(m['blocks'][1]['can_search'])

    def test_expanded_helper_keeps_post_query_write_protection(self):
        root=self.source();unit=root.find('.//ProgramUnit');unit.set('ProgramUnitText',unit.get('ProgramUnitText').replace(':LINES.LABEL',':LINES.CODE'))
        m=self.model(root);self.assertEqual(m['triggers'][0]['status'],'review');self.assertIn('POST-QUERY',m['triggers'][0]['reason'])

    def test_numeric_literal_bounds_and_invalid_range(self):
        m=self.model();item=m['blocks'][1]['items'][3]
        self.assertEqual(item['numeric_bounds'],{'minimum':'1.25','maximum':'1000000.50'})
        root=self.source();root.find('.//Item[@Name="AMOUNT"]').set('HighestAllowedValue','1')
        with self.assertRaisesRegex(MigrationError,'NUMERIC_RANGE'):self.model(root)
        root=self.source();root.find('.//Item[@Name="AMOUNT"]').set('LowestAllowedValue',':GLOBAL.MIN')
        m=self.model(root);self.assertTrue(any(i['code']=='ITEM_SEMANTICS' and i['scope']=='write' for i in m['issues']))


if __name__=='__main__': unittest.main()

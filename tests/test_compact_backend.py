import contextlib
import io
import json
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from frm_forms.cli import main
from java_support import COMPANY_IMPORTS, write_stubs

ROOT = Path(__file__).resolve().parents[1]


def multiple_blocks():
    form = ET.Element('FormModule', Name='OTHER_MODULE', Title='Több blokk')
    ET.SubElement(form, 'Canvas', Name='MAIN', CanvasType='Content', Width='200', Height='100')
    for block_name in ['DETAIL', 'HEADER', 'SUMMARY']:
        block = ET.SubElement(form, 'Block', Name=block_name, DatabaseDataBlock='true',
            QueryDataSourceType='Table', QueryDataSourceName='T_'+block_name, QueryAllowed='true',
            InsertAllowed='false', UpdateAllowed='true', DeleteAllowed='false',
            WhereClause='ID = :FILTERS.ID', OrderByClause='CODE&#10;DESC')
        ET.SubElement(block, 'Item', Name='ID', DataType='Number', ItemType='Text Item',
                      PrimaryKey='true', Required='true', Visible='false', ColumnName='ID')
        ET.SubElement(block, 'Item', Name='CODE', DataType='Char', ItemType='Text Item',
                      CanvasName='MAIN', MaximumLength='40', Required='true', LOVName='CODES')
        ET.SubElement(block, 'Item', Name='ARRIVED', DataType='Date', ItemType='Text Item', CanvasName='MAIN')
        ET.SubElement(block, 'Trigger', Name='POST-QUERY', TriggerText='BEGIN :'+block_name+'.CODE := pkg.load(:'+block_name+'.ID); END;')
    filters = ET.SubElement(form, 'Block', Name='FILTERS', DatabaseDataBlock='false')
    ET.SubElement(filters, 'Item', Name='ID', DataType='Number', ItemType='Text Item', CanvasName='MAIN')
    button = ET.SubElement(filters, 'Item', Name='RUN', ItemType='Push Button', CanvasName='MAIN')
    ET.SubElement(button, 'Trigger', Name='WHEN-BUTTON-PRESSED', TriggerText='BEGIN local_load; END;')
    ET.SubElement(form, 'ProgramUnit', Name='LOCAL_LOAD', ProgramUnitText='PROCEDURE local_load IS BEGIN SELECT ID INTO :FILTERS.ID FROM MASTER; END;')
    ET.SubElement(form, 'LOV', Name='CODES', RecordGroupName='RG_CODES')
    ET.SubElement(form, 'RecordGroup', Name='RG_CODES', RecordGroupQuery='SELECT CODE&#10;FROM CODES')
    return ET.tostring(form)


class CompactBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def generate(self, raw=None, extra=(), out='output'):
        source = self.root/'input.xml'
        source.write_bytes(raw or multiple_blocks())
        output = self.root/out
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            status = main(['migrate', str(source), '--module', 'otherModule', '--scaffold', '--out', str(output), *extra])
        self.assertEqual(status, 0, err.getvalue())
        return output

    def test_module_counts_do_not_grow_with_blocks_or_actions(self):
        out = self.generate()
        # SQL and helpers live directly in ServiceImpl; no Data/domain/repository file.
        self.assertTrue((out/'backend/CL/CommonMigrateTools.java').is_file())  # the one helper file every module calls
        for layer, count in [('CL', 4), ('DPS', 4), ('WBS', 4)]:
            files = [f for f in (out/'backend'/layer).glob('*.java') if f.name != 'CommonMigrateTools.java']
            self.assertEqual(len(files), count, files)
            self.assertTrue(all(f.name.startswith('OtherModule') for f in files))
        dto = (out/'backend/CL/OtherModuleDtos.java').read_text()
        # The encoded ORDER BY (CODE&#10;DESC) now compiles: a bound search with its criteria DTO.
        self.assertEqual(len(re.findall(r'public static class \w+(?:Row|Criteria) \{', dto)), 6)
        self.assertNotIn(' record ', dto)  # Java 11: plain DTO classes
        self.assertEqual(dto.count('Criteria {'), 3)
        self.assertNotIn('FiltersRow', dto)
        self.assertNotIn('public String run', dto)
        # Simple type names, no bean validation: the field rules are checked with the shared FormsChecks.
        self.assertRegex(dto, r'@JsonFormat\(shape = JsonFormat.Shape.STRING\)\s+public BigDecimal id;')
        self.assertIn('public LocalDateTime arrived;', dto)
        self.assertNotIn('jakarta', dto)
        service = (out/'backend/DPS/OtherModuleServiceImpl.java').read_text()
        self.assertIn('FormsChecks.required(errors, "ID", row.id);', service)
        self.assertIn(', 40);', service)  # MaximumLength=40 of a text field
        self.assertNotIn('jakarta', service)
        # The helpers are called from the one shared CL file, not copied into the ServiceImpl.
        self.assertIn('import hu.company.features.cl.CommonMigrateTools.FormsChecks;', service)
        self.assertNotIn('static final class SqlValues', service)
        self.assertIn('public final class CommonMigrateTools', (out/'backend/CL/CommonMigrateTools.java').read_text())
        plan = json.loads((out/'analysis/backend-plan.json').read_text())
        # Per block: search + update; InsertAllowed/DeleteAllowed=false and the bound
        # WHERE leave create, delete and the parameterless list out. Plus one action
        # and one LOV endpoint from the record group query.
        self.assertEqual(len(plan['endpoints']), 9)  # + the Forms COMMIT_FORM chain
        self.assertEqual([e['lov'] for e in plan['endpoints'] if e.get('operation') == 'lov'], ['CODES'])
        self.assertEqual(sorted((s['block'], s['operation']) for s in plan['skipped_operations']),
                         sorted((b, op) for b in ['DETAIL', 'HEADER', 'SUMMARY'] for op in ['list', 'create', 'delete']))
        self.assertFalse(any(e['implemented'] for e in plan['endpoints']))
        self.assertIsNone(plan['dto_selection'][-1]['dto'])

    def test_sql_trigger_lov_and_action_evidence_is_in_the_evidence_file(self):
        out = self.generate()
        evidence = (out/'analysis/backend-evidence.md').read_text()
        for text in ['Eredeti WHERE: ID = :FILTERS.ID', 'Eredeti ORDER BY: CODE\nDESC',
                     'insert_allowed: false', 'delete_allowed: false', 'TRIGGER DETAIL / POST-QUERY',
                     'pkg.load(:DETAIL.ID)', 'LOV rekordcsoport (külön bekötendő): RG_CODES',
                     'SELECT CODE\nFROM CODES', 'SELECT ID INTO :FILTERS.ID FROM MASTER',
                     'SHA256:', 'analysis/discovery/sources/']:
            self.assertIn(text, evidence)
        self.assertNotIn('&#10;', evidence)
        # The ServiceImpl stays readable: log1x calls and short notes, no commented-out Forms code.
        source = (out/'backend/DPS/OtherModuleServiceImpl.java').read_text()
        self.assertNotIn('pkg.load(:DETAIL.ID)', source)
        self.assertIn('return log1x(log, OtherModuleConstants.', source)
        self.assertIn('analysis/backend-evidence.md', source)

    def test_all_http_paths_use_cl_constants_including_buttons(self):
        config = self.root/'config.json'
        config.write_text(json.dumps({'endpoint_names': {'list': 'fetch'}, 'api_prefix': '/service'}))
        form = ET.fromstring(multiple_blocks())
        form.find('Block[@Name="SUMMARY"]').attrib.pop('WhereClause')  # unbound: a plain list endpoint
        out = self.generate(ET.tostring(form), extra=['--config', str(config)])
        constants = (out/'backend/CL/OtherModuleConstants.java').read_text()
        self.assertIn('BASE_PATH = "/service/otherModule"', constants)
        self.assertIn('SUMMARY_LIST_PATH = "/summary/fetch"', constants)
        self.assertIn('DETAIL_SEARCH_PATH = "/detail/query/search"', constants)
        self.assertNotIn('DETAIL_LIST_PATH', constants)
        for layer in ['DPS', 'WBS']:
            controller = (out/f'backend/{layer}/OtherModuleController.java').read_text()
            self.assertEqual(controller.count('Mapping(OtherModuleConstants.'), 9)  # 6 CRUD/search + action + LOV + commit
            self.assertNotIn('Mapping("', controller)
            self.assertIn('Mapping(OtherModuleConstants.ACTION_', controller)
        client = (out/'backend/CL/OtherModuleRestClientImpl.java').read_text()
        self.assertIn('ParameterizedTypeReference<PageResult<DetailRow>>', client)
        self.assertIn('Constants.ACTION_', client)

    def test_generated_multiple_blocks_and_normalized_backend_names_compile(self):
        if not shutil.which('java'):
            self.skipTest('Java 11+ compiler required')
        company = self.root/'company.json'  # the company classes of java_support (log1x, UserDto)
        company.write_text(json.dumps({'java_company_imports': COMPANY_IMPORTS}))
        out = self.generate(extra=['--config', str(company)])
        # Exercise the backend IR independently: the UI intentionally rejects identifier collisions.
        from frm_forms.xmlmodel import parse_xml
        from frm_forms.rules import analyze
        from frm_forms.generate import generate_java, initial_values
        source = self.root/'collisions.xml'
        source.write_bytes(multiple_blocks().replace(b'HEADER', b'DETAIL_2').replace(b'SUMMARY', b'DETAIL$2'))
        model = parse_xml(source); analyze(model, {}, {}); initial_values(model)
        config = json.loads((out/'analysis/effective-config.json').read_text())['config']
        extra = self.root/'collision-output'
        generate_java(model, extra, config, 'collisions', 'hu.company.features.collisions')
        # Two modules, one shared CommonMigrateTools - as in a real project.
        sources = list(out.glob('backend/**/*.java'))+[p for p in extra.glob('backend/**/*.java') if p.name != 'CommonMigrateTools.java']+write_stubs(self.root/'stubs')
        argfile = self.root/'sources.args'
        argfile.write_text('\n'.join('"'+str(p).replace('\\', '/')+'"' for p in sources))
        build = subprocess.run(['java', 'com.sun.tools.javac.Main', '--release', '11', '-encoding', 'UTF-8',
                                '-d', str(self.root/'classes'), '@'+str(argfile)], capture_output=True, text=True, timeout=60)
        self.assertEqual(build.returncode, 0, build.stdout+build.stderr)

    def test_action_only_module_has_no_jdbc_or_unused_row_dtos(self):
        form = ET.fromstring(multiple_blocks())
        for block in list(form.findall('Block')):
            if block.get('DatabaseDataBlock') == 'true':
                form.remove(block)
        out = self.generate(ET.tostring(form))
        self.assertEqual(len(list(out.glob('backend/**/*.java'))), 13)  # 12 module files + CL/CommonMigrateTools
        service = (out/'backend/DPS/OtherModuleServiceImpl.java').read_text()
        dto = (out/'backend/CL/OtherModuleDtos.java').read_text()
        # The button's PL/SQL runs in the database: JDBC is used, the CRUD machinery is not.
        self.assertIn('DbCalls.call(jdbc', service)
        self.assertNotIn('Validator', service)
        self.assertNotIn('PageResult', dto)
        self.assertIn('public static class ActionRequest', dto)

    def test_regeneration_keeps_custom_code_and_provides_fresh_candidate(self):
        out = self.generate()
        service = out/'backend/DPS/OtherModuleServiceImpl.java'
        custom = service.read_bytes()+b'\n// reviewed developer code\n'
        service.write_bytes(custom)
        new_source = multiple_blocks().replace(b'SELECT ID INTO', b'SELECT OTHER_ID INTO')
        self.generate(new_source, ['--regenerate'])
        self.assertEqual(service.read_bytes(), custom)
        candidate = out/'analysis/backend-regeneration/backend/DPS/OtherModuleServiceImpl.java.txt'
        self.assertTrue(candidate.is_file() and candidate.with_suffix('.patch').is_file())
        # SQL shares CREATE_ONCE protection with hand-edited service code.
        self.assertIn('SELECT OTHER_ID INTO', candidate.read_text())
        self.assertNotIn('SELECT OTHER_ID INTO', service.read_text())
        self.assertEqual(len(list(out.glob('backend/**/*.java'))), 13)  # 12 module files + CL/CommonMigrateTools
        self.assertIn('Backend újragenerálás', (out/'INTEGRATION.md').read_text())

    def test_legacy_backend_upgrade_rejected_without_overwriting_work(self):
        out = self.generate()
        manifest = out/'generated-files.json'
        data = json.loads(manifest.read_text()); data.pop('backend_layout')
        manifest.write_text(json.dumps(data))
        old = {p.relative_to(out): p.read_bytes() for p in out.rglob('*') if p.is_file()}
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            status = main(['migrate', str(self.root/'input.xml'), '--module', 'otherModule', '--scaffold', '--out', str(out), '--regenerate'])
        self.assertEqual(status, 1)
        self.assertEqual(old, {p.relative_to(out): p.read_bytes() for p in out.rglob('*') if p.is_file()})

    def test_frontend_only_does_not_generate_backend(self):
        out = self.generate(extra=['--frontend-only'])
        self.assertFalse((out/'backend').exists())
        self.assertFalse((out/'analysis/backend-plan.json').exists())


if __name__ == '__main__':
    unittest.main()

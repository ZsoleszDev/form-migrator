"""backend_trigger_mode=ai: per table a Domain entity and an empty JpaRepository, for the developer; the data
endpoints are frames the Java assistant writes in the service (native SQL through the EntityManager).

The frames carry the SQL Forms runs for the block (its query with the criteria binds, the FOR UPDATE NOWAIT lock,
the INSERT/UPDATE/DELETE) and the LOV's record group query; the service has no repository, no JDBC and no CRUD
machinery. The entity maps the table's reviewed structure (schema.json, Adatbázistáblák panel) when there is one.
"""
import contextlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from frm_forms.cli import main
from frm_forms.jpa_lov import select_labels
from frm_forms.plsql import Unsupported
from java_support import COMPANY_IMPORTS, compile_copies, write_stubs

ROOT = Path(__file__).resolve().parents[1]
REPLICA = ROOT / 'tests' / 'fixtures' / 'felmeres_replika_fmb.xml'

FORM = '''<Module><FormModule Name="LAPOZAS"><Coordinate CoordinateSystem="Real" RealUnit="Pixel"/>
 <Block Name="B" DatabaseDataBlock="true" QueryDataSourceName="T_B" RecordsDisplayCount="5">
  <Item ItemType="Text Item" Name="ID" DataType="Number" PrimaryKey="true" CanvasName="C" XPosition="10" YPosition="10" Width="80" Height="20"/>
  <Item ItemType="Text Item" Name="KOD" DataType="Char" MaximumLength="10" CanvasName="C" XPosition="100" YPosition="10" Width="80" Height="20"/>
 </Block>
 <Block Name="N" DatabaseDataBlock="true" QueryDataSourceName="T_N" RecordsDisplayCount="5">
  <Item ItemType="Text Item" Name="NEV" DataType="Char" MaximumLength="10" CanvasName="C" XPosition="10" YPosition="40" Width="80" Height="20"/>
 </Block><Canvas Name="C" CanvasType="Content" WindowName="W"/><Window Name="W"/>
</FormModule></Module>'''

def generate(root: Path, xml_path: Path, module: str, **config) -> Path:
    settings, out = root / (module + '.json'), root / module
    settings.write_text(json.dumps({'java_company_imports': COMPANY_IMPORTS, 'backend_live': True, **config}), encoding='utf-8')
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        code = main(['migrate', str(xml_path), '--screen', '--module', module, '--config', str(settings), '--out', str(out)])
    assert code == 0, code
    return out


class JpaCrudTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.out = generate(cls.root, REPLICA, 'felmeres')
        cls.dps = cls.out / 'backend' / 'DPS'
        cls.service = (cls.dps / 'FelmeresServiceImpl.java').read_text(encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def read(self, name):
        return (self.dps / name).read_text(encoding='utf-8')

    def test_a_domain_and_an_empty_repository_per_table_named_after_it(self):
        self.assertEqual(sorted(p.name for p in self.dps.glob('*Domain.java')),
                         ['PartnerDomain.java', 'RendelesDomain.java', 'RendelesTetelDomain.java'])
        domain = self.read('RendelesTetelDomain.java')
        self.assertIn('@Entity\n@Table(name = "RENDELES_TETEL")\npublic class RendelesTetelDomain {', domain)
        self.assertIn('    @Id\n    @Column(name = "ID")\n    private BigDecimal id;', domain)
        self.assertIn('import javax.persistence.Entity;', domain)
        self.assertIn('public BigDecimal getRendelesId() {', domain)
        repository = self.read('RendelesTetelRepository.java')
        self.assertIn('public interface RendelesTetelRepository extends JpaRepository<RendelesTetelDomain, BigDecimal> {', repository)

    def frame(self, method):
        """The hint lines of a frame, joined (a long hint wraps onto indented comment lines)."""
        start = self.service.index(' ' + method + '(')
        text = self.service[start:self.service.index('\n    }\n', start)]
        return ' '.join(line.strip().lstrip('/').strip() for line in text.splitlines())

    def test_the_search_frame_holds_the_block_query_and_its_criteria(self):
        tetel = self.frame('searchTetel')
        self.assertIn('SELECT ID, RENDELES_ID, CIKK, MENNYISEG, AKTIV FROM RENDELES_TETEL WHERE ((AKTIV = \'I\') AND '
                      '(RENDELES_ID = :rendelesId)) ORDER BY CIKK OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY', tetel)
        self.assertIn('Feltételek: :rendelesId = request.criteria().rendelesId', tetel)
        # Oracle SQL as written (TRUNC(SYSDATE), a column the block does not map): the model runs it as native SQL
        partner = self.frame('searchPartner')
        self.assertIn('PARTNER.ERVENYES_TOL <= TRUNC(SYSDATE)', partner)
        self.assertIn(':globalCgApp = request.criteria().globalCgApp', partner)

    def test_the_lock_is_the_forms_nowait_lock(self):
        for method in ('updateRendeles', 'deleteRendeles'):
            self.assertIn('Zárolás: SELECT ID, VEVO, DATUM, STATUSZ, OSSZEG FROM RENDELES WHERE ID = :id FOR UPDATE NOWAIT',
                          self.frame(method))
        # The form's own ON-UPDATE replaces the UPDATE Forms would run: the hook saves, the frame says so.
        self.assertIn('triggerOnUpdateRendeles(value, context) (ON-UPDATE): ez végzi a mentést', self.frame('updateRendeles'))
        self.assertNotIn('UPDATE RENDELES SET', self.frame('updateRendeles'))
        self.assertIn('DELETE FROM RENDELES WHERE ID = :id', self.frame('deleteRendeles'))

    def test_the_service_has_no_repository_and_no_jdbc(self):
        code = re.sub(r'//[^\n]*', '', self.service)  # the hints and the commented Forms code are no code
        for word in ('NamedParameterJdbcTemplate', 'MapSqlParameterSource', 'ResultSet', 'jdbc', 'Repository', '@Autowired',
                     'SELECT ', 'INSERT ', 'UPDATE ', 'DELETE ', 'FormsChecks', 'SqlValues.'):
            self.assertNotIn(word, code)
        self.assertIn('    @PersistenceContext\n    private EntityManager entityManager;', self.service)
        self.assertNotIn('public FelmeresServiceImpl(', self.service)  # nothing to inject by constructor
        for name in ('RendelesRepository.java', 'RendelesTetelRepository.java', 'PartnerRepository.java'):
            self.assertRegex(self.read(name), r'extends JpaRepository<\w+Domain, \w+> \{\n\}\n$')

    def test_the_lov_query_is_in_its_frame(self):
        lov = self.frame('lovLovStatusz')
        self.assertIn('SELECT kod, nev FROM statusz_kodtar ORDER BY kod', lov)
        self.assertIn("(:term IS NULL OR UPPER(lov.KOD) LIKE UPPER(:term) || '%')", lov)
        self.assertIn(':limit = LovQuery.limit(request.limit())', lov)
        self.assertIn('new LovResult(LovQuery.rows(rows, "KOD", "NEV"))', lov)
        self.assertNotIn('lov', self.read('RendelesRepository.java').split('{', 1)[1])

    def test_the_screen_loads_the_lov_of_its_autocomplete(self):
        # The LOV endpoint is called by the item itself: typing or opening the list loads the suggestions.
        screen = (self.out / 'frontend/felmeres/felmeres.component.ts').read_text(encoding='utf-8')
        self.assertIn("ownId: 'RENDELES.STATUSZ'", screen)
        self.assertIn('suggestions: [], completeMethod: (event: { query?: string }) => this.onStatuszComplete(event) }', screen)
        method = screen[screen.index('protected onStatuszComplete('):]
        method = method[:method.index('\n  }\n')]
        self.assertIn("this.lovLovstatusz({ term: event?.query ?? '', parameters: {} }).subscribe(res => {", method)
        self.assertIn("this.structures['rendeles']?.find(s => s['ownId'] === 'RENDELES.STATUSZ')", method)
        self.assertIn("value: row['KOD'],", method)  # the column the LOV returns into the item

    def test_the_select_list_names_the_lov_columns(self):
        self.assertEqual(select_labels('SELECT DISTINCT kod, t.nev AS megnevezes, nvl(a, b) x FROM t WHERE a = (SELECT 1 FROM dual)'),
                         ['KOD', 'MEGNEVEZES', 'X'])
        self.assertEqual(select_labels('SELECT a || b, c FROM t', ['NEV', 'C']), ['NEV', 'C'])  # the LOV mapping's names
        for query in ('SELECT a || b FROM t', 'SELECT * FROM t'):
            with self.assertRaises(Unsupported):
                select_labels(query)

    def test_it_compiles(self):
        if not shutil.which('javac'):
            self.skipTest('javac required')
        work = self.root / 'compile'
        sources = compile_copies(sorted(self.out.glob('backend/**/*.java')), work) + write_stubs(work / 'stubs')
        run = subprocess.run(['javac', '-proc:none', '-encoding', 'UTF-8', '-d', str(work / 'classes')] + [str(p) for p in sources],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_jakarta_namespace(self):
        out = generate(self.root, REPLICA, 'jakarta', jpa_namespace='jakarta')
        service = (out / 'backend/DPS/JakartaServiceImpl.java').read_text(encoding='utf-8')
        self.assertIn('import jakarta.persistence.EntityManager;', service)
        self.assertIn('import jakarta.persistence.PersistenceContext;', service)
        self.assertIn('import jakarta.persistence.Entity;', (out / 'backend/DPS/RendelesDomain.java').read_text(encoding='utf-8'))


class EntityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.form = self.root / 'lapozas.xml'
        self.form.write_text(FORM, encoding='utf-8')

    def test_a_block_without_a_primary_key_gets_no_entity_and_no_endpoint(self):
        out = generate(self.root, self.form, 'lapozas')
        self.assertTrue((out / 'backend/DPS/TBDomain.java').is_file())
        self.assertFalse((out / 'backend/DPS/TNDomain.java').exists())
        plan = json.loads((out / 'analysis/backend-plan.json').read_text(encoding='utf-8'))
        self.assertEqual([e['block'] for e in plan['endpoints'] if e.get('operation') in {'list', 'create'}], ['B', 'B'])
        self.assertEqual({e['runs'] for e in plan['endpoints'] if e.get('block') == 'B'}, {'ai'})

    def test_hints_for_a_one_column_block_and_a_case_restricted_item(self):
        # A query of one column returns the values themselves, no Object[]; Forms upper-cases a CaseRestriction item
        # as it is typed, the save does it here. Both would compile wrong-way round and fail only at run time.
        self.form.write_text(FORM.replace('<Block Name="N" DatabaseDataBlock="true" QueryDataSourceName="T_N" RecordsDisplayCount="5">\n'
                                          '  <Item ItemType="Text Item" Name="NEV" DataType="Char"',
                                          '<Block Name="N" DatabaseDataBlock="true" QueryDataSourceName="T_N" RecordsDisplayCount="5">\n'
                                          '  <Item ItemType="Text Item" Name="NEV" DataType="Char" PrimaryKey="true" CaseRestriction="Upper"'),
                             encoding='utf-8')
        out = generate(self.root, self.form, 'lapozas')
        service = ' '.join(line.strip().lstrip('/').strip() for line in
                           (out / 'backend/DPS/LapozasServiceImpl.java').read_text(encoding='utf-8').splitlines())
        self.assertIn('Sor -> new NRow(): a lekérdezés egy oszlopa maga a sor (Object, nem Object[]): NEV -> nev (String).', service)
        self.assertIn('Kis-/nagybetűs mezők (Forms CaseRestriction): row.nev = SqlValues.upper(row.nev).', service)

    def test_the_entity_maps_the_tables_reviewed_structure(self):
        schema = self.root / 'schema.json'
        schema.write_text(json.dumps({'tables': {'T_B': {'primary_key': ['ID'], 'columns': {
            'ID': {'data_type': 'NUMBER', 'nullable': False}, 'KOD': {'data_type': 'VARCHAR2', 'nullable': True},
            'LEIRAS': {'data_type': 'CLOB', 'nullable': True}, 'MODOSITVA': {'data_type': 'DATE', 'nullable': True}}}}}),
            encoding='utf-8')
        settings, out = self.root / 'lapozas.json', self.root / 'lapozas'
        settings.write_text(json.dumps({'java_company_imports': COMPANY_IMPORTS}), encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['migrate', str(self.form), '--module', 'lapozas', '--config', str(settings),
                                   '--schema', str(schema), '--out', str(out)]), 0)
        domain = (out / 'backend/DPS/TBDomain.java').read_text(encoding='utf-8')
        self.assertIn('    @Lob\n    @Column(name = "LEIRAS")\n    private String leiras;', domain)
        self.assertIn('    @Column(name = "MODOSITVA")\n    private LocalDateTime modositva;', domain)
        self.assertNotIn('LEIRAS', (out / 'backend/DPS/LapozasServiceImpl.java').read_text(encoding='utf-8'))

    def test_a_saved_structure_becomes_an_entity_and_an_empty_repository(self):
        from frm_forms import schema_workbench as schema
        out = generate(self.root, self.form, 'lapozas')
        sheet = schema.save_table(out, 'T_N', [{'name': 'ID', 'data_type': 'NUMBER', 'nullable': False},
                                               {'name': 'NEV', 'data_type': 'VARCHAR2', 'nullable': True, 'length': 10,
                                                'length_semantics': 'CHAR'}],
                                  ['ID'], schema.schema_sheet(out)['revision'])
        self.assertEqual(sheet['entity_files'], ['TNDomain.java', 'TNRepository.java'])
        domain = (out / 'backend/DPS/TNDomain.java').read_text(encoding='utf-8')
        self.assertIn('// Forms migrator JPA domain: T_N (Forms-blokkok: N).', domain)
        self.assertIn('    @Id\n    @Column(name = "ID")\n    private BigDecimal id;', domain)
        repository = out / 'backend/DPS/TNRepository.java'
        self.assertIn('public interface TNRepository extends JpaRepository<TNDomain, BigDecimal> {\n}', repository.read_text(encoding='utf-8'))
        # The developer's repository is never overwritten; the entity follows the structure.
        repository.write_text(repository.read_text(encoding='utf-8').replace('{\n}', '{\n    // saját\n}'), encoding='utf-8')
        sheet = schema.save_table(out, 'T_N', [{'name': 'ID', 'data_type': 'NUMBER', 'nullable': False},
                                               {'name': 'NEV', 'data_type': 'VARCHAR2', 'nullable': True, 'length': 10,
                                                'length_semantics': 'CHAR'},
                                               {'name': 'KOD', 'data_type': 'VARCHAR2', 'nullable': True, 'length': 3,
                                                'length_semantics': 'CHAR'}],
                                  ['ID'], schema.schema_sheet(out)['revision'])
        self.assertEqual(sheet['entity_files'], ['TNDomain.java'])
        self.assertIn('// saját', repository.read_text(encoding='utf-8'))
        self.assertIn('private String kod;', (out / 'backend/DPS/TNDomain.java').read_text(encoding='utf-8'))
        # Without a primary key there is no entity (it needs its @Id).
        sheet = schema.save_table(out, 'T_X', [{'name': 'A', 'data_type': 'NUMBER', 'nullable': True}], [],
                                  schema.schema_sheet(out)['revision'])
        self.assertNotIn('entity_files', sheet)
        self.assertFalse((out / 'backend/DPS/TXDomain.java').exists())

if __name__ == '__main__':
    unittest.main()

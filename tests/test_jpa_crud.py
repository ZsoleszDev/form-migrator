"""The block CRUD over Spring Data JPA (backend_trigger_mode=ai): a Domain and a JpaRepository per table, the
company pattern, and no SQL or JDBC in the DPS ServiceImpl.

The generated code is compiled with the offline stubs and run with an in-memory repository (a java.lang.reflect.Proxy
of the generated interface, set into the service's @Autowired field): the page says when it left rows out
(Slice.hasNext), a record another session holds is HTTP 409 at once, and a new record never overwrites an existing
key (save() would update it).
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

RUN = r'''
import hu.company.common.UserDto;
import hu.company.features.lapozas.cl.LapozasDtos.*;
import hu.company.features.lapozas.dps.*;
import java.lang.reflect.Field;
import java.lang.reflect.Proxy;
import java.math.BigDecimal;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Optional;
import org.springframework.dao.DataAccessException;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Slice;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

public class JpaRun {
    static final List<TBDomain> TABLE = new ArrayList<>();
    static boolean locked;

    /** What Spring makes of ORA-00054 (FOR UPDATE NOWAIT on a record another session holds). */
    static class Busy extends DataAccessException {
        Busy() { super("busy"); }
        @Override public Throwable getMostSpecificCause() { return new SQLException("ORA-00054: resource busy", "61000", 54); }
    }

    static Optional<TBDomain> find(Object id) {
        return TABLE.stream().filter(d -> d.getId().equals(id)).findFirst();
    }

    static Object repository() {
        return Proxy.newProxyInstance(TBRepository.class.getClassLoader(), new Class<?>[] {TBRepository.class}, (proxy, method, args) -> {
            switch (method.getName()) {
                case "listB": {
                    Pageable page = (Pageable) args[0];
                    int from = Math.min(page.getPageNumber() * page.getPageSize(), TABLE.size());
                    int to = Math.min(from + page.getPageSize(), TABLE.size());
                    List<TBDomain> content = new ArrayList<>(TABLE.subList(from, to));
                    boolean next = to < TABLE.size();
                    return new Slice<TBDomain>() {
                        public List<TBDomain> getContent() { return content; }
                        public boolean hasNext() { return next; }
                        public Iterator<TBDomain> iterator() { return content.iterator(); }
                    };
                }
                case "lockById": if (locked) throw new Busy(); return find(args[0]);
                case "findById": return find(args[0]);
                case "existsById": return find(args[0]).isPresent();
                case "saveAndFlush": { TBDomain d = (TBDomain) args[0]; if (!TABLE.contains(d)) TABLE.add(d); return d; }
                case "delete": TABLE.remove(args[0]); return null;
                case "flush": return null;
                default: throw new UnsupportedOperationException(method.getName());
            }
        });
    }

    interface Call { void run() throws Exception; }

    static Object status(Call call) throws Exception {
        try { call.run(); } catch (ResponseStatusException e) { return e.status; }
        return null;
    }

    public static void main(String[] args) throws Exception {
        for (int i = 1; i <= 5; i++) { TBDomain d = new TBDomain(); d.setId(new BigDecimal(i)); d.setKod("K" + i); TABLE.add(d); }
        LapozasServiceImpl service = new LapozasServiceImpl();
        for (Field field : LapozasServiceImpl.class.getDeclaredFields()) {
            if (field.getType() == TBRepository.class) { field.setAccessible(true); field.set(service, repository()); }
        }
        UserDto user = new UserDto();
        PageResult<BRow> first = service.listB(user, 0, 2);
        if (first.rows.size() != 2 || !first.hasMore || !"K1".equals(first.rows.get(0).kod)) throw new AssertionError("page 1");
        PageResult<BRow> last = service.listB(user, 4, 2);
        if (last.rows.size() != 1 || last.hasMore) throw new AssertionError("last page: no more rows");
        if (status(() -> service.listB(user, 3, 2)) != HttpStatus.UNPROCESSABLE_ENTITY)
            throw new AssertionError("an offset between pages is refused");

        BRow original = first.rows.get(0);
        BRow value = new BRow(); value.id = original.id; value.kod = "UJ";
        RowResult<BRow> updated = service.updateB(user, original, value);
        if (!"UJ".equals(updated.row.kod) || !"UJ".equals(TABLE.get(0).getKod())) throw new AssertionError("update");

        locked = true;
        BRow again = new BRow(); again.id = updated.row.id; again.kod = "MAS";
        if (status(() -> service.updateB(user, updated.row, again)) != HttpStatus.CONFLICT)
            throw new AssertionError("a locked record is 409 at once");
        locked = false;

        BRow fresh = new BRow(); fresh.id = new BigDecimal(2); fresh.kod = "DUPLA";
        if (status(() -> service.createB(user, fresh)) != HttpStatus.CONFLICT)
            throw new AssertionError("an existing key is not overwritten");
        if (!"K2".equals(find(new BigDecimal(2)).get().getKod())) throw new AssertionError("the existing record stays");
        BRow added = new BRow(); added.id = new BigDecimal(6); added.kod = "K6";
        if (!"K6".equals(service.createB(user, added).row.kod) || TABLE.size() != 6) throw new AssertionError("create");

        service.deleteB(user, service.listB(user, 0, 1).rows.get(0));
        if (TABLE.size() != 5 || find(BigDecimal.ONE).isPresent()) throw new AssertionError("delete");
        System.out.println("jpa crud passed");
    }
}
'''


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

    def test_a_domain_and_a_repository_per_table_named_after_it(self):
        self.assertEqual(sorted(p.name for p in self.dps.glob('*Domain.java')),
                         ['PartnerDomain.java', 'RendelesDomain.java', 'RendelesTetelDomain.java'])
        domain = self.read('RendelesTetelDomain.java')
        self.assertIn('@Entity\n@Table(name = "RENDELES_TETEL")\npublic class RendelesTetelDomain {', domain)
        self.assertIn('    @Id\n    @Column(name = "ID")\n    private BigDecimal id;', domain)
        self.assertIn('import javax.persistence.Entity;', domain)
        self.assertIn('public BigDecimal getRendelesId() {', domain)
        repository = self.read('RendelesTetelRepository.java')
        self.assertIn('public interface RendelesTetelRepository extends JpaRepository<RendelesTetelDomain, BigDecimal> {', repository)

    def test_the_block_query_is_jpql_and_falls_back_to_native_sql(self):
        tetel = self.read('RendelesTetelRepository.java')
        # DEFAULT_WHERE + ORDER BY of the block over the Domain's fields, the Forms bind as a named parameter
        self.assertIn('@Query("SELECT d FROM RendelesTetelDomain d WHERE ((d.aktiv = \'I\') AND (d.rendelesId = :rendelesId)) ORDER BY "', tetel)
        self.assertIn('Slice<RendelesTetelDomain> searchTetel(@Param("rendelesId") BigDecimal rendelesId, Pageable pageable);', tetel)
        partner = self.read('PartnerRepository.java')
        # TRUNC(SYSDATE) and a column the block does not map: no JPQL, the original clause runs as native SQL
        self.assertIn('nativeQuery = true)', partner)
        self.assertIn('// Natív SQL: JPQL-ben nem írható le (Nem leképezett SQL-oszlop: PARTNER.TIPUS).', partner)
        self.assertIn('SqlValues.nvl(SqlValues.normalize(criteria.ctrlSzuro), "")', self.service)  # never a null VARCHAR

    def test_the_lock_is_the_forms_nowait_lock(self):
        repository = self.read('RendelesRepository.java')
        self.assertIn('    @Lock(LockModeType.PESSIMISTIC_WRITE)\n'
                      '    @QueryHints(@QueryHint(name = "javax.persistence.lock.timeout", value = "0"))\n'
                      '    @Query("SELECT d FROM RendelesDomain d WHERE d.id = :id")\n'
                      '    Optional<RendelesDomain> lockById(@Param("id") BigDecimal id);', repository)
        self.assertIn('lock ? rendelesRepository.lockById(key.id) : rendelesRepository.findById(key.id)', self.service)

    def test_the_service_has_no_sql_and_no_jdbc(self):
        code = re.sub(r'//[^\n]*', '', self.service)  # the commented Forms code of the AI frames is no SQL the service runs
        for word in ('NamedParameterJdbcTemplate', 'MapSqlParameterSource', 'ResultSet', 'jdbc', 'SELECT ', 'INSERT ', 'UPDATE ', 'DELETE '):
            self.assertNotIn(word, code)
        self.assertIn('    @Autowired\n    private RendelesRepository rendelesRepository;', self.service)
        self.assertNotIn('public FelmeresServiceImpl(', self.service)  # nothing to inject by constructor

    def test_the_lov_query_is_a_repository_method(self):
        repository = self.read('RendelesRepository.java')
        self.assertIn('List<Object[]> lovLovStatusz(@Param("term") String term, @Param("limit") int limit);', repository)
        self.assertIn('SELECT kod, nev FROM statusz_kodtar ORDER BY kod', repository)
        self.assertIn('return new LovResult(LovQuery.rows(rows, "KOD", "NEV"));', self.service)
        self.assertIn('rendelesRepository.lovLovStatusz(LovQuery.term(request.term()), LovQuery.limit(request.limit()))', self.service)

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
        repository = (out / 'backend/DPS/RendelesRepository.java').read_text(encoding='utf-8')
        self.assertIn('import jakarta.persistence.LockModeType;', repository)
        self.assertIn('"jakarta.persistence.lock.timeout"', repository)
        self.assertIn('import jakarta.persistence.Entity;', (out / 'backend/DPS/RendelesDomain.java').read_text(encoding='utf-8'))


class JpaRuntimeTests(unittest.TestCase):
    def test_paging_lock_insert_and_delete_at_run_time(self):
        if not shutil.which('java'):
            self.skipTest('Java 11+ required')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            form = root / 'lapozas.xml'
            form.write_text(FORM, encoding='utf-8')
            out = generate(root, form, 'lapozas')
            # a block without a primary key gets no Domain and no endpoint
            self.assertFalse((out / 'backend/DPS/TNDomain.java').exists())
            plan = json.loads((out / 'analysis/backend-plan.json').read_text(encoding='utf-8'))
            self.assertEqual([e['block'] for e in plan['endpoints'] if e.get('operation') in {'list', 'create'}], ['B', 'B'])
            main_source = root / 'JpaRun.java'
            main_source.write_text(RUN, encoding='utf-8')
            sources = list(out.glob('backend/**/*.java')) + write_stubs(root / 'stubs') + [main_source]
            build = subprocess.run(['javac', '-proc:none', '-encoding', 'UTF-8', '-d', str(root / 'classes')] + [str(p) for p in sources],
                                   capture_output=True, text=True, timeout=120)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run(['java', '-cp', str(root / 'classes'), 'JpaRun'], capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn('jpa crud passed', run.stdout)


if __name__ == '__main__':
    unittest.main()

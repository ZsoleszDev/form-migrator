"""The generated CRUD at run time: a page says when it left rows out, a locked record is HTTP 409 at once, and an
item's own checks come before its triggers.

The DPS ServiceImpl is compiled with the offline stubs and run against an in-memory NamedParameterJdbcTemplate
(five rows): the list asks one row more than the page (hasMore), POST-QUERY never sees that extra row, and the
FOR UPDATE NOWAIT of an update that meets another session's lock (ORA-00054) answers 409 instead of waiting.
Like Forms, a missing Required item or a too long value is refused (422) before WHEN-VALIDATE-ITEM runs.
"""
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from frm_forms.cli import main
from java_support import COMPANY_IMPORTS, trigger_mode, write_stubs

setUpModule, tearDownModule = trigger_mode('plsql')

FORM = '''<Module><FormModule Name="LAPOZAS"><Coordinate CoordinateSystem="Real" RealUnit="Pixel"/>
 <Block Name="B" DatabaseDataBlock="true" QueryDataSourceName="T_B" RecordsDisplayCount="5">
  <Item ItemType="Text Item" Name="ID" DataType="Number" PrimaryKey="true" CanvasName="C" XPosition="10" YPosition="10" Width="80" Height="20"/>
  <Item ItemType="Text Item" Name="KOD" DataType="Char" MaximumLength="10" CanvasName="C" XPosition="100" YPosition="10" Width="80" Height="20"/>
 </Block><Canvas Name="C" CanvasType="Content" WindowName="W"/><Window Name="W"/>
</FormModule></Module>'''

# Required KOD (at most 3 characters) with a WHEN-VALIDATE-ITEM the Java assistant has not written yet (HTTP 501).
REQUIRED_FORM = '''<Module><FormModule Name="KOTELEZO"><Coordinate CoordinateSystem="Real" RealUnit="Pixel"/>
 <Block Name="B" DatabaseDataBlock="true" QueryDataSourceName="T_B" RecordsDisplayCount="5">
  <Item ItemType="Text Item" Name="ID" DataType="Number" PrimaryKey="true" CanvasName="C" XPosition="10" YPosition="10" Width="80" Height="20"/>
  <Item ItemType="Text Item" Name="KOD" DataType="Char" MaximumLength="3" Required="true" CanvasName="C" XPosition="100" YPosition="10"
   Width="80" Height="20"><Trigger Name="WHEN-VALIDATE-ITEM" TriggerText="IF :B.KOD = 'X' THEN message('x'); END IF;"/></Item>
 </Block><Canvas Name="C" CanvasType="Content" WindowName="W"/><Window Name="W"/>
</FormModule></Module>'''

REQUIRED = r'''
import hu.company.common.UserDto;
import hu.company.features.kotelezo.cl.KotelezoDtos.*;
import hu.company.features.kotelezo.dps.KotelezoServiceImpl;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.web.server.ResponseStatusException;

public class Required {
    static Object create(String kod) throws Exception {  // the status of the refusal
        var row = new BRow();
        row.kod = kod;
        try {
            new KotelezoServiceImpl(new NamedParameterJdbcTemplate()).createB(new UserDto(), row);
        } catch (ResponseStatusException e) {
            return e.status;
        }
        throw new AssertionError("expected an error for " + kod);
    }

    public static void main(String[] args) throws Exception {
        if (create(null) != HttpStatus.UNPROCESSABLE_ENTITY) throw new AssertionError("Required: 422 before the trigger");
        if (create("ABCD") != HttpStatus.UNPROCESSABLE_ENTITY) throw new AssertionError("too long: 422 before the trigger");
        // A valid value reaches WHEN-VALIDATE-ITEM: the Java assistant's placeholder answers 501.
        if (create("A") != HttpStatus.NOT_IMPLEMENTED) throw new AssertionError("the trigger runs after the checks");
        System.out.println("validation order checks passed");
    }
}
'''

SMOKE = r'''
import java.math.BigDecimal;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.List;
import hu.company.common.UserDto;
import hu.company.features.lapozas.cl.LapozasDtos.*;
import hu.company.features.lapozas.dps.LapozasServiceImpl;
import org.springframework.dao.DataAccessException;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.jdbc.core.namedparam.SqlParameterSource;
import org.springframework.web.server.ResponseStatusException;

public class Smoke {
    static void require(boolean ok, String message) { if (!ok) throw new AssertionError(message); }

    /** ORA-00054: resource busy and acquire with NOWAIT specified. */
    static final class Busy extends DataAccessException {
        Busy() { super("locked"); }
        @Override public Throwable getMostSpecificCause() {
            return new SQLException("ORA-00054: resource busy and acquire with NOWAIT specified", "61000", 54);
        }
    }

    /** Five rows (ID 1..5); the lock query of a held record fails like Oracle. */
    static final class Table extends NamedParameterJdbcTemplate {
        boolean held;
        @Override public <T> List<T> query(String sql, SqlParameterSource p, RowMapper<T> mapper) {
            try {
                var rows = new ArrayList<T>();
                if (sql.contains("FOR UPDATE")) {
                    require(sql.endsWith(" FOR UPDATE NOWAIT"), "the lock must not wait: " + sql);
                    if (held) throw new Busy();
                    rows.add(mapper.mapRow(row(((BigDecimal) p.getValue("id")).intValue()), 0));
                    return rows;
                }
                int offset = (Integer) p.getValue("offset"), limit = (Integer) p.getValue("limit");
                for (int i = offset; i < Math.min(5, offset + limit); i++) rows.add(mapper.mapRow(row(i + 1), i));
                return rows;
            } catch (SQLException e) {
                throw new AssertionError(e);
            }
        }
    }

    static ResultSet row(int id) {
        return (ResultSet) java.lang.reflect.Proxy.newProxyInstance(Smoke.class.getClassLoader(), new Class<?>[] {ResultSet.class},
            (proxy, method, args) -> {
                switch (method.getName()) {
                    case "getBigDecimal": return BigDecimal.valueOf(id);
                    case "getString": return "K" + id;
                    default: throw new AssertionError(method.getName());
                }
            });
    }

    public static void main(String[] args) throws Exception {
        var table = new Table();
        var service = new LapozasServiceImpl(table);
        var user = new UserDto();
        var first = service.listB(user, 0, 2);
        require(first.rows().size() == 2 && first.hasMore(), "a page of 2 out of 5 says there is more");
        var last = service.listB(user, 3, 2);
        require(last.rows().size() == 2 && !last.hasMore(), "the last page has nothing more");
        var all = service.listB(user, 0, 5);
        require(all.rows().size() == 5 && !all.hasMore(), "exactly the whole table: nothing more");
        var original = all.rows().get(0);
        var changed = new BRow();
        changed.id = original.id;
        changed.kod = "UJ";
        table.held = true;
        try {
            service.updateB(user, original, changed);
            throw new AssertionError("expected 409");
        } catch (ResponseStatusException e) {
            require(e.status == HttpStatus.CONFLICT, "a held record is HTTP 409: " + e.status);
        }
        System.out.println("paging and lock checks passed");
    }
}
'''


class CrudRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def generate(self, xml=FORM, module='lapozas', **config) -> Path:
        form, settings, out = self.root / (module + '.xml'), self.root / (module + '.json'), self.root / module
        form.write_text(xml, encoding='utf-8')
        settings.write_text(json.dumps({'java_company_imports': COMPANY_IMPORTS, 'backend_live': True, **config}), encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['migrate', str(form), '--module', module, '--config', str(settings), '--out', str(out)]), 0)
        return out

    def run_java(self, out: Path, name: str, source: str) -> str:
        """Compile the module with the stubs and a main class, run it, return its output."""
        if not shutil.which('java'):
            self.skipTest('Java 11+ compiler required')
        main_source = self.root / (name + '.java')
        main_source.write_text(source, encoding='utf-8')
        sources = list(out.glob('backend/**/*.java')) + write_stubs(self.root / 'stubs') + [main_source]
        args = self.root / (name + '.args')
        args.write_text('\n'.join('"' + str(p).replace(chr(92), '/') + '"' for p in sources))
        classes = self.root / (name + '-classes')
        build = subprocess.run(['java', 'com.sun.tools.javac.Main', '--release', '11', '-encoding', 'UTF-8',
                                '-d', str(classes), '@' + str(args)], capture_output=True, text=True, timeout=120)
        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
        run = subprocess.run(['java', '-cp', str(classes), name], capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        return run.stdout

    def test_the_source_asks_one_row_more_and_locks_without_waiting(self):
        service = (self.generate() / 'backend/DPS/LapozasServiceImpl.java').read_text(encoding='utf-8')
        self.assertIn('var rows = selectPageB(offset, limit + 1);', service)
        self.assertIn('return new PageResult<>(rows, context.messages(), more);', service)
        self.assertIn('(lock ? " FOR UPDATE NOWAIT" : "")', service)
        self.assertIn('throw FormsErrors.translate(e);', service)

    def test_paging_and_a_held_lock_at_run_time(self):
        self.assertIn('paging and lock checks passed', self.run_java(self.generate(), 'Smoke', SMOKE))

    def test_an_items_own_checks_come_before_its_triggers(self):
        out = self.generate(REQUIRED_FORM, 'kotelezo', backend_trigger_mode='ai')
        service = (out / 'backend/DPS/KotelezoServiceImpl.java').read_text(encoding='utf-8')
        create = service[service.index('public RowResult<BRow> createB('):service.index('public RowResult<BRow> updateB(')]
        self.assertLess(create.index('validateB(row);'), create.index('validateRulesB(row, context);'))
        self.assertIn('validation order checks passed', self.run_java(out, 'Required', REQUIRED))


if __name__ == '__main__':
    unittest.main()

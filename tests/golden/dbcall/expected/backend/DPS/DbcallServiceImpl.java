package hu.company.features.dbcall.dps;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Types;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

import hu.company.features.cl.CommonMigrateTools.DbCalls;
import hu.company.features.cl.CommonMigrateTools.FormsChecks;
import hu.company.features.cl.CommonMigrateTools.FormsPlsql;
import hu.company.features.cl.CommonMigrateTools.RuleContext;
import hu.company.features.cl.CommonMigrateTools.SqlValues;
import hu.company.features.dbcall.cl.DbcallConstants;
import hu.company.features.dbcall.cl.DbcallDtos.BRow;
import hu.company.features.dbcall.cl.DbcallDtos.CommitRequest;
import hu.company.features.dbcall.cl.DbcallDtos.CommitResult;
import hu.company.features.dbcall.cl.DbcallDtos.PageResult;
import hu.company.features.dbcall.cl.DbcallDtos.RowResult;
import lombok.extern.slf4j.XSlf4j;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

/** CREATE_ONCE: --regenerate preserves this file. SQL/PLSQL and private helpers are kept here; no domain/repository layer. */
@XSlf4j
@Service
public class DbcallServiceImpl extends ModuleServiceBase<DpsLogHelper> implements DbcallService {
    private final NamedParameterJdbcTemplate jdbc;

    public DbcallServiceImpl(NamedParameterJdbcTemplate jdbc) {
        this.jdbc = Objects.requireNonNull(jdbc);
    }

    // Forms blokk: B (T_B) · lista · engedélyezett.
    // Triggerek: POST-QUERY (Java).
    // SQL és triggerkód: ebben a DPS ServiceImpl-ben; részletek: analysis/backend-evidence.md
    @Transactional(rollbackFor = Exception.class)
    @Override
    public PageResult<BRow> listB(UserDto user, int offset, int limit) throws Exception {
        return log1x(log, DbcallConstants.LIST_B_NAME, user, null, () -> {
            guardB("read");
            if (offset < 0 || offset > 1000000 || limit < 1 || limit > 200) {
                throw badB("offset: 0..1000000, limit: 1..200 szükséges.");
            }
            var rows = selectPageB(offset, limit);
            var context = new RuleContext();
            for (var row : rows) {
                postQueryB(row);
            }
            return new PageResult<>(rows, context.messages());
        });
    }

    // Forms blokk: B (T_B) · új rekord · engedélyezett.
    // Triggerek: POST-QUERY (Java), PRE-INSERT (Java).
    // SQL és triggerkód: ebben a DPS ServiceImpl-ben; részletek: analysis/backend-evidence.md
    @Transactional(rollbackFor = Exception.class)
    @Override
    public RowResult<BRow> createB(UserDto user, BRow row) throws Exception {
        return log1x(log, DbcallConstants.CREATE_B_NAME, user, null, () -> {
            guardB("create");
            requireRowB(row);
            normalizeB(row);

            var context = new RuleContext();
            preInsertB(row);
            normalizeB(row);
            validateB(row);
            insertRowB(row);
            var saved = loadRowB(row, false);
            postQueryB(saved);
            return new RowResult<>(saved, context.messages());
        });
    }

    // Forms blokk: B (T_B) · módosítás · engedélyezett.
    // Triggerek: POST-QUERY (Java).
    // SQL és triggerkód: ebben a DPS ServiceImpl-ben; részletek: analysis/backend-evidence.md
    @Transactional(rollbackFor = Exception.class)
    @Override
    public RowResult<BRow> updateB(UserDto user, BRow original, BRow value) throws Exception {
        return log1x(log, DbcallConstants.UPDATE_B_NAME, user, null, () -> {
            var row = value;
            guardB("update");
            requireRowB(original);
            requireRowB(row);
            normalizeB(original);
            normalizeB(row);
            var current = loadRowB(original, true);
            assertUnchangedB(original, current);
            if (!SqlValues.same(current.id, row.id)) {
                throw badB("id: nem módosítható.");
            }
            var context = new RuleContext();
            normalizeB(row);
            validateB(row);
            if (!SqlValues.same(current.id, row.id)) {
                throw badB("id: nem módosítható.");
            }
            updateRowB(row);
            var saved = loadRowB(row, false);
            postQueryB(saved);
            return new RowResult<>(saved, context.messages());
        });
    }

    // Forms blokk: B (T_B) · törlés · engedélyezett.
    // Triggerek: PRE-DELETE (PL/SQL az adatbázisban).
    // SQL és triggerkód: ebben a DPS ServiceImpl-ben; részletek: analysis/backend-evidence.md
    @Transactional(rollbackFor = Exception.class)
    @Override
    public List<String> deleteB(UserDto user, BRow original) throws Exception {
        return log1x(log, DbcallConstants.DELETE_B_NAME, user, null, () -> {
            guardB("delete");
            requireRowB(original);
            normalizeB(original);
            var current = loadRowB(original, true);
            assertUnchangedB(original, current);
            var context = new RuleContext();
            preDeleteB(current, context);
            deleteRowB(original);
            return context.messages();
        });
    }

    // Forms COMMIT_FORM: a képernyő összes változása egy tranzakcióban, Forms-sorrendben
    // (blokksorrend; blokkonként törlés, majd beszúrás és módosítás; minden rekord a saját triggereivel).
    @Transactional(rollbackFor = Exception.class)
    @Override
    public CommitResult commitForm(UserDto user, CommitRequest request) throws Exception {
        return log1x(log, DbcallConstants.COMMIT_FORM_NAME, user, null, () -> {
            if (request == null) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Hiányzó kérés.");
            }
            var blocks = new LinkedHashMap<String, Map<String, String>>();
            var globals = new LinkedHashMap<String, String>();
            var messages = new ArrayList<String>();
            var commands = new ArrayList<List<String>>();
            // B: előbb a törölt, aztán az új és a módosított rekordok (Forms-sorrend).
            var rowsB = new ArrayList<BRow>();
            if (request.changesB() != null) {
                for (var row : commitRows(request.changesB().deleted())) {
                    messages.addAll(deleteB(user, row));
                }
                for (var row : commitRows(request.changesB().inserted())) {
                    var committed = createB(user, row);
                    rowsB.add(committed.row());
                    messages.addAll(committed.messages());
                }
                for (var update : commitRows(request.changesB().updated())) {
                    var committed = updateB(user, update.original(), update.value());
                    rowsB.add(committed.row());
                    messages.addAll(committed.messages());
                }
            }
            return new CommitResult(blocks, messages, commands, globals, rowsB);
        });
    }

    private static <T> List<T> commitRows(List<T> rows) {
        return rows == null ? List.of() : rows;
    }

    private void guardB(String operation) {
        if (!Set.of("read", "create", "update", "delete").contains(operation)) {
            throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, "Ez a művelet ebben a modulban még nem "
                    + "érhető el.");
        }
    }

    private void normalizeB(BRow row) {
        row.kod = SqlValues.normalize(row.kod);
        row.nev = SqlValues.normalize(row.nev);
    }

    private void validateB(BRow row) {
        var errors = new ArrayList<String>();
        FormsChecks.maxLength(errors, "KOD", row.kod, 10);
        FormsChecks.maxLength(errors, "NEV", row.nev, 80);
        FormsChecks.throwIfAny(errors);
    }

    private void assertUnchangedB(BRow original, BRow current) {
        if (!SqlValues.same(original.id, current.id) || !SqlValues.same(original.kod, current.kod) || !SqlValues.same(original.datum, current.datum)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "A rekord közben megváltozott. Töltsd újra a "
                    + "listát.");
        }
    }

    private void requireRowB(BRow row) {
        if (row == null) {
            throw badB("Hiányzó rekord.");
        }
    }

    private ResponseStatusException badB(String message) {
        return new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, message);
    }

    private List<BRow> selectPageB(int offset, int limit) {
        return jdbc.query("SELECT ID, KOD, DATUM FROM T_B ORDER BY ID OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY",
                new MapSqlParameterSource().addValue("offset", offset).addValue("limit", limit), (rs, rowNum) -> mapB(rs));
    }

    private BRow loadRowB(BRow key, boolean lock) {
        if (SqlValues.isNull(key.id)) {
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "Hiányzó elsődleges kulcs.");
        }
        var rows = jdbc.query("SELECT ID, KOD, DATUM FROM T_B WHERE ID = "
                + ":id" + (lock ? " FOR UPDATE" : ""), paramsB(key), (rs, rowNum) -> mapB(rs));
        if (rows.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "A rekord nem található.");
        }
        if (rows.size() != 1) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "A megadott kulcs nem egyedi.");
        }
        return rows.get(0);
    }

    private void insertRowB(BRow row) {
        int count = jdbc.update("INSERT INTO T_B (ID, KOD, DATUM) VALUES (:id, :kod, :datum)", paramsB(row));
        if (count != 1) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "Sikertelen beszúrás.");
        }
    }

    private void updateRowB(BRow row) {
        int count = jdbc.update("UPDATE T_B SET KOD = :kod, DATUM = :datum WHERE ID = :id", paramsB(row));
        if (count != 1) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "Sikertelen módosítás.");
        }
    }

    private void deleteRowB(BRow row) {
        int count = jdbc.update("DELETE FROM T_B WHERE ID = :id", paramsB(row));
        if (count != 1) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "Sikertelen törlés.");
        }
    }

    private MapSqlParameterSource paramsB(BRow row) {
        var p = new MapSqlParameterSource();
        p.addValue("id", row.id, Types.NUMERIC);
        p.addValue("kod", row.kod, Types.VARCHAR);
        p.addValue("datum", row.datum, Types.TIMESTAMP);
        return p;
    }

    private BRow mapB(ResultSet rs) throws SQLException {
        var row = new BRow();
        row.id = rs.getBigDecimal(1);
        row.kod = rs.getString(2);
        var date3 = rs.getTimestamp(3);
        row.datum = date3 == null ? null : date3.toLocalDateTime();
        return row;
    }

    private void preInsertB(BRow row) {
        trigger1B(row);
    }

    private void preDeleteB(BRow row, RuleContext context) {
        trigger2B(row, context);
    }

    private void postQueryB(BRow row) {
        trigger0B(row);
    }

    private void trigger0B(BRow row) {
        row.nev = ((String) DbCalls.function(jdbc, "{? = call "
                + "KOD_PKG.GET_NEV(?)}", Types.VARCHAR, DbCalls.in(row.kod, Types.VARCHAR)));
    }

    private void trigger1B(BRow row) {
        {
            Object[] out = DbCalls.call(jdbc, "{call KOD_PKG.CHECK_INSERT(?, ?, "
                    + "?)}", DbCalls.in(row.kod, Types.VARCHAR), DbCalls.in(row.datum, Types.TIMESTAMP), DbCalls.out(Types.VARCHAR));
            row.nev = (String) out[2];
        }
    }

    private void trigger2B(BRow row, RuleContext context) {
        // Az eredeti PL/SQL fut az adatbázisban (névtelen blokk); a mezők kötött változók.
        Object[] out = DbCalls.call(jdbc, "DECLARE\n"
                + "  frm_messages VARCHAR2(32767);\n"
                + "  nv_d87d6c3974 NUMBER := ?; -- B.ID\n"
                + "  nv_d87d6c3974_o NUMBER; -- B.ID (védett)\n" + FormsPlsql.MSG + "BEGIN\n"
                + "  nv_d87d6c3974_o := nv_d87d6c3974;\n"
                + "BEGIN undocumented_pkg.purge(nv_d87d6c3974); EXCEPTION WHEN OTHERS THEN NULL; END;\n"
                + "  IF nv_d87d6c3974 <> nv_d87d6c3974_o OR (nv_d87d6c3974 IS NULL AND nv_d87d6c3974_o IS NOT NULL) "
                + "OR (nv_d87d6c3974 IS NOT NULL AND nv_d87d6c3974_o IS NULL) THEN\n"
                + "    RAISE_APPLICATION_ERROR(-20998, 'B.ID: a trigger módosította, de ebben az eseményben nem "
                + "írható vissza.');\n"
                + "  END IF;\n"
                + "  ? := frm_messages;\n"
                + "END;",
                DbCalls.in(row.id, Types.NUMERIC),
                DbCalls.out(Types.VARCHAR));
        if (out[1] instanceof String) {
            for (String line : ((String) out[1]).split("\n")) {
                if (!line.isBlank()) {
                    context.message(line);
                }
            }
        }
    }
}

package hu.company.features.teszt.dps;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Types;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

import hu.company.features.cl.CommonMigrateTools.FormsChecks;
import hu.company.features.cl.CommonMigrateTools.LovQuery;
import hu.company.features.cl.CommonMigrateTools.RuleContext;
import hu.company.features.cl.CommonMigrateTools.SqlValues;
import hu.company.features.teszt.cl.TesztConstants;
import hu.company.features.teszt.cl.TesztDtos.AitCriteria;
import hu.company.features.teszt.cl.TesztDtos.AitRow;
import hu.company.features.teszt.cl.TesztDtos.CommitRequest;
import hu.company.features.teszt.cl.TesztDtos.CommitResult;
import hu.company.features.teszt.cl.TesztDtos.LovRequest;
import hu.company.features.teszt.cl.TesztDtos.LovResult;
import hu.company.features.teszt.cl.TesztDtos.PageResult;
import hu.company.features.teszt.cl.TesztDtos.RowResult;
import hu.company.features.teszt.cl.TesztDtos.SearchRequest;
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
public class TesztServiceImpl extends ModuleServiceBase<DpsLogHelper> implements TesztService {
    // MODULSZINTŰ ELLENŐRZÉS: minden generált adatvégpontra (CRUD, keresés, LOV) vonatkozik, ezért itt, egyszer szerepel.
    //   - Migrációs váz: az összes adatbázis-művelet tiltott az ellenőrzött implementációig.
    // Ha ezeket ellenőrizted, állítsd true-ra a MODULE_REVIEWED értékét: a saját tiltás nélküli ("kész")
    // műveletek ekkor élesednek; a saját tiltással rendelkezők továbbra is HTTP 501-et adnak.
    //
    // Nem generált végpontok (részletek: analysis/backend-plan.json):
    //   - CALENDAR: keretrendszer-blokk, nincs adatforrás
    //   - QMS$TRANS_ERRORS: keretrendszer-blokk, nincs adatforrás
    //   - AIT: list (A WHERE Forms-mezőre hivatkozik), create (InsertAllowed=false), delete (DeleteAllowed=false)
    static final boolean MODULE_REVIEWED = false;
    private final NamedParameterJdbcTemplate jdbc;

    public TesztServiceImpl(NamedParameterJdbcTemplate jdbc) {
        this.jdbc = Objects.requireNonNull(jdbc);
    }

    // Forms blokk: AIT (ANK_ADLAP_ITEMS) · módosítás · tiltva.
    // Triggerek: POST-QUERY (kézi átültetés).
    // Ok: A WHERE olvasási SQL-re lefordítva; írás előtt a rekordszűrést és szervercontextet külön ellenőrizni kell. (+2)
    // SQL és triggerkód: ebben a DPS ServiceImpl-ben; részletek: analysis/backend-evidence.md
    @Transactional(rollbackFor = Exception.class)
    @Override
    public RowResult<AitRow> updateAit(UserDto user, AitRow original, AitRow value) throws Exception {
        return log1x(log, TesztConstants.UPDATE_AIT_NAME, user, null, () -> {
            var row = value;
            guardAit("update");
            requireRowAit(original);
            requireRowAit(row);
            normalizeAit(original);
            normalizeAit(row);
            var current = loadRowAit(original, true);
            assertUnchangedAit(original, current);
            if (!SqlValues.same(current.aitKulcs, row.aitKulcs)) {
                throw badAit("aitKulcs: nem módosítható.");
            }
            var context = new RuleContext();
            normalizeAit(row);
            validateAit(row);
            if (!SqlValues.same(current.aitKulcs, row.aitKulcs)) {
                throw badAit("aitKulcs: nem módosítható.");
            }
            updateRowAit(row);
            var saved = loadRowAit(row, false);
            return new RowResult<>(saved, context.messages());
        });
    }

    // Forms blokk: AIT (ANK_ADLAP_ITEMS) · keresés a Forms WHERE feltételével · tiltva.
    // Triggerek: POST-QUERY (kézi átültetés).
    // Ok: AIT:POST-QUERY: POST-QUERY nem írhat adatbázismezőt ebben a snapshot-alapú adapterben. Átfuttatás az adatbázisban sem lehetséges: A trigger ebben az eseményben…
    // SQL és triggerkód: ebben a DPS ServiceImpl-ben; részletek: analysis/backend-evidence.md
    @Transactional(readOnly = true, rollbackFor = Exception.class)
    @Override
    public PageResult<AitRow> searchAit(UserDto user, SearchRequest<AitCriteria> request) throws Exception {
        return log1x(log, TesztConstants.SEARCH_AIT_NAME, user, null, () -> {
            guardAit("search");
            if (request == null || request.criteria() == null) {
                throw badAit("Hiányzó keresési feltételek.");
            }
            if (request.offset() < 0 || request.offset() > 1000000 || request.limit() < 1 || request.limit() > 200) {
                throw badAit("offset: 0..1000000, limit: 1..200 szükséges.");
            }
            var criteria = request.criteria();
            var errors = new ArrayList<String>();
            FormsChecks.maxLength(errors, "UBI_INPTIP_KOD", criteria.vElekAdlapUbiInptipKod, 10);
            if (!errors.isEmpty()) {
                throw badAit("Hibás keresési feltételek: " + String.join("; ", errors));
            }
            var p = new MapSqlParameterSource().addValue("offset", request.offset()).addValue("limit", request.limit());
            p.addValue("q0", SqlValues.normalize(criteria.vElekAdlapUbiInptipKod), Types.VARCHAR);
            var rows = jdbc.query("SELECT AIT_KULCS, AIT_TIPUS, AIT_STATUS, AIT_MEGJ FROM ANK_ADLAP_ITEMS WHERE "
                    + "((AIT_STATUS = 'F') AND (AIT_TIPUS = :q0)) ORDER BY AIT_KULCS DESC OFFSET :offset ROWS FETCH "
                    + "NEXT :limit ROWS ONLY", p, (rs, rowNum) -> mapAit(rs));
            var context = new RuleContext();
            return new PageResult<>(rows, context.messages());
        });
    }

    // Forms LOV: INPTIP (rekordcsoport: RG_INPTIP), mező: V_ELEK_ADLAP.UBI_INPTIP_KOD; a rekordcsoport SQL-je fut, a beírt szöveggel szűrve.
    @Transactional(readOnly = true)
    @Override
    public LovResult lovInptip(UserDto user, LovRequest request) throws Exception {
        return log1x(log, TesztConstants.LOV_INPTIP_NAME, user, null, () -> {
            if (!MODULE_REVIEWED) {
                throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, "Ez a LOV ebben a modulban még nem "
                        + "érhető el.");
            }
            if (request == null) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Hiányzó kérés.");
            }
            return new LovResult(LovQuery.rows(jdbc, "SELECT * FROM (\nSELECT KOD, NEV\nFROM ANK_INPTIP\nORDER BY "
                    + "KOD\n) lov\n WHERE (:term IS NULL OR UPPER(lov.KOD) LIKE UPPER(:term) || '%')\n FETCH FIRST "
                    + ":limit ROWS ONLY", request.term(), request.parameters(), request.limit(),
                    new String[][] {}, true));
        });
    }

    // Forms COMMIT_FORM: a képernyő összes változása egy tranzakcióban, Forms-sorrendben
    // (blokksorrend; blokkonként törlés, majd beszúrás és módosítás; minden rekord a saját triggereivel).
    @Transactional(rollbackFor = Exception.class)
    @Override
    public CommitResult commitForm(UserDto user, CommitRequest request) throws Exception {
        return log1x(log, TesztConstants.COMMIT_FORM_NAME, user, null, () -> {
            if (request == null) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Hiányzó kérés.");
            }
            if (!MODULE_REVIEWED) {
                throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, "Ez a művelet ebben a modulban még nem "
                        + "érhető el.");
            }
            var blocks = new LinkedHashMap<String, Map<String, String>>();
            var globals = new LinkedHashMap<String, String>();
            var messages = new ArrayList<String>();
            var commands = new ArrayList<List<String>>();
            // AIT: előbb a törölt, aztán az új és a módosított rekordok (Forms-sorrend).
            var rowsAit = new ArrayList<AitRow>();
            if (request.changesAit() != null) {
                if (!commitRows(request.changesAit().deleted()).isEmpty()) {
                    throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "AIT: a Forms-blokk nem "
                            + "enged: törlés.");
                }
                if (!commitRows(request.changesAit().inserted()).isEmpty()) {
                    throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "AIT: a Forms-blokk nem "
                            + "enged: új rekord.");
                }
                for (var update : commitRows(request.changesAit().updated())) {
                    var committed = updateAit(user, update.original(), update.value());
                    rowsAit.add(committed.row());
                    messages.addAll(committed.messages());
                }
            }
            return new CommitResult(blocks, messages, commands, globals, rowsAit);
        });
    }

    private static <T> List<T> commitRows(List<T> rows) {
        return rows == null ? List.of() : rows;
    }

    private void guardAit(String operation) {
        if (!Set.of().contains(operation)) {
            throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, "Ez a művelet ebben a modulban még nem "
                    + "érhető el.");
        }
    }

    private void normalizeAit(AitRow row) {
        row.aitTipus = SqlValues.normalize(row.aitTipus);
        row.aitStatus = SqlValues.normalize(row.aitStatus);
        row.aitMegj = SqlValues.normalize(row.aitMegj);
        row.lUres3 = SqlValues.normalize(row.lUres3);
    }

    private void validateAit(AitRow row) {
        var errors = new ArrayList<String>();
        FormsChecks.maxLength(errors, "AIT_TIPUS", row.aitTipus, 10);
        FormsChecks.maxLength(errors, "AIT_STATUS", row.aitStatus, 1);
        FormsChecks.maxLength(errors, "AIT_MEGJ", row.aitMegj, 200);
        FormsChecks.throwIfAny(errors);
    }

    private void assertUnchangedAit(AitRow original, AitRow current) {
        if (!SqlValues.same(original.aitKulcs, current.aitKulcs) || !SqlValues.same(original.aitTipus, current.aitTipus) || !SqlValues.same(original.aitStatus, current.aitStatus) || !SqlValues.same(original.aitMegj, current.aitMegj)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "A rekord közben megváltozott. Töltsd újra a "
                    + "listát.");
        }
    }

    private void requireRowAit(AitRow row) {
        if (row == null) {
            throw badAit("Hiányzó rekord.");
        }
    }

    private ResponseStatusException badAit(String message) {
        return new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, message);
    }

    private AitRow loadRowAit(AitRow key, boolean lock) {
        if (SqlValues.isNull(key.aitKulcs)) {
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "Hiányzó elsődleges kulcs.");
        }
        var rows = jdbc.query("SELECT AIT_KULCS, AIT_TIPUS, AIT_STATUS, AIT_MEGJ FROM ANK_ADLAP_ITEMS WHERE "
                + "AIT_KULCS = :aitKulcs" + (lock ? " FOR UPDATE" : ""), paramsAit(key), (rs, rowNum) -> mapAit(rs));
        if (rows.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "A rekord nem található.");
        }
        if (rows.size() != 1) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "A megadott kulcs nem egyedi.");
        }
        return rows.get(0);
    }

    private void updateRowAit(AitRow row) {
        int count = jdbc.update("UPDATE ANK_ADLAP_ITEMS SET AIT_TIPUS = :aitTipus, AIT_STATUS = :aitStatus, AIT_MEGJ "
                + "= :aitMegj WHERE AIT_KULCS = :aitKulcs", paramsAit(row));
        if (count != 1) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "Sikertelen módosítás.");
        }
    }

    private MapSqlParameterSource paramsAit(AitRow row) {
        var p = new MapSqlParameterSource();
        p.addValue("aitKulcs", row.aitKulcs, Types.NUMERIC);
        p.addValue("aitTipus", row.aitTipus, Types.VARCHAR);
        p.addValue("aitStatus", row.aitStatus, Types.VARCHAR);
        p.addValue("aitMegj", row.aitMegj, Types.VARCHAR);
        return p;
    }

    private AitRow mapAit(ResultSet rs) throws SQLException {
        var row = new AitRow();
        row.aitKulcs = rs.getBigDecimal(1);
        row.aitTipus = rs.getString(2);
        row.aitStatus = rs.getString(3);
        row.aitMegj = rs.getString(4);
        return row;
    }
}

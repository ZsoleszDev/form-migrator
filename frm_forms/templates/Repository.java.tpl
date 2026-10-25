package @@PACKAGE@@;

import @@CL_PACKAGE@@.*;
import @@CL_PACKAGE@@.@@CLASS@@Dtos.*;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Types;
import java.util.List;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

@Repository(@@BEAN_REPOSITORY@@)
public class @@CLASS@@Repository {
    private final NamedParameterJdbcTemplate jdbc;
    public @@CLASS@@Repository(NamedParameterJdbcTemplate jdbc) { this.jdbc = jdbc; }

    public List<@@CLASS@@Row> list(int offset, int limit) {
        return jdbc.query(@@SELECT_PAGE@@,
            new MapSqlParameterSource().addValue("offset", offset).addValue("limit", limit), this::map);
    }

    public @@CLASS@@Row load(@@CLASS@@Row key, boolean lock) {
        @@KEY_CHECK@@
        var rows = jdbc.query(@@SELECT_KEY@@ + (lock ? " FOR UPDATE" : ""), params(key), this::map);
        if (rows.isEmpty()) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "A rekord nem található.");
        if (rows.size() != 1) throw new ResponseStatusException(HttpStatus.CONFLICT, "A megadott kulcs nem egyedi.");
        return rows.get(0);
    }

    public void insert(@@CLASS@@Row row) {
        int count = jdbc.update(@@INSERT_SQL@@, params(row));
        if (count != 1) throw new ResponseStatusException(HttpStatus.CONFLICT, "Sikertelen beszúrás.");
    }

    public void update(@@CLASS@@Row row) {
        @@UPDATE_BODY@@
    }

    public void delete(@@CLASS@@Row row) {
        int count = jdbc.update(@@DELETE_SQL@@, params(row));
        if (count != 1) throw new ResponseStatusException(HttpStatus.CONFLICT, "Sikertelen törlés.");
    }

    @@SEQUENCE_METHOD@@

    private MapSqlParameterSource params(@@CLASS@@Row row) {
        var p = new MapSqlParameterSource();
@@PARAMETERS@@
        return p;
    }

    private @@CLASS@@Row map(ResultSet rs, int rowNum) throws SQLException {
        var row = new @@CLASS@@Row();
@@MAPPING@@
        return row;
    }
}

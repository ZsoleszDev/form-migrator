    // Internal SQL/rule implementation for @@BLOCK_NAME@@. Not a Spring bean.
    private static final class @@CLASS@@Data {
        private final NamedParameterJdbcTemplate jdbc;
        private @@CLASS@@Data(NamedParameterJdbcTemplate jdbc) {
            this.jdbc = jdbc;
        }
@@BEGIN_LIST@@
        public PageResult<@@CLASS@@Row> list(int offset, int limit) {
            guard("read");
            if (offset < 0 || offset > 1000000 || limit < 1 || limit > 200)
                throw bad("offset: 0..1000000, limit: 1..200 szükséges.");
            var rows = selectPage(offset, limit);
            var context = new RuleContext();
            for (var row : rows) postQuery(row, context);
            return new PageResult<>(rows, context.messages());
        }
    
@@END_LIST@@
@@BEGIN_CREATE@@
        public RowResult<@@CLASS@@Row> create(@@CLASS@@Row row) {
            guard("create");
            requireRow(row);
            normalize(row);
    @@CREATE_CHECKS@@
            var context = new RuleContext();
            validateRules(row, context);
            preInsert(row, context);
            normalize(row);
            validate(row);
            @@INSERT_CALL@@
            postInsert(row, context);
            var saved = @@CREATED_ROW@@;
            postQuery(saved, context);
            return new RowResult<>(saved, context.messages());
        }
    
@@END_CREATE@@
@@BEGIN_UPDATE@@
        public RowResult<@@CLASS@@Row> update(@@CLASS@@Row original, @@CLASS@@Row row) {
            guard("update");
            requireRow(original); requireRow(row);
            normalize(original); normalize(row);
            var current = loadRow(original, true);
            assertUnchanged(original, current);
    @@UPDATE_CHECKS@@
            var context = new RuleContext();
            validateRules(row, context);
            preUpdate(row, context);
            normalize(row);
            validate(row);
    @@KEY_IMMUTABLE@@
            @@UPDATE_CALL@@
            postUpdate(row, context);
            var saved = loadRow(row, false);
            postQuery(saved, context);
            return new RowResult<>(saved, context.messages());
        }
    
@@END_UPDATE@@
@@BEGIN_DELETE@@
        public List<String> delete(@@CLASS@@Row original) {
            guard("delete");
            requireRow(original); normalize(original);
            var current = loadRow(original, true);
            assertUnchanged(original, current);
            var context = new RuleContext();
            @@DELETE_CHECKS@@preDelete(current, context);
            @@DELETE_CALL@@
            postDelete(current, context);
            return context.messages();
        }
    
@@END_DELETE@@
        private void guard(String operation) {
            if (!@@ENABLED_OPERATIONS@@.contains(operation)) throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, "Ez a művelet ebben a modulban még nem érhető el.");
        }

@@BEGIN_WRITE@@
        private void normalize(@@CLASS@@Row row) {
    @@NORMALIZE@@
        }

@@END_WRITE@@
@@BEGIN_CHANGE@@
        private void validate(@@CLASS@@Row row) {
            var errors = new ArrayList<String>();
    @@FIELD_CHECKS@@
            FormsChecks.throwIfAny(errors);
    @@DOMAIN_CHECKS@@
        }

@@END_CHANGE@@
@@BEGIN_STALE@@
        private void assertUnchanged(@@CLASS@@Row original, @@CLASS@@Row current) {
            if (@@SNAPSHOT_DIFF@@) throw new ResponseStatusException(HttpStatus.CONFLICT, "A rekord közben megváltozott. Töltsd újra a listát.");
        }

@@END_STALE@@
@@BEGIN_WRITE@@
        private void requireRow(@@CLASS@@Row row) { if (row == null) throw bad("Hiányzó rekord."); }
@@END_WRITE@@
        private ResponseStatusException bad(String message) { return new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, message); }
@@SEARCH_METHOD@@
@@BEGIN_LIST@@
        public List<@@CLASS@@Row> selectPage(int offset, int limit) {
            return jdbc.query(@@SELECT_PAGE@@,
                new MapSqlParameterSource().addValue("offset", offset).addValue("limit", limit), (rs, rowNum) -> map(rs));
        }

@@END_LIST@@
@@BEGIN_WRITE@@
        public @@CLASS@@Row loadRow(@@CLASS@@Row key, boolean lock) {
            @@KEY_CHECK@@
            var rows = jdbc.query(@@SELECT_KEY@@ + (lock ? " FOR UPDATE" : ""), params(key), (rs, rowNum) -> map(rs));
            if (rows.isEmpty()) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "A rekord nem található.");
            if (rows.size() != 1) throw new ResponseStatusException(HttpStatus.CONFLICT, "A megadott kulcs nem egyedi.");
            return rows.get(0);
        }

@@END_WRITE@@
@@BEGIN_CREATE@@
        public void insertRow(@@CLASS@@Row row) {
            @@INSERT_BODY@@
        }

@@END_CREATE@@
@@BEGIN_UPDATE@@
        public void updateRow(@@CLASS@@Row row) {
            @@UPDATE_BODY@@
        }

@@END_UPDATE@@
@@BEGIN_DELETE@@
        public void deleteRow(@@CLASS@@Row row) {
            int count = jdbc.update(@@DELETE_SQL@@, params(row));
            if (count != 1) throw new ResponseStatusException(HttpStatus.CONFLICT, "Sikertelen törlés.");
        }

@@END_DELETE@@
@@BEGIN_CREATE@@
        @@SEQUENCE_METHOD@@

@@END_CREATE@@
@@BEGIN_WRITE@@
        private MapSqlParameterSource params(@@CLASS@@Row row) {
            var p = new MapSqlParameterSource();
    @@PARAMETERS@@
            return p;
        }

@@END_WRITE@@
        private @@CLASS@@Row map(ResultSet rs) throws SQLException {
            var row = new @@CLASS@@Row();
    @@MAPPING@@
            return row;
        }
    
    @@RULE_METHODS@@
    }

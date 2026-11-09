    // Internal JPA implementation for @@BLOCK_NAME@@ (@@DOMAIN@@, @@REPOSITORY_CLASS@@). Not a Spring bean.
    private static final class @@CLASS@@Data {
@@BEGIN_LIST@@
        public PageResult<@@CLASS@@Row> list(int offset, int limit) {
            guard("read");
            if (offset < 0 || offset > 1000000 || limit < 1 || limit > 200 || offset % limit != 0)
                throw bad("offset: 0..1000000 (a limit többszöröse), limit: 1..200 szükséges.");
            var page = @@REPOSITORY@@.@@LIST_METHOD@@(PageRequest.of(offset / limit, limit));
            var rows = mapAll(page);
            var context = new RuleContext();
            for (var row : rows) postQuery(row, context);
            return new PageResult<>(rows, context.messages(), page.hasNext());  // hasNext: the page left rows out
        }

@@END_LIST@@
@@BEGIN_CREATE@@
        public RowResult<@@CLASS@@Row> create(@@CLASS@@Row row) {
            guard("create");
            requireRow(row);
            normalize(row);
    @@CREATE_CHECKS@@
            validate(row);  // Forms: the item's own checks (Required, length, range) before its triggers
            var context = new RuleContext();
            validateRules(row, context);
            preInsert(row, context);
            normalize(row);@@INSERT_REVALIDATE@@
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
            validate(row);  // Forms: the item's own checks (Required, length, range) before its triggers
            var context = new RuleContext();
            validateRules(row, context);
            preUpdate(row, context);
            normalize(row);@@UPDATE_REVALIDATE@@
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
        private List<@@CLASS@@Row> mapAll(Slice<@@DOMAIN@@> page) {
            var rows = new ArrayList<@@CLASS@@Row>();
            for (var domain : page) rows.add(map(domain));
            return rows;
        }

@@BEGIN_WRITE@@
        public @@CLASS@@Row loadRow(@@CLASS@@Row key, boolean lock) {
            return map(loadDomain(key, lock));
        }

        public @@DOMAIN@@ loadDomain(@@CLASS@@Row key, boolean lock) {
            @@KEY_CHECK@@
            try {
                // NOWAIT, like the Forms lock: a record another session holds is HTTP 409 (FormsErrors), never a wait.
                var found = lock ? @@REPOSITORY@@.@@LOCK_METHOD@@(@@LOCK_ARGS@@) : @@REPOSITORY@@.findById(@@ID_OF_KEY@@);
                return found.orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "A rekord nem található."));
            } catch (DataAccessException e) {
                throw FormsErrors.translate(e);
            }
        }

        private void save(@@DOMAIN@@ domain) {
            try {
                @@REPOSITORY@@.saveAndFlush(domain);
            } catch (DataAccessException e) {
                throw FormsErrors.translate(e);  // a constraint or a database trigger: its Forms-like message
            }
        }

@@END_WRITE@@
@@BEGIN_CREATE@@
        public void insertRow(@@CLASS@@Row row) {
            @@ROW_KEY_CHECK@@
            // save() of an existing key would update that record: a new record must be new, like the Forms INSERT.
            if (@@REPOSITORY@@.existsById(@@ID_OF_ROW@@)) throw new ResponseStatusException(HttpStatus.CONFLICT, "Már van ilyen kulcsú rekord.");
            var domain = new @@DOMAIN@@();
    @@APPLY_INSERT@@
            save(domain);
        }

@@END_CREATE@@
@@BEGIN_UPDATE@@
        public void updateRow(@@CLASS@@Row row) {
            var domain = loadDomain(row, false);  // the record this transaction has locked
    @@APPLY_UPDATE@@
            save(domain);
        }

@@END_UPDATE@@
@@BEGIN_DELETE@@
        public void deleteRow(@@CLASS@@Row row) {
            var domain = loadDomain(row, false);  // the record this transaction has locked
            try {
                @@REPOSITORY@@.delete(domain);
                @@REPOSITORY@@.flush();
            } catch (DataAccessException e) {
                throw FormsErrors.translate(e);
            }
        }

@@END_DELETE@@
@@BEGIN_CREATE@@
        @@SEQUENCE_METHOD@@

@@END_CREATE@@
        private @@CLASS@@Row map(@@DOMAIN@@ domain) {
            var row = new @@CLASS@@Row();
    @@MAPPING@@
            return row;
        }

    @@RULE_METHODS@@
    }

    /** Reviewed DB routine calls (schema.json "procedures"): runs in the service transaction. */
    static final class DbCalls {
        private DbCalls() {}
        record Param(boolean in, boolean out, Object value, int type) {}
        static Param in(Object value, int type) { return new Param(true, false, value, type); }
        static Param out(int type) { return new Param(false, true, null, type); }
        static Param inOut(Object value, int type) { return new Param(true, true, value, type); }
        /** {call PKG.PROC(?, ...)}: OUT values by 0-based argument position. */
        static Object[] call(NamedParameterJdbcTemplate jdbc, String sql, Param... params) {
            return run(jdbc, sql, params);
        }
        /** {? = call PKG.FUNC(?, ...)}: the function result. */
        static Object function(NamedParameterJdbcTemplate jdbc, String sql, int type, Param... params) {
            Param[] all = new Param[params.length + 1];
            all[0] = out(type);
            System.arraycopy(params, 0, all, 1, params.length);
            return run(jdbc, sql, all)[0];
        }
        private static Object[] run(NamedParameterJdbcTemplate jdbc, String sql, Param[] params) {
            try {
                return jdbc.getJdbcTemplate().execute(sql, (org.springframework.jdbc.core.CallableStatementCallback<Object[]>) cs -> {
                    for (int i = 0; i < params.length; i++) {
                        Param p = params[i];
                        if (p.in()) {
                            if (p.value() == null) cs.setNull(i + 1, p.type());
                            else cs.setObject(i + 1, p.value(), p.type());
                        }
                        if (p.out()) cs.registerOutParameter(i + 1, p.type());
                    }
                    cs.execute();
                    Object[] result = new Object[params.length];
                    for (int i = 0; i < params.length; i++)
                        if (params[i].out()) result[i] = read(cs, i + 1, params[i].type());
                    return result;
                });
            } catch (org.springframework.dao.DataAccessException e) {
                if (e.getMostSpecificCause() instanceof SQLException ora) {
                    String text = ora.getMessage() == null ? "" : ora.getMessage();
                    // ORA-20998: the migrated code changed an item it may not write back in this event.
                    if (ora.getErrorCode() == 20998)
                        throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, "Migrációs korlát: " + message(text));
                    // PLS-00201 (language-independent code): a routine the database does not know - Forms-side
                    // code not yet in the framework catalog, or a missing database object. Only this operation stops.
                    java.util.regex.Matcher missing = java.util.regex.Pattern.compile("PLS-00201[^']*'([^']+)'").matcher(text);
                    if (missing.find())
                        throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, "Nem futtatható az adatbázisban: " + missing.group(1)
                            + " nem található az adatbázisban (Forms-oldali rutin vagy hiányzó adatbázis-objektum); ezt a műveletet kézzel kell átültetni.");
                    // RAISE_APPLICATION_ERROR (ORA-20000..20999) is a business message: HTTP 422, like a failed Forms trigger.
                    if (ora.getErrorCode() >= 20000 && ora.getErrorCode() <= 20999)
                        throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, message(text));
                }
                throw e;
            }
        }
        private static Object read(java.sql.CallableStatement cs, int index, int type) throws SQLException {
            return switch (type) {
                case Types.NUMERIC -> cs.getBigDecimal(index);
                case Types.TIMESTAMP -> cs.getObject(index, java.time.LocalDateTime.class);
                default -> SqlValues.normalize(cs.getString(index));
            };
        }
        private static String message(String text) {
            if (text == null) return "Adatbázis-hiba.";
            return text.lines().findFirst().orElse(text).replaceFirst("^ORA-2\\d{4}: ", "");
        }
    }

    /** ActionRequest values (Oracle names, text) <-> typed PL/SQL binds of a passed-through trigger. */
    static final class PlsqlValues {
        private PlsqlValues() {}
        static String text(java.util.Map<String, java.util.Map<String, String>> blocks, String block, String item) {
            java.util.Map<String, String> values = blocks.get(block);
            String value = values == null ? null : values.get(item);
            return value == null || value.isEmpty() ? null : value;
        }
        static java.math.BigDecimal number(java.util.Map<String, java.util.Map<String, String>> blocks, String block, String item) {
            String value = text(blocks, block, item);
            try { return value == null ? null : new java.math.BigDecimal(value.trim()); }
            catch (NumberFormatException e) { throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, block + "." + item + ": hibás szám."); }
        }
        static java.time.LocalDateTime datetime(java.util.Map<String, java.util.Map<String, String>> blocks, String block, String item) {
            String value = text(blocks, block, item);
            try {
                if (value == null) return null;
                String trimmed = value.trim();
                return trimmed.length() == 10 ? java.time.LocalDate.parse(trimmed).atStartOfDay() : java.time.LocalDateTime.parse(trimmed);
            } catch (java.time.format.DateTimeParseException e) {
                throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, block + "." + item + ": hibás dátum (ISO: éééé-hh-nnTóó:pp:mm).");
            }
        }
        static String parameter(java.util.Map<String, String> parameters, String name) {
            String value = parameters.get(name);
            return value == null || value.isEmpty() ? null : value;
        }
        static void put(java.util.Map<String, java.util.Map<String, String>> blocks, String block, String item, Object value) {
            blocks.computeIfAbsent(block, key -> new java.util.LinkedHashMap<>())
                  .put(item, value == null ? null : value instanceof java.math.BigDecimal d ? d.toPlainString() : value.toString());
        }
        static java.util.List<String> lines(Object messages) {
            if (!(messages instanceof String text)) return java.util.List.of();
            return java.util.Arrays.stream(text.split("\n")).filter(line -> !line.isBlank()).toList();
        }
    }

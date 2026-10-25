    static final class SqlValues {
        private SqlValues() {}
        public static String normalize(String value) { return value == null || value.isEmpty() ? null : value; }
        public static boolean isNull(Object value) { return value == null || "".equals(value); }
        public static boolean truth(Boolean value) { return Boolean.TRUE.equals(value); }
        public static Boolean not(Boolean value) { return value == null ? null : !value; }
        public static Boolean and(Supplier<Boolean> left, Supplier<Boolean> right) {
            Boolean a = left.get();
            if (Boolean.FALSE.equals(a)) return false;
            Boolean b = right.get();
            if (Boolean.FALSE.equals(b)) return false;
            return a == null || b == null ? null : true;
        }
        public static Boolean or(Supplier<Boolean> left, Supplier<Boolean> right) {
            Boolean a = left.get();
            if (Boolean.TRUE.equals(a)) return true;
            Boolean b = right.get();
            if (Boolean.TRUE.equals(b)) return true;
            return a == null || b == null ? null : false;
        }
        @SuppressWarnings({"rawtypes", "unchecked"})
        public static Boolean compare(Object left, Object right, String operator) {
            if (isNull(left) || isNull(right)) return null;
            int c;
            if (left instanceof BigDecimal a && right instanceof BigDecimal b) c = a.compareTo(b);
            else if (left.getClass() == right.getClass() && left instanceof Comparable) c = ((Comparable) left).compareTo(right);
            else throw new IllegalArgumentException("Unsupported implicit Oracle conversion");
            return switch (operator) {
                case "=" -> c == 0;
                case "<>", "!=" -> c != 0;
                case "<" -> c < 0;
                case ">" -> c > 0;
                case "<=" -> c <= 0;
                case ">=" -> c >= 0;
                default -> throw new IllegalArgumentException(operator);
            };
        }
        public static boolean same(Object a, Object b) {
            return isNull(a) && isNull(b) || Boolean.TRUE.equals(compare(a, b, "="));
        }
        public static BigDecimal math(BigDecimal a, BigDecimal b, String operator) {
            if (a == null || b == null) return null;
            return switch (operator) {
                case "+" -> a.add(b);
                case "-" -> a.subtract(b);
                case "*" -> a.multiply(b);
                case "/" -> a.divide(b, new MathContext(38));
                default -> throw new IllegalArgumentException(operator);
            };
        }
        public static BigDecimal neg(BigDecimal value) { return value == null ? null : value.negate(); }
        public static BigDecimal abs(BigDecimal value) { return value == null ? null : value.abs(); }
        public static <T> T nvl(T value, T fallback) { return isNull(value) ? fallback : value; }
        public static String concat(String a, String b) { return normalize((a == null ? "" : a) + (b == null ? "" : b)); }
        public static String upper(String value) { return value == null ? null : normalize(value.toUpperCase(Locale.ROOT)); }
        public static String lower(String value) { return value == null ? null : normalize(value.toLowerCase(Locale.ROOT)); }
        public static String trim(String value) { return value == null ? null : normalize(value.replaceAll("^ +| +$", "")); }
        public static BigDecimal length(String value) { return isNull(value) ? null : BigDecimal.valueOf(value.codePointCount(0, value.length())); }
    }

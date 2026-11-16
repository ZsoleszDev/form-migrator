"""Java 11 and PMD in the AI-written code: the repairs with one meaning, Java 12+ back to the model, PMD warnings."""
import unittest

from frm_forms import java_lint
from frm_forms import assistant_reply as reply


def repaired(code: str) -> str:
    return java_lint.repair(code, [])


class RepairTests(unittest.TestCase):
    def test_stream_to_list_and_get_first_are_java_11(self):
        self.assertEqual(repaired("var a = rows.stream().map(r -> r.id).toList();"),
                         "var a = rows.stream().map(r -> r.id).collect(Collectors.toList());")
        self.assertEqual(repaired("Object[] r = result.getFirst();"), "Object[] r = result.get(0);")
        right = "var a = rows.stream().map(r -> r.id).collect(Collectors.toList());"
        self.assertEqual(repaired(right), right)  # already Java 8: never touched, and the repair is idempotent
        self.assertEqual(repaired(repaired("var a = s.toList();")), "var a = s.collect(Collectors.toList());")
        deque = "Deque<String> d = new ArrayDeque<>(); String s = d.getFirst();"
        self.assertEqual(repaired(deque), deque)  # a Deque has getFirst() in Java 11

    def test_formatted_on_a_literal_is_string_format(self):
        self.assertEqual(repaired('String t = "%d sor, %s".formatted(n, name);'), 'String t = String.format("%d sor, %s", n, name);')
        self.assertEqual(java_lint.problems(repaired('String t = "%d sor".formatted(n);')), [])
        self.assertTrue(java_lint.problems(repaired("String t = pattern.formatted(n);")))  # not a literal: the model fixes it

    def test_a_literal_comes_first_and_a_string_is_never_compared_with_identity(self):
        self.assertEqual(repaired('if (status.equals("LEZART")) {'), 'if ("LEZART".equals(status)) {')
        self.assertEqual(repaired('if (values.get("B").get("X").equalsIgnoreCase("i")) {'),
                         'if ("i".equalsIgnoreCase(values.get("B").get("X"))) {')
        self.assertEqual(repaired('boolean b = !s.trim().equals("");'), 'boolean b = !"".equals(s.trim());')
        self.assertEqual(repaired('if (kod == "A" || kod != "B") {'), 'if ("A".equals(kod) || !"B".equals(kod)) {')
        self.assertEqual(repaired('return row.getStatus() == "X";'), 'return "X".equals(row.getStatus());')
        for unchanged in ('if ("A".equals(x)) {', 'if (x.equals(y)) {', 'boolean b = new Foo().equals("x");',
                          'String s = a + b == "x" ? 1 : 2;', '// x.equals("A") in a comment', 's = "x.equals(\\"A\\")";',
                          'if (x.equals("A" + y)) {'):
            self.assertEqual(repaired(unchanged), unchanged, unchanged)

    def test_an_empty_collection_is_is_empty(self):
        self.assertEqual(repaired("if (rows.size() == 0) {"), "if (rows.isEmpty()) {")
        self.assertEqual(repaired("if (names.size() > 0 && ok) {"), "if (!names.isEmpty() && ok) {")
        self.assertEqual(repaired("return result.getResultList().size() != 0;"), "return !result.getResultList().isEmpty();")
        self.assertEqual(repaired("boolean none = list.size() < 1;"), "boolean none = list.isEmpty();")
        self.assertEqual(repaired("int n = rows.size() == 0 ? 1 : 2;"), "int n = rows.isEmpty() ? 1 : 2;")
        for unchanged in ("if (x + rows.size() == 0) {", "if (rows.size() == 0.5) {", "if (rows.size() == limit) {",
                          "if (rows.size() > 1) {", "boolean b = rows.size() == 0 + n;"):
            self.assertEqual(repaired(unchanged), unchanged, unchanged)

    def test_case_conversions_name_a_locale_and_big_decimals_their_constants(self):
        self.assertEqual(repaired('String u = s.toUpperCase();'), 'String u = s.toUpperCase(Locale.ROOT);')
        self.assertEqual(repaired('String u = s.toLowerCase(Locale.ROOT);'), 'String u = s.toLowerCase(Locale.ROOT);')
        self.assertEqual(repaired('BigDecimal a = new BigDecimal(0), b = new BigDecimal("1"), c = new BigDecimal(10);'),
                         'BigDecimal a = BigDecimal.ZERO, b = BigDecimal.ONE, c = BigDecimal.TEN;')
        self.assertEqual(repaired("BigDecimal r = new BigDecimal(0.1);"), 'BigDecimal r = new BigDecimal("0.1");')
        self.assertEqual(repaired('BigDecimal r = new BigDecimal("1.0");'), 'BigDecimal r = new BigDecimal("1.0");')
        self.assertEqual(repaired("BigDecimal r = new BigDecimal(5);"), "BigDecimal r = new BigDecimal(5);")

    def test_the_repairs_are_named_in_one_note(self):
        notes = []
        java_lint.repair('if (a.size() == 0 && b.equals("x")) {}', notes)
        self.assertEqual(len(notes), 1)
        self.assertIn("isEmpty()", notes[0])
        self.assertIn('"A".equals(x)', notes[0])
        java_lint.repair("int x = 1;", notes)
        self.assertEqual(len(notes), 1)


class Java11Tests(unittest.TestCase):
    def assertJava11(self, code: str, expected: bool):
        found = java_lint.problems(code)
        self.assertEqual(bool(found), expected, (code, found))

    def test_newer_syntax_and_api_is_found(self):
        for code in ('String sql = """\n    SELECT 1 FROM dual\n    """;',
                     "record Pair(String a, String b) {}",
                     'String s = switch (k) { case "A" -> "x"; default -> "y"; };',
                     "if (o instanceof String s && !s.isEmpty()) {",
                     'String t = "%d sor".formatted(n);',
                     "var last = rows.getLast();",
                     "var both = s.collect(Collectors.teeing(a, b, f));"):
            self.assertJava11(code, True)

    def test_java_11_code_is_not_reported(self):
        for code in ("rows.forEach(r -> r.setId(null));",
                     'switch (k) {\n    case "A":\n        x = 1;\n        break;\n    default:\n        x = 2;\n}',
                     "if (o instanceof String && ok) {",
                     "if (o instanceof String) {",
                     '// record Pair(String a) {} and """ in a comment',
                     'String s = "case A -> B";',
                     "Comparator<String> c = Comparator.<String>naturalOrder().reversed();",
                     "Deque<Object[]> d = new ArrayDeque<>(); var x = d.getLast();",
                     'String s = String.format("%d", n).strip().repeat(2); boolean b = s.isBlank();'):
            self.assertJava11(code, False)

    def test_the_model_is_told_what_to_write_instead(self):
        en = " ".join(e for _, e in java_lint.problems('var t = "%s".formatted(x); var r = o instanceof String s;'))
        self.assertIn('String.format("...", args)', en)
        self.assertIn("(String) x", en)


class WarningTests(unittest.TestCase):
    def test_what_pmd_reports_and_has_no_safe_rewrite(self):
        code = ("try {\n    run();\n} catch (PersistenceException e) {\n    // ignore\n}\n"
                "try { run(); } catch (Exception e) { e.printStackTrace(); }\nSystem.out.println(x);")
        found = " ".join(java_lint.warnings(code))
        for rule in ("EmptyCatchBlock", "AvoidPrintStackTrace", "SystemPrintln", "AvoidCatchingGenericException"):
            self.assertIn(rule, found)
        self.assertEqual(java_lint.warnings("try {\n    run();\n} catch (PersistenceException e) {\n"
                                            "    throw FormsErrors.translate(e);\n}"), [])


class AssistantReplyTests(unittest.TestCase):
    """The checks inside the answer's normal path (assistant_reply.extract)."""

    def context(self):
        return reply.Context(method="onX", service="", mode="service")

    def test_java_17_goes_back_to_the_model_with_the_java_11_way(self):
        answer = '```java\n// SERVICE\nvar t = pattern.formatted(x);\nreturn null;\n```'
        result = reply.extract(answer, self.context())
        self.assertTrue(any("String.formatted()" in p.hu for p in result.problems), result.problems)

    def test_repairs_and_warnings_reach_the_proposal(self):
        answer = ('```java\n// SERVICE\nif (rows.size() == 0) {\n    System.out.println("none");\n}\n'
                  'return new ActionResult(Map.of(), List.of(), List.of(), Map.of());\n```')
        result = reply.extract(answer, self.context())
        self.assertEqual(result.problems, [])
        self.assertIn("if (rows.isEmpty()) {", result.response["body"])
        self.assertTrue(any("SystemPrintln" in w for w in result.warnings), result.warnings)
        self.assertTrue(any("Java 11 / PMD" in n for n in result.notes), result.notes)


if __name__ == "__main__":
    unittest.main()

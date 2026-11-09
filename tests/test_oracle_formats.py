"""Oracle format models and Oracle semantics in the Java assistant's request: exact or none, never a guess."""
import unittest

from frm_forms import assistant_context as context
from frm_forms.ai_triggers import code_text
from frm_forms.oracle_formats import format_calls, has_time, is_number_format, java_pattern


class FormatModelTests(unittest.TestCase):
    def test_datetime_elements_get_their_java_letters(self):
        self.assertEqual(java_pattern('YYYY.MM.DD HH24:MI:SS'), 'yyyy.MM.dd HH:mm:ss')  # MI is minutes, DD the day
        self.assertEqual(java_pattern('DD/MM/YYYY'), 'dd/MM/yyyy')
        self.assertEqual(java_pattern('YYYY-MM-DD"T"HH24:MI'), "yyyy-MM-dd'T'HH:mm")  # quoted text stays text
        self.assertEqual(java_pattern('DDD'), 'DDD')  # day of the year in both
        self.assertEqual(java_pattern('FMDD.MM.YYYY'), 'd.M.yyyy')  # fill mode: no padding

    def test_to_date_accepts_one_or_two_digits_like_oracle(self):
        self.assertEqual(java_pattern('DD.MM.YYYY HH24:MI', parse=True), 'd.M.yyyy H:m')
        self.assertEqual(java_pattern('YYYYMMDD', parse=True), 'yyyyMMdd')  # adjacent numbers keep their width
        self.assertEqual(java_pattern('FXDD.MM.YYYY', parse=True), 'dd.MM.yyyy')  # FX: exact

    def test_nls_dependent_or_unknown_elements_get_no_pattern(self):
        for model in ('DD-MON-YYYY', 'DAY', 'DY', 'YYYY. MONTH DD.', 'HH:MI AM', 'RR.MM.DD', 'WW', 'J', 'SSSSS', 'Q'):
            self.assertIsNone(java_pattern(model), model)
        self.assertIsNone(java_pattern('HH12:MI', parse=True))  # no AM/PM: the hour of the day is unknown

    def test_calls_with_a_literal_format_and_their_kind(self):
        code = "x := to_char(:B.D, 'YYYY.MM.DD') || TO_CHAR(nvl(:B.N, 0), 'FM9990D00') || to_date(a || 'b''c', 'DD.MM.YYYY HH24:MI'); y := to_char(v);"
        self.assertEqual(format_calls(code), [('TO_CHAR', 'YYYY.MM.DD'), ('TO_CHAR', 'FM9990D00'), ('TO_DATE', 'DD.MM.YYYY HH24:MI')])
        self.assertTrue(is_number_format('FM999G990D00'))
        self.assertFalse(is_number_format('YYYY.MM.DD'))
        self.assertTrue(has_time('DD.MM.YYYY HH24:MI'))
        self.assertFalse(has_time('YYYY "MI" DD'))  # quoted text is no element


class OracleSemanticsHintTests(unittest.TestCase):
    CODE = """BEGIN
  -- MESSAGE('régi kód');
  :B.SZOVEG := 'Dátum: ' || TO_CHAR(:B.DATUM, 'YYYY.MM.DD HH24:MI');
  :B.D := TO_DATE(:B.S, 'DD.MM.YYYY');
  :B.E := TO_CHAR(:B.DATUM, 'DD-MON-YYYY');
END;"""

    def test_exact_conversions_replace_the_same_pattern_advice(self):
        lines = context.hints(code_text(self.CODE), 'hook')
        text = '\n'.join(lines)
        self.assertNotIn('<the same pattern>', text)
        self.assertIn('d.format(DateTimeFormatter.ofPattern("yyyy.MM.dd HH:mm"))', text)
        self.assertIn('LocalDate.parse(s.trim(), DateTimeFormatter.ofPattern("d.M.yyyy")).atStartOfDay()', text)
        self.assertIn("TO_CHAR(x, 'DD-MON-YYYY'): no exact java.time pattern", text)
        self.assertIn('SqlValues.concat(a, b), never a + b', text)  # 'a' || NULL = 'a' in Oracle, "anull" in Java
        self.assertFalse(any(line.startswith('MESSAGE(') for line in lines))  # only commented out in the trigger

    def test_hints_never_come_from_the_migrators_own_notes(self):
        # ai_triggers.region: the notes say "context.message(...)"; the trigger itself has no MESSAGE.
        method = '''private void triggerPreInsertB(BRow row, RuleContext context) {
        //region Eredeti Forms-kód: B / PRE-INSERT
        // AI-ra vár. Bemenet/kimenet: a rekord (row.<mező>), üzenet: context.message(...), hiba: context.abort() vagy kivétel.
        // --- B / PRE-INSERT
        // kod_pkg.check_insert(:B.KOD, :B.DATUM, :B.NEV);
        //endregion
        throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, "B / PRE-INSERT");
    }'''
        self.assertEqual(context.original_code(method).strip(), 'kod_pkg.check_insert(:B.KOD, :B.DATUM, :B.NEV);')
        self.assertEqual(context.hints(context.forms_code({}, method), 'hook'), [])
        packet = {'sources': [{'source': "BEGIN MESSAGE('x'); END;"}]}
        self.assertEqual(context.hints(context.forms_code(packet, method), 'hook'), ['MESSAGE(x): context.message(x).'])


if __name__ == '__main__':
    unittest.main()

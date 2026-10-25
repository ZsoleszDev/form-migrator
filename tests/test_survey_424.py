"""4.24: the fixes of the second survey (anonymised patterns, invented names) and the query-button improvements.

The survey replica gets the blocking patterns of the real forms:
  - PRE-FORM: a framework package's state (qms$nav.nav_opening_wnd := FALSE) next to the record group building;
  - a button calling a local package member while another member uses GET_BLOCK_PROPERTY(.., CURRENT_RECORD);
  - the T9 button: an inline DECLARE alert block, RAISE FORM_TRIGGER_FAILURE, then a local procedure whose error
    branch is QMS$FORMS_ERRORS.PUSH(QMS$FORMS_ERRORS.MSGGETTEXT(..)) + QMS$FORMS_ERRORS.RAISE_FAILURE;
  - WHEN-VALIDATE-RECORD calling a package's alert procedure with the package's message constants.
"""
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from xml.sax.saxutils import quoteattr

from java_support import COMPANY_IMPORTS, write_stubs
from frm_forms import framework
from frm_forms.cli import main
from frm_forms.messages import simplify, wrapper
from frm_forms.plsql import Unsupported, parse
from frm_forms.plsql_passthrough import scan
import test_query_actions as qa
import test_query_java as tq

REPLICA = Path(__file__).parent / 'fixtures/felmeres_replika_fmb.xml'
AKTIV = 'Name="AKTIV" ItemType="Text Item" DataType="Char" MaximumLength="1" ColumnName="AKTIV" InitialValue="I" Visible="false"/>'


def attr(code: str) -> str:
    return quoteattr(code.replace('\n', '&#10;'))[1:-1]


T9 = """BEGIN
   QMS$EVENT_ITEM('WHEN-BUTTON-PRESSED');
END;
BEGIN
    IF :CTRL.SZURO IS NULL THEN
        DECLARE
            l_alert_nev CONSTANT VARCHAR2(15) := 'A_HIBA';
            l_alert     ALERT;
            l_gomb NUMBER;
        BEGIN
            l_alert := FIND_ALERT(l_alert_nev);
            SET_ALERT_PROPERTY(l_alert, ALERT_MESSAGE_TEXT, 'Adja meg a szűrőt!');
            l_gomb := SHOW_ALERT(l_alert);
        END;
        RAISE FORM_TRIGGER_FAILURE;
    END IF;
    ell_kod(:CTRL.SZURO);
END;"""

ELL_KOD = """PROCEDURE ell_kod(p_kod VARCHAR2) IS
BEGIN
  IF p_kod = 'X' THEN
    qms$forms_errors.push(qms$forms_errors.msggettext(12, 'Hibás kód'), 'E', 'ELL', 12);
    qms$forms_errors.raise_failure;
  END IF;
  UPDATE rendeles SET statusz = p_kod WHERE id = :RENDELES.ID;
END;"""

ELLENOR_SPEC = """PACKAGE ellenor IS
  c_vevo CONSTANT VARCHAR2(100) := 'A vevő megadása kötelező.';
  c_lezart CONSTANT VARCHAR2(100) := 'Lezárt rendeléshez nem adható vevő.';
  PROCEDURE uzen(p_szoveg IN VARCHAR2);
  PROCEDURE oszlop;
END ellenor;"""

ELLENOR_BODY = """PACKAGE BODY ellenor IS
  PROCEDURE uzen(p_szoveg IN VARCHAR2) IS
    l_alert ALERT;
    l_gomb NUMBER;
  BEGIN
    l_alert := FIND_ALERT('A_INFO');
    SET_ALERT_PROPERTY(l_alert, ALERT_MESSAGE_TEXT, p_szoveg);
    l_gomb := SHOW_ALERT(l_alert);
  END uzen;
  PROCEDURE oszlop IS
  BEGIN
    GO_BLOCK('TETEL');
  END oszlop;
END ellenor;"""

VALIDATE_RECORD = """BEGIN
    IF :RENDELES.STATUSZ != 'L' AND
       :RENDELES.VEVO IS NULL THEN
        ellenor.uzen(ellenor.c_vevo);
        RAISE FORM_TRIGGER_FAILURE;
    END IF;
    IF :RENDELES.STATUSZ = 'L' AND
       :RENDELES.VEVO IS NOT NULL THEN
        ellenor.uzen(ellenor.c_lezart);
        RAISE FORM_TRIGGER_FAILURE;
    END IF;
END;"""


def survey_form() -> str:
    s = REPLICA.read_text(encoding='utf-8')
    buttons = ('<Item Name="PB_T9" ItemType="Push Button" Label="Ellenőriz" CanvasName="C" XPosition="550" YPosition="40" '
               'Width="80" Height="22"><Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="' + attr(T9) + '"/></Item>\n'
               '   <Item Name="PB_INFO" ItemType="Push Button" Label="Info" CanvasName="C" XPosition="640" YPosition="40" '
               'Width="80" Height="22"><Trigger Name="WHEN-BUTTON-PRESSED" TriggerText="'
               + attr("BEGIN\n  rendeles_pkg.ujraszamol;\nEND;") + '"/></Item>\n'
               '   <Item Name="PB_ALAP" ItemType="Push Button"')
    s = s.replace('<Item Name="PB_ALAP" ItemType="Push Button"', buttons, 1)
    units = ('<ProgramUnit Name="ELL_KOD" ProgramUnitType="Procedure" ProgramUnitText="' + attr(ELL_KOD) + '"/>\n'
             '  <ProgramUnit Name="ELLENOR" ProgramUnitType="Package Spec" ProgramUnitText="' + attr(ELLENOR_SPEC) + '"/>\n'
             '  <ProgramUnit Name="ELLENOR" ProgramUnitType="Package Body" ProgramUnitText="' + attr(ELLENOR_BODY) + '"/>\n'
             '  <ProgramUnit Name="BLOKK_FRISSIT"')
    s = s.replace('<ProgramUnit Name="BLOKK_FRISSIT"', units, 1)
    s = s.replace('<Trigger Name="KEY-EXEQRY" TriggerText="BEGIN&amp;#10;    ank_blokk.lekerdez;',
                  '<Trigger Name="WHEN-VALIDATE-RECORD" TriggerText="' + attr(VALIDATE_RECORD) + '"/>\n   '
                  '<Trigger Name="KEY-EXEQRY" TriggerText="BEGIN&amp;#10;    ank_blokk.lekerdez;', 1)
    s = s.replace('  rendeles_globals.activate := FALSE;&amp;#10;',
                  '  rendeles_globals.activate := FALSE;&amp;#10;  qms$nav.nav_opening_wnd := FALSE;&amp;#10;', 1)
    s = s.replace("PROCEDURE ujraszamol;&amp;#10;END rendeles_pkg;",
                  "PROCEDURE ujraszamol;&amp;#10;  PROCEDURE blokk_info;&amp;#10;END rendeles_pkg;")
    s = s.replace("  END ujraszamol;&amp;#10;END rendeles_pkg;",
                  "  END ujraszamol;&amp;#10;  PROCEDURE blokk_info IS&amp;#10;    v VARCHAR2(30);&amp;#10;  BEGIN&amp;#10;"
                  "    v := GET_BLOCK_PROPERTY(&apos;RENDELES&apos;, CURRENT_RECORD);&amp;#10;    g_utolso := 1;&amp;#10;"
                  "  END blokk_info;&amp;#10;END rendeles_pkg;")
    assert s.count('blokk_info') == 3 and 'qms$nav' in s and 'PB_T9' in s and 'ELLENOR' in s
    return s


def migrate(root: Path, xml: str, config=None, module='rendeles') -> Path:
    (root / 'in.xml').write_text(xml, encoding='utf-8')
    (root / 'config.json').write_text(json.dumps({'backend_live': True, 'java_company_imports': COMPANY_IMPORTS,
                                                  'java_import_map': '-', **(config or {})}))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert main(['migrate', str(root / 'in.xml'), '--module', module, '--screen', '--config', str(root / 'config.json'),
                     '--out', str(root / 'out')]) == 0
    return root / 'out'


def endpoints(out: Path) -> dict:
    plan = json.loads((out / 'analysis/backend-plan.json').read_text(encoding='utf-8'))
    return {e.get('owner') or (e['block'] + ':' + e['operation']): e for e in plan['endpoints']}


def javac(root: Path, out: Path, extra=()) -> subprocess.CompletedProcess | None:
    if not shutil.which('java'):
        return None
    sources = list(out.glob('backend/**/*.java')) + write_stubs(root / 'stubs') + list(extra)
    (root / 'sources.args').write_text('\n'.join('"' + str(p).replace(chr(92), '/') + '"' for p in sources))
    run = subprocess.run(['java', 'com.sun.tools.javac.Main', '--release', '11', '-encoding', 'UTF-8', '-d', str(root / 'classes'),
                          '@' + str(root / 'sources.args')], capture_output=True, text=True, timeout=300)
    return None if 'Could not find or load main class' in run.stderr else run


class SurveyReplicaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.out = migrate(cls.root, survey_form())
        cls.service = (cls.out / 'backend/DPS/RendelesServiceImpl.java').read_text(encoding='utf-8')
        cls.endpoints = endpoints(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def method(self, header):
        start = self.service.index(header)
        return self.service[start:self.service.index('\n    }\n', start)]

    def test_every_endpoint_of_the_replica_runs(self):
        blocked = {k: e.get('blockers') for k, e in self.endpoints.items() if not e['implemented']}
        self.assertEqual(blocked, {})

    def test_start_up_drops_the_framework_package_state(self):
        init = self.endpoints['@INIT']
        self.assertEqual((init['runs'], init['implemented']), ('plsql', True))
        self.assertNotIn('nav_opening_wnd', self.method('public ActionResult onFormInit('))

    def test_the_t9_button_shows_messages_instead_of_dialogs(self):
        self.assertEqual(self.endpoints['CTRL.PB_T9']['runs'], 'plsql')
        method = self.method('public ActionResult onCtrlPbT9(')
        self.assertIn("frm_msg('Adja meg a szűrőt!');", method)
        self.assertNotIn('FIND_ALERT', method)
        unit = self.service[self.service.index('static final String ELL_KOD_UI = '):]
        unit = unit[:unit.index(';\n')]
        self.assertIn("frm_msg('Hibás kód');", unit)
        self.assertIn('RAISE FORM_TRIGGER_FAILURE;', unit)
        self.assertNotIn('qms$forms_errors', unit.lower())

    def test_a_package_member_the_button_does_not_reach_stays_out(self):
        self.assertEqual(self.endpoints['CTRL.PB_INFO']['runs'], 'plsql')
        method = self.method('public ActionResult onCtrlPbInfo(')
        self.assertIn('PROCEDURE ujraszamol IS', method)
        self.assertNotIn('blokk_info', method.lower())
        self.assertIn('// Helyi csomag, csak a hívott tagjai (a blokk szövegében): RENDELES_PKG', self.service)

    def test_the_record_validation_calls_the_alert_procedure_as_a_message(self):
        for operation in ('RENDELES:create', 'RENDELES:update'):
            self.assertTrue(self.endpoints[operation]['implemented'], operation)
        self.assertIn('  PROCEDURE uzen(p_szoveg IN VARCHAR2) IS\\n"\n                + "  BEGIN\\n"\n'
                      '                + "    frm_msg(p_szoveg);', self.service)
        self.assertIn("c_vevo CONSTANT VARCHAR2(100) := 'A vevő megadása kötelező.';", self.service)
        self.assertNotIn('PROCEDURE oszlop', self.service)  # GO_BLOCK: not reached, not embedded

    def test_the_generated_java_compiles(self):
        run = javac(self.root, self.out)
        if run is None:
            self.skipTest('JDK (javac) required')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


class MessageTests(unittest.TestCase):
    def test_catalogued_routines_and_alert_blocks_become_messages(self):
        text, notes = simplify("BEGIN\n  QMS$FORMS_ERRORS.PUSH(QMS$FORMS_ERRORS.MSGGETTEXT(37, 'Nem kérdezhető'), 'E', 'X', 37);\n"
                               "  QMS$FORMS_ERRORS.RAISE_FAILURE;\n  wuzenet('Kész: ' || :B.I);\n"
                               "  DECLARE a ALERT; n NUMBER; BEGIN a := FIND_ALERT('X'); "
                               "SET_ALERT_PROPERTY(a, ALERT_MESSAGE_TEXT, 'Hiba'); n := SHOW_ALERT(a); END;\nEND;")
        self.assertIn("MESSAGE('Nem kérdezhető');", text)
        self.assertIn('RAISE FORM_TRIGGER_FAILURE;', text)
        self.assertIn("MESSAGE('Kész: ' || :B.I);", text)
        self.assertIn("MESSAGE('Hiba');", text)
        self.assertNotIn('ALERT', text)
        self.assertEqual(len(notes), 4)

    def test_a_dialog_whose_answer_counts_stays(self):
        source = ("DECLARE a ALERT; n NUMBER; BEGIN a := FIND_ALERT('X'); SET_ALERT_PROPERTY(a, ALERT_MESSAGE_TEXT, 'Biztos?'); "
                  "n := SHOW_ALERT(a); IF n = ALERT_BUTTON1 THEN DELETE FROM t; END IF; END;")
        self.assertEqual(simplify(source)[0], source)

    def test_a_local_alert_procedure_becomes_a_message_procedure(self):
        self.assertEqual(wrapper(ELLENOR_BODY.split('\n', 1)[1].split('  PROCEDURE oszlop')[0].strip()),
                         'PROCEDURE uzen(p_szoveg IN VARCHAR2) IS\nBEGIN\n  MESSAGE(p_szoveg);\nEND uzen;')
        self.assertIsNone(wrapper('PROCEDURE naplo(p VARCHAR2) IS BEGIN INSERT INTO naplo VALUES (p); END;'))

    def test_a_company_routine_from_the_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'catalog.json'
            data = json.loads(framework.DATA.read_text(encoding='utf-8'))
            data['message_calls'] = {'CEG_UZENET.HIBA': 2}
            path.write_text(json.dumps(data), encoding='utf-8')
            catalog = framework.load({'framework_catalog': str(path)})
            self.assertEqual(simplify("ceg_uzenet.hiba(10, 'Rossz adat');", catalog['messages'])[0], "MESSAGE('Rossz adat');")
            self.assertEqual(simplify("wuzenet('x');", catalog['messages'])[0], "wuzenet('x');")  # not in this catalog
            data['message_calls'] = {'X': 0}
            path.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaises(Exception):
                framework.load({'framework_catalog': str(path)})


class ParserTests(unittest.TestCase):
    def test_a_refusal_names_the_source_line(self):
        with self.assertRaises(Unsupported) as error:
            parse('BEGIN\n  NULL;\n  v_x := 1;\nEND;')
        self.assertEqual(str(error.exception), 'Nem értelmezhető PL/SQL: itt „;” kellene, de „:=” áll (3. sor: v_x := 1;).')
        with self.assertRaises(Unsupported) as error:
            scan('BEGIN\n  x := 1; /* nyitott\nEND;')
        self.assertIn('Lezáratlan /* megjegyzés (2. sor:', str(error.exception))

    def test_a_line_comment_ends_at_a_carriage_return_too(self):
        tokens = [t[1] for t in scan('x := 1; -- megjegyzés\ry := 2;') if t[0] not in {'ws', 'comment'}]
        self.assertEqual(tokens, ['x', ':=', '1', ';', 'y', ':=', '2', ';'])

    def test_the_extended_grammar(self):
        nodes = parse("DECLARE\n  v t.c%TYPE := :B.C;\n  e EXCEPTION;\n  PRAGMA EXCEPTION_INIT(e, -20001);\nBEGIN\n"
                      "  IF v IN ('A', 'B') AND :B.N NOT BETWEEN 1 AND 5 AND :B.S LIKE 'X%' THEN\n"
                      "    v := CASE WHEN :B.N = 1 THEN 'egy' ELSE 'más' END;\n  END IF;\n"
                      "  SELECT a INTO :B.A FROM t WHERE k = v;\nEND;", extended=True)
        body = nodes[0]['body']
        self.assertEqual([n['op'] for n in body], ['declare', 'declare', 'pragma', 'if', 'select'])
        self.assertEqual(body[0]['type'], 'T.C%TYPE')
        condition = body[3]['branches'][0]['condition']
        self.assertEqual(condition['left']['left']['op'], 'in')
        self.assertEqual(body[4]['into'], ['B.A'])
        with self.assertRaises(Unsupported):
            parse('BEGIN v := 1; END;')  # the strict grammar of every other consumer is unchanged


class StaticBlockPropertyTests(unittest.TestCase):
    def test_get_block_property_and_name_in_in_a_data_trigger(self):
        source = REPLICA.read_text(encoding='utf-8').replace(
            '<Trigger Name="KEY-EXEQRY" TriggerText="BEGIN&amp;#10;    ank_blokk.lekerdez;',
            '<Trigger Name="PRE-UPDATE" TriggerText="' + attr(
                "BEGIN\n  IF GET_BLOCK_PROPERTY('RENDELES', UPDATE_ALLOWED) = 'TRUE' AND "
                "GET_BLOCK_PROPERTY(:SYSTEM.TRIGGER_BLOCK, QUERY_ALLOWED) = 'TRUE' THEN\n"
                "    :RENDELES.OSSZEG := NVL(NAME_IN('RENDELES.OSSZEG'), 0);\n  END IF;\nEND;") + '"/>\n   '
            '<Trigger Name="KEY-EXEQRY" TriggerText="BEGIN&amp;#10;    ank_blokk.lekerdez;', 1)
        with tempfile.TemporaryDirectory() as temp:
            out = migrate(Path(temp), source)
            self.assertTrue(endpoints(out)['RENDELES:update']['implemented'])
            service = (out / 'backend/DPS/RendelesServiceImpl.java').read_text(encoding='utf-8')
            self.assertIn("IF 'TRUE' = 'TRUE' AND (CASE UPPER('RENDELES') WHEN 'CTRL' THEN 'TRUE'", service)
            self.assertNotIn('NAME_IN', service.split('PRE-UPDATE')[-1].split('}')[0])

    def test_a_long_item_is_text(self):
        source = REPLICA.read_text(encoding='utf-8').replace(
            'Name="NEV" ItemType="Text Item" DataType="Char"', 'Name="NEV" ItemType="Text Item" DataType="Long"', 1)
        with tempfile.TemporaryDirectory() as temp:
            out = migrate(Path(temp), source)
            model = json.loads((out / 'analysis/form.ir.json').read_text(encoding='utf-8'))
            self.assertFalse([i for i in model['issues'] if i['code'] == 'UNSUPPORTED_ITEM' and 'PARTNER.NEV' in i['detail']])
            self.assertTrue(endpoints(out)['PARTNER:search']['implemented'])


class FormLevelTriggerTests(unittest.TestCase):
    def test_a_form_level_post_query_runs_in_the_blocks_without_their_own(self):
        source = REPLICA.read_text(encoding='utf-8').replace(
            ' <Trigger Name="PRE-FORM"',
            ' <Trigger Name="POST-QUERY" TriggerText="' + attr(
                "DECLARE\n  vn_hossz NUMBER;\nBEGIN\n  vn_hossz := LENGTH(:SYSTEM.TRIGGER_BLOCK);\nEND;") + '"/>\n'
            ' <Trigger Name="PRE-FORM"', 1).replace(AKTIV, AKTIV + '\n   <Trigger Name="POST-QUERY" TriggerText="NULL;"/>', 1)
        with tempfile.TemporaryDirectory() as temp:
            out = migrate(Path(temp), source)
            model = json.loads((out / 'analysis/form.ir.json').read_text(encoding='utf-8'))
            original = next(t for t in model['triggers'] if t['id'] == 'RENDELES:POST-QUERY')
            self.assertEqual((original['status'], original['target']), ('converted', 'per-block'))
            copies = {t['block']: t for t in model['triggers'] if t.get('form_level') == 'RENDELES:POST-QUERY'}
            self.assertEqual(set(copies), set(original['form_level_blocks']))
            self.assertNotIn('TETEL', copies)  # TETEL has its own POST-QUERY (Override)
            for copy in copies.values():
                self.assertEqual((copy['status'], copy['target']), ('converted', 'backend'))
            service = (out / 'backend/DPS/RendelesServiceImpl.java').read_text(encoding='utf-8')
            self.assertIn("LENGTH('RENDELES')", service)  # :SYSTEM.TRIGGER_BLOCK of the block's copy

    def test_execution_hierarchy_after_runs_the_form_level_code_first(self):
        source = REPLICA.read_text(encoding='utf-8').replace(
            ' <Trigger Name="PRE-FORM"',
            ' <Trigger Name="PRE-UPDATE" TriggerText="' + attr("BEGIN\n  NULL;\n  NULL;\nEND;") + '"/>\n'
            ' <Trigger Name="PRE-FORM"', 1).replace(
            AKTIV, AKTIV + '\n   <Trigger Name="PRE-UPDATE" ExecutionHierarchy="After" TriggerText="'
            + attr("BEGIN\n  :TETEL.MENNYISEG := 2;\nEND;") + '"/>', 1)
        with tempfile.TemporaryDirectory() as temp:
            out = migrate(Path(temp), source)
            model = json.loads((out / 'analysis/form.ir.json').read_text(encoding='utf-8'))
            ids = [t['id'] for t in model['triggers'] if t['block'] == 'TETEL' and t['event'] == 'PRE-UPDATE']
            self.assertEqual(ids, ['RENDELES:PRE-UPDATE@TETEL', 'TETEL:PRE-UPDATE'])


class JavaCompilerLocalTests(unittest.TestCase):
    def test_declared_variables_are_fields_of_a_holder(self):
        source = REPLICA.read_text(encoding='utf-8').replace(
            '<Trigger Name="KEY-EXEQRY" TriggerText="BEGIN&amp;#10;    ank_blokk.lekerdez;',
            '<Trigger Name="PRE-UPDATE" TriggerText="' + attr(
                "DECLARE\n  v_osszeg rendeles.osszeg%TYPE := :RENDELES.OSSZEG;\nBEGIN\n  v_osszeg := v_osszeg * 2;\n"
                "  IF :RENDELES.STATUSZ IN ('U', 'L') THEN\n    :RENDELES.OSSZEG := v_osszeg;\n  END IF;\nEND;") + '"/>\n   '
            '<Trigger Name="KEY-EXEQRY" TriggerText="BEGIN&amp;#10;    ank_blokk.lekerdez;', 1)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            out = migrate(root, source, {'backend_trigger_mode': 'java'})
            service = (out / 'backend/DPS/RendelesServiceImpl.java').read_text(encoding='utf-8')
            self.assertRegex(service, r'var local = new Object\(\) \{\s+(java\.math\.)?BigDecimal vOsszeg;\s+\};')
            self.assertIn('local.vOsszeg = SqlValues.math(local.vOsszeg, ', service)
            self.assertIn('row.osszeg = local.vOsszeg;', service)
            run = javac(root, out)
            if run is not None:
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


V1 = """PROCEDURE lekerdezesi_feltetelek IS
  lek_sql varchar2(1000);
BEGIN
  lek_sql := 'COL6 = ''00''';
  IF :T1.COL1 IS NOT NULL THEN
    lek_sql := lek_sql || ' and COL7 = ''' || :T1.COL1 || '''';
  END IF;
  IF NAME_IN('T1.COL5') IS NOT NULL THEN
    lek_sql := lek_sql || ' and COL8 like ''%' || NAME_IN('T1.COL5') || '%''';
  END IF;
  IF :T1.COL2 = 1 THEN
    lek_sql := lek_sql || ' and COL9 = ' || CHR(39) || 'X' || CHR(39) || ' and length(COL9) > ' || :T1.COL3;
  END IF;
  Set_Block_Property('BLK', DEFAULT_WHERE, lek_sql);
  go_block('BLK');
  execute_query;
END;"""

V2 = """PROCEDURE lekerdezesi_feltetelek(p_blokk VARCHAR2, p_kod IN VARCHAR2) IS
  v_where VARCHAR2(2000);
  v_order VARCHAR2(200);
  v_resz VARCHAR2(200);
BEGIN
  v_resz := ' and COL9 is not null';
  v_where := 'COL7 = ''' || p_kod || '''' || DECODE(:T1.COL2, 1, ' and COL8 = ''A''', 0, ' and COL8 = ''B''', '');
  IF :T1.COL5 IN ('X', 'Y') THEN
     v_where := v_where || v_resz;
     v_order := 'COL9 DESC';
  ELSE
     v_order := 'COL6';
  END IF;
  set_block_property(p_blokk, DEFAULT_WHERE, v_where);
  set_block_property(p_blokk, ORDER_BY, v_order);
  go_block(p_blokk);
  execute_query;
END;"""
B2 = qa.BUTTON.replace('    lekerdezesi_feltetelek;', "    lekerdezesi_feltetelek('BLK', :T1.COL1);")

B3 = """DECLARE
  v_kod t_blk.col7%TYPE;
  v_tol t_blk.col8%TYPE;
  where_text VARCHAR2(2000);
  order_by_text VARCHAR2(200);
  e_x EXCEPTION;
  PRAGMA EXCEPTION_INIT(e_x, -20001);
BEGIN
  v_kod := :T1.COL1;
  v_tol := :T1.COL5;
  CLEAR_FORM(NO_VALIDATE);
  :T1.COL1 := v_kod;
  :T1.COL5 := v_tol;
  where_text := 'COL7 = :T1.COL1';
  IF :T1.COL5 IS NOT NULL THEN
     where_text := where_text || ' AND (COL8 >= :T1.COL5' || ' OR COL8 IS NULL)';
  ELSIF :T1.COL5 IS NULL THEN
     where_text := where_text || ' AND COL8 IS NULL';
  END IF;
  order_by_text := CASE WHEN v_kod = 'Z' THEN 'COL9 DESC' ELSE 'COL6' END;
  set_block_property('BLK', DEFAULT_WHERE, where_text);
  set_block_property('BLK', ORDER_BY, order_by_text);
  go_block('BLK');
  execute_query;
END;"""

# F3 of the survey: the header goes into the screen's items (SELECT ... INTO), the query uses it
B4 = B3.replace("  where_text := 'COL7 = :T1.COL1';", "  SELECT col7 INTO :T1.COL1 FROM t_blk WHERE col6 = v_kod;\n  where_text := 'COL7 = :T1.COL1';")

SMOKE = r'''import java.lang.reflect.Method;

public class QueryTextRunner {
    public static void main(String[] args) throws Exception {
        Class<?> test = Class.forName(args[0]);
        var constructor = test.getDeclaredConstructor();
        constructor.setAccessible(true);
        Object instance = constructor.newInstance();
        int count = 0;
        for (Method method : test.getDeclaredMethods()) {
            if (method.isAnnotationPresent(org.junit.jupiter.api.Test.class)) {
                method.setAccessible(true);
                method.invoke(instance);
                count++;
            }
        }
        System.out.println("junit OK " + count);
    }
}
'''


class JavaQueryVariantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def generate(self, builder=tq.BUILDER, button=qa.BUTTON, config=None):
        (self.root / 'config.json').write_text(json.dumps({'backend_live': True, 'java_company_imports': COMPANY_IMPORTS,
                                                           'java_import_map': '-', **(config or {})}))
        (self.root / 'q.xml').write_bytes(tq.form(builder, button))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            assert main(['migrate', str(self.root / 'q.xml'), '--module', 'query', '--screen', '--config',
                         str(self.root / 'config.json'), '--out', str(self.root / 'out')]) == 0
        out = self.root / 'out'
        service = (out / 'backend/DPS/QueryServiceImpl.java').read_text(encoding='utf-8')
        model = json.loads((out / 'analysis/form.ir.json').read_text(encoding='utf-8'))
        trigger = next(t for t in model['triggers'] if t['event'] == 'WHEN-BUTTON-PRESSED')
        query = service[service.index('static QueryText '):] if 'static QueryText ' in service else ''
        return out, service, query[:query.index('\n    }\n')] if query else '', trigger

    def test_screen_values_pasted_into_the_sql_are_binds(self):
        out, _, query, trigger = self.generate(V1)
        self.assertIsNone(trigger.get('query_java_reason'))
        self.assertIn('lekSql += " and COL7 = :col1";', query)
        self.assertIn("""lekSql += " and COL8 like '%' || :col5 || '%'";""", query)
        self.assertIn("""lekSql += " and COL9 = 'X' and length(COL9) > :col3";""", query)
        equivalence = endpoints(out)['T1.PB_LEKERDEZES']['equivalence']
        self.assertGreater(equivalence['checked'], 10)
        self.assertEqual(equivalence['unverifiable'], 2)  # NULL pasted without quotes: invalid SQL in Forms

    def test_a_builder_with_parameters_decode_in_and_a_dynamic_order(self):
        _, service, query, trigger = self.generate(V2, B2)
        self.assertIsNone(trigger.get('query_java_reason'))
        self.assertIn('pKod = col1;', query)
        self.assertIn('vWhere = "COL7 = :pKod" + (BigDecimal.ONE.equals(col2) ?', query)
        self.assertIn('if ("X".equals(col5) || "Y".equals(col5)) {', query)
        self.assertIn('vWhere += vResz;', query)
        self.assertIn('q.orderBy = vOrder;', query)
        self.assertIn('(q.orderBy == null || q.orderBy.isBlank() ? " ORDER BY COL6" : " ORDER BY " + q.orderBy)', service)

    def test_an_inline_builder_in_the_trigger(self):
        out, service, query, trigger = self.generate(button=B3)
        self.assertIsNone(trigger.get('query_java_reason'))
        self.assertIn('static QueryText onT1PbLekerdezesQuery(String col1, String col5) {', query)
        self.assertIn('whereText = "COL7 = :vKod";', query)  # the restored item after CLEAR_FORM
        self.assertIn('orderByText = ("Z".equals(vKod) ? "COL9 DESC" : "COL6");', query)
        self.assertIn('A WHERE feltételt a trigger saját kódja alapján', service)
        action = endpoints(out)['T1.PB_LEKERDEZES']
        self.assertNotIn('todo', action)  # its own SET_BLOCK_PROPERTY is what the Java query does
        model = json.loads((out / 'analysis/form.ir.json').read_text(encoding='utf-8'))
        runtime = [i for i in model['issues'] if i['code'] == 'RUNTIME_BLOCK_PROPERTY' and 'where_text' in i['detail']]
        self.assertEqual([i['scope'] for i in runtime], ['review'])

    def test_a_header_loaded_into_the_screen_falls_back_with_the_reason(self):
        out, service, _, trigger = self.generate(button=B4)
        self.assertIn('SELECT ... INTO a lekérdezőgombban (INTO :T1.COL1)', trigger['query_java_reason'])
        action = endpoints(out)['T1.PB_LEKERDEZES']
        self.assertEqual(action['query_java_reason'], trigger['query_java_reason'])
        tasks = (out / 'BACKEND_TASKS.md').read_text(encoding='utf-8')
        self.assertIn('## Lekérdezőgombok', tasks)
        self.assertIn('SELECT ... INTO a lekérdezőgombban', tasks)

    def test_the_equivalence_check_catches_a_wrong_translation(self):
        from frm_forms import query_java
        original = query_java.Builder.finish

        def broken(self, atoms, var, before_replace):
            append, parts = original(self, atoms, var, before_replace)
            return append, [('s', p[1].replace("COL9='B56'", "COL9='B57'")) if p[0] == 's' else p for p in parts]
        query_java.Builder.finish = broken
        try:
            _, _, _, trigger = self.generate()
        finally:
            query_java.Builder.finish = original
        self.assertIn('A Java WHERE eltér az eredeti PL/SQL-étől', trigger['query_java_reason'])
        self.assertIn("PL/SQL: \"COL6 = '00' and ((", trigger['query_java_reason'])
        self.assertIn("COL9 = 'B56'\", Java:", trigger['query_java_reason'])
        self.assertIn("COL9 = 'B57'", trigger['query_java_reason'])

    def test_the_generated_junit_test_passes(self):
        out, _, _, _ = self.generate(V2, B2, {'query_java_tests': True})
        test = (out / 'backend/DPS-test/QueryQueryTextTest.java').read_text(encoding='utf-8')
        self.assertIn('class QueryQueryTextTest {', test)
        self.assertIn('check(QueryServiceImpl.onT1PbLekerdezesQuery(', test)
        runner = self.root / 'QueryTextRunner.java'
        runner.write_text(SMOKE, encoding='utf-8')
        run = javac(self.root, out, [runner])
        if run is None:
            self.skipTest('JDK (javac) required')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        package = test.split(';', 1)[0].replace('package ', '').strip()
        result = subprocess.run(['java', '-cp', str(self.root / 'classes'), 'QueryTextRunner', package + '.QueryQueryTextTest'],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('junit OK 1', result.stdout)

    def test_every_variant_compiles(self):
        for label, builder, button in [('v1', V1, qa.BUTTON), ('v3', tq.BUILDER, B3)]:
            with self.subTest(label):
                out, _, _, _ = self.generate(builder, button)
                run = javac(self.root, out)
                if run is None:
                    self.skipTest('JDK (javac) required')
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                shutil.rmtree(self.root / 'out')
                shutil.rmtree(self.root / 'classes', ignore_errors=True)


if __name__ == '__main__':
    unittest.main()

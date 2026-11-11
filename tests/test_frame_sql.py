"""The SQL of a data endpoint or a LOV: compared as Oracle reads it, run through constants, repaired when retyped.

The reported case: a LOV over a long record group query never got through. The contract compared the statement
letter by letter (whitespace and case aside), so a correct copy without the record group's "--" comment, with an AS
before an alias or split into lines was rejected - twice, then a draft - while a one-line copy that kept the comment
(Oracle then ignores the rest of the statement) passed.
"""
import contextlib
import io
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch

from frm_forms import answer_checks, frame_sql
from frm_forms import assistant_reply as reply
from frm_forms import backend_assistant as engine
from frm_forms.cli import main
from java_support import trigger_mode
from test_company_cl import CL_IMPORTS
from test_dto_dialect import compile_dps
import test_ai_triggers as ai

setUpModule, tearDownModule = trigger_mode('ai')

ROOT = Path(__file__).resolve().parents[1]
INNER = ["SELECT V_M_KODERT.ME_KERT_ERTEK ME_KERT_ERTEK,",
         "       V_M_KODERT.ME_KNEV_NEV ME_KNEV_NEV -- a megnevezés",
         "  FROM V_M_KODERT",
         " WHERE V_M_KODERT.ME_KTIP = 'INPTIP'",
         "   AND V_M_KODERT.ME_ERV_TOL <= SYSDATE",
         "   AND (V_M_KODERT.ME_ERV_IG IS NULL OR V_M_KODERT.ME_ERV_IG >= SYSDATE)",
         "   AND V_M_KODERT.ME_SZUL IN (SELECT S.KOD FROM V_M_SZULO S WHERE S.TIPUS = :RENDELES.STATUSZ)",
         " ORDER BY DECODE(V_M_KODERT.ME_SORREND, NULL, 999, V_M_KODERT.ME_SORREND), V_M_KODERT.ME_KNEV_NEV"]
STATEMENT = "\n".join(INNER).replace(":RENDELES.STATUSZ", ":p0")
WRAPPER = ["SELECT * FROM (", *STATEMENT.split("\n"), ") lov",
           " WHERE (:term IS NULL OR UPPER(lov.ME_KERT_ERTEK) LIKE UPPER(:term) || '%')", " FETCH FIRST :limit ROWS ONLY"]
NO_COMMENT = [line.split("--")[0].rstrip() for line in WRAPPER]


def form() -> bytes:
    """The order form of the survey fixture, its LOV over a long record group query (a comment, a subquery, a bind)."""
    text = (ROOT / "tests/fixtures/felmeres_replika_fmb.xml").read_text(encoding="utf-8")
    query = "&amp;#10;".join(line.replace("<", "&lt;").replace(">", "&gt;").replace("'", "&apos;") for line in INNER)
    text = text.replace('RecordGroupQuery="SELECT kod, nev FROM statusz_kodtar ORDER BY kod"', 'RecordGroupQuery="' + query + '"')
    text = text.replace('<LOVColumnMapping ColumnName="KOD" ReturnItem="RENDELES.STATUSZ"/>',
                        '<LOVColumnMapping ColumnName="ME_KERT_ERTEK" ReturnItem="RENDELES.STATUSZ"/>')
    return text.encode("utf-8")


def literal(lines, newline=True, join=" ", end=""):
    """The SQL as the model may write it in Java: one string (newline False: the lines joined by `join`), or one
    string per line ending in \\n (newline True) or in `end` (newline None)."""
    if newline is False:
        return '"' + join.join(line.strip() for line in lines).replace('"', '\\"') + '"'
    tail = (lambda n: '\\n' if n < len(lines) - 1 else '') if newline else (lambda n: end)
    return "\n        + ".join('"' + (line if newline else line.strip()).replace('"', '\\"') + tail(n) + '"'
                             for n, line in enumerate(lines))


def lov_answer(sql_java: str, dto=True) -> str:
    result, request = ("RtLovResultDto", "RtLovRequestDto") if dto else ("LovResult", "LovRequest")
    getter = (lambda name: "get" + name[0].upper() + name[1:] + "()") if dto else (lambda name: name + "()")
    return ("```java\n// SERVICE\npublic " + result + " lovLovStatusz(UserDto user, " + request + " request) throws Exception {\n"
            "    return log1x(log, RtConstants.LOV_LOV_STATUSZ_NAME, user, null, () -> {\n"
            "        List<Object[]> rows = entityManager.createNativeQuery(" + sql_java + ")\n"
            "                .setParameter(\"term\", LovQuery.term(request." + getter("term") + "))\n"
            "                .setParameter(\"limit\", LovQuery.limit(request." + getter("limit") + "))\n"
            "                .setParameter(\"p0\", LovQuery.text(request." + getter("parameters") + ", \"RENDELES.STATUSZ\"))\n"
            "                .getResultList();\n"
            "        return new " + result + "(LovQuery.rows(rows, \"ME_KERT_ERTEK\", \"ME_KNEV_NEV\"));\n    });\n}\n```")


class SqlTokenTests(unittest.TestCase):
    def test_comments_go_as_oracle_drops_them(self):
        self.assertEqual(frame_sql.strip_comments("SELECT '--x' a -- note\nFROM t /* hint */ WHERE b = 'it''s'").split(),
                         ["SELECT", "'--x'", "a", "FROM", "t", "WHERE", "b", "=", "'it''s'"])
        one_line = "SELECT a, b -- the name FROM t WHERE c = :c"
        self.assertEqual(frame_sql.tokens(one_line), ["SELECT", "A", ",", "B"])  # the rest is the comment
        self.assertEqual(frame_sql.parameters(one_line), [])
        self.assertEqual(frame_sql.parameters(one_line, comments=True), ["c"])

    def test_tokens_as_oracle_reads_them(self):
        self.assertEqual(frame_sql.tokens("select t.a AS x, CAST(b AS NUMBER) y from t where c != 1"),
                         frame_sql.tokens("SELECT T.A X, CAST(B AS NUMBER) Y\nFROM T\nWHERE C <> 1"))
        self.assertNotEqual(frame_sql.tokens("SELECT a FROM t"), frame_sql.tokens("SELECT aFROM t"))  # a lost space
        self.assertNotEqual(frame_sql.tokens("SELECT 'A B' FROM t"), frame_sql.tokens("SELECT 'a b' FROM t"))  # literals exact
        self.assertEqual(frame_sql.parameters("SELECT ':no' FROM t WHERE a = :Kod AND b = :kod"), ["Kod", "kod"])

    def test_a_comment_that_swallows_the_rest_of_a_one_line_statement(self):
        self.assertTrue(frame_sql.swallowed(" ".join(line.strip() for line in WRAPPER)))
        self.assertIsNone(frame_sql.swallowed("\n".join(WRAPPER)))  # its own line: harmless
        self.assertIsNone(frame_sql.swallowed("SELECT a FROM t -- last line comment"))


class ContractTests(unittest.TestCase):
    """answer_checks.problems on the LOV's contract: the record group query, as Oracle reads it."""
    CONTRACT = {"sql": [STATEMENT], "calls": []}

    def check(self, sql_java, constants=None):
        code = reply.extract(lov_answer(sql_java, dto=False), reply.Context(method="lovLovStatusz", mode="service")).response["body"]
        return answer_checks.problems(code, self.CONTRACT, constants)

    def test_a_correct_copy_passes_however_it_is_written(self):
        for name, sql in {"one line, the comment left out": literal(NO_COMMENT, newline=False),
                          "one string per line with \\n, the comment kept": literal(WRAPPER),
                          "one string per line, a trailing space each": literal(NO_COMMENT, newline=None, end=" "),
                          "AS before the aliases": literal([line.replace("ERTEK ME_KERT", "ERTEK AS ME_KERT") for line in NO_COMMENT], newline=False)}.items():
            with self.subTest(name):
                self.assertEqual(self.check(sql), [])

    def test_a_broken_copy_is_found(self):
        swallowed = self.check(literal(WRAPPER, newline=False))  # "-- a megnevezés FROM ..." on one line
        self.assertTrue(any("-- comment" in en for _, en in swallowed), swallowed)
        self.assertTrue(any("hiányzik vagy megváltozott" in hu for hu, _ in swallowed), swallowed)
        joined = self.check(literal(NO_COMMENT, newline=None))  # one string per line, no space between them
        self.assertTrue(any("hiányzik vagy megváltozott" in hu for hu, _ in joined), joined)

    def test_the_constant_is_the_statement_and_its_parameters_are_checked(self):
        constants = [{"name": "LOV_LOV_STATUSZ_SQL", "sql": "\n".join(WRAPPER), "required": STATEMENT, "what": ""}]
        self.assertEqual(self.check("LOV_LOV_STATUSZ_SQL", constants), [])
        code = reply.extract(lov_answer("LOV_LOV_STATUSZ_SQL", dto=False).replace('                .setParameter("p0", LovQuery.text(request.parameters(), "RENDELES.STATUSZ"))\n', ""),
                             reply.Context(method="lovLovStatusz", mode="service")).response["body"]
        self.assertTrue(any('no setParameter for "p0"' in en for _, en in answer_checks.problems(code, self.CONTRACT, constants)))
        missing = answer_checks.problems('return new LovResult(List.of());', self.CONTRACT, constants)
        self.assertIn("createNativeQuery(LOV_LOV_STATUSZ_SQL)", missing[0][1])

    def test_a_string_variable_is_the_statement_unless_it_is_changed(self):
        code = 'String sql = "SELECT 1 FROM T WHERE X = :x";\nvar rows = entityManager.createNativeQuery(sql).getResultList();'
        self.assertIn('no setParameter for "x"', answer_checks.problems(code)[0][1])
        changed = code.replace('";\n', '";\nsql += " AND Y = :y";\n')
        self.assertEqual(answer_checks.problems(changed), [])  # built at run time: not judged


class FrameConstantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        source, config = cls.root / "form.xml", cls.root / "company.json"
        source.write_bytes(form())
        config.write_text(json.dumps({"AWU_AZON": "00123", "java_company_imports": CL_IMPORTS, "backend_trigger_mode": "ai"}))
        cls.output = cls.root / "module"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            assert main(["migrate", str(source), "--screen", "--module", "rt", "--config", str(config), "--out", str(cls.output)]) == 0
        cls.options = engine.Options()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def task(self, output, method):
        return next(t for t in engine.prepare_workspace(output)["tasks"] if t["method"] == method)

    def packet(self, method):
        return json.loads((self.output / engine.AREA / "tasks" / (self.task(self.output, method)["id"] + ".json")).read_text())

    def test_every_frame_names_its_statements(self):
        lov = frame_sql.statements(self.packet("lovLovStatusz"))
        self.assertEqual([c["name"] for c in lov], ["LOV_LOV_STATUSZ_SQL"])
        self.assertEqual(lov[0]["sql"], "\n".join(WRAPPER))  # the statement the comment shows, line by line
        self.assertEqual(frame_sql.parameters(lov[0]["sql"]), ["p0", "term", "limit"])
        listing = frame_sql.statements(self.packet("listRendeles"))[0]
        self.assertEqual(listing["name"], "LIST_RENDELES_SQL")
        self.assertTrue(listing["sql"].endswith(" OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY"))
        self.assertEqual([c["name"] for c in frame_sql.statements(self.packet("deleteTetel"))],
                         ["DELETE_TETEL_LOCK_SQL", "DELETE_TETEL_SQL"])
        self.assertEqual([c["name"] for c in frame_sql.statements(self.packet("createTetel"))], ["CREATE_TETEL_SQL"])
        self.assertEqual(frame_sql.statements(self.packet("onCtrlPbMent")), [])  # a button: its SQL is the Forms code's
        self.assertEqual(frame_sql.statements({**self.packet("lovLovStatusz"), "existing_method": ""}), [])

    def test_the_prompt_names_the_constants(self):
        user = engine.preview(self.output, self.task(self.output, "lovLovStatusz")["id"], self.options)["user"]
        self.assertIn("entityManager.createNativeQuery(LOV_LOV_STATUSZ_SQL)", user)
        self.assertIn("- LOV_LOV_STATUSZ_SQL: the record group query of the comment, with its filter and limit; "
                      "parameters :p0, :term, :limit", user)
        self.assertNotIn("Run the Oracle SQL of\n   the comments as written", user)
        button = engine.preview(self.output, self.task(self.output, "onCtrlPbMent")["id"], self.options)["user"]
        self.assertNotIn("SQL constants of the service", button)

    def propose(self, output, answer):
        client = ai.MethodClient({"lovLovStatusz": answer})
        with patch("frm_forms.ollama.opener", return_value=client):
            result = engine.propose(output, self.task(output, "lovLovStatusz")["id"], self.options)
        return result, client

    def copy(self):
        output = Path(tempfile.mkdtemp(dir=self.root)) / "module"
        shutil.copytree(self.output, output)
        return output

    def test_a_broken_copy_runs_the_constant_is_applied_and_compiles(self):
        # The one-line copy that keeps the "--" comment: Oracle would ignore the rest. One call, no correction round.
        output = self.copy()
        result, client = self.propose(output, lov_answer(literal(WRAPPER, newline=False)))
        self.assertEqual(result["status"], "proposal", result["extraction"])
        self.assertEqual(len(client.requests), 1)
        self.assertTrue(any("LOV_LOV_STATUSZ_SQL" in w for w in result["extraction"]["warnings"]))
        engine.apply_proposal(output, result["id"], reviewed=True)
        service = (output / "backend/DPS/RtServiceImpl.java").read_text(encoding="utf-8")
        self.assertEqual(service.count("private static final String LOV_LOV_STATUSZ_SQL ="), 1)
        self.assertIn('"       V_M_KODERT.ME_KNEV_NEV ME_KNEV_NEV -- a megnevezés\\n"', service)  # its own line
        self.assertIn("createNativeQuery(LOV_LOV_STATUSZ_SQL)", ai.code_lines(service))
        self.assertNotIn("-- a megnevezés FROM", service)
        self.assertLess(service.index("LOV_LOV_STATUSZ_SQL ="), service.index("private EntityManager entityManager"))
        # The kept hints name the constant instead of repeating its SQL; no comment of the migrator's own.
        method = ai.method_text(service, "lovLovStatusz")
        self.assertIn("//region AI-feladat: LOV_STATUSZ", method)
        self.assertRegex(method, r"// SQL \(a rekordcsoport lekérdezése, a beírt szövegre szűrve\):\n\s*// LOV_LOV_STATUSZ_SQL\n")
        self.assertIn("// Paraméterek: :term = LovQuery.term(", method)
        self.assertEqual(service.count("WHERE S.TIPUS = :p0"), 1)
        self.assertNotIn("a migrátor tartja karban", service)
        if shutil.which("java"):
            compile_dps(self, output, Path(tempfile.mkdtemp(dir=self.root)), cls="Rt")

    def test_a_correct_copy_stays_as_written(self):
        output = self.copy()
        result, _ = self.propose(output, lov_answer(literal(NO_COMMENT, newline=False)))
        self.assertEqual(result["status"], "proposal", result["extraction"])
        engine.apply_proposal(output, result["id"], reviewed=True)
        service = (output / "backend/DPS/RtServiceImpl.java").read_text(encoding="utf-8")
        self.assertNotIn("LOV_LOV_STATUSZ_SQL", ai.code_lines(service))
        joined = re.sub(r'"\s*\+\s*"', "", service)  # the formatter splits a long literal into "..." + "..."
        self.assertIn('createNativeQuery("SELECT * FROM ( SELECT V_M_KODERT.ME_KERT_ERTEK ME_KERT_ERTEK, V_M_KODERT.ME_KNEV_NEV '
                      'ME_KNEV_NEV FROM V_M_KODERT', joined)

    def test_the_model_runs_the_constant_and_it_is_declared_once(self):
        output = self.copy()
        result, _ = self.propose(output, lov_answer("LOV_LOV_STATUSZ_SQL"))
        self.assertEqual(result["status"], "proposal", result["extraction"])
        engine.apply_proposal(output, result["id"], reviewed=True)
        service = (output / "backend/DPS/RtServiceImpl.java").read_text(encoding="utf-8")
        again = frame_sql.declare(service, frame_sql.statements(self.packet("lovLovStatusz")))
        self.assertEqual(again.count("String LOV_LOV_STATUSZ_SQL"), 1)
        changed = frame_sql.declare(service, [{"name": "LOV_LOV_STATUSZ_SQL", "sql": "SELECT 1 FROM DUAL"}])
        self.assertIn('LOV_LOV_STATUSZ_SQL = "SELECT 1 FROM DUAL";', changed)  # kept up to date with the comment
        self.assertEqual(changed.count("String LOV_LOV_STATUSZ_SQL"), 1)

    def test_a_cached_answer_is_read_again_with_todays_checks(self):
        # The cache keeps the model's raw answer: a run answered from it (no model call, done at once) goes through
        # the current checks and repairs, not through the processed answer an older migrator stored.
        output = self.copy()
        first, client = self.propose(output, lov_answer(literal(WRAPPER, newline=False)))
        self.assertEqual(len(client.requests), 1)
        cache = next((output.parent / ".frm-assistant-cache").glob("*.json"))
        entry = json.loads(cache.read_text(encoding="utf-8"))
        entry["response"]["body"] = "return null;"  # what an older migrator may have stored
        cache.write_text(json.dumps(entry), encoding="utf-8")
        again, client = self.propose(output, "Nem tudom.")
        self.assertEqual(client.requests, [])
        self.assertTrue(again["cached"])
        self.assertEqual(again["response"]["body"], first["response"]["body"])
        self.assertIn("createNativeQuery(LOV_LOV_STATUSZ_SQL)", again["response"]["body"])
        entry["raw"] = "Nem tudom."  # an answer today's checks reject: the model is asked again
        cache.write_text(json.dumps(entry), encoding="utf-8")
        fresh, client = self.propose(output, lov_answer("LOV_LOV_STATUSZ_SQL"))
        self.assertEqual(len(client.requests), 1)
        self.assertFalse(fresh["cached"])

    def test_a_different_statement_goes_back_to_the_model(self):
        # Another condition (a new parameter): not the comment's statement, so no replacement; the model is told to run
        # the constant.
        output = self.copy()
        other = [line.replace("'INPTIP'", ":tipus") for line in NO_COMMENT]
        answer = lov_answer(literal(other, newline=False)).replace(
            '.setParameter("term"', '.setParameter("tipus", "X")\n                .setParameter("term"')
        result, client = self.propose(output, answer)
        self.assertEqual(result["status"], "draft")
        self.assertEqual(len(client.requests), 2)
        correction = client.requests[1]["messages"][-1]["content"]
        self.assertIn("createNativeQuery(LOV_LOV_STATUSZ_SQL)", correction)


class CrudFrameTests(unittest.TestCase):
    """The default DTO format, a data endpoint with two statements: the lock and the UPDATE."""

    def test_a_retyped_lock_with_a_lost_space_runs_its_constant(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = ai.migrate(root, root / "module")
            answer = ai.ANSWERS["updateOrders"].replace('"SELECT ID, CODE, NAME FROM APP_ORDERS WHERE ID = :id FOR UPDATE NOWAIT"',
                                                         '"SELECT ID, CODE, NAME" + "FROM APP_ORDERS WHERE ID = :id FOR UPDATE NOWAIT"')
            self.assertNotEqual(answer, ai.ANSWERS["updateOrders"])
            task = next(t for t in engine.prepare_workspace(output)["tasks"] if t["method"] == "updateOrders")
            client = ai.MethodClient({"updateOrders": answer})
            with ai.model_path(), patch("frm_forms.ollama.opener", return_value=client):
                result = engine.propose(output, task["id"], engine.Options())
            self.assertEqual(result["status"], "proposal", result["extraction"])
            self.assertEqual(len(client.requests), 1)
            prompt = client.requests[0]["messages"][1]["content"]
            self.assertIn("- UPDATE_ORDERS_LOCK_SQL: the lock (SELECT ... FOR UPDATE NOWAIT); parameters :id", prompt)
            self.assertIn("- UPDATE_ORDERS_SQL: the UPDATE; parameters :code, :name, :id", prompt)
            engine.apply_proposal(output, result["id"], reviewed=True)
            service = (output / ai.SERVICE).read_text(encoding="utf-8")
            update = ai.method_text(service, "updateOrders")
            self.assertIn("createNativeQuery(UPDATE_ORDERS_LOCK_SQL)", update)
            self.assertIn('"UPDATE APP_ORDERS SET CODE = :code, NAME = :name WHERE ID = :id"', update)  # right as written
            joined = re.sub(r'"\s*\+\s*"', "", service)  # the formatter splits a long literal into "..." + "..."
            self.assertIn('UPDATE_ORDERS_LOCK_SQL = "SELECT ID, CODE, NAME FROM APP_ORDERS WHERE ID = :id FOR UPDATE NOWAIT";', joined)
            self.assertNotIn("UPDATE_ORDERS_SQL =", service)  # not used: not declared
            self.assertIn("// 2. Zárolás: UPDATE_ORDERS_LOCK_SQL (:mező = original.mező).", update)  # named, not repeated
            self.assertIn("UPDATE APP_ORDERS SET CODE = :code", update.split("//endregion")[0])  # inline SQL: kept


if __name__ == "__main__":
    unittest.main()

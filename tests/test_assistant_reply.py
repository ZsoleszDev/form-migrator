"""Reading local-model answers: Markdown blocks, whole classes, bare bodies, legacy JSON, mistakes."""
import json
import unittest

from frm_forms import assistant_reply as reply
from frm_forms.java_scan import java_mask, scan_members

SERVICE = """package hu.company.features.lookup.dps;

import java.util.Map;
import hu.company.common.UserDto;
import hu.company.features.lookup.cl.LookupDtos.ActionRequest;
import hu.company.features.lookup.cl.LookupDtos.ActionResult;

public class LookupServiceImpl extends ModuleServiceBase<DpsLogHelper> implements LookupService {
    static final boolean MODULE_REVIEWED = true;

    @Override
    public ActionResult onCtrlRun(UserDto user, ActionRequest request) throws Exception {
        return log1x(log, LookupConstants.ON_CTRL_RUN_NAME, user, null, () -> {
            //region Eredeti Forms-kód kiindulásnak
            // SELECT name INTO :CTRL.NAME FROM app.account WHERE id = :CTRL.ID;
            //endregion
            throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, "Ez a gomb még nincs átültetve");
        });
    }

    @Override
    public ActionResult onCtrlOther(UserDto user, ActionRequest request) throws Exception {
        return log1x(log, LookupConstants.ON_CTRL_OTHER_NAME, user, null, () -> null);
    }

    private String normalize(String value) {
        return value == null || value.isEmpty() ? null : value;
    }
}
"""
REPOSITORY = """package hu.company.features.lookup.dps;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

// Forms migrator JPA repository: APP.ACCOUNT.
public interface AppAccountRepository extends JpaRepository<AppAccountDomain, java.math.BigDecimal> {
    @Query("SELECT d FROM AppAccountDomain d WHERE d.id = :id")
    AppAccountDomain findAccount(@Param("id") java.math.BigDecimal id);
}
"""


def context(**changes):
    values = dict(method="onCtrlRun", service=SERVICE, module_class="Lookup", namespace="jakarta",
                  repositories=[reply.Repository("AppAccountRepository", "appAccountRepository",
                                                 "AppAccountDomain", REPOSITORY, "BigDecimal")])
    values.update(changes)
    return reply.Context(**values)


def service_block(body, label="// SERVICE"):
    return ("```java\n" + label + "\npublic ActionResult onCtrlRun(UserDto user, ActionRequest request) throws Exception {\n"
            "    return log1x(log, LookupConstants.ON_CTRL_RUN_NAME, user, null, () -> {\n"
            + "\n".join("        " + line for line in body.strip().split("\n")) + "\n    });\n}\n```\n")


BODY = """java.math.BigDecimal id = PlsqlValues.number(request.blocks(), "CTRL", "ID");
AppAccountDomain row = appAccountRepository.loadName(id);
if (row == null) {
    throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "NO_DATA_FOUND");
}
Map<String, Map<String, String>> blocks = new LinkedHashMap<>();
PlsqlValues.put(blocks, "CTRL", "NAME", row.getName());
return new ActionResult(blocks, List.of(), List.of(), Map.of());"""

REPO_BLOCK = """```java
// REPOSITORY AppAccountRepository
@Query("SELECT d FROM AppAccountDomain d WHERE d.id = :id")
AppAccountDomain loadName(@Param("id") java.math.BigDecimal id);
```
"""


class ExtractionTests(unittest.TestCase):
    def test_requested_format_gives_body_helpers_repository_and_notes(self):
        text = ("Itt a kód.\n\n" + service_block(BODY + "\n").replace("```\n", "\nprivate String cleaned(String v) {\n    return v == null ? null : v.trim();\n}\n```\n", 1)
                + "\n" + REPO_BLOCK + "\nMISSING: none\nNOTES:\n- A NULL név megmarad.\n")
        result = reply.extract(text, context())
        self.assertEqual(result.problems, [])
        response = result.response
        self.assertEqual(response["status"], "proposal")
        self.assertTrue(response["body"].startswith("java.math.BigDecimal id = PlsqlValues.number"))
        self.assertNotIn("log1x", response["body"])
        self.assertTrue(response["private_methods"].startswith("private String cleaned(String v)"))
        self.assertEqual(response["repositories"][0]["name"], "AppAccountRepository")
        self.assertIn("AppAccountDomain loadName(@Param(\"id\") java.math.BigDecimal id);", response["repositories"][0]["methods"])
        self.assertEqual(response["missing_context"], [])
        self.assertEqual(response["assumptions"], ["A NULL név megmarad."])
        self.assertEqual([b["kind"] for b in result.blocks], ["service", "repository"])

    def test_whole_classes_with_their_own_fields_and_constructor(self):
        text = """```java
package hu.company.features.lookup.dps;
import java.util.*;
@Service
public class LookupServiceImpl extends ModuleServiceBase<DpsLogHelper> implements LookupService {
    @Autowired
    private AppAccountRepository appAccountRepository;

    @Override
    public ActionResult onCtrlRun(UserDto user, ActionRequest request) throws Exception {
        return log1x(log, LookupConstants.ON_CTRL_RUN_NAME, user, null, () -> {
            AppAccountDomain row = appAccountRepository.loadName(null);
            return new ActionResult(Map.of(), List.of(row.getName()), List.of(), Map.of());
        });
    }

    @Override
    public ActionResult onCtrlOther(UserDto user, ActionRequest request) throws Exception {
        return log1x(log, LookupConstants.ON_CTRL_OTHER_NAME, user, null, () -> null);
    }
}
```

```java
import org.springframework.data.jpa.repository.Query;
public interface AppAccountRepository extends JpaRepository<AppAccountDomain, java.math.BigDecimal> {
    String PREFIX = "A";

    @Query("SELECT d FROM AppAccountDomain d WHERE d.id = :id")
    AppAccountDomain loadName(@Param("id") java.math.BigDecimal id);

    public String prefixed(String name) {
        return PREFIX + name;
    }
}
```"""
        result = reply.extract(text, context())
        self.assertEqual(result.problems, [])
        self.assertIn("appAccountRepository.loadName(null)", result.response["body"])
        self.assertEqual(result.response["private_methods"], "")  # the other endpoint is never taken over
        methods = result.response["repositories"][0]["methods"]
        self.assertIn("AppAccountDomain loadName(@Param(\"id\") java.math.BigDecimal id);", methods)
        self.assertIn("String PREFIX = \"A\";", methods)  # an interface field is a constant
        self.assertIn("default String prefixed(String name) {", methods)  # a body in an interface: a default method
        self.assertTrue(any("onCtrlOther" in note for note in result.notes))

    def test_markdown_headings_bare_body_and_unterminated_last_block(self):
        text = ("### SERVICE\n```java\n" + BODY + "\n```\n\n**AppAccountRepository.java**\n```java\n"
                "@Query(\"SELECT d FROM AppAccountDomain d WHERE d.id = :id\")\n"
                "AppAccountDomain loadName(@Param(\"id\") java.math.BigDecimal id);\n")
        result = reply.extract(text, context())
        self.assertEqual(result.problems, [])
        self.assertTrue(result.truncated)
        self.assertEqual(result.blocks[1]["target"], "AppAccountRepository")
        self.assertIn("appAccountRepository.loadName(id)", result.response["body"])
        self.assertTrue(any("nincs lezárva" in warning for warning in result.warnings))

    def test_plain_java_without_fences_and_thinking_text(self):
        text = "<think>Először a SELECT-et nézem.</think>\n" + BODY + "\n\nNOTES:\n- egyszerű lekérdezés\n"
        result = reply.extract(text, context(repositories=[reply.Repository(
            "AppAccountRepository", "appAccountRepository", "AppAccountDomain",
            REPOSITORY.replace("findAccount", "loadName"))]))
        self.assertEqual(result.problems, [])
        self.assertEqual(result.format, "plain")
        self.assertEqual(result.thinking, "Először a SELECT-et nézem.")
        self.assertEqual(result.response["assumptions"], ["egyszerű lekérdezés"])

    def test_original_comment_and_module_gate_are_left_to_the_migrator(self):
        body = ("//region Eredeti Forms-kód kiindulásnak\n// SELECT ...\n//endregion\n"
                "if (!MODULE_REVIEWED) {\n    throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, \"x\");\n}\n" + BODY)
        result = reply.extract(service_block(body) + REPO_BLOCK, context())
        self.assertEqual(result.problems, [])
        self.assertNotIn("region", result.response["body"])
        self.assertNotIn("MODULE_REVIEWED", result.response["body"])

    def test_reported_case_needs_context_with_repository_is_kept_as_visible_draft(self):
        # 4.33.3 threw this answer away twice ("Hiányzó adat esetén nincs részleges repository/DTO.").
        legacy = {"status": "needs_context", "summary": "Kell a PKG_ACCOUNT forrása.", "body": "", "imports": [],
                  "private_methods": "", "assumptions": [], "missing_context": ["PKG_ACCOUNT.CHECK_LIMIT forrása"],
                  "test_cases": [], "dtos": [], "repositories": [{"name": "AppAccountRepository", "imports": [],
                  "methods": "@Query(\"SELECT d FROM AppAccountDomain d WHERE d.id = :id\") AppAccountDomain loadName(@Param(\"id\") java.math.BigDecimal id);"}]}
        result = reply.extract(json.dumps(legacy), context())
        self.assertEqual(result.problems, [])
        self.assertEqual(result.format, "json")
        self.assertEqual(result.response["status"], "needs_context")
        self.assertEqual(result.response["missing_context"], ["PKG_ACCOUNT.CHECK_LIMIT forrása"])
        self.assertEqual(len(result.response["repositories"]), 1)
        self.assertTrue(any("PKG_ACCOUNT.CHECK_LIMIT" in warning for warning in result.warnings))

    def test_a_spring_data_interface_is_the_repository_and_unknown_ones_are_corrected(self):
        # The repository is an interface extending JpaRepository: a Spring Data method name is a method of it, findById
        # is inherited. The repository without the module prefix is the one repository that ends so: repaired.
        text = ("```java\n// SERVICE\nvar row = accountRepository.findById(id).orElse(null);\n"
                "return new ActionResult(Map.of(), List.of(), List.of(), Map.of());\n```\n"
                "```java\n// REPOSITORY AccountRepository\npublic interface AccountRepository extends "
                "JpaRepository<AppAccountDomain, BigDecimal> {\n  List<AppAccountDomain> findByName(String name);\n"
                "  Optional<AppAccountDomain> findById(BigDecimal id);\n}\n```")
        result = reply.extract(text, context())
        self.assertEqual(result.problems, [])
        self.assertIn("appAccountRepository.findById(id)", result.response["body"])
        self.assertEqual(result.response["repositories"][0]["methods"], "List<AppAccountDomain> findByName(String name);")
        self.assertTrue(any("findById() a JpaRepository része" in n for n in result.notes), result.notes)
        unknown = reply.extract(service_block("otherRepository.findById(id);\nreturn null;"), context())
        self.assertIn("`otherRepository` does not exist. Use only these repository fields: appAccountRepository.",
                      reply.correction(unknown, context()))

    def test_jpa_repository_methods_need_no_code(self):
        """findById/save/delete ...: JpaRepository's, also beside the generated methods; nothing to write."""
        text = ("```java\n// SERVICE\nvar row = accountRepository.findById(id).orElse(null);\nrow.setName(\"x\");\n"
                "accountRepository.save(row);\nreturn new ActionResult(Map.of(), List.of(), List.of(), Map.of());\n```\n"
                "```java\n// REPOSITORY AccountRepository\npublic interface AccountRepository extends "
                "JpaRepository<AppAccountDomain, BigDecimal> {}\n```")
        result = reply.extract(text, context())
        self.assertEqual(result.problems, [])
        self.assertEqual(result.response["repositories"], [])
        generated = context(generated={"AppAccountRepository": ["appAccountRepository.loadName(BigDecimal id) returns String"]})
        self.assertEqual(reply.extract(service_block("appAccountRepository.findById(id);\nreturn null;"), generated).problems, [])
        # a method that is neither generated nor JpaRepository's goes back to the model
        result = reply.extract(service_block("appAccountRepository.loadEverything(id);\nreturn null;"), generated)
        self.assertEqual(len(result.problems), 1)
        self.assertIn("Call only the generated methods (their SQL is the Forms code's SQL): "
                      "appAccountRepository.loadName(BigDecimal id) returns String.", result.problems[0].en)

    def test_unicode_escapes_in_strings_become_characters_elsewhere_a_correction(self):
        body = 'throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "Hi\\u00e1nyz\\u00f3 \\"k\\u00f3d\\"");'
        result = reply.extract(service_block(body), context())
        self.assertEqual(result.problems, [])
        self.assertIn('"Hiányzó \\"kód\\""', result.response["body"])
        hidden = "String a = \"x\"; \\u0022; return null;"
        self.assertTrue(any("unicode-escape" in p.hu for p in reply.extract(service_block(hidden), context()).problems))

    def test_getter_style_request_access_is_repaired_without_a_new_call(self):
        result = reply.extract(service_block("String id = request.getBlocks().get(\"CTRL\").get(\"ID\");\n"
                                             "String p = request.getParameters().get(\"X\"); // request.getBlocks() in a comment\n"
                                             "return new ActionResult(Map.of(), List.of(id, p), List.of(), Map.of());"), context())
        self.assertEqual(result.problems, [])
        self.assertIn('String id = request.blocks().get("CTRL").get("ID");', result.response["body"])
        self.assertIn('request.parameters().get("X"); // request.getBlocks() in a comment', result.response["body"])
        self.assertIn("Helyben javítva: request.getBlocks() -> request.blocks(), request.getParameters() -> "
                      "request.parameters()", " ".join(result.notes))

    def test_the_records_getters_and_setters_become_its_fields(self):
        ctx = context(row_fields=frozenset({"id", "vevoNev"}))
        result = reply.extract(service_block('row.setVevoNev(lookup(row.getId()));\nif (row.getId() == null) { row.setVevoNev(null); }\n'
                                             'other.setVevoNev("x");\nrow.setUnknown(1);\nreturn null;'), ctx)
        body = result.response["body"]
        self.assertIn("row.vevoNev = lookup(row.id);", body)
        self.assertIn("if (row.id == null) { row.vevoNev = null; }", body)
        self.assertIn('other.setVevoNev("x");', body)  # not the record
        self.assertIn("row.setUnknown(1);", body)       # not a field of it

    def test_called_repository_method_must_be_written(self):
        result = reply.extract(service_block(BODY), context())
        self.assertEqual(len(result.problems), 1)
        self.assertIn("appAccountRepository.loadName", result.problems[0].hu)
        self.assertIn("does not define it", result.problems[0].en)

    def test_existing_repository_method_is_reused_or_renamed_with_the_call(self):
        same = REPO_BLOCK.replace("loadName", "findAccount")
        result = reply.extract(service_block(BODY.replace("loadName", "findAccount")) + same, context())
        self.assertEqual(result.problems, [])
        self.assertEqual(result.response["repositories"], [])  # identical: the existing method is used
        different = same.replace("WHERE d.id = :id", "WHERE d.id = :id AND d.name IS NOT NULL")
        result = reply.extract(service_block(BODY.replace("loadName", "findAccount")) + different, context())
        self.assertEqual(result.problems, [])
        self.assertIn("AppAccountDomain onCtrlRunFindAccount(", result.response["repositories"][0]["methods"])
        self.assertIn("appAccountRepository.onCtrlRunFindAccount(id)", result.response["body"])

    def test_service_helper_named_like_an_existing_method_is_renamed(self):
        text = service_block("return new ActionResult(Map.of(), List.of(normalize(\"x\")), List.of(), Map.of());").replace(
            "```\n", "\nprivate String normalize(String v) {\n    return v.trim();\n}\n```\n", 1)
        result = reply.extract(text, context())
        self.assertEqual(result.problems, [])
        self.assertIn("private String onCtrlRunNormalize(String v)", result.response["private_methods"])
        self.assertIn("onCtrlRunNormalize(\"x\")", result.response["body"])

    def test_jpql_is_the_way_native_sql_a_warning_and_entity_manager_a_problem(self):
        result = reply.extract(service_block(BODY) + REPO_BLOCK, context())
        self.assertEqual((result.problems, [w for w in result.warnings if "natív" in w]), ([], []))
        native = REPO_BLOCK.replace('@Query("SELECT d FROM AppAccountDomain d WHERE d.id = :id")',
                                    '@Query(value = "SELECT * FROM APP.ACCOUNT WHERE ID = :id", nativeQuery = true)')
        result = reply.extract(service_block(BODY) + native, context())
        self.assertEqual(result.problems, [])
        self.assertTrue(any("nativeQuery = true" in w for w in result.warnings), result.warnings)
        manager = REPO_BLOCK.replace('@Query("SELECT d FROM AppAccountDomain d WHERE d.id = :id")\n'
                                     'AppAccountDomain loadName(@Param("id") java.math.BigDecimal id);',
                                     'public AppAccountDomain loadName(java.math.BigDecimal id) {\n'
                                     '    return entityManager.find(AppAccountDomain.class, id);\n}')
        result = reply.extract(service_block(BODY) + manager, context())
        self.assertTrue(any("EntityManagert használ" in p.hu for p in result.problems))
        self.assertIn("No EntityManager", reply.correction(result, context()))

    def test_placeholder_needs_a_missing_list_and_then_stays_a_draft(self):
        body = "throw new UnsupportedOperationException(\"PKG.CALC hiányzik\");"
        result = reply.extract(service_block(body), context())
        self.assertTrue(any("helyőrző" in p.hu for p in result.problems))
        result = reply.extract(service_block(body) + "\nMISSING:\n- A PKG.CALC függvény forrása\n", context())
        self.assertEqual(result.problems, [])
        self.assertEqual(result.response["status"], "needs_context")
        self.assertEqual(result.response["missing_context"], ["A PKG.CALC függvény forrása"])

    def test_nothing_missing_phrases_do_not_block_the_proposal(self):
        for phrase in ("MISSING: none", "MISSING:\n- Nincs hiányzó adat.", "**MISSING:** N/A", "MISSING: Nothing is missing.",
                       "Hiányzó adatok: nincs"):
            with self.subTest(phrase=phrase):
                result = reply.extract(service_block(BODY) + REPO_BLOCK + "\n" + phrase + "\n", context())
                self.assertEqual(result.problems, [])
                self.assertEqual(result.response["status"], "proposal")

    def test_unbalanced_or_empty_answers_are_reported(self):
        result = reply.extract("```java\n// SERVICE\nif (x) {\n", context(), truncated=True)
        self.assertTrue(result.truncated)
        self.assertIn("zárójelei nem zárnak", result.problems[0].hu)
        self.assertIn("üres", reply.extract("<think>csak gondolkodás", context()).problems[0].hu)
        self.assertIn("SERVICE", reply.extract("Sajnos nem tudom megoldani.", context()).problems[0].hu)

    def test_entity_block_is_skipped_and_imports_are_normalized(self):
        text = ("```java\n// AppAccountDomain.java\n@Entity\npublic class AppAccountDomain { private String name; }\n```\n"
                + service_block(BODY) + REPO_BLOCK.replace("// REPOSITORY AppAccountRepository\n",
                "// REPOSITORY AppAccountRepository\nimport javax.persistence.LockModeType;\nimport com.example.Unknown;\n"
                "import java.util.List;\n"))
        result = reply.extract(text, context())
        self.assertEqual(result.problems, [])
        self.assertEqual(result.blocks[0]["kind"], "entity")
        self.assertEqual(result.response["repositories"][0]["imports"], [])  # imports only for names the code uses
        uses = REPO_BLOCK.replace('@Query("SELECT d FROM AppAccountDomain d WHERE d.id = :id")',
                                  '@Lock(LockModeType.PESSIMISTIC_WRITE)\n@Query("SELECT d FROM AppAccountDomain d WHERE d.id = :id")')
        uses = uses.replace("// REPOSITORY AppAccountRepository\n", "// REPOSITORY AppAccountRepository\n"
                            "import javax.persistence.LockModeType;\n")
        result = reply.extract(service_block(BODY) + uses, context())
        # the project's persistence namespace (jakarta here), whatever the model wrote
        self.assertEqual(result.response["repositories"][0]["imports"], ["jakarta.persistence.LockModeType"])


class ScanTests(unittest.TestCase):
    def test_members_statements_and_continuations(self):
        source = ("@PersistenceContext private EntityManager em;\nprivate static final String X = \"{\";\n"
                  "public <T> java.util.List<T> all(Class<T> type) { return null; }\n"
                  "public Repo(EntityManager em) { this.em = em; }\n")
        kinds = [(m.kind, m.name) for m in scan_members(source, java_mask(source))]
        self.assertEqual(kinds, [("field", "em"), ("field", "X"), ("method", "all"), ("constructor", "Repo")])
        statements = "if (a) { b(); } else if (c) { d(); } else { e(); }\ntry { f(); } catch (Exception x) { g(); }\nreturn h;"
        self.assertEqual([m.kind for m in scan_members(statements, java_mask(statements))], ["statement"] * 3)


if __name__ == "__main__":
    unittest.main()

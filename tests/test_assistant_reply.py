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

import javax.persistence.EntityManager;
import javax.persistence.PersistenceContext;
import org.springframework.stereotype.Repository;

// Forms migrator JPA repository
@Repository
public class LookupAppAccountRepository {
    @PersistenceContext
    private EntityManager entityManager;

    public LookupAppAccountEntity findAccount(java.math.BigDecimal id) {
        return entityManager.find(LookupAppAccountEntity.class, id);
    }
}
"""


def context(**changes):
    values = dict(method="onCtrlRun", service=SERVICE, module_class="Lookup", namespace="jakarta",
                  repositories=[reply.Repository("LookupAppAccountRepository", "lookupAppAccountRepository",
                                                 "LookupAppAccountEntity", REPOSITORY)])
    values.update(changes)
    return reply.Context(**values)


def service_block(body, label="// SERVICE"):
    return ("```java\n" + label + "\npublic ActionResult onCtrlRun(UserDto user, ActionRequest request) throws Exception {\n"
            "    return log1x(log, LookupConstants.ON_CTRL_RUN_NAME, user, null, () -> {\n"
            + "\n".join("        " + line for line in body.strip().split("\n")) + "\n    });\n}\n```\n")


BODY = """java.math.BigDecimal id = PlsqlValues.number(request.blocks(), "CTRL", "ID");
LookupAppAccountEntity row = lookupAppAccountRepository.loadName(id);
if (row == null) {
    throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "NO_DATA_FOUND");
}
Map<String, Map<String, String>> blocks = new LinkedHashMap<>();
PlsqlValues.put(blocks, "CTRL", "NAME", row.getName());
return new ActionResult(blocks, List.of(), List.of(), Map.of());"""

REPO_BLOCK = """```java
// REPOSITORY LookupAppAccountRepository
public LookupAppAccountEntity loadName(java.math.BigDecimal id) {
    return entityManager.find(LookupAppAccountEntity.class, id);
}
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
        self.assertEqual(response["repositories"][0]["name"], "LookupAppAccountRepository")
        self.assertIn("public LookupAppAccountEntity loadName", response["repositories"][0]["methods"])
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
    private LookupAppAccountRepository lookupAppAccountRepository;

    @Override
    public ActionResult onCtrlRun(UserDto user, ActionRequest request) throws Exception {
        return log1x(log, LookupConstants.ON_CTRL_RUN_NAME, user, null, () -> {
            LookupAppAccountEntity row = lookupAppAccountRepository.loadName(null);
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
import javax.persistence.EntityManager;
@Repository
public class LookupAppAccountRepository {
    private final EntityManager em;

    public LookupAppAccountRepository(EntityManager em) {
        this.em = em;
    }

    public LookupAppAccountEntity loadName(java.math.BigDecimal id) {
        return em.find(LookupAppAccountEntity.class, id);
    }
}
```"""
        result = reply.extract(text, context())
        self.assertEqual(result.problems, [])
        self.assertIn("lookupAppAccountRepository.loadName(null)", result.response["body"])
        self.assertEqual(result.response["private_methods"], "")  # the other endpoint is never taken over
        methods = result.response["repositories"][0]["methods"]
        self.assertIn("return entityManager.find(LookupAppAccountEntity.class, id);", methods)
        self.assertNotIn("this.em", methods)
        self.assertNotIn("LookupAppAccountRepository(", methods)
        self.assertTrue(any("onCtrlOther" in note for note in result.notes))

    def test_markdown_headings_bare_body_and_unterminated_last_block(self):
        text = ("### SERVICE\n```java\n" + BODY + "\n```\n\n**LookupAppAccountRepository.java**\n```java\n"
                "public LookupAppAccountEntity loadName(java.math.BigDecimal id) {\n"
                "    return entityManager.find(LookupAppAccountEntity.class, id);\n}\n")
        result = reply.extract(text, context())
        self.assertEqual(result.problems, [])
        self.assertTrue(result.truncated)
        self.assertEqual(result.blocks[1]["target"], "LookupAppAccountRepository")
        self.assertIn("lookupAppAccountRepository.loadName(id)", result.response["body"])
        self.assertTrue(any("nincs lezárva" in warning for warning in result.warnings))

    def test_plain_java_without_fences_and_thinking_text(self):
        text = "<think>Először a SELECT-et nézem.</think>\n" + BODY + "\n\nNOTES:\n- egyszerű lekérdezés\n"
        result = reply.extract(text, context(repositories=[reply.Repository(
            "LookupAppAccountRepository", "lookupAppAccountRepository", "LookupAppAccountEntity",
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
                  "test_cases": [], "dtos": [], "repositories": [{"name": "LookupAppAccountRepository", "imports": [],
                  "methods": "public LookupAppAccountEntity loadName(java.math.BigDecimal id) { return entityManager.find(LookupAppAccountEntity.class, id); }"}]}
        result = reply.extract(json.dumps(legacy), context())
        self.assertEqual(result.problems, [])
        self.assertEqual(result.format, "json")
        self.assertEqual(result.response["status"], "needs_context")
        self.assertEqual(result.response["missing_context"], ["PKG_ACCOUNT.CHECK_LIMIT forrása"])
        self.assertEqual(len(result.response["repositories"]), 1)
        self.assertTrue(any("PKG_ACCOUNT.CHECK_LIMIT" in warning for warning in result.warnings))

    def test_spring_data_and_unknown_repository_become_precise_corrections(self):
        text = ("```java\n// SERVICE\nvar row = accountRepository.findById(id).orElse(null);\n"
                "return new ActionResult(Map.of(), List.of(), List.of(), Map.of());\n```\n"
                "```java\n// REPOSITORY AccountRepository\npublic interface AccountRepository extends "
                "JpaRepository<LookupAppAccountEntity, BigDecimal> {}\n```")
        result = reply.extract(text, context())
        self.assertEqual(len(result.problems), 2)
        correction = reply.correction(result, context())
        self.assertIn("Do not write a Spring Data interface", correction)
        self.assertIn("`accountRepository` does not exist. Use only these repository fields: lookupAppAccountRepository.", correction)
        self.assertIn("// REPOSITORY <Name>", correction)

    def test_unicode_escapes_in_strings_become_characters_elsewhere_a_correction(self):
        body = 'throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "Hi\\u00e1nyz\\u00f3 \\"k\\u00f3d\\"");'
        result = reply.extract(service_block(body), context())
        self.assertEqual(result.problems, [])
        self.assertIn('"Hiányzó \\"kód\\""', result.response["body"])
        hidden = "String a = \"x\"; \\u0022; return null;"
        self.assertTrue(any("unicode-escape" in p.hu for p in reply.extract(service_block(hidden), context()).problems))

    def test_getter_style_request_access_is_corrected(self):
        result = reply.extract(service_block("String id = request.getBlocks().get(\"CTRL\").get(\"ID\");\n"
                                             "return new ActionResult(Map.of(), List.of(id), List.of(), Map.of());"), context())
        self.assertEqual(len(result.problems), 1)
        self.assertIn("use request.blocks()", result.problems[0].en)

    def test_called_repository_method_must_be_written(self):
        result = reply.extract(service_block(BODY), context())
        self.assertEqual(len(result.problems), 1)
        self.assertIn("lookupAppAccountRepository.loadName", result.problems[0].hu)
        self.assertIn("does not define it", result.problems[0].en)

    def test_existing_repository_method_is_reused_or_renamed_with_the_call(self):
        same = REPO_BLOCK.replace("loadName", "findAccount")
        result = reply.extract(service_block(BODY.replace("loadName", "findAccount")) + same, context())
        self.assertEqual(result.problems, [])
        self.assertEqual(result.response["repositories"], [])  # identical: the existing method is used
        different = same.replace("return entityManager.find(LookupAppAccountEntity.class, id);",
                                 "return id == null ? null : entityManager.find(LookupAppAccountEntity.class, id);")
        result = reply.extract(service_block(BODY.replace("loadName", "findAccount")) + different, context())
        self.assertEqual(result.problems, [])
        self.assertIn("public LookupAppAccountEntity onCtrlRunFindAccount(", result.response["repositories"][0]["methods"])
        self.assertIn("lookupAppAccountRepository.onCtrlRunFindAccount(id)", result.response["body"])

    def test_service_helper_named_like_an_existing_method_is_renamed(self):
        text = service_block("return new ActionResult(Map.of(), List.of(normalize(\"x\")), List.of(), Map.of());").replace(
            "```\n", "\nprivate String normalize(String v) {\n    return v.trim();\n}\n```\n", 1)
        result = reply.extract(text, context())
        self.assertEqual(result.problems, [])
        self.assertIn("private String onCtrlRunNormalize(String v)", result.response["private_methods"])
        self.assertIn("onCtrlRunNormalize(\"x\")", result.response["body"])

    def test_jpql_is_a_warning_native_sql_is_a_problem_and_sequences_are_allowed(self):
        jpql = REPO_BLOCK.replace("return entityManager.find(LookupAppAccountEntity.class, id);",
                                  "return entityManager.createQuery(\"select a from LookupAppAccountEntity a where a.id = :id\", "
                                  "LookupAppAccountEntity.class).setParameter(\"id\", id).getSingleResult();")
        result = reply.extract(service_block(BODY) + jpql, context())
        self.assertEqual(result.problems, [])
        self.assertTrue(any("JPQL" in warning for warning in result.warnings))
        native = jpql.replace("createQuery(\"select a from LookupAppAccountEntity a where a.id = :id\", LookupAppAccountEntity.class)",
                              "createNativeQuery(\"SELECT * FROM APP.ACCOUNT WHERE ID = ?\", LookupAppAccountEntity.class)")
        self.assertTrue(any("natív SQL" in p.hu for p in reply.extract(service_block(BODY) + native, context()).problems))
        sequence = REPO_BLOCK.replace("return entityManager.find(LookupAppAccountEntity.class, id);",
                                      "return entityManager.find(LookupAppAccountEntity.class, (java.math.BigDecimal) "
                                      "entityManager.createNativeQuery(\"SELECT APP.ACCOUNT_SEQ.NEXTVAL FROM DUAL\").getSingleResult());")
        self.assertEqual(reply.extract(service_block(BODY) + sequence, context()).problems, [])

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
        text = ("```java\n// LookupAppAccountEntity.java\n@Entity\npublic class LookupAppAccountEntity { private String name; }\n```\n"
                + service_block(BODY) + REPO_BLOCK.replace("// REPOSITORY LookupAppAccountRepository\n",
                "// REPOSITORY LookupAppAccountRepository\nimport javax.persistence.criteria.CriteriaBuilder;\nimport com.example.Unknown;\n"
                "import java.util.List;\n"))
        result = reply.extract(text, context())
        self.assertEqual(result.problems, [])
        self.assertEqual(result.blocks[0]["kind"], "entity")
        self.assertEqual(result.response["repositories"][0]["imports"], [])  # imports only for names the code uses
        uses = REPO_BLOCK.replace("return entityManager.find(LookupAppAccountEntity.class, id);",
                                  "CriteriaBuilder cb = entityManager.getCriteriaBuilder();\n    return entityManager.find(LookupAppAccountEntity.class, id);")
        uses = uses.replace("// REPOSITORY LookupAppAccountRepository\n", "// REPOSITORY LookupAppAccountRepository\n"
                            "import javax.persistence.criteria.CriteriaBuilder;\n")
        result = reply.extract(service_block(BODY) + uses, context())
        self.assertEqual(result.response["repositories"][0]["imports"], ["jakarta.persistence.criteria.CriteriaBuilder"])


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

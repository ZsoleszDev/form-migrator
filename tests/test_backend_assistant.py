"""Offline end-to-end checks: model response -> diff -> reviewed Java -> javac."""
import contextlib
import copy
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
from unittest.mock import patch
import xml.etree.ElementTree as ET

from frm_forms import backend_assistant as assistant
from frm_forms import assistant_context, assistant_log
from frm_forms.cli import main
from frm_forms.common import MigrationError, read_json, write_json
from java_support import COMPANY_IMPORTS, trigger_mode, write_stubs
from test_manual_action_source import FORM

setUpModule, tearDownModule = trigger_mode('plsql')


def answer(**changes):
    result = {"status": "proposal", "summary": "A kiválasztott művelet Java-változata.",
              "body": 'return new ActionResult(java.util.Map.of(), java.util.List.of("minta"), java.util.List.of(), java.util.Map.of());',
              "imports": [], "private_methods": "", "assumptions": [], "missing_context": [],
              "test_cases": ["Üres kérésmezők mellett a minta üzenet érkezik."]}
    result.update(changes)
    return result


class Client:
    """Ollama double: a dict is a legacy JSON answer, a str is the model's raw (Markdown) text,
    a tuple (text, envelope) also sets fields like done_reason or token counts."""
    def __init__(self, *responses): self.responses, self.requests, self.urls = list(responses), [], []
    def open(self, request, timeout):
        self.requests.append(json.loads(request.data))
        self.urls.append(request.full_url)
        value = self.responses.pop(0)
        if isinstance(value, Exception): raise value
        value, envelope = value if isinstance(value, tuple) else (value, {})
        content = value if isinstance(value, str) else json.dumps(value)
        return io.BytesIO(json.dumps({"done": True, "done_reason": "stop", "message": {"role": "assistant", "content": content},
                                      **envelope}).encode())


MARKDOWN = '''Íme az átírás.

```java
// SERVICE
public ActionResult onCgnvW011PbLekerdezes(UserDto user, ActionRequest request) throws Exception {
    return log1x(log, KeziConstants.ON_CGNV_W011_PB_LEKERDEZES_NAME, user, null, () -> {
        //region Eredeti Forms-kód kiindulásnak (az AI másolata)
        // IF get_item_property(...) ...
        //endregion
        List<List<String>> commands = new ArrayList<>();
        String kod = PlsqlValues.text(request.blocks(), "V_ELEK_ASD", "UBI_ASD_KOD");
        commands.add(List.of("SET_BLOCK_PROPERTY", "V_ELEK_ASD", "DEFAULT_WHERE", where(kod)));
        commands.add(List.of("GO_BLOCK", "V_ELEK_ASD"));
        commands.add(List.of("EXECUTE_QUERY"));
        return new ActionResult(new HashMap<>(), List.of(), commands, Map.of());
    });
}

private String where(String kod) {
    return kod == null ? "1=1" : "1=1 AND kod = " + kod;
}
```

NOTES:
- A get_item_property(ENABLED) feltételt a képernyő kezeli.
'''


class BackendAssistantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / 'form.xml'
        source.write_text(FORM, encoding='utf-8')
        config = self.root / 'config.json'
        config.write_text(json.dumps({'java_company_imports': COMPANY_IMPORTS}), encoding='utf-8')
        self.output = self.root / 'out'
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(['migrate', str(source), '--screen', '--module', 'kezi', '--config', str(config), '--out', str(self.output), '--zip'])
        self.assertEqual(code, 0)
        self.index = read_json(self.output / assistant.AREA / 'index.json')
        self.task = next(t for t in self.index['tasks'] if t['method'] == 'onCgnvW011PbLekerdezes')
        self.service = self.output / self.task['target']
        self.options = assistant.Options(num_ctx=32768)

    def propose(self, response=None, options=None):
        client = Client(response or answer())
        with patch('frm_forms.ollama.opener', return_value=client):
            result = assistant.propose(self.output, self.task['id'], options or self.options)
        return result, client

    def test_no_network_during_generation_and_complete_local_chain(self):
        packet = read_json(self.output / assistant.AREA / 'tasks' / (self.task['id'] + '.json'))
        sources = '\n'.join(s['source'] for s in packet['sources'])
        self.assertIn('PROCEDURE lekerdezesi_feltetelek', sources)
        self.assertIn('get_item_property', sources)
        self.assertIn('ActionRequest', str(packet['contracts']))
        self.assertIn('HttpStatus.NOT_IMPLEMENTED', self.service.read_text())
        self.assertTrue((self.output / 'JAVA_ASSISTANT.md').is_file())
        first = self.index
        self.assertEqual(first, assistant.prepare_workspace(self.output))

    def test_complete_migration_task_is_supplied_without_user_prompt(self):
        _, client = self.propose()
        system, user = (m['content'] for m in client.requests[0]['messages'])
        self.assertEqual(system, assistant_context.SYSTEM)
        self.assertNotIn('format', client.requests[0])  # free Markdown answer, no JSON-escaped code
        self.assertIn(assistant_context.rules(has_tables=False, plsql_values=True), user)
        self.assertIn('FROM DUAL needs no table', user)
        self.assertIn('// SERVICE', user)
        self.assertNotIn('Additional requirements', user)
        self.assertIn(assistant_context.rules(), self.index['default_instructions'])

    def test_optional_cli_requirements_extend_the_automatic_task(self):
        self.options.instructions = 'Használd a megadott céges API-t.'
        _, client = self.propose(options=self.options)
        user = client.requests[0]['messages'][1]['content']
        self.assertIn('Additional requirements from the developer:\n' + self.options.instructions, user)

    def test_large_module_inventory_does_not_block_or_enter_the_ai_request(self):
        # Reproduce the former 160 KB rejection using real, irrelevant module data.
        ir_path = self.output / 'analysis/form.ir.json'
        ir = read_json(ir_path)
        ir['blocks'][0]['items'][0]['property_provenance'] = {
            'irrelevant': 'XML_INVENTORY_ONLY_' * 20000}
        write_json(ir_path, ir)
        dto = self.output / 'backend/CL/KeziDtos.java'
        dto.write_text(dto.read_text() + '\n/* ' + 'DTO_INVENTORY_ONLY_' * 10000 + ' */\n')
        # A user's latest edit to the commented trigger must be sent verbatim.
        source = self.service.read_text()
        _, opening, _ = assistant.method_span(source, self.task['method'])
        source = source[:opening + 1] + '\n/* CURRENT_TRIGGER_SOURCE_árvíz */' + source[opening + 1:]
        self.service.write_text(source)
        start, _, end = assistant.method_span(source, self.task['method'])
        result, client = self.propose(answer(status='needs_context', body='',
            missing_context=['Az üzleti rutin forrása szükséges.'], test_cases=[]))
        self.assertEqual(len(client.requests), 1)
        sent = client.requests[0]['messages'][1]['content']
        # The selected method verbatim, in a Java block; nothing else from the module.
        self.assertIn('```java\n' + source[start:end + 1] + '\n```', sent)
        self.assertNotIn('Repositories:', sent)
        self.assertNotIn('XML_INVENTORY_ONLY_', sent)
        self.assertNotIn('DTO_INVENTORY_ONLY_', sent)
        self.assertNotIn('onVElekAsdUbiAsdKod2', sent)  # the other endpoint of the same service
        self.assertGreater(result['context']['original_bytes'], 500000)
        self.assertLess(result['context']['sent_bytes'], 20000)
        self.assertEqual(result['status'], 'needs_context')
        self.assertEqual(result['response']['missing_context'], ['Az üzleti rutin forrása szükséges.'])

    def test_complete_large_trigger_gets_an_actionable_context_limit(self):
        source = self.service.read_text()
        _, opening, _ = assistant.method_span(source, self.task['method'])
        source = source[:opening + 1] + '\n/* ' + 'full trigger source ' * 6000 + ' */' + source[opening + 1:]
        self.service.write_text(source)
        with patch('frm_forms.ollama.opener', side_effect=AssertionError('no truncated input')):
            with self.assertRaisesRegex(MigrationError, r'num_ctx 32768.*legalább \d+'):
                assistant.propose(self.output, self.task['id'], self.options)
        packet = read_json(self.output / assistant.AREA / 'tasks' / (self.task['id'] + '.json'))
        self.assertEqual(packet['existing_method'].count('full trigger source '), 6000)

    def test_proposal_is_separate_then_applies_and_preserves_logging_contract(self):
        before = self.service.read_bytes()
        result, client = self.propose()
        self.assertEqual(self.service.read_bytes(), before)
        self.assertEqual(result['validation']['java_compile'], 'not_run')
        self.assertEqual(client.requests[0]['options']['temperature'], 0)
        self.assertIn('lekerdezesi_feltetelek', client.requests[0]['messages'][1]['content'])
        with self.assertRaises(MigrationError): assistant.apply_proposal(self.output, result['id'])
        applied = assistant.apply_proposal(self.output, result['id'], reviewed=True)
        self.assertTrue(applied['applied'])
        service = self.service.read_text()
        self.assertIn('List.of("minta")', service)
        self.assertIn('import java.util.List;', service)
        self.assertIn('return log1x(', service)
        self.assertIn('if (!MODULE_REVIEWED)', service)
        self.assertIn('@Transactional', service)
        self.assertIn('import org.springframework.transaction.annotation.Transactional;', service)
        # Another button, controller and DTOs are not changed by this proposal.
        self.assertIn('onVElekAsdUbiAsdKod2', service)
        backup = self.output / assistant.AREA / 'proposals' / result['id'] / 'originals' / (self.task['target'] + '.txt')
        self.assertEqual(backup.read_bytes(), before)
        with self.assertRaises(MigrationError): assistant.apply_proposal(self.output, result['id'], reviewed=True)
        assistant.refresh_archives(self.output)
        import zipfile
        with zipfile.ZipFile(self.output.with_suffix('.zip')) as z:
            self.assertEqual(z.read('out/' + self.task['target']), self.service.read_bytes())

    def test_unknown_context_never_generates_partial_success(self):
        missing = answer(status='needs_context', body='', missing_context=['A ROGZITO eljárás teljes törzse szükséges.'], test_cases=[])
        before = self.service.read_bytes()
        result, _ = self.propose(missing)
        self.assertEqual(result['files'], [])
        self.assertEqual(self.service.read_bytes(), before)
        with self.assertRaises(MigrationError): assistant.apply_proposal(self.output, result['id'], reviewed=True)

    def test_changed_target_or_dto_prevents_apply(self):
        result, _ = self.propose()
        dto = self.output / 'backend/CL/KeziDtos.java'
        dto.write_text(dto.read_text() + '\n// contract changed\n')
        before = self.service.read_bytes()
        with self.assertRaisesRegex(MigrationError, 'megváltozott'):
            assistant.apply_proposal(self.output, result['id'], reviewed=True)
        self.assertEqual(self.service.read_bytes(), before)

    def test_tampered_candidate_and_path_escape_are_rejected(self):
        result, _ = self.propose()
        folder = self.output / assistant.AREA / 'proposals' / result['id']
        file = folder / result['files'][0]['candidate']
        file.write_text(file.read_text() + '\n// edited\n')
        with self.assertRaises(MigrationError): assistant.apply_proposal(self.output, result['id'], reviewed=True)
        for path in ('../escape', '/tmp/escape', 'C:\\escape', 'a/../../escape'):
            with self.assertRaises(MigrationError): assistant.safe_file(self.output, path)

    def test_cache_exact_context_and_offline_mode(self):
        first, _ = self.propose()
        options = copy.copy(self.options); options.cached_only = True
        with patch('frm_forms.ollama.opener', side_effect=AssertionError('offline network')):
            cached = assistant.propose(self.output, self.task['id'], options)
        self.assertTrue(cached['cached']); self.assertEqual(cached['calls'], 0)
        options.instructions = 'Más feladat.'
        with self.assertRaisesRegex(MigrationError, 'cache'):
            assistant.propose(self.output, self.task['id'], options)

    def test_bad_json_structure_gets_one_bounded_correction(self):
        client = Client(answer(body='} return null; {'), answer())
        with patch('frm_forms.ollama.opener', return_value=client):
            result = assistant.propose(self.output, self.task['id'], self.options)
        self.assertEqual(result['calls'], 2)
        # The correction carries the rejected answer itself, so the model can fix it.
        retry = client.requests[1]['messages']
        self.assertEqual([m['role'] for m in retry], ['system', 'user', 'assistant', 'user'])
        self.assertEqual(json.loads(retry[2]['content'])['body'], '} return null; {')
        self.assertIn('Your previous answer could not be used', retry[3]['content'])
        self.assertEqual(len(result['format_errors']), 1)

    def test_network_error_has_no_automatic_retry(self):
        client = Client(TimeoutError('offline'))
        with patch('frm_forms.ollama.opener', return_value=client), self.assertRaises(MigrationError):
            assistant.propose(self.output, self.task['id'], self.options)
        self.assertEqual(len(client.requests), 1)

    def test_ollama_http_error_keeps_the_model_server_reason_without_retry(self):
        for body, expected in ((b'{"error":"model qwen-test not found"}', 'model qwen-test not found'),
                               (b'<html>proxy response</html>', 'Not Found')):
            with self.subTest(body=body):
                failure = urllib.error.HTTPError('http://localhost:11434/api/chat', 404, 'Not Found', None, io.BytesIO(body))
                client = Client(failure)
                with patch('frm_forms.ollama.opener', return_value=client), self.assertRaises(MigrationError) as raised:
                    assistant.propose(self.output, self.task['id'], self.options)
                self.assertIn('HTTP 404', str(raised.exception))
                self.assertIn(expected, str(raised.exception))
                self.assertIn('POST http://localhost:11434/api/chat', str(raised.exception))
                self.assertEqual(len(client.requests), 1)

    def test_pasted_chat_endpoint_is_not_appended_twice(self):
        self.options.url = 'http://localhost:11434/api/chat'
        _, client = self.propose()
        self.assertEqual(client.urls, ['http://localhost:11434/api/chat'])

    def test_small_context_rejects_whole_packet_before_network(self):
        options = copy.copy(self.options); options.num_ctx = 4096
        with patch('frm_forms.ollama.opener', side_effect=AssertionError('truncated prompt')), self.assertRaises(MigrationError):
            assistant.propose(self.output, self.task['id'], options)

    def test_structural_rejections_and_private_helpers(self):
        for change in ({'body': 'Runtime.getRuntime().exec("x"); return null;'}, {'body': '// TODO\nreturn null;'},
                       {'private_methods': 'private String field;'}, {'body': 'return null;\\u007d'},
                       {'imports': ['java.io.File']}, {'missing_context': ['unknown signature']}):
            with self.subTest(change=change), self.assertRaises(MigrationError): assistant.validate_response(answer(**change))
        self.assertEqual(assistant.validate_response(answer(private_methods='private String normalize(String value) { return value == null ? "" : value; }'))['status'], 'proposal')

    def test_jdk_parser_rejects_broken_java_without_running_the_candidate(self):
        if assistant.java_parse('class Good {}', 'Good.java') == 'not_available':
            self.skipTest('JDK parser nem elérhető')
        with self.assertRaisesRegex(MigrationError, 'szintaktikai'):
            assistant.java_parse('class Broken { void broken() { int value = ; } }', 'Broken.java')

    def test_jdbc_helpers_are_published_and_applied_with_the_endpoint(self):
        effective_path = self.output / 'analysis/effective-config.json'
        effective = read_json(effective_path)
        effective['metadata']['tables'] = {'APP.MASTER': {'primary_key': ['ID'], 'columns': {
            'ID': {'type': 'number', 'data_type': 'NUMBER', 'nullable': False},
            'NAME': {'type': 'text', 'data_type': 'VARCHAR2', 'nullable': True}}}}
        write_json(effective_path, effective)
        ir_path = self.output / 'analysis/form.ir.json'
        ir = read_json(ir_path)
        # Associate a verified dictionary source with an existing relevant block.
        ir['blocks'][0].update(database=True, table='APP.MASTER')
        write_json(ir_path, ir)
        options = copy.copy(self.options); options.data_access = 'jdbc'
        result, client = self.propose(options=options)
        self.assertEqual(len(result['files']), 3)
        self.assertIn('KeziAppMasterRepository', client.requests[0]['messages'][1]['content'])
        applied = assistant.apply_proposal(self.output, result['id'], reviewed=True)
        self.assertEqual(len(applied['files']), 3)
        self.assertTrue((self.output / 'backend/DPS/KeziAppMasterEntity.java').is_file())
        self.assertIn('findByKey', (self.output / 'backend/DPS/KeziAppMasterRepository.java').read_text())

    def test_java_helper_name_collision_is_rejected(self):
        packet = read_json(self.output / assistant.AREA / 'tasks' / (self.task['id'] + '.json'))
        service = self.service.read_text()
        response = answer(private_methods='private String onCgnvW011PbLekerdezes(String text) { return text; }')
        with self.assertRaisesRegex(MigrationError, 'ütközik'):
            assistant.render_candidate(service, packet, response)

    def test_regeneration_keeps_applied_code_proposal_and_original_backup(self):
        result, _ = self.propose()
        assistant.apply_proposal(self.output, result['id'], reviewed=True)
        before = self.service.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            status = main(['migrate',str(self.root/'form.xml'),'--screen','--module','kezi',
                           '--config',str(self.root/'config.json'),'--out',str(self.output),'--regenerate','--zip'])
        self.assertEqual(status,0)
        self.assertEqual(self.service.read_bytes(),before)
        record = read_json(self.output/assistant.AREA/'proposals'/result['id']/'proposal.json')
        self.assertTrue(record['applied'])
        self.assertTrue((self.output/assistant.AREA/'proposals'/result['id']/'originals'/(self.task['target']+'.txt')).is_file())

    def test_jdbc_helpers_need_verified_dictionary_and_handle_composite_keys(self):
        packet = {'module': 'sample', 'blocks': [{'name': 'B', 'table': 'APP.MASTER', 'database': True}],
                  'schema': {'tables': {'APP.MASTER': {'primary_key': ['ID', 'TENANT'], 'columns': {
                      'ID': {'type': 'number', 'data_type': 'NUMBER'}, 'TENANT': {'type': 'text', 'data_type': 'VARCHAR2'},
                      'CREATED': {'type': 'datetime', 'data_type': 'DATE'}}}}},
                  'service_package_source': 'package hu.sample.dps;\n'}
        files, notes = assistant.data_models(packet)
        self.assertEqual(len(files), 2); self.assertEqual(notes, [])
        repo = next(text for path, text in files.items() if path.endswith('Repository.java'))
        self.assertIn('WHERE ID = :id AND TENANT = :tenant', repo)
        self.assertIn('java.util.Objects.requireNonNull(key.tenant', repo)
        self.assertIn('rs.getTimestamp("CREATED")', repo)
        self.assertNotIn('INSERT', repo)
        packet['schema']['tables']['APP.MASTER']['primary_key'] = []
        self.assertEqual(assistant.data_models(packet)[0], {})
        packet['schema'] = {}
        self.assertEqual(assistant.data_models(packet)[0], {})

    def test_reviewed_service_and_optional_repository_compile_as_java_11(self):
        if not shutil.which('java'): self.skipTest('JDK szükséges')
        result, _ = self.propose(answer(private_methods='private String normalized(String text) { return text == null ? "" : text; }'))
        assistant.apply_proposal(self.output, result['id'], reviewed=True)
        packet = {'module': 'sample', 'blocks': [{'name': 'B', 'table': 'APP.MASTER', 'database': True}],
                  'schema': {'tables': {'APP.MASTER': {'primary_key': ['ID'], 'columns': {
                      'ID': {'type': 'number', 'data_type': 'NUMBER'}, 'CREATED': {'type': 'datetime', 'data_type': 'DATE'}}}}},
                  'service_package_source': 'package hu.sample.dps;\n'}
        helpers, _ = assistant.data_models(packet)
        helper_paths = []
        for relative, text in helpers.items():
            path = self.root / 'helpers' / Path(relative).name
            path.parent.mkdir(exist_ok=True); path.write_text(text, encoding='utf-8'); helper_paths.append(path)
        sources = list((self.output / 'backend').rglob('*.java')) + write_stubs(self.root / 'stubs') + helper_paths
        argsfile = self.root / 'sources.args'
        argsfile.write_text('\n'.join('"' + str(p) + '"' for p in sources))
        compiled = subprocess.run(['java', 'com.sun.tools.javac.Main', '--release', '11', '-encoding', 'UTF-8',
                                   '-d', str(self.root / 'classes'), '@' + str(argsfile)], capture_output=True, text=True, timeout=60)
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        smoke = self.root / 'AssistantSmoke.java'
        smoke.write_text('''public class AssistantSmoke {
    public static void main(String[] args) throws Exception {
        var service = new hu.company.features.kezi.dps.KeziServiceImpl();
        var request = new hu.company.features.kezi.cl.KeziDtos.ActionRequest(java.util.Map.of(), java.util.Map.of());
        try {
            service.onCgnvW011PbLekerdezes(new hu.company.common.UserDto(), request);
            throw new AssertionError("The original module gate was bypassed");
        } catch (org.springframework.web.server.ResponseStatusException expected) {
            System.out.println("module gate preserved");
        }
    }
}''', encoding='utf-8')
        classes = str(self.root / 'classes')
        compiled = subprocess.run(['java', 'com.sun.tools.javac.Main', '--release', '11', '-cp', classes,
                                   '-d', classes, str(smoke)], capture_output=True, text=True, timeout=30)
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        ran = subprocess.run(['java', '-cp', classes, 'AssistantSmoke'], capture_output=True, text=True, timeout=15)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertIn('module gate preserved', ran.stdout)

    # --- 4.34: Markdown answers, AI log, visible failures ---------------------------------------------------

    def test_markdown_answer_is_logged_and_inserted_into_the_existing_method(self):
        result, client = self.propose((MARKDOWN, {'prompt_eval_count': 1234, 'eval_count': 321}))
        self.assertEqual(result['status'], 'proposal')
        self.assertEqual(result['context']['prompt_tokens'], 1234)
        log = assistant_log.read(self.output, result['exchange_id'])
        self.assertEqual(log['status'], 'completed')
        self.assertEqual(log['result'], {'proposal_id': result['id'], 'status': 'proposal'})
        sent = client.requests[0]['messages']
        self.assertEqual((log['prompt']['system'], log['prompt']['user']), (sent[0]['content'], sent[1]['content']))
        self.assertEqual(log['attempts'][0]['raw'], MARKDOWN)
        self.assertEqual(log['attempts'][0]['output_tokens'], 321)
        self.assertEqual(log['attempts'][0]['extraction']['status'], 'proposal')
        self.assertEqual([s['id'] for s in assistant_log.summaries(self.output, self.task['id'])], [log['id']])
        # The log is next to the module, never inside the module ZIP.
        self.assertFalse(assistant_log.folder(self.output).is_relative_to(self.output))
        folder = self.output / assistant.AREA / 'proposals' / result['id']
        text = (folder / 'files' / (self.task['target'] + '.txt')).read_text()
        start, _, end = assistant.method_span(text, self.task['method'])
        method = text[start:end + 1]
        self.assertEqual(method.count('//region'), 1)
        self.assertIn('lek_sql := lek_sql', method)  # the generator's original comment, not the AI's copy
        self.assertNotIn('az AI másolata', method)
        self.assertIn('commands.add(List.of("EXECUTE_QUERY"));', method)
        self.assertIn('private String where(String kod)', text)
        self.assertIn('import java.util.ArrayList;', text)
        self.assertIn('import java.util.HashMap;', text)  # not in the generator's list, added for model code
        self.assertRegex(text, r'import [\w.]+\.CommonMigrateTools\.PlsqlValues;')
        self.assertIn('A get_item_property(ENABLED) feltételt a képernyő kezeli.', result['response']['assumptions'])
        assistant.apply_proposal(self.output, result['id'], reviewed=True)

    def test_rejected_answers_stay_visible_and_the_retry_carries_them(self):
        bad = 'Sajnos a trigger túl bonyolult, nem tudom átírni.'
        client = Client(bad, ('```java\n// SERVICE\nif (x) {\n', {'done_reason': 'length'}))
        with patch('frm_forms.ollama.opener', return_value=client), self.assertRaises(MigrationError) as raised:
            assistant.propose(self.output, self.task['id'], assistant.Options(attempts=3))
        message = str(raised.exception)
        self.assertIn('Nem érkezett használható Java-javaslat (2 AI-hívás)', message)
        self.assertIn('num_predict=4096', message)  # truncated: no third, equally truncated call (4.35 default)
        exchange_id = re.search(r'azonosító: ([a-f0-9]{20})', message).group(1)
        log = assistant_log.read(self.output, exchange_id)
        self.assertEqual(log['status'], 'failed')
        self.assertEqual([a['raw'] for a in log['attempts']], [bad, '```java\n// SERVICE\nif (x) {\n'])
        self.assertTrue(log['attempts'][1]['correction'].startswith('Your previous answer could not be used'))
        self.assertEqual(log['attempts'][1]['done_reason'], 'length')
        retry = client.requests[1]['messages']
        self.assertEqual(retry[2], {'role': 'assistant', 'content': bad})
        self.assertEqual(retry[3]['content'], log['attempts'][1]['correction'])
        self.assertEqual(len(client.requests), 2)

    def test_needs_context_draft_is_shown_but_never_applied(self):
        draft = MARKDOWN.replace('NOTES:', 'MISSING:\n- A V_ELEK_ASD blokk lekérdezési feltételének üzleti szabálya\n\nNOTES:')
        result, _ = self.propose(draft)
        self.assertEqual(result['status'], 'needs_context')
        self.assertTrue(result['files'])  # the draft is viewable and downloadable
        self.assertEqual(result['response']['missing_context'], ['A V_ELEK_ASD blokk lekérdezési feltételének üzleti szabálya'])
        with self.assertRaisesRegex(MigrationError, 'nem alkalmazható'):
            assistant.apply_proposal(self.output, result['id'], reviewed=True)

    def test_preview_is_exactly_the_request_and_needs_no_model(self):
        with patch('frm_forms.ollama.opener', side_effect=AssertionError('preview must not call the model')):
            shown = assistant.preview(self.output, self.task['id'], self.options)
        self.assertEqual(shown['warnings'], [])
        self.assertLess(shown['bytes'], 6000)
        self.assertGreater(shown['original_bytes'], shown['bytes'])
        _, client = self.propose()
        self.assertEqual([m['content'] for m in client.requests[0]['messages']], [shown['system'], shown['user']])

    def test_http_failure_is_logged_with_the_prompt(self):
        failure = urllib.error.HTTPError('http://localhost:11434/api/chat', 500, 'Server Error', None,
                                         io.BytesIO(b'{"error":"out of memory"}'))
        with patch('frm_forms.ollama.opener', return_value=Client(failure)), self.assertRaises(MigrationError) as raised:
            assistant.propose(self.output, self.task['id'], self.options)
        self.assertIn('out of memory', str(raised.exception))
        log = assistant_log.read(self.output, re.search(r'azonosító: ([a-f0-9]{20})', str(raised.exception)).group(1))
        self.assertIn('onCgnvW011PbLekerdezes', log['prompt']['user'])
        self.assertIn('out of memory', log['attempts'][0]['error'])
        self.assertIsNone(log['attempts'][0]['raw'])


class QueryButtonTaskTests(unittest.TestCase):
    def test_query_button_with_original_forms_code_is_offered_with_its_table(self):
        # Reported: only finished CRUD methods were offered; the query button with the commented trigger was not.
        from test_query_java import endpoint, form, generate
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        out = generate(Path(temp.name), form())
        method = endpoint(out)['method']
        tasks = assistant.prepare_workspace(out)['tasks']
        self.assertEqual([t['method'] for t in tasks], [method])
        self.assertEqual(tasks[0]['pending_tables'], ['T_BLK'])  # the query's target block table
        service = (out / tasks[0]['target']).read_text()
        start, _, end = assistant.method_span(service, method)
        self.assertIn('//region Eredeti Forms-kód', service[start:end])
        self.assertNotIn('Return new ActionResult', assistant_context.rules(action_api=False))


if __name__ == '__main__': unittest.main()

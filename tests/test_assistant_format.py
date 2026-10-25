"""Formatting is local, isolated and reproducible during proposal application."""
import copy
import unittest
from unittest.mock import patch

from frm_forms import java_style, java_tidy
from frm_forms.assistant_format import format_java
from frm_forms import backend_assistant as assistant
from frm_forms.common import read_json, write_json, MigrationError
import test_backend_assistant as fixture
from test_backend_assistant import answer


class FormatterTests(unittest.TestCase):
    def test_layout_literals_imports_and_isolation(self):
        source = '''package app;
import javax.servlet.http.HttpServletRequest;
import company.Existing;
import java.util.Set;
public class Example {
private HttpServletRequest request;
private Existing existing;
public java.util.List<String> run(boolean ok){if(ok)return java.util.List.of("ő \\n SQL");return java.util.List.of();}
}'''
        before = copy.deepcopy((java_style.SETTINGS, java_style.MEMBER_TYPES, java_style.FAILED,
                                java_tidy.IMPORT_MAP, java_tidy.OMIT_PACKAGE))
        text, report = format_java(source, 'Example.java', {}, {'Existing': 'wrong.Existing'})
        self.assertEqual(report['status'], 'formatted')
        self.assertIn('import javax.servlet.http.HttpServletRequest;', text)
        self.assertIn('import company.Existing;', text)
        self.assertNotIn('wrong.Existing', text)
        self.assertNotIn('import java.util.Set;', text)
        self.assertIn('import java.util.List;', text)
        self.assertIn('if (ok) {', text)
        self.assertIn('"ő \\n SQL"', text)
        self.assertEqual(format_java(text, 'Example.java', {}, {})[0], text)
        self.assertEqual(before, (java_style.SETTINGS, java_style.MEMBER_TYPES, java_style.FAILED,
                                  java_tidy.IMPORT_MAP, java_tidy.OMIT_PACKAGE))


class ProposalFormattingTests(unittest.TestCase):
    setUp = fixture.BackendAssistantTests.setUp
    propose = fixture.BackendAssistantTests.propose

    def test_one_inference_then_formatted_proposal_and_same_apply_bytes(self):
        result, client = self.propose(answer(private_methods='private int answer(){if(true)return 1;return 2;}'))
        self.assertEqual(len(client.requests), 1)
        report = result['validation']['deterministic_format'][self.task['target']]
        self.assertEqual(report['status'], 'formatted')
        draft = self.output / assistant.AREA / 'proposals' / result['id'] / result['files'][0]['candidate']
        proposed = draft.read_bytes()
        self.assertIn(b'if (true) {', proposed)
        with patch('frm_forms.ollama.opener', side_effect=AssertionError('No model during apply')):
            assistant.apply_proposal(self.output, result['id'], reviewed=True)
        self.assertEqual(self.service.read_bytes(), proposed)

    def test_formatter_failure_does_not_make_an_extra_model_request(self):
        def unsupported(text, origin='', **kwargs):
            kwargs['failures'].append({'file': origin, 'reason': 'unsupported construct'})
            return text
        with patch('frm_forms.java_style.layout', side_effect=unsupported):
            result, client = self.propose()
            self.assertEqual(len(client.requests), 1)
            self.assertEqual(result['validation']['deterministic_format'][self.task['target']]['status'], 'manual')
            assistant.apply_proposal(self.output, result['id'], reviewed=True)

    def test_format_settings_are_part_of_proposal_fingerprint(self):
        result, _ = self.propose()
        path = self.output / 'analysis/effective-config.json'
        config = read_json(path)
        config['config']['java_import_order'] = 'THIRD_PARTY_PACKAGE###STANDARD_JAVA_PACKAGE###STATIC'
        write_json(path, config)
        with self.assertRaises(MigrationError):
            assistant.apply_proposal(self.output, result['id'], reviewed=True)

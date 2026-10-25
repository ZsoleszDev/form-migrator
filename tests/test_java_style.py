"""Checkstyle layout of the generated Java (java_style): the rules the company build checks.

NeedBraces, LeftCurly, RightCurly (alone/same), EmptyLineSeparator, OneStatementPerLine,
AnnotationLocation, CustomImportOrder, AvoidStarImport, UnusedImports, WhitespaceAround,
AvoidEscapedUnicodeCharacters and 4-space indentation.
"""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from frm_forms import java_style
from frm_forms.cli import configuration, main
from frm_forms.common import MigrationError
from test_forms_runtime import fixture


class LayoutTests(unittest.TestCase):
    def setUp(self):
        java_style.configure({})
        self.addCleanup(java_style.configure, {})

    def layout(self, source):
        result = java_style.layout(source, 'Test.java')
        self.assertEqual(java_style.FAILED, [])
        self.assertEqual(java_style.layout(result, 'Test.java'), result)  # stable
        return result

    def test_blocks_get_braces_and_one_statement_per_line(self):
        source = ('package a;\n\nclass A {\n'
                  '    int f(int x) { if (x > 0) return 1; else if (x < 0) return -1; else return 0; }\n'
                  '    void g(int[] v) { for (int i : v) if (i > 0) System.out.println(i); while (v.length > 5) break; }\n'
                  '    void h() { try { g(null); }\n    catch (RuntimeException e) { throw e; } do g(null); while (true); }\n'
                  '}\n')
        self.assertEqual(self.layout(source), '''package a;

class A {
    int f(int x) {
        if (x > 0) {
            return 1;
        } else if (x < 0) {
            return -1;
        } else {
            return 0;
        }
    }

    void g(int[] v) {
        for (int i : v) {
            if (i > 0) {
                System.out.println(i);
            }
        }
        while (v.length > 5) {
            break;
        }
    }

    void h() {
        try {
            g(null);
        } catch (RuntimeException e) {
            throw e;
        }
        do {
            g(null);
        } while (true);
    }
}
''')

    def test_members_are_separated_and_empty_bodies_take_two_lines(self):
        source = ('public final class B {\n    private final int x = 1;\n    private final int y = 2;\n'
                  '    private B() {}\n    @Override public String toString() { return "b"; }\n'
                  '    public int x() { return x; }\n    public static final class C {}\n}\n')
        self.assertEqual(self.layout(source), '''public final class B {
    private final int x = 1;
    private final int y = 2;

    private B() {
    }

    @Override
    public String toString() {
        return "b";
    }

    public int x() {
        return x;
    }

    public static final class C {
    }
}
''')

    def test_switch_labels_lambdas_and_anonymous_classes(self):
        source = ('class G {\n    int f(String op) {\n        switch (op) {\n            case "a": return 1;\n'
                  '            case "b": case "c": g(() -> { if (op == null) return; }); return 2;\n'
                  '            default: throw new IllegalArgumentException(op);\n        }\n    }\n'
                  '    Object t = new Object() { public String toString() { return "t"; } };\n}\n')
        self.assertEqual(self.layout(source), '''class G {
    int f(String op) {
        switch (op) {
            case "a":
                return 1;
            case "b":
            case "c":
                g(() -> {
                    if (op == null) {
                        return;
                    }
                });
                return 2;
            default:
                throw new IllegalArgumentException(op);
        }
    }

    Object t = new Object() {
        public String toString() {
            return "t";
        }
    };
}
''')

    def test_comments_stay_with_their_code(self):
        source = ('class F {\n    /** Javadoc. */\n    void f(boolean x) {\n        if (x) // why\n            g();\n'
                  '        // before else\n        else g(); // after\n    }\n    // g\n    void g() {}\n}\n')
        self.assertEqual(self.layout(source), '''class F {
    /** Javadoc. */
    void f(boolean x) {
        if (x) { // why
            g();
        } else {
            // before else
            g(); // after
        }
    }

    // g
    void g() {
    }
}
''')

    def test_wrapped_lines_stay_at_least_eight_columns_deeper(self):
        source = ('class H {\n    void f() {\n        call(a,\n            b);\n'
                  '        if (x) throw new E(1,\n            2);\n    }\n}\n')
        self.assertEqual(self.layout(source), '''class H {
    void f() {
        call(a,
                b);
        if (x) {
            throw new E(1,
                    2);
        }
    }
}
''')

    def test_imports_are_grouped_sorted_and_only_used_ones_stay(self):
        java_style.MEMBER_TYPES['a.cl.XDtos'] = frozenset({'Row', 'Page'})
        source = ('package a.dps;\n\nimport org.springframework.web.bind.annotation.*;\nimport a.cl.XDtos.*;\n'
                  'import java.util.Map;\nimport java.util.List;\nimport hu.ceg.Unused;\nimport java.lang.String;\n'
                  'import org.springframework.http.HttpStatus;\nimport a.dps.Same;\n\n@RestController\npublic class C {\n'
                  '    HttpStatus status;\n    Same same;\n    @GetMapping("/x")\n'
                  '    public List<Row> list(@RequestParam(name="offset") int offset) { return List.of(); }\n}\n')
        out = self.layout(source)
        self.assertTrue(out.startswith('package a.dps;\n\nimport java.util.List;\n\nimport a.cl.XDtos.Row;\n'
                                       'import org.springframework.http.HttpStatus;\n'
                                       'import org.springframework.web.bind.annotation.GetMapping;\n'
                                       'import org.springframework.web.bind.annotation.RequestParam;\n'
                                       'import org.springframework.web.bind.annotation.RestController;\n\n'
                                       '@RestController\npublic class C {\n'), out)
        self.assertIn('@RequestParam(name = "offset") int offset', out)

    def test_import_order_follows_the_configured_rules(self):
        java_style.configure({'java_import_order': 'THIRD_PARTY_PACKAGE###STANDARD_JAVA_PACKAGE'})
        out = self.layout('import java.util.List;\nimport org.x.Y;\nimport org.x.Y;\n\nclass D {\n    List<Y> v;\n}\n')
        self.assertTrue(out.startswith('import org.x.Y;\n\nimport java.util.List;\n\nclass D {\n'), out)
        self.assertEqual(java_style.validate_import_order(' STATIC###SAME_PACKAGE(3)###THIRD_PARTY_PACKAGE '),
                         'STATIC###SAME_PACKAGE(3)###THIRD_PARTY_PACKAGE')
        for bad in ('STATIC###STATIC', 'JAVA###OTHER', '', 3):
            with self.subTest(bad=bad), self.assertRaises(MigrationError):
                java_style.validate_import_order(bad)

    def test_unicode_escapes_become_utf8_characters(self):
        source = ('class E {\n    String a = "D\\u00e1tum \\ud83d\\ude00";\n    char c = \'\\u00e9\';\n'
                  '    String b = "tab\\u0009 nul\\u0000 kept \\\\u00e1 \\"q\\"";\n}\n')
        out = self.layout(source)
        self.assertIn('String a = "Dátum 😀";', out)
        self.assertIn("char c = 'é';", out)
        self.assertIn('String b = "tab\\t nul\\000 kept \\\\u00e1 \\"q\\"";', out)

    def test_unreadable_input_is_written_unchanged(self):
        source = 'class I {\n    /* unterminated\n}\n'
        with mock.patch.dict(os.environ, {'FRM_JAVA_STYLE_STRICT': ''}):
            self.assertEqual(java_style.layout(source, 'I.java'), source)
        self.assertEqual(java_style.FAILED[-1]['file'], 'I.java')

    def test_the_layout_can_be_switched_off(self):
        java_style.configure({'java_checkstyle_format': False})
        source = 'class J { void f() { if (x) g(); } }\n'
        self.assertEqual(java_style.layout(source), source)

    def test_cli_validates_the_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'config.json'
            for values in ({'java_import_order': 'JAVA###OTHER'}, {'java_checkstyle_format': 'yes'}):
                config.write_text(json.dumps(values))
                with self.subTest(values=values), self.assertRaises(MigrationError):
                    configuration(argparse.Namespace(config=config, java_package=None, max_ai_calls=None, screen=True))

    def test_common_migrate_tools_template_is_already_in_layout(self):
        path = Path(java_style.__file__).with_name('templates') / 'CommonMigrateTools.java.tpl'
        text = path.read_text(encoding='utf-8').replace('@@PACKAGE@@', 'hu.ceg.cl')
        self.assertEqual(self.layout(text), text)
        self.assertLessEqual(max(len(line) for line in text.split('\n')), 100)
        self.assertIn('import java.util.stream.Collectors;\n\nimport org.springframework.dao.DataAccessException;', text)


class GeneratedLayoutTests(unittest.TestCase):
    def generate(self, root, label, config):
        source = root / 'input.xml'
        source.write_bytes(fixture())
        settings = root / (label + '.json')
        settings.write_text(json.dumps({'backend_live': True, **config}))
        output = root / label
        errors = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
            code = main(['migrate', str(source), '--module', 'rt', '--screen', '--config', str(settings), '--out', str(output)])
        self.assertEqual(code, 0, errors.getvalue())
        return output

    def test_every_generated_java_file_is_already_in_checkstyle_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for label, config in [('compact', {}), ('java', {'backend_trigger_mode': 'java'}),
                                  ('company', {'AWU_AZON': '1234'}), ('nopackage', {'java_empty_package': True})]:
                with self.subTest(label=label):
                    output = self.generate(root, label, config)
                    self.assertFalse((output / 'analysis/java-style.json').exists())
                    files = sorted(output.glob('backend/**/*.java'))
                    self.assertGreater(len(files), 5)
                    for path in files:
                        source = path.read_text(encoding='utf-8')
                        java_style.configure({})
                        self.assertEqual(java_style.layout(source, path.name), source, path.name)  # nothing left to fix
                        self.assertEqual(java_style.FAILED, [], path.name)
                        self.check_rules(path.name, source)
        java_style.configure({})

    def check_rules(self, name, source):
        for token in java_style.tokenize(source):
            if token.kind in ('str', 'chr'):  # AvoidEscapedUnicodeCharacters
                self.assertNotRegex(token.text, r'(?<!\\)(?:\\\\)*\\u[0-9a-fA-F]{4}', name)
        lines = source.split('\n')
        imports = [i for i, line in enumerate(lines) if line.startswith('import ')]
        self.assertFalse([lines[i] for i in imports if lines[i].endswith('.*;')], name)  # AvoidStarImport
        java = [i for i in imports if lines[i].startswith(('import java.', 'import javax.'))]
        other = [i for i in imports if i not in java]
        if java and other:  # CustomImportOrder: java/javax first, one empty line before the next group
            self.assertLess(max(java), min(other), name)
            self.assertEqual(lines[max(java) + 1], '', name)
        for group in (java, other):
            names = [tuple(lines[i][len('import '):-1].split('.')) for i in group]
            self.assertEqual(names, sorted(names), name)
        for number, line in enumerate(lines, 1):
            code = line.strip()
            where = f'{name}:{number}'
            self.assertNotRegex(code, r'^(?:\} )?(?:if|else if|for|while) \(.*\)[^{]*;$', where)  # NeedBraces
            self.assertFalse(code.startswith(('@Override ', '@Transactional ')), where)  # AnnotationLocation
            self.assertEqual(line, line.rstrip(), where)
            self.assertNotIn('\t', line, where)
            if not code.startswith('*'):  # Javadoc/comment continuation lines are one column deeper
                self.assertEqual((len(line) - len(line.lstrip(' '))) % 2, 0, where)


if __name__ == '__main__':
    unittest.main()

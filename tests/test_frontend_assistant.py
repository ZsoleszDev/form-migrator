"""The optional AI rewrite of a generated screen (frontend_assistant): standalone, signals, commented code.

The whole file goes to the model only when the request and the answer fit the configured context; otherwise the
class goes in parts (the head with the signals first, then the methods in groups) and is put back together. The
large constant initializers never leave the migrator (__FRM_KEEP_n__). A dynamic Ollama double answers each request
from the code it was sent, the way a model would: the state becomes signals, the writes become .set().
"""
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from frm_forms import backend_assistant as engine, frontend_assistant as fa
from frm_forms.common import MigrationError
from java_support import trigger_mode
from screen_support import ANGULAR_CLI_OPTIONS, with_company_imports
from test_jpa_sql import migrate

setUpModule, tearDownModule = trigger_mode('ai')

ROOT = Path(__file__).resolve().parents[1]
SMALL = dict(model='m', num_ctx=16384, num_predict=4096)   # the laptop default: the replica screen goes in parts
LARGE = dict(model='m', num_ctx=32768, num_predict=12288)  # the whole replica screen fits


def block(text, title):
    found = re.search(re.escape(title) + r"\n```ts\n([\s\S]*?)\n```", text)
    return found.group(1) if found else None


def signalize_head(code):
    code = re.sub(r"protected (\w+Rows): Record<string, unknown>\[\] = \[\];",
                  r"protected readonly \1 = signal<Record<string, unknown>[]>([]);", code)
    code = re.sub(r"protected (\w+Selection): Record<string, unknown> \| null = null;",
                  r"protected readonly \1 = signal<Record<string, unknown> | null>(null);", code)
    code = code.replace("@Input() migrationParameters: Readonly<Record<string, string | null>> = {};",
                        "readonly migrationParameters = input<Readonly<Record<string, string | null>>>({});")
    code = re.sub(r'\[value\]="(\w+Rows)"', r'[value]="\1()"', code)
    code = re.sub(r'\[\(selection\)\]="(\w+Selection)"', r'[selection]="\1()" (selectionChange)="\1.set($event)"', code)
    return code.replace("  constructor() {", "  // Az alaposztály (ServiceBase) beállítása.\n  constructor() {")


def signalize_methods(code):
    code = re.sub(r"this\.(\w+Rows) = (.+);", r"this.\1.set(\2);", code)
    return code.replace("this.migrationParameters[", "this.migrationParameters()[")


class Model:
    """Ollama double: rewrites the code of each request; `change(answer, request)` spoils answers on purpose."""

    def __init__(self, change=None):
        self.requests, self.change = [], change

    def open(self, request, timeout):
        body = json.loads(request.data)
        self.requests.append(body)
        first = body["messages"][1]["content"]
        if (code := block(first, "Component:")) is not None:
            answer = signalize_methods(signalize_head(code))
        elif (code := block(first, "Head:")) is not None:
            answer = signalize_head(code)
        else:
            answer = signalize_methods(block(first, "Methods:"))
        if self.change:
            answer = self.change(answer, body)
        content = "```ts\n" + answer + "\n```\nNOTES:\n- Signalokra átírva."
        return io.BytesIO(json.dumps({"done": True, "done_reason": "stop", "message": {"role": "assistant", "content": content},
                                      "prompt_eval_count": 100, "eval_count": 100}).encode())


class ReadingTests(unittest.TestCase):
    def test_mask_blanks_literals_comments_and_regexes(self):
        source = ("const a = 'x{'; // }\nconst b = `t ${ {k: '}'}.k } {`; /* { */\nconst r = /[}]\\//g;\n"
                  "const c = x / 2 / y;\n")
        mask = fa.ts_mask(source)
        self.assertEqual(len(mask), len(source))
        self.assertTrue(fa.balanced(mask), mask)
        self.assertIn("x / 2 / y", mask)  # a division is not a regex

    def test_members_of_a_tricky_class(self):
        source = ("export class A extends B {\n  // the state\n  @Input() x!: string;\n"
                  "  private readonly s: { a: number } = { a: 1 };\n  readonly f = (v: number): number => { return v; };\n"
                  "  get total(): number { return 1; }\n  load(): { ok: boolean } {\n    return { ok: true };\n  }\n"
                  "  constructor() {\n    super();\n  }\n}\n")
        mask = fa.ts_mask(source)
        name, start, end = fa.class_span(source, mask)
        found = [(m.kind, m.name) for m in fa.members(source, mask, start + 1, end)]
        self.assertEqual(name, "A")
        self.assertEqual(found, [("field", "x"), ("field", "s"), ("field", "f"), ("method", "total"), ("method", "load"),
                                 ("constructor", "constructor")])

    def test_core_imports_follow_the_code(self):
        text = ("import { Component, Input, inject } from '@angular/core';\n@Component({})\nexport class A {\n"
                "  readonly x = input<string>('');\n  readonly rows = signal<number[]>([]);\n  readonly n = computed(() => this.rows().length);\n}\n")
        fixed, notes = fa.fix_core_imports(text)
        self.assertIn("import { Component, signal, computed, input } from '@angular/core';", fixed)
        self.assertTrue(notes)


class ReplicaTests(unittest.TestCase):
    """The replica screen: the plan, both ways, the checks, the apply."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.pristine = migrate(Path(cls.temp.name))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.out = Path(self.work.name) / "rendeles"
        shutil.copytree(self.pristine, self.out)
        self.task = fa.screens(self.out)[0]
        self.source = (self.out / self.task["target"]).read_text(encoding="utf-8")

    def tearDown(self):
        self.work.cleanup()

    def propose(self, mode, size, model=None, **extra):
        model = model or Model()
        with patch("frm_forms.ollama.opener", return_value=model):
            result = fa.propose(self.out, self.task["id"], engine.Options(**size, **extra), mode)
        return result, model

    def test_the_plan_offers_the_whole_file_only_when_it_fits(self):
        small = fa.plan(self.out, self.task["id"], engine.Options(**SMALL))
        self.assertEqual(self.task["target"], "frontend/rendeles/rendeles.component.ts")
        self.assertFalse(small["whole"]["fits"])  # the answer alone needs more than num_predict 4096
        self.assertGreater(small["whole"]["required_num_predict"], 4096)
        self.assertTrue(small["parts"]["fits"])
        self.assertEqual(small["recommended"], "parts")
        self.assertEqual([r["kind"] for r in small["parts"]["requests"]], ["head", "methods", "methods"])
        self.assertEqual(small["kept"], 1)  # the FormBlock structures stay with the migrator
        self.assertLess(small["sent_bytes"], small["bytes"])
        large = fa.plan(self.out, self.task["id"], engine.Options(**LARGE))
        self.assertTrue(large["whole"]["fits"])
        self.assertEqual(large["recommended"], "whole")
        tiny = fa.plan(self.out, self.task["id"], engine.Options(model='m', num_ctx=4096, num_predict=512))
        self.assertIsNone(tiny["recommended"])
        with self.assertRaisesRegex(MigrationError, r"num_ctx\) legalább \d+.*num_predict\) legalább \d+.*Részenként elfér"):
            fa.prepare(self.out, self.task["id"], engine.Options(**SMALL), "whole")

    def test_whole_and_parts_give_the_same_signal_component(self):
        whole, model = self.propose("whole", LARGE)
        self.assertEqual((whole["status"], whole["calls"], len(model.requests)), ("proposal", 1, 1))
        prompt = model.requests[0]["messages"][1]["content"]
        self.assertIn("__FRM_KEEP_1__", prompt)
        self.assertNotIn("ownId: 'CTRL.PB_KERES'", prompt)  # the structures are not sent
        self.assertIn("Signals for the state", prompt)
        parts, model = self.propose("parts", SMALL)
        self.assertEqual((parts["status"], parts["calls"], len(model.requests)), ("proposal", 3, 3))
        head, methods = model.requests[0]["messages"][1]["content"], model.requests[1]["messages"][1]["content"]
        self.assertIn("Methods of the class (not shown, rewritten later):\n- rendelesList(offset = 0, limit = 200)", head)
        self.assertIn("- tetelRows (signal)\n- tetelSelection (signal)", methods)
        self.assertIn("- migrationParameters (input)", methods)
        self.assertNotIn("template:", methods)  # the outline: fields only
        text = fa.proposal(self.out, parts["id"])["text"]
        self.assertEqual(text, fa.proposal(self.out, whole["id"])["text"])
        self.assertIn("import { Component, inject, signal, input } from '@angular/core';", text)
        self.assertIn("protected readonly tetelRows = signal<Record<string, unknown>[]>([]);", text)
        self.assertIn("this.tetelRows.set(res.rows ?? []);", text)
        self.assertIn('[value]="tetelRows()"', text)
        self.assertIn("{ type: 'button', ownId: 'CTRL.PB_KERES'", text)  # the structures are back, unchanged
        self.assertEqual(text.count("/* frm-input:"), self.source.count("/* frm-input:"))
        self.assertIn(fa.proposal(self.out, parts["id"])["validation"]["typescript_syntax"], {"passed", "not_available"})
        self.compile(text)

    def compile(self, text):
        tsc = os.environ.get("FRM_TSC") or shutil.which("tsc")
        if not tsc:
            return
        work = Path(tempfile.mkdtemp(dir=self.work.name))
        shutil.copytree(self.out / "frontend", work / "frontend")
        (work / self.task["target"]).write_text(text, encoding="utf-8")
        shutil.copy(ROOT / "tests/ts_stubs/company.ts", work / "company.ts")
        files = with_company_imports(work / "frontend")
        (work / "tsconfig.json").write_text(json.dumps({"compilerOptions": {
            **ANGULAR_CLI_OPTIONS, "noUnusedLocals": True, "useDefineForClassFields": True, "noEmit": True},
            "files": [str(ROOT / "tests/ts_stubs/host.d.ts")] + files}))
        run = subprocess.run([tsc, "-p", str(work / "tsconfig.json")], capture_output=True, text=True, timeout=180)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_a_bad_answer_gets_one_correction_then_a_draft(self):
        calls = []

        def drop_a_method_once(answer, body):
            calls.append(1)
            return answer.replace("  rendelesDelete(body: unknown) {", "  rendelesRemove(body: unknown) {") if len(calls) == 1 else answer

        result, model = self.propose("whole", LARGE, Model(drop_a_method_once))
        self.assertEqual((result["status"], result["calls"]), ("proposal", 2))
        self.assertIn("Hiányzó metódus(ok): rendelesDelete", model.requests[1]["messages"][3]["content"])

        def assign_a_signal(answer, body):
            return answer.replace("this.tetelRows.set(res.rows ?? []);", "this.tetelRows = res.rows ?? [];")

        draft, _ = self.propose("whole", LARGE, Model(assign_a_signal), attempts=2)
        self.assertEqual(draft["status"], "draft")
        self.assertIn("Signalnak értéket adó kód (tetelRows): .set() vagy .update() kell.", draft["problems"])
        with self.assertRaisesRegex(MigrationError, "nem illeszthető be"):
            fa.apply_proposal(self.out, draft["id"], reviewed=True)

    def test_a_failed_part_keeps_the_original_methods_as_a_draft(self):
        def lose_the_placeholder_and_a_method(answer, body):
            if "Methods:" in body["messages"][1]["content"] and "rendelesList" in body["messages"][1]["content"]:
                return answer.replace("  rendelesList(", "  rendelesListAll(")
            return answer

        result, _ = self.propose("parts", SMALL, Model(lose_the_placeholder_and_a_method))
        self.assertEqual((result["status"], result["calls"]), ("draft", 4))  # head, 2 tries of the 2nd part, 3rd part
        self.assertTrue(any(p.startswith("2. rész (rendelesList") for p in result["problems"]), result["problems"])
        text = fa.proposal(self.out, result["id"])["text"]
        self.assertIn("  rendelesList(offset = 0, limit = 200) {", text)  # the original methods of that part stay
        self.assertNotIn("rendelesListAll", text)
        self.assertIn("this.partnerRows.set(res.rows ?? []);", text)  # the other parts are rewritten

    def test_apply_writes_only_over_the_same_file(self):
        result, _ = self.propose("parts", SMALL)
        target = self.out / self.task["target"]
        applied = fa.apply_proposal(self.out, result["id"], reviewed=True)
        self.assertTrue(applied["applied"])
        self.assertEqual(target.read_text(encoding="utf-8"), fa.proposal(self.out, result["id"])["text"])
        self.assertEqual((self.out / applied["backup"]).read_text(encoding="utf-8"), self.source)
        with self.assertRaisesRegex(MigrationError, "már beillesztették"):
            fa.apply_proposal(self.out, result["id"], reviewed=True)
        target.write_text(self.source, encoding="utf-8")
        again, _ = self.propose("whole", LARGE)
        target.write_text(self.source + "\n// kézi módosítás\n", encoding="utf-8")
        with self.assertRaisesRegex(MigrationError, "megváltozott"):
            fa.apply_proposal(self.out, again["id"], reviewed=True)


try:
    from fastapi.testclient import TestClient
    from frm_forms.web.app import create_app
    from frm_forms.web.settings import Settings
    WEB = True
except ImportError:
    WEB = False


@unittest.skipUnless(WEB, "pip install -r requirements-test.txt")
class WebTests(unittest.TestCase):
    def setUp(self):
        from test_manual_action_source import FORM
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.app = create_app(Settings(data_dir=Path(self.temp.name), folder_dialog=False))
        self.client = TestClient(self.app, base_url="http://localhost")
        self.client.__enter__(); self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {"X-Frm-Client": "local-ui"}
        submitted = self.client.post("/api/jobs", headers=self.headers, files={"file": ("form.xml", FORM.encode(), "application/xml")},
                                     data={"options": json.dumps({"module": "kezi", "screen_window_selection": "all"})})
        self.assertEqual(submitted.status_code, 202, submitted.text)
        job = submitted.json()["id"]
        for _ in range(1500):
            state = self.client.get("/api/jobs/" + job).json()
            if state["status"] in {"completed", "failed", "needs_input"}: break
            time.sleep(.02)
        self.assertEqual(state["status"], "completed", state)
        self.base = "/api/jobs/" + job + "/frontend-assistant"

    def test_opt_in_fit_run_and_apply(self):
        index = self.client.get(self.base).json()
        self.assertFalse(index["enabled"])
        task = index["tasks"][0]
        self.assertEqual(task["name"], "kezi.component.ts")
        self.assertIn(task["recommended"], {"whole", "parts"})
        refused = self.client.post(self.base, headers=self.headers, json={"task_id": task["id"], "mode": "parts"})
        self.assertEqual(refused.status_code, 409)
        self.assertIn("nincs bekapcsolva", refused.json()["detail"])
        saved = self.client.post("/api/assistant/settings", headers=self.headers,
                                 json={"url": "http://localhost:11434", "model": "m", "auto_run": False})
        self.assertEqual(saved.status_code, 200, saved.text)
        # One switch alone keeps the rest of the settings (the connection, the automation).
        switched = self.client.post("/api/assistant/settings", headers=self.headers, json={"frontend_ai": True}).json()
        self.assertEqual((switched["frontend_ai"], switched["url"], switched["model"], switched["auto_run"]),
                         (True, "http://localhost:11434", "m", False))
        tight = self.client.get(self.base, params={"num_ctx": 4096, "num_predict": 512}).json()["tasks"][0]
        self.assertFalse(tight["whole"]["fits"])
        too_small = self.client.post(self.base, headers=self.headers,
                                     json={"task_id": task["id"], "mode": "whole", "num_ctx": 4096, "num_predict": 512})
        self.assertEqual(too_small.status_code, 409)
        self.assertIn("num_ctx ≥", too_small.json()["detail"])
        preview = self.client.post(self.base + "/prompt", headers=self.headers, json={"task_id": task["id"], "mode": "parts"}).json()
        self.assertIn("Rewrite the head of this Angular component", preview["user"])
        with patch("frm_forms.ollama.opener", return_value=Model()):
            started = self.client.post(self.base, headers=self.headers,
                                       json={"task_id": task["id"], "mode": "parts", "num_ctx": 16384, "num_predict": 4096})
            self.assertEqual(started.status_code, 202, started.text)
            for _ in range(1500):
                run = self.client.get("/api/jobs/" + self.base.split("/")[3] + "/assistant/runs/" + started.json()["id"]).json()
                if run["status"] not in {"queued", "running"}: break
                time.sleep(.02)
        self.assertEqual(run["status"], "completed", run)
        self.assertFalse(run["applied"])  # never applied without review
        proposal = self.client.get(self.base + "/proposals/" + run["result"]["id"]).json()
        self.assertEqual(proposal["status"], "proposal", proposal)
        self.assertIn("--- frontend/kezi/kezi.component.ts", proposal["patch"])
        applied = self.client.post(self.base + "/proposals/" + proposal["id"] + "/apply", headers=self.headers, json={"reviewed": True})
        self.assertEqual(applied.status_code, 200, applied.text)
        listed = self.client.get(self.base).json()["proposals"]
        self.assertEqual([(p["id"], p["applied"]) for p in listed], [(proposal["id"], True)])
        page = self.client.get("/ai-log")
        self.assertIn("Frontend AI-átírás (opcionális)", page.text)


if __name__ == "__main__":
    unittest.main()

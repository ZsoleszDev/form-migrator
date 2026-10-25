"""Web-layer assistant manager without FastAPI: run records carry the AI log, preview, failure link."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from frm_forms.cli import main
from frm_forms.web.assistant import AssistantManager, AssistantRequest, PromptRequest
from frm_forms.web.jobs import JobError
from java_support import COMPANY_IMPORTS, trigger_mode
from test_backend_assistant import MARKDOWN, Client
from test_manual_action_source import FORM

setUpModule, tearDownModule = trigger_mode('plsql')


class FakeJobs:
    """The parts of JobManager the assistant uses."""
    def __init__(self, root: Path):
        self.root = root
        self.settings = SimpleNamespace(data_dir=root / "data")

    def get(self, job_id):
        if job_id != "job1":
            raise JobError("Nincs ilyen feladat.", 404)
        return {"id": job_id, "status": "completed"}


class AssistantManagerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "data").mkdir()
        source, config = root / "form.xml", root / "config.json"
        source.write_text(FORM, encoding="utf-8")
        config.write_text(json.dumps({"java_company_imports": COMPANY_IMPORTS}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(["migrate", str(source), "--screen", "--module", "kezi", "--config", str(config),
                         "--out", str(root / "job1" / "module")])
        self.assertEqual(code, 0)
        self.manager = AssistantManager(FakeJobs(root))
        self.addCleanup(self.manager.close)
        # These checks read the proposal of a finished run: no automatic apply (test_ai_triggers covers it).
        self.manager.save_settings({"auto_apply": False})
        self.task = next(t for t in self.manager.index("job1")["tasks"] if t["method"] == "onCgnvW011PbLekerdezes")

    def finished(self, run_id):
        for _ in range(500):
            state = self.manager.state("job1", run_id)
            if state["status"] not in {"queued", "running"}:
                return state
            time.sleep(0.02)
        self.fail("Az AI-futás nem fejeződött be.")

    def test_completed_run_exposes_its_exchange_and_the_preview_matches(self):
        with patch("frm_forms.ollama.opener", return_value=Client(MARKDOWN)):
            started = self.manager.start("job1", AssistantRequest(task_id=self.task["id"]))
            self.assertRegex(started["exchange_id"], r"^[a-f0-9]{20}$")
            state = self.finished(started["id"])
        self.assertEqual(state["status"], "completed", state)
        self.assertEqual(state["result"]["exchange_id"], started["exchange_id"])
        listed = self.manager.exchanges("job1", self.task["id"])["exchanges"]
        self.assertEqual([(e["id"], e["status"], e["proposal_status"]) for e in listed],
                         [(started["exchange_id"], "completed", "proposal")])
        log = self.manager.exchange("job1", started["exchange_id"])
        self.assertEqual(log["attempts"][0]["raw"], MARKDOWN)
        preview = self.manager.prompt("job1", PromptRequest(task_id=self.task["id"]))
        self.assertEqual((preview["system"], preview["user"]), (log["prompt"]["system"], log["prompt"]["user"]))
        self.assertEqual(self.manager.index("job1")["recent_run"]["exchange_id"], started["exchange_id"])
        with self.assertRaises(JobError) as missing:
            self.manager.exchange("job1", "0" * 20)
        self.assertEqual(missing.exception.status, 404)
        with self.assertRaises(JobError) as invalid:
            self.manager.exchange("job1", "../x")
        self.assertEqual(invalid.exception.status, 422)

    def test_failed_run_names_the_log_page_and_keeps_the_answers(self):
        with patch("frm_forms.ollama.opener", return_value=Client("Nem tudom.", "Most sem tudom.")):
            started = self.manager.start("job1", AssistantRequest(task_id=self.task["id"], attempts=2))
            state = self.finished(started["id"])
        self.assertEqual(state["status"], "failed")
        self.assertIn("/ai-log?job=job1&exchange=" + started["exchange_id"], state["error"])
        self.assertEqual(self.manager.index("job1")["recent_run"]["exchange_id"], started["exchange_id"])
        log = self.manager.exchange("job1", started["exchange_id"])
        self.assertEqual([a["raw"] for a in log["attempts"]], ["Nem tudom.", "Most sem tudom."])

    def test_errors_before_any_request_have_no_log(self):
        with patch("frm_forms.web.assistant.engine.propose", side_effect=TimeoutError()):
            started = self.manager.start("job1", AssistantRequest(task_id=self.task["id"]))
            state = self.finished(started["id"])
        self.assertEqual(state["error"], "TimeoutError")
        self.assertIsNone(state["exchange_id"])
        self.assertEqual(self.manager.exchanges("job1")["exchanges"], [])


if __name__ == "__main__":
    unittest.main()

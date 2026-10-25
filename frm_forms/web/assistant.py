"""The local Java assistant runs: one at a time, in a queue, so a CPU laptop is never asked twice at once.

4.35: after a migration (and after every saved table structure) the triggers whose tables are known are queued
automatically; a successful, syntax-checked proposal without MISSING items is applied at once (the original file
is backed up, the diff stays reviewable). Every run shows its live state while the model works: phase (prompt
reading / thinking / writing), elapsed time, generated tokens and speed, the last lines of the answer. A run can
be cancelled; the connection to Ollama is closed, so the laptop stops generating too.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import threading
import time
import uuid

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from frm_forms import backend_assistant as engine
from frm_forms import assistant_log
from frm_forms.common import MigrationError, read_json
from frm_forms import schema_workbench, input_bindings
from .jobs import JobError

LOG = logging.getLogger(__name__)
FINISHED = {"completed", "failed", "cancelled", "interrupted"}
KEEP_RUNS = 400


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AssistantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    task_id: str = Field(pattern=r"^[a-f0-9]{20}$")
    url: str = Field(default="http://localhost:11434", max_length=1000)
    model: str = Field(default="qwen3-coder:30b", pattern=r"^[A-Za-z0-9_./:@-]{1,120}$")
    num_ctx: int = Field(default=16384, ge=4096, le=65536, strict=True)
    num_predict: int = Field(default=4096, ge=512, le=16384, strict=True)
    timeout: int = Field(default=900, ge=1, le=3600, strict=True)
    attempts: int = Field(default=2, ge=1, le=3, strict=True)
    think: Literal["default", "disabled", "low", "medium", "high"] = "default"
    data_access: Literal["jpa", "inline", "jdbc"] = "jpa"
    instructions: str = Field(default="", max_length=8000)
    project_context: str = Field(default="", max_length=24000)
    cached_only: bool = False


class PromptRequest(BaseModel):
    """What the next run would send; the model is not called."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    task_id: str = Field(pattern=r"^[a-f0-9]{20}$")
    num_ctx: int = Field(default=16384, ge=4096, le=65536, strict=True)
    num_predict: int = Field(default=4096, ge=512, le=16384, strict=True)
    data_access: Literal["jpa", "inline", "jdbc"] = "jpa"
    instructions: str = Field(default="", max_length=8000)
    project_context: str = Field(default="", max_length=24000)


class AutoSettings(BaseModel):
    """The shared AI settings the automatic runs use (also saved by every manual run)."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    url: str = Field(default="", max_length=1000)
    model: str = Field(default="", max_length=120)
    num_ctx: int = Field(default=16384, ge=4096, le=65536, strict=True)
    num_predict: int = Field(default=4096, ge=512, le=16384, strict=True)
    timeout: int = Field(default=900, ge=1, le=3600, strict=True)
    attempts: int = Field(default=2, ge=1, le=3, strict=True)
    think: Literal["default", "disabled", "low", "medium", "high"] = "default"
    auto_run: bool = True
    auto_apply: bool = True


class AutoRequest(BaseModel):
    """Queue every ready trigger of a module now (force: also those an earlier automatic run gave up on)."""
    model_config = ConfigDict(extra="forbid")
    force: bool = False


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    clear_queue: bool = False


class AssistantManager:
    def __init__(self, manager):
        self.manager = manager
        self.lock = threading.RLock()
        self.wake = threading.Condition(self.lock)
        self.runs: dict[str, dict] = {}
        self.queue: deque[str] = deque()
        self.active: str | None = None
        self.cancel_requested: set[str] = set()
        self.closed = False
        # (job, task) -> fingerprint an automatic run already tried: no endless retry of the same input.
        self.auto_tried: dict[tuple[str, str], str] = {}
        self.worker = threading.Thread(target=self._work, daemon=True, name="frm-assistant-queue")
        self.worker.start()
        if hasattr(manager, "on_completed"):
            manager.on_completed = self.migration_completed

    # ---------------------------------------------------------------- settings

    @property
    def settings_path(self) -> Path:
        return Path(self.manager.settings.data_dir) / "assistant-settings.json"

    def settings(self) -> dict:
        try:
            value = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return AutoSettings.model_validate({k: v for k, v in value.items() if k in AutoSettings.model_fields}).model_dump()
        except (OSError, ValueError):
            fallback = getattr(self.manager.settings, "ollama_url", "") or ""
            return AutoSettings(url="" if fallback.startswith("http://xx") else fallback,
                                model=getattr(self.manager.settings, "ollama_model", "") or "").model_dump()

    def save_settings(self, values: dict) -> dict:
        merged = AutoSettings.model_validate({**self.settings(), **values}).model_dump()
        if merged["url"]:
            try:
                from frm_forms.ollama import base_url
                merged["url"] = base_url(merged["url"])
            except MigrationError as exc:
                raise JobError(str(exc), 422) from exc
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.settings_path)
        return merged

    def options_from_settings(self, settings: dict | None = None) -> engine.Options:
        s = settings or self.settings()
        options = engine.Options(url=s["url"], model=s["model"], num_ctx=s["num_ctx"], num_predict=s["num_predict"],
                                 timeout=s["timeout"], attempts=s["attempts"], think=s["think"], data_access="jpa")
        options.validate()
        return options

    # ---------------------------------------------------------------- lifecycle

    def close(self):
        with self.lock:
            self.closed = True
            if self.active:
                self.cancel_requested.add(self.active)
            for run_id in list(self.queue):
                self.runs[run_id].update(status="interrupted", finished=now(), error="A backend leállt.")
            self.queue.clear()
            self.wake.notify_all()

    def require_idle(self, job_id: str):
        with self.lock:
            if self.active and self.runs[self.active]["job_id"] == job_id:
                raise JobError("Ehhez a modulhoz Java-asszisztens fut; a törlés/újrafuttatás a befejezés után érhető el "
                               "(vagy állítsd le a futást).", 409)
            self._drop_queued(job_id)

    def _drop_queued(self, job_id: str, reason: str = "A modul törlése/újrafuttatása miatt kivéve a sorból.") -> int:
        removed = [run_id for run_id in self.queue if self.runs[run_id]["job_id"] == job_id]
        for run_id in removed:
            self.queue.remove(run_id)
            self.runs[run_id].update(status="cancelled", finished=now(), error=reason)
        return len(removed)

    def output(self, job_id: str) -> Path:
        job = self.manager.get(job_id)
        if job["status"] != "completed":
            raise JobError("Az asszisztens a modul generálása után használható.", 409)
        return self.manager.root / job_id / "module"

    # ---------------------------------------------------------------- the queue

    def _record(self, job_id: str, task: dict, *, auto: bool, options: engine.Options, request: dict | None = None) -> dict:
        run_id = uuid.uuid4().hex
        record = {"id": run_id, "job_id": job_id, "status": "queued", "task_id": task["id"], "method": task["method"],
                  "owner": task.get("owner"), "event": task.get("event", ""), "kind": task.get("kind"),
                  "fingerprint": task.get("fingerprint"), "auto": auto, "queued": now(), "started": None,
                  "finished": None, "exchange_id": assistant_log.new_id(), "progress": None, "applied": False,
                  "options": {"model": options.model, "num_ctx": options.num_ctx, "num_predict": options.num_predict,
                              "timeout": options.timeout},
                  "_options": options, "_request": request or {}}
        self.runs[run_id] = record
        while len(self.runs) > KEEP_RUNS:
            oldest = next((key for key, value in self.runs.items() if value["status"] in FINISHED), None)
            if oldest is None: break
            del self.runs[oldest]
        return record

    def public(self, record: dict) -> dict:
        return {k: v for k, v in record.items() if not k.startswith("_")}

    def _queued_task(self, job_id: str, task_id: str) -> bool:
        return any(self.runs[r]["job_id"] == job_id and self.runs[r]["task_id"] == task_id for r in self.queue) or (
            self.active is not None and self.runs[self.active]["job_id"] == job_id and self.runs[self.active]["task_id"] == task_id)

    def start(self, job_id: str, request: AssistantRequest):
        """A run the developer asked for: first in the queue (now, when nothing runs)."""
        output = self.output(job_id)
        options = engine.Options(**request.model_dump(exclude={"task_id"}))
        options.validate()
        with self.lock:
            if self.closed:
                raise JobError("A backend leállítás alatt van.", 503)
            index = engine.prepare_workspace(output)
            task = next((t for t in index["tasks"] if t["id"] == request.task_id), None)
            if task is None: raise JobError("Nincs ilyen asszisztensfeladat.", 404)
            if task.get("pending_tables"):
                raise JobError("Előbb add meg a táblaszerkezeteket az Adatbázistáblák panelen: " + ", ".join(task["pending_tables"]), 409)
            if self._queued_task(job_id, task["id"]):
                raise JobError("Ez a végpont már a sorban van vagy éppen fut.", 409)
            # The shared settings follow the developer's last manual choice (the automatic runs use them).
            try:
                self.save_settings({"url": options.url, "model": options.model, "num_ctx": options.num_ctx,
                                    "num_predict": options.num_predict, "timeout": min(options.timeout, 3600),
                                    "attempts": options.attempts, "think": options.think})
            except (JobError, OSError, ValueError):
                pass
            record = self._record(job_id, task, auto=False, options=options,
                                  request={"project_context": request.project_context, "instructions": request.instructions})
            if self.active is None and not self.queue:
                record["status"] = "running"   # picked at once: the UI sees it running immediately
            self.queue.appendleft(record["id"])
            self.wake.notify_all()
            return self.public(record)

    def migration_completed(self, job_id: str) -> None:
        """JobManager callback: a module is ready, queue its triggers (if the settings allow)."""
        try:
            self.auto_start(job_id, reason="migration")
        except Exception as exc:  # never break the job manager
            LOG.warning("Automatikus AI-indítás kihagyva (%s): %s", job_id, exc)

    def auto_start(self, job_id: str, force: bool = False, reason: str = "manual") -> dict:
        """Queue every trigger of the module whose tables are known and which is not done yet."""
        settings = self.settings()
        if reason != "manual" and not settings["auto_run"]:
            return {"queued": 0, "reason": "Az automatikus AI-futás ki van kapcsolva."}
        if not settings["url"] or not settings["model"]:
            if reason == "manual":
                raise JobError("Előbb mentsd az AI-kapcsolatot (Ollama-cím és modell) a közös AI-beállításoknál.", 409)
            return {"queued": 0, "reason": "Nincs mentett AI-kapcsolat (Ollama-cím és modell)."}
        try:
            options = self.options_from_settings(settings)
        except MigrationError as exc:
            raise JobError(str(exc), 422) from exc
        output = self.output(job_id)
        if not (output / "analysis/backend-plan.json").is_file():
            return {"queued": 0, "reason": "Ehhez a feladathoz nem készült backend."}
        with self.lock:
            if self.closed:
                return {"queued": 0, "reason": "A backend leállítás alatt van."}
            index = engine.prepare_workspace(output)
            done = self._done_tasks(output)
            queued, waiting, skipped = [], [], []
            for task in index["tasks"]:
                if task["id"] in done or self._queued_task(job_id, task["id"]):
                    continue
                if task.get("pending_tables"):
                    waiting.append(task["method"]); continue
                key = (job_id, task["id"])
                if not force and self.auto_tried.get(key) == task.get("fingerprint"):
                    skipped.append(task["method"]); continue
                record = self._record(job_id, task, auto=True, options=options)
                self.queue.append(record["id"])
                queued.append(task["method"])
            if queued:
                self.wake.notify_all()
            return {"queued": len(queued), "methods": queued, "waiting_for_tables": waiting, "skipped": skipped}

    def stop(self, job_id: str | None, request: CancelRequest) -> dict:
        with self.lock:
            cancelled = None
            target = request.run_id or self.active
            if target and target in self.runs and (job_id is None or self.runs[target]["job_id"] == job_id):
                record = self.runs[target]
                if record["status"] == "queued":
                    self.queue.remove(target)
                    record.update(status="cancelled", finished=now(), error="Kivéve a sorból.")
                elif record["status"] == "running":
                    self.cancel_requested.add(target)
                cancelled = target
            removed = 0
            if request.clear_queue:
                if job_id:
                    removed = self._drop_queued(job_id, "A sort leállítottad.")
                else:
                    for run_id in list(self.queue):
                        self.runs[run_id].update(status="cancelled", finished=now(), error="A sort leállítottad.")
                    removed = len(self.queue); self.queue.clear()
            return {"cancelled": cancelled, "removed": removed}

    def _done_tasks(self, output: Path) -> set:
        """Tasks with an applied proposal (they no longer carry a placeholder, but keep their region)."""
        done = set()
        for file in (output / engine.AREA / "proposals").glob("*/proposal.json"):
            try:
                record = read_json(file)
            except (OSError, ValueError):
                continue
            if record.get("applied"):
                done.add(record.get("task_id"))
        return done

    def _work(self):
        while True:
            with self.lock:
                while not self.closed and not self.queue:
                    self.wake.wait(1.0)
                if self.closed:
                    return
                run_id = self.queue.popleft()
                record = self.runs[run_id]
                self.active = run_id
                record.update(status="running", started=now())
            try:
                self._run(record)
            except Exception as exc:  # a bug in one run never stops the queue
                LOG.exception("Java-asszisztens sor: váratlan hiba")
                with self.lock:
                    record.update(status="failed", finished=now(), error=str(exc) or type(exc).__name__)
            finally:
                with self.lock:
                    self.active = None
                    self.cancel_requested.discard(run_id)
                    self.wake.notify_all()

    def _run(self, record: dict) -> None:
        run_id, job_id = record["id"], record["job_id"]
        options: engine.Options = record["_options"]
        extra = record.get("_request") or {}
        options.project_context = extra.get("project_context", options.project_context)
        options.instructions = extra.get("instructions", options.instructions)
        exchange_id = record["exchange_id"]
        try:
            output = self.output(job_id)
        except JobError as exc:
            with self.lock:
                record.update(status="cancelled", finished=now(), error=str(exc))
            return

        def progress(snapshot: dict) -> None:
            with self.lock:
                record["progress"] = {**snapshot, "updated": time.time()}

        def cancel() -> bool:
            return self.closed or run_id in self.cancel_requested

        if record["auto"]:
            self.auto_tried[(job_id, record["task_id"])] = record.get("fingerprint")
        try:
            result = engine.propose(output, record["task_id"], options, Path(self.manager.settings.data_dir) / "assistant-cache",
                                    exchange_id=exchange_id, progress=progress, cancel=cancel)
        except engine.Cancelled as exc:
            with self.lock:
                record.update(status="cancelled", finished=now(), error=str(exc))
            return
        except Exception as exc:
            message = str(exc).strip() or type(exc).__name__
            logged = (assistant_log.folder(output) / (exchange_id + ".json")).is_file()
            if logged:
                # Readable without rebuilding the Angular UI: the page is served by this backend.
                message += f" Megnyitás böngészőben: /ai-log?job={job_id}&exchange={exchange_id}"
            LOG.error("Java-asszisztens %s: %s", run_id, message[:2000])
            with self.lock:
                record.update(status="failed", finished=now(), error=message[:3000], exchange_id=exchange_id if logged else None)
            return
        applied, apply_error = False, None
        if self.closed:
            with self.lock:
                record.update(status="interrupted", finished=now())
            return
        settings = self.settings()
        if (result.get("status") == "proposal" and result.get("apply_allowed") and settings["auto_apply"]
                and result.get("validation", {}).get("java_parse") in {"passed", "not_available"}):
            with self.lock:
                try:
                    engine.apply_proposal(output, result["id"], reviewed=True)
                    applied = True
                except MigrationError as exc:
                    apply_error = str(exc)
                    if "megváltozott" in apply_error and record["auto"] and not record.get("requeued"):
                        # The module changed meanwhile (an other run was applied, a table was saved): once more.
                        again = self._record(job_id, {"id": record["task_id"], "method": record["method"],
                                                      "owner": record["owner"], "event": record["event"],
                                                      "kind": record["kind"]}, auto=True, options=options)
                        again["requeued"] = True
                        self.queue.append(again["id"])
                        self.wake.notify_all()
        with self.lock:
            try:
                engine.refresh_archives(output)
            except Exception as exc:  # the ZIP is refreshed again by the next change
                LOG.warning("ZIP-frissítés: %s", exc)
            record.update(status="completed", finished=now(), result=result, applied=applied, apply_error=apply_error)

    # ---------------------------------------------------------------- reading

    def queue_state(self, job_id: str | None = None) -> dict:
        """The live view of the queue: the running run (with its progress), the queued ones, the last finished."""
        with self.lock:
            def view(record):
                item = self.public(record)
                item.pop("result", None)
                if record.get("result"):
                    item["proposal_id"] = record["result"].get("id")
                    item["proposal_status"] = record["result"].get("status")
                return item
            active = self.runs.get(self.active) if self.active else None
            queued = [view(self.runs[r]) for r in self.queue if job_id is None or self.runs[r]["job_id"] == job_id]
            finished = [view(r) for r in reversed(list(self.runs.values()))
                        if r["status"] in FINISHED and (job_id is None or r["job_id"] == job_id)][:30]
            settings = self.settings()
            return {"active": view(active) if active and (job_id is None or active["job_id"] == job_id) else None,
                    "busy_with_other_job": bool(active and job_id and active["job_id"] != job_id),
                    "queued": queued, "finished": finished,
                    "settings": {k: settings[k] for k in ("url", "model", "auto_run", "auto_apply", "num_ctx", "num_predict", "timeout")},
                    "server_time": time.time()}

    def auto_summary(self, job_id: str, index: dict, output: Path) -> dict:
        done = self._done_tasks(output)
        tasks = index.get("tasks", [])
        with self.lock:
            busy = {self.runs[r]["task_id"] for r in self.queue if self.runs[r]["job_id"] == job_id}
            if self.active and self.runs[self.active]["job_id"] == job_id:
                busy.add(self.runs[self.active]["task_id"])
            failed = {}
            for record in self.runs.values():
                if record["job_id"] == job_id and record["status"] == "failed":
                    failed[record["task_id"]] = record.get("error")
                elif record["job_id"] == job_id and record["status"] == "completed":
                    failed.pop(record["task_id"], None)
        return {"total": len(tasks) + len(done - {t["id"] for t in tasks}),
                "done": len(done),
                "waiting_for_tables": sum(1 for t in tasks if t.get("pending_tables") and t["id"] not in done),
                "in_queue": len(busy),
                "failed": sum(1 for t in tasks if t["id"] in failed and t["id"] not in busy and t["id"] not in done),
                "ready": sum(1 for t in tasks if not t.get("pending_tables") and t["id"] not in done and t["id"] not in busy)}

    def index(self, job_id: str):
        output = self.output(job_id)
        if not (output / "analysis/backend-plan.json").is_file():
            raise JobError("Ehhez a feladathoz nem készült backend.", 409)
        with self.lock:
            index_path = engine.safe_file(output, engine.AREA + "/index.json")
            index = read_json(index_path) if index_path.is_file() else {}
            if index.get("version") != engine.VERSION:
                index = engine.prepare_workspace(output)
            proposals = []
            for file in sorted((output / engine.AREA / "proposals").glob("*/proposal.json")):
                record = read_json(file)
                proposals.append({k: record.get(k) for k in ("id", "task_id", "method", "status", "model", "applied")})
            active = self.runs.get(self.active) if self.active else None
            active_run = ({key: active[key] for key in ('id', 'status', 'task_id', 'exchange_id')}
                          if active and active['job_id'] == job_id and active['status'] == 'running' else None)
            recent = next((run for run in reversed(list(self.runs.values())) if run['job_id'] == job_id and run['status'] != 'queued'), None)
            recent_run = ({key: recent[key] for key in ('id', 'status', 'task_id', 'exchange_id')} if recent else None)
            if recent_run is not None:
                if recent.get('error'): recent_run['error'] = recent['error']
                if recent.get('result'): recent_run['proposal_id'] = recent['result']['id']
            done = self._done_tasks(output)
            tasks = [{**t, "done": t["id"] in done} for t in index.get("tasks", [])]
            # The finished ones stay visible in the overview (the task list itself only offers what is left).
            done_tasks = []
            for task_id in sorted(done - {t["id"] for t in tasks}):
                packet_path = engine.safe_file(output, engine.AREA + "/tasks/" + task_id + ".json")
                try:
                    packet = read_json(packet_path)
                except (OSError, ValueError):
                    continue
                endpoint = packet.get("endpoint") or {}
                event = endpoint.get("event") or next((s.get("name") for s in packet.get("sources", [])
                                                       if s.get("kind") == "trigger" and s.get("owner") == endpoint.get("owner")), "")
                done_tasks.append({"id": task_id, "method": packet.get("method"), "kind": packet.get("kind"),
                                   "owner": endpoint.get("owner", endpoint.get("block")) or "", "event": event or "",
                                   "pending_tables": [], "tables": [], "blockers": [], "done": True})
            return {**index, "tasks": tasks, "done_tasks": done_tasks, "proposals": proposals, "input_bindings": input_bindings.sheet(output),
                    "active_run": active_run, "recent_run": recent_run,
                    "auto": self.auto_summary(job_id, index, output), "queue": self.queue_state(job_id)}

    def prompt(self, job_id: str, request: PromptRequest):
        output = self.output(job_id)
        options = engine.Options(num_ctx=request.num_ctx, num_predict=request.num_predict, data_access=request.data_access,
                                 instructions=request.instructions, project_context=request.project_context)
        with self.lock:
            # The preview re-indexes the task files; a running request is already visible in its AI log.
            if self.active and self.runs[self.active]["job_id"] == job_id and self.runs[self.active]["task_id"] == request.task_id:
                raise JobError("Futó AI-hívás közben nincs előnézet; az elküldött kérés az AI-naplóban látható.", 409)
            try:
                return engine.preview(output, request.task_id, options)
            except MigrationError as exc:
                raise JobError(str(exc), 409) from exc

    def exchanges(self, job_id: str, task_id: str | None = None):
        return {"exchanges": assistant_log.summaries(self.output(job_id), task_id)}

    def exchange(self, job_id: str, exchange_id: str):
        if not assistant_log.ID.fullmatch(exchange_id): raise JobError("Hibás AI-napló azonosító.", 422)
        try:
            value = assistant_log.read(self.output(job_id), exchange_id)
        except FileNotFoundError as exc:
            raise JobError("Nincs ilyen AI-napló (vagy már törlődött a régebbiek közül).", 404) from exc
        with self.lock:
            live = next((r for r in self.runs.values() if r.get("exchange_id") == exchange_id and r["status"] == "running"), None)
            if live and live.get("progress"):
                value["live"] = live["progress"]
        return value

    def schema(self, job_id: str):
        with self.lock:
            return schema_workbench.schema_sheet(self.output(job_id))

    def save_schema(self, job_id: str, request):
        # A running model keeps its prepared request; a proposal of an older schema is re-queued at apply time.
        with self.lock:
            output = self.output(job_id)
            try:
                sheet = schema_workbench.save_table(output, request.table, request.columns, request.primary_key,
                                                     request.revision, output.parent / "inputs/schema.json")
            except schema_workbench.SchemaConflict as exc:
                raise JobError(str(exc), 409) from exc
            engine.prepare_workspace(output)
            engine.refresh_archives(output)
        try:
            # The triggers this table unblocked start at once (when the automatic run is on).
            sheet = {**sheet, "auto": self.auto_start(job_id, reason="schema")}
        except JobError as exc:
            sheet = {**sheet, "auto": {"queued": 0, "reason": str(exc)}}
        return sheet

    def import_schema_csv(self, job_id: str, request):
        with self.lock:
            sheet = schema_workbench.schema_sheet(self.output(job_id))
            if sheet["revision"] != request.revision:
                raise JobError("A séma közben módosult. Frissítsd a modult a CSV beolvasása előtt.", 409)
            table = request.table.strip().upper()
            if table not in {row["table"] for row in sheet["tables"]}:
                raise JobError("A kiválasztott tábla nincs a modul táblái között.", 422)
            return schema_workbench.parse_table_csv(request.text, table)

    def export_schema(self, job_id: str):
        with self.lock:
            return schema_workbench.load_schema(self.output(job_id))[0]

    def bindings(self, job_id: str):
        with self.lock:
            return input_bindings.sheet(self.output(job_id))

    def save_bindings(self, job_id: str, request):
        with self.lock:
            if self.active and self.runs[self.active]["job_id"] == job_id:
                raise JobError("Ehhez a modulhoz Java-asszisztens fut; a bekötéseket a futás után mentsd.", 409)
            output = self.output(job_id)
            try:
                sheet = input_bindings.save(output, {"version": 1, "bindings": request.bindings},
                                             request.revision, output.parent / "inputs/config.json")
            except input_bindings.BindingConflict as exc:
                raise JobError(str(exc), 409) from exc
            engine.prepare_workspace(output)
            engine.refresh_archives(output)
            return sheet

    def state(self, job_id: str, run_id: str):
        self.output(job_id)
        with self.lock:
            run = self.runs.get(run_id)
            if not run or run["job_id"] != job_id:
                raise JobError("Nincs ilyen asszisztensfutás; újraindítás után a kész javaslatok a listában maradnak.", 404)
            value = self.public(run)
            if run["status"] == "queued":
                value["position"] = list(self.queue).index(run_id) + 1 if run_id in self.queue else None
            return value

    def proposal(self, job_id: str, proposal_id: str):
        if not engine.ID.fullmatch(proposal_id): raise JobError("Hibás javaslatazonosító.", 422)
        output = self.output(job_id)
        file = engine.safe_file(output, engine.AREA + "/proposals/" + proposal_id + "/proposal.json")
        if not file.is_file(): raise JobError("Nincs ilyen Java-javaslat.", 404)
        return read_json(file)

    def archive(self, job_id: str, proposal_id: str):
        self.proposal(job_id, proposal_id)
        output = self.output(job_id)
        archive = output.parent / ("java-" + proposal_id + ".zip")
        with self.lock:
            engine.proposal_archive(output, proposal_id, archive)
        return archive

    def apply(self, job_id: str, proposal_id: str):
        output = self.output(job_id)
        with self.lock:
            # Safe while another run works: the apply checks every file's hash; a later stale proposal is re-queued.
            result = engine.apply_proposal(output, proposal_id, reviewed=True)
            engine.refresh_archives(output)
            return result


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewed: Literal[True]


class TableSchemaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    table: str = Field(min_length=1, max_length=257)
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    columns: list[dict] = Field(min_length=1, max_length=1000)
    primary_key: list[str] = Field(default_factory=list, max_length=32)


class TableCsvRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str = Field(min_length=1, max_length=257)
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    text: str = Field(min_length=1, max_length=1_000_000)


class InputBindingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    bindings: dict[str, dict] = Field(max_length=2000)

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
import zipfile

from .file_io import atomic_json
from .models import MigrationOptions
from .settings import PROJECT_ROOT, Settings

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
# Waiting for a user decision (question.json): no process runs, nothing is lost on restart.
WAITING = {"needs_input"}
IDLE = TERMINAL | WAITING
BATCH_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")


class JobError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Windows exit codes of a process the operating system stopped (NTSTATUS), as an unsigned 32-bit value.
WINDOWS_CRASHES = {0xC00000FD: "verem-túlcsordulás (túl mélyen beágyazott forrás)", 0xC0000005: "memóriahozzáférési hiba",
                   0xC0000017: "elfogyott a memória", 0xC000013A: "a folyamatot megszakították (Ctrl+C)"}


def failure_message(log: str, code: int | None) -> str:
    """The error of a failed run for the job list: the worker's last HIBA line, else what the log and the exit code say."""
    if "HIBA: " in log:
        return log.rsplit("HIBA: ", 1)[-1].strip()
    lines = [line.strip() for line in log.splitlines() if line.strip()]
    exception = next((line for line in reversed(lines) if re.match(r"^[A-Za-z_][\w.]*(?:Error|Exception|Interrupt)\b", line)), None)
    if exception:
        return "Váratlan belső hiba: " + exception[:400] + " A napló vége a feladat Áttekintés fülén látható."
    if code is not None and (code & 0xFFFFFFFF) in WINDOWS_CRASHES:
        return "A generáló folyamat összeomlott: " + WINDOWS_CRASHES[code & 0xFFFFFFFF] + f" (kilépési kód 0x{code & 0xFFFFFFFF:08X})."
    if code is not None and code < 0:
        try:
            name = signal.Signals(-code).name
        except ValueError:
            name = "jelzés " + str(-code)
        reason = " (valószínűleg elfogyott a memória)" if name == "SIGKILL" else ""
        return f"A generáló folyamatot az operációs rendszer leállította: {name}{reason}."
    last = (" Utolsó naplósor: " + lines[-1][:300]) if lines else ""
    return f"A generálás hibával leállt (kilépési kód {code}).{last} A napló vége a feladat Áttekintés fülén látható."


def tail(path: Path, size: int = 32768) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - size))
            return stream.read(size).decode("utf-8", "replace")
    except OSError:
        return ""


class WorkLock:
    """OS releases this lock even after an unclean server shutdown."""
    def __init__(self, path: Path):
        self.file = path.open("a+b")
        self.file.seek(0)
        if path.stat().st_size == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("Egy másik FRM backend már használja ezt a munkamappát. Egy workerrel indítsd a szervert.") from None

    def close(self):
        self.file.close()


class JobManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.data_dir / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.disk_lock = WorkLock(settings.data_dir / "server.lock")
        self.lock = threading.RLock()
        self.jobs: dict[str, dict] = {}
        self.queue: queue.Queue[str | None] = queue.Queue()
        self.closing = threading.Event()
        self.process: subprocess.Popen | None = None
        # Called with the job id after a successful migration (the Java assistant queues its triggers).
        self.on_completed = None
        self.thread = threading.Thread(target=self._work, daemon=True, name="frm-migration-worker")
        for path in self.root.glob("*/job.json"):
            if not re.fullmatch(r"[0-9a-f]{32}", path.parent.name):
                continue
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if job.get("id") != path.parent.name:
                    continue
                if job["status"] not in IDLE:
                    job.update(status="interrupted", phase="interrupted", finished_at=now(), error="A backend leállt a generálás befejezése előtt. A feladat újrafuttatható.")
                    atomic_json(path, job)
                self.jobs[job["id"]] = job
            except (OSError, ValueError, KeyError):
                continue
        self.thread.start()

    def close(self):
        self.closing.set()
        self.queue.put(None)
        self.thread.join(timeout=12)
        if self.thread.is_alive():
            raise RuntimeError("A migrációs worker nem állt le időben; a munkamappa zárolva marad.")
        with self.lock:
            for job in self.jobs.values():
                if job["status"] not in IDLE:
                    self._update(job, status="interrupted", phase="interrupted", finished_at=now(), error="A backend leállt. A feladat újrafuttatható.")
        self.disk_lock.close()

    def _update(self, job: dict, **values):
        with self.lock:
            job.update(values)
            atomic_json(self.root / job["id"] / "job.json", job)

    def public(self, job: dict) -> dict:
        return {k: v for k, v in job.items() if k not in {"cancel_requested"}}

    def get(self, job_id: str) -> dict:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise JobError("A feladat nem található.", 404)
            return self.public(job)

    def list(self) -> list[dict]:
        with self.lock:
            return [self.public(j) for j in sorted(self.jobs.values(), key=lambda j: j["created_at"], reverse=True)]

    def submit(self, incoming: Path, filename: str, options: MigrationOptions, config: dict, batch: str | None = None) -> dict:
        with self.lock:
            if self.closing.is_set():
                raise JobError("A backend leállítás alatt van.", 503)
            if batch is not None and not BATCH_ID.fullmatch(batch):
                raise JobError("Hibás tömeges futtatás-azonosító.", 422)
            if sum(j["status"] not in IDLE for j in self.jobs.values()) >= self.settings.max_pending:
                raise JobError("A feladatsor megtelt. Várd meg egy generálás végét.", 429)
            job_id = uuid.uuid4().hex
            destination = self.root / job_id
            destination.mkdir()
            try:
                incoming.rename(destination / "inputs")
                atomic_json(destination / "inputs" / "config.json", config)
                job = {"id": job_id, "filename": filename, "source_type": Path(filename).suffix.lower(),
                       "options": options.model_dump(), "status": "queued", "phase": "queued", "created_at": now(),
                       "started_at": None, "finished_at": None, "error": None, "summary": None, "review_required": False,
                       "cancel_requested": False, "exit_code": None, "module": options.module, "files_count": 0,
                       "batch": batch, "question": None}
                self._update(job)
                self.jobs[job_id] = job
                self.queue.put(job_id)
                return self.public(job)
            except Exception:
                shutil.rmtree(destination, ignore_errors=True)
                raise

    def cancel(self, job_id: str) -> dict:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise JobError("A feladat nem található.", 404)
            if job["status"] in TERMINAL:
                return self.public(job)
            if job["status"] in {"queued", *WAITING}:
                self._update(job, cancel_requested=True, status="cancelled", phase="cancelled", finished_at=now())
            else:
                self._update(job, cancel_requested=True, phase="cancelling")
            return self.public(job)

    def delete(self, job_id: str):
        with self.lock:
            job = self.get(job_id)
            if job["status"] not in IDLE:
                raise JobError("Előbb állítsd le a futó vagy várakozó feladatot.", 409)
            shutil.rmtree(self.root / job_id)
            del self.jobs[job_id]

    def retry(self, job_id: str) -> dict:
        with self.lock:
            job = self.get(job_id)
            if job["status"] in WAITING:
                raise JobError("A feladat döntésre vár: válaszd ki a fő képernyőt.", 409)
            if job["status"] not in TERMINAL:
                raise JobError("A feladat még folyamatban van.", 409)
            source = self.root / job_id / "inputs"
            incoming = self.settings.data_dir / ("retry-" + uuid.uuid4().hex)
            try:
                shutil.copytree(source, incoming)
                options = MigrationOptions.model_validate(job["options"])
                # Use current trusted exporter settings, so a repaired Oracle
                # environment can retry an old FMB without replaying stale paths.
                config = {**self.settings.engine_config, **options.engine_overrides()}
                # Keep the reviewed decisions belonging to this job, while
                # exporter/connection settings still come from current config.
                previous = json.loads((source / 'config.json').read_text(encoding='utf-8'))
                for key in ('screen_overrides', 'screen_input_bindings'):
                    if key in previous:
                        config[key] = previous[key]
                return self.submit(incoming, job["filename"], options, config, job.get("batch"))
            finally:
                shutil.rmtree(incoming, ignore_errors=True)

    def answer(self, job_id: str, window: str | None = None, windows: list[str] | None = None) -> dict:
        """Continue a job waiting for a decision: the main window, or the windows to generate."""
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise JobError("A feladat nem található.", 404)
            if job["status"] != "needs_input" or not job.get("question"):
                raise JobError("A feladat nem vár döntésre.", 409)
            names = {c["name"] for c in job["question"].get("choices", [])}
            if job["question"].get("kind") == "windows":
                if not windows or any(w not in names for w in windows):
                    raise JobError("Legalább egy, a lehetőségek között szereplő ablakot válassz.", 422)
                key, value = "screen_windows", list(dict.fromkeys(windows))
            else:
                if window not in names:
                    raise JobError("A választott ablak nem szerepel a lehetőségek között.", 422)
                key, value = "screen_primary_window", window
            folder = self.root / job_id
            config = json.loads((folder / "inputs" / "config.json").read_text(encoding="utf-8"))
            config[key] = value
            atomic_json(folder / "inputs" / "config.json", config)
            (folder / "question.json").unlink(missing_ok=True)
            options = dict(job["options"], **{key: value})
            self._update(job, options=options, status="queued", phase="queued", question=None, error=None,
                         started_at=None, finished_at=None, exit_code=None)
            self.queue.put(job_id)
            return self.public(job)

    def preview_path(self, job_id: str) -> Path:
        job = self.get(job_id)
        if job["status"] != "completed":
            raise JobError("Az előnézet a generálás befejezése után érhető el.", 409)
        path = self.root / job_id / "module" / "analysis" / "screen-preview.html"
        if not path.is_file():
            raise JobError("Ehhez a feladathoz nem készült képernyő-előnézet.", 404)
        return path

    def helper_path(self, job_id: str, name: str) -> Path:
        """A shared helper of a generated module (CommonMigrateTools.java, wf-table.ts)."""
        job = self.get(job_id)
        if job["status"] != "completed":
            raise JobError("A segédfájl a generálás befejezése után tölthető le.", 409)
        relative = {"CommonMigrateTools.java": "backend/CL/CommonMigrateTools.java",
                    "wf-table.ts": "frontend/wf-table.ts"}[name]
        path = self.root / job_id / "module" / relative
        if not path.is_file():
            raise JobError("Ehhez a feladathoz nem készült " + name + ".", 404)
        return path

    def batch_jobs(self, batch: str) -> list[dict]:
        if not BATCH_ID.fullmatch(batch or ""):
            raise JobError("Hibás tömeges futtatás-azonosító.", 422)
        with self.lock:
            jobs = [j for j in self.jobs.values() if j.get("batch") == batch]
        if not jobs:
            raise JobError("A tömeges futtatás nem található.", 404)
        return sorted(jobs, key=lambda j: (j["created_at"], j["filename"]))

    def batch_report(self, batch: str) -> dict:
        """Portfolio of one batch: the ranked blockers over its completed jobs."""
        from frm_forms.portfolio import aggregate, markdown
        from frm_forms import framework
        jobs = self.batch_jobs(batch)
        entries = [{"folder": j["id"] + "/module", "input": j["filename"],
                    "status": "ok" if j["status"] == "completed" else "failed",
                    "error": None if j["status"] == "completed" else (j.get("error") or self._state_text(j))}
                   for j in jobs if j["status"] in IDLE]
        report = aggregate(self.root, entries, framework.load({})["call_prefixes"])
        counts = {state: sum(j["status"] == state for j in jobs)
                  for state in ("queued", "running", "needs_input", "completed", "failed", "cancelled", "interrupted")}
        return {"batch": batch, "total": len(jobs), "counts": counts, "done": all(j["status"] in IDLE for j in jobs),
                "jobs": [{"id": j["id"], "filename": j["filename"], "status": j["status"], "module": j.get("module")} for j in jobs],
                "report": report, "markdown": markdown(report)}

    def batch_survey(self, batch: str, names: bool = False) -> dict:
        """Survey of one batch: what disables the endpoints, with anonymized code shapes."""
        from frm_forms import survey
        entries = [{"folder": j["id"] + "/module", "input": j["filename"],
                    "status": "ok" if j["status"] == "completed" else "failed",
                    "error": None if j["status"] == "completed" else (j.get("error") or self._state_text(j))}
                   for j in self.batch_jobs(batch) if j["status"] in IDLE]
        report = survey.collect(self.root, entries, names=names)
        return {"batch": batch, "report": report, "markdown": survey.markdown(report)}

    def batch_archive(self, batch: str) -> tuple[Path, str]:
        """One ZIP: every completed module package plus the portfolio report and the survey."""
        summary = self.batch_report(batch)
        felmeres = self.batch_survey(batch)
        folder = self.settings.data_dir / "batches"
        folder.mkdir(exist_ok=True)
        path = folder / (batch + ".zip")
        used = set()
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("PORTFOLIO_HU.md", summary["markdown"])
            archive.writestr("portfolio.json", json.dumps(summary["report"], ensure_ascii=False, indent=2))
            archive.writestr("FELMERES_HU.md", felmeres["markdown"])
            archive.writestr("felmeres.json", json.dumps(felmeres["report"], ensure_ascii=False, indent=2))
            for job in self.batch_jobs(batch):
                module_zip = self.root / job["id"] / "module.zip"
                if job["status"] != "completed" or not module_zip.is_file():
                    continue
                name = (job.get("download_name") or job.get("module") or Path(job["filename"]).stem) + ".zip"
                while name.lower() in used:
                    name = name[:-4] + "_" + job["id"][:6] + ".zip"
                used.add(name.lower())
                archive.write(module_zip, "modulok/" + name)
        return path, "tomeges-" + batch + ".zip"

    def deploy(self, outputs: list[Path], project: str | None, layout: dict, dry_run: bool, force: bool) -> dict:
        """Generated files into the developer's project (frm_forms.project_deploy), on this machine.

        project: the main project folder (optional); layout: the chosen folder of each part (full paths)."""
        from frm_forms.common import MigrationError
        from frm_forms.project_deploy import deploy, map_project, mapped_folders, markdown, validate_layout
        folder = Path(project).expanduser() if project else None
        if folder is not None and not folder.is_absolute():
            raise JobError("A projektmappát teljes útvonallal add meg (például C:\\projektek\\rendszer).", 422)
        allowed = [Path(root).expanduser().resolve() for root in self.settings.project_roots]
        if allowed and folder is not None and not any(folder.resolve().is_relative_to(root) for root in allowed):
            raise JobError("A projektmappa nincs az engedélyezett mappák között (FRM_PROJECT_ROOTS).", 403)
        try:
            layout = validate_layout(layout) if layout else {}
            mapped = map_project(folder, layout)
        except MigrationError as exc:
            raise JobError(str(exc), 422) from exc
        # the folders really written: a chosen package folder means its src/main/java
        if allowed and not all(any(path.resolve().is_relative_to(root) for root in allowed) for path in mapped_folders(mapped)):
            raise JobError("Egy rész mappája nincs az engedélyezett mappák között (FRM_PROJECT_ROOTS).", 403)
        try:
            report = deploy(outputs, folder, layout, dry_run=dry_run, force=force, mapped=mapped)
        except MigrationError as exc:
            raise JobError(str(exc), 422) from exc
        for output in outputs:
            own = dict(report, files=[f for f in report["files"] if f["output"] == str(output)])
            atomic_json(output / "analysis" / "project-deploy.json", own)
        for entry in report["files"]:
            entry.pop("output", None)
        return {**report, "markdown": markdown(report)}

    def job_deploy(self, job_id: str, project: str | None, layout: dict, dry_run: bool, force: bool) -> dict:
        job = self.get(job_id)
        if job["status"] != "completed":
            raise JobError("A telepítés a generálás befejezése után érhető el.", 409)
        return self.deploy([self.root / job_id / "module"], project, layout, dry_run, force)

    def batch_deploy(self, batch: str, project: str | None, layout: dict, dry_run: bool, force: bool) -> dict:
        outputs = [self.root / j["id"] / "module" for j in self.batch_jobs(batch) if j["status"] == "completed"]
        if not outputs:
            raise JobError("A tömeges futtatásnak még nincs befejezett modulja.", 409)
        return self.deploy(outputs, project, layout, dry_run, force)

    @staticmethod
    def _state_text(job: dict) -> str:
        return {"needs_input": "Döntésre vár: a fő képernyő kiválasztása.", "queued": "Várakozik.", "running": "Folyamatban."}.get(job["status"], job["status"])

    def clear_cache(self) -> dict:
        with self.lock:
            if any(j["status"] not in IDLE for j in self.jobs.values()):
                raise JobError("A cache csak üres feladatsor mellett törölhető.", 409)
            cache = self.settings.data_dir / "cache"
            count = len(list(cache.glob("*.json"))) if cache.exists() else 0
            shutil.rmtree(cache, ignore_errors=True)
            return {"deleted_entries": count}

    def files(self, job_id: str) -> list[dict]:
        job = self.get(job_id)
        if job["status"] != "completed":
            raise JobError("A forráskód a generálás befejezése után érhető el.", 409)
        manifest = self.root / job_id / "module" / "generated-files.json"
        return json.loads(manifest.read_text(encoding="utf-8"))["files"]

    def source_file(self, job_id: str, relative: str) -> Path:
        allowed = {f["path"] for f in self.files(job_id)}
        if relative not in allowed:
            raise JobError("A kért fájl nem része a generált forrásnak.", 404)
        root = (self.root / job_id / "module").resolve()
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise JobError("A fájl nem található.", 404)
        return candidate

    def archive(self, job_id: str, kind: str) -> tuple[Path, str]:
        job = self.get(job_id)
        if job["status"] != "completed":
            raise JobError("A letöltés a generálás befejezése után érhető el.", 409)
        paths = {"all": "module.zip", "frontend": "frontend.zip", "backend": "backend.zip"}
        if kind not in paths:
            raise JobError("Ismeretlen csomagtípus.", 404)
        path = self.root / job_id / paths[kind]
        if not path.is_file():
            raise JobError("A ZIP már nem található.", 404)
        return path, (job.get("download_name") or job["module"]) + ("" if kind == "all" else "-" + kind) + ".zip"

    def _stop_process(self, process: subprocess.Popen):
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            else:
                os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def _work(self):
        while not self.closing.is_set():
            job_id = self.queue.get()
            if job_id is None:
                return
            with self.lock:
                job = self.jobs.get(job_id)
                if job is None or job["status"] != "queued":
                    continue
                self._update(job, status="running", phase="starting", started_at=now())
            folder = self.root / job_id
            process = None
            try:
                env = dict(os.environ)
                env.update(FRM_OLLAMA_URL=job["options"].get("ollama_url") or self.settings.ollama_url, FRM_OLLAMA_MODEL=job["options"]["ollama_model"], PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
                env["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
                with (folder / "worker.log").open("wb") as log:
                    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
                    process = subprocess.Popen([sys.executable, "-m", "frm_forms.web.worker", str(folder)], cwd=PROJECT_ROOT,
                                               env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **kw)
                    self.process = process
                    start = time.monotonic()
                    while process.poll() is None:
                        if self.closing.is_set() or job["cancel_requested"]:
                            self._stop_process(process)
                            break
                        if time.monotonic() - start > self.settings.job_timeout_seconds:
                            self._stop_process(process)
                            raise JobError(f"A generálás túllépte a {self.settings.job_timeout_seconds} másodperces futásidőkorlátot.")
                        try:
                            phase = json.loads((folder / "progress.json").read_text())["phase"]
                            if phase != job["phase"] and not job["cancel_requested"]:
                                self._update(job, phase=phase)
                        except (OSError, ValueError, KeyError):
                            pass
                        self.closing.wait(0.2)
                if self.closing.is_set():
                    self._update(job, status="interrupted", phase="interrupted", finished_at=now(), error="A backend leállt. A feladat újrafuttatható.")
                elif job["cancel_requested"]:
                    self._update(job, status="cancelled", phase="cancelled", finished_at=now())
                elif process.returncode == 4 and (folder / "question.json").is_file():
                    # The run stopped for a decision (e.g. several main windows): ask, do not fail.
                    question = json.loads((folder / "question.json").read_text(encoding="utf-8"))
                    self._update(job, status="needs_input", phase="needs_input", question=question, exit_code=4)
                elif process.returncode not in {0, 3}:
                    message = failure_message(tail(folder / "worker.log", 6000), process.returncode)
                    self._update(job, status="failed", phase="failed", finished_at=now(), error=message, exit_code=process.returncode)
                else:
                    self._update(job, phase="packaging")
                    for kind in ("frontend", "backend"):
                        with zipfile.ZipFile(folder / (kind + ".zip"), "w", zipfile.ZIP_DEFLATED) as archive:
                            for file in sorted((folder / "module" / kind).rglob("*")):
                                if self.closing.is_set() or job["cancel_requested"]:
                                    raise JobError("A csomagolás megszakadt.")
                                if file.is_file():
                                    entry = zipfile.ZipInfo(file.relative_to(folder / "module").as_posix(), (2020, 1, 1, 0, 0, 0))
                                    entry.compress_type = zipfile.ZIP_DEFLATED
                                    entry.external_attr = 0o100644 << 16
                                    archive.writestr(entry, file.read_bytes())
                    summary = json.loads((folder / "module" / "analysis" / "summary.json").read_text(encoding="utf-8"))
                    config = json.loads((folder / "module" / "analysis" / "effective-config.json").read_text(encoding="utf-8"))
                    ir = json.loads((folder / "module" / "analysis" / "form.ir.json").read_text(encoding="utf-8"))
                    manifest = json.loads((folder / "module" / "generated-files.json").read_text(encoding="utf-8"))
                    review = bool(summary["blocking_issues"] or summary.get("frontend_review_issues") or summary.get("frontend_error_issues") or any(b["write_blockers"] for b in ir["blocks"] if b["database"]))
                    with self.lock:
                        if job["cancel_requested"] or self.closing.is_set():
                            raise JobError("A generálás megszakadt.")
                        self._update(job, status="completed", phase="completed", finished_at=now(), summary=summary, review_required=review,
                                     module=config["module"], download_name=json.loads((folder / "module/analysis/ui-model.json").read_text(encoding="utf-8"))["module"]["key"], exit_code=process.returncode, files_count=len(manifest["files"]))
                    if self.on_completed is not None:
                        try:
                            self.on_completed(job_id)
                        except Exception:  # the job is done either way; the assistant logs its own problems
                            pass
            except Exception as exc:
                if process is not None:
                    self._stop_process(process)
                state = "interrupted" if self.closing.is_set() else "cancelled" if job["cancel_requested"] else "failed"
                self._update(job, status=state, phase=state, finished_at=now(), error=str(exc))
            finally:
                self.process = None

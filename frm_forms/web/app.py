from __future__ import annotations

from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
import platform
import shutil
import tempfile
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from frm_forms import __version__
from frm_forms.common import MigrationError, RESERVED
from frm_forms.contracts import validate_company_config
from frm_forms.screen_overrides import validate as validate_screen_overrides
from frm_forms.ui_config import validate_field_lengths
from .folders import FolderError, FolderPicker, list_folders
from .jobs import JobError, JobManager, tail
from .models import DeployRequest, FolderRequest, JobAnswer, MigrationOptions, OllamaConnection, PickRequest
from .settings import PROJECT_ROOT, Settings
from .assistant import (AssistantManager, AssistantRequest, ApplyRequest, PromptRequest, TableSchemaRequest,
                        TableCsvRequest, InputBindingsRequest, AutoRequest, AutoSettings, CancelRequest)

AI_LOG_PAGE = Path(__file__).resolve().parents[1] / "data" / "ai-log.html"


class BodyTooLarge(Exception):
    pass


class DiagnosedCORSMiddleware(CORSMiddleware):
    def preflight_response(self, request_headers):
        response = super().preflight_response(request_headers)
        if response.status_code == 400:
            # Only fixed middleware reason codes: never log tokens or header values.
            logging.getLogger('uvicorn.error').warning(
                'CORS preflight: %s. Ellenőrzés: FRM_CORS_ORIGINS / FRM_CORS_HEADERS; aktív beállítások: /api/defaults.',
                response.body.decode('utf-8', errors='replace'))
        return response


class LocalRequestGuard:
    """Bound request bodies and require an explicit header for local mutations.

    CORS alone does not prevent a hostile page from submitting a simple form.
    The custom header forces browsers to preflight before sending a mutation.
    This is a local single-user adapter, not internet-facing authentication.
    """
    def __init__(self, app, origins: list[str], max_body: int):
        self.app, self.origins, self.max_body = app, set(origins), max_body

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        origin = headers.get(b"origin", b"").decode("latin-1")
        error, status = None, 400
        if origin and origin not in self.origins:
            error, status = "Ez az origin nem férhet hozzá a helyi backendhez.", 403
        elif scope["method"] in {"POST", "PUT", "PATCH", "DELETE"} and headers.get(b"x-frm-client") != b"local-ui":
            error, status = "A módosító kéréshez X-Frm-Client: local-ui fejléc szükséges.", 403
        if b"content-length" in headers:
            try:
                size = int(headers[b"content-length"])
                if size < 0:
                    raise ValueError
                if size > self.max_body:
                    error, status = "A feltöltés meghaladja a méretkorlátot.", 413
            except ValueError:
                error, status = "Hibás Content-Length.", 400
        if error:
            return await JSONResponse({"detail": error}, status_code=status)(scope, receive, send)
        count = 0
        async def bounded_receive():
            nonlocal count
            message = await receive()
            if message["type"] == "http.request":
                count += len(message.get("body", b""))
                if count > self.max_body:
                    raise BodyTooLarge
            return message
        try:
            await self.app(scope, bounded_receive, send)
        except BodyTooLarge:
            await JSONResponse({"detail": "A feltöltés meghaladja a méretkorlátot."}, status_code=413)(scope, receive, send)


async def save_upload(upload: UploadFile, path: Path, limit: int):
    size = 0
    with path.open("wb") as stream:
        while chunk := await upload.read(512 * 1024):
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, f"A(z) {upload.filename} fájl túl nagy.")
            stream.write(chunk)
    if size == 0:
        raise HTTPException(400, "Üres fájl nem tölthető fel.")


def validate_json_file(path: Path, root_key: str, extra_keys: frozenset = frozenset()):
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        allowed = {root_key, *extra_keys}
        if not isinstance(value, dict) or set(value) - allowed or not all(isinstance(value.get(k, {}), dict) for k in allowed):
            raise ValueError("Kizárólag " + ", ".join(repr(k) for k in sorted(allowed)) + " gyökérkulcsú JSON objektum szükséges.")
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(400, f"{path.name}: {exc}") from exc


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        manager = JobManager(settings)
        app.state.manager = manager
        app.state.assistant = AssistantManager(manager)
        try:
            yield
        finally:
            app.state.assistant.close()
            picker.cancel()  # an open folder dialog does not outlive the server
            manager.close()

    app = FastAPI(title="FRM local migration API", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url="/api/openapi.json")
    picker = FolderPicker(settings.folder_dialog_command or None)

    @app.exception_handler(JobError)
    @app.exception_handler(FolderError)
    async def job_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        messages = [".".join(str(p) for p in error["loc"]) + ": " + error["msg"] for error in exc.errors()]
        return JSONResponse({"detail": "; ".join(messages)}, status_code=422)

    @app.exception_handler(MigrationError)
    async def migration_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    def manager() -> JobManager:
        return app.state.manager

    @app.get("/api/health")
    def health():
        jobs = manager().list()
        return {"status": "ok", "version": __version__, "python": platform.python_version(),
                "exporter": settings.exporter(), "active_jobs": sum(j["status"] == "running" for j in jobs),
                "queued_jobs": sum(j["status"] == "queued" for j in jobs), "local_only": True}

    @app.get("/api/defaults")
    def defaults():
        return {"options": settings.defaults().model_dump(), "ollama_url": settings.ollama_url,
                "limits": {"file_bytes": settings.max_upload_bytes, "json_bytes": settings.max_json_bytes,
                           "pending_jobs": settings.max_pending, "job_timeout_seconds": settings.job_timeout_seconds},
                "cors_origins": settings.cors_origins,
                "cors_headers": settings.cors_headers,
                "cors_allow_credentials": settings.cors_allow_credentials,
                "folders": {"dialog": settings.folder_dialog, "limited": bool(settings.project_roots)},
                "client_contract": {"mutation_header": {"X-Frm-Client": "local-ui"},
                                    "authorization": "accepted_but_not_validated",
                                    "response_format": "plain_json"}}

    @app.post("/api/ollama/check")
    def check_ollama(request: OllamaConnection):
        from frm_forms.ollama import check_connection
        result = check_connection(request.url, request.model)
        # The automatic Java-assistant runs (after a migration) use the last checked connection.
        try:
            app.state.assistant.save_settings({"url": result["url"], "model": request.model})
        except (JobError, OSError, ValueError):
            pass
        return result

    # The shared AI settings of the automatic runs: auto_run (start after a migration), auto_apply, limits.
    @app.get("/api/assistant/settings")
    def assistant_settings():
        return app.state.assistant.settings()

    @app.post("/api/assistant/settings")
    def assistant_save_settings(request: AutoSettings):
        return app.state.assistant.save_settings(request.model_dump())

    # The queue of every module: the running request with its live progress, the waiting ones, the finished.
    @app.get("/api/assistant/queue")
    def assistant_queue():
        return app.state.assistant.queue_state()

    @app.post("/api/assistant/cancel")
    def assistant_cancel(request: CancelRequest):
        return app.state.assistant.stop(None, request)

    @app.get("/api/jobs")
    def jobs():
        return {"jobs": manager().list()}

    @app.post("/api/jobs", status_code=202)
    async def create_job(file: Annotated[UploadFile, File()], options: Annotated[str, Form()] = "{}",
                         schema_file: Annotated[UploadFile | None, File()] = None,
                         rules_file: Annotated[UploadFile | None, File()] = None,
                         screen_overrides_file: Annotated[UploadFile | None, File()] = None,
                         field_lengths_file: Annotated[UploadFile | None, File()] = None,
                         olb_files: Annotated[list[UploadFile] | None, File()] = None,
                         mmb_file: Annotated[UploadFile | None, File()] = None,
                         pld_files: Annotated[list[UploadFile] | None, File()] = None,
                         batch: Annotated[str | None, Form(max_length=40)] = None):
        filename = (file.filename or "").replace("\\", "/").split("/")[-1]
        suffix = Path(filename).suffix.lower()
        if suffix not in {".fmb", ".xml"} or len(filename) > 160:
            raise HTTPException(400, "Legfeljebb 160 karakteres nevű .fmb vagy Forms2XML .xml fájl szükséges.")
        if len(options.encode("utf-8")) > 16384:
            raise HTTPException(400, "Túl nagy beállításobjektum.")
        try:
            data = json.loads(options)
            if not isinstance(data, dict):
                raise ValueError("A beállítások JSON objektumként szükségesek.")
            selected = MigrationOptions.model_validate({**settings.defaults().model_dump(), **data})
            if any(part in RESERVED for part in selected.java_package.split(".")):
                raise ValueError("A Java package fenntartott szót tartalmaz.")
            validate_company_config(selected.engine_overrides())
            if selected.project_layout:
                # The chosen folders must exist and give the packages; checked now, not when the job runs.
                from frm_forms.project_deploy import layout_packages
                layout_packages(selected.project_layout)
                allowed = [Path(root).expanduser().resolve() for root in settings.project_roots]
                if allowed and not all(any(Path(folder).expanduser().resolve().is_relative_to(root) for root in allowed)
                                       for folder in selected.project_layout.values()):
                    raise HTTPException(403, "Egy célmappa nincs az engedélyezett mappák között (FRM_PROJECT_ROOTS).")
        except (ValueError, ValidationError, MigrationError) as exc:
            raise HTTPException(422, str(exc)) from exc
        incoming = Path(tempfile.mkdtemp(prefix="upload-", dir=settings.data_dir))
        try:
            await save_upload(file, incoming / ("input" + suffix), settings.max_upload_bytes)
            if schema_file:
                await save_upload(schema_file, incoming / "schema.json", settings.max_json_bytes)
                # dictionary-import output: data dictionary tables and routine signatures.
                validate_json_file(incoming / "schema.json", "blocks", frozenset({"procedures", "tables"}))
            if rules_file:
                await save_upload(rules_file, incoming / "rules.json", settings.max_json_bytes)
                validate_json_file(incoming / "rules.json", "replacements")
            overrides = None
            if screen_overrides_file:
                if selected.generation_mode != 'screen':
                    raise HTTPException(422, 'Képernyő-felülbírálás csak képernyőváz módban használható.')
                await save_upload(screen_overrides_file, incoming / 'screen-overrides.json', settings.max_json_bytes)
                try:
                    overrides = json.loads((incoming / 'screen-overrides.json').read_text(encoding='utf-8'))
                    validate_screen_overrides(overrides, selected.layout_columns)
                except (ValueError, MigrationError) as exc:
                    raise HTTPException(422, str(exc)) from exc
            lengths = None
            if field_lengths_file:
                await save_upload(field_lengths_file, incoming / 'field-lengths.json', settings.max_json_bytes)
                try:
                    lengths = json.loads((incoming / 'field-lengths.json').read_text(encoding='utf-8-sig'))
                    validate_field_lengths(lengths)
                except (ValueError, MigrationError) as exc:
                    raise HTTPException(422, str(exc)) from exc
            companions = [(upload, "olb") for upload in (olb_files or [])]
            if mmb_file:
                companions.append((mmb_file, "mmb"))
            if len(companions) > 32:
                raise HTTPException(400, "Legfeljebb 32 kísérő XML tölthető fel.")
            seen = set()
            for upload, kind in companions:
                original = upload.filename or ""
                basename = original.replace("\\", "/").split("/")[-1]
                if basename != original or not basename.lower().endswith("_" + kind + ".xml") or len(basename) > 160:
                    raise HTTPException(400, "Őrizd meg az export fájlnevét: <név>_" + kind + ".xml; útvonal nem adható meg.")
                if basename.casefold() in seen:
                    raise HTTPException(400, "Ismétlődő kísérő fájlnév: " + basename)
                seen.add(basename.casefold())
                folder = incoming / kind
                folder.mkdir(exist_ok=True)
                await save_upload(upload, folder / basename, settings.max_upload_bytes)
            # Attached PL/SQL libraries as text (.pld) or binary (.pll, converted with frmcmp): libraries.py.
            if len(pld_files or []) > 32:
                raise HTTPException(400, "Legfeljebb 32 PL/SQL-könyvtár tölthető fel.")
            for upload in pld_files or []:
                original = upload.filename or ""
                basename = original.replace("\\", "/").split("/")[-1]
                if basename != original or Path(basename).suffix.lower() not in {".pld", ".pll"} or len(basename) > 160:
                    raise HTTPException(400, "PL/SQL-könyvtár: <könyvtárnév>.pld vagy .pll fájlnév kell; útvonal nem adható meg.")
                if basename.casefold() in seen:
                    raise HTTPException(400, "Ismétlődő kísérő fájlnév: " + basename)
                seen.add(basename.casefold())
                (incoming / "pld").mkdir(exist_ok=True)
                await save_upload(upload, incoming / "pld" / basename, settings.max_upload_bytes)
            config = {**settings.engine_config, **selected.engine_overrides()}
            if overrides is not None: config['screen_overrides'] = overrides
            if lengths is not None: config['screen_field_lengths'] = lengths
            return manager().submit(incoming, filename, selected, config, batch or None)
        finally:
            shutil.rmtree(incoming, ignore_errors=True)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return manager().get(job_id)

    @app.post("/api/jobs/{job_id}/cancel", status_code=202)
    def cancel_job(job_id: str):
        return manager().cancel(job_id)

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    def retry_job(job_id: str):
        with app.state.assistant.lock:
            app.state.assistant.require_idle(job_id)
            return manager().retry(job_id)

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str):
        with app.state.assistant.lock:
            app.state.assistant.require_idle(job_id)
            manager().delete(job_id)
        return {"deleted": True}

    @app.delete("/api/cache")
    def clear_cache():
        return manager().clear_cache()

    @app.get("/api/jobs/{job_id}/files")
    def job_files(job_id: str):
        return {"files": manager().files(job_id)}

    @app.get("/api/jobs/{job_id}/assistant")
    def assistant_index(job_id: str):
        return app.state.assistant.index(job_id)

    @app.post("/api/jobs/{job_id}/assistant", status_code=202)
    def assistant_start(job_id: str, request: AssistantRequest):
        return app.state.assistant.start(job_id, request)

    @app.post("/api/jobs/{job_id}/assistant/auto", status_code=202)
    def assistant_auto(job_id: str, request: AutoRequest):
        return app.state.assistant.auto_start(job_id, force=request.force)

    @app.get("/api/jobs/{job_id}/assistant/queue")
    def assistant_job_queue(job_id: str):
        app.state.assistant.output(job_id)
        return app.state.assistant.queue_state(job_id)

    @app.post("/api/jobs/{job_id}/assistant/cancel")
    def assistant_job_cancel(job_id: str, request: CancelRequest):
        app.state.assistant.output(job_id)
        return app.state.assistant.stop(job_id, request)

    @app.get("/api/jobs/{job_id}/assistant/schema")
    def assistant_schema(job_id: str):
        return app.state.assistant.schema(job_id)

    @app.post("/api/jobs/{job_id}/assistant/schema")
    def assistant_save_schema(job_id: str, request: TableSchemaRequest):
        return app.state.assistant.save_schema(job_id, request)

    @app.post("/api/jobs/{job_id}/assistant/schema/import")
    def assistant_import_schema_csv(job_id: str, request: TableCsvRequest):
        return app.state.assistant.import_schema_csv(job_id, request)

    @app.get("/api/jobs/{job_id}/assistant/schema/download")
    def assistant_export_schema(job_id: str):
        return JSONResponse(app.state.assistant.export_schema(job_id),
                            headers={"Content-Disposition": 'attachment; filename="schema.json"'})

    @app.get("/api/jobs/{job_id}/assistant/bindings")
    def assistant_bindings(job_id: str):
        return app.state.assistant.bindings(job_id)

    @app.post("/api/jobs/{job_id}/assistant/bindings")
    def assistant_save_bindings(job_id: str, request: InputBindingsRequest):
        return app.state.assistant.save_bindings(job_id, request)

    @app.get("/api/jobs/{job_id}/assistant/bindings/download")
    def assistant_export_bindings(job_id: str):
        sheet = app.state.assistant.bindings(job_id)
        return JSONResponse({"screen_input_bindings": {"version": 1, "bindings": sheet.get("bindings", {})}},
                            headers={"Content-Disposition": 'attachment; filename="input-bindings.config.json"'})

    @app.get("/api/jobs/{job_id}/assistant/runs/{run_id}")
    def assistant_state(job_id: str, run_id: str):
        return app.state.assistant.state(job_id, run_id)

    @app.get("/api/jobs/{job_id}/assistant/proposals/{proposal_id}")
    def assistant_proposal(job_id: str, proposal_id: str):
        return app.state.assistant.proposal(job_id, proposal_id)

    @app.get("/api/jobs/{job_id}/assistant/proposals/{proposal_id}/download")
    def assistant_download(job_id: str, proposal_id: str):
        file = app.state.assistant.archive(job_id, proposal_id)
        return FileResponse(file, filename=file.name, media_type="application/zip")

    @app.post("/api/jobs/{job_id}/assistant/proposals/{proposal_id}/apply")
    def assistant_apply(job_id: str, proposal_id: str, request: ApplyRequest):
        return app.state.assistant.apply(job_id, proposal_id)

    # What was sent to the local model and what it answered (also for rejected answers).
    @app.post("/api/jobs/{job_id}/assistant/prompt")
    def assistant_prompt(job_id: str, request: PromptRequest):
        return app.state.assistant.prompt(job_id, request)

    @app.get("/api/jobs/{job_id}/assistant/exchanges")
    def assistant_exchanges(job_id: str, task_id: str | None = Query(default=None, pattern=r"^[a-f0-9]{20}$")):
        return app.state.assistant.exchanges(job_id, task_id)

    @app.get("/api/jobs/{job_id}/assistant/exchanges/{exchange_id}")
    def assistant_exchange(job_id: str, exchange_id: str):
        return app.state.assistant.exchange(job_id, exchange_id)

    @app.get("/ai-log")
    def ai_log_page():
        # Plain page from this backend: usable without rebuilding the Angular UI. Texts are shown with
        # textContent only; the model's answer is never interpreted as HTML.
        return FileResponse(AI_LOG_PAGE, media_type="text/html; charset=utf-8",
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                     "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; "
                                     "style-src 'unsafe-inline'; connect-src 'self'; img-src data:; base-uri 'none'; "
                                     "form-action 'none'; frame-ancestors 'self'"})

    @app.get("/assistant")
    def assistant_page(job: str | None = Query(default=None, max_length=100)):
        # Old bookmarks enter the same Angular application; there is no second UI.
        from urllib.parse import urlencode
        query = {'view': 'assistant'}
        if job: query['job'] = job
        return RedirectResponse('/?' + urlencode(query), status_code=307)

    @app.get("/api/jobs/{job_id}/file")
    def preview(job_id: str, path: str = Query(min_length=1, max_length=500)):
        file = manager().source_file(job_id, path)
        with file.open("rb") as stream:
            content = stream.read(1024 * 1024)
        return {"path": path, "text": content.decode("utf-8", "replace"), "truncated": file.stat().st_size > len(content)}

    @app.get("/api/jobs/{job_id}/logs")
    def logs(job_id: str):
        manager().get(job_id)
        return {"text": tail(settings.data_dir / "jobs" / job_id / "worker.log")}

    @app.get("/api/jobs/{job_id}/helpers/{name}")
    def helper(job_id: str, name: Literal["CommonMigrateTools.java", "wf-table.ts"]):
        # The shared helpers are not deployed with a module: downloaded once and kept in the project.
        return FileResponse(manager().helper_path(job_id, name), filename=name,
                            media_type="text/plain; charset=utf-8")

    @app.get("/api/jobs/{job_id}/download")
    def download(job_id: str, kind: Literal["all", "frontend", "backend"] = "all"):
        file, filename = manager().archive(job_id, kind)
        return FileResponse(file, media_type="application/zip", filename=filename)

    @app.post("/api/jobs/{job_id}/answer", status_code=202)
    def answer_job(job_id: str, answer: JobAnswer):
        # A job waiting for the main window (needs_input) continues with this choice.
        return manager().answer(job_id, answer.screen_primary_window, answer.screen_windows)

    @app.get("/api/jobs/{job_id}/preview")
    def job_preview(job_id: str):
        # Static, script-free HTML. The UI shows it in a sandboxed iframe.
        return FileResponse(manager().preview_path(job_id), media_type="text/html; charset=utf-8",
                            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:",
                                     "X-Content-Type-Options": "nosniff"})

    @app.post("/api/jobs/{job_id}/deploy")
    def deploy_job(job_id: str, request: DeployRequest):
        # Writes into the developer's project on this machine (the API only listens on localhost).
        return manager().job_deploy(job_id, request.project, request.layout, request.dry_run, request.force)

    @app.post("/api/batches/{batch_id}/deploy")
    def deploy_batch(batch_id: str, request: DeployRequest):
        return manager().batch_deploy(batch_id, request.project, request.layout, request.dry_run, request.force)

    # Choosing the project folders without typing paths ("Tallózás…"). POST: only the local UI may ask (X-Frm-Client).
    @app.post("/api/fs/folders")
    def folders(request: FolderRequest):
        return list_folders(request.path, settings.project_roots)

    @app.post("/api/fs/pick")
    def pick_folder(request: PickRequest):
        # Blocks until the developer closes the dialog (a worker thread, not the event loop).
        if not settings.folder_dialog:
            raise FolderError("A mappaválasztó ablak ki van kapcsolva (FRM_FOLDER_DIALOG=false); a böngészőben tallózz.", 501)
        return picker.pick(request.title, request.initial, settings.project_roots)

    @app.post("/api/fs/pick/cancel")
    def cancel_pick():
        return {"cancelled": picker.cancel()}

    @app.get("/api/batches/{batch_id}")
    def batch_report(batch_id: str):
        return manager().batch_report(batch_id)

    @app.get("/api/batches/{batch_id}/survey")
    def batch_survey(batch_id: str, names: bool = False):
        return manager().batch_survey(batch_id, names)

    @app.get("/api/batches/{batch_id}/download")
    def batch_download(batch_id: str):
        path, name = manager().batch_archive(batch_id)
        return FileResponse(path, filename=name, media_type="application/zip")

    @app.get("/api/examples/{filename}")
    def example(filename: str):
        if filename not in {"customer_fmb.xml", "schema.json", "review_fmb.xml"}:
            raise HTTPException(404, "Nincs ilyen példa.")
        return FileResponse(PROJECT_ROOT / "examples" / filename, filename=filename)

    # Unknown API paths must remain JSON 404s, not index.html responses.
    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    def api_not_found(rest: str):
        raise HTTPException(404, "Ismeretlen API végpont.")

    if (settings.static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="local-angular")
    else:
        @app.get("/")
        def no_ui():
            return {"message": "A backend fut. A frontend: http://localhost:4200. Buildhez a web-ui mappában npm run build.", "health": "/api/health"}

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])
    app.add_middleware(LocalRequestGuard, origins=settings.cors_origins,
                       max_body=settings.max_upload_bytes + 3 * settings.max_json_bytes + 65536)
    app.add_middleware(DiagnosedCORSMiddleware, allow_origins=settings.cors_origins,
                       allow_credentials=settings.cors_allow_credentials, allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                       allow_headers=settings.cors_headers, expose_headers=["Content-Disposition"], max_age=600)
    return app

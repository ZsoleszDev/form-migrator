"""What the Java assistant sent to the local model and what came back.

One JSON file per run (exchange), written before the first request and updated after every
attempt, so the UI can show the prompt while the model is still working and the raw answer
even when it was rejected. The files live next to the module folder (<module>-ai-log), never
inside it: they are not part of the module ZIP that goes into the company project.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

from .common import MigrationError

VERSION = 1
ID = re.compile(r"[a-f0-9]{20}")
KEEP = 60                       # newest exchanges kept per module
MAX_TEXT = 400_000              # characters of one stored answer/thinking


def folder(output: Path) -> Path:
    output = Path(output).resolve()
    return output.parent / (output.name + "-ai-log")


def new_id() -> str:
    return uuid.uuid4().hex[:20]


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write(path: Path, value: dict) -> None:
    """Atomic replace (readers never see half a file); retries Windows sharing conflicts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=path.name + ".",
                                     suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
    try:
        for attempt in range(7):
            try:
                os.replace(temporary, path)
                return
            except OSError as exc:
                if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 6:
                    raise
                time.sleep(min(0.02 * 2 ** attempt, 0.2))
    finally:
        temporary.unlink(missing_ok=True)


def clip(text: str) -> str:
    text = text or ""
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "\n[… a napló csak az első " + str(MAX_TEXT) + " karaktert őrzi meg]"


class Exchange:
    """One assistant run: the request, every attempt's raw answer and what the migrator took from it."""

    def __init__(self, output: Path, exchange_id: str, *, task_id: str, method: str, target: str, model: str,
                 url: str, options: dict, prompt: dict):
        if not ID.fullmatch(exchange_id):
            raise MigrationError("Hibás AI-napló azonosító.")
        self.root = folder(output)
        self.path = self.root / (exchange_id + ".json")
        prune(self.root)
        self.data = {"version": VERSION, "id": exchange_id, "task_id": task_id, "method": method, "target": target,
                     "status": "running", "started": now(), "finished": None, "model": model, "url": url,
                     "options": options, "prompt": prompt, "attempts": [], "cached": False, "result": None,
                     "error": None}
        self.save()

    def save(self) -> None:
        write(self.path, self.data)

    def begin(self, number: int, correction: str | None = None) -> dict:
        attempt = {"n": number, "started": now(), "seconds": None, "correction": correction, "raw": None,
                   "thinking": None, "done_reason": None, "prompt_tokens": None, "output_tokens": None,
                   "extraction": None, "error": None}
        self.data["attempts"].append(attempt)
        self.save()
        attempt["_clock"] = time.monotonic()
        return attempt

    def end(self, attempt: dict, *, raw: str | None = None, thinking: str | None = None, outer: dict | None = None,
            extraction: dict | None = None, error: str | None = None) -> None:
        attempt["seconds"] = round(time.monotonic() - attempt.pop("_clock", time.monotonic()), 1)
        if raw is not None: attempt["raw"] = clip(raw)
        if thinking: attempt["thinking"] = clip(thinking)
        if outer:
            attempt["done_reason"] = outer.get("done_reason") or ("stop" if outer.get("done") else None)
            for key, target in (("prompt_eval_count", "prompt_tokens"), ("eval_count", "output_tokens")):
                if isinstance(outer.get(key), int): attempt[target] = outer[key]
            for key in ("load_duration", "total_duration", "prompt_eval_duration", "eval_duration"):
                if isinstance(outer.get(key), int): attempt[key.replace("duration", "seconds")] = round(outer[key] / 1e9, 1)
            if isinstance(outer.get("eval_count"), int) and isinstance(outer.get("eval_duration"), int) and outer["eval_duration"]:
                attempt["tokens_per_second"] = round(outer["eval_count"] / (outer["eval_duration"] / 1e9), 2)
            live = outer.get("live") if isinstance(outer.get("live"), dict) else {}
            if live.get("first_token_seconds") is not None:
                attempt["first_token_seconds"] = live["first_token_seconds"]
        if extraction is not None: attempt["extraction"] = extraction
        if error: attempt["error"] = error[:4000]
        self.save()

    def cached(self, entry: dict) -> None:
        self.data["cached"] = True
        self.data["attempts"].append({"n": 0, "started": now(), "seconds": 0, "correction": None,
                                      "raw": clip(entry.get("raw") or ""), "thinking": None, "done_reason": "cache",
                                      "prompt_tokens": None, "output_tokens": None,
                                      "extraction": entry.get("extraction"), "error": None,
                                      "note": "Gyorsítótárból: ugyanerre a kérésre korábban kapott válasz; nem volt új AI-hívás."})
        self.save()

    def finish(self, status: str, *, result: dict | None = None, error: str | None = None) -> None:
        if self.data["status"] != "running":
            return
        for attempt in self.data["attempts"]:
            attempt.pop("_clock", None)
        self.data.update(status=status, finished=now(), result=result, error=(error or None) and error[:4000])
        self.save()


def prune(root: Path, keep: int = KEEP) -> None:
    if not root.is_dir(): return
    files = sorted((p for p in root.glob("*.json") if ID.fullmatch(p.stem)), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in files[keep:]:
        try:
            stale.unlink()
        except OSError:
            pass


def read(output: Path, exchange_id: str) -> dict:
    if not ID.fullmatch(exchange_id or ""):
        raise MigrationError("Hibás AI-napló azonosító.")
    path = folder(output) / (exchange_id + ".json")
    if not path.is_file():
        raise FileNotFoundError(exchange_id)
    value = json.loads(path.read_text(encoding="utf-8"))
    for attempt in value.get("attempts", []):
        attempt.pop("_clock", None)
    return value


def summaries(output: Path, task_id: str | None = None, limit: int = 50) -> list[dict]:
    root = folder(output)
    if not root.is_dir(): return []
    result = []
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if not ID.fullmatch(path.stem): continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if task_id and value.get("task_id") != task_id: continue
        attempts = value.get("attempts", [])
        result.append({"id": value.get("id"), "task_id": value.get("task_id"), "method": value.get("method"),
                       "status": value.get("status"), "started": value.get("started"), "finished": value.get("finished"),
                       "model": value.get("model"), "cached": value.get("cached", False),
                       "attempts": len([a for a in attempts if a.get("n")]),
                       "proposal_id": (value.get("result") or {}).get("proposal_id"),
                       "proposal_status": (value.get("result") or {}).get("status"),
                       "error": (value.get("error") or "")[:300] or None})
        if len(result) >= limit: break
    return result

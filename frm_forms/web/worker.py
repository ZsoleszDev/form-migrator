from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from frm_forms.cli import migration
from frm_forms.common import failure_report, run_deep
from frm_forms.screen_windows import PrimaryWindowRequired, WindowSelectionRequired
from .file_io import atomic_json

# Exit code of a run that stopped for a user decision (see question.json).
NEEDS_INPUT = 4


def run(job_dir: Path) -> int:
    record = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    options = record["options"]
    inputs = job_dir / "inputs"
    args = argparse.Namespace(input=inputs / ("input" + record["source_type"]), out=job_dir / "module",
                              config=inputs / "config.json", schema=inputs / "schema.json" if (inputs / "schema.json").exists() else None,
                              rules=inputs / "rules.json" if (inputs / "rules.json").exists() else None,
                              module=options["module"], java_package=None, max_ai_calls=None, ai=options["ai_mode"],
                              cache_dir=job_dir.parent.parent / "cache", zip=True, strict=options["strict"],
                              scaffold=options.get('generation_mode', 'strict') == 'scaffold',
                              screen=options.get('generation_mode', 'strict') == 'screen',
                              olb=sorted((inputs / "olb").glob("*")), mmb=next((inputs / "mmb").glob("*"), None),
                              pld=sorted((inputs / "pld").glob("*")))

    def progress(phase):
        try:
            atomic_json(job_dir / "progress.json", {"phase": phase})
        except OSError as exc:
            # Progress is advisory; output and job metadata errors still fail.
            print("FIGYELMEZTETÉS: Az állapotjelzés nem menthető: " + str(exc),
                  file=sys.stderr, flush=True)

    try:
        return run_deep(migration, args, on_progress=progress)
    except WindowSelectionRequired as exc:
        # Not a failure: the web UI shows the windows with a preview and re-queues with screen_windows.
        atomic_json(job_dir / "question.json", {"kind": "windows", "message": str(exc), "choices": exc.candidates})
        print("DÖNTÉS SZÜKSÉGES: " + str(exc), file=sys.stderr, flush=True)
        return NEEDS_INPUT
    except PrimaryWindowRequired as exc:
        # Not a failure: the web UI asks which window is the main screen and
        # re-queues this job with screen_primary_window set.
        atomic_json(job_dir / "question.json", {"kind": "primary_window", "message": str(exc), "choices": exc.candidates})
        print("DÖNTÉS SZÜKSÉGES: " + str(exc), file=sys.stderr, flush=True)
        return NEEDS_INPUT
    except Exception as exc:
        # the traceback goes to the log; the last HIBA line is the job's error message (type, message, place)
        print(failure_report(exc), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(run(Path(sys.argv[1]).resolve()))

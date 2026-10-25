"""Atomic JSON writes with bounded retries for Windows sharing conflicts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time


def atomic_json(path: Path, value: dict) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    temp = None
    try:
        # Close the handle before replace: Windows cannot rename an open temp.
        # Unique names also prevent concurrent writers sharing one temp file.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as stream:
            temp = Path(stream.name)
            stream.write(payload)
        for attempt in range(7):
            try:
                os.replace(temp, path)
                return
            except OSError as exc:
                if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 6:
                    raise
                time.sleep(min(0.02 * 2**attempt, 0.2))
    finally:
        if temp is not None:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                # Cleanup must not obscure the original write error.
                pass

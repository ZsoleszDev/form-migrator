from __future__ import annotations

import shutil
import os
import subprocess
import time
from pathlib import Path

from .common import MigrationError


def export_fmb(source: Path, destination: Path, config: dict) -> Path:
    """Run licensed Oracle Forms2XML; never decode an FMB as text."""
    command = config.get("export_command")
    if command is None:
        executable = shutil.which("frmf2xml") or shutil.which("frmf2xml.bat")
        if not executable:
            raise MigrationError("Az FMB bináris. Nem található frmf2xml. Exportáld Oracle Forms környezetben XML-re, vagy állítsd be az export_command argumentumlistát a config.json-ban.")
        command = [executable, "USE_PROPERTY_IDS=NO", "DUMP=ALL", "OVERWRITE=YES", "{input}"]
    if not isinstance(command, list) or not command or not all(isinstance(p, str) for p in command):
        raise MigrationError("export_command: nem üres JSON string-lista kell, shell parancslánc helyett.")
    if not any("{input}" in part for part in command):
        raise MigrationError("export_command: hiányzik az {input} helyőrző.")
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / (source.stem + "_fmb.xml")
    args = [p.replace("{input}", str(source.resolve())).replace("{output_dir}", str(destination.resolve())).replace("{output}", str(output.resolve())) for p in command]
    if os.name == "nt" and Path(args[0]).suffix.lower() in {".bat", ".cmd"} and any(any(c in p for c in '&|<>^%!\r\n') for p in args):
        raise MigrationError("Windows batch export: shell-metakaraktert tartalmazó útvonal nem támogatott. Használd a Java Forms2XML parancsot közvetlenül vagy előre exportált XML-t.")
    started = time.time()
    try:
        proc = subprocess.run(args, cwd=destination, capture_output=True, timeout=config.get("export_timeout_seconds", 180), shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MigrationError(f"Oracle export nem futott le: {exc}") from exc
    log = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
    (destination / "export.log").write_text(log, encoding="utf-8")
    if proc.returncode:
        raise MigrationError(f"Oracle export hibakód: {proc.returncode}. Kimenet vége:\n{log[-3000:]}")
    produced = fresh_output(destination, output.name, started) or fresh_output(source.parent, output.name, started)
    if produced is not None:
        if produced.parent != destination:
            # Forms2XML writes next to the input FMB when it gets an absolute path
            # (as here); older wrappers write into the working directory. Both work.
            shutil.copyfile(produced, output)
            (destination / "export.log").write_text(log + f"\n[FRM] Az XML a bemenet mellől átvéve: {produced}\n", encoding="utf-8")
            return output
        return produced
    candidates = [p for p in destination.iterdir() if p.is_file() and p.suffix.lower() == ".xml"]
    if len(candidates) == 1:
        return candidates[0]
    raise MigrationError(f"Az exporter nem adott egyértelmű XML-kimenetet. Várt fájl: {output.name} "
                         f"(exportmappában vagy a bemenet mellett); exportmappa: {[p.name for p in candidates]}. {log[-1500:]}")


def fresh_output(folder: Path, name: str, started: float) -> Path | None:
    """The export XML in folder (case-insensitive name) written by this run, not an older file."""
    try:
        matches = [p for p in folder.iterdir() if p.is_file() and p.name.lower() == name.lower()]
    except OSError:
        return None
    matches = [p for p in matches if p.stat().st_mtime >= started - 2]
    return matches[0] if len(matches) == 1 else None

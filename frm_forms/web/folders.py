"""Choosing folders in the local web UI without typing paths: the folders of a project's parts.

The server runs on the developer's machine (it only answers localhost), so it can offer two ways:
  - FolderPicker: the operating system's own folder dialog (on Windows the usual Explorer folder picker),
    opened by a child process (tkinter askdirectory) in front of the other windows. One at a time; the UI can
    cancel it. Where it cannot open (no tkinter, no desktop session), the UI falls back to the in-page browser;
  - list_folders: the subfolders of a folder for that in-page browser, with the drives (Windows) or / and the
    home folder as starting points. Hidden and system folders and symlinks are left out; Java projects
    (src/main/java) and Angular projects (angular.json) are marked.
FRM_PROJECT_ROOTS (settings.project_roots) limits both to the folders below the allowed ones.
"""
from __future__ import annotations

import os
from pathlib import Path
import string
import subprocess
import sys
import threading

MAX_FOLDERS = 500
WINDOWS_SYSTEM = {'$recycle.bin', 'system volume information', 'recovery', 'config.msi', 'msocache', 'perflogs',
                  '$windows.~bt', '$windows.~ws', '$winreagent', 'documents and settings'}
HIDDEN_OR_SYSTEM = 0x2 | 0x4  # FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM

# argv: title, initial folder ('' = the dialog's own default). Prints the chosen folder (UTF-8), nothing on cancel.
PICK_SCRIPT = r'''
import sys
if sys.platform == 'win32':
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # a sharp dialog on scaled displays
    except Exception:
        pass
try:
    import tkinter
    from tkinter import filedialog
except Exception:
    sys.exit(3)
try:
    root = tkinter.Tk()
except Exception:
    sys.exit(4)
root.withdraw()
try:
    root.attributes('-topmost', True)
except Exception:
    pass
root.update()
chosen = filedialog.askdirectory(parent=root, title=sys.argv[1], initialdir=sys.argv[2] or None, mustexist=True)
root.destroy()
sys.stdout.buffer.write((chosen or '').encode('utf-8'))
'''


class FolderError(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def allowed_roots(allowed: list[str]) -> list[Path]:
    return [Path(root).expanduser().resolve() for root in allowed]


def is_allowed(path: Path, roots: list[Path]) -> bool:
    return not roots or any(path.is_relative_to(root) for root in roots)


def windows_drives() -> list[str]:
    listdrives = getattr(os, 'listdrives', None)  # Python 3.12+
    if listdrives:
        return list(listdrives())
    import ctypes
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return [letter + ':\\' for index, letter in enumerate(string.ascii_uppercase) if mask >> index & 1]


def kind(folder: Path) -> str | None:
    """'java' for a Java project (src/main/java), 'angular' for an Angular project (angular.json)."""
    try:
        if (folder / 'angular.json').is_file():
            return 'angular'
        if (folder / 'src' / 'main' / 'java').is_dir():
            return 'java'
    except OSError:
        pass
    return None


def starting_points(roots: list[Path]) -> list[dict]:
    if roots:
        return [{'name': str(root), 'path': str(root), 'kind': kind(root)} for root in roots if root.is_dir()]
    home = Path.home()
    points = [{'name': 'Saját mappa (' + (home.name or str(home)) + ')', 'path': str(home), 'kind': None}]
    for drive in (windows_drives() if os.name == 'nt' else ['/']):
        points.append({'name': drive, 'path': drive, 'kind': None})
    return points


def visible(entry: os.DirEntry) -> bool:
    if entry.name.startswith('.') or entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
        return False
    if os.name == 'nt':
        if entry.name.lower() in WINDOWS_SYSTEM:
            return False
        attributes = getattr(entry.stat(follow_symlinks=False), 'st_file_attributes', 0)
        if attributes & HIDDEN_OR_SYSTEM:
            return False
        if getattr(entry, 'is_junction', lambda: False)():
            return False
    return True


def list_folders(path: str | None, allowed: list[str]) -> dict:
    """{'path', 'parent', 'kind', 'roots', 'folders': [{'name', 'path', 'kind'}], 'truncated'} for the in-page browser.

    path None: the starting points (drives / and the home folder, or the allowed folders)."""
    roots = allowed_roots(allowed)
    points = starting_points(roots)
    if not path:
        return {'path': None, 'parent': None, 'kind': None, 'roots': points, 'folders': points, 'truncated': False}
    folder = Path(path).expanduser()
    if not folder.is_absolute():
        raise FolderError('Teljes útvonal kell (például C:\\projektek).', 422)
    folder = folder.resolve()
    if not folder.is_dir():
        raise FolderError('A mappa nem létezik: ' + str(folder), 404)
    if not is_allowed(folder, roots):
        raise FolderError('A mappa nincs az engedélyezett mappák között (FRM_PROJECT_ROOTS).', 403)
    folders = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                try:
                    if visible(entry):
                        folders.append(entry)
                except OSError:
                    continue
    except PermissionError as exc:
        raise FolderError('A mappa nem olvasható (nincs hozzá jogosultság): ' + str(folder), 403) from exc
    except OSError as exc:
        raise FolderError('A mappa nem olvasható: ' + str(folder), 422) from exc
    folders.sort(key=lambda entry: entry.name.casefold())
    parent = folder.parent if folder.parent != folder and is_allowed(folder.parent, roots) else None
    return {'path': str(folder), 'parent': str(parent) if parent else None, 'kind': kind(folder), 'roots': points,
            'folders': [{'name': entry.name, 'path': str(folder / entry.name), 'kind': kind(folder / entry.name)}
                        for entry in folders[:MAX_FOLDERS]],
            'truncated': len(folders) > MAX_FOLDERS}


class FolderPicker:
    """The operating system's folder dialog on this machine, one at a time."""

    def __init__(self, command: list[str] | None = None, timeout: int = 900):
        self.command = command or [sys.executable, '-c', PICK_SCRIPT]
        self.timeout = timeout
        self.lock = threading.Lock()
        self.process: subprocess.Popen | None = None
        self.cancelled = False

    def pick(self, title: str, initial: str | None, allowed: list[str]) -> dict:
        """{'path': the chosen folder or None, 'cancelled': bool}."""
        roots = allowed_roots(allowed)
        if not self.lock.acquire(blocking=False):
            raise FolderError('Már nyitva van egy mappaválasztó ablak; zárd be, vagy nyomd meg a „Mégse” gombot.', 409)
        try:
            start = Path(initial).expanduser() if initial else None
            if start is None or not start.is_absolute() or not start.is_dir() or not is_allowed(start.resolve(), roots):
                start = roots[0] if roots else None
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            self.cancelled = False
            try:
                self.process = subprocess.Popen([*self.command, title, str(start or '')], stdin=subprocess.DEVNULL,
                                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags)
            except OSError as exc:
                raise FolderError('A mappaválasztó ablak nem nyitható meg ezen a gépen.', 501) from exc
            try:
                output, _ = self.process.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.communicate()
                return {'path': None, 'cancelled': True}
            if self.cancelled:
                return {'path': None, 'cancelled': True}
            if self.process.returncode != 0:  # 3: no tkinter, 4: no desktop session, else the dialog failed
                raise FolderError('A mappaválasztó ablak nem nyitható meg ezen a gépen (nincs tkinter vagy asztali '
                                  'munkamenet); a böngészőben is kitallózható.', 501)
            chosen = output.decode('utf-8', 'replace').strip()
            if not chosen:
                return {'path': None, 'cancelled': True}
            folder = Path(chosen).resolve()
            if not is_allowed(folder, roots):
                raise FolderError('A kiválasztott mappa nincs az engedélyezett mappák között (FRM_PROJECT_ROOTS).', 403)
            return {'path': str(folder), 'cancelled': False, 'kind': kind(folder)}
        finally:
            self.process = None
            self.lock.release()

    def cancel(self) -> bool:
        """Closes the open dialog (the UI's Cancel); True when one was open."""
        process = self.process
        if process is None or process.poll() is not None:
            return False
        self.cancelled = True
        process.kill()
        return True

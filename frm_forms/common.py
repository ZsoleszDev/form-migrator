from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path


class MigrationError(Exception):
    pass


# Deeply nested Forms code (a WHERE built of 2000 || pieces, 40 nested IFs) is a deep syntax tree: the parsers, the
# compilers and json.dumps of form.ir.json recurse into it. A migration runs on a thread with a large stack and a high
# recursion limit, so a big form does not stop with RecursionError (or a stack overflow on Windows).
DEEP_STACK_BYTES = 512 * 1024 * 1024
DEEP_RECURSION_LIMIT = 100_000


def model_cache(model: dict, key: str, compute):
    """A value derived from the analysed form, computed once per run (the model's "_cache", never written out)."""
    store = model.setdefault("_cache", {})
    if key not in store:
        store[key] = compute()
    return store[key]


def failure_place(exc: BaseException) -> str:
    """' (hely: frm_forms/x.py:12, function)': the migrator's innermost frame of an exception, or ''."""
    import traceback
    frames = traceback.extract_tb(exc.__traceback__)
    ours = [f for f in frames if "frm_forms" in f.filename.replace("\\", "/")]
    place = ours[-1] if ours else frames[-1] if frames else None
    if place is None:
        return ""
    path = place.filename.replace("\\", "/")
    path = path[path.rfind("frm_forms/"):] if "frm_forms/" in path else path.rsplit("/", 1)[-1]
    return f" (hely: {path}:{place.lineno}, {place.name})"


def failure_report(exc: BaseException) -> str:
    """The log text of a failed run: the traceback, then one HIBA line the web UI shows (type, message, place)."""
    import traceback
    if isinstance(exc, (MigrationError, OSError)):
        return "HIBA: " + str(exc)  # an expected, user-facing failure (a missing file ...)
    where = failure_place(exc)
    hint = {RecursionError: " A forrásban túl mélyen beágyazott szerkezet van.",
            MemoryError: " Elfogyott a memória."}.get(type(exc), "")
    message = " ".join(str(exc).split())[:400] or type(exc).__name__
    return ("".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            + f"HIBA: Váratlan belső hiba ({type(exc).__name__}): {message}{where}.{hint}")


def run_deep(function, *args, **kwargs):
    """function(*args, **kwargs) on a thread with a large stack; its result, or its exception (traceback kept)."""
    import sys
    import threading
    outcome = {}

    def target():
        try:
            outcome["value"] = function(*args, **kwargs)
        except BaseException as exc:  # re-raised in the caller's thread, with its traceback
            outcome["error"] = exc

    previous_limit, previous_size = sys.getrecursionlimit(), threading.stack_size()
    thread = None
    try:
        for size in (DEEP_STACK_BYTES, 128 * 1024 * 1024, 0):
            try:
                threading.stack_size(size)
                # the limit follows the stack: a small default stack keeps the default limit
                sys.setrecursionlimit(max(previous_limit, DEEP_RECURSION_LIMIT * size // DEEP_STACK_BYTES))
                thread = threading.Thread(target=target, name="frm-migration", daemon=True)
                thread.start()
                break
            except (ValueError, RuntimeError, MemoryError):
                thread = None  # a platform without that much stack: try smaller
        while thread is not None and thread.is_alive():
            thread.join(0.2)  # Ctrl+C still reaches the main thread
    finally:
        sys.setrecursionlimit(previous_limit)
        threading.stack_size(previous_size)
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


RESERVED = set("abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for goto if implements import instanceof int interface long native new package private protected public return short static strictfp super switch synchronized this throw throws transient try void volatile while var record sealed permits yield true false null constructor prototype __proto__ name length arguments await delete export function let typeof with from get set".split())
RESERVED -= {"name", "length", "from", "get", "set"}


def name(value: str, style: str = "camel") -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    words = re.findall(r"[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|[A-Z]+|[0-9]+", ascii_value)
    words = [w.lower() for w in words] or ["field"]
    result = words[0] + "".join(w.title() for w in words[1:])
    if result[0].isdigit():
        result = "n" + result
    if result in RESERVED:
        result += "Value"
    if style == "pascal":
        return result[0].upper() + result[1:]
    if style == "kebab":
        return "-".join(words) if not words[0][0].isdigit() else "n-" + "-".join(words)
    return result


def unique_names(values: list[str], style: str = "camel") -> dict[str, str]:
    result: dict[str, str] = {}
    used: set[str] = set()
    for value in values:
        if value in result:
            raise MigrationError(f"Ismétlődő Oracle objektumnév: {value}")
        base = name(value, style)
        candidate = base
        count = 2
        while candidate.lower() in used:
            candidate = base + str(count)
            count += 1
        used.add(candidate.lower())
        result[value] = candidate
    return result


def identifier(value: str, qualified: bool = False) -> str:
    pattern = r"[A-Za-z][A-Za-z0-9_$#]*(?:\.[A-Za-z][A-Za-z0-9_$#]*)?" if qualified else r"[A-Za-z][A-Za-z0-9_$#]*"
    if not re.fullmatch(pattern, value):
        raise MigrationError(f"Nem támogatott SQL azonosító: {value!r}; idézett/dinamikus SQL-hez adapter kell.")
    return value


def jstr(value: str) -> str:
    # ASCII escapes also keep Java and JavaScript source safe for unusual labels.
    return json.dumps(value, ensure_ascii=True)


def java_text_block(value: str, indent: str = '                ') -> str:
    """Readable Java 11 string literal (no text block): one concatenated line per source line."""
    lines = value.split('\n')
    parts = [jstr(line + ('\n' if i < len(lines) - 1 else '')) for i, line in enumerate(lines)]
    return ('\n' + indent + '+ ').join(parts) if parts else '""'


def read_json(path: Path) -> dict:
    try:
        obj = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        raise MigrationError(f"Nem olvasható JSON: {path}: {exc}") from exc
    if not isinstance(obj, dict):
        raise MigrationError(f"JSON objektum szükséges: {path}")
    return obj


def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").strip().encode("utf-8")).hexdigest()


# Forms2XML escapes its own character references: a line break in TriggerText,
# WhereClause or ProgramUnitText arrives as the literal text "&#10;" after XML
# parsing. Only whitespace references are restored; anything else stays as is.
LINE_ESCAPE = re.compile(r"&#([xX][0-9a-fA-F]{1,8}|[0-9]{1,8});")


def decode_line_escapes(text: str) -> str:
    def replace(match):
        value = match.group(1)
        number = int(value[1:], 16) if value[:1] in "xX" else int(value)
        return chr(number) if number in (9, 10, 13) else match.group()
    return LINE_ESCAPE.sub(replace, text or "")

"""Small, offset-preserving Java source scanning used by the Java assistant.

Not a Java parser: comments and literals are blanked (java_mask) so braces, parentheses
and keywords can be found reliably, then class bodies are split into members. This is
enough to take methods out of a model's answer and to place them into generated files.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .common import MigrationError

NAME = r"[A-Za-z_$][\w$]*"
MODIFIERS = {"public", "protected", "private", "static", "final", "synchronized", "abstract",
             "native", "default", "strictfp", "transient", "volatile"}
# Words that start statements, never a member declaration.
STATEMENT_WORDS = {"if", "else", "for", "while", "do", "switch", "case", "try", "catch", "finally", "return",
                   "throw", "new", "synchronized", "assert", "yield", "break", "continue", "this", "super"}
METHOD_HEADER = re.compile(
    r"^(?P<mods>(?:(?:" + "|".join(sorted(MODIFIERS)) + r")\s+)*)"
    r"(?:<[^;{}()]*>\s*)?"
    r"(?P<type>" + NAME + r"(?:\s*\.\s*" + NAME + r")*(?:\s*<[^;{}()]*>)?(?:\s*\[\s*\])*)\s+"
    r"(?P<name>" + NAME + r")\s*\((?P<params>[^()]*(?:\([^()]*\)[^()]*)*)\)\s*(?:\[\s*\]\s*)*"
    r"(?:throws\s+[\w$.,\s<>]+)?$")
CONSTRUCTOR_HEADER = re.compile(
    r"^(?P<mods>(?:(?:public|protected|private)\s+)*)(?P<name>[A-Z][\w$]*)\s*"
    r"\((?P<params>[^()]*(?:\([^()]*\)[^()]*)*)\)\s*(?:throws\s+[\w$.,\s<>]+)?$")
FIELD_HEADER = re.compile(
    r"^(?P<mods>(?:(?:" + "|".join(sorted(MODIFIERS)) + r")\s+)*)"
    r"(?P<type>" + NAME + r"(?:\s*\.\s*" + NAME + r")*(?:\s*<[^;{}]*>)?(?:\s*\[\s*\])*)\s+"
    r"(?P<name>" + NAME + r")\s*(?:\[\s*\]\s*)*(?:=[\s\S]*)?$")
TYPE_HEADER = re.compile(r"\b(?P<kind>class|interface|enum|record)\s+(?P<name>" + NAME + r")")


def java_mask(source: str) -> str:
    """Blank comments and quoted literals, retaining offsets for method edits."""
    chars = list(source)
    token = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'')
    for match in token.finditer(source):
        chars[match.start():match.end()] = ["\n" if c == "\n" else " " for c in match.group()]
    return "".join(chars)


def matching_brace(mask: str, opening: int) -> int:
    depth = 0
    for pos in range(opening, len(mask)):
        if mask[pos] == "{": depth += 1
        elif mask[pos] == "}": depth -= 1
        if depth == 0: return pos
    raise MigrationError("BACKEND_ASSISTANT: lezáratlan Java blokk.")


def balanced(mask: str) -> bool:
    """Braces and parentheses close in order (comments and literals already blanked)."""
    stack = []
    for char in mask:
        if char in "({":
            stack.append(char)
        elif char in ")}":
            if not stack or stack.pop() != ("(" if char == ")" else "{"):
                return False
    return not stack


def strip_annotations(header: str) -> str:
    """Remove @Annotation and @Annotation(...) (nested parentheses included) from a masked header."""
    out, i = [], 0
    while i < len(header):
        match = re.compile(r"@\s*(?!interface\b)" + NAME + r"(?:\s*\.\s*" + NAME + r")*").match(header, i)
        if not match:
            out.append(header[i]); i += 1
            continue
        i = match.end()
        rest = re.compile(r"\s*\(").match(header, i)
        if rest:
            depth, j = 0, rest.end() - 1
            while j < len(header):
                if header[j] == "(": depth += 1
                elif header[j] == ")":
                    depth -= 1
                    if depth == 0: break
                j += 1
            i = j + 1
        out.append(" ")
    return "".join(out)


def _top_level_assignment(header: str) -> bool:
    depth = 0
    for char in header:
        if char in "(<[": depth += 1
        elif char in ")>]": depth = max(0, depth - 1)
        elif char == "=" and depth == 0: return True
    return False


@dataclass
class Member:
    kind: str            # method | constructor | field | type | init | statement
    start: int           # first code character of the member (after leading comments)
    end: int             # exclusive
    lead: int            # start including the comments/blank lines directly before it
    name: str = ""
    header: str = ""     # masked header, annotations removed, whitespace collapsed
    body: tuple[int, int] | None = None   # offsets of "{" and "}" of a method/type body
    type_kind: str = ""  # class/interface/enum/record for kind == "type"
    complete: bool = True

    def text(self, source: str, with_lead: bool = True) -> str:
        return source[self.lead if with_lead else self.start:self.end]


def classify(header: str, has_body: bool) -> tuple[str, str, str]:
    """(kind, name, type_kind) of one member header (masked, annotations removed)."""
    plain = " ".join(header.split())
    typed = TYPE_HEADER.search(plain)
    if typed and not _top_level_assignment(plain.split("{")[0]):
        prefix = plain[:typed.start()].split()
        if all(word in MODIFIERS for word in prefix):
            return "type", typed["name"], typed["kind"]
    if not plain or plain == "static":
        return ("init", "", "") if has_body else ("statement", "", "")
    first = re.match(NAME, plain)
    if first and first.group() in STATEMENT_WORDS:
        return "statement", "", ""
    if has_body:
        method = METHOD_HEADER.match(plain)
        if method and method["type"].split("<")[0].strip() not in STATEMENT_WORDS | MODIFIERS and method["name"] not in STATEMENT_WORDS:
            return "method", method["name"], ""
        constructor = CONSTRUCTOR_HEADER.match(plain)
        if constructor:
            return "constructor", constructor["name"], ""
        return "statement", "", ""
    if "(" in plain.split("=")[0] and not _top_level_assignment(plain):
        method = METHOD_HEADER.match(plain)
        if method:
            return "method", method["name"], ""   # abstract/interface method without body
        return "statement", "", ""
    field = FIELD_HEADER.match(plain)
    if field and field["type"].split("<")[0].strip() not in STATEMENT_WORDS | MODIFIERS:
        return "field", field["name"], ""
    return "statement", "", ""


def scan_members(source: str, mask: str, start: int = 0, end: int | None = None) -> list[Member]:
    """Split a class body (or a loose sequence of members/statements) into members."""
    end = len(mask) if end is None else end
    result, i = [], start
    while i < end:
        lead = i
        while i < end and mask[i].isspace():
            i += 1
        if i >= end:
            break
        member_start, depth, j = i, 0, i
        stop, body = None, None
        while j < end:
            char = mask[j]
            if char in "([":
                depth += 1
            elif char in ")]":
                depth -= 1
            elif depth <= 0 and char == ";":
                stop = j + 1
                break
            elif depth <= 0 and char == "{":
                header = mask[member_start:j]
                try:
                    close = matching_brace(mask[:end], j)
                except MigrationError:
                    stop = None
                    break
                if _top_level_assignment(strip_annotations(header)):
                    # field = {...} / anonymous class / lambda initializer: runs until its ";"
                    semicolon = mask.find(";", close, end)
                    stop = (semicolon + 1) if semicolon >= 0 else close + 1
                    break
                body, stop = (j, close), close + 1
                # "else {", "catch (...) {", "finally {", "while (...);" continue the same statement
                after = re.compile(r"\s*(else|catch|finally|while)\b").match(mask, stop)
                if after and after.end() <= end and body:
                    keyword = after.group(1)
                    j = after.end()
                    if keyword == "while":   # do { } while (...);
                        semicolon = mask.find(";", j, end)
                        stop = semicolon + 1 if semicolon >= 0 else end
                        break
                    continue
                break
            j += 1
        if stop is None:
            header = strip_annotations(mask[member_start:end])
            kind, name, type_kind = classify(header, False)
            result.append(Member(kind, member_start, end, lead, name, " ".join(header.split()), None, type_kind, False))
            break
        header_end = body[0] if body else stop - 1
        header = strip_annotations(mask[member_start:header_end])
        kind, name, type_kind = classify(header, body is not None)
        result.append(Member(kind, member_start, stop, lead, name, " ".join(header.split()), body, type_kind))
        i = stop
    return result


def method_names(source: str) -> set[str]:
    """Names of the methods declared directly in the classes of a source file."""
    mask = java_mask(source)
    names = set()
    for member in scan_members(source, mask):
        if member.kind == "type" and member.body:
            names |= {m.name for m in scan_members(source, mask, member.body[0] + 1, member.body[1]) if m.kind == "method"}
        elif member.kind == "method":
            names.add(member.name)
    return names


def methods_by_name(source: str) -> dict[str, list[str]]:
    """Method name -> whitespace-normalized full text, for the classes of a source file."""
    mask = java_mask(source)
    found: dict[str, list[str]] = {}
    def add(members):
        for member in members:
            if member.kind == "method":
                found.setdefault(member.name, []).append(" ".join(source[member.start:member.end].split()))
    for member in scan_members(source, mask):
        if member.kind == "type" and member.body:
            add(scan_members(source, mask, member.body[0] + 1, member.body[1]))
    add(scan_members(source, mask))
    return found


def replace_identifier(source: str, old: str, new: str, *, member_of: str | None = None,
                       allow_this: bool = True) -> str:
    """Rename an identifier in code only (not in comments/strings).

    member_of=None: a free name (`em.find`, `this.em`), never `other.em`.
    member_of="repo": a member access `repo.old(` / `this.repo.old(`.
    """
    mask = java_mask(source)
    if member_of is None:
        prefix = r"(?:(?<=this\.)|(?<![\w$.]))" if allow_this else r"(?<![\w$.])"
        pattern = re.compile(prefix + re.escape(old) + r"(?![\w$])")
    else:
        pattern = re.compile(r"(?:(?<=this\.)|(?<![\w$.]))" + re.escape(member_of) + r"\s*\.\s*(" + re.escape(old) + r")(?![\w$])")
    spans = [(m.start(1), m.end(1)) if member_of else (m.start(), m.end()) for m in pattern.finditer(mask)]
    for begin, finish in reversed(spans):
        source = source[:begin] + new + source[finish:]
    return source


SEQUENCE_QUERY = re.compile(r'\bcreateNativeQuery\s*\(\s*"\s*SELECT\s+[A-Za-z_][\w$#]*(?:\.[A-Za-z_][\w$#]*)?\.NEXTVAL'
                            r'\s+FROM\s+(?:SYS\.)?DUAL\s*"\s*\)', re.I)


def without_sequence_queries(source: str) -> str:
    """The one allowed native query (a sequence value) replaced by a neutral call of the same length."""
    return SEQUENCE_QUERY.sub(lambda m: "sequenceValue(" + " " * (len(m.group()) - 15) + ")", source)


def dedent(text: str) -> str:
    """Remove the common indentation of non-empty lines, trim blank edges."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[0].strip(): lines.pop(0)
    while lines and not lines[-1].strip(): lines.pop()
    widths = [len(line) - len(line.lstrip(" \t")) for line in lines if line.strip()]
    cut = min(widths) if widths else 0
    return "\n".join(line[cut:] if line.strip() else "" for line in lines)

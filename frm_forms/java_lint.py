"""Java 11 and PMD in the AI-written service code (assistant_reply.finish), without another model call.

The company builds with Java 11 and checks the code with PMD. A small local model writes what it saw most: newer Java,
or code PMD reports. A correction round costs a whole model call (minutes on a laptop CPU), so the findings are of
three kinds:

  - repaired here, because each has exactly one meaning (a note in the proposal says so):
      stream.toList() (Java 16)                -> stream.collect(Collectors.toList())
      list.getFirst() (Java 21)                -> list.get(0)
      x.equals("A"), x.equalsIgnoreCase("A")   -> "A".equals(x)            (LiteralsFirstInComparisons; a NULL
                                                                            is no match, as in Oracle, not an NPE)
      x == "A", x != "A"                       -> "A".equals(x), !"A".equals(x)  (== compares String references)
      x.size() == 0, x.size() > 0 ...          -> x.isEmpty(), !x.isEmpty()      (UseCollectionIsEmpty)
      s.toUpperCase(), s.toLowerCase()         -> s.toUpperCase(Locale.ROOT)     (UseLocaleWithCaseConversions)
      new BigDecimal(0 | 1 | 10)               -> BigDecimal.ZERO / ONE / TEN    (BigIntegerInstantiation)
      new BigDecimal(1.5)                      -> new BigDecimal("1.5")          (AvoidDecimalLiteralsInBigDecimal-
                                                                                  Constructor: 0.1 is no exact double)
  - Java 12+ syntax and API that does not compile with Java 11 (records, text blocks, switch expressions, instanceof
    patterns, String.formatted() ...): sent back to the model (problems), also when no JDK checks the syntax;
  - what PMD reports but has no single safe rewrite (an empty catch block, printStackTrace, System.out, a caught
    Exception or Throwable): a warning for the developer; no model call is spent on it.

Only code is touched: comments and string literals never match a pattern (java_mask), and an expression is rewritten
only where its operands are certain (a simple chain such as a, a.b(), a.get("X").c() in a low-precedence position).
"""
from __future__ import annotations

import re

from .java_scan import java_mask

TOKEN = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'')
IDENT = re.compile(r"[A-Za-z_$][\w$]*")
KEYWORDS = {"new", "return", "throw", "case", "else", "instanceof", "assert", "yield"}


def literals(code: str) -> list[tuple[int, int]]:
    """The (start, end) of every string literal of the code (not of a comment)."""
    return [(m.start(), m.end()) for m in TOKEN.finditer(code) if m.group().startswith('"')]


def without_comments(code: str) -> str:
    chars = list(code)
    for match in TOKEN.finditer(code):
        if match.group().startswith("/"):
            chars[match.start():match.end()] = [" " if c != "\n" else "\n" for c in match.group()]
    return "".join(chars)


def operand_start(code: str, mask: str, end: int) -> int | None:
    """The start of the simple chain that ends at `end` (a, a.b(), a.get("X").c(), (a + b)), else None."""
    i = end
    while i > 0 and code[i - 1].isspace():
        i -= 1
    first = True
    while True:
        if i == 0:
            return None if first else i
        char = mask[i - 1]
        if char == ")":
            depth, j = 0, i - 1
            while j >= 0:
                if mask[j] == ")":
                    depth += 1
                elif mask[j] == "(":
                    depth -= 1
                    if depth == 0:
                        break
                j -= 1
            if j < 0:
                return None
            i = j
            name = re.search(r"[A-Za-z_$][\w$]*\s*$", mask[:i])
            if name and name.group().strip() not in KEYWORDS:
                i = name.start()
        elif re.match(r"[\w$]", char):
            name = re.search(r"[A-Za-z_$][\w$]*$", mask[:i])
            if not name or name.group() in KEYWORDS or name.group()[0].isdigit():
                return None
            i = name.start()
        elif char == " " and not code[i - 1].isspace():
            return None  # a literal or a comment: no chain of ours
        else:
            return None if first else i
        first = False
        k = i
        while k > 0 and code[k - 1].isspace():
            k -= 1
        if k > 0 and mask[k - 1] == "." and not (k > 1 and mask[k - 2] == "."):
            i = k - 1
            while i > 0 and code[i - 1].isspace():
                i -= 1
            continue
        return i


def previous_token(code: str, mask: str, start: int) -> str:
    i = start
    while i > 0 and code[i - 1].isspace():
        i -= 1
    if i == 0:
        return ""
    two = mask[max(0, i - 2):i]
    if two in {"&&", "||", "->", "==", "!=", "<=", ">=", "+=", "-=", "*=", "/="}:
        return two
    if re.match(r"[\w$]", mask[i - 1]):
        return re.search(r"[\w$]+$", mask[:i]).group()
    return mask[i - 1]


def next_token(mask: str, end: int) -> str:
    rest = mask[end:].lstrip()
    if not rest:
        return ""
    return rest[:2] if rest[:2] in {"&&", "||", "==", "!=", "<=", ">=", "->"} else rest[0]


# Where a comparison may be rewritten as a call: its neighbours bind less tightly than == (or end the expression).
LOW_BEFORE = {"", "(", ",", "=", "&&", "||", "&", "|", "^", "?", ":", "{", "}", ";", "->", "return"}
LOW_AFTER = {"", ")", ",", ";", "&&", "||", "&", "|", "^", "?", ":", "}"}


def replace_all(code: str, edits: list[tuple[int, int, str]]) -> str:
    for start, end, text in sorted(edits, reverse=True):
        code = code[:start] + text + code[end:]
    return code


def literal_equals(code: str) -> tuple[str, int]:
    """x.equals("A") -> "A".equals(x); x == "A" -> "A".equals(x); x != "A" -> !"A".equals(x)."""
    mask = java_mask(code)
    edits, taken = [], []
    for start, end in literals(code):
        if any(s <= start < e for s, e in taken):
            continue
        before = mask[:start].rstrip()
        after = mask[end:]
        call = re.search(r"\.\s*(equals|equalsIgnoreCase)\s*\(\s*$", before)
        close = re.match(r"\s*\)", after)
        if call and close:
            receiver = operand_start(code, mask, call.start())
            if receiver is None or previous_token(code, mask, receiver) == "new":
                continue
            target = code[receiver:call.start()].strip()
            edits.append((receiver, end + close.end(), code[start:end] + "." + call.group(1) + "(" + target + ")"))
            taken.append((receiver, end + close.end()))
            continue
        operator = re.search(r"(==|!=)\s*$", before)
        if operator and next_token(mask, end) in LOW_AFTER:
            left_end = operator.start()
            left = operand_start(code, mask, left_end)
            if left is None or previous_token(code, mask, left) not in LOW_BEFORE:
                continue
            target = code[left:left_end].strip()
            prefix = "!" if operator.group(1) == "!=" else ""
            edits.append((left, end, prefix + code[start:end] + ".equals(" + target + ")"))
            taken.append((left, end))
    return replace_all(code, edits), len(edits)


SIZE = re.compile(r"\.\s*size\s*\(\s*\)\s*(==|!=|<=|>=|<|>)\s*([01])(?![\w.])")
EMPTY = {("==", "0"): True, ("<", "1"): True, ("<=", "0"): True, ("!=", "0"): False, (">", "0"): False, (">=", "1"): False}


def collection_empty(code: str) -> tuple[str, int]:
    mask = java_mask(code)
    edits = []
    for match in SIZE.finditer(mask):
        empty = EMPTY.get((match.group(1), match.group(2)))
        if empty is None:
            continue
        start = operand_start(code, mask, match.start())
        if start is None or previous_token(code, mask, start) not in LOW_BEFORE or next_token(mask, match.end()) not in LOW_AFTER:
            continue
        edits.append((start, match.end(), ("" if empty else "!") + code[start:match.start()].strip() + ".isEmpty()"))
    return replace_all(code, edits), len(edits)


def simple(code: str, pattern: str, replacement, skip=None) -> tuple[str, int]:
    """A code-only pattern (matched in the mask) and its fixed replacement; skip(mask, match) keeps a match."""
    mask = java_mask(code)
    edits = [(m.start(), m.end(), replacement(m) if callable(replacement) else replacement)
             for m in re.finditer(pattern, mask) if not (skip and skip(mask, m))]
    return replace_all(code, edits), len(edits)


def static_call(owner: str):
    """skip= for a pattern that is right as a static call of `owner` (Collectors.toList() is Java 8)."""
    return lambda mask, match: re.search(r"(?<![\w$.])" + owner + r"\s*$", mask[:match.start()]) is not None


def big_decimal_literals(code: str) -> tuple[str, int]:
    """new BigDecimal(0 | 1 | 10 | "0" | "1" | "10") -> the constant; new BigDecimal(1.5) -> new BigDecimal("1.5")."""
    names = {"0": "ZERO", "1": "ONE", "10": "TEN"}
    code, count = simple(code, r"\bnew\s+BigDecimal\s*\(\s*(0|1|10)\s*\)", lambda m: "BigDecimal." + names[m.group(1)])
    mask = java_mask(code)
    edits = [(m.start(), m.end(), "BigDecimal." + names[m.group(1)])
             for m in re.finditer(r'\bnew\s+BigDecimal\s*\(\s*"(0|1|10)"\s*\)', code) if mask[m.start()] == "n"]
    code = replace_all(code, edits)
    decimal = r"\bnew\s+BigDecimal\s*\(\s*(-?)\s*((?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?)[dD]?\s*\)"
    code, decimals = simple(code, decimal, lambda m: 'new BigDecimal("' + m.group(1) + m.group(2) + '")')
    return code, count + len(edits) + decimals


REPAIRS = (
    ("stream.toList() -> collect(Collectors.toList()) (Java 11)",
     lambda code: simple(code, r"\.\s*toList\s*\(\s*\)", ".collect(Collectors.toList())", static_call("Collectors"))),
    ("list.getFirst() -> list.get(0) (Java 11)",
     lambda code: (code, 0) if re.search(r"\b(?:Deque|LinkedList)\b", java_mask(code))
     else simple(code, r"\.\s*getFirst\s*\(\s*\)", ".get(0)")),
    ('x.equals("A") / x == "A" -> "A".equals(x) (PMD: a literal first, a String never with ==)', literal_equals),
    ("size() == 0 -> isEmpty() (PMD UseCollectionIsEmpty)", collection_empty),
    ("toUpperCase() / toLowerCase() -> (Locale.ROOT) (PMD UseLocaleWithCaseConversions)",
     lambda code: simple(code, r"\.\s*(toUpperCase|toLowerCase)\s*\(\s*\)", lambda m: "." + m.group(1) + "(Locale.ROOT)")),
    ("new BigDecimal(0/1/10 vagy tizedes tört) -> BigDecimal.ZERO/ONE/TEN, new BigDecimal(\"1.5\") (PMD)",
     big_decimal_literals),
)


def repair(code: str, notes: list) -> str:
    """The Java 11 / PMD slips with one meaning, repaired in the code; a Hungarian note for each kind."""
    done = []
    for label, step in REPAIRS:
        code, count = step(code)
        if count:
            done.append(label)
    if done:
        notes.append("Helyben javítva (Java 11 / PMD): " + "; ".join(done) + ".")
    return code


# Java 12+ that javac --release 11 rejects (or, for the API, compiles nowhere): (pattern, Hungarian, English).
JAVA11 = (
    (r"(?<![\w$.])record\s+[A-Z][\w$]*\s*[(<]", "record (Java 16)",
     "Java 11 has no records: write a private static final class with fields and a constructor, or use the DTOs."),
    (r"\bcase\b[^:;{}]*?->|\bdefault\s*->|(?<![\w$.])yield\s+[^;=\s]", "switch-kifejezés (Java 14)",
     "Java 11 has no switch expressions (case X -> / yield): use if/else or a classic switch with case X: ... break;."),
    (r"\binstanceof\s+(?:final\s+)?[A-Za-z_$][\w$.]*(?:\s*<[^<>;]*(?:<[^<>;]*>[^<>;]*)*>)?(?:\s*\[\s*\])*\s+[A-Za-z_$][\w$]*",
     "instanceof-minta (Java 16)",
     "Java 11 has no instanceof patterns (x instanceof String s): test with instanceof, then cast: (String) x."),
    (r"\bsealed\s+(?:abstract\s+)?(?:class|interface)\b|\bnon-sealed\b|\bpermits\s+[A-Z]", "sealed osztály (Java 17)",
     "Java 11 has no sealed classes; do not create classes at all."),
    (r"\.\s*formatted\s*\(", "String.formatted() (Java 15)",
     'Java 11 has no String.formatted(): write String.format("...", args).'),
    (r"\.\s*(?:stripIndent|translateEscapes)\s*\(", "String.stripIndent()/translateEscapes() (Java 15)",
     "Java 11 has no String.stripIndent()/translateEscapes()."),
    (r"\bCollectors\s*\.\s*teeing\b", "Collectors.teeing() (Java 12)",
     "Java 11 has no Collectors.teeing(): collect twice, or loop."),
    (r"\.\s*mapMulti\s*\(", "Stream.mapMulti() (Java 16)", "Java 11 has no Stream.mapMulti(): use flatMap or a loop."),
    (r"\bHexFormat\b", "HexFormat (Java 17)", "Java 11 has no HexFormat."),
)


def problems(code: str) -> list[tuple[str, str]]:
    """(Hungarian for the developer, English for the model) of every Java 12+ construct of the code."""
    mask = java_mask(code)
    found = []
    if '"""' in without_comments(code):
        found.append(("A kód szövegblokkot (\"\"\", Java 15) használ; a cél Java 11.",
                      'Java 11 has no text blocks ("""): write a normal string literal; join long SQL with +.'))
    for pattern, hu, en in JAVA11:
        if re.search(pattern, mask):
            found.append((f"A kód Java 11-ben nem létező elemet használ: {hu}.", en))
    if not re.search(r"\b(?:Deque|LinkedList)\b", mask) and re.search(r"\.\s*(?:getLast|removeLast|removeFirst)\s*\(\s*\)", mask):
        found.append(("A kód List.getLast()/removeLast()/removeFirst() hívást használ (Java 21); a cél Java 11.",
                      "Java 11 List has no getLast()/removeLast()/removeFirst(): use list.get(list.size() - 1) / "
                      "list.remove(list.size() - 1) / list.remove(0)."))
    return found


# What PMD reports and the migrator cannot rewrite safely: shown to the developer only.
PMD_WARNINGS = (
    (r"\bcatch\s*\([^()]*\)\s*\{\s*\}", "üres catch blokk (PMD EmptyCatchBlock): a hiba elnyelése megváltoztathatja a Forms-működést"),
    (r"\.\s*printStackTrace\s*\(", "printStackTrace() (PMD AvoidPrintStackTrace): naplózás helyett"),
    (r"\bSystem\s*\.\s*(?:out|err)\s*\.", "System.out/err (PMD SystemPrintln): a log mezőt kell használni"),
    (r"\bcatch\s*\(\s*(?:final\s+)?(?:Exception|Throwable|RuntimeException|Error|NullPointerException)\s+[\w$]+\s*\)",
     "általános kivétel elkapása (PMD AvoidCatchingGenericException/Throwable/NPE): a konkrét kivételt kell elkapni"),
)


def warnings(code: str) -> list[str]:
    mask = java_mask(code)
    return ["PMD-figyelmeztetés: " + text + "." for pattern, text in PMD_WARNINGS if re.search(pattern, mask)]

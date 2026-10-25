"""Oracle datetime format models (TO_CHAR / TO_DATE) as java.time DateTimeFormatter patterns.

An Oracle format model is not a Java pattern: MM is the month in both, but MI is the minute (Java: mm), DD the day of
the month (Java DD: the day of the year), YYYY the calendar year (Java YYYY: the week-based year), HH24 has no Java
spelling. Only elements with an exact equivalent are converted. Month and day names, AM/PM and week numbers depend on
the NLS settings of the Oracle session, Julian days and seconds past midnight have no pattern letter: a format with
any of them gets no conversion (None), so nothing is guessed.
"""
from __future__ import annotations

import re

# Longest first: HH24 before HH, DDD before DD, YYYY before YY.
NUMERIC = [("HH24", "HH", "H"), ("HH12", "hh", "h"), ("YYYY", "yyyy", "yyyy"), ("RRRR", "yyyy", "yyyy"),
           ("DDD", "DDD", "D"), ("HH", "hh", "h"), ("MI", "mm", "m"), ("SS", "ss", "s"), ("MM", "MM", "M"),
           ("DD", "dd", "d"), ("YY", "yy", "yy")]
SEPARATORS = set(" -/,.;:")


def java_pattern(model: str, parse: bool = False) -> str | None:
    """The DateTimeFormatter pattern of an Oracle datetime format model, or None without an exact equivalent.

    parse=True (TO_DATE): without FX, Oracle accepts 1 or 2 digits for a numeric element, so the single-letter Java
    form is used where a separator follows it (adjacent numbers, such as YYYYMMDD, keep their fixed width).
    """
    text, i, out, fill, exact = model, 0, [], False, False
    tokens = []
    while i < len(text):
        c = text[i]
        if c == '"':
            end = text.find('"', i + 1)
            if end < 0:
                return None
            tokens.append(("literal", text[i + 1:end]))
            i = end + 1
            continue
        if c in SEPARATORS:
            tokens.append(("literal", c))
            i += 1
            continue
        upper = text[i:].upper()
        if upper.startswith("FM"):
            fill = not fill
            i += 2
            continue
        if upper.startswith("FX"):
            exact = not exact
            i += 2
            continue
        for element, padded, single in NUMERIC:
            if upper.startswith(element):
                tokens.append(("number", element, padded, single, fill, exact))
                i += len(element)
                break
        else:
            # MON, DAY, AM, RR, WW, J, SSSSS, FF ...: NLS-dependent or no pattern letter
            return None
    for n, token in enumerate(tokens):
        if token[0] == "literal":
            if token[1]:
                # Letters (and the quote) are pattern syntax in Java: a literal text is quoted.
                out.append(token[1] if all(ch in SEPARATORS for ch in token[1]) else "'" + token[1].replace("'", "''") + "'")
            continue
        _, element, padded, single, filled, fixed = token
        if parse and element in {"HH", "HH12"}:
            return None  # a 12-hour clock without AM/PM: java.time cannot resolve the hour of the day
        if filled:
            out.append(single)  # FM: no padding (Oracle 1.5.2024, Java d.M.yyyy)
        elif parse and not fixed:
            neighbours = [tokens[k] for k in (n - 1, n + 1) if 0 <= k < len(tokens)]
            adjacent = any(t[0] == "number" for t in neighbours)
            out.append(padded if adjacent else single)
        else:
            out.append(padded)
    return "".join(out)


def has_time(model: str) -> bool:
    """True when the format model has an hour, minute or second element (TO_DATE: LocalDateTime, not LocalDate)."""
    return re.search(r"HH24|HH12|HH|MI|SS", re.sub(r'"[^"]*"', "", model), re.I) is not None


def is_number_format(model: str) -> bool:
    """A number format model (9, 0, G, D, L, $ ...): TO_CHAR(number, fmt), not a datetime one."""
    stripped = re.sub(r'"[^"]*"', "", model).upper().replace("FM", "")
    return bool(stripped) and re.fullmatch(r"[90.,GDLCUVBS$EMIPRTXN+-]+", stripped) is not None and re.search(r"[90]", stripped) is not None


def format_calls(code: str) -> list[tuple[str, str]]:
    """(function, format) of every TO_CHAR / TO_DATE / TO_TIMESTAMP call with a literal format, in code order."""
    found = []
    for match in re.finditer(r"\b(TO_CHAR|TO_DATE|TO_TIMESTAMP)\s*\(", code, re.I):
        depth, i, args, start, quoted = 1, match.end(), [], match.end(), False
        while i < len(code) and depth:
            c = code[i]
            if quoted:
                if c == "'" and code[i + 1:i + 2] == "'":
                    i += 2
                    continue
                if c == "'":
                    quoted = False
            elif c == "'":
                quoted = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    args.append(code[start:i].strip())
            elif c == "," and depth == 1:
                args.append(code[start:i].strip())
                start = i + 1
            i += 1
        if len(args) >= 2 and re.fullmatch(r"'(?:[^']|'')*'", args[1]):
            pair = (match.group(1).upper(), args[1][1:-1].replace("''", "'"))
            if pair not in found:
                found.append(pair)
    return found

"""Static SQL table evidence and explicit, versioned user-supplied table metadata.

Names are collected from executable token positions, never comments or strings.
This is a bounded lexical recognizer, not Oracle name resolution or a SQL parser.
Unknown/dynamic/quoted objects remain visible diagnostics rather than guessed
tables, column types, primary keys or write permissions.
"""
from __future__ import annotations

from copy import deepcopy
import csv
import hashlib
import io
import json
from functools import lru_cache
from pathlib import Path
import re

from .common import MigrationError, read_json
from .discovery import KEYWORDS, lex, source_view
from .web.file_io import atomic_json

AREA = "analysis/backend-assistant"
IDENT = re.compile(r"[A-Z][A-Z0-9_$#]{0,127}")
TABLE = re.compile(r"[A-Z][A-Z0-9_$#]{0,127}(?:\.[A-Z][A-Z0-9_$#]{0,127})?")
TYPES = {"VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR", "NUMBER", "FLOAT", "DATE", "TIMESTAMP", "CLOB", "NCLOB", "BLOB", "RAW", "BINARY_FLOAT", "BINARY_DOUBLE", "TIMESTAMP WITH TIME ZONE", "TIMESTAMP WITH LOCAL TIME ZONE"}
CHAR_TYPES = {"VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR"}
NUMERIC = {"NUMBER", "FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE"}
STOP_FROM = {"WHERE", "GROUP", "ORDER", "HAVING", "CONNECT", "START", "UNION", "MINUS", "INTERSECT", "RETURNING", "FETCH", "FOR", "SET"}


def is_oracle_dual(table: str) -> bool:
    """Oracle's built-in one-row source needs no user schema or repository."""
    return table.strip().upper() in {"DUAL", "SYS.DUAL"}


def parse_table_csv(text: str, table: str) -> dict:
    """Preview an IntelliJ column export without modifying any saved metadata.

    The six-column form contains DATA_LENGTH in bytes, not declared character
    semantics. NUMBER's storage length must never become precision or length.
    Eight-column exports also supply CHAR_LENGTH and CHAR_USED. A header is
    optional in the documented six/eight-column order; other orders need one.
    """
    if not TABLE.fullmatch(table) or is_oracle_dual(table):
        raise MigrationError("Válassz valódi adatbázistáblát a CSV beolvasása előtt.")
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 1_000_000:
        raise MigrationError("Nem üres, legfeljebb 1 MB méretű CSV szükséges.")
    text = text.lstrip("\ufeff")
    first = next((line for line in text.splitlines() if line.strip()), "")
    try:
        delimiter = max((",", ";", "\t"), key=lambda value: len(next(csv.reader([first], delimiter=value))))
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        records = [(reader.line_num, row) for row in reader if any(cell.strip() for cell in row)]
    except csv.Error as exc:
        raise MigrationError("Hibás CSV-formátum vagy túl hosszú mező: " + str(exc)) from exc
    if not records:
        raise MigrationError("A CSV nem tartalmaz oszlopadatot.")
    fields = ["COLUMN_NAME", "DATA_TYPE", "DATA_LENGTH", "DATA_PRECISION", "DATA_SCALE", "NULLABLE"]
    aliases = {"OSZLOP_NEVE": "COLUMN_NAME", "OSZLOPNEV": "COLUMN_NAME", "OSZLOPNÉV": "COLUMN_NAME"}
    header = [aliases.get(cell.strip().upper(), cell.strip().upper()) for cell in records[0][1]]
    has_header = "COLUMN_NAME" in header and "DATA_TYPE" in header
    if has_header:
        if len(set(header)) != len(header) or set(fields) - set(header):
            raise MigrationError("A CSV fejlécében egyszer szerepeljen: " + ", ".join(fields))
        allowed = set(fields) | {"CHAR_LENGTH", "CHAR_USED", "OWNER", "TABLE_NAME"}
        if set(header) - allowed:
            raise MigrationError("Ismeretlen CSV-oszlop: " + ", ".join(sorted(set(header) - allowed)))
        fields = header
        records = records[1:]
    elif len(records[0][1]) == 8:
        fields += ["CHAR_LENGTH", "CHAR_USED"]
    elif len(records[0][1]) != 6:
        line, cells = records[0]
        raise MigrationError(f"CSV {line}. sor: fejléc nélkül 6 vagy 8 mező szükséges, {len(cells)} érkezett. "
                             "A bővített sorrend: COLUMN_NAME, DATA_TYPE, DATA_LENGTH, DATA_PRECISION, "
                             "DATA_SCALE, NULLABLE, CHAR_LENGTH, CHAR_USED.")
    if not 1 <= len(records) <= 1000:
        raise MigrationError("A CSV legalább 1, legfeljebb 1000 adatsort tartalmazhat.")
    columns, names, owners, warnings = [], set(), set(), set()

    def integer(value: str, label: str, line: int):
        if value.upper() in {"", "NULL", "<NULL>"}: return None
        if not re.fullmatch(r"-?\d{1,9}", value):
            raise MigrationError(f"CSV {line}. sor: {label} egész szám vagy üres érték lehet.")
        return int(value)

    for line, cells in records:
        if len(cells) != len(fields):
            raise MigrationError(f"CSV {line}. sor: {len(fields)} mező szükséges, {len(cells)} érkezett.")
        data = dict(zip(fields, (cell.strip() for cell in cells)))
        name = data["COLUMN_NAME"].upper()
        if not IDENT.fullmatch(name) or name in names:
            raise MigrationError(f"CSV {line}. sor: hibás vagy ismétlődő oszlopnév ({name}). Csak egy tábla és séma exportját add meg.")
        names.add(name)
        owner, table_name = data.get("OWNER", "").upper(), data.get("TABLE_NAME", "").upper()
        if owner: owners.add(owner)
        if (table_name and table_name != table.rsplit(".", 1)[-1]) or (owner and "." in table and owner != table.split(".")[0]):
            raise MigrationError(f"CSV {line}. sor: az export másik táblához vagy sémához tartozik.")
        if len(owners) > 1:
            raise MigrationError("A CSV több séma adatait tartalmazza. Szűkítsd a lekérdezést egy OWNER értékre.")
        nullable = data["NULLABLE"].upper()
        if nullable not in {"Y", "N"}:
            raise MigrationError(f"CSV {line}. sor: a NULLABLE értéke Y vagy N legyen.")
        data_type = re.sub(r"\s+", " ", data["DATA_TYPE"].upper())
        timestamp = re.fullmatch(r"TIMESTAMP\((\d)\)(.*)", data_type)
        if timestamp: data_type = "TIMESTAMP" + timestamp[2]
        if data_type not in TYPES:
            raise MigrationError(f"CSV {line}. sor: nem támogatott Oracle-típus ({data_type}).")
        length = integer(data["DATA_LENGTH"], "DATA_LENGTH", line)
        precision = integer(data["DATA_PRECISION"], "DATA_PRECISION", line)
        scale = integer(data["DATA_SCALE"], "DATA_SCALE", line)
        char_length = integer(data.get("CHAR_LENGTH", ""), "CHAR_LENGTH", line)
        char_used = data.get("CHAR_USED", "").upper()
        if char_used in {"NULL", "<NULL>"}: char_used = ""
        if char_used not in {"", "B", "C"}:
            raise MigrationError(f"CSV {line}. sor: a CHAR_USED értéke B, C vagy üres legyen.")
        if char_length is not None and char_length < 0:
            raise MigrationError(f"CSV {line}. sor: a CHAR_LENGTH nem lehet negatív.")
        if data_type not in CHAR_TYPES and (char_used or char_length not in {None, 0}):
            raise MigrationError(f"CSV {line}. sor: CHAR_LENGTH/CHAR_USED karakterhossz csak szövegtípushoz adható meg.")
        row = {"name": name, "data_type": data_type, "nullable": nullable == "Y",
               "length": None, "length_semantics": "", "precision": None, "scale": None,
               "default": "", "comment": ""}
        if data_type in CHAR_TYPES:
            if char_used == "C" or data_type in {"NCHAR", "NVARCHAR2"}:
                if char_length is None:
                    raise MigrationError(f"CSV {line}. sor: CHAR/NCHAR hosszhoz CHAR_LENGTH is kell; használd a bővített exportot.")
                row.update(length=char_length, length_semantics="CHAR")
            else:
                row.update(length=length, length_semantics="BYTE")
                if not char_used:
                    warnings.add("A CSV csak bytehosszt közöl: a szövegoszlopok BYTE-korláttal töltődtek ki. Az eredeti CHAR/BYTE deklaráció ebből nem állapítható meg; CHAR használatnál add meg a karakterhosszt, vagy importáld a bővített exportot.")
        elif data_type == "RAW": row.update(length=length, length_semantics="BYTE")
        if data_type in {"NUMBER", "FLOAT"}: row["precision"] = precision
        if data_type == "NUMBER": row["scale"] = scale
        if data_type.startswith("TIMESTAMP"):
            row["precision"] = int(timestamp[1]) if timestamp else scale
        columns.append(row)
    # Reuse the editor's type/range validation before returning a complete draft.
    validate_table(table, columns, [])
    warnings.add("A CSV nem tartalmaz elsődleges kulcsot, alapértéket és megjegyzést. A kulcsot az adatbázis alapján jelöld; a NOT NULL önmagában nem jelent PK-t.")
    return {"table": table, "columns": columns, "warnings": sorted(warnings)}


class SchemaConflict(MigrationError):
    pass


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def path_in(output: Path, relative: str) -> Path:
    from .backend_assistant import safe_file
    return safe_file(output, relative)


@lru_cache(maxsize=2048)
def table_references(source: str) -> dict:
    view, _ = source_view(source)
    tokens, warnings = lex(view)
    words = [t[0] for t in tokens]
    depths, parents, pairs, stack = [], [], {}, []
    for i, word in enumerate(words):
        depths.append(len(stack))
        parents.append(words[stack[-1] - 1] if stack and stack[-1] else "")
        if word == "(": stack.append(i)
        elif word == ")" and stack:
            opening = stack.pop(); pairs[opening] = i
    # CTE names are scoped to their SQL statement/subquery. A later real table
    # with the same name is not removed from the evidence.
    ctes = []
    for i, word in enumerate(words):
        if word != "WITH": continue
        end = next((n for n in range(i + 1, len(words)) if words[n] == ";" or depths[n] < depths[i]), len(words))
        at = i + 1
        while at < end and IDENT.fullmatch(words[at]):
            cte = words[at]; at += 1
            if at < end and words[at] == "(": at = pairs.get(at, end) + 1
            if at + 1 >= end or words[at:at + 2] != ["AS", "("]: break
            ctes.append((cte, i, end))
            at = pairs.get(at + 1, end) + 1
            if at >= end or words[at] != ",": break
            at += 1
    # A nested SELECT owns its aliases. Ordinary expression parentheses do not.
    # Deliberately do not infer correlated outer columns from inner SQL: CTE,
    # derived-table and function aliases can shadow an outer physical table.
    query_starts = {at: close for at, close in pairs.items()
                    if at + 1 < len(words) and words[at + 1] in {"SELECT", "WITH"}}
    query_scopes, active_queries = [], []
    for i in range(len(words)):
        while active_queries and i > query_starts[active_queries[-1]]: active_queries.pop()
        if i in query_starts: active_queries.append(i)
        query_scopes.append(active_queries[-1] if active_queries else -1)
    found, from_depths = [], set()
    segment = 0

    def add(at: int, operation: str):
        if at >= len(words): return
        word = words[at]
        after_name = at + 1
        # Oracle also accepts whitespace around schema qualification. A quoted
        # final identifier is masked by lex; never turn its owner into a table.
        while after_name < len(words) and words[after_name] == ".":
            if after_name + 1 >= len(words) or not IDENT.fullmatch(words[after_name + 1]):
                warnings.append("Összetett/idézőjeles objektumnév: kézi azonosítás szükséges."); return
            word += "." + words[after_name + 1]
            after_name += 2
        if word in {"TABLE", "XMLTABLE", "JSON_TABLE"} and at + 1 < len(words) and words[at + 1] == "(":
            warnings.append("Táblafüggvény/kollekció: a tényleges adatforrás külön ellenőrzendő."); return
        if not TABLE.fullmatch(word) or word in KEYWORDS or is_oracle_dual(word): return
        if any(cte == word and start <= at < end for cte, start, end in ctes): return
        if after_name < len(words) and words[after_name] == "@":
            warnings.append("Adatbázis-link: " + word + "@…; a távoli séma és objektum kézi azonosítást igényel."); return
        aliases = {word, word.rsplit(".", 1)[-1]}
        after = after_name
        if after < len(words) and words[after] == "AS": after += 1
        if after < len(words) and IDENT.fullmatch(words[after]) and words[after] not in KEYWORDS | {"LEFT", "RIGHT", "FULL", "INNER", "OUTER", "CROSS", "NATURAL", "PARTITION", "SAMPLE", "MATCHED"}:
            aliases.add(words[after])
        columns = set()
        # INSERT target column list, not SELECT INTO variables or function args.
        if operation == "INSERT" and after_name < len(words) and words[after_name] == "(":
            close = pairs.get(after_name, after_name)
            listed = words[after_name + 1:close]
            if all(w == "," or IDENT.fullmatch(w) for w in listed): columns.update(w for w in listed if w != ",")
        found.append({"table": word, "operation": operation, "line": view.count("\n", 0, tokens[at][1]) + 1,
                      "columns": columns, "aliases": aliases, "segment": segment, "scope": query_scopes[at]})

    for i, word in enumerate(words):
        previous = words[i - 1] if i else ""
        next_word = words[i + 1] if i + 1 < len(words) else ""
        if word == ";":
            from_depths.clear(); segment += 1; continue
        if word == ")": from_depths.discard(depths[i])
        if word in STOP_FROM: from_depths.discard(depths[i])
        if word in {"FROM", "JOIN"} and parents[i] not in {"EXTRACT", "TRIM"}:
            from_depths.add(depths[i]); add(i + 1, "DELETE" if previous == "DELETE" else "SELECT")
        elif word == "," and depths[i] in from_depths:
            add(i + 1, "SELECT")
        elif word == "UPDATE" and previous not in {"FOR", "ON", "BEFORE", "AFTER"} and next_word not in {"SET", "OF"}:
            add(i + 1, "UPDATE")
        elif word == "INTO" and previous in {"INSERT", "MERGE"}:
            add(i + 1, previous)
        elif word == "DELETE" and next_word != "FROM" and previous not in {"ON", "BEFORE", "AFTER"}:
            add(i + 1, "DELETE")
        elif word == "USING" and any(r["operation"] == "MERGE" and r["segment"] == segment for r in found):
            add(i + 1, "SELECT")
        elif word == "%" and next_word in {"TYPE", "ROWTYPE"}:
            pieces = previous.split(".")
            table = ".".join(pieces[:-1]) if next_word == "TYPE" else previous
            if TABLE.fullmatch(table):
                found.append({"table": table, "operation": "%" + next_word, "line": view.count("\n", 0, tokens[i][1]) + 1,
                              "columns": {pieces[-1]} if next_word == "TYPE" else set(), "aliases": {table},
                              "segment": segment, "scope": query_scopes[i]})
    for segment_id, scope_id in {(r["segment"], r["scope"]) for r in found}:
        aliases = {}
        for entry in found:
            if (entry["segment"], entry["scope"]) == (segment_id, scope_id):
                for alias in entry["aliases"]: aliases.setdefault(alias, set()).add(entry["table"])
        current = 0
        for i, word in enumerate(words):
            if word == ";": current += 1; continue
            if (current, query_scopes[i]) != (segment_id, scope_id) or word.startswith(":") or "." not in word: continue
            alias, column = word.rsplit(".", 1)
            targets = aliases.get(alias, set())
            if len(targets) == 1 and IDENT.fullmatch(column):
                for entry in found:
                    if (entry["segment"], entry["scope"]) == (segment_id, scope_id) and entry["table"] in targets:
                        entry["columns"].add(column)
    if any(words[i:i + 2] == ["EXECUTE", "IMMEDIATE"] for i in range(len(words) - 1)):
        warnings.append("Dinamikus SQL: a futásidejű táblanevek ebből a forrásból nem bizonyíthatók.")
    unique = {}
    for entry in found:
        if is_oracle_dual(entry["table"]): continue
        key = (entry["table"], entry["operation"], entry["line"])
        unique.setdefault(key, set()).update(entry["columns"])
    return {"tables": [{"table": t, "operation": op, "line": line, "columns": sorted(cols)}
                       for (t, op, line), cols in sorted(unique.items())], "warnings": sorted(set(warnings))}


def load_schema(output: Path) -> tuple[dict, dict, list[str]]:
    effective = read_json(path_in(output, "analysis/effective-config.json"))
    metadata = deepcopy(effective.get("metadata", {}))
    tables = metadata.setdefault("tables", {})
    if not isinstance(tables, dict): raise MigrationError("schema.json: tables objektum szükséges.")
    if len({str(k).upper() for k in tables}) != len(tables): raise MigrationError("Ütköző táblanevek a sémában.")
    metadata["tables"] = {str(k).upper(): v for k, v in tables.items()}
    file = path_in(output, AREA + "/schema-overrides.json")
    overrides = read_json(file) if file.is_file() else {"version": 1, "tables": {}}
    conflicts = []
    for table, entry in overrides.get("tables", {}).items():
        base = metadata["tables"].get(table)
        if fingerprint(base) != entry.get("expected_base") and base != entry.get("definition"):
            conflicts.append(table)
        else:
            metadata["tables"][table] = deepcopy(entry["definition"])
    return metadata, overrides, conflicts


def schema_revision(metadata: dict, overrides: dict) -> str:
    return fingerprint({"schema": metadata, "overrides": overrides})


def schema_sheet(output: Path, codes: list[dict] | None = None, blocks: list[dict] | None = None, bundle=None) -> dict:
    whole_module = codes is None and blocks is None
    metadata, overrides, conflicts = bundle or load_schema(output)
    if codes is None:
        codes = read_json(path_in(output, "analysis/discovery/form-map.json")).get("code", [])
    if blocks is None:
        blocks = read_json(path_in(output, "analysis/form.ir.json")).get("blocks", [])
    references, warnings = {}, []
    known_tables = {str(b.get("table") or "").upper() for b in blocks if b.get("database")} | set(metadata["tables"])
    for block in blocks:
        table = str(block.get("table") or "").strip().upper()
        if block.get("database") and TABLE.fullmatch(table):
            references.setdefault(table, []).append({"owner": block["name"], "operation": "Forms-blokk", "line": None, "columns": []})
    for code in codes:
        scan = table_references(code.get("source_view", code.get("source", "")))
        for ref in scan["tables"]:
            if ref["operation"] in {"%TYPE", "%ROWTYPE"} and ref["table"] not in known_tables:
                warnings.append(code.get("owner", "") + ": " + ref["table"] + ref["operation"] + " lehet tábla, kurzor vagy rekord; kézi névfeloldás szükséges.")
                continue
            references.setdefault(ref["table"], []).append({**ref, "owner": code.get("owner", ""), "source_id": code.get("id", "")})
        warnings += [code.get("owner", "") + ": " + w for w in scan["warnings"]]
    # Existing imported definitions are editable/exportable even when not used
    # in the selected task; no implicit owner/schema-name resolution is done.
    tables = {t for t in set(references) | (set(metadata["tables"]) if whole_module else set()) if not is_oracle_dual(t)}
    result = []
    for table in sorted(tables):
        spec = metadata["tables"].get(table)
        reasons = []
        columns = spec.get("columns", {}) if isinstance(spec, dict) else {}
        if not isinstance(columns, dict): columns = {}
        if not columns: reasons.append("A tábla oszlopai nincsenek megadva.")
        for column, definition in columns.items():
            if not isinstance(definition, dict) or not definition.get("data_type") or type(definition.get("nullable")) is not bool:
                reasons.append(column + ": Oracle-típus és nullable szükséges.")
        if isinstance(spec, dict) and not isinstance(spec.get("primary_key"), list):
            reasons.append("Az elsődleges kulcs listája nincs megadva (kulcs nélküli táblánál üres lista).")
        keys = spec.get("primary_key", []) if isinstance(spec, dict) else []
        if isinstance(keys, list) and (not all(isinstance(k, str) and k in columns for k in keys)
                or any(isinstance(columns.get(k), dict) and columns[k].get("nullable") is True for k in keys if isinstance(k, str))):
            reasons.append("Az elsődleges kulcs oszlopai vagy nullable beállításai ellentmondásosak.")
        expected = sorted({c for ref in references.get(table, []) for c in ref.get("columns", [])})
        missing = sorted(set(expected) - set(columns))
        if missing: reasons.append("Az SQL-ben hivatkozott, még hiányzó oszlopok: " + ", ".join(missing))
        if table in conflicts: reasons.append("Az új séma és a korábbi kézi definíció eltér; ellenőrizd újra a szerkezetet.")
        result.append({"table": table, "status": "needs_schema" if reasons else "ready", "reasons": reasons,
                       "definition": spec, "references": references.get(table, []), "referenced_columns": expected,
                       "provenance": (spec or {}).get("source", "imported_schema") if isinstance(spec, dict) else "missing",
                       "conflict": table in conflicts})
    return {"version": 1, "revision": schema_revision(metadata, overrides), "tables": result,
            "pending": [t["table"] for t in result if t["status"] != "ready"], "warnings": sorted(set(warnings)),
            "notice": "A kézi szerkezet felhasználói adat, nem élő adatbázis-ellenőrzés. Írást nem engedélyez."}


def validate_table(table: str, columns: list, primary_key: list) -> dict:
    if not isinstance(table, str) or not TABLE.fullmatch(table.strip().upper()):
        raise MigrationError("Tábla: egyszerű TABLE vagy OWNER.TABLE név szükséges.")
    if not isinstance(columns, list) or not 1 <= len(columns) <= 1000:
        raise MigrationError("Legalább egy, legfeljebb 1000 oszlopot adj meg.")
    normalized = {}
    allowed = {"name", "data_type", "nullable", "length", "length_semantics", "precision", "scale", "default", "comment"}
    for row in columns:
        if not isinstance(row, dict) or set(row) - allowed: raise MigrationError("Ismeretlen oszlopbeállítás.")
        column = str(row.get("name") or "").strip().upper()
        data_type = re.sub(r"\s+", " ", str(row.get("data_type") or "").strip().upper())
        if not IDENT.fullmatch(column) or column in normalized:
            raise MigrationError("Hibás vagy ismétlődő oszlopnév: " + column)
        if data_type not in TYPES: raise MigrationError(column + ": válassz támogatott Oracle-adattípust.")
        if type(row.get("nullable")) is not bool: raise MigrationError(column + ": a nullable értékét külön add meg.")
        spec = {"data_type": data_type, "nullable": row["nullable"],
                "type": "text" if data_type in CHAR_TYPES else "number" if data_type in NUMERIC else "datetime" if data_type in {"DATE", "TIMESTAMP"} else "unsupported"}
        length, precision, scale = row.get("length"), row.get("precision"), row.get("scale")
        semantics = row.get("length_semantics")
        if length is not None:
            if type(length) is not int or not 1 <= length <= 32767 or data_type not in CHAR_TYPES | {"RAW"}:
                raise MigrationError(column + ": a hossz csak szöveg/RAW típusnál, 1..32767 között adható meg.")
            if data_type in CHAR_TYPES and semantics not in {"BYTE", "CHAR"}:
                raise MigrationError(column + ": a hosszhoz BYTE vagy CHAR mértékegység szükséges.")
            if data_type in {"NVARCHAR2", "NCHAR"} and semantics != "CHAR":
                raise MigrationError(column + ": NCHAR/NVARCHAR2 hossza karakterben értendő.")
            spec.update(length=length, length_semantics=semantics or "BYTE")
            if semantics == "CHAR": spec.update(char_length=length, max_length=length)
            else: spec["data_length"] = length
        elif data_type in CHAR_TYPES | {"RAW"}:
            raise MigrationError(column + ": add meg az oszlophosszt.")
        if precision is not None:
            timestamp = data_type.startswith("TIMESTAMP")
            lower, upper = (0, 9) if timestamp else (1, 126 if data_type == "FLOAT" else 38)
            if (data_type not in {"NUMBER", "FLOAT"} and not timestamp) or type(precision) is not int or not lower <= precision <= upper:
                raise MigrationError(column + f": hibás precision ({lower}..{upper}).")
            spec["datetime_precision" if timestamp else "precision"] = precision
        if scale is not None:
            if data_type != "NUMBER" or type(scale) is not int or not -84 <= scale <= 127:
                raise MigrationError(column + ": NUMBER scale -84..127 közötti egész lehet.")
            spec["scale"] = scale
        for key in ("default", "comment"):
            value = row.get(key, "")
            if not isinstance(value, str) or len(value) > 2000: raise MigrationError(column + ": túl hosszú " + key)
            if value: spec[key] = value
        normalized[column] = spec
    if not isinstance(primary_key, list) or not all(isinstance(k, str) for k in primary_key):
        raise MigrationError("Az elsődleges kulcs oszlopnévlista szükséges.")
    keys = [k.strip().upper() for k in primary_key]
    if len(set(keys)) != len(keys) or set(keys) - set(normalized): raise MigrationError("Hibás elsődlegeskulcs-lista.")
    if any(normalized[k]["nullable"] for k in keys): raise MigrationError("Elsődleges kulcs oszlopa nem lehet nullable.")
    return {"columns": normalized, "primary_key": keys, "source": "manual_table_editor", "confirmed": True}


def save_table(output: Path, table: str, columns: list, primary_key: list, revision: str, source_schema: Path | None = None) -> dict:
    metadata, overrides, _ = load_schema(output)
    if revision != schema_revision(metadata, overrides): raise SchemaConflict("A séma közben módosult. Töltsd újra a táblát mentés előtt.")
    table = table.strip().upper()
    definition = validate_table(table, columns, primary_key)
    base = read_json(path_in(output, "analysis/effective-config.json")).get("metadata", {}).get("tables", {})
    base = {str(k).upper(): v for k, v in base.items()}
    overrides.setdefault("tables", {})[table] = {"expected_base": fingerprint(base.get(table)), "definition": definition}
    metadata.setdefault("tables", {})[table] = definition
    changes = [(path_in(output, AREA + "/schema-overrides.json"), overrides), (path_in(output, "analysis/schema.json"), metadata)]
    if source_schema is not None: changes.append((source_schema, metadata))
    previous = [(p, p.read_bytes() if p.is_file() else None) for p, _ in changes]
    written = []
    try:
        for path, value in changes:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(path, value); written.append(path)
    except OSError:
        for path, old in reversed(previous):
            if path not in written: continue
            if old is None: path.unlink(missing_ok=True)
            else: path.write_bytes(old)
        raise
    return schema_sheet(output)

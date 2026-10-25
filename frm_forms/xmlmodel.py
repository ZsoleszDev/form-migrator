from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from .common import MigrationError, digest, unique_names


def canonical(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower().split("}")[-1])


def tag(element: ET.Element) -> str:
    return canonical(element.tag)


def props(element: ET.Element) -> dict[str, str]:
    result = {canonical(k): v for k, v in element.attrib.items()}
    for child in element:
        if tag(child) in {"property", "propertyvalue"}:
            key = child.get("Name") or child.get("name")
            if key:
                result[canonical(key)] = child.get("Value", child.get("value", child.text or ""))
        elif len(child) == 0 and tag(child) in {"triggertext", "programunittext", "recordgroupquery"}:
            result[tag(child)] = child.text or ""
    return result


PROPERTY_ALIASES = {
    'databasedatablock': ('DatabaseDataBlock', 'DatabaseBlock'),
    'dmldatatargetname': ('DMLDataTargetName', 'DMLDataName'),
    'dmldatatargettype': ('DMLDataTargetType', 'DMLDataType'),
    'whereclause': ('WhereClause', 'DefaultWhere'),
    'masterdatablock': ('MasterDataBlock', 'MasterBlock'),
    'detaildatablock': ('DetailDataBlock', 'DetailBlock'),
}


def get(p: dict, *keys: str, default: str = "") -> str:
    for key in keys:
        ck = canonical(key)
        if ck in PROPERTY_ALIASES:
            values = [p[canonical(a)] for a in PROPERTY_ALIASES[ck] if canonical(a) in p]
            normalized = {v.strip().upper() if ck != 'whereclause' else v for v in values}
            if ck == 'databasedatablock':
                normalized = {'true' if canonical(v) in {'true','yes','1','propertytrue'} else
                              'false' if canonical(v) in {'false','no','0','propertyfalse'} else v for v in values}
            if len(normalized) > 1:
                raise MigrationError('CONFLICTING_PROPERTY_ALIASES: '+', '.join(PROPERTY_ALIASES[ck]))
            if values: return values[0]
        if canonical(key) in p:
            return p[canonical(key)]
    if isinstance(p, TracedProperties):
        p.record_default(canonical(keys[0]), default)
    return default


def package_part(p: dict) -> str | None:
    """Only an explicit Forms ProgramUnitType distinguishes a package pair.

    Do not infer identity from its source text or from an unknown/numeric enum.
    """
    return {"packagespec": "spec", "packagebody": "body"}.get(canonical(get(p, "ProgramUnitType")))


class TracedProperties(dict):
    """Track existing parser fallbacks without inserting them into XML properties."""
    def __init__(self, values: dict, record: dict):
        super().__init__(values)
        self.record = record

    def record_default(self, key: str, value: str):
        previous = self.record["properties"].get(key)
        if previous is not None:
            if previous["source"] == "default" and previous["value"] != value:
                raise MigrationError(f"INCONSISTENT_PROPERTY_DEFAULT: {self.record['path']}: {key}: {previous['value']!r} / {value!r}")
            return
        self.record["properties"][key] = {"value": value, "source": "default", "origin": None, "via": [],
                                         "basis": "existing-generator-fallback"}


def yes(p: dict, *keys: str, default: bool = False) -> bool:
    value = get(p, *keys, default="yes" if default else "no")
    if canonical(value) in {"yes", "true", "1", "propertytrue"}:
        return True
    if canonical(value) in {"no", "false", "0", "propertyfalse"}:
        return False
    raise MigrationError(f"Nem értelmezhető Forms logikai property: {keys}: {value!r}. USE_PROPERTY_IDS=NO és angol XML szükséges.")


def integer(p: dict, *keys: str) -> int | None:
    value = get(p, *keys)
    if not value:
        return None
    try:
        number = int(value)
        return number if number > 0 else None
    except ValueError:
        return None


BOUNDARIES = {"formmodule", "block", "item", "trigger", "programunit", "recordgroup", "lov", "relation", "canvas", "window", "attachedlibrary", "objectgroup", "objectlibrary"}


def scoped(element: ET.Element, wanted: str):
    for child in element:
        kind = tag(child)
        if kind == wanted:
            yield child
        elif kind not in BOUNDARIES:
            yield from scoped(child, wanted)


def parse_xml(path: Path, max_bytes: int = 32 * 1024 * 1024, *, resolved_root: ET.Element | None = None,
              property_metadata: dict[int, dict] | None = None) -> dict:
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise MigrationError(f"Az XML nagyobb mint {max_bytes} bájt.")
    # Reject custom entities, including UTF-16 encodings; external SYSTEM DTD
    # declarations from Forms2XML are fine: ElementTree never fetches them.
    if b"<!entity" in raw.lower().replace(b"\x00", b""):
        raise MigrationError("Az XML egyedi ENTITY deklarációt tartalmaz; exportáld újra ENTITY nélkül.")
    try:
        root = ET.fromstring(raw) if resolved_root is None else resolved_root
    except ET.ParseError as exc:
        raise MigrationError(f"Hibás vagy nem támogatott XML: {exc}") from exc
    forms = [e for e in root.iter() if tag(e) == "formmodule"]
    if len(forms) != 1:
        raise MigrationError("Pontosan egy angol nevű FormModule kell. Az MMB/OLB nem önálló képernyőbemenet.")
    form = forms[0]
    property_cache = {}

    def properties(element):
        if property_metadata is None:
            return props(element)
        if id(element) not in property_cache:
            property_cache[id(element)] = TracedProperties(props(element), property_metadata[id(element)])
        return property_cache[id(element)]

    fp = properties(form)
    form_name = get(fp, "Name", default=path.stem).upper()
    form_owner = "@FORM:" + form_name
    model = {"ir_version": 1, "name": form_name, "title": get(fp, "Title", default=form_name), "properties": fp,
             "blocks": [], "triggers": [], "program_units": [], "record_groups": [], "lovs": [], "relations": [], "libraries": [],
             "issues": [], "inventory": dict(sorted(Counter(tag(e) for e in root.iter()).items())), "source_sha256": hashlib.sha256(raw).hexdigest()}

    def issue(code: str, owner: str, detail: str, scope: str = "review") -> None:
        model["issues"].append({"code": code, "owner": owner, "detail": detail, "scope": scope})

    def triggers(element: ET.Element, owner: str, block: str | None, item: str | None = None):
        for tr in scoped(element, "trigger"):
            p = properties(tr)
            source = get(p, "TriggerText", "Text", default=tr.text or "")
            event = get(p, "Name", "TriggerName").upper()
            model["triggers"].append({"id": owner + ":" + event, "owner": owner, "block": block, "item": item,
                                      "event": event, "source": source, "sha256": digest(source), "properties": p})

    triggers(form, form_name, None)
    for be in scoped(form, "block"):
        bp = properties(be)
        bn = get(bp, "Name").upper()
        if not bn:
            raise MigrationError("Névtelen adatblokk az XML-ben.")
        table = get(bp, "QueryDataSourceName", "DMLDataTargetName").upper()
        block = {"name": bn, "properties": bp, "table": table, "database": yes(bp, "DatabaseDataBlock", default=bool(table)), "items": [],
                 "query_allowed": yes(bp, "QueryAllowed", default=True), "insert_allowed": yes(bp, "InsertAllowed", default=True),
                 "update_allowed": yes(bp, "UpdateAllowed", default=True), "delete_allowed": yes(bp, "DeleteAllowed", default=True)}
        triggers(be, bn, bn)
        for ie in scoped(be, "item"):
            ip = properties(ie)
            item_name = get(ip, "Name").upper()
            if not item_name:
                raise MigrationError(f"Névtelen item: {bn}")
            it = canonical(get(ip, "ItemType", default="Text Item"))
            datatype = canonical(get(ip, "DataType", default="Character"))
            kind = {"textitem": "text", "displayitem": "display", "checkbox": "checkbox", "listitem": "select", "radiogroup": "radio", "pushbutton": "button", "button": "button"}.get(it, "unsupported")
            # Forms' own data types: ALPHA and LONG are text (LONG up to 32 KB through JDBC), MONEY/RNUMBER/RINT numbers,
            # EDATE/JDATE dates.
            value_type = {"char": "text", "character": "text", "varchar2": "text", "alpha": "text", "long": "text",
                          "number": "number", "integer": "number", "int": "number", "money": "number", "rnumber": "number",
                          "rint": "number", "rmoney": "number", "date": "datetime", "datetime": "datetime", "edate": "datetime",
                          "jdate": "datetime"}.get(datatype, "unsupported")
            options = []
            for opt in ie.iter():
                if tag(opt) in {"listitemelement", "listelement", "radiobutton"}:
                    op = properties(opt)
                    options.append({"label": get(op, "Label", "ListItemLabel", "Name"), "value": get(op, "Value", "ListItemValue", "RadioButtonValue")})
            item = {"name": item_name, "column": get(ip, "ColumnName", default=item_name).upper(), "kind": kind, "type": value_type,
                    "label": get(ip, "Prompt", "Label", default=item_name), "required": yes(ip, "Required"), "max_length": integer(ip, "MaximumLength", "MaxLength"),
                    "visible": yes(ip, "Visible", default=True), "enabled": yes(ip, "Enabled", default=True) and kind != "display", "concealed": yes(ip, "ConcealData"),
                    "insert_allowed": yes(ip, "InsertAllowed", default=True) and not yes(ip, "QueryOnly"), "update_allowed": yes(ip, "UpdateAllowed", default=True) and not yes(ip, "QueryOnly"),
                    # DUMP=ALL may include DatabaseItem defaults on control items.
                    "database": block["database"] and yes(ip, "DatabaseItem", default=True) and kind != "button", "primary_key": yes(ip, "PrimaryKey"),
                    "checked_value": get(ip, "CheckBoxCheckedValue", "CheckedValue", default="Y"), "unchecked_value": get(ip, "CheckBoxUncheckedValue", "UncheckedValue", default="N"),
                    "options": options, "initial_value": get(ip, "InitialValue"), "lov": get(ip, "LOVName"), "properties": ip}
            if datatype in {"int", "integer", "rint"}:
                item["precision"], item["scale"] = 38, 0
            if datatype == "long":
                issue("ITEM_SEMANTICS", bn, f"{bn}.{item_name}: DataType=LONG; szövegként kezelve. Oracle LONG oszlopra nem "
                                            "lehet WHERE-feltételt írni, és 32 KB fölött a JDBC-olvasás külön kezelést igényel.")
            if kind == "unsupported" or value_type == "unsupported":
                issue("UNSUPPORTED_ITEM", bn, f"{bn}.{item_name}: ItemType={it}, DataType={datatype}", "all")
            if item["lov"]:
                issue("DYNAMIC_LOV", bn, f"{bn}.{item_name}: LOV={item['lov']}; rekordcsoport/return mapping adapter szükséges.", "write")
            if kind in {"select", "radio"} and not options:
                issue("EMPTY_LIST", bn, f"{bn}.{item_name}: nincs statikus értéklista.", "write")
            if get(ip, "CalculationMode", default="None").lower() not in {"", "none"}:
                issue("CALCULATED_ITEM", bn, f"{bn}.{item_name}: CalculationMode={get(ip, 'CalculationMode')}", "all")
            for key in ("CopyValueFromItem", "LowestAllowedValue", "HighestAllowedValue"):
                if get(ip, key):
                    issue("ITEM_SEMANTICS", bn, f"{bn}.{item_name}: {key}={get(ip, key)}", "write")
            if get(ip, "FormatMask"):
                issue("FORMAT_MASK", bn, f"{bn}.{item_name}: {get(ip, 'FormatMask')}; webes megjelenítéshez ellenőrizendő.")
            if canonical(get(ip, "CaseRestriction", default="Mixed")) not in {"", "mixed"}:
                issue("CASE_RESTRICTION", bn, f"{bn}.{item_name}: CaseRestriction={get(ip, 'CaseRestriction')}; normalizáló adapter szükséges.", "write")
            block["items"].append(item)
            triggers(ie, bn + "." + item_name, bn, item_name)
        field_names = unique_names([i["name"] for i in block["items"]])
        for item in block["items"]:
            item["field"] = field_names[item["name"]]
        if get(bp, "WhereClause", "DefaultWhere"):
            issue("QUERY_FILTER", bn, "A Forms WHERE-feltételhez ellenőrzött repository-adapter kell; a szűrés nem hagyható el.", "all")
        if get(bp, "QueryDataSourceType", default="Table").lower() not in {"table", "none", ""}:
            issue("QUERY_SOURCE", bn, f"QueryDataSourceType={get(bp, 'QueryDataSourceType')}", "all")
        if get(bp, "DMLDataTargetName") and get(bp, "DMLDataTargetName").upper() != table:
            issue("DML_TARGET", bn, "A lekérdezési és a DML-cél eltér.", "all")
        if get(bp, "DMLDataTargetType", default="Table").lower() not in {"table", "none", ""}:
            issue("DML_TYPE", bn, "Nem táblára épülő DML.", "all")
        if get(bp, "OrderByClause"):
            issue("ORDER_BY", bn, "A Forms ORDER BY adaptert igényel; nem helyettesítjük önkényesen.", "all")
        model["blocks"].append(block)
    if not model["blocks"]:
        raise MigrationError("A FormModule nem tartalmaz feldolgozható Block elemet.")
    names = unique_names([b["name"] for b in model["blocks"]])
    classes = unique_names([b["name"] for b in model["blocks"]], "pascal")
    for block in model["blocks"]:
        block["key"] = names[block["name"]]
        block["class"] = classes[block["name"]]
    parents = {id(child): parent for parent in form.iter() for child in parent}
    model['relation_contexts'] = []
    inherited = 0
    for element in form.iter():
        t, p = tag(element), properties(element)
        if t == "programunit":
            model["program_units"].append(p)
            issue("PROGRAM_UNIT", form_owner, f"Program unit megőrizve: {get(p, 'Name')}; csak híváskor blokkol, nincs automatikus fordítás.")
        elif t == "recordgroup":
            model["record_groups"].append(p)
        elif t == "lov":
            model["lovs"].append(p)
        elif t == "relation":
            model["relations"].append(p)
            parent = parents.get(id(element))
            while parent is not None and tag(parent) != 'block': parent = parents.get(id(parent))
            parent_block = get(properties(parent),'Name').upper() if parent is not None else ''
            master = get(p,'MasterDataBlock').upper() or parent_block
            model['relation_contexts'].append({'name':get(p,'Name'), 'master':master,
                'detail':get(p,'DetailDataBlock').upper(), 'join':get(p,'JoinCondition'),
                'parent_block':parent_block, 'master_source':'explicit' if get(p,'MasterDataBlock') else 'parent_block',
                'properties':dict(p)})
            issue("MASTER_DETAIL", form_owner, f"Reláció: {get(p, 'Name')}; koordinált lekérdezés és tranzakció adaptert igényel.", "all")
        elif t == "attachedlibrary":
            model["libraries"].append(p)
            issue("ATTACHED_LIBRARY", form_owner, f"Külső könyvtár megőrizve: {get(p, 'Name')}; nincs PLL/PLD fordítás.")
        if any(get(p, k) for k in ("SubclassObjectGroup", "SubclassModule", "SubclassObjectName", "ParentModule")):
            inherited += 1
        if t in {"visualattribute", "graphics", "canvas", "window", "tabpage", "alert", "editor", "parameter", "menu"}:
            issue("PRESENTATION_OR_CONTEXT", form_owner, f"{t}: {get(p, 'Name')}; az eredeti XML/FIR megőrzi, pixelpontos elrendezés/context nem generálódik.")
    if inherited:
        # One module-level entry instead of one identical entry per inherited object.
        issue("INHERITANCE", form_owner, f"Örökölt Forms objektum ({inherited} db): az öröklési lánc feloldását külön ellenőrizni kell.", "all")
    ids = [t["id"] for t in model["triggers"]]
    if len(ids) != len(set(ids)):
        raise MigrationError("Ismétlődő triggerazonosító az XML-ben.")
    if property_metadata is not None:
        # Downstream Java/Angular generators receive ordinary dictionaries and
        # retain their previous behavior. Only the parser's defaults are audited.
        for record in [model, *model["blocks"], *model["triggers"], *(item for block in model["blocks"] for item in block["items"])]:
            values = record["properties"]
            record["property_provenance"] = {key: values.record["properties"][key] for key in sorted(values.record["properties"])}
            record["properties"] = dict(values)
        for key in ("program_units", "record_groups", "lovs", "relations", "libraries"):
            model[key] = [dict(values) for values in model[key]]
        # Keep the original source inventory comparable with previous releases.
        # Normalizing Property/TriggerText children into attributes and adding
        # inherited children changes the effective tree, not the uploaded XML.
        model["effective_inventory"] = model["inventory"]
        model["inventory"] = dict(sorted(Counter(tag(element) for element in ET.fromstring(raw).iter()).items()))
    return model

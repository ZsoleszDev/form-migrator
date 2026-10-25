"""Explicit host contracts. No knowledge of private Optimus UI import paths."""
from urllib.parse import urlsplit
import re
from .common import MigrationError, RESERVED

COMPANY_DEFAULTS = {
    "AWU_AZON": "",
    "java_cl_http_helpers": {"GET": "exGetEntity", "POST": "exPostEntity", "PUT": "", "DELETE": ""},
    "wbs_base_url": "", "dps_base_url": "",
    "html_selectors": {"form_block": "ank-form-block", "button": "button", "table": "p-table"},
    "form_block_types": {"text": "text", "number": "inputNumber", "datetime": "calendar", "checkbox": "checkBox", "select": "dropdown", "radio": "radioButton", "password": "password", "textarea": "inputTextarea"},
    "form_block_structure_type": "FormBlock.Structure",
    "form_block_columns": "6",
    "form_block_checkbox_boolean": True,
    "calendar_blocks": [], "table_blocks": [],
    "table_bindings": {"rows": "value", "columns": "columns", "field": "field", "header": "header"},
    "endpoint_names": {"list": "getdata", "create": "create", "update": "update", "delete": "delete"},
}


def validate_awu_azon(value):
    """Keep menu identifiers as text, including significant leading zeroes."""
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str):
        raise MigrationError("AWU_AZON: legfeljebb 30 számjegy szükséges.")
    value = value.strip()
    if value and not re.fullmatch(r"[0-9]{1,30}", value):
        raise MigrationError("AWU_AZON: csak a számot add meg, AWU_ előtag nélkül (legfeljebb 30 számjegy).")
    return value


def validate_cl_package(value):
    """The module's CL package; a whole segment may be {module} (the module name, for batch runs)."""
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return value
        if (len(value) <= 200 and re.fullmatch(r"[a-z][a-z0-9_]*(?:\.(?:[a-z][a-z0-9_]*|\{module\}))+", value)
                and not any(part in RESERVED for part in value.split('.'))):
            return value
    raise ValueError("cl_package: Java package szükséges (például hu.company.cl.pages.modules.xymodul; "
                     "tömeges futtatáshoz a {module} a modul nevét jelenti), vagy üres.")


def validate_common_migrate_tools_package(value):
    """Package only, shared by CLI configuration and the web API."""
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return value
        if (len(value) <= 200 and re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", value)
                and not any(part in RESERVED for part in value.split('.'))):
            return value
    raise MigrationError("common_migrate_tools_package: érvényes Java package kell (pl. hu.ceg.common.cl), "
                         "a CommonMigrateTools osztálynév nélkül, vagy üres érték az alapértelmezéshez.")


def validate_base_url(value, label, allow_empty=True):
    if value == "" and allow_empty:
        return value
    if not isinstance(value, str) or len(value) > 1000 or re.search(r"[\s\\{}\x00-\x1f]", value):
        raise MigrationError(label + ": érvényes HTTP(S) alap URL szükséges.")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise MigrationError(label + ": hibás URL/port.") from exc
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise MigrationError(label + ": HTTP(S) alap URL kell, hitelesítési adat, query és fragment nélkül.")
    return value.rstrip("/")


def validate_company_config(config):
    config["AWU_AZON"] = validate_awu_azon(config.get("AWU_AZON", ""))
    helpers = config.get("java_cl_http_helpers", {})
    if not isinstance(helpers, dict) or set(helpers) - set(COMPANY_DEFAULTS["java_cl_http_helpers"]):
        raise MigrationError("java_cl_http_helpers: GET/POST/PUT/DELETE segédmetódusok objektuma szükséges.")
    config["java_cl_http_helpers"] = {**COMPANY_DEFAULTS["java_cl_http_helpers"], **helpers}
    for verb, helper in config["java_cl_http_helpers"].items():
        if not isinstance(helper, str) or helper in RESERVED or not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", helper) and not (verb in {"PUT", "DELETE"} and helper == ""):
            raise MigrationError(f"java_cl_http_helpers.{verb}: Java metódusnév szükséges.")
    for key in ("wbs_base_url", "dps_base_url"):
        config[key] = validate_base_url(config[key], key)
    patterns = {
        "html_selectors": r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*",
        "form_block_types": r"[A-Za-z][A-Za-z0-9_-]*",
        "endpoint_names": r"[A-Za-z][A-Za-z0-9_-]*",
        "table_bindings": r"[A-Za-z][A-Za-z0-9_]*",
    }
    for key, pattern in patterns.items():
        value = config[key]
        if not isinstance(value, dict) or set(value) - set(COMPANY_DEFAULTS[key]):
            raise MigrationError(key + ": hibás kulcsokat tartalmazó objektum.")
        config[key] = {**COMPANY_DEFAULTS[key], **value}
        for name, text in config[key].items():
            if key == "form_block_types" and name == "password" and text == "":
                continue
            if not isinstance(text, str) or not re.fullmatch(pattern, text):
                raise MigrationError(f"{key}.{name}: hibás azonosító.")
            if key == "html_selectors" and text in {"script", "iframe", "object", "embed", "style", "link", "base", "img"}:
                raise MigrationError(f"html_selectors.{name}: nem UI-selector.")
    if not re.fullmatch(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*", str(config["form_block_structure_type"])):
        raise MigrationError("form_block_structure_type: pontozott TypeScript típusnév szükséges.")
    if str(config["form_block_columns"]) not in {str(i) for i in range(1, 25)}:
        raise MigrationError("form_block_columns: 1..24 szükséges.")
    config["form_block_columns"] = str(config["form_block_columns"])
    if type(config["form_block_checkbox_boolean"]) is not bool:
        raise MigrationError("form_block_checkbox_boolean: boolean szükséges.")
    if len(set(config["endpoint_names"].values())) != 4:
        raise MigrationError("endpoint_names: a négy művelet neve legyen különböző.")

    for key in ("calendar_blocks", "table_blocks"):
        if not isinstance(config[key], list) or len(config[key]) > 100 or any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_$#]*", v) for v in config[key]):
            raise MigrationError(key + ": Oracle blokknevek listája szükséges.")
    if config['table_bindings']['field'] == config['table_bindings']['header']:
        raise MigrationError("table_bindings: field és header neve különbözzön.")
    if config['table_bindings']['rows'] == config['table_bindings']['columns']:
        raise MigrationError("table_bindings: rows és columns input különbözzön.")

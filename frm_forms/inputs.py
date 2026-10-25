"""Validate and preserve a Forms2XML input set; inheritance.py resolves it.

Companion identity comes from Oracle's <filename>_olb.xml naming convention,
never from a guessed relationship between ParentModule and ObjectLibrary.Name.
No file named in an XML attribute is opened implicitly.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from .common import MigrationError, write_json
from .xmlmodel import get, props, tag, yes


MAX_XML_BYTES = 32 * 1024 * 1024
MODULE_TAGS = {"formmodule", "objectlibrary", "menumodule"}


class InputIssues(MigrationError):
    """Machine-readable, deterministic diagnostics; a rejection creates no bundle."""

    def __init__(self, issues: list[dict]):
        self.issues = sorted(issues, key=lambda issue: json.dumps(issue, ensure_ascii=False, sort_keys=True))
        super().__init__(json.dumps({"input_issues_version": 1, "status": "rejected", "issues": self.issues},
                                    ensure_ascii=False, indent=2))


def reject(code: str, detail: str, **context) -> None:
    raise InputIssues([{"code": code, "detail": detail, **context}])


def filename(value: str) -> str:
    """Windows and Unix references are both interpreted as names, never as paths."""
    return value.replace("\\", "/").rsplit("/", 1)[-1]


@dataclass
class XmlInput:
    kind: str
    filename: str
    identity: str
    raw: bytes
    root: ET.Element
    module_name: str

    @property
    def archive_path(self) -> str:
        if self.kind == "form":
            return "analysis/source.xml"
        return f"analysis/inputs/{self.kind}/{self.identity[:-4]}_{self.kind}.xml"

    def record(self) -> dict:
        return {"kind": self.kind, "filename": self.filename, "identity": self.identity,
                "module_name": self.module_name, "sha256": hashlib.sha256(self.raw).hexdigest(),
                "bytes": len(self.raw), "archive_path": self.archive_path}


def read_input(path: Path, kind: str) -> XmlInput:
    path = Path(path)
    if not path.is_file() or path.suffix.casefold() != ".xml":
        reject("INVALID_INPUT_FILE", "Létező Forms2XML .xml export szükséges; a --olb és --mmb nem bináris bemenet.",
               filename=path.name, kind=kind)
    identity = path.name
    if kind != "form":
        suffix = f"_{kind}.xml"
        if not path.name.casefold().endswith(suffix) or len(path.name) == len(suffix):
            reject("INVALID_EXPORT_FILENAME", f"Őrizd meg az Oracle export fájlnevét: <eredeti-név>{suffix}.",
                   filename=path.name, kind=kind)
        identity = path.name[:-len(suffix)].casefold() + f".{kind}"
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_XML_BYTES + 1)
    except OSError:
        reject("UNREADABLE_INPUT", "Az XML bemenet nem olvasható.", filename=path.name, kind=kind)
    if len(raw) > MAX_XML_BYTES:
        reject("XML_TOO_LARGE", f"Az XML legfeljebb {MAX_XML_BYTES} bájt lehet.", filename=path.name)
    # Match the form parser's entity policy, including UTF-16 / UTF-32 input.
    if b"<!entity" in raw.lower().replace(b"\x00", b""):
        reject("XML_ENTITY", "Egyedi ENTITY deklaráció nem engedélyezett; exportálj újra ENTITY nélkül.", filename=path.name)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        reject("INVALID_XML", f"Hibás XML: {exc}", filename=path.name)
    expected = {"form": "formmodule", "olb": "objectlibrary", "mmb": "menumodule"}[kind]
    modules = [element for element in root.iter() if tag(element) in MODULE_TAGS]
    if len(modules) != 1 or tag(modules[0]) != expected:
        reject("WRONG_MODULE_TYPE", f"Pontosan egy {expected} modul szükséges ebben a bemenetben.",
               filename=path.name, kind=kind)
    return XmlInput(kind, path.name, identity, raw, root, get(props(modules[0]), "Name"))


def objects(document: XmlInput):
    """Iterative traversal keeps deeply nested input independent of Python recursion."""
    pending = [(document.root, "")]
    while pending:
        element, parent = pending.pop()
        p = props(element)
        object_name = get(p, "Name")
        owner = parent + "/" + tag(element) + (":" + object_name if object_name else "")
        yield element, p, owner
        pending.extend((child, owner) for child in reversed(list(element)))


@dataclass
class InputSet:
    documents: list[XmlInput]
    references: list[dict]
    unresolved: list[dict]
    missing_libraries: list[dict] = None

    def __post_init__(self):
        if self.missing_libraries is None:
            self.missing_libraries = []

    @property
    def form(self) -> XmlInput:
        return self.documents[0]

    def write(self, output: Path) -> None:
        for document in self.documents:
            target = output / document.archive_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(document.raw)
        write_json(output / "analysis" / "inputs.json", {
            "input_set_version": 3,
            "companion_processing": "validated-preserved-and-inheritance-resolved",
            "inheritance_resolution": "analysis/inheritance.json",
            "menu_generation": "not-implemented",
            # A missing companion degrades one object's provenance, not the run.
            # --strict-inheritance turns the same finding back into a rejection.
            "missing_library_policy": "reported-not-fatal-unless-strict",
            "files": [document.record() for document in self.documents],
            "olb_references": self.references,
            "missing_libraries": self.missing_libraries,
        })


def load_inputs(form: Path, olbs: list[Path] | None = None, mmb: Path | None = None,
                strict_inheritance: bool = False) -> InputSet:
    primary = read_input(form, "form")
    libraries: dict[str, XmlInput] = {}
    for path in sorted(olbs or [], key=lambda path: (Path(path).name.casefold(), str(path))):
        document = read_input(path, "olb")
        if document.identity in libraries:
            reject("DUPLICATE_OLB", "Ugyanahhoz az OLB fájlnévhez több exportot adtál meg; nem választható ki önkényesen.",
                   filename=document.identity)
        libraries[document.identity] = document
    documents = [primary] + [libraries[key] for key in sorted(libraries)]
    if mmb is not None:
        documents.append(read_input(mmb, "mmb"))

    references, unresolved, issues = [], [], []
    missing: dict[str, list[dict]] = {}
    for document in documents:
        for element, p, owner in objects(document):
            parent = get(p, "ParentFilename").strip()
            context = {"source": document.filename, "owner": owner, "object_kind": tag(element),
                       "affected_items": sum(tag(child) == "item" for child in element.iter())}
            if parent:
                basename = filename(parent)
                identity = basename.casefold()
                if not identity.endswith(".olb") or identity == ".olb":
                    issues.append({"code": "UNSUPPORTED_PARENT_FILE", "filename": basename, **context,
                                   "detail": "Ebben a bemeneti szerződésben a ParentFilename csak .olb fájlra hivatkozhat."})
                else:
                    reference = {**context, "filename": basename, "parent_filename": parent,
                                 "identity": identity, "provided": identity in libraries}
                    references.append(reference)
                    if identity not in libraries:
                        missing.setdefault(identity, []).append(reference)
            # A reference is not evidence that all properties and trigger bodies
            # have already been expanded. Do not pass it to the legacy generator.
            inherited = parent or any(get(p, key).strip() for key in (
                "ParentModule", "ParentName", "SubclassObjectGroup", "SubclassModule", "SubclassObjectName"))
            subclass_trigger = tag(element) == "trigger" and yes(p, "SubclassSubObject")
            if inherited or subclass_trigger:
                unresolved.append({**context, "parent_filename": parent,
                                   "parent_name": get(p, "ParentName")})
    # Count known form items through supplied parent chains as well as direct links.
    # A missing parent block can contain additional inherited items; that count is
    # explicitly a lower bound, never presented as a guessed total.
    owners = {id(child): parent for document in documents for parent in document.root.iter() for child in parent}
    catalog = {}
    document_of = {}
    for document in documents:
        for element in document.root.iter():
            document_of[id(element)] = document
            object_name = get(props(element), "Name")
            if document.kind == "olb" and object_name:
                catalog.setdefault((document.identity, object_name.casefold()), []).append(element)
    def missing_dependencies(element, active=None):
        active = set() if active is None else active
        if id(element) in active: return set()
        active = active | {id(element)}
        p = props(element); found = set(); parent_file = get(p, "ParentFilename").strip()
        if parent_file:
            identity = filename(parent_file).casefold()
            if identity not in libraries: found.add(identity)
            else:
                for parent in catalog.get((identity, get(p, "ParentName").casefold()), []):
                    found.update(missing_dependencies(parent, active))
        container = owners.get(id(element))
        if container is not None: found.update(missing_dependencies(container, active))
        return found
    impacted = {identity: set() for identity in missing}
    for element, _, owner in objects(primary):
        if tag(element) == "item":
            for identity in missing_dependencies(element):
                if identity in impacted: impacted[identity].add(owner)
    missing_libraries = []
    for identity, uses in sorted(missing.items()):
        export_name = identity[:-4] + "_olb.xml"
        missing_libraries.append({"code": "MISSING_OLB", "filename": identity, "required_export": export_name,
                                  "reference_count": len(uses), "references": uses,
                                  "affected_items": len(impacted[identity]), "affected_item_names": sorted(impacted[identity]),
                                  "count_basis": "known exported form items; missing parent containers may contain more items",
                                  "detail": f"Hiányzó OLB: {identity}. A form DUMP=ALL exportja az effektív property-ket tartalmazza, "
                                            f"de az öröklés forrása és az örökölt triggertörzsek nem igazolhatók. Exportáld "
                                            f"frmf2xml USE_PROPERTY_IDS=NO DUMP=ALL OVERWRITE=YES \"{identity}\" paranccsal, "
                                            f'majd add meg: --olb "{export_name}".'})
    # A missing companion is a provenance gap on named objects, never a reason to
    # discard the whole module. Every other input issue stays fatal.
    if strict_inheritance:
        issues.extend(missing_libraries)
    if issues:
        raise InputIssues(issues)
    return InputSet(documents, references, unresolved, missing_libraries)

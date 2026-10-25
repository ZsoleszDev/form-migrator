"""Deterministic OLB object inheritance; no UI inference or PL/SQL rewriting.

All lookup is by supplied filename and object name. Ambiguous identities,
incomplete links and unresolvable subclass children are errors, never defaults.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
import xml.etree.ElementTree as ET

from .common import write_json
from .inputs import InputSet, XmlInput, filename, reject
from .xmlmodel import canonical, get, package_part


MAX_DEPTH = 128
PROPERTY_ELEMENTS = {"property", "propertyvalue"}
TEXT_PROPERTIES = {"triggertext", "programunittext", "recordgroupquery"}
LEGACY_LINKS = {"subclassobjectgroup", "subclassmodule", "subclassobjectname"}
CONTAINERS = {"module", "formmodule", "objectlibrary", "objectlibrarytab", "menumodule"}
# An object group entry points at another object in the module; it carries no
# properties of its own and is never a merge target. Its identity is therefore
# the pair (name, referenced type), not the name alone: one group may legally
# list a block and a visual attribute that happen to share a name.
REFERENCE_KINDS = {"objectgroupchild"}
REFERENCE_TYPE_KEYS = ("Type", "ObjectType", "ChildObjectType", "ObjectGroupChildType")
REFERENCE_TYPE_PROPERTIES = frozenset(canonical(key) for key in REFERENCE_TYPE_KEYS)


def reference_type(node: Node) -> str:
    """Compare reference identities across export dialects without guessing a target.

    Preserve every original property, but never let the getter's first alias
    silently mask a contradictory type value on the same reference.
    """
    values = {key: node.properties[key] for key in sorted(REFERENCE_TYPE_PROPERTIES & node.properties.keys())}
    types = {canonical(value) for value in values.values()}
    if len(types) > 1:
        reject("CONFLICTING_REFERENCE_TYPE", "Az ObjectGroupChild típust megadó property-jei ellentmondanak egymásnak.",
               source=node.document.filename, owner=node.path, child=node.name, values=values)
    return next(iter(types), "")


def child_key(node: Node) -> tuple:
    if node.kind in REFERENCE_KINDS:
        return (node.kind, node.name.casefold(), reference_type(node))
    if node.kind == "programunit":
        return (node.kind, node.name.casefold(), package_part(node.properties))
    return (node.kind, node.name.casefold())


def boolean(value: str, *, owner: str) -> bool:
    normalized = canonical(value)
    if normalized in {"true", "yes", "1", "propertytrue"}:
        return True
    if normalized in {"false", "no", "0", "propertyfalse"}:
        return False
    reject("INVALID_SUBCLASS_FLAG", "A SubclassSubObject nem értelmezhető logikai érték.", owner=owner, value=value)


@dataclass
class Node:
    document: XmlInput
    xml_tag: str
    path: str
    properties: dict[str, str]
    provenance: dict[str, dict]
    children: list[Node] = field(default_factory=list)
    text: str | None = None
    parents: list[dict] = field(default_factory=list)
    unresolved: dict | None = None

    @property
    def kind(self):
        return canonical(self.xml_tag)

    @property
    def name(self):
        return get(self.properties, "Name", "TriggerName")

    @property
    def location(self):
        return {"file": self.document.filename, "object": self.path}


def validate_children(node: Node) -> None:
    """Validate original and merged siblings without dropping either package part."""
    if node.kind in REFERENCE_KINDS:
        reference_type(node)
    siblings: dict[tuple, list[Node]] = {}
    for child in node.children:
        if not child.name:
            continue
        # Check the whole program-unit name group, including unknown types.
        # Merely adding ProgramUnitType to a key would also admit arbitrary
        # conflicting Procedure/Function/package declarations.
        key = (child.kind, child.name.casefold()) if child.kind == "programunit" else child_key(child)
        previous = siblings.setdefault(key, [])
        for other in previous:
            if child.kind == "programunit" and {package_part(other.properties), package_part(child.properties)} == {"spec", "body"}:
                continue
            differing = sorted(k for k in set(other.properties) | set(child.properties)
                               if other.properties.get(k) != child.properties.get(k))
            # Equivalent type aliases do not make otherwise identical pointers
            # conflict. child_key already checked the normalized reference type.
            if child.kind in REFERENCE_KINDS and not child.children and not other.children:
                if all(key in REFERENCE_TYPE_PROPERTIES for key in differing):
                    continue
            context = {}
            detail = "Azonos típusú és nevű gyerekobjektum ugyanabban a szülőben."
            if child.kind == "programunit":
                detail += " Egy névhez csak egy explicit Package Spec és egy Package Body pár megengedett."
                context["declarations"] = [{"object": member.path,
                                            "program_unit_type": get(member.properties, "ProgramUnitType") or None}
                                           for member in [*previous, child]]
            reject("DUPLICATE_CHILD", detail, source=node.document.filename, owner=node.path,
                   child=child.name, kind=child.kind, differing_properties=differing, **context)
        if child.kind in REFERENCE_KINDS:
            previous.clear()  # Repeated identical pointers need only one comparison.
        previous.append(child)


def parse_node(element: ET.Element, document: XmlInput, path: str, depth: int = 0) -> Node:
    if depth > MAX_DEPTH:
        reject("XML_DEPTH_LIMIT", f"Az XML objektumfája legfeljebb {MAX_DEPTH} szintű lehet.", source=document.filename, owner=path)
    kind = canonical(element.tag)
    values, provenance = {}, {}

    def put(xml_name: str, value: str, representation: str):
        key = canonical(xml_name)
        if kind == "trigger" and key == "text":
            key = "triggertext"
        if key in values:
            reject("DUPLICATE_PROPERTY", "Egy objektum ugyanazt a property-t több helyen deklarálja; nincs önkényes precedencia.",
                   source=document.filename, owner=path, property=key)
        values[key] = value
        provenance[key] = {"value": value, "source": "explicit",
                           "origin": {"file": document.filename, "object": path, "property": xml_name,
                                      "representation": representation}, "via": []}

    for key, value in element.attrib.items():
        put(key, value, "attribute")
    object_children = []
    for child in element:
        child_kind = canonical(child.tag)
        if child_kind in PROPERTY_ELEMENTS:
            attrs = {canonical(k): v for k, v in child.attrib.items()}
            if not attrs.get("name") or len(child):
                reject("INVALID_PROPERTY_ELEMENT", "A Property elemhez név és skalár érték szükséges.", source=document.filename, owner=path)
            put(attrs["name"], attrs.get("value", child.text or ""), "property-element")
        elif child_kind in TEXT_PROPERTIES and not len(child):
            put(child.tag, child.text or "", "text-element")
        else:
            object_children.append(child)
    text = element.text if element.text and element.text.strip() else None
    if kind == "trigger" and text is not None:
        put("TriggerText", text, "element-text")
        text = None
    node = Node(document, element.tag, path, values, provenance, text=text)
    for index, child in enumerate(object_children):
        attrs = {canonical(k): v for k, v in child.attrib.items()}
        label = attrs.get("name", attrs.get("triggername", ""))
        segment = canonical(child.tag) + (":" + label if label else "") + f"[{index}]"
        parsed = parse_node(child, document, path + "/" + segment, depth + 1)
        node.children.append(parsed)
    validate_children(node)
    return node


def walk(node: Node):
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        pending.extend(reversed(current.children))


def inherited_copy(node: Node, destination: Node) -> Node:
    """Preserve ultimate origin; classify copied values relative to their new owner."""
    result = Node(node.document, node.xml_tag, node.path, dict(node.properties), copy.deepcopy(node.provenance),
                  text=node.text, parents=copy.deepcopy(node.parents))
    for record in result.provenance.values():
        if record["source"] != "default":
            record["source"] = "inherited"
        record["via"].append(destination.location)
    result.children = [inherited_copy(child, destination) for child in node.children]
    return result


@dataclass
class Resolution:
    root: ET.Element
    metadata: dict[int, dict]
    documents: list[dict]
    templates: list[dict]
    form_objects: list[dict]
    resolved_links: list[dict]
    unresolved_links: list[dict] = field(default_factory=list)

    @property
    def form_has_inheritance(self) -> bool:
        return any(record["parents"] or any(value["source"] == "inherited" for value in record["properties"].values())
                   for record in self.form_objects)

    @property
    def gaps(self) -> list[dict]:
        """Per-source rollup of what could not be verified, and why.

        Grouped by the named companion where there is one; links that name no
        file at all (a module-qualified or legacy Subclass* reference) are
        collected separately so the export hint is never invented.
        """
        grouped: dict[str, dict] = {}
        for link in self.unresolved_links:
            key = link.get("filename") or ""
            entry = grouped.setdefault(key, {
                "filename": key or None,
                "required_export": (key[:-4] + "_olb.xml") if key.endswith(".olb") else None,
                "reasons": [], "reference_count": 0, "affected_items": 0, "affected_triggers": 0, "objects": []})
            entry["reference_count"] += 1
            entry["affected_items"] += link["kind"] == "item"
            entry["affected_triggers"] += link["kind"] == "trigger"
            if link["reason"] not in entry["reasons"]:
                entry["reasons"].append(link["reason"])
            entry["objects"].append(link)
        for entry in grouped.values():
            entry["reasons"].sort()
        return [grouped[key] for key in sorted(grouped)]

    def write(self, output: Path):
        # Defaults recorded by the legacy parser are references in form_objects;
        # serialization happens after parsing, so all actually used fallbacks show.
        write_json(output / "analysis" / "inheritance.json", {
            "inheritance_version": 1,
            "precedence": "explicit-over-inherited",
            "default_basis": "existing-generator-fallbacks-not-Oracle-default-catalog",
            "unresolved_policy": "a missing companion marks the named object unverified; it never invents a value or a trigger body",
            "resolved_links": self.resolved_links,
            "unresolved_links": self.unresolved_links,
            "gaps": self.gaps,
            "form_objects": self.form_objects,
            "companion_documents": self.documents,
        })
        write_json(output / "analysis" / "olb-catalog.json", {
            "catalog_version": 1, "key": ["file", "object_name"],
            "program_unit_discriminator": "explicit ProgramUnitType selects Package Spec or Package Body",
            "ambiguity_policy": "duplicate names are listed; a lookup must have exactly one candidate after the explicit package-part discriminator, when present",
            "templates": self.templates,
        })
        target = output / "analysis" / "effective-source.xml"
        target.write_bytes(ET.tostring(self.root, encoding="utf-8", xml_declaration=True) + b"\n")


class Resolver:
    def __init__(self, inputs: InputSet, strict: bool = False):
        self.inputs = inputs
        self.strict = strict
        self.unresolved_nodes: dict[int, dict] = {}
        self.unresolved: list[dict] = []
        self.roots = {document.identity: parse_node(document.root, document, "/" + canonical(document.root.tag))
                      for document in inputs.documents}
        self.libraries = {document.identity: document for document in inputs.documents if document.kind == "olb"}
        self.by_identity = {document.identity: document for document in inputs.documents}
        self.catalog: dict[tuple[str, str], list[Node]] = {}
        # Templates come from libraries; lookups may also target the module's
        # own objects, so the resolvable index is the wider of the two.
        self.index: dict[tuple[str, str], list[Node]] = {}
        self.owners = {id(child): node for root in self.roots.values() for node in walk(root) for child in node.children}
        for identity in sorted(self.roots):
            for node in walk(self.roots[identity]):
                if node.name and node.kind not in CONTAINERS:
                    self.index.setdefault((identity, node.name.casefold()), []).append(node)
                    if identity in self.libraries:
                        self.catalog.setdefault((identity, node.name.casefold()), []).append(node)
        self.cache: dict[int, Node] = {}
        self.active: list[Node] = []
        self.links: list[dict] = []

    def gap(self, node: Node, code: str, detail: str, filename: str = "", parent_name: str = "", **context) -> None:
        """Strict mode rejects; otherwise the object keeps its own values, marked."""
        if self.strict:
            reject(code, detail, filename=filename, parent_name=parent_name, **node.location, **context)
        return self.unverified(node, filename, parent_name, code)

    def unverified(self, node: Node, identity: str, parent_name: str, reason: str) -> None:
        """Record a provenance gap and continue with the object's own properties.

        The DUMP=ALL form already carries effective values, so the object stays
        usable; what is lost is the proof of where they came from and any body
        that lives only in the companion. Nothing here is filled in by guess.
        """
        record = self.unresolved_nodes.get(id(node))
        if record is None:
            record = {"object": node.location, "kind": node.kind, "name": node.name,
                      "filename": identity, "parent_name": parent_name, "reason": reason}
            self.unresolved_nodes[id(node)] = record
            self.unresolved.append(record)
        node.unresolved = record
        return None

    def unresolved_ancestor(self, node: Node) -> dict | None:
        """A container's missing parent also leaves its subobjects unverifiable."""
        current, seen = node, set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if id(current) in self.unresolved_nodes:
                return self.unresolved_nodes[id(current)]
            current = self.owners.get(id(current))
        return None

    def parent(self, node: Node) -> Node | None:
        """Locate the object this one inherits from, or record why we cannot.

        Every failure here has the same shape: the object's own values stay
        usable (a DUMP=ALL export already carries them), but their origin
        cannot be proven. That is a provenance gap, not a corrupt module, so
        outside --strict-inheritance it is reported instead of fatal.
        """
        p = node.properties
        legacy = [key for key in LEGACY_LINKS if get(p, key).strip()]
        if legacy:
            return self.gap(node, "UNSUPPORTED_INHERITANCE_LINK",
                            "A feloldó ParentFilename/ParentName kapcsolatot vár; a régi Subclass* kapcsolat nem találgatható.",
                            parent_name=get(p, "SubclassObjectName"), links=sorted(legacy))
        parent_file = get(p, "ParentFilename").strip()
        parent_name = get(p, "ParentName").strip()
        parent_module = get(p, "ParentModule").strip()
        if not (parent_file or parent_name or parent_module):
            return None
        # A module-qualified reference to the module's own name is local: the
        # template was copied in, so the link points inside this same export.
        # A foreign module name still never selects a file by guess.
        if not parent_file and parent_module and parent_module.casefold() == node.document.module_name.casefold():
            identity = node.document.identity
        elif parent_file:
            identity = filename(parent_file).casefold()
        else:
            return self.gap(node, "INCOMPLETE_PARENT_REFERENCE",
                            "Hiányzó ParentFilename; külső fájlt nem keresünk ParentModule alapján.",
                            parent_module=parent_module, parent_name=parent_name)
        if not parent_name:
            return self.gap(node, "INCOMPLETE_PARENT_REFERENCE", "A ParentFilename mellől hiányzik a ParentName.",
                            filename=identity, parent_filename=parent_file)
        document = self.by_identity.get(identity)
        if document is None:
            return self.gap(node, "MISSING_OLB", "A szülő OLB XML-exportját add meg --olb kapcsolóval.",
                            filename=identity, parent_name=parent_name)
        if parent_module and parent_module.casefold() != document.module_name.casefold():
            return self.gap(node, "PARENT_MODULE_MISMATCH", "A ParentModule nem egyezik a megadott modul nevével.",
                            filename=identity, parent_name=parent_name,
                            parent_module=parent_module, library_module=document.module_name)
        candidates = self.index.get((identity, parent_name.casefold()), [])
        if not candidates:
            return self.gap(node, "MISSING_PARENT_OBJECT", "A megadott export nem tartalmaz ilyen nevű sablonobjektumot.",
                            filename=identity, parent_name=parent_name)
        if node.kind == "programunit" and package_part(p) and all(c.kind == "programunit" for c in candidates):
            candidates = [c for c in candidates if package_part(c.properties) == package_part(p)]
            if not candidates:
                return self.gap(node, "PROGRAM_UNIT_TYPE_MISMATCH",
                                "A szülő neve létezik, de nincs a megadott ProgramUnitType szerinti csomagrész.",
                                filename=identity, parent_name=parent_name,
                                expected=get(p, "ProgramUnitType"))
        if len(candidates) != 1:
            return self.gap(node, "AMBIGUOUS_PARENT_OBJECT",
                            "A (fájl, objektumnév) nem egyértelmű; nem választunk az azonos nevű objektumok közül.",
                            filename=identity, parent_name=parent_name,
                            candidates=[candidate.location for candidate in candidates])
        parent = candidates[0]
        if parent.kind != node.kind:
            return self.gap(node, "PARENT_TYPE_MISMATCH", "A szülő és a leszármazott XML objektumtípusa eltér.",
                            filename=identity, parent_name=parent_name,
                            parent=parent.location, expected=node.kind, actual=parent.kind)
        if node.kind == "programunit":
            own_type = canonical(get(p, "ProgramUnitType"))
            parent_type = canonical(get(parent.properties, "ProgramUnitType"))
            if own_type and parent_type and own_type != parent_type:
                return self.gap(node, "PROGRAM_UNIT_TYPE_MISMATCH", "A szülő és a leszármazott ProgramUnitType értéke eltér.",
                                filename=identity, parent_name=parent_name, parent=parent.location,
                                expected=get(p, "ProgramUnitType"), actual=get(parent.properties, "ProgramUnitType"))
        return parent

    def resolve(self, node: Node, implicit: Node | None = None) -> Node:
        if implicit is None and id(node) in self.cache:
            return self.cache[id(node)]
        # A named subobject may itself be referenced from another OLB. Establish
        # its containing object's inheritance first instead of treating it as an
        # unrelated top-level template.
        if implicit is None and "subclasssubobject" in node.properties and boolean(node.properties["subclasssubobject"], owner=node.path):
            owner = self.owners.get(id(node))
            if owner is not None and all(owner is not active for active in self.active) and not any(get(node.properties, key) for key in ("ParentFilename", "ParentModule", "ParentName")):
                self.resolve(owner)
                if id(node) in self.cache:
                    return self.cache[id(node)]
        if any(node is active for active in self.active):
            first = next(i for i, active in enumerate(self.active) if active is node)
            reject("INHERITANCE_CYCLE", "Ciklikus OLB-öröklés; a teljes hivatkozási kör szerepel a chain mezőben.",
                   chain=[active.location for active in self.active[first:]] + [node.location])
        if len(self.active) >= MAX_DEPTH:
            reject("INHERITANCE_DEPTH_LIMIT", f"Az öröklési/objektum-feloldási lánc legfeljebb {MAX_DEPTH} szintű lehet.",
                   chain=[active.location for active in self.active] + [node.location])
        self.active.append(node)
        try:
            explicit_parent = self.parent(node)
            if explicit_parent is not None and implicit is not None and explicit_parent.location != implicit.location:
                reject("AMBIGUOUS_PARENT", "Az objektum explicit szülője eltér a tartalmazó sablonból kapott szülőtől.",
                       **node.location, explicit_parent=explicit_parent.location, implicit_parent=implicit.location)
            base = self.resolve(explicit_parent) if explicit_parent is not None else implicit
            subclass = boolean(node.properties["subclasssubobject"], owner=node.path) if "subclasssubobject" in node.properties else False
            if subclass and base is None:
                # Distinguish "the companion was not supplied" from "this export
                # claims an inheritance that does not exist anywhere". Only the
                # first is survivable, and only outside strict mode.
                ancestor = None if self.strict else self.unresolved_ancestor(node)
                if ancestor is None:
                    reject("ORPHAN_SUBCLASS", "SubclassSubObject=true, de nincs azonosítható örökölt szülőobjektum.", **node.location)
                self.unverified(node, ancestor["filename"], ancestor.get("parent_name", ""), "missing-olb-subobject")
            if base is not None and not node.name:
                reject("UNNAMED_SUBCLASS", "Az örökölt objektumhoz explicit Name szükséges.", **node.location)
            result = inherited_copy(base, node) if base is not None else Node(node.document, node.xml_tag, node.path, {}, {})
            result.document, result.xml_tag, result.path = node.document, node.xml_tag, node.path
            result.unresolved = node.unresolved
            result.properties.update(node.properties)
            result.provenance.update(copy.deepcopy(node.provenance))
            result.text = node.text if node.text is not None else result.text
            if base is not None:
                link = {"child": node.location, "parent": base.location, "mode": "explicit" if explicit_parent else "subobject"}
                self.links.append(link)
                result.parents.append(link)
            if node.kind == "trigger" and base is not None:
                own_body = node.properties.get("triggertext", "")
                if not own_body.strip():
                    if not subclass:
                        reject("EMPTY_TRIGGER_OVERRIDE", "Az üres helyi trigger csak SubclassSubObject=true esetén örökölhet törzset.", **node.location)
                    body = base.properties.get("triggertext", "")
                    if not body.strip():
                        self.gap(node, "MISSING_INHERITED_TRIGGER_BODY", "Az örökölt trigger törzse a lánc végén is üres.",
                                 parent_name=base.name or "", parent=base.location)
                    else:
                        inherited = inherited_copy(base, node)
                        result.properties["triggertext"] = body
                        result.provenance["triggertext"] = inherited.provenance["triggertext"]
            result.children = self.children(node, base)
            validate_children(result)
            self.cache[id(node)] = result
            return result
        finally:
            self.active.pop()

    def children(self, node: Node, base: Node | None) -> list[Node]:
        if base is None:
            return [self.resolve(child) for child in node.children]
        # Child identity includes a package's spec/body part. Anonymous collections may
        # be inherited whole, but cannot be merged with local members by position.
        for kind in {child.kind for child in base.children} & {child.kind for child in node.children}:
            members = [child for child in base.children + node.children if child.kind == kind]
            if any(not child.name for child in members):
                reject("AMBIGUOUS_CHILD_COLLECTION", "Névtelen gyerekelemeket nem lehet pozícióból összepárosítani.", **node.location, kind=kind)
        local = {child_key(child): child for child in node.children if child.name}
        result, matched = [], set()
        # Retain parent's ordering; explicit replacements keep that position.
        for parent_child in base.children:
            key = child_key(parent_child)
            child = local.get(key) if parent_child.name else None
            if child is None:
                inherited = inherited_copy(parent_child, node)
                if inherited.kind == "trigger" and not inherited.properties.get("triggertext", "").strip():
                    self.gap(inherited, "MISSING_INHERITED_TRIGGER_BODY", "Az örökölt triggerhez nincs törzs.",
                             parent_name=parent_child.name or "", parent=parent_child.location)
                result.append(inherited)
            else:
                matched.add(key)
                result.append(self.resolve(child, parent_child))
        for child in node.children:
            if child_key(child) in matched:
                continue
            # The container's parent did resolve, but it has no such member. The
            # claim is contradicted by evidence we actually hold, so it is named
            # precisely rather than reported as a parentless orphan.
            if "subclasssubobject" in child.properties and boolean(child.properties["subclasssubobject"], owner=child.path):
                self.gap(child, "MISSING_PARENT_SUBOBJECT", "A feloldott szülő nem tartalmaz ilyen nevű alobjektumot.",
                         parent_name=child.name or "", parent=base.location)
            result.append(self.resolve(child))
        return result

    def run(self) -> Resolution:
        # Resolve and validate all supplied OLBs, not just a convenient first match.
        resolved = {key: self.resolve(self.roots[key]) for key in sorted(self.roots) if key != self.inputs.form.identity}
        form = self.resolve(self.roots[self.inputs.form.identity])
        for document in [form, *resolved.values()]:
            for node in walk(document):
                copied = any(record["source"] == "inherited" for record in node.provenance.values())
                if copied and node.kind == "trigger" and not node.properties.get("triggertext", "").strip():
                    self.gap(node, "MISSING_INHERITED_TRIGGER_BODY", "Az örökölt triggerhez a feloldott objektumfában sincs törzs.",
                             parent_name=node.name or "")
                if copied and node.kind == "item":
                    # These pairs are already accepted by xmlmodel.py. An OLB
                    # from one export dialect and a form from another must not
                    # make the legacy first-match getter shadow a local value.
                    for aliases in [("maximumlength", "maxlength"), ("checkboxcheckedvalue", "checkedvalue"),
                                    ("checkboxuncheckedvalue", "uncheckedvalue")]:
                        values = {key: node.properties[key] for key in aliases if key in node.properties}
                        if len(set(values.values())) > 1:
                            reject("CONFLICTING_PROPERTY_ALIASES", "Az örökölt objektumban a parser által azonosként kezelt property-nevek értéke eltér; nem választunk önkényesen XML-dialektust.",
                                   **node.location, values=values)
        metadata, form_objects = {}, []

        def serialize(node: Node, path: str, records: list[dict], metadata_map: dict | None = None) -> ET.Element:
            element = ET.Element(node.xml_tag, {key: node.properties[key] for key in sorted(node.properties)})
            element.text = node.text
            record = {"path": path, "kind": node.kind, "name": node.name,
                      "properties": copy.deepcopy(node.provenance), "parents": node.parents,
                      "unresolved": copy.deepcopy(node.unresolved)}
            records.append(record)
            if metadata_map is not None:
                metadata_map[id(element)] = record
            for index, child in enumerate(node.children):
                segment = child.kind + (":" + child.name if child.name else "") + f"[{index}]"
                element.append(serialize(child, path + "/" + segment, records, metadata_map))
            return element

        root = serialize(form, "/" + form.kind, form_objects, metadata)
        companions = []
        for key in sorted(resolved):
            records = []
            serialize(resolved[key], "/" + resolved[key].kind, records)
            companions.append({"file": resolved[key].document.filename, "objects": records})
        templates = []
        for (file, object_name), candidates in sorted(self.catalog.items()):
            for candidate in candidates:
                value = self.resolve(candidate)
                records = []
                serialize(value, candidate.path, records)
                templates.append({"file": file, "object_name": candidate.name, "lookup_name": object_name,
                                  "ambiguous": len(candidates) != 1, "kind": candidate.kind,
                                  "location": candidate.location, "objects": records})
                if candidate.kind == "programunit":
                    templates[-1]["program_unit_type"] = get(value.properties, "ProgramUnitType") or None
                    if package_part(candidate.properties) and all(c.kind == "programunit" for c in candidates):
                        templates[-1]["ambiguous"] = sum(package_part(c.properties) == package_part(candidate.properties)
                                                         for c in candidates) != 1
        # The same subobject can be expanded along more than one route. Keep the
        # audit ordered and unique without depending on traversal/cache order.
        import json
        links = {json.dumps(link, ensure_ascii=False, sort_keys=True): link for link in self.links}
        gaps = {json.dumps(gap, ensure_ascii=False, sort_keys=True): gap for gap in self.unresolved}
        return Resolution(root, metadata, companions, templates, form_objects,
                          [links[key] for key in sorted(links)], [gaps[key] for key in sorted(gaps)])


def resolve_inputs(inputs: InputSet, strict: bool = False) -> Resolution:
    return Resolver(inputs, strict).run()

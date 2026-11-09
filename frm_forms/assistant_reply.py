"""The local model's answer -> the migrator's normalized Java proposal.

The prompt asks for Markdown with ```java blocks: `// SERVICE` (the rewritten method and
its private helpers) and `// REPOSITORY <Name>` (new repository methods), then optional
`MISSING:` / `NOTES:` lines. Small local models do not always follow that literally, so
complete classes, a bare method body, headings instead of comment labels, an unterminated
last block and the legacy JSON answer are accepted too.

Every decision is recorded (blocks, notes, warnings, problems), so the UI can show what was
taken from the answer and why. A problem makes the attempt unusable; its English text goes
back to the model in the correction request. Nothing here uses the network or the disk.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re

from .java_scan import (balanced, dedent, java_mask, matching_brace, methods_by_name, replace_identifier,
                        scan_members, strip_annotations, without_sequence_queries)

FQN = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_$][\w$]*)+")
IMPORT_LINE = re.compile(r"^[ \t]*import[ \t]+(static[ \t]+)?([\w.$]+?)(\.\*)?[ \t]*;[ \t]*$", re.M)
PACKAGE_LINE = re.compile(r"^[ \t]*package[ \t]+[\w.]+[ \t]*;[ \t]*$", re.M)
FENCE_OPEN = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})[ \t]*(?P<lang>[^\s`]*)[^\n]*$", re.M)
THINK = re.compile(r"<think>([\s\S]*?)(?:</think>|$)", re.I)
SECTION = re.compile(r"^[ \t>*_#-]*(?P<key>MISSING|HI[ÁA]NYZ[ÓO](?:[ \t]+ADATOK?)?|HI[ÁA]NYZIK|NOTES?|MEGJEGYZ[ÉE]S(?:EK)?|"
                     r"FELT[ÉE]TELEZ[ÉE]S(?:EK)?|ASSUMPTIONS?)[ \t*_]*(?::|$)[ \t*_]*(?P<rest>.*)$", re.I | re.M)
REGION = re.compile(r"^[ \t]*//[ \t]*region\b[^\n]*\n(?:[^\n]*\n)*?[ \t]*//[ \t]*endregion\b[^\n]*(?:\n|$)", re.M)
GATE = re.compile(r"^[ \t]*if\s*\(\s*!\s*MODULE_REVIEWED\s*\)\s*(?:\{[^{}]*\}|throw\b[^;]*;)[ \t]*\n?", re.M)
JAVA_LANGS = {"", "java"}
SKIPPED_LANGS = {"sql", "plsql", "pls", "text", "txt", "markdown", "md", "xml", "bash", "sh", "shell", "yaml", "yml",
                 "properties", "diff", "patch", "html", "ts", "typescript", "js", "javascript", "json"}
SYSTEM_API = re.compile(r"\b(Runtime|ProcessBuilder|ScriptEngineManager|ClassLoader|DriverManager)\b|"
                        r"\bSystem\s*\.\s*(?:exit|setProperty)\b|\bjava\s*\.\s*(?:net|io|nio|lang\s*\.\s*reflect)\b")
NATIVE_DB = re.compile(r"\b(?:createNativeQuery|createStoredProcedureQuery|prepareStatement|prepareCall|getConnection|"
                       r"JdbcTemplate|NamedParameterJdbcTemplate|DbCalls)\b")
DTO_TYPES = {"String": "String", "Boolean": "Boolean", "boolean": "Boolean", "Integer": "Integer", "int": "Integer",
             "Long": "Long", "long": "Long", "Double": "Double", "double": "Double", "Float": "Float", "float": "Float",
             "BigDecimal": "java.math.BigDecimal", "LocalDateTime": "java.time.LocalDateTime",
             "LocalDate": "java.time.LocalDate", "OffsetDateTime": "java.time.OffsetDateTime", "byte[]": "byte[]"}
DTO_TYPES.update({value: value for value in list(DTO_TYPES.values())})
NOTHING_HEAD = {"none", "nothing", "nincs", "semmi", "n/a", "na", "nil", "-", "—"}
NOTHING_REST = {"is", "are", "missing", "identified", "needed", "required", "provided", "all", "hiányzó", "hiányzik",
                "hianyzo", "hianyzik", "adat", "adatok", "forrás", "forras", "elem", "ilyen", "nem", "context", "information",
                "dependencies", "dependency", "data", "source", "sources", "kontextus", "információ", "szükséges"}


@dataclass(frozen=True)
class Problem:
    hu: str   # shown to the developer
    en: str   # sent back to the model in the correction request


@dataclass
class Repository:
    name: str                 # LookupAppAccountRepository
    variable: str             # lookupAppAccountRepository (field in the service)
    entity: str               # LookupAppAccountEntity
    existing: str = ""        # the repository class as it is now
    id_type: str = ""         # BigDecimal, or <Entity>Id for a composite key (reply_repair's findById ...)


@dataclass
class Context:
    method: str                                   # the selected service method
    service: str = ""                             # the DPS ServiceImpl as it is now
    repositories: list = field(default_factory=list)
    module_class: str = ""                        # PascalCase module name (supplemental DTO prefix)
    namespace: str = "javax"                      # javax/jakarta persistence
    mode: str = "jpa"                             # jpa | inline | jdbc
    allowed_imports: frozenset = frozenset()      # FQNs the developer already uses
    routines: frozenset = frozenset()             # the generated database routine methods (db_routines)
    generated: dict = field(default_factory=dict)  # repository -> the migrator's methods of the task's SQL (prompt lines)
    row_fields: frozenset = frozenset()           # a hook: the public fields of its record (row.field)


@dataclass
class Block:
    code: str
    lang: str = ""
    heading: str = ""
    closed: bool = True


@dataclass
class Reply:
    response: dict | None = None
    format: str = "markdown"
    thinking: str = ""
    truncated: bool = False
    blocks: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)

    def problem(self, hu: str, en: str) -> None:
        if all(p.hu != hu for p in self.problems):
            self.problems.append(Problem(hu, en))

    def warn(self, text: str) -> None:
        if text not in self.warnings: self.warnings.append(text)

    def note(self, text: str) -> None:
        if text not in self.notes: self.notes.append(text)

    def report(self) -> dict:
        status = "rejected" if self.problems else (self.response or {}).get("status", "rejected")
        return {"format": self.format, "status": status, "truncated": self.truncated, "blocks": self.blocks,
                "notes": self.notes, "warnings": self.warnings, "problems": [p.hu for p in self.problems],
                "missing": self.missing}


# --- reading the answer -------------------------------------------------------------------------------------------

def split_thinking(text: str) -> tuple[str, str]:
    """The answer without <think> sections, and the thinking text itself."""
    parts = []
    def keep(match):
        parts.append(match.group(1).strip()); return ""
    content = THINK.sub(keep, text)
    closing = content.lower().rfind("</think>")
    if closing >= 0:   # the opening tag came from the chat template
        parts.insert(0, content[:closing].strip()); content = content[closing + len("</think>"):]
    return content.strip(), "\n\n".join(p for p in parts if p)


def fenced_blocks(text: str) -> tuple[list[Block], str]:
    """Markdown code blocks and the prose around them (the last block may be unterminated)."""
    blocks, prose, pos = [], [], 0
    while True:
        match = FENCE_OPEN.search(text, pos)
        if not match:
            prose.append(text[pos:]); break
        before = text[pos:match.start()]
        prose.append(before)
        fence = match["fence"]
        closing = re.compile(r"^[ \t]{0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*$", re.M)
        start = match.end() + 1 if text[match.end():match.end() + 1] == "\n" else match.end()
        end = closing.search(text, start)
        code = text[start:end.start()] if end else text[start:]
        blocks.append(Block(code, match["lang"].lower().strip("{}."), heading(before), bool(end)))
        pos = end.end() if end else len(text)
    return blocks, "\n".join(prose)


def heading(prose: str) -> str:
    lines = [line.strip() for line in prose.strip().split("\n")[-3:] if line.strip()]
    if not lines: return ""
    text = re.sub(r"[#*_`>]+", " ", lines[-1]).strip().rstrip(":").strip()
    return text if len(text) <= 120 else ""


def is_nothing(item: str) -> bool:
    words = re.findall(r"[\wáéíóöőúüű/—-]+", item.lower())
    if not words or item.strip() in {"[]", "-", "—"}: return True
    head, rest = words[0], set(words[1:])
    if head in NOTHING_HEAD and rest <= NOTHING_REST: return True
    return head == "no" and len(words) > 1 and words[1] == "missing" and rest <= NOTHING_REST


def sections(prose: str) -> tuple[list[str], list[str]]:
    """MISSING: and NOTES: items written outside the code blocks."""
    found = {"missing": [], "notes": []}
    current = None
    for line in prose.replace("\r\n", "\n").split("\n"):
        match = SECTION.match(line)
        if match:
            current = "missing" if match["key"].upper().startswith(("MISSING", "HIÁNY", "HIANY")) else "notes"
            rest = match["rest"].strip()
            if rest: found[current].append(rest)
            continue
        if current is None: continue
        stripped = line.strip()
        if not stripped:
            if found[current]: current = None
            continue
        bullet = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)$", line)
        if bullet:
            found[current].append(bullet.group(1).strip())
        elif found[current] and line[:1] in " \t":
            found[current][-1] += " " + stripped
        else:
            current = None
    def clean(items):
        result = []
        for item in items:
            item = re.sub(r"^(?:context|information|adat|kontextus)\s*:\s*", "", item.strip(" *_`"), flags=re.I).strip()
            if item and not is_nothing(item) and item not in result:
                result.append(item[:3000])
        return result[:30]
    return clean(found["missing"]), clean(found["notes"])


def parse_json_answer(text: str):
    """The legacy JSON proposal (whole answer or a ```json block), else None."""
    candidates = [text.strip()] + [m.group(1).strip() for m in re.finditer(r"```json[ \t]*\n([\s\S]*?)```", text)]
    for candidate in candidates:
        if not candidate.startswith("{"): continue
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict) and {"body", "status"} & set(value):
            return value
    return None


def looks_like_java(text: str) -> bool:
    mask = java_mask(text)
    return (";" in mask and "{" in mask) or bool(re.search(r"\breturn\b[^;]*;", mask))


def take_imports(code: str) -> tuple[list[tuple[str, bool, bool]], str]:
    """Import entries (fqn, static, wildcard) and the code with import/package lines blanked."""
    mask = java_mask(code)
    imports = [(m.group(2), bool(m.group(1)), bool(m.group(3))) for m in IMPORT_LINE.finditer(mask)]
    cleaned = list(code)
    for pattern in (IMPORT_LINE, PACKAGE_LINE):
        for m in pattern.finditer(mask):
            cleaned[m.start():m.end()] = " " * (m.end() - m.start())
    return imports, "".join(cleaned)


def first_comment_label(code: str) -> str:
    for line in code.split("\n")[:4]:
        stripped = line.strip()
        if not stripped: continue
        match = re.match(r"^(?://+|/\*+)\s*(.*?)\s*(?:\*+/)?$", stripped)
        return match.group(1).strip() if match else ""
    return ""


# --- classifying blocks -------------------------------------------------------------------------------------------

def by_entity(code: str, ctx: Context, candidates):
    """The repository whose entity the code uses most (or the only candidate)."""
    mask = java_mask(code)
    counts = sorted(((len(re.findall(r"(?<![\w$])" + re.escape(r.entity) + r"(?![\w$])", mask)), r) for r in candidates),
                    key=lambda item: -item[0])
    counts = [item for item in counts if item[0]]
    if counts and (len(counts) == 1 or counts[0][0] > counts[1][0]):
        return counts[0][1]
    return candidates[0] if len(candidates) == 1 else None


def resolve_repository(name: str, ctx: Context, code: str = ""):
    exact = next((r for r in ctx.repositories if name and (r.name == name or r.variable == name)), None)
    if exact: return exact
    lowered = name.lower().removesuffix(".java")
    stem = lowered.removesuffix("repository")
    fuzzy = [r for r in ctx.repositories if lowered and (r.name.lower().endswith(lowered) or lowered.endswith(r.name.lower())
             or (stem and r.entity.lower().removesuffix("entity").endswith(stem)))]
    if len(fuzzy) == 1: return fuzzy[0]
    return by_entity(code, ctx, fuzzy or ctx.repositories)


def classify_block(block: Block, ctx: Context) -> tuple[str, object, str]:
    """(kind, target, label): kind is service | repository | dto | entity | unknown."""
    label = first_comment_label(block.code) or block.heading
    upper = label.upper()
    words = set(re.findall(r"[A-Za-z_$][\w$]*", label))
    if re.match(r"^\W*(?:REPOSITORY|REPO)\b", upper):
        named = re.findall(r"\b(\w+Repository)\b", label)
        return "repository", resolve_repository(named[0] if named else "", ctx, block.code), label
    if re.match(r"^\W*(?:SERVICE|SERVICEIMPL)\b", upper):
        return "service", None, label
    named = next((r for r in ctx.repositories if r.name in words or r.variable in words), None)
    if named: return "repository", named, label
    if ctx.method in words or re.search(r"ServiceImpl\b", label):
        return "service", None, label
    dto = re.search(r"\b(\w+Dto)\b", label)
    if dto and (re.match(r"^\W*DTO\b", upper) or re.fullmatch(r"\W*\w+Dto(?:\.java)?\W*", label)):
        return "dto", dto.group(1), label
    mask = java_mask(block.code)
    types = re.findall(r"\b(class|interface|record)\s+([A-Za-z_$][\w$]*)", mask)
    names = {name for _, name in types}
    if any(name.endswith("ServiceImpl") for name in names) or re.search(
            r"\b" + re.escape(ctx.method) + r"\s*\([^;{}]*\)\s*(?:throws[^{;]*)?\{", mask):
        return "service", None, label
    for repo in ctx.repositories:
        if repo.name in names: return "repository", repo, label
    if any(repo.entity in names for repo in ctx.repositories) or re.search(r"@\s*Entity\b", mask):
        return "entity", None, label
    if any(name.endswith("Dto") for name in names) and not re.search(r"\bentityManager\b", mask):
        return "dto", next(name for name in names if name.endswith("Dto")), label
    interface = next((name for kind, name in types if kind == "interface" and name.endswith("Repository")), None)
    if interface:
        return "repository", resolve_repository(interface, ctx, block.code), label
    if re.search(r"\b(?:entityManager|EntityManager|CriteriaBuilder|CriteriaQuery)\b", mask) or "REPOSITOR" in upper:
        return "repository", by_entity(block.code, ctx, ctx.repositories) if ctx.repositories else None, label
    return "unknown", None, label


# --- members ------------------------------------------------------------------------------------------------------

def member_source(code: str, member) -> str:
    """A member with the comment lines directly above it, dedented."""
    line_start = max(code.rfind("\n", 0, member.start) + 1, member.lead)
    lead = code[member.lead:line_start].split("\n")
    if member.lead and code[member.lead - 1:member.lead] != "\n" and lead:
        lead = lead[1:]   # the rest of the previous member's line (a trailing comment)
    comments = [line for line in lead if line.strip().startswith(("//", "/*", "*"))]
    return dedent("\n".join(comments + [code[line_start:member.end]]))


def with_access(text: str, access: str) -> str:
    """A method's source with @Override removed and the given access modifier."""
    member = next((m for m in scan_members(text, java_mask(text)) if m.kind == "method"), None)
    if member is None: return text
    open_brace = member.body[0] if member.body else member.end
    header = re.sub(r"@Override\b[ \t]*\n?[ \t]*", "", text[member.start:open_brace])
    if access == "private":
        header = re.sub(r"(?<![\w$])(?:public|protected)\s+", "", header)
    if not re.search(r"(?<![\w$])(?:public|private|protected)\s", java_mask(header)):
        annotations = re.match(r"^(\s*(?:@[\w.$]+(?:\s*\((?:[^()]|\([^()]*\))*\))?\s*)*)", header)
        at = annotations.end() if annotations else 0
        header = header[:at] + access + " " + header[at:]
    return text[:member.start] + header + text[open_brace:]


def lambda_body(code: str, mask: str, start: int, end: int):
    """The block body of the log1x(..., () -> { ... }) lambda between start and end, else None."""
    found = re.compile(r"\blog1x\s*\(").search(mask, start, end)
    if not found: return None
    arrow = re.compile(r"->\s*\{").search(mask, found.end(), end)
    if not arrow: return None
    opening = arrow.end() - 1
    return code[opening + 1:matching_brace(mask, opening)]


def clean_body(body: str, reply: Reply) -> str:
    text = dedent(body)
    text, regions = REGION.subn("", text)
    if regions:
        reply.note("Az AI által visszaküldött eredeti Forms-kommentet elhagytuk; a migrátor a meglévő metódus kommentjét tartja meg.")
    text, gates = GATE.subn("", text)
    if gates:
        reply.note("A MODULE_REVIEWED modulkaput a migrátor adja hozzá; az AI változatát elhagytuk.")
    return dedent(text)


def placeholders(code: str) -> list[str]:
    mask = java_mask(code)
    return [word for word in ("NOT_IMPLEMENTED", "UnsupportedOperationException") if re.search(r"\b" + word + r"\b", mask)]


def service_parts(block: Block, ctx: Context, reply: Reply, info: dict, existing: dict) -> dict | None:
    imports, code = take_imports(block.code.replace("\r\n", "\n"))
    mask = java_mask(code)
    if not balanced(mask):
        reply.problem("A SERVICE blokk zárójelei nem zárnak" + (" (a válasz megszakadt)." if not block.closed else "."),
                      "The SERVICE block has unbalanced braces or parentheses. Send the complete method again.")
        return None
    members = scan_members(code, mask)
    types = [m for m in members if m.kind == "type" and m.body]
    if types:
        container = next((t for t in types if any(x.kind == "method" and x.name == ctx.method
                                                  for x in scan_members(code, mask, t.body[0] + 1, t.body[1]))), types[0])
        members = scan_members(code, mask, container.body[0] + 1, container.body[1])
        info["note"] = "teljes osztályból (" + container.name + ")"
    methods = [m for m in members if m.kind == "method" and m.body]
    target = next((m for m in methods if m.name == ctx.method), None)
    loose = [m for m in members if m.kind in {"statement", "init"} or (m.kind == "field" and not types and not methods)]
    helpers = []
    if target is None and not methods:
        body = lambda_body(code, mask, 0, len(code))
        info["note"] = "csak a metódustörzs" + (" (log1x-ből)" if body is not None else "")
        body = code if body is None else body
    else:
        if target is None:
            candidates = [m for m in methods if re.search(r"\bActionResult\b", m.header) and " private " not in " " + m.header + " "]
            if len(candidates) == 1 and not loose:
                target = candidates[0]
                reply.warn(f"Az AI a metódust más néven adta vissza ({target.name}); a(z) {ctx.method} törzseként használtuk.")
        if target is not None:
            body = lambda_body(code, mask, target.body[0], target.body[1])
            wrapped = body is not None
            body = code[target.body[0] + 1:target.body[1]] if body is None else body
            info["note"] = ((info["note"] + "; ") if info.get("note") else "") + (
                "a metódus log1x-törzse" if wrapped else "a metódus törzse (log1x nélkül)")
        elif loose:
            body = "\n".join(member_source(code, m) for m in loose)
            reply.warn("A SERVICE blokkban metódus és laza utasítások is voltak; az utasításokat vettük a metódus törzsének.")
        else:
            reply.problem(f"A SERVICE blokkban nincs {ctx.method} metódus.",
                          f"The SERVICE block must contain the complete rewritten method {ctx.method}.")
            return None
        for member in methods:
            if member is target: continue
            text = member_source(code, member)
            if member.name in existing:
                same = " ".join(code[member.start:member.end].split()) in existing[member.name]
                if same or not re.search(r"(?<![\w$])private(?![\w$])", member.header):
                    reply.note(f"A service meglévő {member.name} metódusát is visszaküldte az AI; nem vettük át "
                               "(csak a kiválasztott végpont változik).")
                    continue
            helpers.append(with_access(text, "private"))
        for member in members:
            if member.kind == "field" and (types or methods):
                if re.search(r"Repository\b", member.header):
                    reply.note("A repository-mezőt a migrátor köti be a service-be; az AI mezőjét elhagytuk.")
                elif member.name not in {"log", "jdbc"} and not re.search(r"(?<![\w$])static(?![\w$])", member.header):
                    reply.warn("A service-be javasolt mezőt (" + member.name + ") nem vettük át; használj helyi változót.")
            elif member.kind == "type":
                reply.warn("A SERVICE blokk belső osztályát (" + member.name + ") nem vettük át.")
    return {"body": clean_body(body, reply), "helpers": helpers, "imports": imports}


def as_default(text: str, name: str) -> str:
    """A method with a body, as a default method of the repository interface (no access or static modifier)."""
    mask = java_mask(text)
    match = re.search(r"(?m)^([ \t]*)((?:(?:public|protected|private|static|final|synchronized|default)\s+)*)"
                      r"(?=(?:<[^;{}()]*>\s*)?[\w.$<>?, \[\]]+\s+" + re.escape(name) + r"\s*\()", mask)
    if not match:
        return text
    return text[:match.start(2)] + "default " + text[match.end(2):]


def repository_parts(block: Block, repo: Repository, ctx: Context, reply: Reply, info: dict) -> dict | None:
    """The model's additions to a repository: the repository is `interface X extends JpaRepository<Domain, ID>`, so
    query methods without a body (@Query JPQL, or nativeQuery = true with the original SQL) and default methods."""
    from .reply_repair import STANDARD
    imports, code = take_imports(block.code.replace("\r\n", "\n"))
    mask = java_mask(code)
    if not balanced(mask):
        reply.problem(f"A(z) {repo.name} blokk zárójelei nem zárnak" + (" (a válasz megszakadt)." if not block.closed else "."),
                      f"The REPOSITORY {repo.name} block has unbalanced braces. Send its complete methods again.")
        return None
    members = scan_members(code, mask)
    containers = [m for m in members if m.kind == "type" and m.body]
    if containers:
        container = next((t for t in containers if t.name == repo.name), containers[0])
        if container.name != repo.name:
            reply.warn(f"Az AI {container.name} típust írt; a metódusait a(z) {repo.name} repositoryba tettük.")
        members = scan_members(code, mask, container.body[0] + 1, container.body[1])
        info["note"] = "teljes típusból, csak a metódusok kellenek"
    methods, constants = [], []
    for member in members:
        text = member_source(code, member)
        if member.kind == "field":
            if re.search(r"\bEntityManager\b", member.header):
                reply.note(f"A(z) {repo.name} EntityManager-mezőjét elhagytuk: a repository interfész (extends JpaRepository).")
            elif "=" in java_mask(code[member.start:member.end]):
                constants.append(text)  # an interface field is a constant (static final)
            else:
                reply.warn(f"A(z) {repo.name} mezőjét ({member.name}) elhagytuk: egy repository interfésznek nincs mezője.")
        elif member.kind == "constructor":
            reply.note(f"A(z) {repo.name} konstruktorát elhagytuk: a repository interfész.")
        elif member.kind == "method" and member.name in STANDARD:
            reply.note(f"A(z) {member.name}() a JpaRepository része; a modell változatát elhagytuk.")
        elif member.kind == "method" and not member.body:
            methods.append(text)  # a @Query method, or a Spring Data method name
        elif member.kind == "method":
            if re.search(r"\b(?:entityManager|EntityManager|CriteriaBuilder)\b", java_mask(text)):
                reply.problem(f"A(z) {repo.name} {member.name}() metódusa EntityManagert használ; a repository interfész.",
                              f"{repo.name} is an interface extending JpaRepository<{repo.entity}, ...>: write "
                              f"{member.name} as a method without a body with @Query (JPQL over {repo.entity}, or "
                              "nativeQuery = true with the original SQL), or as a default method that calls such "
                              "methods. No EntityManager.")
                continue
            methods.append(as_default(text, member.name))
        elif member.kind == "type":
            reply.warn(f"A(z) {repo.name} blokk belső típusát ({member.name}) elhagytuk.")
        elif code[member.start:member.end].strip():
            reply.warn(f"A(z) {repo.name} blokk egy nem metódus részét elhagytuk: " + " ".join(code[member.start:member.end].split())[:80])
    return {"methods": methods, "constants": constants, "imports": imports}


def dto_parts(block: Block, reply: Reply) -> list[dict]:
    _, code = take_imports(block.code.replace("\r\n", "\n"))
    mask = java_mask(code)
    found = []
    for member in scan_members(code, mask):
        if member.kind != "type" or not member.name.endswith("Dto"): continue
        fields = []
        if member.type_kind == "record":
            params = re.search(r"\(([^()]*)\)", member.header)
            for part in (params.group(1).split(",") if params and params.group(1).strip() else []):
                words = strip_annotations(part).split()
                if len(words) >= 2: fields.append(("".join(words[:-1]), words[-1]))
            reply.warn(f"A(z) {member.name} recordként érkezett; a migrátor getX()/setX() DTO-osztályt készít belőle.")
        elif member.body:
            for inner in scan_members(code, mask, member.body[0] + 1, member.body[1]):
                if inner.kind == "field" and not re.search(r"(?<![\w$])static(?![\w$])", inner.header):
                    match = re.match(r"^(?:(?:private|protected|public|final|transient)\s+)*(.+?)\s+([A-Za-z_$][\w$]*)\s*(?:=.*)?$",
                                     inner.header)
                    if match: fields.append((match.group(1).replace(" ", ""), match.group(2)))
        mapped = []
        for typ, name in fields:
            if typ not in DTO_TYPES:
                reply.problem(f"A(z) {member.name}.{name} mező típusa ({typ}) nem támogatott.",
                              "Do not create new classes; use local variables or the entities instead of a DTO.")
                mapped = []
                break
            mapped.append({"name": name, "type": DTO_TYPES[typ]})
        if mapped:
            found.append({"name": member.name, "fields": mapped})
    return found


# --- imports and consistency ----------------------------------------------------------------------------------------

def keep_imports(entries, ctx: Context, provided: set, own_package: str, code: str, reply: Reply, where: str) -> list[str]:
    used = set(re.findall(r"(?<![\w$.])([A-Z][\w$]*)", java_mask(code)))
    result = []
    for fqn, static, wildcard in entries:
        if static or wildcard:
            if wildcard or fqn.rsplit(".", 1)[-1] in used:
                reply.note(f"{where}: a(z) {'static ' if static else ''}{fqn}{'.*' if wildcard else ''} importot "
                           "nem vettük át; a migrátor osztályonként importál.")
            continue
        if not FQN.fullmatch(fqn): continue
        package, simple = fqn.rsplit(".", 1)
        if simple not in used or simple in provided or package == own_package: continue
        if package.startswith(("javax.persistence", "jakarta.persistence")):
            fqn = ctx.namespace + ".persistence" + package.split(".persistence", 1)[1] + "." + simple
        elif not (fqn.startswith(("java.", "org.springframework.")) or fqn in ctx.allowed_imports):
            reply.warn(f"{where}: ismeretlen import kihagyva: {fqn}")
            continue
        if fqn not in result: result.append(fqn)
    return result


def decode_literal_escapes(code: str) -> str:
    """`"\\u00e1"` -> `"á"` inside string/char literals (Java source is UTF-8). Escapes that would change the
    literal's structure (quotes, backslash, control characters) stay; any \\u left is reported."""
    def literal(match):
        text, out, i = match.group(0), [], 0
        while i < len(text):
            if text[i] == "\\" and i + 1 < len(text):
                escape = re.match(r"\\u+([0-9a-fA-F]{4})", text[i:])
                if escape:
                    char = chr(int(escape.group(1), 16))
                    if char.isprintable() and char not in "\"'\\":
                        out.append(char); i += escape.end(); continue
                out.append(text[i:i + 2]); i += 2; continue
            out.append(text[i]); i += 1
        return "".join(out)
    return re.sub(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', literal, code)


def pascal(name: str) -> str:
    return name[:1].upper() + name[1:]


def unique(name: str, taken: set) -> str:
    candidate, counter = name, 2
    while candidate in taken:
        candidate, counter = name + str(counter), counter + 1
    return candidate


def first_method(text: str):
    return next((m for m in scan_members(text, java_mask(text)) if m.kind == "method"), None)


def check_service_code(code: str, ctx: Context, reply: Reply, missing: bool) -> None:
    mask = java_mask(code)
    if SYSTEM_API.search(mask):
        reply.problem("A service-kód nem megengedett rendszer-, fájl- vagy hálózati API-t használ.",
                      "Do not use Runtime, ProcessBuilder, reflection, files or network APIs.")
    if ctx.mode == "jpa":
        if re.search(r"\b(?:entityManager|EntityManager|Session|SessionFactory)\b", mask):
            generated = [line for lines in ctx.generated.values() for line in lines]
            reply.problem("A service közvetlenül EntityManagert használ; az adatbázis-művelet a repositoryba tartozik.",
                          ("The service must not use the EntityManager. Call the generated repository methods instead: "
                           + "; ".join(generated) + ".") if generated else
                          "The service must not use the EntityManager. Put every database operation into a repository "
                          "method and call that method from the service.")
        if NATIVE_DB.search(mask) or re.search(r"(?<![\w$.])jdbc\s*\.", mask):
            reply.problem("A service SQL-, JDBC- vagy PL/SQL-hívást tartalmaz; JPA-módban a repository éri el az adatbázist.",
                          "Do not keep the PL/SQL or JDBC call (DbCalls, jdbc). Implement the logic in Java and use "
                          "repository methods for the database.")
    getters = sorted(set(re.findall(r"\brequest\s*\.\s*(get(?:Blocks|Parameters))\s*\(", mask)))
    if getters:
        reply.problem("A kérés DTO-n nincs " + ", ".join(g + "()" for g in getters) + " metódus; request.blocks() és "
                      "request.parameters() a helyes.",
                      "ActionRequest has no getBlocks()/getParameters(); use request.blocks() and request.parameters().")
    found = placeholders(code)
    if found and not missing:
        reply.problem("A kódban helyőrző maradt: " + ", ".join(found) + ".",
                      "The method still throws a placeholder (" + ", ".join(found) + "). Implement the logic, or list "
                      "exactly what is missing under MISSING:.")


def consistency(ctx: Context, service_code: str, new_methods: dict, reply: Reply) -> None:
    mask = java_mask(service_code)
    variables = {r.variable for r in ctx.repositories}
    from .reply_repair import STANDARD
    for repo in ctx.repositories:
        # JpaRepository's own methods (findById, save ...) are there in every repository
        available = set(methods_by_name(repo.existing)) | new_methods.get(repo.name, set()) | STANDARD
        calls = re.finditer(r"(?:(?<=this\.)|(?<![\w$.]))" + re.escape(repo.variable) + r"\s*\.\s*([A-Za-z_$][\w$]*)\s*\(", mask)
        for name in dict.fromkeys(call.group(1) for call in calls):
            if name not in available:
                generated = ctx.generated.get(repo.name)
                reply.problem(f"A service a(z) {repo.variable}.{name}() metódust hívja, de a(z) {repo.name} nem tartalmazza.",
                              (f"The service calls {repo.variable}.{name}(...), which does not exist. Call only the "
                               "generated methods (their SQL is the Forms code's SQL): " + "; ".join(generated) + ".")
                              if generated else
                              f"The service calls {repo.variable}.{name}(...), but the REPOSITORY {repo.name} block does "
                              "not define it. Add it there as an @Query method without a body (JPQL over "
                              f"{repo.entity}, or nativeQuery = true with the original SQL).")
    for unknown in sorted(set(re.findall(r"(?:(?<=this\.)|(?<![\w$.]))([a-z][\w$]*Repository)\s*\.", mask)) - variables):
        names = ", ".join(sorted(variables))
        reply.problem(f"A service ismeretlen repositoryt használ: {unknown} (elérhető: {names or 'nincs'}).",
                      f"`{unknown}` does not exist. " + (f"Use only these repository fields: {names}." if names else
                      "No repository is available; list the needed table under MISSING:."))


# --- assembling the proposal ---------------------------------------------------------------------------------------

def extract(raw: str, ctx: Context, *, truncated: bool = False) -> Reply:
    reply = Reply(truncated=truncated)
    content, reply.thinking = split_thinking(raw or "")
    if not content.strip():
        reply.problem("Az AI-válasz üres" + (" (csak gondolkodási szöveg érkezett)." if reply.thinking else "."),
                      "Your answer was empty. Send the Java code blocks in the required format.")
        return reply
    legacy = parse_json_answer(content)
    if legacy is not None:
        reply.format = "json"
        return from_json(legacy, ctx, reply)
    blocks, prose = fenced_blocks(content)
    if not any(b.code.strip() for b in blocks):
        cut = SECTION.search(content)
        code = content[:cut.start()] if cut else content
        if looks_like_java(code):
            reply.format = "plain"
            reply.warn("Az AI nem használt kódblokkot; a választ Java-kódként dolgoztuk fel.")
            blocks, prose = [Block(code)], content[cut.start():] if cut else ""
    reply.missing, reply.assumptions = sections(prose)
    return assemble(blocks, ctx, reply)


def assemble(blocks: list[Block], ctx: Context, reply: Reply) -> Reply:
    existing_service = methods_by_name(ctx.service) if ctx.service else {}
    service, helpers, service_imports, dtos, extra_helpers = None, [], [], [], []
    repo_parts = {r.name: {"methods": [], "constants": [], "imports": []} for r in ctx.repositories}
    classified, unknown = [], []
    for index, block in enumerate(blocks, 1):
        lines = len(block.code.strip().split("\n")) if block.code.strip() else 0
        info = {"index": index, "lang": block.lang, "lines": lines, "closed": block.closed, "kind": "", "target": "",
                "label": "", "used": False, "note": ""}
        reply.blocks.append(info)
        if not block.code.strip():
            info.update(kind="empty", note="üres blokk"); continue
        if block.lang in SKIPPED_LANGS or (block.lang not in JAVA_LANGS and not looks_like_java(block.code)):
            info.update(kind="skipped", note="nem Java-blokk (" + (block.lang or "szöveg") + ")"); continue
        if not block.closed:
            reply.truncated = True
            reply.warn(f"A(z) {index}. kódblokk nincs lezárva (a válasz valószínűleg a kimeneti keretnél megszakadt).")
        kind, target, label = classify_block(block, ctx)
        info.update(kind=kind, label=label)
        top = re.match(r"^\s*//[^\n]*(?:\n|$)", block.code)
        if top and label and first_comment_label(block.code) == label:
            block = Block(block.code[top.end():], block.lang, block.heading, block.closed)   # the label is not code
        (unknown if kind == "unknown" else classified).append((index, block, kind, target, info))
    if not any(kind == "service" for _, _, kind, _, _ in classified) and unknown:
        index, block, _, target, info = unknown.pop(0)
        info.update(kind="service", note="címke nélkül, tartalom alapján")
        classified.append((index, block, "service", target, info))
    for item in list(unknown):
        # A block of private methods only, after the SERVICE block: the service's helpers (the method may call them).
        methods = helper_methods(item[1])
        if methods and any(kind == "service" for _, _, kind, _, _ in classified):
            unknown.remove(item)
            item[4].update(kind="service", note="címke nélküli privát segédmetódusok", used=True)
            extra_helpers += methods
    for _, _, _, _, info in unknown:
        info["note"] = "nem azonosítható blokk, kihagyva"
        reply.warn(f"A(z) {info['index']}. kódblokkot nem tudtuk besorolni, kihagytuk.")
    for index, block, kind, target, info in sorted(classified, key=lambda item: item[0]):
        if kind == "entity":
            info["note"] = "entity: a migrátor generálja a táblaszerkezetből, kihagyva"
            reply.note("Az AI entity-osztályt is írt; az entityt a migrátor generálja, ezt a blokkot kihagytuk.")
        elif kind == "repository" and target is None:
            info["note"] = "ismeretlen repository"
            if ctx.mode != "jpa" or not ctx.repositories:
                reply.problem("Az AI repository-kódot írt, de ehhez a metódushoz nincs elérhető repository.",
                              "No repository is available for this method. Do not write repository code; if the logic "
                              "needs a table, list it under MISSING:.")
            else:
                reply.problem(f"Nem azonosítható, melyik repositoryhoz tartozik a(z) {index}. blokk.",
                              "Start every repository block with `// REPOSITORY <Name>` using one of: "
                              + ", ".join(r.name for r in ctx.repositories) + ".")
        elif kind == "service":
            info["target"] = ctx.method
            parts = service_parts(block, ctx, reply, info, existing_service)
            if parts is None: continue
            if service is None or (not service["body"].strip() and parts["body"].strip()):
                service = parts
            elif parts["body"].strip():
                reply.warn(f"Több SERVICE blokk érkezett; az elsőt használtuk, a(z) {index}. blokk törzsét elhagytuk.")
            helpers += parts["helpers"]
            service_imports += parts["imports"]
            info["used"] = True
        elif kind == "repository":
            info["target"] = target.name
            parts = repository_parts(block, target, ctx, reply, info)
            if parts is None: continue
            for key in ("methods", "constants", "imports"):
                repo_parts[target.name][key] += parts[key]
            info["used"] = bool(parts["methods"] or parts["constants"])
            if not info["used"]:
                info["note"] = ((info["note"] + "; ") if info["note"] else "") + "nincs benne új metódus"
        elif kind == "dto":
            info["target"] = target or ""
            found = dto_parts(block, reply)
            dtos += found
            info["used"] = bool(found)
    return finish(ctx, reply, service, helpers + extra_helpers, service_imports, repo_parts, dtos)


def helper_methods(block: Block) -> list[str]:
    """The methods of a block that holds private methods and nothing else (no class, field or statement)."""
    _, code = take_imports(block.code.replace("\r\n", "\n"))
    mask = java_mask(code)
    if not balanced(mask):
        return []
    members = scan_members(code, mask)
    if not members or any(m.kind != "method" or not m.body or not re.search(r"(?<![\w$])private(?![\w$])", m.header)
                          for m in members):
        return []
    return [member_source(code, m) for m in members]


def finish(ctx: Context, reply: Reply, service, helpers, service_imports, repo_parts, dtos, summary: str = "") -> Reply:
    missing = bool(reply.missing)
    body = (service or {}).get("body", "")
    if not body.strip() and not missing:
        if not reply.problems:
            reply.problem("Nem találtunk használható SERVICE kódot az AI válaszában.",
                          f"Send the complete rewritten method {ctx.method} in a ```java block that starts with // SERVICE.")
        return reply
    # The usual slips with one meaning (request.getBlocks(), row.getX(), a repository without the module prefix):
    # repaired here, not by another model call (reply_repair).
    from . import reply_repair
    fixes = []
    body = reply_repair.service(body, ctx, fixes)
    helpers = [reply_repair.service(h, ctx, fixes) for h in helpers]
    for text in dict.fromkeys(fixes):
        reply.note(text)
    # Repository methods: reuse identical existing ones, rename colliding ones (and the service calls).
    new_methods = {}
    for repo in ctx.repositories:
        existing = methods_by_name(repo.existing)
        generated = {re.match(r"[\w$]+\.([\w$]+)\(", line).group(1) for line in ctx.generated.get(repo.name, [])}
        additions, taken = [], set(existing)
        for text in repo_parts[repo.name]["methods"]:
            member = first_method(text)
            if member is None: continue
            name = member.name
            if name in existing and " ".join(text[member.start:].split()) in existing[name]:
                reply.note(f"A(z) {repo.name}.{name}() már létezik ugyanígy; a meglévőt használjuk.")
                continue
            if name in generated:
                # The model rewrote a method the migrator generated from the SQL: the generated one stays.
                reply.note(f"A(z) {repo.name}.{name}() a migrátor generált metódusa (az eredeti SQL-ből); "
                           "az AI saját változatát elhagytuk.")
                continue
            if name in existing:
                new = unique(ctx.method + pascal(name), taken | set(existing))
                reply.warn(f"A(z) {repo.name}.{name}() már létezik más tartalommal; az új metódus neve: {new}.")
                text = replace_identifier(text, name, new)
                additions = [replace_identifier(a, name, new) for a in additions]
                body = replace_identifier(body, name, new, member_of=repo.variable)
                helpers = [replace_identifier(h, name, new, member_of=repo.variable) for h in helpers]
                name = new
            taken.add(name)
            additions.append(text)
        repo_parts[repo.name]["methods"] = additions
        new_methods[repo.name] = {m.name for m in map(first_method, additions) if m}
    # Service helpers: never replace another method of the service.
    existing_service = set(methods_by_name(ctx.service)) if ctx.service else set()
    final_helpers, helper_names = [], set()
    for text in helpers:
        member = first_method(text)
        if member is None: continue
        name = member.name
        if name in ctx.routines:
            reply.problem(f"Az AI megírta a(z) {name} adatbázis-rutin metódusát; azt a migrátor adja.",
                          f"Do not write {name}: it is the generated method of a database routine. Call it.")
            continue
        if name in existing_service:
            new = unique(ctx.method + pascal(name), existing_service | helper_names)
            reply.warn(f"A service-ben már van {name} metódus; az AI segédmetódusának új neve: {new}.")
            text = replace_identifier(text, name, new)
            body = replace_identifier(body, name, new)
            final_helpers = [replace_identifier(h, name, new) for h in final_helpers]
            name = new
        if " ".join(text.split()) in {" ".join(h.split()) for h in final_helpers}:
            continue
        helper_names.add(name)
        final_helpers.append(text)
    for dto in dtos:
        if ctx.module_class and not dto["name"].startswith(ctx.module_class):
            new = ctx.module_class + dto["name"]
            reply.note(f"A(z) {dto['name']} DTO neve a modul előtagjával: {new}.")
            body = replace_identifier(body, dto["name"], new)
            final_helpers = [replace_identifier(h, dto["name"], new) for h in final_helpers]
            for parts in repo_parts.values():
                parts["methods"] = [replace_identifier(m, dto["name"], new) for m in parts["methods"]]
            dto["name"] = new
    body = decode_literal_escapes(body)
    final_helpers = [decode_literal_escapes(h) for h in final_helpers]
    for parts in repo_parts.values():
        parts["methods"] = [decode_literal_escapes(m) for m in parts["methods"]]
        parts["constants"] = [decode_literal_escapes(c) for c in parts["constants"]]
    service_code = body + "\n" + "\n".join(final_helpers)
    # findById/save/delete ... on a repository without generated methods: the migrator writes them (reply_repair).
    available = {r.name: set(methods_by_name(r.existing)) | new_methods.get(r.name, set()) for r in ctx.repositories}
    for name, methods in reply_repair.standard_methods(service_code, ctx, available, reply.warnings).items():
        repo_parts[name]["methods"] += [source for source, _ in methods]
        new_methods[name] = new_methods.get(name, set()) | {first_method(source).name for source, _ in methods}
    checks = Reply()
    if "\\u" in service_code + "".join(m for p in repo_parts.values() for m in p["methods"] + p["constants"]):
        checks.problem("A kódban \\uXXXX unicode-escape maradt (biztonsági okból nem vehető át).",
                       "Do not use \\uXXXX escapes; write the characters directly (the source is UTF-8).")
    if body.strip():
        check_service_code(service_code, ctx, checks, missing)
        consistency(ctx, service_code, new_methods, checks)
    if ctx.mode != "jpa" and any(p["methods"] or p["constants"] for p in repo_parts.values()):
        checks.problem("Repository-kód csak JPA-módban vehető át.", "Do not write repository code in this mode.")
    repositories = []
    for repo in ctx.repositories:
        parts = repo_parts[repo.name]
        if not parts["methods"] and not parts["constants"]: continue
        code = "\n\n".join(parts["constants"] + parts["methods"])
        if any(re.search(r"\bnativeQuery\s*=\s*true\b", text) for text in parts["methods"]):
            reply.warn(f"A(z) {repo.name} natív SQL-t is használ (@Query nativeQuery = true); ellenőrizd, hogy JPQL-ben nem írható-e le.")
        if NATIVE_DB.search(java_mask(without_sequence_queries(code))):
            checks.problem(f"A(z) {repo.name} JDBC-t vagy EntityManager-natív hívást használ.",
                           f"{repo.name} is an interface extending JpaRepository: write @Query methods (JPQL, or "
                           "nativeQuery = true with the original SQL); no JDBC, createNativeQuery or stored procedure calls.")
        if SYSTEM_API.search(java_mask(code)):
            checks.problem(f"A(z) {repo.name} nem megengedett rendszer-API-t használ.",
                           "Do not use Runtime, ProcessBuilder, reflection, files or network APIs.")
        imports = keep_imports(parts["imports"], ctx, provided_repository(ctx), package_of(repo.existing), code, reply, repo.name)
        repositories.append({"name": repo.name, "methods": code, "imports": imports})
    for problem in checks.problems:
        # A draft that waits for missing source is only shown, never applied: its gaps are warnings.
        if missing: reply.warn(problem.hu)
        else: reply.problem(problem.hu, problem.en)
    service_imports = keep_imports(service_imports, ctx, provided_service(ctx), package_of(ctx.service), service_code,
                                   reply, "SERVICE")
    if re.search(r"\b(?:TODO|FIXME)\b", service_code + "\n".join(r["methods"] for r in repositories)):
        reply.warn("A kódban TODO/FIXME megjegyzés maradt; átvétel előtt ellenőrizd.")
    notes = reply.assumptions[:10]
    text = summary or (notes[0] if notes else "")
    if not text:
        count = sum(len(names) for names in new_methods.values())
        text = ((f"A(z) {ctx.method} végpont Java-változata" + (f", {count} új repository-metódussal." if count else "."))
                if body.strip() else "Az AI további adatot kér a teljes átíráshoz.")
    reply.response = {
        "status": "needs_context" if missing else "proposal",
        "summary": text[:4000],
        "body": body.strip("\n"),
        "imports": service_imports,
        "private_methods": "\n\n".join(final_helpers),
        "repositories": repositories,
        "dtos": dtos,
        "assumptions": notes,
        "missing_context": reply.missing,
        "test_cases": [],
    }
    if missing:
        reply.warn("Az AI szerint ez hiányzik a teljes átíráshoz: " + "; ".join(reply.missing))
    return reply


def from_json(value: dict, ctx: Context, reply: Reply) -> Reply:
    """The legacy JSON answer, checked like the Markdown one."""
    def text(key):
        item = value.get(key)
        return item if isinstance(item, str) else ""
    def strings(key):
        items = value.get(key)
        return [s.strip()[:3000] for s in items if isinstance(s, str) and s.strip()] if isinstance(items, list) else []
    reply.missing = [s for s in strings("missing_context") if not is_nothing(s)]
    if value.get("status") == "needs_context" and not reply.missing:
        reply.missing = ["Az AI további adatot kért, de nem nevezte meg, mit."]
    reply.assumptions = strings("assumptions")
    service = None
    body, helper_code = text("body"), text("private_methods")
    if body.strip() or helper_code.strip():
        reply.blocks.append({"index": 1, "lang": "json", "lines": len(body.split("\n")), "closed": True, "kind": "service",
                             "target": ctx.method, "label": "body + private_methods", "used": True, "note": "JSON-mezők"})
        if not balanced(java_mask(body)) or not balanced(java_mask(helper_code)):
            reply.problem("A body vagy a private_methods mező zárójelei nem zárnak.",
                          "The method body has unbalanced braces. Send the complete method body.")
            return reply
        service = {"body": clean_body(body, reply), "helpers": [],
                   "imports": [(i, False, False) for i in strings("imports")]}
        helper_mask = java_mask(helper_code)
        for member in scan_members(helper_code, helper_mask):
            if member.kind == "method" and member.body:
                service["helpers"].append(with_access(member_source(helper_code, member), "private"))
            else:
                reply.problem("A private_methods mezőben csak teljes privát metódus lehet.",
                              "private_methods may contain complete private methods only.")
    repo_parts = {r.name: {"methods": [], "constants": [], "imports": []} for r in ctx.repositories}
    entries = value.get("repositories") if isinstance(value.get("repositories"), list) else []
    for index, entry in enumerate(entries, 2):
        if not isinstance(entry, dict) or not isinstance(entry.get("methods"), str): continue
        name = str(entry.get("name", ""))
        repo = resolve_repository(name, ctx, entry["methods"]) if ctx.repositories else None
        info = {"index": index, "lang": "json", "lines": len(entry["methods"].split("\n")), "closed": True,
                "kind": "repository", "target": repo.name if repo else name, "label": "repositories[]", "used": False, "note": ""}
        reply.blocks.append(info)
        if repo is None:
            reply.problem("Ismeretlen repository a válaszban: " + name,
                          "Use only these repositories: " + (", ".join(r.name for r in ctx.repositories) or "none") + ".")
            continue
        parts = repository_parts(Block(entry["methods"]), repo, ctx, reply, info)
        if parts is None: continue
        parts["imports"] += [(i, False, False) for i in entry.get("imports", []) if isinstance(i, str)]
        for key in ("methods", "constants", "imports"):
            repo_parts[repo.name][key] += parts[key]
        info["used"] = bool(parts["methods"])
    dtos = []
    for entry in value.get("dtos") if isinstance(value.get("dtos"), list) else []:
        if not (isinstance(entry, dict) and isinstance(entry.get("name"), str) and isinstance(entry.get("fields"), list)):
            continue
        fields = [f for f in entry["fields"] if isinstance(f, dict)]
        if fields and all(isinstance(f.get("type"), str) and f["type"] in DTO_TYPES and isinstance(f.get("name"), str) for f in fields):
            dtos.append({"name": entry["name"], "fields": [{"name": f["name"], "type": DTO_TYPES[f["type"]]} for f in fields]})
        else:
            reply.problem(f"A(z) {entry['name']} DTO mezőtípusa nem támogatott.",
                          "Do not create new classes; use local variables or the entities instead of a DTO.")
    return finish(ctx, reply, service, (service or {}).get("helpers", []), (service or {}).get("imports", []),
                  repo_parts, dtos, text("summary").strip())


def provided_service(ctx: Context) -> set:
    names = {m.group(2).rsplit(".", 1)[-1] for m in IMPORT_LINE.finditer(ctx.service)}
    names |= {r.name for r in ctx.repositories} | {r.entity for r in ctx.repositories}
    return names | {"ActionRequest", "ActionResult", "UserDto"}


def provided_repository(ctx: Context) -> set:
    return {r.name for r in ctx.repositories} | {r.entity for r in ctx.repositories} | {r.entity + "Id" for r in ctx.repositories}


def package_of(source: str) -> str:
    match = re.search(r"^\s*package\s+([\w.]+)\s*;", source or "", re.M)
    return match.group(1) if match else ""


def correction(reply: Reply, ctx: Context) -> str:
    """The follow-up user message of a retry (English, like the prompt)."""
    lines = ["Your previous answer could not be used:"] + ["- " + p.en for p in reply.problems]
    blocks = "one ```java block that starts with `// SERVICE`"
    if ctx.mode == "jpa" and any(r.name not in ctx.generated for r in ctx.repositories):
        blocks += " and, for new repository methods, one block per repository that starts with `// REPOSITORY <Name>`"
    lines += ["", "Send the complete answer again: " + blocks + ". Do not explain the code."]
    return "\n".join(lines)

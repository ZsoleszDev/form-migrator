from __future__ import annotations

import hashlib
import json
from pathlib import Path
from .common import name, write_json
from .generate import write
from .rules import BLOCKING_SCOPES


def operation_state(b: dict, op: str) -> str:
    """Report cell for one CRUD operation of a block."""
    plan = b.get("endpoint_plan")
    if plan is None:
        return "igen" if b["can_" + op] else "tiltva"
    if not b["database"]:
        return "—"
    generated = plan.get("list") or plan.get("search") if op == "read" else plan.get(op)
    if not generated:
        return "nem készül"
    key = "search" if op == "read" and plan.get("search") else op
    if b.get("can_" + key):
        return "igen"
    return "kész, MODULE_REVIEWED-re vár" if b.get("ready_" + key) else "tiltva"


def summary(model: dict) -> dict:
    return {"form": model["name"], "generation_mode": model.get('generation_mode', 'strict'),
            "frontend_mode": {"scaffold": "review_scaffold", "screen": "structural_single_file"}.get(model.get('generation_mode'), "ui_model_multifile"), "blocks": len(model["blocks"]), "items": sum(len(b["items"]) for b in model["blocks"]),
            "triggers": len(model["triggers"]), "converted_triggers": sum(t["status"] == "converted" and not t.get("ai") for t in model["triggers"]),
            # backend_trigger_mode=ai: the frame is generated, the body waits for the Java assistant
            "ai_triggers": sum(bool(t.get("ai")) for t in model["triggers"]),
            "review_triggers": sum(t["status"] == "review" for t in model["triggers"]),
            "framework_triggers": sum(t["status"] == "framework" for t in model["triggers"]),
            "readable_blocks": sum(bool(b["can_read"]) for b in model["blocks"]),
            "writable_blocks": sum(bool(b["can_create"] or b["can_update"] or b["can_delete"]) for b in model["blocks"]),
            "blocking_issues": sum(i["scope"] in BLOCKING_SCOPES for i in model["issues"]),
            "module_review_required": bool(model.get("module_gate", {}).get("required")), "ai": model.get("ai", {})}


def markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ").replace("`", "'")


def reports(model: dict, output: Path, module: str, package: str, config: dict) -> None:
    info = summary(model)
    write_json(output / "analysis" / "form.ir.json", {k: v for k, v in model.items() if k != "_cache"})
    write_json(output / "analysis" / "summary.json", info)
    write_json(output / "analysis" / "issues.json", model["issues"])
    write_json(output / "analysis" / "object-inventory.json", model["inventory"])
    write_json(output / "analysis" / "external-code.json", {k: model[k] for k in ["program_units", "record_groups", "lovs", "relations", "libraries"]})
    for n, tr in enumerate(model["triggers"]):
        filename = f"{n:04d}-{tr['sha256'][:12]}.sql"
        write(output / "analysis" / "original-triggers" / filename, "-- " + tr["id"].replace("\n", " ") + "\n" + tr["source"] + "\n")
    advice = [{"trigger": t["id"], "sha256": t["sha256"], "status": t.get("ai_status"), "advice": t.get("ai_advice"), "error": t.get("ai_error"), "shared_from": t.get("ai_shared_from")} for t in model["triggers"] if "ai_status" in t]
    write_json(output / "analysis" / "ai-advice.json", advice)
    lines = [f"# Migrációs riport — {model['name']}", "", "Ez tényleges generálási leltár, nem funkcionális azonosságot becslő százalék. A forrás XML teljes példánya az analysis/source.xml fájlban marad.", "", "| Mutató | Darab |", "|---|---:|"]
    if (output/'BACKEND_TASKS.md').exists():
        lines[2] += ' Konkrét backendbekötések és megmaradt feladatok: [BACKEND_TASKS.md](BACKEND_TASKS.md).'
    lines += [f"| {label} | {info[key]} |" for key, label in [("blocks", "Blokkok"), ("items", "Mezők/gombok"), ("triggers", "Triggerek"), ("converted_triggers", "Szabálymotor által felismert triggerek"), ("ai_triggers", "AI-ra váró triggerek (Java asszisztens)"), ("review_triggers", "Átültetendő triggerek"), ("framework_triggers", "Keretrendszeri (nem teendő) triggerek"), ("blocking_issues", "Blokkoló leletek")]]
    lines += ["", "## Blokkok", "", "| Oracle blokk | REST-részútvonal | Lekérdezés | Beszúrás | Módosítás | Törlés |", "|---|---|---|---|---|---|"]
    for b in model["blocks"]:
        lines.append("| " + " | ".join([markdown_cell(b["name"]), b["key"]] + [operation_state(b, op) for op in ["read", "create", "update", "delete"]]) + " |")
    gate = model.get("module_gate", {})
    if gate.get("required"):
        lines += ["", "A „kész, MODULE_REVIEWED-re vár” műveleteknek nincs saját tiltása; a DPS ServiceImpl egyetlen MODULE_REVIEWED kapcsolója élesíti őket, ha a modulszintű ellenőrzés megtörtént:", ""]
        lines += ["- " + markdown_cell(reason) for reason in gate["reasons"]]
    skipped = [(b["name"], b["backend_skip"]) for b in model["blocks"] if b.get("backend_skip")]
    if skipped:
        lines += ["", "Backend nélküli blokkok (nincs adatforrás):", ""]
        lines += ["- " + markdown_cell(block) + ": " + markdown_cell(reason) for block, reason in skipped]
    lines += ["", "## Triggerek", "", "| Azonosító | Eredmény | Cél / ok | SHA256 |", "|---|---|---|---|"]
    for tr in model["triggers"]:
        lines.append("| " + " | ".join(markdown_cell(v) for v in [tr["id"], tr["status"], tr.get("reason", tr["target"]), tr["sha256"]]) + " |")
    lines += ["", "## Ellenőrzési tételek", "", "| Kód | Objektum | Hatókör | Részlet |", "|---|---|---|---|"]
    for issue in model["issues"]:
        lines.append("| " + " | ".join(markdown_cell(issue[k]) for k in ["code", "owner", "scope", "detail"]) + " |")
    lines += ["", "Hatókör: all = a kapcsolódó olvasás és írás tiltva; read = csak az olvasás; write = minden írás; create/update/delete = csak az adott írás; button = az érintett gomb tiltva; frontend = képernyő-feladat, a backendet nem tiltja; review = megőrzött/ellenőrizendő elem.", "", "## AI-használat", "", "```json", json.dumps(model.get("ai", {}), ensure_ascii=False, indent=2), "```", "", "Az AI-javaslatok nem oldanak fel tiltást, és nem kerülnek a forráskódba. Az analysis/ai-advice.json tartalmazza őket.", "", "## Esemény- és adatbázis-szemantika", "", "A következő szemantika a Java backendhez tartozik. A 3.0-s frontend csak a FormBlock képernyőt építi fel; az eredeti gombok eseményt adnak a hostnak, a triggerek nem futnak automatikusan a böngészőben.", "",
        "- Egy REST-mentés egy blokk egyetlen rekordját kezeli egy Spring-tranzakcióban. Több blokk COMMIT_FORM viselkedése nem támogatott.",
        "- WHEN-VALIDATE-ITEM: minden támogatott item-trigger lefut mentéskor, XML-sorrendben; utána WHEN-VALIDATE-RECORD, majd PRE-INSERT/PRE-UPDATE. Nincs Forms-fókuszváltási állapotgép.",
        "- PRE-DELETE a zárolt aktuális rekordon, POST-QUERY a betöltött rekordon fut. POST-QUERY adatbázisoszlopot író változata blokkolva van a snapshot sértetlensége miatt.",
        "- A mentési snapshot az összes leképezett DB-oszlopot összehasonlítja, SELECT FOR UPDATE NOWAIT után (a más munkamenet által zárolt rekord HTTP 409, nincs várakozás). DB oldali version oszlop nélkül az ABA-változás (visszaállított érték) nem érzékelhető.",
        "- BigDecimal, Oracle üres sztring = NULL, háromértékű logika és rövidzár támogatott. NLS, CHAR padding, locale szerinti rendezés, numerikus túlcsordulás és teljes Oracle NUMBER kerekítés nem emulált. Osztás: 38 számjegyű MathContext.",
        "- DATE a napszakot is megőrzi, LocalDateTime formában. TIMESTAMP időzónával, LOB, LONG, RAW és speciális típusok adaptert igényelnek.",
        "- A meglévő DB triggerek, constraint-ek, defaultok és package-ek továbbra is a DB-ben maradnak; az eszköz nem csatlakozik az adatbázishoz és nem ellenőrzi őket.",
        "- UI Enabled/Visible nem jogosultság. A host alkalmazás jogosultságát és sor-/tenant-szűrését be kell kötni.",
        "- A komponens standard mezőelrendezést készít; az eredeti canvas, LOV, tab, alert és összes Forms-runtime tulajdonság nem emulált.",
        "", "## Beépítés", "", "A konkrét package, import és útvonal az INTEGRATION.md fájlban található. A generátor tesztelése nem helyettesíti a host Java/Angular buildet és az üzleti regressziót.", ""]
    lines += ["## Frontend modell", "", "A sémavalidált mezőterv: analysis/ui-model.json. A generált frontendben nincs automatikus CRUD vagy Forms runtime.", ""]
    for change in model.get("presentation", {}).get("changes", []):
        lines.append("- " + markdown_cell(change["source"]) + ": " + change["detail"])
    if model.get("inheritance_summary", {}).get("form_has_inheritance"):
        lines += ["", "## OLB-öröklés — 2. lépés", "",
                  f"Feloldott kapcsolatok: {model['inheritance_summary']['resolved_links']}. A property-k és trigger-törzsek forrása az analysis/inheritance.json fájlban, a sablonkatalógus az analysis/olb-catalog.json fájlban található.",
                  "Az explicit értékek felülírják az örökölteket. A default jelölés a meglévő parser helyettesítő értékét jelenti; nem Oracle-verziókon át érvényes alapértéktáblát.",
                  "Az XML-öröklés feloldása nem oldja fel a backend üzleti tiltásait. Az INHERITANCE / INHERITANCE_BACKEND_REVIEW tételek modulszintű ellenőrzésként maradnak meg: a DPS ServiceImpl MODULE_REVIEWED kapcsolója mögött, nem metódusonként ismételve.", ""]
    lines += ['', '## Modultérkép és gombvégpontok', '',
        'Kereshető, offline objektum- és hívástérkép: analysis/discovery/form-explorer.html. Eredeti forrás: analysis/discovery/sources/.',
        'A gombesemények fix végpontterve: analysis/action-plan.json. A lexikai hívásjelöltek szignatúrája, iránya és futási sorrendje ellenőrizendő.',
        'Java generáláskor modulonként 4 CL-, 5 DPS- és 5 WBS-fájl készül. Fájlok, DTO-kiválasztás és végpontok: analysis/backend-plan.json.',
        'A DPS *ServiceImpl.java CRUD-metódusainál az adatforrás és műveleti utasítások, protected *Reviewed gombmetódusainál a forrás/híváslánc és HTTP 501 váz található. Ez a fájl CREATE_ONCE, itt folytasd az implementációt.', '']
    write(output / "migration-report.md", "\n".join(lines))
    from .integration import guide
    write(output / "INTEGRATION.md", guide(module, package, config, model.get("presentation", {}).get("name")))


def manifest(output: Path) -> None:
    entries = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "generated-files.json":
            entries.append({"path": path.relative_to(output).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size})
    from . import __version__
    write_json(output / "generated-files.json", {"generator": "frm-forms-migrator", "version": __version__, "files": entries})

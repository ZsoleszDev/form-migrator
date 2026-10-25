from collections import Counter
from pathlib import Path
import hashlib
from .common import write_json
from .angular_ui import overwrite_policy

def report(model):
    items=[i for b in model['blocks'] for i in b['items']]; counts=Counter(i['widget'] for i in items)
    explicit=sum(i['type_source']=='explicit' for i in items); inferred=len(items)-explicit
    def cell(v):return str(v).replace('|','\\|').replace('\n',' ').replace('\r',' ')
    lines=['## Frontend UI-modell 1.0.0', '', 'A frontend kizárólag az analysis/ui-model.json fájlból készül. Az ItemType forrása és az OLB property-proveniencia külön adat.', '',
           f'Itemek: {len(items)}. ItemType attribútumból: {explicit}/{len(items)}; heurisztikából: {inferred}/{len(items)}.', '', '| Widget | Darab |', '|---|---:|']
    lines += [f'| {key} | {counts[key]} |' for key in sorted(counts)]
    lines += ['', '### Nem támogatott itemek', '', '| Item | Canvas | ItemType / ok |', '|---|---|---|']
    unsupported=[i for i in model['issues'] if i['code']=='UNSUPPORTED_ITEM']
    lines += [f'| {cell(i["owner"])} | {cell(i.get("canvas",""))} | {cell(i["detail"])} |' for i in unsupported]
    if not unsupported: lines+=['| — | — | Nincs |']
    lines += ['', '### Öröklés', '', f'Feloldott kapcsolatok: {model["inheritance"]["resolved_links"]}.']
    unresolved=model['inheritance']['unresolved']
    if unresolved:
        lines += ['', '#### Feloldatlan öröklés', '',
                  'A feloldatlan öröklés nem állítja meg a generálást. A DUMP=ALL export effektív property-i megmaradnak; '
                  'ami elvész, az a származás igazolása és a csak a szülőobjektumban létező triggertörzs. '
                  'Szigorú ellenőrzéshez: --strict-inheritance.', '',
                  '| Forrás | Ok | Hivatkozás | Item | Trigger | Teendő |', '|---|---|---:|---:|---:|---|']
        for entry in unresolved:
            todo = '`--olb '+cell(entry['required_export'])+'`' if entry['required_export'] else 'nincs megnevezett fájl; kézi ellenőrzés'
            lines.append(f'| {cell(entry["filename"] or "—")} | {cell(", ".join(entry["reasons"]))} | {entry["reference_count"]} '
                         f'| {entry["affected_items"]} | {entry["affected_triggers"]} | {todo} |')
        bodies=[t for t in model['triggers'] if t.get('inherited_unresolved')]
        lines += ['', f'Törzs nélkül maradt örökölt trigger: {len(bodies)}. Ezekre nem generálunk kódot.']
        lines += [f'- {cell(t["id"])} — {cell(t["event"])}' for t in bodies[:20]]
        if len(bodies)>20: lines += [f'- … további {len(bodies)-20} trigger az analysis/ui-model.json fájlban.']
    else:
        lines += ['', 'Feloldatlan öröklés: nincs.']
    lines += ['', '### Endpoint-deklarációk', '', '| Azonosító | Művelet | Útvonal | Állapot |', '|---|---|---|---|']
    lines += [f'| {cell(e["id"])} | {e["method"]} | {cell(e["path"] or "TODO: host szerződés")} | '+('csak deklaráció; nincs LOV backend' if e['declaration_only'] else 'meglévő backend engedélyei szerint')+' |' for e in model['endpoints']]
    lines += ['', '### Frontend issue-k', '', '| Súly | Kód | Hely | Leírás |', '|---|---|---|---|']
    lines += ['| '+' | '.join(cell(i[k]) for k in ['severity','code','owner','detail'])+' |' for i in model['issues']]
    lines += ['', 'Az ismeretlen Optimus adapterek és az üzleti triggerek TODO-integrációs pontok. Nincs kitalált komponensnév vagy automatikusan végrehajtott PL/SQL.', '']
    return '\n'.join(lines)


def integration(model):
    module=model['module']['key']; r=model['rendering']
    return f'''# Angular 22 beépítés — {module}

1. Másold be a frontend/{module}/ könyvtárat a fő alkalmazásba.
2. A komponens translate inputjára add a céges fordítófüggvényt; töltsd be az i18n/{module}.hu.json szótárt. Minden látható forrásszöveg i18n-kulcson szerepel.
3. emit_imports={str(r['emit_imports']).lower()}. Kikapcsolt állapotban a fejlesztő egészítse ki a TS- és @Component imports listákat. Angular: Component, Input, OnChanges, OnDestroy, ChangeDetectorRef (@angular/core), AbstractControl és FormGroup (@angular/forms). Táblázat: a saját Optimus p-table exportja. A tesztben Optimus UI 2.0.2 TableModule-t használunk, a generátor nem feltételez privát importútvonalat. A céges FormBlock típust és ank-form-block implementációját a saját package-ből importáld. A generált típusok/factory-k útvonala a model.ts, form-structure.ts, surfaces.ts és blocks/ fájlokból látható. Nem használunk CUSTOM_ELEMENTS_SCHEMA-t.
4. Automatikus importhoz konfiguráld: emit_imports=true; optimus_import_path; optimus_form_block_symbol; form_block_type_import_path; environment_import_path. Táblázathoz table_import_path és table_symbol is kell. Privát symbol- és package-nevet nem feltételezünk.
5. A blokkok állapota a komponens states mezőjében található. A tábla betöltésekor a model.ts loadOracleRow(spec,row) függvényével alakítsd át a szerver rekordjait; rendelj új tömböt a megfelelő states[block].rows mezőhöz. Az adatbetöltés a host feladata. toOracleRow visszaalakítja a checkboxokat és DATE/NUMBER értékeket.
6. Az action és lookup inputot ellenőrzött host adapterhez kösd. Az actions.ts csak trigger-deklaráció, a LOV útvonala szándékosan null az endpoints.ts fájlban. endpointUrl(environment.baseUrl) alapú URL-képzés áll rendelkezésre az ismert útvonalakhoz. Nincs új LOV backend.
7. A checkbox kötelezősége null/üres értéket tilt; a false (UncheckedValue, pl. 0) érvényes. A generált bindGroup ezt az Angular FormControl szintjén is érvényesíti. Szövegként tárolt NUMBER nem kerül veszteséges JS Number konverzióra.
8. TODO: a tab megjelenítés natív, hozzáférhető gombokra épül; a privát Optimus tabs API ismeretében cserélhető. Image-hez az imageTemplates inputban add meg a BLOCK.ITEM → TemplateRef térképet; a generátor nem talál ki képkomponenst. A gombok/LOV-ok adapter nélkül hibával állnak meg, nem hajtanak végre helyettesítő üzleti logikát.
9. Újragenerálás: --regenerate. A component.ts/.html/.scss és blocks/*.component.ts fájlok CREATE_ONCE, bájtonként megőrzöttek. model, structures, actions, endpoints, surfaces és i18n ALWAYS_REGENERATE. Nézd át a migration-report.md és generated-files.json fájlokat; megváltozott mezők/blokkok után a megőrzött shell kézi illesztése szükséges lehet.

A formblock selector konfigurálható: html_selectors.form_block; a p-table-é: html_selectors.table. A cserekomponensnek ugyanazt az input/output/template szerződést kell biztosítania. A widgettípusok: frm_forms/data/widget-map.json (vagy config widget_map). Saját CSS-osztály nincs; a két tab-layout osztály Tailwind utility.
'''


def append_report(model,output):
    target=output/'migration-report.md'
    existing=target.read_text(encoding='utf-8') if target.exists() else '# Frontend elemzési riport\n\n'
    target.write_text(existing+'\n'+report(model),encoding='utf-8')
    integration_file=output/'INTEGRATION.md'
    legacy=integration_file.read_text(encoding='utf-8') if integration_file.exists() else ''
    # Keep only the existing Java integration guide, not the superseded single-file UI text.
    legacy = legacy.split('## 1. CL / CommonLib', 1)[-1].split('## 4. Angular 22', 1)[0] if legacy else ''
    integration_file.write_text(integration(model)+'\n---\n\n## 1. CL / CommonLib'+legacy,encoding='utf-8')
    issues_file=output/'analysis/issues.json'
    import json
    previous=json.loads(issues_file.read_text(encoding='utf-8')) if issues_file.exists() else []
    write_json(issues_file,previous+[{**issue,'scope':'frontend'} for issue in model['issues']])
    summary_file=output/'analysis/summary.json'
    info=json.loads(summary_file.read_text(encoding='utf-8')) if summary_file.exists() else {}
    info['frontend_review_issues']=sum(i['severity']=='review' for i in model['issues'])
    info['frontend_error_issues']=sum(i['severity']=='error' for i in model['issues'])
    write_json(summary_file,info)


def update_manifest(output):
    from . import __version__
    files=[]
    for file in sorted(output.rglob('*')):
        if file.is_file() and file.name!='generated-files.json':
            rel=file.relative_to(output).as_posix(); raw=file.read_bytes()
            files.append({'path':rel,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'overwrite_policy':overwrite_policy(rel)})
    write_json(output/'generated-files.json',{'generator':'frm-forms-migrator','version':__version__,'ui_model_version':'1.0.0',
        'backend_layout': 'module-service-v2' if (output/'backend').exists() else None, 'files':files})


def write_issue_summary(output):
    """Bounded web preview; full issues.json remains authoritative and downloadable."""
    import json
    issues = json.loads((output / 'analysis/issues.json').read_text(encoding='utf-8'))
    grouped = {}
    for issue in issues:
        key = (issue['code'], issue.get('severity', 'review'), issue.get('scope', 'review'))
        if key not in grouped:
            grouped[key] = {'count': 0, 'example': issue}
        grouped[key]['count'] += 1
    groups = []; examples = []
    for (code, severity, scope), entry in sorted(grouped.items()):
        groups.append({'code': code, 'severity': severity, 'scope': scope, 'count': entry['count']})
        example = entry['example']
        examples.append({'code': code, 'severity': severity, 'scope': scope, 'owner': example.get('owner', '')[:500],
            'detail': f"{entry['count']} tétel. Példa: " + example.get('detail', '')[:1200], 'count': entry['count']})
    write_json(output / 'analysis/issues-summary.json', {'total': len(issues), 'groups': groups[:100],
        'issues': examples[:100], 'preview_only': True, 'full_file': 'analysis/issues.json'})

def input_rejection_report(issues,output):
    """Explicit --analysis-only diagnostic artifact; never a partial UI/source bundle."""
    write_json(output/'analysis/input-issues.json',{'status':'rejected','issues':issues})
    lines=['# Bemenet elutasítva', '', 'Nem készült UI-modell vagy generált forrás. A bemeneti/öröklési hibákat előbb javítani kell.', '',
           '| Kód | OLB / fájl | Érintett itemek | Részlet |','|---|---|---:|---|']
    for issue in issues:
        def cell(v):return str(v).replace('|','\\|').replace('\n',' ')
        lines.append('| '+' | '.join(cell(v) for v in [issue['code'],issue.get('filename',issue.get('file','')),issue.get('affected_items','ismeretlen; lásd references'),issue['detail']])+' |')
    output.mkdir(parents=True,exist_ok=True);(output/'migration-report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')

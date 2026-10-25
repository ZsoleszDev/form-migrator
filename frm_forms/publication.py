"""Stage regeneration first. Never follow a symlink or overwrite a protected shell."""
from pathlib import Path
import json
import shutil
import difflib
from .common import MigrationError
from .angular_ui import overwrite_policy


def preserve(destination,stage):
    manifest=destination/'generated-files.json'
    if not manifest.is_file(): raise MigrationError('REGENERATE: a célban nincs FRM manifest.')
    try: previous=json.loads(manifest.read_text(encoding='utf-8'))
    except ValueError as exc: raise MigrationError('REGENERATE: sérült manifest.') from exc
    if previous.get('generator')!='frm-forms-migrator' or previous.get('ui_model_version')!='1.0.0':
        raise MigrationError('REGENERATE: csak a többfájlos UI-modell 1.0.0 kiadás újragenerálható; korábbi egysoros kiadáshoz válassz új mappát.')
    entries={entry['path'] for entry in previous['files']}
    if (stage/'backend').exists() and any(p.startswith('backend/') for p in entries) and previous.get('backend_layout') != 'module-service-v2':
        raise MigrationError('REGENERATE: a korábbi backend más fájlfelépítést használ. A közvetlen DPS ServiceImpl (module-service-v2) váltáshoz generálj új mappába, és emeld át az egyedi implementációt; a meglévő fájlokat nem írjuk felül.')
    if (stage/'backend').exists() and any(p.startswith('backend/') for p in entries):
        old_company_cl = (destination/'analysis/cl-contract.json').exists()
        new_company_cl = (stage/'analysis/cl-contract.json').exists()
        if old_company_cl != new_company_cl:
            raise MigrationError('REGENERATE: a CL-formátum megváltozott. Generálj új mappába; a megőrzött DPS/WBS fájlok külön illesztést igényelnek.')
        if old_company_cl and new_company_cl:
            old_contract = json.loads((destination/'analysis/cl-contract.json').read_text(encoding='utf-8'))
            new_contract = json.loads((stage/'analysis/cl-contract.json').read_text(encoding='utf-8'))
            if old_contract.get('backend_style') != new_contract.get('backend_style'):
                raise MigrationError('REGENERATE: a DPS/WBS szerződése megváltozott. Generálj új mappába, majd emeld át a kézi ServiceImpl/ControllerImpl módosításokat.')
    changes=[]
    for entry in entries:
        path=Path(entry)
        if path.is_absolute() or '..' in path.parts: raise MigrationError('REGENERATE: nem biztonságos manifest útvonal.')
    for file in sorted(destination.rglob('*')):
        if file.is_symlink(): raise MigrationError('REGENERATE: symlink nem engedélyezett: '+str(file.relative_to(destination)))
        if not file.is_file(): continue
        relative=file.relative_to(destination); rel=relative.as_posix(); target=stage/relative
        if (overwrite_policy(rel)=='create_once'
                or rel.startswith('analysis/backend-assistant/proposals/')
                or rel == 'analysis/backend-assistant/schema-overrides.json'
                or rel == 'analysis/input-bindings.json'
                or rel.startswith('analysis/input-binding-originals/')
                or rel not in entries and rel!='generated-files.json'):
            if target.exists() and rel not in entries: raise MigrationError('REGENERATE: generált fájl ütközik kézi fájllal: '+rel)
            if relative.parts[0]=='backend' and target.exists() and file.read_bytes()!=target.read_bytes():
                # A candidate is documentation, not another compilable backend file.
                candidate=stage/'analysis/backend-regeneration'/relative.with_suffix('.java.txt')
                candidate.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(target,candidate)
                diff=''.join(difflib.unified_diff(file.read_text(encoding='utf-8').splitlines(True),
                    target.read_text(encoding='utf-8').splitlines(True), fromfile='preserved/'+rel, tofile='fresh/'+rel))
                candidate.with_suffix('.patch').write_text(diff,encoding='utf-8')
                changes.append({'preserved':rel,'candidate':candidate.relative_to(stage).as_posix(),
                                'notice':'Manual merge required before build; interfaces/DTOs are regenerated.'})
            if rel == 'analysis/input-bindings.json' and target.exists():
                from .input_bindings import preserve_report, CONFIG
                previous_bindings = json.loads(file.read_text(encoding='utf-8'))
                fresh_bindings = json.loads(target.read_text(encoding='utf-8'))
                merged = preserve_report(previous_bindings, fresh_bindings)
                target.write_text(json.dumps(merged, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
                effective_path = stage/'analysis/effective-config.json'
                effective = json.loads(effective_path.read_text(encoding='utf-8'))
                effective['config'][CONFIG] = {'version': 1, 'bindings': merged['bindings']}
                effective_path.write_text(json.dumps(effective, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            else:
                target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(file,target)
    if changes:
        report=stage/'analysis/backend-regeneration/changes.json'
        report.write_text(json.dumps({'changes':changes},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        plan_file=stage/'analysis/backend-plan.json'
        if plan_file.is_file():
            plan=json.loads(plan_file.read_text(encoding='utf-8'))
            plan['regeneration_status']={
                'manual_merge_required': True,
                'preserved_files': [change['preserved'] for change in changes],
                'changes': report.relative_to(stage).as_posix(),
                'notice': 'Endpoint implemented/runs fields describe fresh generated candidates. '
                          'Preserved implementations may still contain the previous HTTP 501 stub; merge before deployment.'}
            plan_file.write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        with (stage/'INTEGRATION.md').open('a',encoding='utf-8') as guide:
            guide.write('\n## Backend újragenerálás\n\nA ServiceImpl/ControllerImpl fájlok megmaradtak. '
                        'Az új interfészekhez szükséges kézi összefésülés: `analysis/backend-regeneration/changes.json`; '
                        'mellette a friss Java-javaslatok `.java.txt` és az eltérések `.patch` fájlokban.\n')
    # A preserved shell may need changes when schemas evolve. Never silently edit it.


def publish(stage,destination,regenerate=False,archive=None,zip_path=None):
    if destination.is_symlink() or (zip_path is not None and zip_path.is_symlink()):
        raise MigrationError('OUTPUT_SYMLINK: a cél nem lehet symlink.')
    if (destination.exists() or zip_path is not None and zip_path.exists()) and not regenerate:
        raise MigrationError('OUTPUT_EXISTS: a cél már létezik.')
    backup=stage.parent/'previous-output'; zip_backup=stage.parent/'previous.zip'
    had_destination=destination.exists(); had_zip=zip_path is not None and zip_path.exists()
    if had_destination: destination.rename(backup)
    try:
        if had_zip: zip_path.rename(zip_backup)
        stage.rename(destination)
        if archive is not None: archive.rename(zip_path)
    except BaseException:
        if destination.exists(): shutil.rmtree(destination)
        if had_destination: backup.rename(destination)
        if had_zip and zip_backup.exists(): zip_backup.rename(zip_path)
        raise

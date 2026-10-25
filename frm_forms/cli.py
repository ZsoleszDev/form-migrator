from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import zipfile

from . import __version__
from .common import MigrationError, RESERVED, failure_report, name, read_json, run_deep, write_json
from .contracts import COMPANY_DEFAULTS, validate_company_config, validate_common_migrate_tools_package, validate_cl_package
from .exporter import export_fmb
from .generate import generate_angular, generate_java, initial_values
from .inputs import load_inputs, InputIssues
from .inheritance import resolve_inputs
from .java_style import validate_import_order
from .ollama import advise
from .report import reports, summary, manifest
from .rules import analyze, finalize_capabilities
from .xmlmodel import parse_xml
from .presentation import output_name
from .ui_config import UI_DEFAULTS, validate as validate_ui_config
from .ui_model import build_ui_model, require_supported, write_analysis
from .angular_ui import generate as generate_ui
from .ui_inventory import inventory, collect
from .ui_report import append_report, update_manifest, input_rejection_report, write_issue_summary
from .publication import preserve, publish
from .discovery import build_map, write_map
from .action_scaffold import generate_actions, write_frontend_actions
from . import scaffold
from . import angular_screen


DEFAULTS = {"java_package": "hu.company.features", "api_prefix": "/api/forms", "angular_selector_prefix": "app", "export_timeout_seconds": 180,
            "max_ai_calls": 1, "ai_max_source_chars": 1200, "ai_max_prompt_bytes": 3200, "ai_timeout_seconds": 120, "ai_num_ctx": 2048,
            "ai_num_predict": 256, "ai_keep_alive": "2m", "ai_cache_salt": "1",
            # LOV endpoints from RecordGroupQuery (reviewed read-only SQL). false: the
            # earlier policy, no LOV code in the backend, the SQL stays analysis material.
            "backend_lov_endpoints": True,
            # true: generated endpoints are live at once (MODULE_REVIEWED = true, writes allowed unless
            # schema.json says writable:false). The web UI turns it on by default (FRM_BACKEND_LIVE).
            "backend_live": False,
            # "ai" (4.35): no deterministic translation of server-side trigger code. Every trigger gets its method
            # frame (endpoint/hook, DTOs, transaction, log1x) with the original Forms code commented in its body;
            # the Java assistant (local AI) writes that body. "plsql" runs the original PL/SQL in Oracle (4.34),
            # "java" is the earlier Java-first compiler with PL/SQL fallback.
            "backend_trigger_mode": "ai",
            # Query buttons (DEFAULT_WHERE builders): "java" = the SQL request only, in readable Java (query_java);
            # "plsql" = the 4.x adapter that runs the original builder in Oracle and matches precompiled variants.
            "query_action_mode": "java",
            # true: backend/DPS-test/<Module>QueryTextTest.java, a JUnit 5 test of the Java query buttons' WHERE, with the
            # cases of the equivalence check (expected values from the original PL/SQL); copy it to the DPS src/test/java.
            "query_java_tests": False,
            # Company backend conventions: every DPS/WBS ServiceImpl and ControllerImpl method runs in
            # log1x(log, <Module>Constants.<METHOD>_NAME, user, null, () -> ...). Set once per company.
            "java_service_base_dps": "ModuleServiceBase<DpsLogHelper>", "java_service_base_wbs": "ModuleServiceBase<WbsLogHelper>",
            "java_controller_base_dps": "ModuleControllerBase<DpsLogHelper>", "java_controller_base_wbs": "ModuleControllerBase<WbsLogHelper>",
            "java_user_type": "UserDto", "java_user_expression": "getUser()", "java_company_imports": [],
            # CommonMigrateTools (CL): the helper file every module calls; "" = <java_package>.cl.
            "common_migrate_tools_package": "",
            # java-imports.json: {"RestResponseDto": "hu.ff.xy.cl.modules.RestResponseDto", ...}; "" = <migrator>/java-imports.json.
            "java_import_map": "",
            # java-variables.json: the value of a variable the generated Java creates ({"variableName": "ibuKod",
            # "variableValue": "commonService.Details(param)", "autowired": ..., "import": ...}); "" = <migrator>/java-variables.json.
            "java_variable_map": "",
            # The module's CL package (DTOs, Constants, RestClient); {module} = module name. "" = <java_package>.<module>.cl.
            "cl_package": "",
            # The module's DPS / WBS package ({module} allowed); empty: <java_package>.<module>.dps / .wbs. Chosen
            # project folders (project_layout, --layout) set cl_package, dps_package and wbs_package from their paths.
            "dps_package": "",
            "wbs_package": "",
            # true: module Java files without package line (the IDE sets it where the files are copied). The web turns it on.
            "java_empty_package": False,
            # Forms CALL_FORM/OPEN_FORM/NEW_FORM target -> Angular route; default "/<form name in lower case>".
            "form_routes": {},
            # Checkstyle layout of every generated Java file (java_style): braces, blank lines, import
            # groups, UTF-8 literals, 4-space indentation. false: the generator's own layout.
            "java_checkstyle_format": True,
            # Checkstyle CustomImportOrder customImportOrderRules: groups in this order, one empty line between.
            "java_import_order": "STATIC###STANDARD_JAVA_PACKAGE###THIRD_PARTY_PACKAGE",
            # Code page of the attached-library .pld files (--pld); "" = UTF-8, else cp1250 (Windows Forms).
            "pld_encoding": "",
            # Deploy into the project (--project / --layout): the folders of its parts, absolute or relative to the
            # main project folder: {"CL": "C:/p/x-cl", "DPS": ..., "WBS": ..., "frontend": "C:/p/x-ui"}. A Java part
            # is its project folder or its src/main/java, the frontend the Angular project or its screens folder.
            "project_layout": {}}

DEFAULTS.update(COMPANY_DEFAULTS)
DEFAULTS.update(UI_DEFAULTS)


def configuration(args) -> dict:
    import copy
    import os
    result = copy.deepcopy(DEFAULTS)
    # FRM_BACKEND_TRIGGER_MODE=plsql|java: the earlier default for a whole installation (web server, CI);
    # a config file still decides per run.
    if os.getenv("FRM_BACKEND_TRIGGER_MODE"):
        result["backend_trigger_mode"] = os.environ["FRM_BACKEND_TRIGGER_MODE"].strip().lower()
    if args.config:
        values = read_json(args.config)
        unknown = set(values) - set(DEFAULTS) - {"export_command", "library_export_command", "template_dir", "ai_think"}
        if unknown:
            raise MigrationError(f"Ismeretlen config kulcsok: {sorted(unknown)}")
        result.update(values)
        if result.get("template_dir"):
            result["template_dir"] = str((args.config.resolve().parent / result["template_dir"]).resolve())
            if not Path(result["template_dir"]).is_dir():
                raise MigrationError("A template_dir nem létezik.")
    # Batch runs (survey) set a few keys for every form without a config file of their own.
    result.update(getattr(args, "config_overrides", None) or {})
    if args.java_package:
        result["java_package"] = args.java_package
    if getattr(args, "awu_azon", None) is not None:
        result["AWU_AZON"] = args.awu_azon
    if args.max_ai_calls is not None:
        result["max_ai_calls"] = args.max_ai_calls
    package = result["java_package"]
    if not isinstance(package, str) or not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", package) or any(p in RESERVED for p in package.split(".")):
        raise MigrationError("java_package: érvényes, legalább két tagú Java alap package kell.")
    if not isinstance(result["api_prefix"], str) or not re.fullmatch(r"/[A-Za-z0-9/_-]*", result["api_prefix"]):
        raise MigrationError("api_prefix: / kezdetű statikus útvonal szükséges.")
    if not isinstance(result["angular_selector_prefix"], str) or not re.fullmatch(r"[a-z][a-z0-9-]*", result["angular_selector_prefix"]):
        raise MigrationError("Hibás angular_selector_prefix.")
    limits = {"max_ai_calls": (0, 100), "ai_max_source_chars": (100, 8000), "ai_max_prompt_bytes": (500, 16000), "ai_timeout_seconds": (1, 600),
              "export_timeout_seconds": (1, 3600), "ai_num_ctx": (1024, 8192), "ai_num_predict": (64, 2048)}
    for key, (lo, hi) in limits.items():
        if type(result[key]) is not int or not lo <= result[key] <= hi:
            raise MigrationError(f"{key}: {lo}..{hi} közötti egész szám szükséges.")
    type_expression = r"[A-Za-z_][\w.]*(?:<[\w.<>, ]+>)?"
    for key in ("java_service_base_dps", "java_service_base_wbs", "java_controller_base_dps", "java_controller_base_wbs", "java_user_type"):
        if not isinstance(result[key], str) or not re.fullmatch(type_expression, result[key]):
            raise MigrationError(f"{key}: Java típus szükséges (pl. ModuleServiceBase<DpsLogHelper>).")
    if not isinstance(result["java_user_expression"], str) or not re.fullmatch(r"[A-Za-z_][\w.]*(?:\(\))?", result["java_user_expression"]):
        raise MigrationError("java_user_expression: metódushívás vagy mező szükséges (pl. getUser()).")
    result["common_migrate_tools_package"] = validate_common_migrate_tools_package(result["common_migrate_tools_package"])
    if not isinstance(result["java_company_imports"], list) or not all(isinstance(i, str) and re.fullmatch(r"[a-z][\w]*(?:\.[\w]+)+(?:\.\*)?", i) for i in result["java_company_imports"]):
        raise MigrationError("java_company_imports: teljes Java-osztálynevek listája (pl. hu.ceg.common.UserDto).")
    if type(result["backend_live"]) is not bool:
        raise MigrationError("backend_live: true vagy false szükséges.")
    if type(result["backend_lov_endpoints"]) is not bool:
        raise MigrationError("backend_lov_endpoints: true vagy false szükséges.")
    if result["backend_trigger_mode"] not in ("ai", "plsql", "java"):
        raise MigrationError("backend_trigger_mode: ai, plsql vagy java szükséges.")
    if result["query_action_mode"] not in ("java", "plsql"):
        raise MigrationError("query_action_mode: java vagy plsql szükséges.")
    if type(result["query_java_tests"]) is not bool:
        raise MigrationError("query_java_tests: true vagy false szükséges.")
    if result.get("ai_think") not in {None, False, True, "low", "medium", "high"}:
        raise MigrationError("ai_think: null/boolean/low/medium/high szükséges.")
    validate_company_config(result)
    if result.get("widget_map") and args.config:
        result["widget_map"] = str((args.config.resolve().parent / result["widget_map"]).resolve())
    try:
        result["cl_package"] = validate_cl_package(result["cl_package"])
    except ValueError as exc:
        raise MigrationError(str(exc)) from exc
    for key in ("dps_package", "wbs_package"):
        try:
            result[key] = validate_cl_package(result[key])
        except ValueError as exc:
            raise MigrationError(str(exc).replace("cl_package", key)) from exc
    if not isinstance(result["form_routes"], dict) or not all(
            isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in result["form_routes"].items()):
        raise MigrationError('form_routes: {"FORMNEV": "/utvonal"} objektum szükséges.')
    result["form_routes"] = {k.upper(): v.strip() for k, v in result["form_routes"].items()}
    if type(result["java_empty_package"]) is not bool:
        raise MigrationError("java_empty_package: true vagy false szükséges.")
    if type(result["java_checkstyle_format"]) is not bool:
        raise MigrationError("java_checkstyle_format: true vagy false szükséges.")
    result["java_import_order"] = validate_import_order(result["java_import_order"])
    if not isinstance(result.get("java_import_map"), str):
        raise MigrationError("java_import_map: a java-imports.json útvonala szükséges (vagy üres).")
    if result["java_import_map"] and result["java_import_map"].strip() != "-" and args.config:  # "-" = no map
        result["java_import_map"] = str((args.config.resolve().parent / result["java_import_map"]).resolve())
    if not isinstance(result.get("java_variable_map"), str):
        raise MigrationError("java_variable_map: a java-variables.json útvonala szükséges (vagy üres).")
    if result["java_variable_map"] and result["java_variable_map"].strip() != "-" and args.config:  # "-" = no map
        result["java_variable_map"] = str((args.config.resolve().parent / result["java_variable_map"]).resolve())
    if result.get("framework_catalog") and args.config:
        result["framework_catalog"] = str((args.config.resolve().parent / result["framework_catalog"]).resolve())
    if not isinstance(result['screen_overrides'], dict):
        raise MigrationError('SCREEN_OVERRIDES: objektum szükséges.')
    if getattr(args, 'screen_overrides', None):
        if result['screen_overrides'].get('items'):
            raise MigrationError('SCREEN_OVERRIDES: config és --screen-overrides egyszerre nem adhat szabályokat.')
        result['screen_overrides'] = read_json(args.screen_overrides)
    if not isinstance(result['screen_overrides'], dict):
        raise MigrationError('SCREEN_OVERRIDES: objektum szükséges.')
    if getattr(args, 'field_lengths', None):
        if result['screen_field_lengths'].get('fields'):
            raise MigrationError('FIELD_LENGTHS: config és --field-lengths egyszerre nem adhat mezőhosszakat.')
        result['screen_field_lengths'] = read_json(args.field_lengths)
    if result['screen_overrides'].get('items') and not getattr(args, 'screen', False):
        raise MigrationError('SCREEN_OVERRIDES: a felülbírálások csak --screen módban használhatók.')
    validate_ui_config(result, screen_mode=getattr(args, 'screen', False))
    from .project_deploy import common_tools_in_project, layout_packages, parse_layout, validate_layout
    result["project_layout"] = {**validate_layout(result["project_layout"]), **parse_layout(getattr(args, "layout", None))}
    if result["project_layout"]:
        # The module's own folders in the project: their paths give the packages, so the files compile there,
        # and the imports point at the CommonMigrateTools the project already has.
        root = getattr(args, "project", None)
        for layer, package in layout_packages(result["project_layout"], root).items():
            result[layer.lower() + "_package"] = package
        if not result["common_migrate_tools_package"]:
            result["common_migrate_tools_package"] = common_tools_in_project(result["project_layout"], root) or ""
    return result


def package_bundle(bundle, archive_path, folder_name):
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(bundle.rglob("*")):
            if file.is_file():
                info = zipfile.ZipInfo(folder_name + "/" + file.relative_to(bundle).as_posix(), (2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, file.read_bytes())


def migration(args, on_progress=None) -> int:
    from . import java_variables
    try:
        return _migration(args, on_progress)
    finally:
        java_variables.configure({})  # the variable map belongs to this run only


def _migration(args, on_progress=None) -> int:
    from . import java_variables
    emit = on_progress or (lambda phase: None)
    config = configuration(args)
    # java-variables.json: before the analysis, which already writes the Java of the data triggers.
    java_variables.configure(java_variables.load(config))
    source = args.input.resolve()
    if not source.is_file() or source.suffix.lower() not in {".xml", ".fmb"}:
        raise MigrationError("Létező .fmb vagy Forms2XML .xml bemeneti fájl kell.")
    if args.out.is_symlink():
        raise MigrationError("OUTPUT_SYMLINK: a cél nem lehet symlink.")
    destination = args.out.resolve()
    zip_path = destination.with_name(destination.name + ".zip")
    regenerate = getattr(args, "regenerate", False)
    analysis_only = getattr(args, "analysis_only", False)
    scaffold_mode = getattr(args, "scaffold", False)
    screen_mode = getattr(args, "screen", False)
    generation_mode = 'screen' if screen_mode else 'scaffold' if scaffold_mode else 'strict'
    if sum([analysis_only, scaffold_mode, screen_mode]) > 1:
        raise MigrationError("--analysis-only, --scaffold és --screen közül egy választható.")
    if regenerate and destination.exists():
        previous = read_json(destination / 'analysis/summary.json')
        if previous.get('generation_mode', 'strict') != generation_mode:
            raise MigrationError('GENERATION_MODE_CHANGED: generálási mód váltásához új célmappa szükséges.')
    if (destination.exists() or (args.zip and zip_path.exists())) and not regenerate:
        raise MigrationError("A kimenet már létezik. Használj új célmappát; kézi javításokat nem írunk felül.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    metadata = read_json(args.schema) if args.schema else {}
    replacements = read_json(args.rules) if args.rules else {}
    if set(metadata) - {"blocks", "procedures", "tables"} or set(replacements) - {"replacements"}:
        raise MigrationError("schema: kizárólag blocks, procedures és tables; rules: kizárólag replacements gyökérkulcs engedélyezett.")
    with tempfile.TemporaryDirectory(prefix=".frm-stage-", dir=destination.parent) as temp:
        stage = Path(temp)
        bundle = stage / "bundle"
        bundle.mkdir()
        if source.suffix.lower() == ".fmb":
            emit("exporting")
        xml = export_fmb(source, stage / "oracle-export", config) if source.suffix.lower() == ".fmb" else source
        emit("parsing")
        try:
            strict_inheritance = getattr(args, "strict_inheritance", False)
            inputs = load_inputs(xml, getattr(args, "olb", None), getattr(args, "mmb", None), strict_inheritance)
            resolution = resolve_inputs(inputs, strict_inheritance)
        except InputIssues as exc:
            if not analysis_only:
                raise
            if regenerate:
                raise MigrationError("ANALYSIS_ONLY: hibás bemeneti diagnosztikához új célmappát válassz.") from exc
            input_rejection_report(exc.issues, bundle)
            update_manifest(bundle)
            diagnostic_archive = stage / "diagnostics.zip" if args.zip else None
            if diagnostic_archive is not None:
                package_bundle(bundle, diagnostic_archive, destination.name)
            publish(bundle, destination, False, diagnostic_archive, zip_path if args.zip else None)
            print(json.dumps({"status": "rejected", "output": str(destination), "issues": exc.issues}, ensure_ascii=False, indent=2))
            return 3
        # Parse the bytes already validated, using the original name for legacy
        # FormModule.Name fallback. No second read of a potentially changed input.
        # screen_windows: the windows left out vanish before anything is generated (backend included).
        window_scope = None
        if config.get('screen_windows'):
            from .window_scope import prune
            window_scope = prune(resolution.root, config['screen_windows'])
        snapshot = stage / "validated" / xml.name
        snapshot.parent.mkdir()
        snapshot.write_bytes(inputs.form.raw)
        model = parse_xml(snapshot, resolved_root=resolution.root, property_metadata=resolution.metadata)
        # Attached libraries (.pld): the units the form reaches become its program units (libraries.attach).
        from .libraries import attach, load_libraries
        from . import framework
        libraries = load_libraries(getattr(args, "pld", None), config, stage)
        if libraries or model["libraries"]:
            write_json(bundle / "analysis" / "libraries.json", attach(model, libraries, framework.load(config)))
        model["inheritance_summary"] = {"version": 1, "resolved_links": len(resolution.resolved_links),
                                        "form_has_inheritance": resolution.form_has_inheritance,
                                        "details": "analysis/inheritance.json", "catalog": "analysis/olb-catalog.json"}
        if resolution.form_has_inheritance:
            # Resolving XML properties is not approval of inherited business
            # logic. Retain a form-wide backend guard in this frontend phase,
            # including links exported without a ParentModule attribute.
            model["issues"].append({"code": "INHERITANCE_BACKEND_REVIEW", "owner": "@FORM:" + model["name"], "scope": "all",
                                    "detail": "Az XML-öröklés feloldva; az örökölt üzleti működés backend-ellenőrzést igényel. A 2. lépés nem engedélyez automatikusan adatbázis-műveleteket."})
        module = args.module or (name(model['name']) if screen_mode else output_name(model))
        if not re.fullmatch(r"[a-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*", module):
            raise MigrationError("--module: betűvel kezdődő camelCase vagy kötőjeles technikai név szükséges.")
        package = config["java_package"] + "." + name(module).lower()
        emit("analyzing")
        # The framework catalog also classifies triggers: Headstart/Designer plumbing is not backend work.
        model['options'] = {key: config[key] for key in ('backend_live', 'backend_trigger_mode', 'query_action_mode')}
        analyze(model, metadata, replacements, framework.load(config))
        initial_values(model)
        model['generation_mode'] = generation_mode
        if scaffold_mode or screen_mode:
            model['issues'].append({'code': 'SCAFFOLD_REVIEW_REQUIRED', 'owner': '@FORM:' + model['name'], 'scope': 'all',
                                    'detail': 'Migrációs váz: az összes adatbázis-művelet tiltott az ellenőrzött implementációig.'})
        finalize_capabilities(model)
        ui_model = build_ui_model(resolution, config, module, model)
        discovery = build_map([(inputs.form.filename, resolution.root)] + [(d.filename, d.root) for d in inputs.documents[1:]],
                              framework.load(config))
        if not analysis_only and not scaffold_mode and not screen_mode:
            require_supported(ui_model)
        if args.ai == "assist":
            print(f"Ollama-javaslatok: legfeljebb {config['max_ai_calls']} kérés, egyenként {config['ai_timeout_seconds']} s timeout.", file=sys.stderr, flush=True)
        if args.ai != "off":
            emit("ai")
        model["ai"] = advise(model, args.ai, config, args.cache_dir.resolve())
        actions = generate_actions(discovery, bundle, config, module, package, model)
        if not analysis_only:
            if not getattr(args, "frontend_only", False):
                emit("java")
                generate_java(model, bundle, config, module, package, discovery=discovery, actions=actions)
            emit("angular")
            if screen_mode:
                screen_plan = angular_screen.generate(resolution, ui_model, bundle, config, module, discovery)
            elif scaffold_mode:
                scaffold.generate(ui_model, bundle, actions)
            else:
                frontend_root = generate_ui(ui_model, bundle)
                write_frontend_actions(actions, frontend_root)
        write_analysis(ui_model, bundle)
        if window_scope is not None:
            write_json(bundle / "analysis" / "window-scope.json", window_scope)
        write_map(discovery, bundle / 'analysis/discovery')
        if not getattr(args, 'frontend_only', False):
            from .backend_handoff import write_handoff
            write_handoff(model, discovery, bundle, config, module)
        write_json(bundle / "analysis" / "attribute-inventory.json", collect([(d.filename,d.root) for d in inputs.documents], config["property_aliases"]))
        emit("reporting")
        reports(model, bundle, module, package, config)
        from .workbench import write_workbench
        write_workbench(model, bundle, discovery)  # analysis/MUNKAPAD.html: the manual work left, card by card
        append_report(ui_model, bundle)
        if scaffold_mode:
            scaffold.append_review_report(ui_model, bundle)
        if screen_mode:
            angular_screen.append_screen_report(screen_plan, bundle)
        if (bundle / 'CL_INTEGRATION.md').exists():
            with (bundle / 'INTEGRATION.md').open('a', encoding='utf-8') as guide:
                guide.write('\n## Céges CL/DPS/WBS\n\nA backend szerződései egymáshoz illesztettek. '
                            'A privát importok, HTTP-segédek és a frontend válaszburkoló/adapterszerződés bekötése: `CL_INTEGRATION.md`.\n')
        from .runtime_coverage import write_coverage
        write_coverage(model, bundle)
        write_issue_summary(bundle)
        inputs.write(bundle)
        resolution.write(bundle)
        log = stage / "oracle-export" / "export.log"
        if log.exists():
            shutil.copyfile(log, bundle / "analysis" / "export.log")
        write_json(bundle / "analysis" / "effective-config.json", {"config": config, "metadata": metadata, "rules": replacements, "module": module, "package": package})
        if regenerate and destination.exists():
            preserve(destination, bundle)
        if not analysis_only and not getattr(args, 'frontend_only', False):
            from .backend_assistant import prepare_workspace
            prepare_workspace(bundle)  # local task packets; no model/network call
        update_manifest(bundle)
        # Publish only the fully validated/staged result; restore old folder on failure.
        staged_archive = None
        if args.zip:
            emit("packaging")
            staged_archive = stage / "bundle.zip"
            package_bundle(bundle, staged_archive, destination.name)
        publish(bundle, destination, regenerate, staged_archive, zip_path if args.zip else None)
    result = summary(model)
    print(json.dumps({"output": str(destination), "zip": str(zip_path) if args.zip else None, **result}, ensure_ascii=False, indent=2))
    if analysis_only and any(i["severity"] == "error" for i in ui_model["issues"]):
        return 3
    code = 3 if args.strict and (result["blocking_issues"] or any(b["write_blockers"] for b in model["blocks"] if b["database"])) else 0
    if (getattr(args, "project", None) or getattr(args, "layout", None)) and not analysis_only:
        # Straight into the developer's project: CL, DPS, WBS and the frontend in their own folders.
        from .project_deploy import parse_layout, run as deploy_run
        layout = {**config["project_layout"], **parse_layout(getattr(args, "layout", None))}
        code = max(code, deploy_run([destination], args.project, layout, force=getattr(args, "project_force", False)))
    return code


LAYOUT_HELP = ("Egy rész mappája: CL, DPS, WBS (a Java-projekt mappája vagy a src/main/java) vagy frontend (az Angular-projekt "
               "vagy a képernyők mappája); teljes útvonal, vagy a --project mappához képest. Önmagában is telepít.")


def project_options(parser) -> None:
    parser.add_argument("--project", type=Path, help="Generálás után telepítés a projektbe: a fő projektmappa, amelyben a CL, DPS, WBS és frontend projektet a migrátor megkeresi")
    parser.add_argument("--layout", action="append", default=[], metavar="RÉSZ=MAPPA", help="Generálás után telepítés. " + LAYOUT_HELP)
    parser.add_argument("--project-force", action="store_true", help="Régi kapcsoló, nincs hatása: a generált fájl mindig frissül, a CREATE_ONCE fájl soha")


class SinglePath(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest) is not None:
            raise argparse.ArgumentError(self, "csak egyszer adható meg")
        setattr(namespace, self.dest, values)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Oracle Forms FMB/XML → beilleszthető Angular + Java modul. Alapból 0 AI-hívás.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    migrate = commands.add_parser("migrate", help="Modul generálása FMB/XML fájlból")
    migrate.add_argument("input", type=Path)
    migrate.add_argument("--olb", type=Path, action="append", default=[], metavar="LIBRARY_olb.xml",
                         help="Oracle Object Library XML-export; könyvtáranként ismételhető")
    migrate.add_argument("--pld", type=Path, action="append", default=[], metavar="KONYVTAR.pld",
                        help="Csatolt PL/SQL könyvtár szövegesen (.pld; .pll esetén frmcmp alakítja át). Ismételhető.")
    migrate.add_argument("--mmb", type=Path, action=SinglePath, metavar="MENU_mmb.xml",
                         help="Egy Oracle MenuModule XML-export; megőrzéshez, menügenerálás nélkül")
    migrate.add_argument("--out", type=Path, required=True, help="Új, még nem létező modulmappa")
    migrate.add_argument("--regenerate", action="store_true", help="Meglévő 4.x modul frissítése; CREATE_ONCE fájlok megőrzése")
    migrate.add_argument("--analysis-only", action="store_true", help="Csak diagnosztika/UI-modell, generált forrás nélkül")
    migrate.add_argument("--scaffold", action="store_true", help="Részleges migrációs váz; hibák megőrzése, tiltott mezők és ellenőrizendő végpontok")
    migrate.add_argument("--screen", action="store_true", help="Szerkezethű, egyfájlos Angular képernyőváz: FormBlock + Optimus + Tailwind; üzleti működés nélkül")
    migrate.add_argument("--screen-overrides", type=Path, help="Ellenőrzött, forráslenyomathoz kötött képernyő-felülbírálások JSON fájlja")
    migrate.add_argument("--field-lengths", type=Path, help="Mezőhosszak JSON: formControlName -> {min, max} (minta: analysis/field-lengths.template.json)")
    migrate.add_argument("--strict-inheritance", action="store_true",
                         help="Hiányzó OLB esetén elutasítás. Alapból a generálás lefut, és a hiány review issue-ként jelenik meg.")
    migrate.add_argument("--frontend-only", action="store_true", help="Csak frontend + elemzés; nincs Java-generálás")
    migrate.add_argument("--config", type=Path)
    migrate.add_argument("--schema", type=Path, help="Ellenőrzött DB-mapping JSON")
    migrate.add_argument("--rules", type=Path, help="Ellenőrzött, SHA256-alapú PL/SQL-helyettesítések")
    migrate.add_argument("--module", help="Java/API technikai név; --screen módban az Angular komponens neve is ez lesz.")
    migrate.add_argument("--awu-azon", help="WebMenuLeaf AWU_ utáni száma; bekapcsolja a céges CL/DPS/WBS-formátumot.")
    migrate.add_argument("--java-package", help="Alap package, a modulnév automatikusan hozzáadódik")
    migrate.add_argument("--ai", choices=["off", "assist", "cached"], default="off")
    migrate.add_argument("--max-ai-calls", type=int)
    migrate.add_argument("--cache-dir", type=Path, default=Path(".frm-ai-cache"))
    migrate.add_argument("--zip", action="store_true", help="A célmappa mellett modul-ZIP is készül")
    migrate.add_argument("--strict", action="store_true", help="Átültetendő/tiltott műveleteknél 3-as kilépési kód, a kimenet megmarad")
    project_options(migrate)
    assist = commands.add_parser("backend-assist", help="Helyi AI Java-javaslat egy generált modul végpontjához")
    assist.add_argument("output", type=Path, help="A már generált modul mappája")
    assist.add_argument("--list", action="store_true", help="Végpontok listája; nincs AI-hívás")
    assist.add_argument("--task", help="A --list által kiírt feladatazonosító")
    assist.add_argument("--url", default=os.getenv("FRM_OLLAMA_URL", "http://localhost:11434"))
    assist.add_argument("--model", default=os.getenv("FRM_OLLAMA_MODEL", "qwen3-coder:30b"))
    assist.add_argument("--num-ctx", type=int, default=32768)
    assist.add_argument("--num-predict", type=int, default=8192)
    assist.add_argument("--timeout", type=int, default=600)
    assist.add_argument("--attempts", type=int, default=2, help="Legfeljebb ennyi kérés formátumjavítással együtt (1..3)")
    assist.add_argument("--think", choices=["default", "disabled", "low", "medium", "high"], default="default")
    assist.add_argument("--data-access", choices=["jpa", "inline", "jdbc"], default="jpa")
    assist.add_argument("--instructions", default="", help="A kiválasztott végpont konkrét fejlesztési feladata")
    assist.add_argument("--project-context", type=Path, help="UTF-8 szöveg: céges API-minta, ellenőrzött hívott rutinok")
    assist.add_argument("--cached-only", action="store_true", help="Kizárólag korábbi, azonos kontextusú válasz")
    assist.add_argument("--show-prompt", action="store_true",
                        help="Csak kiírja a modellnek küldendő promptot (system + user); nincs AI-hívás")
    apply_java = commands.add_parser("backend-apply", help="Átnézett Java-javaslat átvétele a generált modulba")
    apply_java.add_argument("output", type=Path)
    apply_java.add_argument("--proposal", required=True)
    apply_java.add_argument("--reviewed", action="store_true", help="A diff és feltételezések fejlesztői ellenőrzése megtörtént")
    inspect = commands.add_parser("inventory", help="Az összes XML elem/attribútum leltára")
    inspect.add_argument("inputs", nargs="+", type=Path)
    inspect.add_argument("--out", type=Path, required=True)
    batch = commands.add_parser("batch", help="Sok form generálása egy futással és rangsorolt összesítő riport (PORTFOLIO_HU.md)")
    batch.add_argument("inputs", nargs="*", type=Path, help="Form XML/FMB fájlok vagy mappák (mappában: *.fmb és FormModule XML)")
    batch.add_argument("--out", type=Path, required=True, help="Gyűjtőmappa; formonként egy almappa készül")
    batch.add_argument("--mode", choices=["screen", "scaffold", "strict"], default="screen", help="Generálási mód minden formra (alap: screen)")
    batch.add_argument("--pld", type=Path, action="append", default=[], metavar="KONYVTAR.pld",
                        help="Csatolt PL/SQL könyvtár szövegesen (.pld; .pll esetén frmcmp alakítja át). Ismételhető.")
    batch.add_argument("--olb", type=Path, action="append", default=[], metavar="LIBRARY_olb.xml")
    batch.add_argument("--config", type=Path)
    batch.add_argument("--schema", type=Path, help="Közös ellenőrzött DB-mapping JSON")
    batch.add_argument("--rules", type=Path, help="Közös SHA256-alapú PL/SQL-helyettesítések")
    batch.add_argument("--java-package")
    batch.add_argument("--field-lengths", type=Path, help="Közös mezőhossz JSON (formControlName -> {min, max})")
    batch.add_argument("--regenerate", action="store_true", help="Meglévő almappák frissítése")
    batch.add_argument("--report-only", action="store_true", help="Nem generál: a --out alatti meglévő kimenetekből készít riportot")
    project_options(batch)
    survey = commands.add_parser("survey", help="Felmérés: sok form generálása és megosztható riport arról, mi tiltja a végpontokat (FELMERES_HU.md, felmeres.json)")
    survey.add_argument("inputs", nargs="*", type=Path, help="Form XML/FMB fájlok vagy mappák (mappában: *.fmb és FormModule XML)")
    survey.add_argument("--out", type=Path, required=True, help="Gyűjtőmappa; formonként egy almappa készül")
    survey.add_argument("--pld", type=Path, action="append", default=[], metavar="KONYVTAR.pld",
                        help="Csatolt PL/SQL könyvtár szövegesen (.pld; .pll esetén frmcmp alakítja át). Ismételhető.")
    survey.add_argument("--olb", type=Path, action="append", default=[], metavar="LIBRARY_olb.xml")
    survey.add_argument("--config", type=Path)
    survey.add_argument("--schema", type=Path, help="Közös ellenőrzött DB-mapping JSON")
    survey.add_argument("--rules", type=Path, help="Közös SHA256-alapú PL/SQL-helyettesítések")
    survey.add_argument("--report-only", action="store_true", help="Nem generál: a --out alatti meglévő kimenetekből készít felmérést")
    survey.add_argument("--names", action="store_true", help="Valódi nevekkel (csak helyi használatra, ne oszd meg)")
    survey.set_defaults(mode="screen", java_package=None, field_lengths=None, regenerate=True)
    dsql = commands.add_parser("dictionary-sql", help="Oracle adatszótár-szkript a kimenetekben használt táblákhoz és DB-rutinokhoz")
    dsql.add_argument("outputs", nargs="+", type=Path, help="migrate kimeneti mappák vagy egy batch gyűjtőmappa")
    dsql.add_argument("--out", type=Path, required=True, help="A generált SQL*Plus szkript (pl. dictionary.sql)")
    dsql.add_argument("--spool", default="dictionary.txt", help="A szkript kimeneti fájlja (alap: dictionary.txt)")
    dsql.add_argument("--config", type=Path, help="Saját framework_catalog esetén a config (a keretrendszeri hívások kiszűréséhez)")
    dimp = commands.add_parser("dictionary-import", help="Az adatszótár-export (dictionary.txt) átalakítása schema.json tables/procedures szakaszokká")
    dimp.add_argument("export", type=Path)
    dimp.add_argument("--out", type=Path, required=True, help="A kimeneti schema.json")
    dimp.add_argument("--merge", type=Path, help="Meglévő schema.json: a kézi, ellenőrzött beállításai elsőbbséget kapnak")
    verify = commands.add_parser("verify-db", help="A generált SQL és PL/SQL lefordítása a céladatbázisban (DBMS_SQL.PARSE), végrehajtás nélkül")
    verify.add_argument("outputs", nargs="+", type=Path, help="migrate kimeneti mappák vagy egy batch gyűjtőmappa")
    verify.add_argument("--dsn", required=True, help="Oracle kapcsolat: gep:port/szolgaltatas (python-oracledb thin mód)")
    verify.add_argument("--user", required=True, help="Adatbázis-felhasználó (olvasási jog elég: a PARSE nem futtat)")
    verify.add_argument("--password-env", default="FRM_DB_PASSWORD", help="A jelszót tartalmazó környezeti változó (alap: FRM_DB_PASSWORD)")
    verify.add_argument("--report", type=Path, help="Az összesítő riport (alap: <első mappa>/DB_VERIFY_HU.md)")
    deploy = commands.add_parser("deploy", help="Generált modulok telepítése a projektbe (CL, DPS, WBS, frontend a saját mappájába)")
    deploy.add_argument("outputs", nargs="+", type=Path, help="migrate kimeneti mappák vagy egy batch gyűjtőmappa")
    deploy.add_argument("--project", type=Path, help="A fő projektmappa: a --layout-tal meg nem adott részeket a migrátor itt keresi meg")
    deploy.add_argument("--layout", action="append", default=[], metavar="RÉSZ=MAPPA", help=LAYOUT_HELP)
    deploy.add_argument("--dry-run", action="store_true", help="Csak a terv: mi hova kerülne, írás nélkül")
    deploy.add_argument("--force", action="store_true", help="Régi kapcsoló, nincs hatása: a generált fájl mindig frissül, a CREATE_ONCE fájl soha")
    deploy.add_argument("--config", type=Path, help="Config a project_layout beállítással")
    deploy.add_argument("--report", type=Path, help="A riport (alap: <első kimenet>/PROJECT_DEPLOY_HU.md)")
    args = parser.parse_args(argv)
    try:
        if args.command == "backend-assist":
            from .backend_assistant import Options, prepare_workspace, preview, propose, refresh_archives
            from . import assistant_log
            if args.list:
                result = prepare_workspace(args.output)
            else:
                if not args.task:
                    raise MigrationError("Válassz --task azonosítót a --list listájából.")
                context = args.project_context.read_text(encoding="utf-8-sig") if args.project_context else ""
                options = Options(
                    url=args.url, model=args.model, num_ctx=args.num_ctx, num_predict=args.num_predict,
                    timeout=args.timeout, attempts=args.attempts, think=args.think, data_access=args.data_access,
                    instructions=args.instructions, project_context=context, cached_only=args.cached_only)
                if args.show_prompt:
                    shown = preview(args.output, args.task, options)
                    print("=== SYSTEM ===\n" + shown["system"] + "\n\n=== USER ===\n" + shown["user"])
                    print(f"[{shown['bytes']} byte, ~{shown['estimated_input_tokens']} token; "
                          f"szükséges kontextus {shown['required_tokens']} / {shown['num_ctx']}]", file=sys.stderr)
                    for warning in shown["warnings"]:
                        print("FIGYELEM: " + warning, file=sys.stderr)
                    return 0
                try:
                    result = propose(args.output, args.task, options)
                finally:
                    print("AI-napló (küldött prompt, nyers válaszok): " + str(assistant_log.folder(args.output)), file=sys.stderr)
            refresh_archives(args.output)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "backend-apply":
            from .backend_assistant import apply_proposal, refresh_archives
            result = apply_proposal(args.output, args.proposal, args.reviewed)
            refresh_archives(args.output)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "deploy":
            from .project_deploy import parse_layout, run as deploy_run, validate_layout
            layout = validate_layout(read_json(args.config).get("project_layout") or {}) if args.config else {}
            layout.update(parse_layout(args.layout))
            return deploy_run(args.outputs, args.project, layout, dry_run=args.dry_run, force=args.force, report_path=args.report)
        if args.command == "verify-db":
            from .db_verify import run as run_verify
            return run_verify(args)
        if args.command == "dictionary-sql":
            from .dictionary import run_sql
            return run_sql(args)
        if args.command == "dictionary-import":
            from .dictionary import run_import
            return run_import(args)
        if args.command in ("batch", "survey"):
            if not args.inputs and not args.report_only:
                raise MigrationError("BATCH_INPUT: adj meg legalább egy formot vagy mappát (vagy --report-only).")
            from .portfolio import run_batch
            code = run_deep(run_batch, args, survey=args.command == "survey")
            if getattr(args, "project", None) or getattr(args, "layout", None):
                from .project_deploy import parse_layout, run as deploy_run, validate_layout
                layout = validate_layout(read_json(args.config).get("project_layout") or {}) if args.config else {}
                layout.update(parse_layout(args.layout))
                code = max(code, deploy_run([args.out], args.project, layout, force=args.project_force))
            return code
        if args.command == "inventory":
            inventory(args.inputs, args.out)
            print("Attribútumleltár és kereshető modultérkép: " + str(args.out / 'form-explorer.html'))
            return 0
        return run_deep(migration, args)
    except MigrationError as exc:
        print("HIBA: " + str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Megszakítva.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(failure_report(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Generated files straight into the developer's project: CL, DPS, WBS and the frontend in their own folders.

Every part has a folder of its own, chosen or found:
  - chosen: layout {"CL"|"DPS"|"WBS"|"frontend": folder} (the web UI's "Tallózás…" buttons, project_layout in the
    config, --layout KEY=PATH). Absolute, or relative to the main project folder;
  - found: map_project() looks over the main project folder, when one is given: the Java source roots
    (.../src/main/java) by folder name (…-cl, …-dps, …-wbs), then by the packages and company classes found
    in them; the Angular project (angular.json) and its screens folder.

Where the files go:
  - the module's own folder (exact): a chosen Java folder that is not a project folder or a source root, a chosen
    frontend folder without angular.json. The files go straight into it, no folder is invented. A Java file gets
    the package of the folder (its path below src/main/java, else from its "hu" folder on); the generator writes
    that package when the folders are chosen before the generation (layout_packages), and a deploy into other
    folders rewrites the package lines and the imports between the layers. The frontend files of
    frontend/<module>/ go into the folder without the <module> folder;
  - a project folder or source root (Java), an Angular project, or a found part: Java by its package under the
    source root, the frontend under the screens folder in <module>/.
The shared helpers (CommonMigrateTools.java, wf-table.ts) are never deployed: they are downloaded once
and kept in the project. The deploy looks for them there (helpers in the report: found, version) and points the
imports at the copy it found: the generated code may use any package for CommonMigrateTools and the relative path
'../wf-table', nothing has to be set for them before the generation.

Generated files are overwritten; CREATE_ONCE files (ServiceImpl, ControllerImpl, the component) are written
only when they do not exist yet - the fresh version stays in the output. Only the generated files are written:
no record of the deploy is kept in the project (the .frm-deploy.json of 4.16-4.20 is removed when the migrator
wrote it). Nothing is written outside the part folders and no symlink is followed. dry_run: the plan only.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from .common import MigrationError

LAYERS = ('CL', 'DPS', 'WBS')
PARTS = (*LAYERS, 'frontend')
LAYOUT_KEYS = set(PARTS)
LEGACY_MANIFEST = '.frm-deploy.json'  # 4.16-4.20 kept the deploy's hashes in it: removed now
SKIP_DIRS = {'node_modules', 'target', 'build', 'dist', 'out', 'bin', 'obj', 'coverage', 'tmp', 'temp', 'venv',
             '__pycache__', 'local-data'}
MAX_DEPTH = 8
FRONTEND_TOKENS = {'frontend', 'front', 'fe', 'ui', 'web', 'angular', 'client'}
CONTENT_MARKERS = {'DPS': ('DpsLogHelper',), 'WBS': ('WbsLogHelper', 'WbsServiceBase', 'WbsControllerBase'),
                   'CL': ('DataProviderServiceRestClientBase', 'CommonMigrateTools')}
DOCUMENTS = {'.md', '.txt'}
JAVA_SEGMENT = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*')
PACKAGE_LINE = re.compile(r'^package\s+([\w.]+)\s*;[ \t]*\n?', re.M)
# The shared helpers: downloaded once and kept in the project, never deployed with a module.
COMMON_TOOLS = 'CommonMigrateTools.java'
SCREEN_RUNTIME = 'wf-table.ts'  # the table of the generated screens (4.26; frm-forms-screen.ts before)
HELPERS = {'backend/CL/' + COMMON_TOOLS: COMMON_TOOLS, 'frontend/' + SCREEN_RUNTIME: SCREEN_RUNTIME}
HELPER_VERSION = {COMMON_TOOLS: re.compile(r'\bVERSION\s*=\s*"(\d+)"'),
                  SCREEN_RUNTIME: re.compile(r"\bWF_TABLE_VERSION\s*=\s*['\"](\d+)['\"]")}
RUNTIME_IMPORT = re.compile(r"""(\bfrom\s+['"])((?:\.\.?/)+)wf-table(['"])""")
WRITTEN = {'new', 'updated'}
STATUS_TEXT = {'new': 'új', 'updated': 'frissítve', 'unchanged': 'változatlan', 'kept': 'megőrizve (CREATE_ONCE)',
               'skipped': 'kihagyva'}


def tokens(path: Path) -> set[str]:
    return {t for t in re.split(r'[^a-z0-9]+', path.as_posix().lower()) if t}


def walk(root: Path, depth: int = MAX_DEPTH):
    """(directory, subdirectory names, file names) under root: no hidden, build or dependency folders, no symlinks."""
    limit = depth
    for current, directories, files in os.walk(root, followlinks=False):
        here = Path(current)
        depth = len(here.relative_to(root).parts)
        directories[:] = [] if depth >= limit else sorted(
            d for d in directories if d not in SKIP_DIRS and not d.startswith('.') and not (here / d).is_symlink())
        yield here, directories, files


def is_java_root(path: Path) -> bool:
    return path.parts[-3:] == ('src', 'main', 'java')


def java_source_root(folder: Path) -> Path | None:
    """The source root of a chosen Java project folder (its src, src/main or src/main/java itself); None for any
    other folder: that is the module's own folder."""
    if is_java_root(folder):
        return folder
    for candidate in (folder / 'src' / 'main' / 'java', folder / 'main' / 'java', folder / 'java'):
        if candidate.is_dir() and is_java_root(candidate):
            return candidate
    return None


def enclosing_java_root(folder: Path) -> Path | None:
    return next((p for p in [folder, *folder.parents] if is_java_root(p)), None)


def folder_package(folder: Path) -> str | None:
    """The Java package of a folder: its path below src/main/java, else from its first "hu" folder on."""
    parts = folder.parts
    root = enclosing_java_root(folder)
    rest = parts[len(root.parts):] if root is not None else parts[parts.index('hu'):] if 'hu' in parts else ()
    return '.'.join(rest) if rest and all(JAVA_SEGMENT.fullmatch(part) for part in rest) else None


def required_package(key: str, folder: Path) -> str:
    package = folder_package(folder)
    if package is None:
        raise MigrationError(f'PROJECT_LAYOUT: a(z) {key} mappájából nem állapítható meg a Java-csomag ({folder}); a modul '
                             'mappáját válaszd a src/main/java alatt (például …/src/main/java/hu/ceg/…/dps).')
    return package


def java_home(source_root: Path) -> Path:
    """The Java project folder of a source root (…/rendszer-dps): the report shows the paths from it."""
    return source_root.parents[2] if is_java_root(source_root) else source_root


def angular_screens(angular: Path) -> Path:
    """The folder of the generated screens in one Angular project."""
    try:
        workspace = json.loads((angular / 'angular.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        workspace = {}
    projects = workspace.get('projects') if isinstance(workspace.get('projects'), dict) else {}
    chosen = next((p for p in projects.values() if isinstance(p, dict) and p.get('projectType') == 'application'),
                  next(iter(projects.values()), {}) if projects else {})
    source = angular / (chosen.get('sourceRoot') or str(Path(chosen.get('root') or '') / 'src'))
    for current, _, files in walk(source if source.is_dir() else angular):
        if 'wf-table.ts' in files or 'frm-forms-screen.ts' in files:
            return current  # where the screens of earlier deploys are
    return source / 'app'


def frontend_home(screens: Path) -> Path:
    """The Angular project of a screens folder (the nearest folder above with angular.json), else the folder."""
    for folder in [screens, *screens.parents][:12]:
        if (folder / 'angular.json').is_file():
            return folder
    return screens


def chosen_folder(key: str, value: str, root: Path | None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        if root is None:
            raise MigrationError(f'PROJECT_LAYOUT: a(z) {key} mappát teljes útvonallal add meg (nincs fő projektmappa): {value}')
        path = root / path
    path = path.resolve()
    if path.is_dir():
        return path
    if path.exists():
        raise MigrationError(f'PROJECT_LAYOUT: a(z) {key} útvonal nem mappa: {path}')
    raise MigrationError(f'PROJECT_LAYOUT: a(z) {key} mappa nem létezik: {path}')


def part_folders(key: str, folder: Path) -> tuple[Path, Path, bool]:
    """(target, home, exact) of a chosen folder: the files go into (exact) or under the target; the report shows the
    paths from the home (the Java or Angular project folder)."""
    if key == 'frontend':
        if (folder / 'angular.json').is_file():
            return angular_screens(folder), folder, False
        return folder, frontend_home(folder), True
    source = java_source_root(folder)
    if source is not None:
        return source, java_home(source), False
    enclosing = enclosing_java_root(folder)
    return folder, java_home(enclosing) if enclosing is not None else folder, True


def layout_packages(layout: dict, root=None) -> dict:
    """{layer: package} of the Java parts chosen as the module's own folder: the generator writes these packages
    (cl_package, dps_package, wbs_package), so the files compile where they are deployed."""
    root = Path(root).expanduser().resolve() if root else None
    packages = {}
    for layer in LAYERS:
        value = (layout or {}).get(layer)
        if not value or (root is None and not Path(value).expanduser().is_absolute()):
            continue  # relative to a --project given at the deploy
        folder = chosen_folder(layer, value, root)
        if part_folders(layer, folder)[2]:
            packages[layer] = required_package(layer, folder)
    return packages


def java_roots(folders) -> list[Path]:
    """The source roots (src/main/java) of Java part folders, each once."""
    roots = []
    for folder in folders:
        found = enclosing_java_root(folder) if folder is not None else None
        if found is not None and found not in roots:
            roots.append(found)
    return roots


def find_helper(roots, name: str) -> list[Path]:
    """The copies of a shared helper below the given folders (no build or dependency folders, no symlinks)."""
    found = []
    for root in roots:
        for current, _, files in walk(root, depth=14):
            if name in files and (current / name) not in found:
                found.append(current / name)
    return found


def helper_version(path: Path, name: str) -> str | None:
    try:
        found = HELPER_VERSION[name].search(path.read_text(encoding='utf-8', errors='ignore'))
    except OSError:
        return None
    return found.group(1) if found else None


def common_tools_in_project(layout: dict, root=None) -> str | None:
    """The package of the CommonMigrateTools.java already in the project (in the source roots of the chosen Java
    parts): the generated imports point at it."""
    root = Path(root).expanduser().resolve() if root else None
    folders = []
    for layer in LAYERS:
        value = (layout or {}).get(layer)
        if value and (root is not None or Path(value).expanduser().is_absolute()):
            folders.append(chosen_folder(layer, value, root))
    for path in find_helper(java_roots(folders), COMMON_TOOLS):
        found = PACKAGE_LINE.search(path.read_text(encoding='utf-8', errors='ignore'))
        if found:
            return found.group(1)
    return None


def map_project(root=None, layout: dict | None = None) -> dict:
    """{'root', 'CL', 'DPS', 'WBS', 'frontend' (target roots or None), 'home', 'candidates'} of a project.

    root: the main project folder, the parts not chosen in layout are found in it (optional)."""
    layout = {k: v for k, v in (layout or {}).items() if isinstance(v, str) and v.strip()}
    unknown = set(layout) - LAYOUT_KEYS
    if unknown:
        raise MigrationError('PROJECT_LAYOUT: ismeretlen kulcs: ' + ', '.join(sorted(unknown)) + ' (CL, DPS, WBS, frontend)')
    if root is None or not str(root).strip():
        if not layout:
            raise MigrationError('PROJECT: add meg a fő projektmappát, vagy legalább egy rész (CL, DPS, WBS, frontend) mappáját.')
        root = None
    else:
        root = Path(root).expanduser().resolve()
        if not root.is_dir():
            raise MigrationError('PROJECT: a projektmappa nem létezik: ' + str(root))
    result = {'root': root, 'home': {}, 'exact': {}, 'chosen': {}, 'candidates': {'java': [], 'angular': []}}
    for key in PARTS:
        result[key] = None
    for key, value in layout.items():
        result['chosen'][key] = chosen_folder(key, value, root)
        result[key], result['home'][key], result['exact'][key] = part_folders(key, result['chosen'][key])
    missing = [key for key in PARTS if key not in layout]
    if root is None or not missing:
        return result
    java_roots, angular = [], []
    for current, directories, files in walk(root):
        if is_java_root(current):
            java_roots.append(current)
            directories[:] = []
            continue
        if 'angular.json' in files:
            angular.append(current)
    result['candidates'] = {'java': [p.relative_to(root).as_posix() for p in java_roots],
                            'angular': [p.relative_to(root).as_posix() for p in angular]}
    scores = {path: layer_scores(root, path) for path in java_roots}
    for layer in (layer for layer in LAYERS if layer in missing):
        ranked = sorted(((s[layer], path) for path, s in scores.items() if s[layer] > 0), key=lambda x: -x[0])
        if not ranked:
            continue
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
            raise MigrationError(f'PROJECT_LAYOUT: a {layer} projekt nem egyértelmű ('
                                 + ', '.join(p.relative_to(root).as_posix() for s, p in ranked if s == ranked[0][0])
                                 + f'); válaszd ki a mappáját (--layout {layer}=<mappa>)')
        result[layer], result['home'][layer], result['exact'][layer] = ranked[0][1], java_home(ranked[0][1]), False
    if 'frontend' in missing and angular:
        chosen = angular
        if len(angular) > 1:
            chosen = [a for a in angular if tokens(a.relative_to(root)) & FRONTEND_TOKENS] or angular
        if len(chosen) > 1:
            raise MigrationError('PROJECT_LAYOUT: több Angular-projekt van (' + ', '.join(a.relative_to(root).as_posix()
                                 for a in chosen) + '); válaszd ki a frontend mappáját (--layout frontend=<mappa>)')
        result['frontend'], result['home']['frontend'], result['exact']['frontend'] = angular_screens(chosen[0]), chosen[0], False
    return result


def mapped_folders(mapped: dict) -> list[Path]:
    """Every folder a deploy may write into: the part folders and their homes."""
    return [path for path in [*(mapped[key] for key in PARTS), *mapped['home'].values()] if path is not None]


def layer_scores(root: Path, java_root: Path) -> dict[str, int]:
    """How much a Java source root looks like the CL, DPS or WBS project."""
    project = java_home(java_root)
    names = tokens(project.relative_to(root)) if project != root and project.is_relative_to(root) else set()
    scores = {layer: (10 if layer.lower() in names else 0) for layer in LAYERS}
    packages, seen = {layer: 0 for layer in LAYERS}, 0
    for current, directories, files in walk(java_root):
        name = current.name.lower()
        for layer in LAYERS:
            if name == layer.lower():
                packages[layer] += 1
        for file in files:
            if not file.endswith('.java') or seen >= 400:
                continue
            seen += 1
            try:
                text = (current / file).read_text(encoding='utf-8', errors='ignore')
            except OSError:
                continue
            for layer, markers in CONTENT_MARKERS.items():
                if any(marker in text for marker in markers):
                    scores[layer] += 1
    for layer in LAYERS:
        scores[layer] += min(packages[layer], 5)
    return scores


def output_folders(outputs) -> list[Path]:
    """migrate output folders, or the module folders of a batch folder (each with generated-files.json)."""
    folders = []
    for output in outputs:
        output = Path(output)
        if (output / 'generated-files.json').is_file():
            folders.append(output)
        elif output.is_dir():
            folders += sorted(d for d in output.iterdir() if (d / 'generated-files.json').is_file())
    if not folders:
        raise MigrationError('DEPLOY: nincs generált modul (generated-files.json) a megadott mappákban.')
    return folders


def generated_packages(output: Path) -> dict:
    """The packages the generator used in one output: {'CL', 'DPS', 'WBS', 'tools' (CommonMigrateTools)}."""
    try:
        effective = json.loads((output / 'analysis/effective-config.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise MigrationError('DEPLOY: hiányzik az analysis/effective-config.json: ' + str(output)) from exc
    config, package = effective['config'], effective['package']
    from .compact_backend import common_tools_package
    from .java_imports import layer_package
    return {**{layer: layer_package(config, package, layer) for layer in LAYERS}, 'tools': common_tools_package(config)}


def rewrite_packages(text: str, mapping: dict, tools: tuple | None) -> str:
    """Point the references between the layers at the packages they are deployed to.

    mapping: {generated package: deployed package} of the layers; tools: (generated, found) package of
    CommonMigrateTools, when the project has its own copy. Only package.Class references change."""
    olds = {old for old, new in mapping.items() if old != new} | ({tools[0]} if tools and tools[0] != tools[1] else set())
    if not olds:
        return text

    def replace(match):
        old, name = match.group(1), match.group(2)
        if name == 'CommonMigrateTools':
            new = tools[1] if tools and old == tools[0] else old
        else:
            new = mapping.get(old, old)
        return new + '.' + name
    pattern = r'(?<![\w.$])(' + '|'.join(re.escape(old) for old in sorted(olds, key=len, reverse=True)) + r')\.([A-Z_$][\w$]*|\*)'
    return re.sub(pattern, replace, text)


def with_package(text: str, package: str) -> str:
    """The Java source with this package line (the web generates without one: java_empty_package)."""
    if PACKAGE_LINE.search(text):
        return PACKAGE_LINE.sub(lambda m: 'package ' + package + ';\n', text, count=1)
    return 'package ' + package + ';\n\n' + text


def plan_files(output: Path, mapped: dict, runtime: Path | None, tools: str | None) -> list[dict]:
    """Part, target, content and policy of every deployable file of one output.

    runtime: the wf-table.ts of the project, tools: the package of its CommonMigrateTools (None: not found)."""
    from .angular_ui import overwrite_policy
    manifest = json.loads((output / 'generated-files.json').read_text(encoding='utf-8'))
    generated = None
    planned = []
    for entry in manifest['files']:
        relative = Path(entry['path'])
        if relative.is_absolute() or '..' in relative.parts or relative.suffix in DOCUMENTS:
            continue
        if relative.as_posix() in HELPERS:
            continue  # downloaded once and kept in the project
        source = output / relative
        if not source.is_file() or source.is_symlink():
            continue
        policy = entry.get('overwrite_policy') or overwrite_policy(relative.as_posix())
        item = {'source': source, 'path': relative.as_posix(), 'policy': policy, 'part': None, 'target': None,
                'data': None, 'reason': None}
        if relative.parts[0] == 'backend' and len(relative.parts) == 3 and relative.parts[1] in LAYERS and relative.suffix == '.java':
            layer = item['part'] = relative.parts[1]
            generated = generated or generated_packages(output)
            deployed = {key: (mapped['packages'].get(key) or generated[key]) for key in LAYERS}
            text = source.read_text(encoding='utf-8')
            text = with_package(text, deployed[layer])  # the generator may leave it to the IDE: it is known here
            text = rewrite_packages(text, {generated[key]: deployed[key] for key in LAYERS},
                                    (generated['tools'], tools) if tools else None)
            item['data'] = text.encode('utf-8')
            if mapped.get(layer) is None:
                item['reason'] = f'nincs {layer} mappa (válaszd ki, vagy add meg: --layout {layer}=<mappa>)'
            elif mapped['exact'].get(layer):
                item['target'] = mapped[layer] / relative.name  # the module's own folder: nothing invented
            else:
                item['target'] = mapped[layer].joinpath(*deployed[layer].split('.'), relative.name)
        elif relative.parts[0] == 'frontend' and len(relative.parts) > 1:
            item['part'] = 'frontend'
            if mapped.get('frontend') is None:
                item['reason'] = 'nincs frontend mappa (válaszd ki, vagy add meg: --layout frontend=<mappa>)'
            elif mapped['exact'].get('frontend'):
                # the module's own folder: frontend/<module>/x goes straight into it
                item['target'] = mapped['frontend'].joinpath(*(relative.parts[2:] if len(relative.parts) > 2 else relative.parts[1:]))
            else:
                item['target'] = mapped['frontend'].joinpath(*relative.parts[1:])
            data = source.read_bytes()
            if runtime is not None and item['target'] is not None and relative.suffix == '.ts':
                text = data.decode('utf-8')
                path = os.path.relpath(runtime.with_suffix(''), item['target'].parent).replace(os.sep, '/')
                path = path if path.startswith('.') else './' + path
                data = RUNTIME_IMPORT.sub(lambda m: m.group(1) + path + m.group(3), text).encode('utf-8')
            item['data'] = data
        else:
            continue  # analysis, reports: they stay in the output
        planned.append(item)
    return planned


def helper_report(mapped: dict, outputs: list[Path]) -> tuple[list[dict], Path | None, str | None]:
    """The shared helpers: where the project has them and which version, against the generated one."""
    entries, runtime, tools = [], None, None
    sources = {name: next((o / rel for o in outputs if (o / rel).is_file()), None) for rel, name in HELPERS.items()}
    java = [mapped.get(layer) for layer in LAYERS if mapped.get(layer) is not None]
    searches = {COMMON_TOOLS: java_roots(java) if java else None,
                SCREEN_RUNTIME: [mapped['home']['frontend']] if mapped.get('frontend') is not None else None}
    for rel, name in HELPERS.items():
        source = sources[name]
        if source is None:
            continue
        generated = helper_version(source, name)
        entry = {'name': name, 'source': rel, 'version': generated, 'found': None, 'found_version': None}
        if searches[name] is None:
            entry['status'] = 'unknown'  # the part is not chosen
        else:
            copies = find_helper(searches[name], name)
            if name == SCREEN_RUNTIME and copies:
                target = mapped['frontend']
                copies.sort(key=lambda p: (not target.is_relative_to(p.parent), -len(p.parts)))  # the nearest above first
            if copies:
                entry['found'], entry['found_version'] = str(copies[0]), helper_version(copies[0], name)
                if name == SCREEN_RUNTIME:
                    runtime = copies[0]
                else:
                    package = PACKAGE_LINE.search(copies[0].read_text(encoding='utf-8', errors='ignore'))
                    tools = package.group(1) if package else None
                found, wanted = entry['found_version'], generated
                entry['status'] = ('ok' if found == wanted or not (found and wanted) else
                                   'outdated' if int(found) < int(wanted) else 'newer')
            else:
                entry['status'] = 'missing'
                if name == SCREEN_RUNTIME:  # where the generated import looks for it
                    base = mapped['frontend'].parent if mapped['exact'].get('frontend') else mapped['frontend']
                    entry['expected'] = str(base / SCREEN_RUNTIME)
                elif mapped.get('CL') is not None and enclosing_java_root(mapped['CL']) is not None:
                    package = generated_packages(outputs[0])['tools']  # the package line of the downloaded file
                    entry['expected'] = str(enclosing_java_root(mapped['CL']).joinpath(*package.split('.'), COMMON_TOOLS))
        entries.append(entry)
    return entries, runtime, tools


def legacy_manifests(mapped: dict) -> list[Path]:
    """The .frm-deploy.json files of earlier deploys (4.16: main folder, 4.17-4.20: each part's home) that the
    migrator wrote."""
    found = []
    for folder in dict.fromkeys(p for p in [mapped['root'], *mapped['home'].values()] if p is not None):
        path = folder / LEGACY_MANIFEST
        if not path.is_file() or path.is_symlink():
            continue
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get('generator') == 'frm-forms-migrator' and isinstance(data.get('files'), dict):
            found.append(path)
    return found


def deploy(outputs, root=None, layout: dict | None = None, dry_run: bool = False, force: bool = False,
           mapped: dict | None = None) -> dict:
    """Place the generated files of migrate outputs into the project's part folders; returns the report.

    mapped: the map_project(root, layout) result when the caller already has it. force: kept for the callers of
    4.16-4.20; a generated file is always updated, a CREATE_ONCE file never."""
    mapped = mapped or map_project(root, layout)
    folders = output_folders(outputs)
    exact = [key for key in PARTS if mapped['exact'].get(key)]
    if exact and len(folders) > 1:
        raise MigrationError('DEPLOY: a modul saját mappájába (' + ', '.join(exact) + ') egyszerre csak egy modul '
                             'telepíthető; a többi modult egyenként telepítsd, vagy válaszd a projekt mappáját.')
    mapped['packages'] = {key: required_package(key, mapped[key]) for key in LAYERS if mapped['exact'].get(key)}
    helpers, runtime, tools = helper_report(mapped, folders)
    results, counts = [], {}
    for output in folders:
        for item in plan_files(output, mapped, runtime, tools):
            status, target, display = 'skipped', item['target'], None
            if target is not None:
                base, home = mapped[item['part']], mapped['home'][item['part']]
                inside = target.is_relative_to(base) and target.resolve().is_relative_to(base.resolve())
                links = [p for p in [target, *target.parents] if p != base and p.is_relative_to(base) and p.is_symlink()]
                if not inside or links:
                    status, item['reason'] = 'skipped', 'a cél a rész mappáján kívülre vagy symlinkre mutat'
                else:
                    display = item['part'] + ': ' + target.relative_to(home).as_posix()
                    if not target.exists():
                        status = 'new'
                    elif target.read_bytes() == item['data']:
                        status = 'unchanged'
                    else:
                        status = 'kept' if item['policy'] == 'create_once' else 'updated'
                    if status in WRITTEN and not dry_run:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        temporary = target.with_name(target.name + '.frm-tmp')
                        temporary.write_bytes(item['data'])
                        os.replace(temporary, target)
            counts[status] = counts.get(status, 0) + 1
            results.append({'output': str(output), 'part': item['part'], 'source': item['path'],
                            'target': str(target) if target is not None and display else None, 'display': display,
                            'status': status, 'policy': item['policy'], **({'reason': item['reason']} if item['reason'] else {})})
    removed = legacy_manifests(mapped)
    if not dry_run:
        for path in removed:
            path.unlink()
    return {'project': str(mapped['root']) if mapped['root'] is not None else None, 'dry_run': dry_run, 'force': force,
            'layout': {key: (str(mapped[key]) if mapped[key] is not None else None) for key in PARTS},
            'exact': {key: bool(mapped['exact'].get(key)) for key in PARTS}, 'packages': mapped['packages'],
            'helpers': helpers, 'candidates': mapped['candidates'], 'counts': counts, 'files': results,
            'removed': [str(path) for path in removed]}


def markdown(report: dict) -> str:
    lines = ['# Telepítés a projektbe' + (' – előnézet' if report['dry_run'] else ''), '']
    if report.get('project'):
        lines += [f"Fő projektmappa: `{report['project']}`", '']
    lines += ['| Rész | Mappa |', '|---|---|']
    lines += [f"| {key} | {('`' + value + '`') if value else '— (nincs kiválasztva / nem található)'} |"
              for key, value in report['layout'].items()]
    lines += ['', 'Összesítés: ' + (', '.join(f"{STATUS_TEXT.get(k, k)}: {v}" for k, v in sorted(report['counts'].items())) or 'nincs fájl'),
              '', '| Fájl | Cél | Állapot |', '|---|---|---|']
    lines += [f"| {r['source']} | {r.get('display') or '—'} | {STATUS_TEXT.get(r['status'], r['status'])}"
              + (f" ({r['reason']})" if r.get('reason') else '') + ' |' for r in report['files']]
    if report.get('removed'):
        lines += ['', ('Törlendő' if report['dry_run'] else 'Törölve') + ' (korábbi telepítés nyilvántartása, a migrátor már nem használja): '
                  + ', '.join('`' + path + '`' for path in report['removed'])]
    helper_text = {'ok': 'megvan a projektben', 'outdated': 'régebbi változat van a projektben: töltsd le az újat, és cseréld le',
                   'newer': 'újabb változat van a projektben, mint amit ez a generálás vár',
                   'missing': 'nincs a projektben: töltsd le, és tedd a helyére',
                   'unknown': 'a rész nincs kiválasztva'}
    if report.get('helpers'):
        lines += ['', 'Segédfájlok (nem kerülnek a projektbe; egyszer kell letölteni és a projektben tartani):', '']
        for helper in report['helpers']:
            where = (' – `' + helper['found'] + '`') if helper.get('found') else (' – ide: `' + helper['expected'] + '`') if helper.get('expected') else ''
            lines.append(f"- `{helper['name']}` (változat: {helper.get('version') or '?'}): {helper_text.get(helper['status'], helper['status'])}{where}")
    if any(r['status'] == 'kept' for r in report['files']):
        lines += ['', 'Megőrizve: a CREATE_ONCE fájlok (ServiceImpl, ControllerImpl, képernyőkomponens) a projektben már '
                      'léteztek. A friss változatuk a generált kimenetben van, a változásokat kézzel kell átvezetni.']
    return '\n'.join(lines) + '\n'


def parse_layout(values) -> dict:
    """--layout KEY=PATH options."""
    layout = {}
    for value in values or []:
        key, sep, path = value.partition('=')
        if not sep or key not in LAYOUT_KEYS or not path.strip():
            raise MigrationError('--layout: KULCS=MAPPA alak kell (CL, DPS, WBS vagy frontend): ' + value)
        layout[key] = path.strip()
    return layout


def validate_layout(value) -> dict:
    """The project_layout setting: {"CL"|"DPS"|"WBS"|"frontend": "<folder: absolute, or relative to the project>"}."""
    if not isinstance(value, dict) or not all(k in LAYOUT_KEYS and isinstance(v, str) and v.strip() for k, v in value.items()):
        raise MigrationError('project_layout: {"CL": "...", "DPS": "...", "WBS": "...", "frontend": "..."} objektum kell '
                             '(teljes útvonalak, vagy a fő projektmappához képest).')
    return {k: v.strip() for k, v in value.items()}


def run(outputs, project, layout: dict, dry_run: bool = False, force: bool = False, report_path=None) -> int:
    """CLI: deploy, write PROJECT_DEPLOY_HU.md (+ analysis/project-deploy.json in each output), print the summary.

    Exit code 3 when a file was not written because of a conflict, or skipped for a missing project part."""
    report = deploy(outputs, project, layout, dry_run=dry_run, force=force)
    folders = output_folders(outputs)
    text = markdown(report)
    target = Path(report_path) if report_path else folders[0] / 'PROJECT_DEPLOY_HU.md'
    target.write_text(text, encoding='utf-8')
    for folder in folders:
        own = dict(report, files=[f for f in report['files'] if f['output'] == str(folder)])
        (folder / 'analysis').mkdir(exist_ok=True)
        (folder / 'analysis' / 'project-deploy.json').write_text(json.dumps(own, ensure_ascii=False, indent=2) + '\n',
                                                                 encoding='utf-8')
    print(json.dumps({'project': report['project'], 'dry_run': dry_run, 'layout': report['layout'], 'counts': report['counts'],
                      'report': str(target)}, ensure_ascii=False, indent=2))
    return 3 if report['counts'].get('conflict') or report['counts'].get('skipped') else 0

"""The generated screen as tests read it (4.26: one component file, no shared runtime; the table is wf-table.ts; 4.27: a frame)."""
from pathlib import Path
import re

from frm_forms.service_inline import mask

WF_TABLE = 'wf-table.ts'


def component(out: Path) -> str:
    return next((Path(out) / 'frontend').rglob('*.component.ts')).read_text(encoding='utf-8')


def ts_method(source: str, name: str) -> str | None:
    """The TypeScript method or getter `name` of the source (any visibility), or None."""
    visible = mask(source)
    match = re.search(r'^  (?:private |public |protected )?(?:get )?' + re.escape(name) + r'(?:<[^>]+>)?\([^\n]*\)[^\n]*\{',
                      visible, re.M)
    if not match:
        return None
    start, end, depth = match.start(), match.end(), 1
    while depth:
        depth += {'{': 1, '}': -1}.get(visible[end], 0)
        end += 1
    return source[start:end]


def screen_method(out: Path, name: str) -> str | None:
    """A method of the generated component, for a Node harness class."""
    return ts_method(component(out), name)


def screen_field(out: Path, name: str) -> str | None:
    """A field of the generated component (its declaration up to the closing ';'), for a Node harness class."""
    source = component(out)
    match = re.search(r'^  (?:private |protected |public )?(?:readonly )?' + re.escape(name) + r'\b[^=\n]*=', source, re.M)
    if not match:
        return None
    end = source.index(';\n', match.end())
    return source[match.start():end + 1]


# The company names the generated files use from the host project (TODO imports in the generated code).
COMPANY_NAMES = ('ServiceBase', 'WFF', 'ToastService', 'FormBlocksComponent', 'FormBlock')
STUBS = Path(__file__).with_name('ts_stubs')
# The tsconfig of a new Angular CLI project (ng new): the flags a developer's project compiles the screens with.
ANGULAR_CLI_OPTIONS = {'strict': True, 'noImplicitOverride': True, 'noPropertyAccessFromIndexSignature': True,
                       'noImplicitReturns': True, 'noFallthroughCasesInSwitch': True, 'skipLibCheck': True,
                       'isolatedModules': True, 'experimentalDecorators': True, 'target': 'ES2022', 'module': 'ES2022',
                       'moduleResolution': 'bundler', 'lib': ['ES2022', 'DOM']}


def with_company_imports(frontend: Path, company: str = 'company') -> list[str]:
    """Import the company stand-ins (a sibling of frontend/) into every generated .ts file that uses them."""
    from frm_forms.ts_imports import code
    files = []
    for path in sorted(frontend.rglob('*.ts')):
        depth = len(path.relative_to(frontend).parts)
        text = path.read_text(encoding='utf-8')
        names = [n for n in COMPANY_NAMES if re.search(r'\b' + n + r'\b', code(text))]  # in code, not only in a TODO
        if names:
            text = 'import { ' + ', '.join(names) + " } from '" + '../' * depth + company + "';\n" + text
        path.write_text(text, encoding='utf-8')
        files.append(str(path))
    return files


def ngc_check(out: Path, ngc: str) -> tuple[int, str]:
    """Compile the generated frontend with the Angular compiler (strictTemplates, the Angular CLI tsconfig flags).

    The company classes and the Optimus modules are stand-ins (tests/ts_stubs/angular); elements and properties
    the stand-ins do not declare are allowed (NO_ERRORS_SCHEMA), every template expression is type-checked."""
    import json
    import shutil
    import subprocess
    import tempfile
    home = Path(ngc).resolve().parents[2]  # <install>/node_modules/.bin/ngc: @angular/* resolve from <install>
    work = Path(tempfile.mkdtemp(prefix='frm-ngc-', dir=home))
    try:
        shutil.copytree(Path(out) / 'frontend', work / 'frontend')
        shutil.copy(STUBS / 'angular' / 'company.ts', work / 'company.ts')
        shutil.copytree(STUBS / 'angular' / 'optimus', work / 'optimus')
        files = with_company_imports(work / 'frontend')
        for name in files:
            path = Path(name)
            text = path.read_text(encoding='utf-8')
            if '@Component({' in text:
                text = "import { NO_ERRORS_SCHEMA } from '@angular/core';\n" + text.replace(
                    '@Component({', '@Component({\n  schemas: [NO_ERRORS_SCHEMA],')
                path.write_text(text, encoding='utf-8')
        (work / 'tsconfig.json').write_text(json.dumps({
            'compilerOptions': {**ANGULAR_CLI_OPTIONS, 'outDir': str(work / 'dist'), 'baseUrl': str(work),
                                'paths': {'@openng/optimus-ui/*': ['optimus/*']}},
            'angularCompilerOptions': {'strictTemplates': True, 'strictInjectionParameters': True,
                                       'strictInputAccessModifiers': True},
            'files': files}))
        run = subprocess.run([ngc, '-p', str(work / 'tsconfig.json')], capture_output=True, text=True, timeout=300)
        return run.returncode, (run.stdout + run.stderr).replace(str(work) + '/', '')
    finally:
        shutil.rmtree(work, ignore_errors=True)


def screen_source(out: Path) -> str:
    """The generated screen's code: since 4.26 one component file (no shared runtime)."""
    return component(out)

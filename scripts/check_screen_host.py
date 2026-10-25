"""Generate and strictly compile a screen with an existing Angular project's dependencies.

No substitute FormBlock or dependency installation. Temporary sources/configuration
are removed; existing project files are not rewritten.
"""
import argparse
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from frm_forms.cli import main as migrate
from frm_forms.common import MigrationError, read_json


def check(args):
    host = args.host_project.resolve()
    tsconfig = (host / args.tsconfig).resolve()
    profile = read_json(args.profile)
    if profile.get('emit_imports') is not True:
        raise MigrationError('HOST_PROFILE: emit_imports=true és a tényleges FormBlock-importok szükségesek.')
    if not tsconfig.is_file(): raise MigrationError('HOST_TSCONFIG: nem létező fájl: ' + str(tsconfig))
    node = shutil.which('node')
    if not node: raise MigrationError('HOST_NODE: a node parancs nem érhető el.')
    # Follow Node's normal resolution, including workspace/hoisted dependencies.
    resolver = r'''const {createRequire}=require('node:module');
const {dirname,resolve}=require('node:path');const {readFileSync}=require('node:fs');
const req=createRequire(process.argv[1]);const path=req.resolve('@angular/compiler-cli/package.json');
const pkg=JSON.parse(readFileSync(path,'utf8'));const bin=typeof pkg.bin==='string'?pkg.bin:pkg.bin.ngc;
const ts=req('typescript');const config=ts.getParsedCommandLineOfConfigFile(process.argv[2],{},
  {...ts.sys,onUnRecoverableConfigFileDiagnostic:d=>{throw new Error(ts.flattenDiagnosticMessageText(d.messageText,'\n'));}});
console.log(JSON.stringify({compiler:resolve(dirname(path),bin),version:pkg.version,rootDir:config.options.rootDir}));'''
    probe = subprocess.run([node, '-e', resolver, str(host / 'package.json'), str(tsconfig)], cwd=host,
                           capture_output=True, text=True, timeout=15)
    if probe.returncode:
        raise MigrationError('HOST_COMPILER: a host projekt telepített @angular/compiler-cli csomagja nem található.\n' + probe.stderr)
    installed = json.loads(probe.stdout)
    component_dir = (host / args.component_dir).resolve() if args.component_dir else next(
        (p for p in [host / 'src/app', host / 'src', host] if p.is_dir()))
    if not component_dir.is_dir() or not component_dir.is_relative_to(host):
        raise MigrationError('HOST_COMPONENT_DIR: meglévő, host projekten belüli célmappa szükséges.')
    with tempfile.TemporaryDirectory(prefix='.frm-screen-check-', dir=host) as temp:
        stage = Path(temp)
        command = ['migrate', str(args.source.resolve()), '--screen', '--frontend-only', '--module', 'hostCheck',
                   '--config', str(args.profile.resolve()), '--out', str(stage / 'generated')]
        for path in args.olb: command += ['--olb', str(path.resolve())]
        if args.screen_overrides: command += ['--screen-overrides', str(args.screen_overrides.resolve())]
        with contextlib.redirect_stdout(io.StringIO()): status = migrate(command)
        if status: return status
        generated = list((stage / 'generated/frontend').rglob('*.component.ts'))
        if len(generated) != 1: raise MigrationError('HOST_SOURCE: pontosan egy generált screen komponens szükséges.')
        # Relative imports are interpreted at the intended component location.
        with tempfile.NamedTemporaryFile(prefix='.frm-screen-check-', suffix='.component.ts', dir=component_dir,
                                         delete=False) as stream:
            source = Path(stream.name)
        try:
            source.write_bytes(generated[0].read_bytes())
            config = stage / 'tsconfig.json'
            config.write_text(json.dumps({'extends': str(tsconfig), 'compilerOptions': {
                'strict': True, 'noEmit': True, 'composite': False, 'incremental': True,
                'rootDir': installed.get('rootDir', str(host)),
                'tsBuildInfoFile': str(stage / 'check.tsbuildinfo'), 'outDir': str(stage / 'compiled')},
                'angularCompilerOptions': {'strictTemplates': True, 'strictInjectionParameters': True,
                                           'strictInputAccessModifiers': True},
                'files': [str(source)], 'include': [], 'exclude': [], 'references': []}), encoding='utf-8')
            result = subprocess.run([node, installed['compiler'], '-p', str(config)], cwd=host, timeout=args.timeout)
            if result.returncode: return result.returncode
        finally:
            source.unlink(missing_ok=True)
    print('OK: a generált képernyő lefordult a host projekt importjaival és Angular ' + installed['version'] + ' fordítójával.')
    print('Ez fordítási ellenőrzés; a FormBlock megjelenítését és eseményeit a host alkalmazásban kell kipróbálni.')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host-project', type=Path, required=True)
    parser.add_argument('--tsconfig', type=Path, default=Path('tsconfig.app.json'), help='A host projektből feloldott tsconfig.')
    parser.add_argument('--component-dir', type=Path, help='A komponens tervezett mappája, a host projektből feloldva; relatív importok alapja.')
    parser.add_argument('--profile', type=Path, required=True, help='Generátorprofil a tényleges FormBlock-importokkal.')
    parser.add_argument('--source', type=Path, required=True, help='Forms XML vagy a profil exportparancsával olvasható FMB.')
    parser.add_argument('--olb', type=Path, action='append', default=[])
    parser.add_argument('--screen-overrides', type=Path)
    parser.add_argument('--timeout', type=int, default=120, help='A fordítás időkorlátja másodpercben.')
    args = parser.parse_args(argv)
    if args.timeout <= 0: parser.error('--timeout: pozitív egész szükséges.')
    try:
        return check(args)
    except (MigrationError, OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
        print('HIBA: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__': raise SystemExit(main())

"""Compile scaffold against real Angular and a test FormBlock contract."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from check_generated_angular import ROOT, HOST
from frm_forms.cli import main as migrate


def main():
    web = ROOT/'web-ui'; compiler = web/'node_modules/@angular/compiler-cli/bundles/src/bin/ngc.js'
    if not compiler.is_file(): raise SystemExit('Előbb: cd web-ui && npm ci')
    source = Path(sys.argv[1]).resolve() if len(sys.argv)>1 else ROOT/'examples/review_fmb.xml'
    with tempfile.TemporaryDirectory(prefix='frm-scaffold-check-', dir=web/'node_modules') as temp:
        stage = Path(temp); (stage/'host.ts').write_text(HOST)
        profile = {'emit_imports':True, 'optimus_import_path':'@test/host','optimus_form_block_symbol':'HostFormBlock',
            'form_block_type_import_path':'@test/host','environment_import_path':'@test/host','table_import_path':'@openng/optimus-ui/table','table_symbol':'TableModule'}
        (stage/'config.json').write_text(json.dumps(profile))
        with contextlib.redirect_stdout(io.StringIO()):
            assert migrate(['migrate',str(source),'--scaffold','--out',str(stage/'result'),'--config',str(stage/'config.json')]) == 0
        (stage/'tsconfig.json').write_text(json.dumps({'extends':str(web/'tsconfig.json'), 'compilerOptions': {
            'outDir':str(stage/'compiled'), 'paths':{'@test/host':[str(stage/'host.ts')]}},
            'files':[str(stage/'host.ts'), *map(str, (stage/'result/frontend').rglob('*.ts'))], 'include':[]}))
        subprocess.run(['node',str(compiler),'-p',str(stage/'tsconfig.json')],cwd=web,check=True,timeout=60)
    print('OK: scaffold Angular strict compile; host FormBlock contract stub (private implementation not tested)')


if __name__=='__main__':main()

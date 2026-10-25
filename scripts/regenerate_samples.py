"""Regenerate samples; Java changes require an explicit reviewed-release flag."""
from pathlib import Path
import argparse
import contextlib
import io
import json
import shutil
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from frm_forms.cli import main
SAMPLES=[
 ('customer','customer_fmb.xml',['--schema','examples/schema.json']),
 ('review','review_fmb.xml',[]),
 ('vasarloLekerdezo','vasarlo-lekerdezo_fmb.xml',[]),
 ('multi-input','customer_fmb.xml',['--schema','examples/schema.json','--olb','examples/companions/base_olb.xml','--olb','examples/companions/shared_olb.xml','--mmb','examples/companions/navigation_mmb.xml']),
 ('inherited-checkbox','inheritance/checkbox_fmb.xml',['--olb','examples/inheritance/base_olb.xml','--olb','examples/inheritance/qmsolb65_olb.xml']),
 ('ui-features','ui-features_fmb.xml',[]),
 ('backend-query','backend-query_fmb.xml',['--module','queryDemo']),
]
def main_samples():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--accept-java-changes',action='store_true',help='Accept reviewed generator changes; record the file diff and still require compact layer counts.')
    settings=parser.parse_args()
    changes=[]
    with tempfile.TemporaryDirectory(prefix='frm-samples-',dir=ROOT.parent) as temporary:
        stage=Path(temporary)
        for sample,source,options in SAMPLES:
            out=stage/sample;args=['migrate',str(ROOT/'examples'/source),'--out',str(out),'--ai','off','--zip']
            args.extend(str(ROOT/value) if value.startswith('examples/') else value for value in options)
            with contextlib.redirect_stdout(io.StringIO()): assert main(args)==0, sample
            old=ROOT/'sample-output'/sample
            old_files={p.relative_to(old).as_posix():p.read_bytes() for p in old.rglob('*') if p.is_file()} if old.exists() else {}
            new_files={p.relative_to(out).as_posix():p.read_bytes() for p in out.rglob('*') if p.is_file()}
            java_old={k:v for k,v in old_files.items() if k.startswith('backend/') and k.endswith('.java')}
            java_new={k:v for k,v in new_files.items() if k.startswith('backend/') and k.endswith('.java')}
            for layer,expected in [('CL',4),('DPS',5),('WBS',5)]:
                actual=sum(k.startswith('backend/'+layer+'/') for k in java_new)
                if actual!=expected: raise RuntimeError(f'{sample}: {layer} has {actual} Java files, expected {expected}')
            if java_old and java_old!=java_new and not settings.accept_java_changes:
                raise RuntimeError('Java output changed: '+sample+'. Review the generator change before using --accept-java-changes.')
            changes.append({'sample':sample,'java_files_compared':len(java_old),'java_unchanged':java_old==java_new,
                'java_changes_accepted':bool(java_old and java_old!=java_new and settings.accept_java_changes),
                'added':sorted(set(new_files)-set(old_files)), 'removed':sorted(set(old_files)-set(new_files)),
                'modified':sorted(k for k in set(old_files)&set(new_files) if old_files[k]!=new_files[k])})
        # All runs and Java checks succeeded before replacing the public samples.
        (ROOT/'sample-output').mkdir(exist_ok=True)
        for sample,_,_ in SAMPLES:
            destination=ROOT/'sample-output'/sample
            if destination.exists(): shutil.rmtree(destination)
            shutil.move(str(stage/sample),destination)
            (stage/(sample+'.zip')).replace(ROOT/'sample-output'/(sample+'.zip'))
    (ROOT/'docs').mkdir(exist_ok=True)
    (ROOT/'docs/sample-output-diff.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps([{'sample':x['sample'],'java_files_compared':x['java_files_compared'],'modified':len(x['modified']),'added':len(x['added']),'removed':len(x['removed'])} for x in changes],indent=2))
if __name__=='__main__':main_samples()

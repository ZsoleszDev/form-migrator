"""Verify generated layers against real Spring 6.2 and a loopback HTTP bridge.

First run: python scripts/check_java_http.py --download
Later runs work offline with the cached Maven Central jars. No Oracle DB is used.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import contextlib
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from frm_forms.cli import main as migrate

DEPENDENCIES = [
    ('org.springframework', artifact, '6.2.11') for artifact in
    ('spring-core', 'spring-jcl', 'spring-beans', 'spring-context', 'spring-expression', 'spring-aop', 'spring-web', 'spring-webmvc', 'spring-test', 'spring-jdbc', 'spring-tx')
] + [
    ('com.fasterxml.jackson.core', artifact, '2.19.2') for artifact in ('jackson-core', 'jackson-annotations', 'jackson-databind')
] + [
    ('com.fasterxml.jackson.datatype', 'jackson-datatype-jsr310', '2.19.2'),
    ('jakarta.validation', 'jakarta.validation-api', '3.0.2'),
    ('jakarta.servlet', 'jakarta.servlet-api', '6.0.0'),
    ('io.micrometer', 'micrometer-observation', '1.14.11'),
    ('io.micrometer', 'micrometer-commons', '1.14.11'),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true', help='Download missing verification-only dependencies from Maven Central')
    args = parser.parse_args()
    cache = ROOT / 'build/java-verification-deps'
    cache.mkdir(parents=True, exist_ok=True)

    def artifact(coordinate):
        group, name, version = coordinate
        filename = f'{name}-{version}.jar'
        jar = cache / filename
        checksum = cache / (filename + '.sha256')
        if jar.is_file() and checksum.is_file() and hashlib.sha256(jar.read_bytes()).hexdigest() == checksum.read_text().strip():
            return jar
        if not args.download:
            raise RuntimeError('Missing dependency; first run with --download: ' + filename)
        url = 'https://repo.maven.apache.org/maven2/' + group.replace('.', '/') + f'/{name}/{version}/{filename}'
        with urllib.request.urlopen(url, timeout=30) as response:
            raw = response.read()
        jar.write_bytes(raw)
        checksum.write_text(hashlib.sha256(raw).hexdigest())
        return jar

    with ThreadPoolExecutor(max_workers=6) as executor:
        jars = list(executor.map(artifact, DEPENDENCIES))
    classpath = os.pathsep.join(str(jar) for jar in jars)
    with tempfile.TemporaryDirectory(prefix='frm-spring-check-') as temp:
        folder = Path(temp)
        module = folder / 'customer'
        with contextlib.redirect_stdout(io.StringIO()):
            assert migrate(['migrate', str(ROOT / 'examples/customer_fmb.xml'), '--out', str(module), '--module', 'customer', '--schema', str(ROOT / 'examples/schema.json')]) == 0
            query_module=folder/'query-demo'
            assert migrate(['migrate',str(ROOT/'examples/backend-query_fmb.xml'),'--out',str(query_module),'--module','queryDemo']) == 0
        destinations = {}
        for layer in ('CL', 'DPS', 'WBS'):
            output = folder / (layer + '-classes')
            output.mkdir()
            destinations[layer] = output
            sources = list((module / 'backend' / layer).glob('*.java'))+list((query_module/'backend'/layer).glob('*.java'))
            argfile = folder / (layer + '.args')
            argfile.write_text('\n'.join('"' + str(p).replace('\\', '/') + '"' for p in sources), encoding='utf-8')
            cp = classpath if layer == 'CL' else classpath + os.pathsep + str(destinations['CL'])
            subprocess.run(['java', 'com.sun.tools.javac.Main', '-encoding', 'UTF-8', '--release', '17', '-cp', cp, '-d', str(output), '@' + str(argfile)], check=True, timeout=60)
            print(f'OK: {layer} compiled separately against real dependencies')
        cp = classpath + os.pathsep + os.pathsep.join(str(value) for value in destinations.values())
        source = folder / 'HttpLayersSmoke.java'
        source.write_text((ROOT / 'tests/HttpLayersSmoke.java.txt').read_text(encoding='utf-8'), encoding='utf-8')
        memory = folder / 'MemoryCustomerJdbc.java'
        memory.write_text((ROOT / 'tests/MemoryCustomerJdbc.java.txt').read_text(encoding='utf-8'), encoding='utf-8')
        query_smoke=folder/'QueryHttpSmoke.java'
        query_smoke.write_text((ROOT/'tests/QueryHttpSmoke.java.txt').read_text(encoding='utf-8'),encoding='utf-8')
        subprocess.run(['java', 'com.sun.tools.javac.Main', '-encoding', 'UTF-8', '--release', '17', '-cp', cp, '-d', str(folder), str(source), str(memory),str(query_smoke)], check=True, timeout=60)
        subprocess.run(['java', '-cp', cp + os.pathsep + str(folder), 'HttpLayersSmoke'], check=True, timeout=40)
        subprocess.run(['java','-cp',cp+os.pathsep+str(folder),'QueryHttpSmoke'],check=True,timeout=40)


if __name__ == '__main__': main()

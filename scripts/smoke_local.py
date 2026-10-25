"""Run a real loopback HTTP smoke test; no Oracle, database or AI is called."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, method="GET", data=None, headers=None):
        req = urllib.request.Request(base + path, method=method, data=data, headers=headers or {})
        with opener.open(req, timeout=10) as response:
            return response.status, response.headers, response.read()

    with tempfile.TemporaryDirectory(prefix="frm-http-check-") as temp:
        folder = Path(temp)
        env = dict(os.environ)
        # Do not inherit a developer's Oracle config for this synthetic check.
        env.pop("FRM_SERVER_CONFIG", None)
        for key in ('FRM_CORS_ORIGINS', 'FRM_CORS_HEADERS', 'FRM_CORS_ALLOW_CREDENTIALS'):
            env.pop(key, None)
        with (folder / "server.log").open("wb") as log:
            process = subprocess.Popen([sys.executable, "-m", "frm_forms.web", "--port", str(port), "--data-dir", str(folder / "data")],
                                       cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 15
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("Backend startup failed: " + (folder / "server.log").read_text(errors="replace"))
                    try:
                        status, _, raw = request("/api/health")
                        break
                    except (urllib.error.URLError, TimeoutError):
                        if time.monotonic() > deadline:
                            raise RuntimeError("Backend startup timeout")
                        time.sleep(0.1)
                assert status == 200 and json.loads(raw)["local_only"]
                print("OK: real Python HTTP server on loopback")

                _, _, raw = request("/")
                html = raw.decode("utf-8")
                assert "<frm-studio" in html, "Missing compiled Angular UI; run npm run build in web-ui"
                assets = re.findall(r'(?:src|href)="([^"?#]+\.(?:js|css))"', html)
                assert assets and any(asset.endswith(".js") for asset in assets)
                for asset in assets:
                    assert not asset.startswith(("http:", "https:", "//")), asset
                    asset_status, _, content = request("/" + asset.lstrip("/"))
                    assert asset_status == 200 and len(content) > 0
                print(f"OK: bundled Angular index and {len(assets)} local assets")

                origin = {"Origin": "http://localhost:4200"}
                _, cors, _ = request("/api/jobs", "OPTIONS", headers={**origin,
                    "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type,x-frm-client"})
                assert cors["access-control-allow-origin"] == origin["Origin"]
                assert 'authorization' in cors['access-control-allow-headers'].lower()
                print("OK: localhost:4200 Bearer CORS preflight")
                origin['Authorization'] = 'Bearer synthetic-smoke-token'
                _, _, raw = request('/api/defaults', headers=origin)
                defaults = json.loads(raw)
                assert 'http://localhost:4200' in defaults['cors_origins']
                assert 'authorization' in defaults['cors_headers']
                assert defaults['client_contract']['authorization'] == 'accepted_but_not_validated'

                boundary = "frm-test-" + uuid.uuid4().hex
                parts = []
                for name, filename, content in [
                    ("file", "customer_fmb.xml", (ROOT / "examples/customer_fmb.xml").read_bytes()),
                    ("schema_file", "schema.json", (ROOT / "examples/schema.json").read_bytes()),
                    ("options", None, json.dumps({"module": "customer", "ai_mode": "off"}).encode()),
                ]:
                    disposition = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else "")
                    parts.append(f"--{boundary}\r\nContent-Disposition: {disposition}\r\n\r\n".encode() + content + b"\r\n")
                body = b"".join(parts) + f"--{boundary}--\r\n".encode()
                status, headers, raw = request("/api/jobs", "POST", body, {**origin,
                    "X-Frm-Client": "local-ui", "Content-Type": f"multipart/form-data; boundary={boundary}"})
                assert status == 202
                assert headers["access-control-allow-origin"] == origin["Origin"]
                job_id = json.loads(raw)["id"]
                deadline = time.monotonic() + 20
                while True:
                    _, _, raw = request("/api/jobs/" + job_id)
                    job = json.loads(raw)
                    if job["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                        break
                    assert time.monotonic() < deadline, "Generation timeout"
                    time.sleep(0.1)
                assert job["status"] == "completed", job
                assert job["summary"]["converted_triggers"] == 5, job
                assert job["summary"]["ai"]["attempted_calls"] == 0, job
                for kind in ("all", "frontend", "backend"):
                    status, headers, content = request(f"/api/jobs/{job_id}/download?kind={kind}", headers=origin)
                    assert status == 200 and "ugyfelek" in headers["content-disposition"] and ".zip" in headers["content-disposition"]
                    assert headers["access-control-allow-origin"] == origin["Origin"]
                    with zipfile.ZipFile(io.BytesIO(content)) as archive:
                        assert archive.testzip() is None and archive.namelist()
                        if kind != "all":
                            assert all(name.startswith(kind + "/") for name in archive.namelist())
                    print(f"OK: {kind} ZIP download and CRC ({len(content)} bytes)")
                print("OK: upload -> subprocess generation -> three downloads; 0 AI calls")
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    main()

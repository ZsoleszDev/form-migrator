"""Local browser-test fixture: real API/worker plus a deterministic HTTP Ollama double."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time

import uvicorn
from frm_forms.web.app import create_app
from frm_forms.web.settings import Settings
from test_input_bindings import FORM
from test_backend_assistant import answer
from test_jpa_assistant import implementation

api_port, ollama_port, folder = int(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
folder.mkdir(parents=True, exist_ok=True)
(folder / 'lookup.xml').write_text(FORM, encoding='utf-8')
requests = []


class Ollama(BaseHTTPRequestHandler):
    def do_GET(self):
        data = json.dumps({'models': [{'name': 'browser-qwen:30b'}]} if self.path == '/api/tags' else requests).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(data)

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        requests.append(request)
        if self.path == '/api/generate':
            data = json.dumps({'done': True, 'response': json.dumps({'target': 'backend', 'summary': 'A trigger Java-átírásra vár.', 'steps': ['Add meg a táblaszerkezetet.']})}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(data)
            return
        # Long enough to exercise tab switching, reload and active-run recovery.
        time.sleep(2)
        if len([value for value in requests if 'messages' in value]) == 1:
            response = answer(status='needs_context', body='', summary='A tesztválasz további projektmintát kér.',
                              missing_context=['Add meg a projektminta mezőt.'], test_cases=[])
        else:
            response = implementation()
            response['body'] = response['body'].replace('java.util.List.of(), java.util.List.of()',
                'java.util.List.of("Böngészőteszt"), java.util.List.of()')
            response['summary'] = 'Rögzített böngészőteszt-javaslat; nem üzleti működésigazolás.'
        data = json.dumps({'done': True, 'message': {'content': json.dumps(response)}}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(data)

    def log_message(self, *_):
        pass


mock = ThreadingHTTPServer(('127.0.0.1', ollama_port), Ollama)
threading.Thread(target=mock.serve_forever, daemon=True).start()
settings = Settings(data_dir=folder / 'jobs-data', port=api_port, folder_dialog=False)
uvicorn.run(create_app(settings), host='127.0.0.1', port=api_port, log_level='warning')

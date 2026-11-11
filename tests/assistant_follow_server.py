"""Browser fixture of tests/assistant_follow_browser.cjs: the real API and worker, and an Ollama double that streams
its answer slowly (token by token, like a laptop CPU), so the live view of a running request can be watched."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sys
import threading
import time

import uvicorn
from frm_forms.web.app import create_app
from frm_forms.web.settings import Settings
from test_input_bindings import FORM

api_port, ollama_port, folder = int(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
folder.mkdir(parents=True, exist_ok=True)
(folder / 'lookup.xml').write_text(FORM, encoding='utf-8')
STEP = float(sys.argv[4]) if len(sys.argv) > 4 else 0.35

ANSWER = '''```java
// SERVICE
public ActionResult {method}(UserDto user, ActionRequest request) throws Exception {
    return log1x(log, LookupConstants.ON_CTRL_RUN_NAME, user, null, () -> {
        List<String> messages = new ArrayList<>();
        messages.add("Élő böngészőteszt");
        return new ActionResult(Map.of(), messages, List.of(), Map.of());
    });
}
```
'''


class Ollama(BaseHTTPRequestHandler):
    def do_GET(self):
        data = json.dumps({'models': [{'name': 'browser-qwen:30b'}]}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(data)

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path == '/api/generate':
            data = json.dumps({'done': True, 'response': '{}'}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(data)
            return
        user = request['messages'][1]['content']
        method = re.search(r"Method:\n```java\n[^\n(]*?(\w+)\(", user)
        text = ANSWER.replace('{method}', method.group(1) if method else 'onCtrlRun')
        self.send_response(200); self.send_header('Content-Type', 'application/x-ndjson'); self.end_headers()
        time.sleep(STEP * 3)  # the CPU reads the prompt
        pieces = re.findall(r'\s*\S+', text)
        for piece in pieces:
            self.wfile.write((json.dumps({'done': False, 'message': {'role': 'assistant', 'content': piece}}) + '\n').encode())
            self.wfile.flush()
            time.sleep(STEP)
        self.wfile.write((json.dumps({'done': True, 'done_reason': 'stop', 'prompt_eval_count': 900, 'eval_count': len(pieces),
                                      'message': {'role': 'assistant', 'content': ''}}) + '\n').encode())
        self.wfile.flush()

    def log_message(self, *_):
        pass


mock = ThreadingHTTPServer(('127.0.0.1', ollama_port), Ollama)
threading.Thread(target=mock.serve_forever, daemon=True).start()
settings = Settings(data_dir=folder / 'jobs-data', port=api_port, folder_dialog=False)
uvicorn.run(create_app(settings), host='127.0.0.1', port=api_port, log_level='warning')

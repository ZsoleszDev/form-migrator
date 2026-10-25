"""Real HTTP checks for pasted endpoints, model names and actionable failures."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest

from fastapi.testclient import TestClient

from frm_forms.ollama import base_url
from frm_forms.web.app import create_app
from frm_forms.web.settings import Settings


class OllamaConnectionTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.status = 200
        self.payload = {"models": [{"name": "qwen-test:30b"}, {"name": "local:latest"}]}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.calls.append(("GET", self.path))
                body = json.dumps(owner.payload).encode()
                self.send_response(owner.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.client = TestClient(create_app(Settings(data_dir=Path(self.temporary.name))), base_url="http://localhost")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def check(self, url=None, model="qwen-test:30b"):
        return self.client.post("/api/ollama/check", headers={"X-Frm-Client": "local-ui"},
                                json={"url": url or self.url, "model": model})

    def test_saved_chat_endpoint_uses_server_model_list_without_inference(self):
        response = self.check(self.url + "/api/chat/")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["url"], self.url)
        self.assertEqual(response.json()["chat_endpoint"], self.url + "/api/chat")
        self.assertEqual(self.calls, [("GET", "/api/tags")])
        # Proxy prefixes remain part of the actual request path.
        response = self.check(self.url + "/ollama/api/chat")
        self.assertEqual(response.json()["chat_endpoint"], self.url + "/ollama/api/chat")
        self.assertEqual(self.calls[-1], ("GET", "/ollama/api/tags"))

    def test_missing_model_is_distinguished_from_missing_server(self):
        response = self.check(model="unknown-model")
        self.assertEqual(response.status_code, 422)
        self.assertIn("Ollama elérhető", response.json()["detail"])
        self.assertIn("qwen-test:30b", response.json()["detail"])
        self.assertEqual(self.check(model="local").status_code, 200)

    def test_wrong_http_route_and_non_ollama_json_show_the_requested_url(self):
        self.status = 404
        self.payload = {"error": "route missing"}
        response = self.check()
        self.assertEqual(response.status_code, 422)
        self.assertIn("HTTP 404", response.json()["detail"])
        self.assertIn("GET " + self.url + "/api/tags", response.json()["detail"])
        self.status, self.payload = 200, {"not_ollama": True}
        response = self.check()
        self.assertEqual(response.status_code, 422)
        self.assertIn("nem Ollama modelllistát", response.json()["detail"])

    def test_invalid_address_or_missing_client_header_does_not_contact_server(self):
        for url in ("http://user:password@localhost:11434", "ftp://localhost", "http://localhost:bad"):
            with self.subTest(url=url):
                self.assertEqual(self.check(url).status_code, 422)
        response = self.client.post("/api/ollama/check", json={"url": self.url, "model": "qwen-test:30b"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.calls, [])

    def test_base_url_keeps_port_and_prevents_duplicate_api_paths(self):
        for suffix in ("", "/", "/api", "/api/chat", "/api/generate", "/chat"):
            with self.subTest(suffix=suffix):
                self.assertEqual(base_url(self.url + suffix), self.url)


if __name__ == "__main__":
    unittest.main()

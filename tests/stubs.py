"""Loopback HTTP stubs for OpenAI-compatible, Paperless, Immich, and Inbox Zero APIs.

They implement only the shapes Towpath's adapters use, so tests run with no
network and no real services. They are not evidence that a real service
behaves the same way.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PROBE_HINT = "Return the JSON object"


class Stub:
    def __init__(self, route):
        self.requests: list[dict] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                url = urlparse(self.path)
                body = json.loads(raw) if raw else None
                req = {"method": self.command, "path": url.path, "query": parse_qs(url.query),
                       "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body}
                stub.requests.append(req)
                status, payload, headers = route(req)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class OpenAIStub(Stub):
    def __init__(self, model="stub-model", models=True, json_schema=True, json_object=True, embeddings=True,
                 answers=None, default_answer='{"is_statement": true, "confidence": 0.9}'):
        self.model = model
        self.flags = {"models": models, "json_schema": json_schema, "json_object": json_object,
                      "embeddings": embeddings}
        self.answers = list(answers or [])
        self.default_answer = default_answer
        super().__init__(self.route)

    def chat_requests(self):
        return [r for r in self.requests if r["path"].endswith("/chat/completions")]

    def route(self, req):
        err = (400, {"error": {"message": "unsupported", "type": "invalid_request_error"}}, None)
        if req["path"] == "/v1/models":
            if not self.flags["models"]:
                return 404, {"error": {"message": "not found"}}, None
            return 200, {"object": "list", "data": [{"id": self.model, "object": "model", "created": 0,
                                                     "owned_by": "stub"}]}, None
        if req["path"] == "/v1/embeddings":
            if not self.flags["embeddings"]:
                return 404, {"error": {"message": "not found"}}, None
            inputs = req["body"]["input"]
            return 200, {"object": "list", "model": self.model,
                         "data": [{"object": "embedding", "index": i, "embedding": [0.1, 0.2, 0.3]}
                                  for i in range(len(inputs))],
                         "usage": {"prompt_tokens": 1, "total_tokens": 1}}, None
        if req["path"] == "/v1/chat/completions":
            fmt = (req["body"].get("response_format") or {}).get("type")
            if fmt == "json_schema" and not self.flags["json_schema"]:
                return err
            if fmt == "json_object" and not self.flags["json_object"]:
                return err
            prompt = req["body"]["messages"][-1]["content"]
            if prompt == "Reply with the word ok.":
                content = "ok"
            elif PROBE_HINT in prompt:
                content = '{"ok": true}'
            elif self.answers:
                content = self.answers.pop(0)
            else:
                content = self.default_answer
            return 200, {"id": "cmpl-stub", "object": "chat.completion", "created": 0, "model": self.model + "-served",
                         "choices": [{"index": 0, "finish_reason": "stop",
                                      "message": {"role": "assistant", "content": content}}],
                         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}, None
        return 404, {"error": {"message": "unknown path"}}, None

"""A stub registry that speaks enough of the OCI distribution API to test the release scripts' client."""

import json
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging" / "release"))
from registry import sha256_digest  # noqa: E402


class State:
    def __init__(self):
        self.manifests, self.blobs, self.uploads = {}, {}, {}
        self.mode = "ok"
        self.redirect_saw_auth = []
        self.token_generation = 1
        self.token_requests = 0
        self.expire_on_put = False
        self.reject_bearer = False
        self.require_upload_headers = None
        self.reject_patch = False
        self.upload_methods = []
        self.put_delay = 0
        self.challenge_scope = None
        self.token_scopes = []
        self.blob_put_authorizations = []


def handler_for(state: State, port_holder: list):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, body=b"", headers=None):
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _error(self, status, code):
            self._send(status, json.dumps({"errors": [{"code": code}]}).encode(), {"Content-Type": "application/json"})

        def _authorized(self, name):
            if self.command == "PUT" and "/blobs/uploads/" in self.path:
                state.blob_put_authorizations.append(self.headers.get("Authorization"))
            if self.command == "PUT" and state.expire_on_put:
                state.token_generation += 1
                state.expire_on_put = False
            if not state.reject_bearer and self.headers.get("Authorization", "").startswith(
                    f"Bearer t-{state.token_generation}-"):
                return True
            # Consume a rejected upload body so the client receives the 401 rather than a reset.
            if self.command in {"PATCH", "PUT"}:
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
            realm = f"http://127.0.0.1:{port_holder[0]}/token"
            scope = state.challenge_scope or f"repository:{name}:pull,push"
            self._send(401, b"", {"WWW-Authenticate": f'Bearer realm="{realm}",service="test",'
                                                      f'scope="{scope}"'})
            return False

        def _route(self):
            url = urlparse(self.path)
            if url.path == "/token":
                if self.headers.get("Authorization") != "Basic dXNlcjpzZWNyZXQ=":  # user:secret
                    return self._send(401)
                scope = parse_qs(url.query).get("scope", [""])[0]
                state.token_requests += 1
                state.token_scopes.append(scope)
                return self._send(200, json.dumps({"token": f"t-{state.token_generation}-{scope}"}).encode())
            if url.path.startswith("/storage/"):
                state.redirect_saw_auth.append(self.headers.get("Authorization"))
                if self.headers.get("Authorization"):
                    return self._send(400, b"credentials sent to storage")
                return self._send(200, state.blobs[url.path.split("/")[-1]])
            parts = url.path.split("/")
            if parts[1] != "v2":
                return self._send(404)
            kind_index = next(i for i, p in enumerate(parts) if p in ("manifests", "blobs"))
            name, kind, rest = "/".join(parts[2:kind_index]), parts[kind_index], parts[kind_index + 1:]
            if not self._authorized(name):
                return None
            if state.mode == "deny":
                return self._error(403, "DENIED")
            if state.mode == "broken":
                return self._error(500, "UNKNOWN")
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            if kind == "manifests":
                key = (name, rest[0])
                if self.command == "GET":
                    found = state.manifests.get(key)
                    if found is None:
                        return self._error(404, "MANIFEST_UNKNOWN")
                    return self._send(200, found[1], {"Content-Type": found[0],
                                                      "Docker-Content-Digest": sha256_digest(found[1])})
                if self.command == "PUT":
                    digest = sha256_digest(body)
                    state.manifests[key] = state.manifests[(name, digest)] = (self.headers["Content-Type"], body)
                    return self._send(201, b"", {"Docker-Content-Digest": digest})
            if kind == "blobs" and rest[0] == "uploads":
                if self.command == "POST":
                    upload = str(uuid.uuid4())
                    state.uploads[upload] = b""
                    return self._send(202, b"", {"Location": f"/v2/{name}/blobs/uploads/{upload}?state=opaque%2Btoken"})
                upload = rest[1]
                state.upload_methods.append(self.command)
                if self.command == "PATCH":
                    if state.reject_patch:
                        return self._error(416, "REQUESTED_RANGE_NOT_SATISFIABLE")
                    if state.require_upload_headers == "PATCH" and (
                        self.headers.get("Content-Range") != f"0-{len(body) - 1}"
                        or self.headers.get("Content-Type") != "application/octet-stream"
                    ):
                        return self._error(404, "BLOB_UPLOAD_INVALID")
                    state.uploads[upload] += body
                    return self._send(202, b"", {"Location": f"/v2/{name}/blobs/uploads/{upload}?state=x"})
                if self.command == "PUT":
                    time.sleep(state.put_delay)
                    if state.reject_patch and parse_qs(url.query).get("state") != ["opaque+token"]:
                        return self._error(404, "BLOB_UPLOAD_INVALID")
                    if state.require_upload_headers == "PUT" and (
                        self.headers.get("Content-Type") != "application/octet-stream"
                    ):
                        return self._error(404, "BLOB_UPLOAD_INVALID")
                    digest = parse_qs(url.query)["digest"][0]
                    data = state.uploads.pop(upload) + body
                    if sha256_digest(data) != digest:
                        return self._error(400, "DIGEST_INVALID")
                    state.blobs[digest] = data
                    return self._send(201, b"", {"Docker-Content-Digest": digest})
            if kind == "blobs":
                digest = rest[0]
                if digest not in state.blobs:
                    return self._error(404, "BLOB_UNKNOWN")
                if self.command == "HEAD":
                    return self._send(200, b"", {"Content-Length": str(len(state.blobs[digest]))})
                return self._send(307, b"", {"Location": f"http://127.0.0.1:{port_holder[0]}/storage/{digest}"})
            return self._send(405)

        do_GET = do_PUT = do_POST = do_PATCH = do_HEAD = _route

    return Handler

"""The release workflow's OCI registry client against a small in-process registry (bearer auth, redirects)."""

import hashlib
import json
import socket
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging" / "release"))

from registry import HttpRegistry, RegistryError, image_config, platform_manifest, sha256_digest  # noqa: E402


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


@pytest.fixture
def server():
    state, port = State(), []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(state, port))
    port.append(httpd.server_address[1])
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield state, f"127.0.0.1:{port[0]}/owner/towpath"
    httpd.shutdown()


def test_manifest_round_trip_with_bearer_auth(server):
    state, repo = server
    client = HttpRegistry("user", "secret")
    assert client.get_manifest(repo, "sha-abc") is None
    raw = json.dumps({"schemaVersion": 2, "config": {"digest": "sha256:" + "0" * 64}, "layers": []}).encode()
    digest = client.put_manifest(repo, "sha-abc", raw, "application/vnd.oci.image.manifest.v1+json")
    found = client.get_manifest(repo, "sha-abc")
    assert found.digest == digest == sha256_digest(raw) and found.raw == raw
    assert client.get_manifest(repo, digest).digest == digest


def test_expired_cached_bearer_token_is_refreshed(server):
    state, repo = server
    client = HttpRegistry("user", "secret")
    assert client.get_manifest(repo, "sha-abc") is None
    assert state.token_requests == 1
    state.token_generation += 1
    assert client.get_manifest(repo, "sha-abc") is None
    assert state.token_requests == 2


def test_token_expiry_during_streamed_upload_rewinds_the_payload(server, tmp_path):
    state, repo = server
    client = HttpRegistry("user", "secret")
    payload = tmp_path / "source.tar.xz"
    payload.write_bytes(b"source package\n" * 20_000)
    digest = sha256_digest(payload.read_bytes())
    state.expire_on_put = True
    client.upload_blob(repo, payload, digest, payload.stat().st_size)
    assert state.blobs[digest] == payload.read_bytes()
    assert state.token_scopes == ["repository:owner/towpath:pull", "repository:owner/towpath:pull,push",
                                  "repository:owner/towpath:pull,push"]  # one refresh of the upload token
    assert state.blob_put_authorizations == ["Bearer t-1-repository:owner/towpath:pull,push",
                                             "Bearer t-2-repository:owner/towpath:pull,push"]


def test_bearer_refresh_is_bounded_when_new_tokens_are_also_refused(server):
    state, repo = server
    state.reject_bearer = True
    with pytest.raises(RegistryError, match="HTTP 401"):
        HttpRegistry("user", "secret").get_manifest(repo, "sha-abc")
    assert state.token_requests == 2


def test_bad_credentials_permission_and_server_errors_are_errors_not_absence(server):
    state, repo = server
    with pytest.raises(RegistryError, match="authentication failed") as denied:
        HttpRegistry("user", "wrong").get_manifest(repo, "sha-abc")
    assert denied.value.status == 401
    with pytest.raises(RegistryError):
        HttpRegistry(None, None).get_manifest(repo, "sha-abc")
    state.mode = "deny"
    with pytest.raises(RegistryError, match="403 DENIED") as denied:
        HttpRegistry("user", "secret").get_manifest(repo, "sha-abc")
    assert denied.value.status == 403
    state.mode = "broken"
    with pytest.raises(RegistryError, match="500") as broken:
        HttpRegistry("user", "secret").get_manifest(repo, "sha-abc")
    assert broken.value.status == 500


def test_network_failure_is_an_error():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(RegistryError):
        HttpRegistry("user", "secret", timeout=5).get_manifest(f"127.0.0.1:{port}/owner/towpath", "sha-abc")


def test_streamed_blob_upload_and_redirected_download(server, tmp_path):
    state, repo = server
    client = HttpRegistry("user", "secret")
    payload = tmp_path / "source.tar.xz"
    payload.write_bytes(b"x" * 300_000)
    digest = "sha256:" + hashlib.sha256(payload.read_bytes()).hexdigest()
    assert not client.blob_exists(repo, digest)
    client.upload_blob(repo, payload, digest, payload.stat().st_size)
    assert client.blob_exists(repo, digest)
    assert client.get_blob(repo, digest) == payload.read_bytes()
    assert state.redirect_saw_auth == [None]  # storage redirects are followed without registry credentials
    uploads_before = len(state.blobs)
    client.upload_blob(repo, payload, digest, payload.stat().st_size)  # already present: skipped
    assert len(state.blobs) == uploads_before


def test_blob_upload_sends_binary_content_type(server, tmp_path):
    state, repo = server
    state.require_upload_headers = "PUT"
    payload = tmp_path / "source.tar.xz"
    payload.write_bytes(b"synthetic source archive\n" * 100)
    digest = sha256_digest(payload.read_bytes())
    client = HttpRegistry("user", "secret")
    client.upload_blob(repo, payload, digest, payload.stat().st_size)
    assert client.get_blob(repo, digest) == payload.read_bytes()


@pytest.mark.parametrize("payload", [b"", b"synthetic source archive\n" * 10000], ids=["empty", "source-archive"])
def test_monolithic_upload_avoids_patch_range_failures_and_preserves_location(server, payload):
    state, repo = server
    state.reject_patch = True
    state.require_upload_headers = "PUT"
    digest = sha256_digest(payload)
    client = HttpRegistry("user", "secret")
    client.upload_blob(repo, payload, digest, len(payload))
    assert state.blobs[digest] == payload
    assert state.upload_methods == ["PUT"]


def test_blob_transfer_outlives_the_short_metadata_request_timeout(server):
    state, repo = server
    state.put_delay = 0.6
    payload = b"synthetic source\n" * 1000
    digest = sha256_digest(payload)
    HttpRegistry("user", "secret", timeout=0.25).upload_blob(repo, payload, digest, len(payload))
    assert state.blobs[digest] == payload


def test_upload_uses_requested_scope_when_challenge_advertises_only_pull(server):
    state, repo = server
    state.challenge_scope = "repository:owner/towpath:pull"
    payload = b"synthetic source archive"
    digest = sha256_digest(payload)
    HttpRegistry("user", "secret").upload_blob(repo, payload, digest, len(payload))
    assert state.blobs[digest] == payload
    assert state.token_scopes == ["repository:owner/towpath:pull", "repository:owner/towpath:pull,push"]
    # A large body can stall before receiving an early 401. Authenticate its first attempt.
    assert state.blob_put_authorizations == ["Bearer t-1-repository:owner/towpath:pull,push"]


def test_index_resolves_to_the_linux_amd64_image(server):
    state, repo = server
    client = HttpRegistry("user", "secret")
    config = json.dumps({"config": {"Labels": {"org.opencontainers.image.revision": "r"}}}).encode()
    config_digest = sha256_digest(config)
    client.upload_blob(repo, config, config_digest, len(config))
    image = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                        "config": {"digest": config_digest, "size": len(config)}, "layers": []}).encode()
    image_digest = client.put_manifest(repo, "img", image,
                                       "application/vnd.oci.image.manifest.v1+json")
    index = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "manifests": [
        {"digest": image_digest, "platform": {"os": "linux", "architecture": "amd64"}},
        {"digest": "sha256:" + "f" * 64, "platform": {"os": "unknown", "architecture": "unknown"}}]}).encode()
    client.put_manifest(repo, "sha-r", index, "application/vnd.oci.image.index.v1+json")
    found = client.get_manifest(repo, "sha-r")
    assert found.is_index and platform_manifest(client, repo, found).digest == image_digest
    assert image_config(client, repo, found) == (config_digest, json.loads(config))

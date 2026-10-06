"""A small OCI distribution client (standard library only) for the release workflow.

It reads and writes manifests and blobs by tag or digest, so a release alias can point at the
exact bytes of an already published manifest (same digest, no rebuild). "Not found" is the only
answer treated as absence; authentication, permission, and network failures are errors, so a
publish step never mistakes "could not check" for "free to write".
"""

import base64
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

MANIFEST_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)
INDEX_TYPES = MANIFEST_TYPES[0], MANIFEST_TYPES[2]
NOT_FOUND_CODES = {"MANIFEST_UNKNOWN", "NAME_UNKNOWN", "BLOB_UNKNOWN", "NOT_FOUND"}


class RegistryError(RuntimeError):
    """The registry could not answer (authentication, permission, network, or server error)."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Manifest:
    digest: str
    media_type: str
    raw: bytes

    @property
    def body(self) -> dict:
        return json.loads(self.raw)

    @property
    def is_index(self) -> bool:
        return self.media_type in INDEX_TYPES or "manifests" in self.body


def sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def split_reference(reference: str) -> tuple[str, str]:
    """``ghcr.io/owner/name`` -> (``ghcr.io``, ``owner/name``)."""
    host, _, name = reference.partition("/")
    if not name or "." not in host and ":" not in host and host != "localhost":
        raise ValueError(f"not a registry repository: {reference!r}")
    return host, name


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class HttpRegistry:
    """OCI distribution API over HTTPS (plain HTTP only for localhost, as used in rehearsals)."""

    def __init__(self, username: str | None = None, password: str | None = None, timeout: float = 60):
        self.username, self.password, self.timeout = username, password, timeout
        self._tokens: dict[tuple[str, str], str] = {}
        self._opener = urllib.request.build_opener(_NoRedirect)

    # -- transport ---------------------------------------------------------------------------

    @staticmethod
    def _base(host: str) -> str:
        local = host.split(":")[0] in {"localhost", "127.0.0.1"}
        return f"{'http' if local else 'https'}://{host}"

    def _token(self, challenge: str, host: str, scope_hint: str, *, refresh: bool = False) -> str:
        params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        realm = params.pop("realm", None)
        if not realm:
            raise RegistryError(f"{host}: unusable authentication challenge")
        # GHCR can advertise only pull even on an upload POST. Request the caller's
        # operation scope so the POST token is cached under the key used by its PUT.
        # Otherwise a large PUT starts without auth and may stall before reading 401.
        if scope_hint:
            params["scope"] = scope_hint
        else:
            params.setdefault("scope", "")
        key = (host, params.get("scope", ""))
        if refresh:
            self._tokens.pop(key, None)
        if key in self._tokens:
            return self._tokens[key]
        request = urllib.request.Request(f"{realm}?{urllib.parse.urlencode(params)}")
        if self.username and self.password:
            basic = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            request.add_header("Authorization", f"Basic {basic}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RegistryError(f"{host}: authentication failed ({exc.code})", status=exc.code) from None
        except (urllib.error.URLError, OSError) as exc:
            raise RegistryError(f"{host}: authentication service unreachable ({exc})") from None
        token = data.get("token") or data.get("access_token")
        if not token:
            raise RegistryError(f"{host}: authentication returned no token")
        self._tokens[key] = token
        return token

    def _request(self, method: str, host: str, path: str, *, scope: str, headers: dict | None = None,
                 data=None, url: str | None = None, auth: str | None = None, timeout: float | None = None):
        """Return (status, headers, body). Raises RegistryError for transport and auth failures."""
        # Allow the initial challenge, then one refresh if the cached bearer token was refused.
        # A persistently refused replacement still fails; uploads rewind before either retry.
        for attempt in (1, 2, 3):
            request = urllib.request.Request(url or self._base(host) + path, method=method, data=data,
                                             headers=dict(headers or {}))
            if auth:
                request.add_header("Authorization", auth)
            try:
                with self._opener.open(request, timeout=self.timeout if timeout is None else timeout) as response:
                    return response.status, response.headers, response.read()
            except urllib.error.HTTPError as exc:
                body = exc.read()
                if exc.code == 401 and attempt < 3:
                    challenge = exc.headers.get("WWW-Authenticate", "")
                    if challenge.lower().startswith("bearer"):
                        auth = f"Bearer {self._token(challenge, host, scope, refresh=bool(auth))}"
                    elif challenge.lower().startswith("basic") and self.username and self.password:
                        auth = "Basic " + base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
                    else:
                        raise RegistryError(f"{host}{path}: authentication required", status=401) from None
                    if hasattr(data, "seek"):
                        data.seek(0)
                    continue
                return exc.code, exc.headers, body
            except (urllib.error.URLError, OSError) as exc:
                raise RegistryError(f"{host}{path}: {getattr(exc, 'reason', exc)}") from None
        raise RegistryError(f"{host}{path}: authentication was refused", status=401)

    @staticmethod
    def _error(host: str, path: str, status: int, body: bytes) -> RegistryError:
        try:
            codes = [e.get("code") for e in json.loads(body).get("errors", [])]
        except ValueError:
            codes = []
        return RegistryError(f"{host}{path}: HTTP {status} {' '.join(c for c in codes if c)}".strip(), status=status)

    @staticmethod
    def _not_found(status: int, body: bytes) -> bool:
        if status != 404:
            return False
        try:
            codes = {e.get("code") for e in json.loads(body).get("errors", [])}
        except ValueError:
            return True  # a bare 404 with no error document
        return not codes or bool(codes & NOT_FOUND_CODES)

    # -- manifests ---------------------------------------------------------------------------

    def get_manifest(self, repository: str, reference: str) -> Manifest | None:
        host, name = split_reference(repository)
        path = f"/v2/{name}/manifests/{reference}"
        status, headers, body = self._request("GET", host, path, scope=f"repository:{name}:pull",
                                              headers={"Accept": ", ".join(MANIFEST_TYPES)})
        if self._not_found(status, body):
            return None
        if status != 200:
            raise self._error(host, path, status, body)
        digest = sha256_digest(body)
        stated = headers.get("Docker-Content-Digest")
        if stated and stated != digest:
            raise RegistryError(f"{host}{path}: manifest digest {stated} does not match its content {digest}")
        if reference.startswith("sha256:") and reference != digest:
            raise RegistryError(f"{host}{path}: content does not match the requested digest")
        media_type = (headers.get("Content-Type") or json.loads(body).get("mediaType") or "").split(";")[0]
        return Manifest(digest, media_type, body)

    def put_manifest(self, repository: str, reference: str, raw: bytes, media_type: str) -> str:
        host, name = split_reference(repository)
        path = f"/v2/{name}/manifests/{reference}"
        status, headers, body = self._request("PUT", host, path, scope=f"repository:{name}:pull,push",
                                              headers={"Content-Type": media_type}, data=raw)
        if status not in (200, 201):
            raise self._error(host, path, status, body)
        digest = sha256_digest(raw)
        stated = headers.get("Docker-Content-Digest")
        if stated and stated != digest:
            raise RegistryError(f"{host}{path}: registry stored {stated}, expected {digest}")
        return digest

    # -- blobs -------------------------------------------------------------------------------

    def blob_exists(self, repository: str, digest: str) -> bool:
        host, name = split_reference(repository)
        path = f"/v2/{name}/blobs/{digest}"
        status, _, body = self._request("HEAD", host, path, scope=f"repository:{name}:pull")
        if status in (200, 307):
            return True
        if status == 404:
            return False
        raise self._error(host, path, status, body)

    def get_blob(self, repository: str, digest: str) -> bytes:
        host, name = split_reference(repository)
        path = f"/v2/{name}/blobs/{digest}"
        status, headers, body = self._request("GET", host, path, scope=f"repository:{name}:pull")
        if status in (301, 302, 303, 307, 308):  # storage redirect: follow without registry credentials
            status, headers, body = self._request("GET", host, path, scope="", url=headers["Location"])
        if status != 200:
            raise self._error(host, path, status, body)
        if sha256_digest(body) != digest:
            raise RegistryError(f"{host}{path}: blob content does not match its digest")
        return body

    def upload_blob(self, repository: str, path_or_bytes, digest: str, size: int) -> None:
        """Stream a whole blob with OCI POST-then-PUT; preserve the opaque upload URL.

        There is no PATCH range/offset state to recover. Authentication retries rewind
        the stream; other failures stop publication and a later run starts a new session.
        """
        if self.blob_exists(repository, digest):
            return
        host, name = split_reference(repository)
        scope = f"repository:{name}:pull,push"
        start = f"/v2/{name}/blobs/uploads/"
        status, headers, body = self._request("POST", host, start, scope=scope, headers={"Content-Length": "0"},
                                              data=b"")
        if status != 202:
            raise self._error(host, start, status, body)
        location = urllib.parse.urljoin(self._base(host) + start, headers["Location"])
        auth = self._auth_for(host, scope)
        handle = open(path_or_bytes, "rb") if not isinstance(path_or_bytes, bytes) else None
        try:
            payload = handle if handle is not None else path_or_bytes
            joiner = "&" if "?" in location else "?"
            try:
                status, headers, body = self._request(
                    "PUT", host, start, scope=scope, url=f"{location}{joiner}digest={digest}", auth=auth,
                    headers={"Content-Length": str(size), "Content-Type": "application/octet-stream"}, data=payload,
                    timeout=max(self.timeout, 300))
            except RegistryError as exc:
                raise RegistryError(f"PUT upload failed (digest={digest}, size={size}): {exc}",
                                    status=exc.status) from None
            if status != 201:
                raise RegistryError(f"PUT upload failed (digest={digest}, size={size}): "
                                    f"{self._error(host, start, status, body)}", status=status)
            stated = headers.get("Docker-Content-Digest")
            if stated and stated != digest:
                raise RegistryError("PUT upload returned a different blob digest")
        finally:
            if handle is not None:
                handle.close()

    def _auth_for(self, host: str, scope: str) -> str | None:
        token = self._tokens.get((host, scope))
        return f"Bearer {token}" if token else None


def platform_manifest(registry, repository: str, manifest: Manifest, os: str = "linux",
                      architecture: str = "amd64") -> Manifest:
    """The single-platform image manifest behind ``manifest`` (itself, or the index entry)."""
    if not manifest.is_index:
        return manifest
    matches = [m for m in manifest.body["manifests"]
               if m.get("platform", {}).get("os") == os and m.get("platform", {}).get("architecture") == architecture]
    if len(matches) != 1:
        raise RegistryError(f"{repository}@{manifest.digest}: expected one {os}/{architecture} image, "
                            f"found {len(matches)}")
    found = registry.get_manifest(repository, matches[0]["digest"])
    if found is None:
        raise RegistryError(f"{repository}@{matches[0]['digest']}: platform manifest is missing")
    return found


def image_config(registry, repository: str, manifest: Manifest) -> tuple[str, dict]:
    """(config digest, config JSON) of the linux/amd64 image that ``manifest`` names."""
    image = platform_manifest(registry, repository, manifest)
    digest = image.body["config"]["digest"]
    return digest, json.loads(registry.get_blob(repository, digest))

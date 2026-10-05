"""Read halves of document systems and photo libraries, reached over their APIs.

They answer one question: does the destination already hold a file with this
checksum? Built against each project's documented API; not yet exercised
against a running instance (see docs/setup/local-quickstart.md).
"""

import re
from urllib.parse import urlencode

from towpath import credentials
from towpath.http import request_json


class LookupConnector:
    """Common shape: describe, probe, enumerate (registration only), lookup."""

    capabilities = ["lookup"]

    def __init__(self, source, base_dir=None):
        self.source_id = source.id
        self.kind = source.kind
        self.base_url = source.base_url
        self._secret = credentials.resolve(source.credential, base_dir)

    def describe(self) -> dict:
        return {"schema": "towpath.source/0", "source_id": self.source_id, "kind": self.kind,
                "connector": self.connector, "authority": "read", "capabilities": self.capabilities,
                "algorithm": self.algorithm(), "version": self.version()}

    def enumerate(self, cursor=None, **_):
        yield ("full_start", None)
        yield ("done", {"kind": "full", "cursor": None, "complete": True})


class PaperlessConnector(LookupConnector):
    """Paperless-ngx: checksum lookup with a token for a view-only user.

    Version 3 stores SHA-256 checksums; version 2 stores MD5.
    """

    connector = "paperless/0"

    def _headers(self):
        return {"Authorization": f"Token {self._secret}"}

    def version(self) -> str | None:
        if not hasattr(self, "_version"):
            _, headers = request_json("GET", f"{self.base_url}/api/", self._headers())
            self._version = headers.get("x-version")
        return self._version

    def algorithm(self) -> str:
        version = self.version()
        match = re.match(r"(\d+)", version or "")
        if not match:
            raise RuntimeError("Paperless did not report its version (X-Version header); cannot choose a checksum")
        return "sha256" if int(match.group(1)) >= 3 else "md5"

    def probe(self) -> dict:
        data, _ = request_json("GET", f"{self.base_url}/api/documents/?page_size=1", self._headers())
        return {"ok": True, "documents": data.get("count"), "version": self.version(), "algorithm": self.algorithm()}

    def lookup(self, algorithm: str, checksums: list[str]) -> dict[str, tuple[bool, str | None]]:
        if algorithm != self.algorithm():
            raise ValueError(f"Paperless here uses {self.algorithm()}, not {algorithm}")
        results = {}
        for checksum in checksums:
            query = urlencode({"checksum__iexact": checksum, "page_size": 1})
            data, _ = request_json("GET", f"{self.base_url}/api/documents/?{query}", self._headers())
            hits = data.get("results") or []
            results[checksum] = (bool(hits), str(hits[0]["id"]) if hits else None)
        return results


class ImmichConnector(LookupConnector):
    """Immich: checksum lookup with an API key limited to reading assets.

    Immich reports base64 SHA-1 checksums. The lookup uses metadata search,
    which needs only asset read permission.
    """

    connector = "immich/0"

    def _headers(self):
        return {"x-api-key": self._secret}

    def version(self) -> str | None:
        if not hasattr(self, "_version"):
            data, _ = request_json("GET", f"{self.base_url}/api/server/version", self._headers())
            self._version = ".".join(str(data.get(k, "")) for k in ("major", "minor", "patch"))
        return self._version

    def algorithm(self) -> str:
        return "sha1-base64"

    def probe(self) -> dict:
        data, _ = request_json("POST", f"{self.base_url}/api/search/metadata", self._headers(), {"size": 1})
        return {"ok": True, "assets": (data.get("assets") or {}).get("total"), "version": self.version()}

    def lookup(self, algorithm: str, checksums: list[str]) -> dict[str, tuple[bool, str | None]]:
        if algorithm != "sha1-base64":
            raise ValueError(f"Immich uses sha1-base64, not {algorithm}")
        results = {}
        for checksum in checksums:
            # A search request: it reads, and creates nothing.
            data, _ = request_json("POST", f"{self.base_url}/api/search/metadata", self._headers(),
                                   {"checksum": checksum, "size": 1})
            items = (data.get("assets") or {}).get("items") or []
            results[checksum] = (bool(items), items[0]["id"] if items else None)
        return results

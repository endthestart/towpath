"""A small JSON-over-HTTP helper for read adapters, using only the standard library."""

import json
import urllib.error
import urllib.request


class HttpStatusError(RuntimeError):
    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} from {url}")
        self.status = status
        self.body = body


def request_json(method: str, url: str, headers: dict, body=None, timeout: float = 30.0):
    """Return (parsed JSON, response headers). Raises HttpStatusError on non-2xx."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Accept": "application/json", **headers,
                                          **({"Content-Type": "application/json"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - configured service URL
            raw = resp.read()
            return (json.loads(raw) if raw else None), {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        raise HttpStatusError(exc.code, url, exc.read().decode(errors="replace")[:500]) from None

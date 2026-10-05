"""Endpoint profiles: validation, fingerprints, and explicit clients."""

import ipaddress
import socket
from urllib.parse import urlparse

from towpath import credentials
from towpath.canonical import digest

LOCAL_DESTINATIONS = {"this-machine", "bundled"}


class ProfileError(ValueError):
    pass


def fingerprint(endpoint) -> str:
    """Changing any of these fields voids grants and capability reports."""
    return digest({"base_url": endpoint.base_url, "model": endpoint.model, "kind": endpoint.kind,
                   "credential": endpoint.credential})[:16]


def check_destination(endpoint, bundled_hosts: tuple[str, ...] = ()) -> None:
    host = urlparse(endpoint.base_url).hostname
    if not host:
        raise ProfileError(f"endpoint {endpoint.id}: base URL has no host")
    if endpoint.destination == "this-machine":
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise ProfileError(f"endpoint {endpoint.id}: cannot resolve {host}") from exc
        addresses = {ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos}
        if not addresses or not all(a.is_loopback for a in addresses):
            raise ProfileError(f"endpoint {endpoint.id}: destination this-machine requires a loopback host, "
                               f"but {host} resolves elsewhere")
    elif endpoint.destination == "bundled" and host not in bundled_hosts:
        raise ProfileError(f"endpoint {endpoint.id}: destination bundled requires one of the bundled hosts "
                           f"{list(bundled_hosts)}, not {host}")


def make_client(endpoint, base_dir=None, max_retries: int = 0):
    """An OpenAI client that uses only the profile, never environment defaults."""
    import openai  # optional dependency: pip install "towpath[models]"

    key = credentials.resolve(endpoint.credential, base_dir) or "not-used"
    client = openai.OpenAI(base_url=endpoint.base_url, api_key=key, timeout=endpoint.timeout_seconds,
                           max_retries=max_retries)
    # The SDK fills these from OPENAI_* environment variables; none may reach an endpoint implicitly.
    client.organization = None
    client.project = None
    client.admin_api_key = None
    client.webhook_secret = None
    return client

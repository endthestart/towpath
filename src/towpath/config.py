"""Strict configuration loading.

Unknown keys are rejected, so a setting cannot quietly introduce a capability
the design does not allow (for example a write credential). Secrets never
appear here, only credential references (see ``towpath.credentials``).
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from towpath import credentials

SOURCE_KINDS = {"mail-provider", "document-system", "photo-library"}
ADAPTER_KEYS = {
    "fixture-gmail": {"path"},
    "gmail": {"client_secrets", "token"},
    "folder": {"path"},
    "paperless": {"base_url", "token"},
    "immich": {"base_url", "token"},
}
ADAPTER_KINDS = {
    "fixture-gmail": {"mail-provider"},
    "gmail": {"mail-provider"},
    "folder": {"document-system", "photo-library"},
    "paperless": {"document-system"},
    "immich": {"photo-library"},
}
SELECTOR_KEYS = {"id", "media_types", "disposition", "min_bytes", "max_bytes", "destination",
                 "exclude_labels", "classifier"}
ENDPOINT_KEYS = {"base_url", "credential", "model", "kind", "destination", "allow_data", "timeout_seconds"}
PROVIDER_KEYS = {"id", "kind", "adapter", "base_url", "api_key", "links"}
TOP_KEYS = {"stores", "sources", "selectors", "endpoints", "tasks", "providers", "bundled_hosts"}
STORE_KEYS = {"dir"}
DESTINATIONS = {"this-machine", "bundled", "self-hosted", "third-party"}
DATA_CLASSES = {"synthetic", "metadata", "content", "attachments", "derived-personal"}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Source:
    id: str
    kind: str
    adapter: str
    path: Path | None = None
    client_secrets: Path | None = None
    token: Path | None = None
    base_url: str | None = None
    credential: str | None = None


@dataclass(frozen=True)
class Selector:
    id: str
    media_types: tuple[str, ...]
    destination: str
    disposition: str | None = None
    min_bytes: int = 0
    max_bytes: int | None = None
    exclude_labels: tuple[str, ...] = ()
    classifier: str | None = None


@dataclass(frozen=True)
class Endpoint:
    id: str
    base_url: str
    credential: str
    model: str
    kind: str
    destination: str
    allow_data: tuple[str, ...]
    timeout_seconds: float = 60.0


@dataclass(frozen=True)
class Provider:
    id: str
    kind: str
    adapter: str
    base_url: str
    api_key: str
    links: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    root: Path
    store_dir: Path
    sources: dict[str, Source] = field(default_factory=dict)
    selectors: dict[str, Selector] = field(default_factory=dict)
    endpoints: dict[str, Endpoint] = field(default_factory=dict)
    tasks: dict[str, str] = field(default_factory=dict)
    providers: dict[str, Provider] = field(default_factory=dict)
    bundled_hosts: tuple[str, ...] = ()


def _check_keys(table: dict, allowed: set[str], where: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise ConfigError(f"unknown key(s) in {where}: {', '.join(sorted(unknown))}")


def _cred(ref: str, where: str, root: Path | None = None) -> str:
    try:
        credentials.validate(ref)
    except credentials.CredentialError as exc:
        raise ConfigError(f"{where}: {exc}") from exc
    if ref.startswith("file:") and root is not None and not Path(ref[5:]).is_absolute():
        return "file:" + str((root / ref[5:]).resolve())
    return ref


def _source(raw: dict, root: Path) -> Source:
    sid = raw.get("id", "?")
    adapter = raw.get("adapter")
    if adapter not in ADAPTER_KEYS:
        raise ConfigError(f"source {sid}: unknown adapter {adapter!r}")
    _check_keys(raw, {"id", "kind", "adapter"} | ADAPTER_KEYS[adapter], f"source {sid}")
    if raw["kind"] not in SOURCE_KINDS or raw["kind"] not in ADAPTER_KINDS[adapter]:
        raise ConfigError(f"source {sid}: adapter {adapter} cannot be kind {raw['kind']!r}")
    missing = ADAPTER_KEYS[adapter] - set(raw)
    if missing:
        raise ConfigError(f"source {sid}: missing {', '.join(sorted(missing))}")
    resolve = lambda key: (root / raw[key]).resolve() if key in raw else None  # noqa: E731
    token = raw.get("token")
    if adapter in {"paperless", "immich"}:
        return Source(sid, raw["kind"], adapter, base_url=raw["base_url"].rstrip("/"),
                      credential=_cred(token, f"source {sid}", root))
    return Source(sid, raw["kind"], adapter, path=resolve("path"), client_secrets=resolve("client_secrets"),
                  token=resolve("token"))


def _endpoint(eid: str, raw: dict, root: Path) -> Endpoint:
    _check_keys(raw, ENDPOINT_KEYS, f"endpoint {eid}")
    missing = {"base_url", "credential", "model", "kind", "destination", "allow_data"} - set(raw)
    if missing:
        raise ConfigError(f"endpoint {eid}: missing {', '.join(sorted(missing))}")
    if raw["kind"] not in {"chat", "embeddings"}:
        raise ConfigError(f"endpoint {eid}: kind must be chat or embeddings")
    if raw["destination"] not in DESTINATIONS:
        raise ConfigError(f"endpoint {eid}: destination must be one of {sorted(DESTINATIONS)}")
    bad = set(raw["allow_data"]) - DATA_CLASSES
    if bad:
        raise ConfigError(f"endpoint {eid}: unknown data classes {sorted(bad)}")
    return Endpoint(eid, raw["base_url"].rstrip("/"), _cred(raw["credential"], f"endpoint {eid}", root), raw["model"],
                    raw["kind"], raw["destination"], tuple(raw["allow_data"]),
                    float(raw.get("timeout_seconds", 60)))


def load(path: Path) -> Config:
    path = Path(path).resolve()
    data = tomllib.loads(path.read_text())
    _check_keys(data, TOP_KEYS, "configuration")
    root = path.parent
    stores = data.get("stores", {})
    _check_keys(stores, STORE_KEYS, "[stores]")
    store_dir = (root / stores.get("dir", "state")).resolve()

    sources = {s.id: s for s in (_source(raw, root) for raw in data.get("sources", []))}

    selectors: dict[str, Selector] = {}
    for raw in data.get("selectors", []):
        _check_keys(raw, SELECTOR_KEYS, f"selector {raw.get('id', '?')}")
        dest = raw["destination"]
        if dest not in sources or sources[dest].kind == "mail-provider":
            raise ConfigError(f"selector {raw['id']}: destination {dest!r} is not a destination source")
        selectors[raw["id"]] = Selector(
            id=raw["id"], media_types=tuple(raw["media_types"]), destination=dest,
            disposition=raw.get("disposition"), min_bytes=int(raw.get("min_bytes", 0)),
            max_bytes=raw.get("max_bytes"), exclude_labels=tuple(raw.get("exclude_labels", ())),
            classifier=raw.get("classifier"))

    endpoints = {eid: _endpoint(eid, raw, root) for eid, raw in data.get("endpoints", {}).items()}
    tasks = dict(data.get("tasks", {}))
    for task, eid in tasks.items():
        if eid not in endpoints:
            raise ConfigError(f"task {task}: unknown endpoint {eid!r}")

    providers: dict[str, Provider] = {}
    for raw in data.get("providers", []):
        _check_keys(raw, PROVIDER_KEYS, f"provider {raw.get('id', '?')}")
        if raw.get("kind") != "mail-management" or raw.get("adapter") != "inbox-zero":
            raise ConfigError(f"provider {raw.get('id')}: only kind mail-management with adapter inbox-zero")
        providers[raw["id"]] = Provider(raw["id"], raw["kind"], raw["adapter"], raw["base_url"].rstrip("/"),
                                        _cred(raw["api_key"], f"provider {raw['id']}", root),
                                        dict(raw.get("links", {})))

    return Config(root=root, store_dir=store_dir, sources=sources, selectors=selectors, endpoints=endpoints,
                  tasks=tasks, providers=providers, bundled_hosts=tuple(data.get("bundled_hosts", ())))

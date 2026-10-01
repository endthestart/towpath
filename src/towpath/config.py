"""Strict configuration loading.

Unknown keys are rejected, so a setting cannot quietly introduce a capability
the design does not allow (for example a write credential in this slice).
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

SOURCE_KINDS = {"mail-provider", "document-system", "photo-library"}
ADAPTERS = {"fixture-gmail", "folder"}
SOURCE_KEYS = {"id", "kind", "adapter", "path"}
SELECTOR_KEYS = {"id", "media_types", "disposition", "min_bytes", "max_bytes", "destination",
                 "exclude_labels", "classifier"}
TOP_KEYS = {"stores", "sources", "selectors"}
STORE_KEYS = {"dir"}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Source:
    id: str
    kind: str
    adapter: str
    path: Path


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
class Config:
    root: Path
    store_dir: Path
    sources: dict[str, Source] = field(default_factory=dict)
    selectors: dict[str, Selector] = field(default_factory=dict)


def _check_keys(table: dict, allowed: set[str], where: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise ConfigError(f"unknown key(s) in {where}: {', '.join(sorted(unknown))}")


def load(path: Path) -> Config:
    path = Path(path).resolve()
    data = tomllib.loads(path.read_text())
    _check_keys(data, TOP_KEYS, "configuration")
    root = path.parent
    stores = data.get("stores", {})
    _check_keys(stores, STORE_KEYS, "[stores]")
    store_dir = (root / stores.get("dir", "state")).resolve()

    sources: dict[str, Source] = {}
    for raw in data.get("sources", []):
        _check_keys(raw, SOURCE_KEYS, f"source {raw.get('id', '?')}")
        if raw["kind"] not in SOURCE_KINDS:
            raise ConfigError(f"source {raw['id']}: unknown kind {raw['kind']!r}")
        if raw["adapter"] not in ADAPTERS:
            raise ConfigError(f"source {raw['id']}: unknown adapter {raw['adapter']!r}")
        sources[raw["id"]] = Source(raw["id"], raw["kind"], raw["adapter"], (root / raw["path"]).resolve())

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
    return Config(root=root, store_dir=store_dir, sources=sources, selectors=selectors)

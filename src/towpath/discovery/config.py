"""Strict validation of the ``[files]`` table.

Pure validation: nothing here touches the filesystem, imports a provider, or
starts a process. A configuration without ``[files]`` never imports this
module, and one with ``enabled = false`` builds no providers.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ADAPTERS = {
    # Test aid: answers from a generated catalog of synthetic files.
    "fixture": {"required": {"catalog"}, "optional": set()},
    # Recoll's Python binding, run in a separate interpreter (see providers/recoll.py).
    "recoll": {"required": {"confdir"}, "optional": {"python"}},
    # Capability slot only: probe reports the version, search is not implemented.
    "sist2": {"required": {"command"}, "optional": {"index"}},
}
FILES_KEYS = {"enabled", "recover_dir", "limits", "roots", "providers"}
ROOT_KEYS = {"alias", "path", "exclude"}
PROVIDER_BASE_KEYS = {"id", "adapter", "roots"}
ALIAS = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

# Defaults, and ceilings a configuration may not exceed.
LIMITS = {
    "max_results": (20, 200),
    "max_scan": (500, 5000),
    "max_excerpt_bytes": (2000, 65536),
    "max_recover_bytes": (50_000_000, 2_000_000_000),
    "timeout_seconds": (30, 600),
    "max_output_bytes": (4_000_000, 64_000_000),
}


class FilesConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Root:
    alias: str
    path: Path
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderConfig:
    id: str
    adapter: str
    roots: tuple[str, ...]
    options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FilesConfig:
    enabled: bool
    recover_dir: Path
    limits: dict
    roots: dict[str, Root]
    providers: dict[str, ProviderConfig]


def _keys(table: dict, allowed: set[str], where: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise FilesConfigError(f"unknown key(s) in {where}: {', '.join(sorted(unknown))}")


def _path(value, root: Path, where: str) -> Path:
    if not isinstance(value, str) or not value:
        raise FilesConfigError(f"{where} must be a non-empty string")
    if "\x00" in value:
        raise FilesConfigError(f"{where} contains a NUL byte")
    # normpath, not resolve(): validation must not touch the filesystem.
    return Path(os.path.normpath(value if os.path.isabs(value) else root / value))


def _inside(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def _limits(raw) -> dict:
    if not isinstance(raw, dict):
        raise FilesConfigError("[files.limits] must be a table")
    _keys(raw, set(LIMITS), "[files.limits]")
    limits = {}
    for name, (default, ceiling) in LIMITS.items():
        value = raw.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise FilesConfigError(f"[files.limits] {name} must be a positive integer")
        if value > ceiling:
            raise FilesConfigError(f"[files.limits] {name} may not exceed {ceiling}")
        limits[name] = value
    return limits


def _exclude(patterns, where: str) -> tuple[str, ...]:
    if not isinstance(patterns, list) or not all(isinstance(p, str) and p for p in patterns):
        raise FilesConfigError(f"{where} exclude must be a list of non-empty strings")
    for pattern in patterns:
        if pattern.startswith("/") or ".." in Path(pattern).parts:
            raise FilesConfigError(f"{where} exclude {pattern!r} must be relative to the root, without '..'")
    return tuple(patterns)


def _provider(raw: dict, roots: dict[str, Root], root: Path) -> ProviderConfig:
    pid = raw.get("id")
    if not isinstance(pid, str) or not ALIAS.match(pid):
        raise FilesConfigError(f"files provider id {pid!r} must match {ALIAS.pattern}")
    adapter = raw.get("adapter")
    if adapter not in ADAPTERS:
        raise FilesConfigError(f"files provider {pid}: unknown adapter {adapter!r}")
    spec = ADAPTERS[adapter]
    _keys(raw, PROVIDER_BASE_KEYS | spec["required"] | spec["optional"], f"files provider {pid}")
    missing = ({"roots"} | spec["required"]) - set(raw)
    if missing:
        raise FilesConfigError(f"files provider {pid}: missing {', '.join(sorted(missing))}")
    names = raw["roots"]
    if not isinstance(names, list) or not names:
        raise FilesConfigError(f"files provider {pid}: roots must be a non-empty list of root aliases")
    unknown = [name for name in names if name not in roots]
    if unknown:
        raise FilesConfigError(f"files provider {pid}: unknown root(s) {', '.join(map(str, unknown))}")
    options = {}
    for key in ("catalog", "confdir", "index"):
        if key in raw:
            options[key] = _path(raw[key], root, f"files provider {pid} {key}")
    if "python" in raw:
        value = raw["python"]
        if isinstance(value, str) and value and "/" not in value and "\x00" not in value:
            options["python"] = value  # a command name, looked up on PATH (for example "python3")
        else:
            options["python"] = str(_path(value, root, f"files provider {pid} python"))
    if "command" in raw:
        command = raw["command"]
        if not isinstance(command, list) or not command or not all(isinstance(c, str) and c for c in command):
            raise FilesConfigError(f"files provider {pid}: command must be a non-empty list of strings (no shell)")
        options["command"] = tuple(command)
    return ProviderConfig(pid, adapter, tuple(names), options)


def parse(raw, root: Path, store_dir: Path) -> FilesConfig:
    """Validate ``[files]``. ``root`` is the config file's folder."""
    if not isinstance(raw, dict):
        raise FilesConfigError("[files] must be a table")
    _keys(raw, FILES_KEYS, "[files]")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise FilesConfigError("[files] enabled must be true or false")
    limits = _limits(raw.get("limits", {}))

    roots: dict[str, Root] = {}
    for entry in raw.get("roots", []):
        if not isinstance(entry, dict):
            raise FilesConfigError("[[files.roots]] entries must be tables")
        alias = entry.get("alias")
        where = f"files root {alias}"
        _keys(entry, ROOT_KEYS, where)
        if not isinstance(alias, str) or not ALIAS.match(alias):
            raise FilesConfigError(f"files root alias {alias!r} must match {ALIAS.pattern}")
        if alias in roots:
            raise FilesConfigError(f"files root {alias} is defined twice")
        if "path" not in entry:
            raise FilesConfigError(f"{where}: missing path")
        path = _path(entry["path"], root, f"{where} path")
        for other in roots.values():
            if _inside(path, other.path) or _inside(other.path, path):
                raise FilesConfigError(f"{where} overlaps files root {other.alias}; roots may not nest")
        roots[alias] = Root(alias, path, _exclude(entry.get("exclude", []), where))

    recover_dir = _path(raw.get("recover_dir", str(store_dir / "recovered")), root, "[files] recover_dir")
    for r in roots.values():
        if _inside(recover_dir, r.path) or _inside(r.path, recover_dir):
            raise FilesConfigError(f"[files] recover_dir may not be inside or contain files root {r.alias}")

    providers: dict[str, ProviderConfig] = {}
    for entry in raw.get("providers", []):
        if not isinstance(entry, dict):
            raise FilesConfigError("[[files.providers]] entries must be tables")
        provider = _provider(entry, roots, root)
        if provider.id in providers:
            raise FilesConfigError(f"files provider {provider.id} is defined twice")
        providers[provider.id] = provider
    return FilesConfig(enabled, recover_dir, limits, roots, providers)

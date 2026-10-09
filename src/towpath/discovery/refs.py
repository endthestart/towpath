"""Path safety: every provider reference must land inside a configured root.

Provider output is untrusted. A reference is accepted only when it is a
``file:`` URL or a root-relative path, it resolves (after following symlinks)
inside a configured root, and it is not excluded there. Member names inside
containers are labels only and are never used as filesystem paths.
"""

import fnmatch
import functools
import os
import re
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

ALLOWED_SCHEMES = {"file"}
SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]")


class BadReference(ValueError):
    """A reference that escapes its roots or uses a refused scheme."""


def _real(path: Path) -> Path:
    return Path(os.path.realpath(path))


class Folders:
    """Real paths for many files at once, for listing a large tree. Each folder is resolved and listed once and
    only symbolic links are followed one by one, so files' own metadata is never read. Reads still resolve
    each path in full first."""

    def __init__(self, size: int = 4096):
        self._folder = functools.lru_cache(maxsize=size)(self._resolve_folder)
        self.base = functools.lru_cache(maxsize=64)(_real)  # each root's own real path

    @staticmethod
    def _resolve_folder(folder: str) -> tuple[Path, frozenset] | None:
        try:
            with os.scandir(folder) as entries:
                links = frozenset(e.name for e in entries if e.is_symlink())
        except OSError:
            return None
        return _real(Path(folder)), links

    def real(self, path: Path) -> Path:
        found = self._folder(str(path.parent))
        if found is None or path.name in found[1]:
            return _real(path)
        return found[0] / path.name


def _within(child: Path, parent: Path) -> bool:
    """For normalised paths: compared as text, as ``parent in child.parents`` would, without building each parent."""
    child, parent = str(child), str(parent)
    return child == parent or child.startswith(parent if parent.endswith("/") else parent + "/")


def _relative(child: str, parent: str) -> str:
    """``child`` below ``parent``, both normalised and ``_within`` each other."""
    return child[len(parent):] if parent.endswith("/") else child[len(parent) + 1:]


def excluded(root, rel_path: str) -> bool:
    """fnmatch semantics: ``*`` also crosses folders, so ``private/*`` covers everything under private/."""
    return any(fnmatch.fnmatchcase(rel_path, pattern) or fnmatch.fnmatchcase(rel_path + "/", pattern)
               for pattern in root.exclude)


def check_relative(rel_path: str) -> str:
    if not rel_path or "\x00" in rel_path:
        raise BadReference("empty or NUL-containing path")
    pure = PurePosixPath(rel_path)
    if pure.is_absolute() or ".." in pure.parts or "\\" in rel_path:
        raise BadReference("path must be relative to its root, without '..'")
    return str(pure)


def resolve_in_root(root, rel_path: str, folders: Folders | None = None) -> Path:
    """The real filesystem path of ``rel_path`` under ``root``, refusing anything that escapes it."""
    rel_path = check_relative(rel_path)
    base = _real(root.path) if folders is None else folders.base(root.path)
    target = _real(base / rel_path) if folders is None else folders.real(base / rel_path)
    if not _within(target, base):
        raise BadReference("reference escapes its root")
    return target


def locate(reference: str, roots: dict, folders: Folders | None = None) -> tuple[str, str]:
    """Map a provider's ``file:`` URL (or absolute path) to ``(root alias, relative path)``.

    The lexical path picks the root; the real path (symlinks followed) must stay inside it.
    """
    if "://" in reference or reference.startswith("file:"):
        parts = urlsplit(reference)
        if parts.scheme not in ALLOWED_SCHEMES:
            raise BadReference(f"scheme {parts.scheme!r} is not allowed")
        if parts.netloc not in {"", "localhost"}:
            raise BadReference("file URLs must not name a host")
        path = unquote(parts.path)
    else:
        path = reference
    if "\x00" in path or not os.path.isabs(path):
        raise BadReference("reference must be an absolute file path")
    lexical = os.path.normpath(path)
    for alias, root in roots.items():
        base = os.path.normpath(root.path)
        if lexical != base and _within(lexical, base):
            rel = _relative(lexical, base)
            resolve_in_root(root, rel, folders)
            return alias, rel
        real_base = str(_real(root.path) if folders is None else folders.base(root.path))
        if lexical != real_base and _within(lexical, real_base):
            rel = _relative(lexical, real_base)
            resolve_in_root(root, rel, folders)
            return alias, rel
    raise BadReference("reference is outside every configured root")


def safe_filename(name: str | None, fallback: str = "recovered") -> str:
    """A single path component built from an untrusted name."""
    base = PurePosixPath((name or "").replace("\\", "/")).name
    base = SAFE_NAME.sub("_", base).strip(" .")
    return base[:120] or fallback

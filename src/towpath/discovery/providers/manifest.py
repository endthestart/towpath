"""Manifest provider: file references from an existing inventory, with no text extraction.

An inventory (a manifest written by a backup, catalog, or ``find``/``stat`` job) lists files a text
indexer may skip, such as camera raw images. Towpath reads the manifest, never the files, so every
entry is a reference with metadata only. Searching matches path and member names; nothing here can
claim content search. A manifest that declares an incomplete scope never yields a complete import.

Formats (``towpath.files.manifest/1``): a JSON object with ``entries``, or JSON Lines whose first
line is the header object and each further line one entry. See docs/file-discovery.md.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from towpath.canonical import canonical_json
from towpath.discovery.providers.base import BaseProvider, Hit, Listing, Unavailable
from towpath.discovery.records import HASH_LENGTHS, STATUSES, DateFact, Extraction, Member

FORMAT = "towpath.files.manifest/1"
ENTRY_KEYS = {"path", "size", "mtime", "media_type", "sha256", "members", "status", "detail"}


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat(timespec="seconds")
    except ValueError:
        return None


def _epoch(value) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    iso = _iso(value)
    return int(datetime.fromisoformat(iso).timestamp()) if iso else None


class Provider(BaseProvider):
    adapter = "manifest"
    search_depth = "catalog"
    capabilities = {"probe": "verified", "search": "verified", "describe": "verified", "excerpt": "not-implemented",
                    "recover": "not-implemented", "enumerate": "verified", "root-scoped-query": "verified"}

    def __init__(self, provider_config, files_config):
        super().__init__(provider_config, files_config)
        self.paths = [Path(p) for p in provider_config.options["manifests"]]
        self._loaded = None

    # -- reading ----------------------------------------------------------------------------------------

    def _read(self, path: Path) -> tuple[dict, list]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise Unavailable(f"manifest {path.name} unreadable: {type(exc).__name__}") from None
        try:
            if path.suffix == ".jsonl":
                lines = [line for line in text.splitlines() if line.strip()]
                header = json.loads(lines[0]) if lines else {}
                entries = [json.loads(line) for line in lines[1:]]
            else:
                header = json.loads(text)
                entries = header.pop("entries", None)
        except ValueError:
            raise Unavailable(f"manifest {path.name} is not valid JSON") from None
        if header.get("format") != FORMAT or not isinstance(entries, list):
            raise Unavailable(f"manifest {path.name} is not a {FORMAT} manifest")
        if header.get("root") not in self.roots:
            raise Unavailable(f"manifest {path.name} describes a root this provider is not configured for")
        return header, entries

    def _manifests(self) -> list[tuple[dict, list[Hit], int]]:
        """(header, valid hits, rejected count) per manifest."""
        if self._loaded is None:
            self._loaded = []
            for path in self.paths:
                header, entries = self._read(path)
                hits, rejected = [], 0
                for entry in entries:
                    hit = self._hit(header, entry)
                    if hit is None:
                        rejected += 1
                    else:
                        hits.append(hit)
                self._loaded.append((header, hits, rejected))
        return self._loaded

    def _hit(self, header: dict, entry) -> Hit | None:
        if not isinstance(entry, dict) or set(entry) - ENTRY_KEYS or not isinstance(entry.get("path"), str):
            return None
        rel = entry["path"]
        if not rel or rel.startswith("/") or ".." in rel.split("/") or "\x00" in rel:
            return None
        try:
            members = tuple(Member(kind, name, index) for kind, name, index in entry.get("members", []))
        except (TypeError, ValueError):
            return None
        status = entry.get("status", "skipped")
        if status not in STATUSES:
            return None
        hashes = {}
        sha = entry.get("sha256")
        if sha is not None:
            if not isinstance(sha, str) or len(sha) != HASH_LENGTHS["sha256"] or not re.fullmatch(r"[0-9a-f]+", sha):
                return None
            hashes["sha256"] = sha
        size = entry.get("size")
        mtime = _epoch(entry.get("mtime"))
        dates = []
        if entry.get("mtime") is not None and _iso(entry["mtime"]):
            dates.append(DateFact("member-modified" if members else "file-modified", _iso(entry["mtime"]),
                                  "inventory manifest"))
        if _iso(header.get("captured_at")):
            dates.append(DateFact("indexed-at", _iso(header["captured_at"]), "manifest captured"))
        root = self.roots[header["root"]]
        native = canonical_json([header["root"], rel, [[m.kind, m.name, m.index] for m in members]])
        detail = entry.get("detail") or "inventory manifest: metadata only, no text extraction"
        stamp = {"size": size, "mtime": mtime, "basis": "inventory manifest"} if not members else None
        version = f"size={size};mtime={mtime}" if size is not None and mtime is not None else None
        return Hit(native_id=native, url=str(Path(root.path) / rel), members=members,
                   extraction=Extraction(status, "manifest", str(header.get("manifest_id") or ""), None, None, detail),
                   media_type=entry.get("media_type"), size=size, dates=tuple(dates), hashes=hashes, version=version,
                   source_stamp=stamp)

    def _all(self, aliases: set[str]) -> list[Hit]:
        return [h for header, hits, _ in self._manifests() if header["root"] in aliases for h in hits]

    # -- provider contract ----------------------------------------------------------------------------------

    def probe(self, timeout: float) -> dict:
        loaded = self._manifests()
        return {"tool": "manifest", "version": "1", "manifests": len(loaded),
                "entries": sum(len(h) for _, h, _ in loaded), "rejected": sum(r for _, _, r in loaded),
                "scope_complete": all((h.get("scope") or {}).get("complete") is True for h, _, _ in loaded)}

    def search(self, query: str, roots: list, max_rows: int, timeout: float) -> Listing:
        words = [w.lower() for w in query.split() if w]
        matches = []
        for hit in self._all({r.alias for r in roots}):
            names = " ".join([hit.url, *(m.name or "" for m in hit.members)]).lower()
            if words and all(w in names for w in words):
                matches.append(hit)
                if len(matches) > max_rows:
                    break
        return Listing(matches[:max_rows], len(matches) <= max_rows, min(len(matches), max_rows))

    def enumerate(self, root, max_rows: int, timeout: float, offset: int = 0) -> Listing:
        rows = self._all({root.alias})[offset:]
        complete_scope = all((h.get("scope") or {}).get("complete") is True and not rejected
                             for h, _, rejected in self._manifests() if h["root"] == root.alias)
        if len(rows) > max_rows:
            exhausted = False
        else:
            exhausted = True if complete_scope else None
        return Listing(rows[:max_rows], exhausted, min(len(rows), max_rows))

    def describe(self, native_id: str, timeout: float) -> Hit:
        for _, hits, _ in self._manifests():
            for hit in hits:
                if hit.native_id == native_id:
                    return hit
        raise Unavailable("no such entry in the configured manifests")

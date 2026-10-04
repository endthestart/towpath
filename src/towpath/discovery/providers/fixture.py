"""Fixture provider: answers from a generated catalog of synthetic files.

A test aid only. Its matching (every query word as a case-insensitive
substring) is not a search engine and proves nothing about real providers.
Like a real index, it reports the version and source stamp recorded when the
catalog was generated (or re-stamped by ``corpus.reindex``), not the file's
current state, so changing a corpus file without re-indexing leaves it stale.
"""

import base64
import json
import os
from pathlib import Path

from towpath.discovery.providers.base import BaseProvider, Excerpt, Hit, LimitExceeded, Listing, Unavailable
from towpath.discovery.records import DateFact, Extraction, Member


class Provider(BaseProvider):
    adapter = "fixture"
    capabilities = {"probe": "verified", "search": "verified", "describe": "verified", "excerpt": "verified",
                    "recover": "verified", "enumerate": "verified", "root-scoped-query": "verified"}

    def __init__(self, provider_config, files_config):
        super().__init__(provider_config, files_config)
        self.catalog_path = Path(provider_config.options["catalog"])
        self._entries = None

    def _catalog(self) -> list[dict]:
        if self._entries is None:
            try:
                data = json.loads(self.catalog_path.read_text())
            except (OSError, ValueError) as exc:
                raise Unavailable(f"fixture catalog unreadable: {type(exc).__name__}") from None
            if data.get("format") != "towpath.files.fixture-catalog/1":
                raise Unavailable("fixture catalog has an unknown format")
            self._entries = [e for e in data["entries"] if e["root"] in self.roots]
        return self._entries

    def _url(self, entry: dict) -> str:
        # Lexical, unresolved: the service decides whether it stays inside the root.
        return "file://" + os.path.normpath(os.path.join(self.roots[entry["root"]].path, entry["path"]))

    @staticmethod
    def _version(entry: dict) -> str | None:
        stamp = entry.get("source")
        if not stamp:
            return None
        return f"size={stamp['size']};mtime={stamp['mtime']};ctime={stamp['ctime']}"

    def _hit(self, entry: dict, passage: dict | None = None) -> Hit:
        ex = entry["extraction"]
        return Hit(
            native_id=entry["native_id"], url=self._url(entry),
            members=tuple(Member(kind, name, index) for kind, name, index in entry["members"]),
            extraction=Extraction(ex["status"], ex["parser"], ex["parser_version"], ex["extracted_bytes"],
                                  ex["limit_bytes"], ex["detail"]),
            media_type=entry["media_type"], size=entry["size"],
            dates=tuple(DateFact(**d) for d in entry["dates"]), hashes=dict(entry["hashes"]),
            version=self._version(entry), passage=passage, source_stamp=entry.get("source"))

    def _entry(self, native_id: str) -> dict:
        for entry in self._catalog():
            if entry["native_id"] == native_id:
                return entry
        raise Unavailable("no such item in the provider")

    def probe(self, timeout: float) -> dict:
        return {"tool": "fixture", "version": "1", "entries": len(self._catalog()), "test_aid": True}

    @staticmethod
    def _bounded(rows: list, max_rows: int) -> tuple[list, bool]:
        return rows[:max_rows], len(rows) <= max_rows

    def search(self, query: str, roots: list, max_rows: int, timeout: float) -> Listing:
        words = [w.lower() for w in query.split() if w]
        wanted = {r.alias for r in roots}
        matches = []
        for entry in self._catalog() if words else []:
            if entry["root"] not in wanted:
                continue
            names = " ".join(str(m[1] or "") for m in entry["members"]) + " " + entry["path"]
            if all(w in ((entry["text"] or "") + "\n" + names).lower() for w in words):
                matches.append(entry)
                if len(matches) > max_rows:
                    break
        rows, exhausted = self._bounded(matches, max_rows)
        hits = []
        for entry in rows:
            start = (entry["text"] or "").lower().find(words[0])
            hits.append(self._hit(entry, {"kind": "text-offset", "start": start} if start >= 0 else None))
        return Listing(hits, exhausted, len(rows))

    def enumerate(self, root, max_rows: int, timeout: float) -> Listing:
        rows, exhausted = self._bounded([e for e in self._catalog() if e["root"] == root.alias], max_rows)
        return Listing([self._hit(e) for e in rows], exhausted, len(rows))

    def describe(self, native_id: str, timeout: float) -> Hit:
        return self._hit(self._entry(native_id))

    def excerpt(self, native_id: str, start: int, max_bytes: int, timeout: float) -> Excerpt:
        entry = self._entry(native_id)
        text = entry["text"]
        if text is None:
            raise Unavailable(f"no extracted text ({entry['extraction']['status']})")
        start = max(0, min(start, len(text)))
        piece = text[start:start + max_bytes]
        return Excerpt(piece, start, entry["extraction"]["extracted_bytes"],
                       entry["extraction"]["status"] == "truncated")

    def recover(self, native_id: str, dest, max_bytes: int, timeout: float) -> None:
        entry = self._entry(native_id)
        if "recover_b64" in entry:
            data = base64.b64decode(entry["recover_b64"])
        elif not entry["members"]:
            data = None
        else:
            raise Unavailable("the fixture catalog holds no bytes for this member")
        if data is None:
            # A top-level file: the service has already confirmed it is inside its root.
            from towpath.discovery.refs import resolve_in_root

            source = resolve_in_root(self.roots[entry["root"]], entry["path"])
            if source.stat().st_size > max_bytes:
                raise LimitExceeded("item is larger than max_recover_bytes")
            data = source.read_bytes()
        if len(data) > max_bytes:
            raise LimitExceeded("item is larger than max_recover_bytes")
        Path(dest).write_bytes(data)

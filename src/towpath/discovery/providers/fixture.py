"""Fixture provider: answers from a generated catalog of synthetic files.

A test aid only. Its matching (every query word as a case-insensitive
substring) is not a search engine and proves nothing about real providers.
Versions are read live from the files on disk, so changing or deleting a
corpus file behaves as it would with a real provider.
"""

import base64
import json
import os
from pathlib import Path

from towpath.discovery.providers.base import BaseProvider, Excerpt, Hit, LimitExceeded, Unavailable
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

    def _version(self, entry: dict) -> str | None:
        path = os.path.join(self.roots[entry["root"]].path, entry["path"])
        try:
            st = os.stat(path)
        except OSError:
            return None
        return f"size={st.st_size};mtime_ns={st.st_mtime_ns}"

    def _hit(self, entry: dict, passage: dict | None = None) -> Hit:
        ex = entry["extraction"]
        return Hit(
            native_id=entry["native_id"], url=self._url(entry),
            members=tuple(Member(kind, name, index) for kind, name, index in entry["members"]),
            extraction=Extraction(ex["status"], ex["parser"], ex["parser_version"], ex["extracted_bytes"],
                                  ex["limit_bytes"], ex["detail"]),
            media_type=entry["media_type"], size=entry["size"],
            dates=tuple(DateFact(**d) for d in entry["dates"]), hashes=dict(entry["hashes"]),
            version=self._version(entry), passage=passage)

    def _entry(self, native_id: str) -> dict:
        for entry in self._catalog():
            if entry["native_id"] == native_id:
                return entry
        raise Unavailable("no such item in the provider")

    def probe(self, timeout: float) -> dict:
        return {"tool": "fixture", "version": "1", "entries": len(self._catalog()), "test_aid": True}

    def search(self, query: str, roots: list, max_rows: int, timeout: float):
        words = [w.lower() for w in query.split() if w]
        if not words:
            return
        wanted = {r.alias for r in roots}
        rows = 0
        for entry in self._catalog():
            if entry["root"] not in wanted:
                continue
            names = " ".join(str(m[1] or "") for m in entry["members"]) + " " + entry["path"]
            haystack = ((entry["text"] or "") + "\n" + names).lower()
            if all(w in haystack for w in words):
                text = (entry["text"] or "").lower()
                start = text.find(words[0])
                yield self._hit(entry, {"kind": "text-offset", "start": start} if start >= 0 else None)
                rows += 1
                if rows >= max_rows:
                    return

    def enumerate(self, root, max_rows: int, timeout: float):
        for n, entry in enumerate(e for e in self._catalog() if e["root"] == root.alias):
            if n >= max_rows:
                return
            yield self._hit(entry)

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

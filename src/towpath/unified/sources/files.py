"""File discovery catalogs as unified adapters."""

from towpath.stores import open_store
from towpath.unified import envelopes
from towpath.unified.contracts import CoverageSummary, Filters, SourcePage, SourceStatus
from towpath.unified.federation import SourceAdapter, SourceDenied, SourceUnavailable, UnknownReference
from towpath.unified.sources.common import FILE_DEPTH, MAX_SCAN, READ_ROLE, _any, _fts_query, _like, _offset, _scan


def searchable_roots(config) -> set[str]:
    """Files roots with a search grant: the roots whose catalog any page may show."""
    if config.files is None:
        return set()
    from towpath.discovery import policy

    return {root for root, features in policy.granted(config).items() if "search" in features}


class FilesAdapter(SourceAdapter):
    """One configured files provider: its catalog in the files store, plus its content index in connect mode."""

    source_type = "files"

    def __init__(self, config, provider_config, connect: bool = False):
        self.config, self.provider_config, self.connect = config, provider_config, connect
        self.provider_id = provider_config.id
        self.source_id = f"files-{provider_config.id}"
        self.depth = FILE_DEPTH.get(provider_config.adapter)

    def _granted(self) -> tuple[list[str], list[str]]:
        from towpath.discovery import policy

        grants = policy.granted(self.config)
        roots = list(self.provider_config.roots)
        return [r for r in roots if "search" in grants.get(r, set())], [r for r in roots
                                                                        if "search" not in grants.get(r, set())]

    def _db(self):
        return open_store(self.config.store_dir, "files", READ_ROLE)

    def status(self) -> SourceStatus:
        from towpath.discovery import store as fstore

        files = self.config.files
        state, reason = "ready", None
        if files is None or not files.enabled:
            state, reason = "disabled", "file discovery is not enabled"
        granted, ungranted = self._granted()
        counts: dict[str, int] = {}
        complete: list = []
        db = self._db()
        try:
            for alias in granted:
                for status, n in fstore.counts(db, self.provider_id, alias):
                    key = envelopes.FILE_COVERAGE.get(status, "unknown")
                    counts[key] = counts.get(key, 0) + n
                latest = db.execute("SELECT complete FROM coverage WHERE provider_id = ? AND root_alias = ? "
                                    "ORDER BY rowid DESC LIMIT 1", (self.provider_id, alias)).fetchone()
                complete.append(None if latest is None else bool(latest["complete"]))
            last = db.execute("SELECT * FROM runs WHERE provider_id = ? AND kind = 'import' ORDER BY started_at DESC "
                              "LIMIT 1", (self.provider_id,)).fetchone()
        finally:
            db.close()
        notes = []
        if ungranted:
            notes.append(f"roots without a search grant are not searched: {', '.join(ungranted)}")
        if any(c is None for c in complete):
            notes.append("some roots have never been imported into the catalog")
        if not granted or ungranted or any(c is False for c in complete):
            inventory = False if (ungranted or any(c is False for c in complete)) else None
        else:
            inventory = None if any(c is None for c in complete) else True
        text_done = counts.get("text-extracted", 0) == sum(counts.values())
        if inventory is True:
            content = self.depth == "content-index" and text_done
        else:
            content = False if (self.depth != "content-index" or inventory is False) else None
        if self.depth != "content-index":
            notes.append("this provider holds references and metadata only; it cannot search file text")
        capabilities = {"catalog-search": "verified", "describe": "verified"}
        if self.depth == "content-index":
            capabilities["content-search"] = "verified" if self.connect else "disabled"
            capabilities["excerpt"] = "verified" if self.connect else "disabled"
        depths = ("catalog", "content-index") if self.connect and self.depth == "content-index" else ("catalog",)
        return SourceStatus(
            self.source_id, "files", f"Files via {self.provider_config.adapter} ({self.provider_id})", state,
            capabilities, depths,
            {"roots": list(self.provider_config.roots), "searched_roots": granted, "ungranted_roots": ungranted},
            CoverageSummary(counts, inventory, content, last["run_id"] if last else None, tuple(notes)),
            {"last_import": last["started_at"] if last else None,
             "last_termination": last["termination"] if last else None,
             "last_reason": last["reason"] if last else None}, reason)

    def _catalog(self, filters: Filters, cursor: str | None, limit: int) -> SourcePage:
        from towpath.discovery import refs

        granted, _ = self._granted()
        if not granted:
            raise SourceDenied(f"no root of files provider {self.provider_id} has a search grant")
        roots = self.config.files.roots
        where = ["o.provider_id = ?", "o.missing_since_run IS NULL",
                 f"o.root_alias IN ({','.join('?' for _ in granted)})"]
        args: list = [self.provider_id, *granted]
        text = "lower(o.rel_path || ' ' || o.members)"
        for w in filters.words:
            where.append(f"{text} LIKE ? ESCAPE '\\'")
            args.append(_like(w))
        if filters.extensions:
            where.append(_any(f"{text} LIKE ? ESCAPE '\\'", len(filters.extensions)))
            args += [_like(f".{e}") for e in filters.extensions]
        if filters.media_types:
            where.append(_any("lower(coalesce(o.media_type, '')) LIKE ?", len(filters.media_types)))
            args += [f"{m}%" for m in filters.media_types]
        if filters.within:
            root, _, folder = filters.within.partition("/")
            where.append("o.root_alias = ?")
            args.append(root)
            if folder:
                where.append("(o.rel_path = ? OR o.rel_path LIKE ? ESCAPE '\\')")
                args += [folder, folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"]
        if filters.min_size is not None:
            where.append("o.size >= ?")
            args.append(filters.min_size)
        if filters.max_size is not None:
            where.append("o.size <= ?")
            args.append(filters.max_size)
        # Catalog order (the import's, which is path order for a first import). With a query the trigram index
        # drives: it yields rows in that order, so a page stops after its rows instead of sorting every match.
        match = _fts_query(filters.words, filters.extensions)
        if match is not None:
            sql = ("SELECT o.* FROM occurrences_text t JOIN occurrences o ON o.rowid = t.rowid "
                   f"WHERE t.text MATCH ? AND {' AND '.join(where)} ORDER BY t.rowid LIMIT ? OFFSET ?")
            args.insert(0, match)
        else:
            sql = f"SELECT o.* FROM occurrences o WHERE {' AND '.join(where)} ORDER BY o.rowid LIMIT ? OFFSET ?"
        db = self._db()
        try:
            def fetch(offset, n):
                return db.execute(sql, (*args, n, offset)).fetchall()

            def accept(row):
                if row["root_alias"] not in roots or refs.excluded(roots[row["root_alias"]], row["rel_path"]):
                    return None
                result = envelopes.file_result(self.source_id, dict(row), "catalog",
                                               ("name",) if filters.words else ())
                return result if filters.accepts_metadata(result) else None

            results, next_offset, bounded = _scan(fetch, accept, limit, _offset(cursor))
        finally:
            db.close()
        status = self.status()
        notes = ["catalog: paths, names, types and dates from the last import; no file text"]
        if bounded:
            notes.append(f"stopped after scanning {MAX_SCAN} catalog rows; continue with the cursor")
        return SourcePage(self.source_id, "ok", "catalog", tuple(results),
                          str(next_offset) if next_offset is not None else None, next_offset is not None,
                          scope=status.scope, coverage=status.coverage, notes=tuple(notes))

    def _content(self, filters: Filters, cursor: str | None, limit: int) -> SourcePage:
        from towpath.discovery import service

        offset = _offset(cursor)
        found = service.search(self.config, filters.text, self.provider_id, limit, offset)
        results = []
        for rank, occ in enumerate(found["results"], start=offset + 1):
            result = envelopes.file_result(self.source_id, occ, "content-index", ("text",), provider_rank=rank)
            if occ.get("passage"):
                from dataclasses import replace

                result = replace(result, locator={**result.locator, "passage": occ["passage"]})
            if filters.accepts_metadata(result):
                results.append(result)
        more = found["more_may_exist"]
        status = self.status()
        notes = ["content index: the provider's extracted text; results carry no text (excerpts need a grant)"]
        if filters.extensions or filters.media_types or filters.kinds or filters.after or filters.before \
                or filters.name:
            notes.append("typed filters were applied after the provider's ranking; a page may be short")
        if found.get("stopped"):
            notes.append(f"stopped early: {found['stopped']}")
        return SourcePage(self.source_id, "ok", "content-index", tuple(results),
                          str(offset + found["limit"]) if more else None, more, scope=status.scope,
                          coverage=status.coverage, notes=tuple(notes))

    def search(self, filters: Filters, cursor: str | None, limit: int) -> SourcePage:
        if self.config.files is None or not self.config.files.enabled:
            raise SourceUnavailable("file discovery is not enabled")
        if filters.metadata_only or not self.connect or self.depth != "content-index":
            return self._catalog(filters, cursor, limit)
        return self._content(filters, cursor, limit)

    def describe(self, native: str) -> dict:
        from towpath.discovery import service

        try:
            if self.connect:
                described = service.describe(self.config, native)
                record = described["occurrence"]
                state = {k: described.get(k) for k in ("state", "reason", "provider_version", "source")}
            else:
                record = service.stored_record(self.config, native, "search")
                state = {"state": "not-checked", "reason": "freshness is checked by towpath-connect, not the UI"}
        except service.NotFound as exc:
            raise UnknownReference(str(exc)) from None
        result = envelopes.file_result(self.source_id, record)
        return {"schema": "towpath.describe/0", "ref": str(result.ref), "result": result.to_dict(), **state,
                "content": "excerpts need an excerpt grant on the root; recovery needs a recover grant"}

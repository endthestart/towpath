"""Unified adapters over the sources Towpath already reads.

Two modes, chosen by the caller:

- **local** (any role, including the web UI): reads only the source and files stores, read-only.
  Every page is ``catalog`` depth. No credential is loaded, no provider is contacted, and no
  provider subprocess is started.
- **connect** (``towpath-connect``): may also ask the providers themselves. Gmail answers keyword
  text with ``messages.list q`` through the existing paced client; IMAP with ``SEARCH TEXT`` over a
  read-only session; a files provider with its own content index (for example Recoll).

Metadata-only queries (``extension:nef``) are always answered from the catalogs.
"""

import json

from towpath.stores import open_store
from towpath.unified import envelopes
from towpath.unified.contracts import (
    CoverageSummary,
    Filters,
    Reference,
    SourcePage,
    SourceStatus,
    split_part,
)
from towpath.unified.federation import SourceAdapter, SourceDenied, SourceUnavailable, StaleReference, UnknownReference

READ_ROLE = "web"  # a reader of the source and files stores: opened read-only (mode=ro)
SCAN_BATCH = 200
MAX_SCAN = 5000
FILE_DEPTH = {"recoll": "content-index", "fixture": "content-index", "manifest": "catalog", "sist2": None}


def _scan(fetch, accept, limit: int, offset: int) -> tuple[list, int | None, bool]:
    """Page through raw rows, keeping those ``accept`` turns into results.

    Returns (results, next raw offset or None, scan bound reached). The cursor is a raw-row offset, so
    filtering never makes a capped scan look finished.
    """
    results, position, bounded = [], offset, False
    while True:
        rows = fetch(position, SCAN_BATCH)
        for row in rows:
            position += 1
            result = accept(row)
            if result is None:
                continue
            if len(results) == limit:
                return results, position - 1, False
            results.append(result)
        if len(rows) < SCAN_BATCH:
            return results, None, False
        if position - offset >= MAX_SCAN:
            bounded = True
            return results, position, bounded


def _like(word: str) -> str:
    return "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _any(clause: str, n: int) -> str:
    return "(" + " OR ".join([clause] * n) + ")"


def _offset(cursor: str | None) -> int:
    try:
        value = int(cursor or 0)
    except ValueError:
        raise StaleReference("this cursor does not belong to this source") from None
    if value < 0:
        raise StaleReference("this cursor does not belong to this source")
    return value


# -- mail ---------------------------------------------------------------------------------------------------


class MailAdapter(SourceAdapter):
    """A Gmail or IMAP source: its metadata index, plus provider search in connect mode."""

    def __init__(self, config, source, connect: bool = False):
        self.config, self.source, self.connect = config, source, connect
        self.source_id = source.id
        self.source_type = "imap" if source.adapter == "imap" else "gmail"
        self._connector = None

    def _db(self):
        return open_store(self.config.store_dir, "source", READ_ROLE)

    def connector(self):
        if not self.connect:
            raise SourceUnavailable("provider access needs towpath-connect (the local UI never contacts sources)")
        if self._connector is None:
            from towpath.adapters import build_connector

            self._connector = build_connector(self.source, self.config)
        return self._connector

    def close(self) -> None:
        if self._connector is not None and hasattr(self._connector, "close"):
            self._connector.close()
            self._connector = None

    def status(self) -> SourceStatus:
        db = self._db()
        try:
            runs = db.execute("SELECT * FROM runs WHERE source_id = ? AND kind != 'fetch' ORDER BY seq DESC",
                              (self.source_id,)).fetchall()
            present = db.execute("SELECT COUNT(*) FROM items WHERE source_id = ? AND absent_since_run IS NULL",
                                 (self.source_id,)).fetchone()[0]
            absent = db.execute("SELECT COUNT(*) FROM items WHERE source_id = ? AND absent_since_run IS NOT NULL",
                                (self.source_id,)).fetchone()[0]
        finally:
            db.close()
        latest = runs[0] if runs else None
        success = next((r for r in runs if r["complete"]), None)
        notes = ["message bodies are not indexed locally; keyword text needs provider search (towpath-connect)"]
        if absent:
            notes.append(f"{absent} indexed messages are no longer in the source and are not searched")
        coverage = CoverageSummary({"metadata-cataloged": present}, bool(latest["complete"]) if latest else None,
                                   False, latest["run_id"] if latest else None, tuple(notes))
        provider = "verified" if self.connect else "disabled"
        scope = ({"mailboxes": list(self.source.mailboxes) if self.source.mailboxes else "all selectable"}
                 if self.source_type == "imap" else {"messages": "all labels except spam and trash"})
        return SourceStatus(
            self.source_id, self.source_type, f"{'IMAP' if self.source_type == 'imap' else 'Gmail'} {self.source_id}",
            "ready" if latest else "not-indexed",
            {"catalog-search": "verified", "provider-search": provider, "describe": "verified",
             "selected-content": "verified"},
            ("catalog", "provider-search") if self.connect else ("catalog",), scope, coverage,
            {"last_attempt": latest["started_at"] if latest else None,
             "last_termination": latest["termination"] if latest else None,
             "last_reason": latest["reason"] if latest else None,
             "last_success": success["finished_at"] if success else None},
            None if latest else "not indexed yet: run towpath connect sync")

    # -- catalog -------------------------------------------------------------------------------------------

    def _labels(self, db, item_id: str) -> list | None:
        row = db.execute("SELECT o.labels FROM observations o JOIN runs r ON r.run_id = o.run_id "
                         "WHERE o.item_id = ? ORDER BY r.seq DESC LIMIT 1",
                         (item_id,)).fetchone()
        return json.loads(row["labels"]) if row else None

    def _catalog(self, filters: Filters, cursor: str | None, limit: int) -> SourcePage:
        words = filters.words
        want_messages = ((not filters.kinds or "mail-message" in filters.kinds)
                         and not filters.extensions and not filters.media_types)
        want_parts = not filters.kinds or "mail-part" in filters.kinds
        selects, args = [], []
        if want_messages:
            where = ["i.source_id = ?", "i.absent_since_run IS NULL"]
            args.append(self.source_id)
            for w in words:
                where.append("(lower(coalesce(i.subject, '')) LIKE ? ESCAPE '\\' "
                             "OR lower(coalesce(i.from_addr, '')) LIKE ? ESCAPE '\\')")
                args += [_like(w), _like(w)]
            selects.append("SELECT 'm' AS k, i.item_id, '' AS part_id, coalesce(i.date_utc, '') AS d FROM items i "
                           f"WHERE {' AND '.join(where)}")
        if want_parts:
            where = ["i.source_id = ?", "i.absent_since_run IS NULL", "p.filename IS NOT NULL"]
            args.append(self.source_id)
            for w in words:
                where.append("lower(p.filename) LIKE ? ESCAPE '\\'")
                args.append(_like(w))
            if filters.extensions:
                where.append(_any("lower(p.filename) LIKE ?", len(filters.extensions)))
                args += [f"%.{e}" for e in filters.extensions]
            if filters.media_types:
                where.append(_any("lower(coalesce(p.mime_type, '')) LIKE ?", len(filters.media_types)))
                args += [f"{m}%" for m in filters.media_types]
            selects.append("SELECT 'p' AS k, p.item_id, p.part_id, coalesce(i.date_utc, '') AS d FROM parts p "
                           f"JOIN items i USING (item_id) WHERE {' AND '.join(where)}")
        if not selects:
            return SourcePage(self.source_id, "ok", "catalog", more_may_exist=False,
                              coverage=self.status().coverage, notes=("no mail result kind matches these filters",))
        sql = " UNION ALL ".join(selects) + " ORDER BY d DESC, item_id, part_id LIMIT ? OFFSET ?"
        db = self._db()
        try:
            def fetch(offset, n):
                return db.execute(sql, (*args, n, offset)).fetchall()

            def accept(row):
                item = db.execute("SELECT * FROM items WHERE item_id = ?", (row["item_id"],)).fetchone()
                if row["k"] == "m":
                    matched = tuple(f for f, v in (("subject", item["subject"]), ("from", item["from_addr"]))
                                    if words and any(w in (v or "").lower() for w in words))
                    result = envelopes.mail_result(self.source_id, self.source_type, dict(item), "catalog", matched,
                                                   labels=self._labels(db, item["item_id"]))
                else:
                    part = db.execute("SELECT * FROM parts WHERE item_id = ? AND part_id = ?",
                                      (row["item_id"], row["part_id"])).fetchone()
                    result = envelopes.mail_part_result(self.source_id, self.source_type, dict(item), dict(part),
                                                        "catalog", ("filename",) if words else ())
                return result if filters.accepts_metadata(result) else None

            results, next_offset, bounded = _scan(fetch, accept, limit, _offset(cursor))
        finally:
            db.close()
        notes = ["catalog: subject, sender and attachment names only"]
        if bounded:
            notes.append(f"stopped after scanning {MAX_SCAN} index rows; continue with the cursor")
        return SourcePage(self.source_id, "ok", "catalog", tuple(results),
                          str(next_offset) if next_offset is not None else None, next_offset is not None,
                          scope=self.status().scope, coverage=self.status().coverage, notes=tuple(notes))

    # -- provider search ---------------------------------------------------------------------------------------

    def _from_record(self, db, record: dict) -> tuple[dict, bool]:
        """A row from the local index when it has this message, else a row built from a provider record."""
        row = db.execute("SELECT * FROM items WHERE source_id = ? AND native_id = ?",
                         (self.source_id, record["native_id"])).fetchone()
        if row is not None:
            return dict(row), True
        return {"item_id": None, **{k: record.get(k) for k in ("native_id", "thread_id", "rfc_message_id", "subject",
                                                              "from_addr", "date_header", "date_utc",
                                                              "internal_date", "size_estimate")},
                "absent_since_run": None}, False

    def _expand(self, db, row: dict, parts: list[dict], indexed: bool, rank: int, filters: Filters) -> list:
        """A provider match as a message result, or as its named parts when type filters ask for parts."""
        if filters.extensions or filters.media_types or filters.kinds == ("mail-part",):
            out = []
            for part in parts:
                if part.get("filename"):
                    r = envelopes.mail_part_result(self.source_id, self.source_type, row, part, "provider-search",
                                                   ("provider-match",))
                    if filters.accepts_metadata(r):
                        out.append(r)
            return out
        r = envelopes.mail_result(self.source_id, self.source_type, row, "provider-search", ("provider-match",),
                                  provider_rank=rank, labels=self._labels(db, row["item_id"]) if indexed else None)
        if not indexed:
            from dataclasses import replace

            r = replace(r, coverage="discovered", locator={**r.locator, "indexed_locally": False})
        return [r] if filters.accepts_metadata(r) else []

    def _provider(self, filters: Filters, cursor: str | None, limit: int) -> SourcePage:
        connector = self.connector()
        db = self._db()
        results = []
        try:
            if self.source_type == "gmail":
                found = connector.search(filters.text, cursor, limit)
                estimate = found["estimate"]
                for rank, native in enumerate(found["ids"], start=1):
                    row = db.execute("SELECT * FROM items WHERE source_id = ? AND native_id = ?",
                                     (self.source_id, native)).fetchone()
                    if row is not None:
                        row, indexed = dict(row), True
                        parts = [dict(p) for p in db.execute("SELECT * FROM parts WHERE item_id = ?",
                                                             (row["item_id"],))]
                    else:
                        record = connector.confirm(native)  # listed by Gmail, not yet in the local index
                        if record is None:
                            continue
                        row, indexed = self._from_record(db, record)
                        parts = record["parts"]
                    results += self._expand(db, row, parts, indexed, rank, filters)
                next_cursor = found["next_cursor"]
            else:
                from towpath.adapters.errors import NotFound

                try:
                    found = connector.search(filters.words, cursor, limit)
                except NotFound as exc:
                    raise StaleReference(str(exc)) from None
                except ValueError:
                    raise StaleReference("this cursor does not belong to this source") from None
                estimate = None
                for rank, record in enumerate(found["records"], start=1):
                    row, indexed = self._from_record(db, record)
                    results += self._expand(db, row, record["parts"], indexed, rank, filters)
                next_cursor = found["next_cursor"]
        finally:
            db.close()
        notes = ["provider search: the source matched these; Towpath has not verified a passage"]
        if filters.extensions or filters.media_types or filters.kinds or filters.after or filters.before \
                or filters.name:
            notes.append("typed filters were applied after the provider's own matching; a page may be short")
        return SourcePage(self.source_id, "ok", "provider-search", tuple(results[:limit]), next_cursor,
                          next_cursor is not None,
                          {"value": estimate, "basis": "Gmail resultSizeEstimate (an estimate, not a count)"}
                          if estimate is not None else None,
                          scope=self.status().scope, coverage=self.status().coverage, notes=tuple(notes))

    def search(self, filters: Filters, cursor: str | None, limit: int) -> SourcePage:
        if filters.metadata_only or not self.connect:
            return self._catalog(filters, cursor, limit)
        return self._provider(filters, cursor, limit)

    def describe(self, native: str) -> dict:
        message, part_id = split_part(native)
        db = self._db()
        try:
            row = db.execute("SELECT * FROM items WHERE source_id = ? AND native_id = ?",
                             (self.source_id, message)).fetchone()
            if row is None:
                raise UnknownReference(f"{self.source_id}:{native} is not in the local index")
            parts = [dict(p) for p in db.execute("SELECT * FROM parts WHERE item_id = ? ORDER BY part_id",
                                                 (row["item_id"],))]
            labels = self._labels(db, row["item_id"])
            fetches = {(f["part_id"]): dict(f) for f in db.execute(
                "SELECT * FROM fetches WHERE item_id = ? ORDER BY at", (row["item_id"],))}
        finally:
            db.close()
        if part_id is not None and part_id not in {p["part_id"] for p in parts}:
            raise UnknownReference(f"{self.source_id}:{native}: no such part")
        result = envelopes.mail_result(self.source_id, self.source_type, dict(row), labels=labels)
        if part_id is not None:
            part = next(p for p in parts if p["part_id"] == part_id)
            result = envelopes.mail_part_result(self.source_id, self.source_type, dict(row), part)
        return {
            "schema": "towpath.describe/0", "ref": str(Reference(self.source_id, native)), "result": result.to_dict(),
            "state": "absent" if row["absent_since_run"] else "present", "as_of_run": row["last_seen_run"],
            "parts": [{**{k: p[k] for k in ("part_id", "mime_type", "charset", "filename", "disposition", "size",
                                            "sha256")},
                       "ref": f"{self.source_id}:{message}#part={p['part_id']}",
                       "fetch": fetches.get(p["part_id"], {}).get("status"),
                       "selected": p["part_id"] == part_id} for p in parts],
            "content": "selected content is read only on request, through the queue and towpath-connect",
        }


# -- files --------------------------------------------------------------------------------------------------


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
                for row in db.execute("SELECT extraction FROM occurrences WHERE provider_id = ? AND root_alias = ? "
                                      "AND missing_since_run IS NULL", (self.provider_id, alias)):
                    status = json.loads(row["extraction"]).get("status")
                    key = envelopes.FILE_COVERAGE.get(status, "unknown")
                    counts[key] = counts.get(key, 0) + 1
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
        where = ["provider_id = ?", "missing_since_run IS NULL",
                 f"root_alias IN ({','.join('?' for _ in granted)})"]
        args: list = [self.provider_id, *granted]
        for w in filters.words:
            where.append("lower(rel_path || ' ' || members) LIKE ? ESCAPE '\\'")
            args.append(_like(w))
        if filters.extensions:
            where.append(_any("lower(rel_path || ' ' || members) LIKE ?", len(filters.extensions)))
            args += [f"%.{e}%" for e in filters.extensions]
        if filters.media_types:
            where.append(_any("lower(coalesce(media_type, '')) LIKE ?", len(filters.media_types)))
            args += [f"{m}%" for m in filters.media_types]
        sql = (f"SELECT * FROM occurrences WHERE {' AND '.join(where)} "
               "ORDER BY root_alias, rel_path, occurrence_id LIMIT ? OFFSET ?")
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


def build_adapters(config, connect: bool = False) -> dict[str, SourceAdapter]:
    """Every configured mail source and files provider, in configuration order."""
    adapters: dict[str, SourceAdapter] = {}
    for source in config.sources.values():
        if source.kind == "mail-provider":
            adapters[source.id] = MailAdapter(config, source, connect)
    if config.files is not None:
        for provider_config in config.files.providers.values():
            adapter = FilesAdapter(config, provider_config, connect)
            if adapter.source_id in adapters:
                raise ValueError(f"source id {adapter.source_id} is used by both a mail source and a files provider")
            adapters[adapter.source_id] = adapter
    return adapters


def store_adapters(store_dir) -> dict[str, SourceAdapter]:
    """Mail sources known to the source store, for a UI started without a configuration (local mode only)."""
    from types import SimpleNamespace

    config = SimpleNamespace(store_dir=store_dir, files=None, sources={})
    adapters: dict[str, SourceAdapter] = {}
    db = open_store(store_dir, "source", READ_ROLE)
    try:
        rows = db.execute("SELECT source_id, adapter, descriptor FROM sources WHERE kind = 'mail-provider' "
                          "ORDER BY source_id").fetchall()
    finally:
        db.close()
    for row in rows:
        try:
            descriptor = json.loads(row["descriptor"] or "{}")
        except ValueError:
            descriptor = {}
        boxes = descriptor.get("mailboxes")
        source = SimpleNamespace(id=row["source_id"], adapter=row["adapter"], kind="mail-provider",
                                 mailboxes=tuple(boxes) if isinstance(boxes, list) else None)
        try:
            adapters[row["source_id"]] = MailAdapter(config, source, connect=False)
        except Exception:  # noqa: BLE001 - a malformed source id is skipped, not fatal
            continue
    return adapters

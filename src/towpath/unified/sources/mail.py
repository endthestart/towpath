"""A Gmail or IMAP source as a unified adapter."""

import json

from towpath.stores import open_store
from towpath.unified import envelopes
from towpath.unified.contracts import CoverageSummary, Filters, Reference, SourcePage, SourceStatus, split_part
from towpath.unified.federation import SourceAdapter, SourceUnavailable, StaleReference, UnknownReference
from towpath.unified.sources.common import (
    MAX_SCAN, MAX_TEXT_IDS, READ_ROLE, _any, _expansion_cursor, _labels, _like, _offset, _scan,
)


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
            present, absent = db.execute("SELECT count(*) - count(absent_since_run), count(absent_since_run) "
                                         "FROM items WHERE source_id = ?", (self.source_id,)).fetchone()
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

    def _text_matches(self, words: list[str]) -> list[str]:
        """Messages whose text (as mail management read it) has every word, newest first, at most MAX_TEXT_IDS."""
        from towpath.mail import search as mail_search

        if not words:
            return []
        if getattr(self.source, "adapter", None) in ("facebook", "imessage"):  # conversation text from an export
            from towpath import export_text

            return export_text.search(self.config.store_dir, self.source_id, words, MAX_TEXT_IDS, READ_ROLE)
        try:
            found = mail_search.search(self.config.store_dir, self.source_id, " ".join(words), limit=MAX_TEXT_IDS,
                                       role=READ_ROLE)
        except mail_search.QueryError:  # a word that looks like an operator mail search doesn't take
            return []
        return [r["item_id"] for r in found]

    def _catalog(self, filters: Filters, cursor: str | None, limit: int) -> SourcePage:
        words = filters.words
        text_ids = self._text_matches(words) if words else []
        want_messages = ((not filters.kinds or "mail-message" in filters.kinds)
                         and not filters.extensions and not filters.media_types)
        want_parts = not filters.kinds or "mail-part" in filters.kinds
        selects, args = [], []
        if want_messages:
            where = ["i.source_id = ?", "i.absent_since_run IS NULL"]
            args.append(self.source_id)
            header = []
            for w in words:
                header.append("(lower(coalesce(i.subject, '')) LIKE ? ESCAPE '\\' "
                              "OR lower(coalesce(i.from_addr, '')) LIKE ? ESCAPE '\\')")
                args += [_like(w), _like(w)]
            if header and text_ids:  # every word in the subject or sender, or every word in the message's text
                where.append(f"(({' AND '.join(header)}) OR i.item_id IN ({','.join('?' for _ in text_ids)}))")
                args += text_ids
            else:
                where += header
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
                    if item["item_id"] in text_ids:
                        matched += ("text",)
                    result = envelopes.mail_result(self.source_id, self.source_type, dict(item), "catalog", matched,
                                                   labels=_labels(db, item["item_id"]))
                else:
                    part = db.execute("SELECT * FROM parts WHERE item_id = ? AND part_id = ?",
                                      (row["item_id"], row["part_id"])).fetchone()
                    result = envelopes.mail_part_result(self.source_id, self.source_type, dict(item), dict(part),
                                                        "catalog", ("filename",) if words else ())
                return result if filters.accepts_metadata(result) else None

            results, next_offset, bounded = _scan(fetch, accept, limit, _offset(cursor))
        finally:
            db.close()
        notes = ["catalog: subject, sender and attachment names" +
                 (", and message text where mail management has read it" if text_ids else " only")]
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
                                  provider_rank=rank, labels=_labels(db, row["item_id"]) if indexed else None)
        if not indexed:
            from dataclasses import replace

            r = replace(r, coverage="discovered", locator={**r.locator, "indexed_locally": False})
        return [r] if filters.accepts_metadata(r) else []

    def _provider_page(self, connector, db, filters: Filters, token, limit: int) -> tuple[list, str | None, int | None]:
        """One provider page, expanded into results (messages, or their matching parts), in a stable order."""
        results = []
        if self.source_type == "gmail":
            found = connector.search(filters.text, token, limit)
            for rank, native in enumerate(found["ids"], start=1):
                row = db.execute("SELECT * FROM items WHERE source_id = ? AND native_id = ?",
                                 (self.source_id, native)).fetchone()
                if row is not None:
                    row, indexed = dict(row), True
                    parts = [dict(p) for p in db.execute("SELECT * FROM parts WHERE item_id = ? ORDER BY part_id",
                                                         (row["item_id"],))]
                else:
                    record = connector.confirm(native)  # listed by Gmail, not yet in the local index
                    if record is None:
                        continue
                    row, indexed = self._from_record(db, record)
                    parts = record["parts"]
                results += self._expand(db, row, parts, indexed, rank, filters)
            return results, found["next_cursor"], found["estimate"]
        from towpath.adapters.errors import NotFound

        try:
            found = connector.search(filters.words, token, limit)
        except NotFound as exc:
            raise StaleReference(str(exc)) from None
        except ValueError:
            raise StaleReference("this cursor does not belong to this source") from None
        for rank, record in enumerate(found["records"], start=1):
            row, indexed = self._from_record(db, record)
            results += self._expand(db, row, record["parts"], indexed, rank, filters)
        return results, found["next_cursor"], None

    def _provider(self, filters: Filters, cursor: str | None, limit: int) -> SourcePage:
        """Provider search, one provider page per call.

        A provider page can expand into more results than fit (a message with several matching
        parts), so the continuation is ``{"page": <provider token of this page>, "after": <last ref
        returned>}``: the next call asks the provider for the same page and resumes after that
        reference. If the provider no longer returns it there, the search stops as stale rather than
        skipping or repeating results.
        """
        state = _expansion_cursor(cursor)
        connector = self.connector()
        db = self._db()
        try:
            expanded, next_token, estimate = self._provider_page(connector, db, filters, state["page"], limit)
        finally:
            db.close()
        if state["after"] is not None:
            refs = [str(r.ref) for r in expanded]
            if state["after"] not in refs:
                raise StaleReference("the provider's answer for this page changed since the last page; search again")
            expanded = expanded[refs.index(state["after"]) + 1:]
        if len(expanded) > limit:
            results = expanded[:limit]
            next_cursor = json.dumps({"page": state["page"], "after": str(results[-1].ref)}, sort_keys=True)
        else:
            results = expanded
            next_cursor = json.dumps({"page": next_token, "after": None}, sort_keys=True) if next_token else None
        notes = ["provider search: the source matched these; Towpath has not verified a passage"]
        if filters.extensions or filters.media_types or filters.kinds or filters.after or filters.before \
                or filters.name:
            notes.append("typed filters were applied after the provider's own matching; a page may be short")
        return SourcePage(self.source_id, "ok", "provider-search", tuple(results), next_cursor,
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
            labels = _labels(db, row["item_id"])
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

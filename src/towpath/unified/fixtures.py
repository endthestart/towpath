"""Synthetic sources for the unified contracts: invented mail, files, and coverage states.

A test and demonstration aid. Matching is case-insensitive substring over each record's synthetic
searchable text and is no search engine. Every name, address and date is invented and uses reserved
domains (example.com, example.org, example.net).
"""

from dataclasses import replace

from towpath.unified.contracts import (
    CoverageSummary,
    Filters,
    Match,
    Reference,
    Result,
    SourcePage,
    SourceStatus,
    TypedDate,
    extension_of,
    imap_native,
    part_native,
)
from towpath.unified.federation import SourceAdapter, SourceUnavailable

GMAIL_RECORDS = [
    {"native": "fx-g-0001", "title": "Towpath survey notes for the canal society",
     "from": "surveyor@example.org", "date": "2009-04-02T10:15:00+00:00", "labels": ["INBOX"],
     "body": "The lock keeper counted forty narrowboats near the aqueduct.",
     "parts": [{"part_id": "0", "mime_type": "text/plain", "filename": None, "size": 61}]},
    {"native": "fx-g-0002", "title": "Photos from the aqueduct walk", "from": "walker@example.net",
     "date": "2011-08-20T18:40:00+00:00", "labels": ["INBOX"], "body": "Raw files attached from the walk.",
     "parts": [{"part_id": "0", "mime_type": "text/plain", "filename": None, "size": 33},
               {"part_id": "1", "mime_type": "image/x-nikon-nef", "filename": "DSC_0042.NEF", "size": 24117248}]},
]

IMAP_RECORDS = [
    {"mailbox": "INBOX", "uidvalidity": 1700000001, "uid": 7, "title": "Re: aqueduct restoration budget",
     "from": "treasurer@example.com", "date": "2010-02-11T09:00:00+00:00",
     "body": "The restoration estimate for the aqueduct is attached as a spreadsheet.",
     "flags": ["\\Seen"]},
    {"mailbox": "Archive/2008", "uidvalidity": 1700000002, "uid": 3, "title": "Canal boat club minutes",
     "from": "secretary@example.com", "date": "2008-06-30T20:00:00+00:00",
     "body": "Minutes of the summer meeting. The towpath fundraiser raised a modest sum.", "flags": []},
]

FILE_RECORDS = [
    {"native": "occ_fx_nef_0001", "root": "photos", "path": "2011/aqueduct/DSC_0042.NEF",
     "media_type": "image/x-nikon-nef", "size": 24117248, "status": "unsupported", "text": None,
     "dates": [("file-modified", "2011-08-20T18:31:00+00:00")]},
    {"native": "occ_fx_nef_0002", "root": "photos", "path": "2011/aqueduct/DSC_0043.nef",
     "media_type": "image/x-nikon-nef", "size": 23980032, "status": "unsupported", "text": None,
     "dates": [("file-modified", "2011-08-20T18:32:00+00:00")]},
    {"native": "occ_fx_txt_0003", "root": "archive", "path": "notes/towpath-survey.txt",
     "media_type": "text/plain", "size": 2048, "status": "indexed",
     "text": "Survey of the towpath between the aqueduct and the third lock.",
     "dates": [("file-modified", "2009-04-03T08:00:00+00:00")]},
    {"native": "occ_fx_zip_0004", "root": "archive", "path": "2003/old-mail.zip",
     "members": [["archive-member", "mail/backup.mbox", None], ["mail-message", None, 2],
                 ["attachment", "paper.docx", None]],
     "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "size": 4096,
     "status": "indexed", "text": "Canal towpaths and the regional market of 1830.",
     "dates": [("member-modified", "2004-01-01T12:00:00+00:00")]},
    {"native": "occ_fx_pst_0005", "root": "archive", "path": "outlook/old.pst",
     "media_type": "application/vnd.ms-outlook", "size": 1048576, "status": "unreadable", "text": None,
     "dates": [("file-modified", "2004-01-02T00:00:00+00:00")]},
]


def _match_text(filters: Filters, haystack: str, fields: tuple[str, ...]) -> tuple[bool, tuple[str, ...]]:
    if filters.metadata_only:
        return True, ()
    text = haystack.lower()
    return all(w in text for w in filters.words), fields


class StaticAdapter(SourceAdapter):
    """A source answering from synthetic records.

    ``mode``: ``ok`` (complete inventory, confirmed end of results), ``partial`` (inventory still
    running: more may exist, absence cannot be established), or ``unavailable`` (every call fails).
    """

    def __init__(self, status: SourceStatus, rows: list[tuple[Result, str]], depth: str, mode: str = "ok"):
        self.source_id, self.source_type = status.source_id, status.source_type
        self._status, self._rows, self.depth, self.mode = status, rows, depth, mode

    def status(self) -> SourceStatus:
        return self._status

    def search(self, filters: Filters, cursor: str | None, limit: int) -> SourcePage:
        if self.mode == "unavailable":
            raise SourceUnavailable("synthetic source is configured to be unreachable")
        fields = ("subject", "body") if self.source_type in {"gmail", "imap"} else ("name", "text")
        depth = "catalog" if filters.metadata_only else self.depth
        hits = []
        for result, haystack in self._rows:
            ok, matched = _match_text(filters, haystack, fields)
            if ok and filters.accepts_metadata(result):
                hits.append(replace(result, match=Match(depth, matched)))
        start = int(cursor or 0)
        page = hits[start:start + limit]
        more = start + limit < len(hits)
        partial = self.mode == "partial"
        return SourcePage(self.source_id, "partial" if partial else "ok", depth, tuple(page),
                          next_cursor=str(start + limit) if more else None,
                          more_may_exist=True if (more or partial) else False,
                          scope=self._status.scope, coverage=self._status.coverage,
                          notes=("synthetic fixture source",))

    def describe(self, native: str) -> dict:
        for result, _ in self._rows:
            if result.ref.native == native:
                return {"result": result.to_dict(), "state": "current", "synthetic": True}
        raise LookupError(native)


def _mail_dates(value: str) -> tuple[TypedDate, ...]:
    return (TypedDate("message-date", value, "instant", "Date header"),)


def gmail_adapter(source_id: str = "gmail-fixture", mode: str = "ok") -> StaticAdapter:
    rows = []
    for rec in GMAIL_RECORDS:
        legacy = {"native_id": rec["native"], "subject": rec["title"], "from_addr": rec["from"],
                  "date_utc": rec["date"], "labels": rec["labels"], "parts": rec["parts"]}
        result = Result(source_id, "gmail", Reference(source_id, rec["native"]), "mail-message", rec["title"],
                        Match("catalog"), version=None, dates=_mail_dates(rec["date"]),
                        locator={"message_id": rec["native"], "labels": rec["labels"]}, coverage="metadata-cataloged",
                        restrictions={"content": "selected-content-request"}, legacy=legacy)
        rows.append((result, f"{rec['title']}\n{rec['body']}"))
        for part in rec["parts"]:
            if part["filename"]:
                ref = Reference(source_id, part_native(rec["native"], part["part_id"]))
                rows.append((Result(source_id, "gmail", ref, "mail-part", part["filename"], Match("catalog"),
                                    dates=_mail_dates(rec["date"]),
                                    locator={"message_id": rec["native"], "part_id": part["part_id"]},
                                    media_type=part["mime_type"], size=part["size"],
                                    extension=extension_of(part["filename"]), coverage="metadata-cataloged",
                                    legacy={"item": rec["native"], "part": part}), part["filename"]))
    cov = CoverageSummary({"metadata-cataloged": len(GMAIL_RECORDS)}, True, False,
                          notes=("message bodies are not indexed locally; keyword text uses provider search",))
    status = SourceStatus(source_id, "gmail", "Synthetic Gmail account", "ready",
                          {"catalog-search": "verified", "provider-search": "verified", "describe": "verified",
                           "selected-content": "verified"}, ("catalog", "provider-search"),
                          {"labels": "all", "history": "full"}, cov)
    return StaticAdapter(status, rows, "provider-search", mode)


def imap_adapter(source_id: str = "imap-fixture", mode: str = "ok") -> StaticAdapter:
    rows = []
    for rec in IMAP_RECORDS:
        native = imap_native(rec["mailbox"], rec["uidvalidity"], rec["uid"])
        legacy = {"native_id": native, "subject": rec["title"], "from_addr": rec["from"], "date_utc": rec["date"],
                  "labels": [rec["mailbox"], *rec["flags"]]}
        result = Result(source_id, "imap", Reference(source_id, native), "mail-message", rec["title"],
                        Match("provider-search"), version=f"UIDVALIDITY={rec['uidvalidity']}",
                        dates=_mail_dates(rec["date"]),
                        locator={"mailbox": rec["mailbox"], "uidvalidity": rec["uidvalidity"], "uid": rec["uid"]},
                        coverage="metadata-cataloged", legacy=legacy)
        rows.append((result, f"{rec['title']}\n{rec['body']}"))
    cov = CoverageSummary({"metadata-cataloged": len(IMAP_RECORDS)}, True, False)
    status = SourceStatus(source_id, "imap", "Synthetic IMAP account", "ready",
                          {"catalog-search": "verified", "provider-search": "verified", "describe": "verified"},
                          ("catalog", "provider-search"), {"mailboxes": ["INBOX", "Archive/2008"]}, cov)
    return StaticAdapter(status, rows, "provider-search", mode)


def files_adapter(source_id: str = "files-fixture", mode: str = "partial") -> StaticAdapter:
    rows = []
    for rec in FILE_RECORDS:
        members = rec.get("members", [])
        name = (members[-1][1] if members and members[-1][1] else rec["path"].rsplit("/", 1)[-1])
        coverage = {"indexed": "text-extracted", "unsupported": "unsupported",
                    "unreadable": "unreadable"}[rec["status"]]
        legacy = {"occurrence_id": rec["native"], "root_alias": rec["root"], "rel_path": rec["path"],
                  "members": [{"kind": k, "name": n, "index": i} for k, n, i in members],
                  "extraction": {"status": rec["status"]}}
        result = Result(source_id, "files", Reference(source_id, rec["native"]),
                        "archive-member" if members else "file", name, Match("catalog"),
                        version="size={};mtime=fixture".format(rec["size"]),
                        dates=tuple(TypedDate(m, v, "instant", "provider metadata") for m, v in rec["dates"]),
                        locator={"root": rec["root"], "path": rec["path"], "members": legacy["members"]},
                        media_type=rec["media_type"], size=rec["size"], extension=extension_of(name),
                        coverage=coverage, legacy=legacy)
        rows.append((result, f"{rec['path']} {name}\n{rec['text'] or ''}"))
    cov = CoverageSummary({"text-extracted": 2, "unsupported": 2, "unreadable": 1, "pending": 40}, False, False,
                          notes=("indexing is still running: the inventory is not complete",))
    status = SourceStatus(source_id, "files", "Synthetic NAS roots", "ready",
                          {"catalog-search": "verified", "content-search": "verified", "describe": "verified",
                           "excerpt": "verified"}, ("catalog", "content-index"), {"roots": ["archive", "photos"]}, cov)
    return StaticAdapter(status, rows, "content-index", mode)


def demo_adapters(files_mode: str = "partial", imap_mode: str = "ok", gmail_mode: str = "ok") -> dict:
    """Three synthetic sources: Gmail, IMAP, and partially indexed files, each mode configurable."""
    adapters = [gmail_adapter(mode=gmail_mode), imap_adapter(mode=imap_mode), files_adapter(mode=files_mode)]
    return {a.source_id: a for a in adapters}

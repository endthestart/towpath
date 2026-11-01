"""The sync engine's interface to an IMAP account."""

import base64
import binascii
import json
import quopri
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.parser import BytesHeaderParser
from email.utils import parseaddr, parsedate_to_datetime

from towpath.adapters.errors import NotFound, RequestStop, Unfetchable
from towpath.adapters.imap.session import FETCH_BATCH, MAX_PART_BYTES, METADATA_ITEMS, ReadOnlySession, _guarded
from towpath.adapters.imap.structure import flatten_structure
from towpath.unified.contracts import ContractError, imap_native, parse_imap_native


class ImapConnector:
    kind = "mail-provider"

    def __init__(self, source_id: str, session_factory, mailboxes: tuple[str, ...] | None = None,
                 batch: int = FETCH_BATCH):
        self.source_id = source_id
        self._factory = session_factory
        self._session = None
        self.configured = tuple(mailboxes) if mailboxes else None
        self.batch = batch
        self._listed: dict[str, tuple[int, set[int]]] = {}  # mailbox -> (uidvalidity, uids) seen this run
        self._present: list[str] | None = None

    @property
    def session(self) -> ReadOnlySession:
        if self._session is None:
            self._session = self._factory()
        return self._session

    def close(self) -> None:
        if self._session is not None:
            self._session.logout()
            self._session = None

    def describe(self) -> dict:
        return {"schema": "towpath.source/0", "source_id": self.source_id, "kind": self.kind,
                "connector": "imap/0", "authority": "read",
                "capabilities": ["enumerate", "fetch_part", "provider_search"], "retention": "on-demand",
                "mailboxes": list(self.configured) if self.configured else "all"}

    @_guarded
    def probe(self) -> dict:
        boxes = self.session.mailboxes()
        missing = [m for m in (self.configured or ()) if m not in boxes]
        return {"ok": not missing, "capabilities": self.session.capabilities(), "mailboxes": len(boxes),
                "missing_mailboxes": len(missing)}

    def _mailboxes(self) -> list[str]:
        """Configured mailboxes that LIST still shows, or every selectable mailbox."""
        if self._present is None:
            listed = self.session.mailboxes()
            self._present = [m for m in self.configured if m in listed] if self.configured else sorted(listed)
        return self._present

    def _record(self, mailbox: str, uidvalidity: int, uid: int, data: dict) -> dict:
        problems = []
        raw_headers = next((v for k, v in data.items() if k.startswith(b"BODY[HEADER.FIELDS")), b"") or b""
        headers = BytesHeaderParser().parsebytes(raw_headers)
        date_header, date_utc = headers.get("Date"), None
        if date_header:
            try:
                date_utc = parsedate_to_datetime(date_header).astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError):
                problems.append("invalid date header")
        else:
            problems.append("missing date header")
        subject = headers.get("Subject")
        if subject:
            try:
                subject = str(make_header(decode_header(subject)))
            except (LookupError, ValueError):
                problems.append("undecodable subject")
        internal = data.get(b"INTERNALDATE")
        internal_ms = str(int(internal.timestamp() * 1000)) if isinstance(internal, datetime) else None
        try:
            parts = flatten_structure(data.get(b"BODYSTRUCTURE"))
        except (IndexError, TypeError, ValueError):
            parts, truncated = [], True
            problems.append("invalid body structure")
        else:
            truncated = data.get(b"BODYSTRUCTURE") is None
        for part in parts:
            part.pop("encoding", None)
        return {
            "native_id": imap_native(mailbox, uidvalidity, uid), "thread_id": None, "labels": [mailbox],
            "internal_date": internal_ms, "size_estimate": data.get(b"RFC822.SIZE"),
            "rfc_message_id": headers.get("Message-ID"), "subject": subject,
            "from_addr": parseaddr(headers.get("From") or "")[1] or None, "date_header": date_header,
            "date_utc": date_utc, "parts": parts, "structure_truncated": truncated, "inline_data_discarded": 0,
            "problems": problems,
        }

    def _read(self, mailbox: str, uidvalidity: int, uids: list[int]):
        for start in range(0, len(uids), self.batch):
            chunk = uids[start:start + self.batch]
            rows = self.session.fetch(chunk, METADATA_ITEMS)
            for uid in chunk:
                if uid in rows:  # absent: expunged between SEARCH and FETCH
                    yield self._record(mailbox, uidvalidity, uid, rows[uid])

    @_guarded
    def enumerate(self, cursor: str | None, full_state: dict | None = None, already_seen: set[str] | None = None,
                  **_):
        """List every mailbox's UIDs and read metadata for those not yet indexed.

        Every run is a full listing (UID SEARCH ALL is cheap); only new UIDs are fetched. The cursor
        records each mailbox's UIDVALIDITY and highest indexed UID, checkpointed after each mailbox.
        Indexed items the listing no longer shows go to the engine's confirm step.
        """
        state = json.loads(cursor) if cursor else {"mailboxes": {}}
        if not full_state:
            yield ("full_start", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        seen = set(already_seen or ())
        for mailbox in self._mailboxes():
            info = self.session.examine(mailbox)
            uidvalidity = info["uidvalidity"]
            uids = self.session.search_all()
            self._listed[mailbox] = (uidvalidity, set(uids))
            previous = state["mailboxes"].get(mailbox)
            floor = previous["last_uid"] if previous and previous["uidvalidity"] == uidvalidity else 0
            wanted = [u for u in uids if u > floor and imap_native(mailbox, uidvalidity, u) not in seen]
            for record in self._read(mailbox, uidvalidity, wanted):
                yield ("item", record)
            yield ("listed", "reconcile", [imap_native(mailbox, uidvalidity, u) for u in uids])
            state["mailboxes"][mailbox] = {"uidvalidity": uidvalidity, "last_uid": max(uids or [floor])}
            yield ("checkpoint", json.dumps(state, sort_keys=True))
        yield ("confirm", None)
        yield ("done", {"kind": "full", "cursor": json.dumps(state, sort_keys=True), "complete": True})

    @_guarded
    def confirm(self, native_id: str) -> dict | None:
        """Re-check one indexed message the listing did not show. None means it is gone from this identity."""
        try:
            mailbox, uidvalidity, uid = parse_imap_native(native_id)
        except ContractError:
            return None
        if mailbox not in self._mailboxes():
            return None  # the mailbox is gone from LIST (deleted or renamed): this identity no longer exists
        if mailbox in self._listed:
            current, uids = self._listed[mailbox]
            if current != uidvalidity or uid not in uids:
                return None
        info = self.session.examine(mailbox)
        if info["uidvalidity"] != uidvalidity:
            return None
        rows = self.session.fetch([uid], METADATA_ITEMS)
        return self._record(mailbox, uidvalidity, uid, rows[uid]) if uid in rows else None

    # -- provider search and selected content ---------------------------------------------------------

    @_guarded
    def search(self, words: list[str], cursor: str | None, limit: int) -> dict:
        """Server-side keyword search (IMAP SEARCH TEXT), newest UID first, one page at a time.

        The cursor pins the mailbox position and UIDVALIDITY; if UIDVALIDITY changes between pages
        the search stops rather than mixing identities. Matches are whatever the server's SEARCH
        implementation finds; Towpath does not verify a passage.
        """
        if not words:
            raise RequestStop("provider search needs keyword text")
        position = json.loads(cursor) if cursor else {"m": 0, "v": None, "b": None}
        mailboxes = self._mailboxes()
        page: list[tuple[int, dict]] = []
        more, index = False, position["m"]
        while index < len(mailboxes) and not more:
            mailbox = mailboxes[index]
            uidvalidity = self.session.examine(mailbox)["uidvalidity"]
            before = None
            if position["v"] is not None and index == position["m"]:
                if uidvalidity != position["v"]:
                    raise NotFound("the mailbox identity (UIDVALIDITY) changed during paging; search again")
                before = position["b"]
            uids = sorted((u for u in self.session.search_text(words) if before is None or u < before),
                          reverse=True)
            done = 0
            while done < len(uids):
                if len(page) == limit:
                    more = True
                    break
                chunk = uids[done:done + limit - len(page)]
                page += [(index, r) for r in self._read(mailbox, uidvalidity, chunk)]
                done += len(chunk)
            index += 1
        next_cursor = None
        if more:
            last_index, last = page[-1]
            _, uidvalidity, uid = parse_imap_native(last["native_id"])
            next_cursor = json.dumps({"m": last_index, "v": uidvalidity, "b": uid})
        return {"records": [r for _, r in page], "next_cursor": next_cursor, "more": more}

    @_guarded
    def fetch(self, native_id: str, part_id: str) -> bytes:
        """One part's decoded bytes, read with BODY.PEEK so the message's flags do not change."""
        mailbox, uidvalidity, uid = parse_imap_native(native_id)
        if mailbox not in self._mailboxes() or self.session.examine(mailbox)["uidvalidity"] != uidvalidity:
            raise NotFound(f"{native_id}: this mailbox identity no longer exists")
        rows = self.session.fetch([uid], ("BODYSTRUCTURE",))
        if uid not in rows:
            raise NotFound(native_id)
        parts = {p["part_id"]: p for p in flatten_structure(rows[uid][b"BODYSTRUCTURE"])}
        if part_id not in parts:
            raise NotFound(f"{native_id}/{part_id}")
        if parts[part_id]["size"] > MAX_PART_BYTES:
            raise Unfetchable(f"part is larger than {MAX_PART_BYTES} bytes; not fetched")
        item = f"BODY.PEEK[{part_id}]"
        data = self.session.fetch([uid], (item,)).get(uid, {})
        raw = data.get(f"BODY[{part_id}]".encode())
        if raw is None:
            raise NotFound(f"{native_id}/{part_id}")
        encoding = parts[part_id]["encoding"]
        try:
            if encoding == "base64":
                return base64.b64decode(raw, validate=False)
            if encoding == "quoted-printable":
                return quopri.decodestring(raw)
        except (binascii.Error, ValueError):
            raise Unfetchable("the part's transfer encoding could not be decoded") from None
        return raw

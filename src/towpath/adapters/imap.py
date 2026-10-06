"""Read-only IMAP connector, built on IMAPClient (BSD-3-Clause; see docs/evaluations/imap-client.md).

Read-only by construction:

- Towpath talks to the server only through :class:`ReadOnlySession`, which exposes LOGIN, CAPABILITY,
  LIST, EXAMINE, UID SEARCH (ALL, or TEXT with the query words as values), UID FETCH and LOGOUT.
  The IMAPClient object, with its STORE, COPY, MOVE, EXPUNGE, APPEND and folder commands, is never
  handed out.
- Mailboxes are opened with EXAMINE (RFC 9051 section 6.3.3). A server that answers EXAMINE with
  READ-WRITE is refused.
- FETCH items are checked against an allow-list. Body sections must use ``BODY.PEEK``, which does
  not set ``\\Seen`` (RFC 9051 section 6.4.5); ``BODY[...]``, ``RFC822`` and ``RFC822.TEXT`` are refused.

Identity is mailbox + UIDVALIDITY + UID (RFC 9051 section 2.3.1.1). When UIDVALIDITY changes,
old references are not mapped onto new UIDs: the mailbox is listed afresh, and the old items are
marked absent by the sync engine's confirm step only after this connector reports them gone.
"""

import base64
import binascii
import functools
import inspect
import json
import quopri
import re
import socket
from contextlib import contextmanager
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.parser import BytesHeaderParser
from email.utils import collapse_rfc2231_value, parseaddr, parsedate_to_datetime

from towpath.adapters.errors import AuthStop, NotFound, RequestStop, ServerStop, Unfetchable
from towpath.unified.contracts import ContractError, imap_native, parse_imap_native

HEADER_ITEM = "BODY.PEEK[HEADER.FIELDS (DATE SUBJECT FROM MESSAGE-ID)]"
METADATA_ITEMS = ("RFC822.SIZE", "INTERNALDATE", "BODYSTRUCTURE", HEADER_ITEM)
ALLOWED_FETCH = re.compile(
    r"^(UID|FLAGS|INTERNALDATE|RFC822\.SIZE|BODYSTRUCTURE|ENVELOPE|BODY\.PEEK\[[A-Z0-9 .()\-]*\](<\d+\.\d+>)?)$")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
FETCH_BATCH = 100
MAX_PART_BYTES = 50_000_000


class ReadOnlyViolation(RuntimeError):
    """Towpath was about to issue, or the server granted, something other than read-only access."""


def _text(value) -> str | None:
    if value is None:
        return None
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


class ReadOnlySession:
    """The only IMAP operations Towpath performs."""

    def __init__(self, client):
        self._client = client
        client.normalise_times = False  # keep the server's time zone instead of converting to local time

    def login(self, username: str, password: str) -> None:
        self._client.login(username, password)

    def capabilities(self) -> list[str]:
        return sorted(_text(c) for c in self._client.capabilities())

    def mailboxes(self) -> list[str]:
        """Selectable mailboxes from LIST."""
        out = []
        for flags, _, name in self._client.list_folders():
            if any(_text(f).lower() in {"\\noselect", "\\nonexistent"} for f in flags):
                continue
            out.append(_text(name))
        return out

    def examine(self, mailbox: str) -> dict:
        info = self._client.select_folder(mailbox, readonly=True)
        if b"READ-WRITE" in info:
            raise ReadOnlyViolation("the server opened an EXAMINEd mailbox read-write; refusing to continue")
        return {"uidvalidity": int(info[b"UIDVALIDITY"]), "uidnext": info.get(b"UIDNEXT"),
                "exists": info.get(b"EXISTS")}

    def search_all(self) -> list[int]:
        return sorted(int(u) for u in self._client.search(["ALL"]))

    def search_text(self, words: list[str]) -> list[int]:
        """UID SEARCH TEXT for every word (AND). Words are always values, never search keys."""
        criteria: list = []
        for word in words:
            criteria += ["TEXT", word]
        charset = None if all(w.isascii() for w in words) else "UTF-8"
        return sorted(int(u) for u in self._client.search(criteria, charset=charset))

    def fetch(self, uids: list[int], items: tuple[str, ...]) -> dict:
        for item in items:
            if not ALLOWED_FETCH.match(item):
                raise ReadOnlyViolation(f"FETCH item {item!r} could change message state; only PEEK reads are used")
        return self._client.fetch(uids, list(items)) if uids else {}

    def logout(self) -> None:
        try:
            self._client.logout()
        except Exception:  # noqa: BLE001 - a failed goodbye changes nothing on the server
            pass


def connect_session(source) -> ReadOnlySession:
    """Open, secure and log in. The password is resolved here and kept nowhere."""
    from imapclient import IMAPClient

    from towpath import credentials

    if source.security == "plain-loopback" and source.host not in LOOPBACK:
        raise RequestStop("plain IMAP is allowed only to a loopback address")
    client = IMAPClient(source.host, port=source.port, ssl=source.security == "tls",
                        timeout=source.timeout_seconds)
    try:
        if source.security == "starttls":
            client.starttls()
        session = ReadOnlySession(client)
        session.login(source.username, credentials.resolve(source.credential) or "")
    except BaseException:
        try:
            client.shutdown()
        except Exception:  # noqa: BLE001
            pass
        raise
    return session


@contextmanager
def _mapped_errors():
    """Map library and network failures to the sync engine's clean, recorded stops."""
    try:
        from imapclient.exceptions import IMAPClientAbortError, LoginError
    except ImportError:
        raise RequestStop("IMAP support needs its optional dependency: pip install 'towpath[imap]'") from None
    try:
        yield
    except LoginError:
        raise AuthStop("the IMAP server rejected the login; check the account and credential reference") from None
    except (IMAPClientAbortError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
        raise ServerStop(f"IMAP server or network failure ({type(exc).__name__}); progress is saved and "
                         "the next run resumes") from None


def _guarded(fn):
    if inspect.isgeneratorfunction(fn):
        @functools.wraps(fn)
        def generator(self, *args, **kwargs):
            with _mapped_errors():
                yield from fn(self, *args, **kwargs)
        return generator

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with _mapped_errors():
            return fn(self, *args, **kwargs)
    return wrapper


# -- BODYSTRUCTURE ------------------------------------------------------------------------------------


def _params(raw) -> dict:
    if not raw or not isinstance(raw, (tuple, list)):
        return {}
    items = [_text(x) for x in raw]
    return {items[i].lower(): items[i + 1] for i in range(0, len(items) - 1, 2)}


def _decoded(value: str | None) -> str | None:
    if not value:
        return value
    try:
        return str(make_header(decode_header(collapse_rfc2231_value(value))))
    except (LookupError, ValueError):
        return value


def _leaf(node, part_id: str, depth: int) -> dict:
    mtype, subtype = (_text(node[0]) or "").lower(), (_text(node[1]) or "").lower()
    params = _params(node[2])
    # Extension data positions differ by type (RFC 9051 section 7.5.2).
    dsp_index = 9 if mtype == "text" else (11 if (mtype, subtype) == ("message", "rfc822") else 8)
    disposition, dsp_params = None, {}
    if len(node) > dsp_index and isinstance(node[dsp_index], (tuple, list)) and node[dsp_index]:
        disposition = (_text(node[dsp_index][0]) or "").lower() or None
        dsp_params = _params(node[dsp_index][1] if len(node[dsp_index]) > 1 else None)
    filename = dsp_params.get("filename") or params.get("name")
    return {"part_id": part_id, "depth": depth, "mime_type": f"{mtype}/{subtype}", "charset": params.get("charset"),
            "filename": _decoded(filename), "disposition": disposition, "size": int(node[6] or 0),
            "attachment_id": None, "encoding": (_text(node[5]) or "7bit").lower()}


def flatten_structure(body, prefix: str = "", depth: int = 0) -> list[dict]:
    """Leaf parts with IMAP part numbers (1, 1.2, ...). A single-part message's body is part 1."""
    if body is None:
        return []
    if isinstance(body[0], list):
        parts = []
        for i, child in enumerate(body[0], start=1):
            parts.extend(flatten_structure(child, f"{prefix}{i}.", depth + 1) if isinstance(child[0], list)
                         else [_leaf(child, f"{prefix}{i}", depth + 1)])
        return parts
    return [_leaf(body, prefix.rstrip(".") or "1", depth)]


# -- connector ----------------------------------------------------------------------------------------


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

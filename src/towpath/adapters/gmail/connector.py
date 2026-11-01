"""The sync engine's interface to a Gmail account, over the real client or the fixture."""

from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

from towpath import fieldmask
from towpath.adapters.errors import CursorExpired, InvalidPageToken, NotFound, RequestStop
from towpath.adapters.gmail.fixture_client import b64url_decode


def _header(headers: list[dict], name: str) -> str | None:
    for h in headers or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value")
    return None


def _param(value: str | None, key: str) -> str | None:
    for piece in (value or "").split(";")[1:]:
        k, _, v = piece.strip().partition("=")
        if k.lower() == key:
            return v.strip().strip('"')
    return None


class GmailConnector:
    kind = "mail-provider"

    def __init__(self, source_id: str, client, mask_depth: int = fieldmask.DEFAULT_DEPTH):
        self.source_id = source_id
        self.client = client
        self.mask_depth = mask_depth
        self.mask = fieldmask.message_mask(mask_depth)

    def describe(self) -> dict:
        return {"schema": "towpath.source/0", "source_id": self.source_id, "kind": self.kind,
                "connector": "gmail/0", "authority": "read",
                "capabilities": ["enumerate", "fetch_part", "labels", "incremental_cursor", "provider_search"],
                "retention": "on-demand"}

    def probe(self) -> dict:
        profile = self.client.get_profile()
        return {"ok": True, "account": profile["emailAddress"], "history_id": profile["historyId"]}

    # -- indexing -------------------------------------------------------------

    def _parts(self, payload: dict) -> tuple[list[dict], bool, int]:
        """Flatten the MIME tree. Returns parts, whether the mask was too
        shallow, and how many bytes of inline body data arrived (and were dropped)."""
        parts: list[dict] = []
        truncated = False
        discarded = 0

        def walk(node: dict, depth: int) -> None:
            nonlocal truncated, discarded
            if "mimeType" not in node:
                truncated = True  # only the sentinel partId came back
                return
            body = node.get("body") or {}
            if "data" in body:
                discarded += len(body["data"])
            headers = node.get("headers") or []
            content_type = _header(headers, "Content-Type")
            disposition = _header(headers, "Content-Disposition")
            parts.append({
                "part_id": node.get("partId", ""),
                "depth": depth,
                "mime_type": node.get("mimeType"),
                "charset": _param(content_type, "charset"),
                "filename": node.get("filename") or None,
                "disposition": disposition.split(";")[0].strip().lower() if disposition else None,
                "size": body.get("size", 0),
                "attachment_id": body.get("attachmentId"),
            })
            for child in node.get("parts") or []:
                walk(child, depth + 1)

        walk(payload, 0)
        return parts, truncated, discarded

    def _record(self, message: dict) -> dict:
        payload = message.get("payload") or {}
        headers = payload.get("headers") or []
        problems = []
        date_header = _header(headers, "Date")
        date_utc = None
        if date_header:
            try:
                date_utc = parsedate_to_datetime(date_header).isoformat()
            except (TypeError, ValueError):
                problems.append("invalid date header")
        else:
            problems.append("missing date header")
        subject = _header(headers, "Subject")
        if subject:
            try:
                subject = str(make_header(decode_header(subject)))
            except (LookupError, ValueError):
                problems.append("undecodable subject")
        parts, truncated, discarded = self._parts(payload)
        return {
            "native_id": message["id"],
            "thread_id": message.get("threadId"),
            "labels": sorted(message.get("labelIds") or []),
            "internal_date": message.get("internalDate"),
            "size_estimate": message.get("sizeEstimate"),
            "rfc_message_id": _header(headers, "Message-ID"),
            "subject": subject,
            "from_addr": parseaddr(_header(headers, "From") or "")[1] or None,
            "date_header": date_header,
            "date_utc": date_utc,
            "parts": parts,
            "structure_truncated": truncated,
            "inline_data_discarded": discarded,
            "problems": problems,
        }

    def close(self) -> None:
        for owner in (self.client, getattr(self, "limiter", None)):
            if owner is not None and hasattr(owner, "close"):
                owner.close()

    def _incremental(self, cursor: str):
        """History since ``cursor``, one page at a time, with a checkpoint after each record.

        Yields ("cursor_expired", cursor) if Gmail no longer accepts the cursor.
        """
        token, restarted, latest = None, False, cursor
        while True:
            try:
                page = self.client.list_history(cursor, page_token=token)
            except CursorExpired:
                yield ("cursor_expired", cursor)
                return
            except InvalidPageToken:
                if token is None or restarted:
                    raise RequestStop("Gmail rejected the history page token twice") from None
                token, restarted = None, True  # replaying records is safe: every event is idempotent
                continue
            for record in page.get("history", []):
                for added in record.get("messagesAdded", []):
                    try:
                        msg = self.client.get_message(added["message"]["id"], fields=self.mask)
                    except NotFound:
                        continue
                    yield ("item", self._record(msg))
                for deleted in record.get("messagesDeleted", []):
                    yield ("deleted", deleted["message"]["id"])
                for key in ("labelsAdded", "labelsRemoved"):
                    for change in record.get(key, []):
                        event = "labels_added" if key == "labelsAdded" else "labels_removed"
                        yield (event, change["message"]["id"], sorted(change["labelIds"]))
                yield ("checkpoint", record["id"])
            latest = page.get("historyId", latest)
            token = page.get("nextPageToken")
            if not token:
                yield ("done", {"kind": "incremental", "cursor": latest, "complete": True})
                return

    def _listing(self, phase: str, token: str | None, seen: set[str]):
        """List message IDs from ``token`` (None is the first page), reading any not yet seen.

        Before each page it yields ("page", phase, token) so a stop can resume at
        that page. An invalid saved token restarts the listing from the first
        page; already-seen IDs are skipped, so nothing is read twice.
        """
        restarted = False
        while True:
            yield ("page", phase, token)
            try:
                page = self.client.list_messages(page_token=token)
            except InvalidPageToken:
                if token is None or restarted:
                    raise RequestStop("Gmail rejected the message listing page token twice") from None
                token, restarted = None, True
                continue
            present = []
            for ref in page.get("messages", []):
                message_id = ref["id"]
                if message_id in seen:
                    present.append(message_id)
                    continue
                try:
                    msg = self.client.get_message(message_id, fields=self.mask)
                except NotFound:
                    continue  # listed, then deleted before it was read
                seen.add(message_id)
                present.append(message_id)
                yield ("item", self._record(msg))
            yield ("listed", phase, present)
            token = page.get("nextPageToken")
            if not token:
                return

    def enumerate(self, cursor: str | None, full_state: dict | None = None, already_seen: set[str] | None = None,
                  **_):
        """Yield index events. Incremental when a cursor is usable, full otherwise.

        Full syncs have three phases, each resumable:

        - scan: list every message and read those not yet indexed in this sync;
        - reconcile: list again, reading anything the scan missed because the
          mailbox changed underneath it (page tokens can shift);
        - confirm: the caller re-reads each indexed message the reconcile
          listing did not show, and marks it absent only if Gmail says it is gone.

        Events: ("full_start", history_id), ("page", phase, token), ("item", record),
        ("listed", phase, ids), ("phase", name), ("confirm", None), ("deleted", id),
        ("labels_added"/"labels_removed", id, labels), ("checkpoint", history_record_id),
        ("cursor_expired", cursor), ("done", {"kind", "cursor", "complete"}).
        """
        if cursor and not full_state:
            expired = False
            for event in self._incremental(cursor):
                if event[0] == "cursor_expired":
                    expired = True
                yield event
            if not expired:
                return

        state = dict(full_state or {})
        if not state.get("history_id"):
            state = {"history_id": self.client.get_profile()["historyId"], "phase": "scan", "page_token": None}
            yield ("full_start", state["history_id"])
        seen = set(already_seen or ())
        phase, token = state.get("phase") or "scan", state.get("page_token")
        if phase == "scan":
            yield from self._listing("scan", token, seen)
            yield ("phase", "reconcile")
            phase, token = "reconcile", None
        if phase == "reconcile":
            yield from self._listing("reconcile", token, seen)
            yield ("phase", "confirm")
        yield ("confirm", None)
        yield ("done", {"kind": "full", "cursor": state["history_id"], "complete": True})

    def search(self, query: str, page_token: str | None, limit: int) -> dict:
        """Gmail's own search (``messages.list`` with ``q``): one page of message IDs, newest first.

        Same read-only scope and the same paced client as indexing; each page costs one list call.
        ``estimate`` is Gmail's ``resultSizeEstimate``, an estimate and never a count.
        """
        if not query.strip():
            raise RequestStop("provider search needs query text")
        page = self.client.list_messages(page_token=page_token, max_results=limit, q=query)
        return {"ids": [m["id"] for m in page.get("messages", [])], "next_cursor": page.get("nextPageToken"),
                "estimate": page.get("resultSizeEstimate")}

    def confirm(self, native_id: str) -> dict | None:
        """Re-read one message. None means Gmail says it no longer exists."""
        try:
            return self._record(self.client.get_message(native_id, fields=self.mask))
        except NotFound:
            return None

    # -- content ---------------------------------------------------------------

    def fetch(self, native_id: str, part_id: str) -> bytes:
        """Fetch one part's bytes. Other parts in the response are dropped."""
        message = self.client.get_message(native_id, fields="payload")

        def find(node: dict):
            if node.get("partId") == part_id:
                return node
            for child in node.get("parts") or []:
                hit = find(child)
                if hit is not None:
                    return hit
            return None

        part = find(message["payload"])
        if part is None:
            raise NotFound(f"{native_id}/{part_id}")
        body = part.get("body") or {}
        if body.get("attachmentId"):
            return b64url_decode(self.client.get_attachment(native_id, body["attachmentId"])["data"])
        return b64url_decode(body.get("data", ""))

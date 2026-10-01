"""Gmail-shaped connector.

``GmailConnector`` implements Towpath's connector interface (describe, probe,
enumerate, fetch) on top of a small read-only client interface that mirrors
the Gmail API calls Towpath uses. ``FixtureGmailClient`` serves synthetic data
in Gmail's response shapes; a client for the real API can replace it later.
The client interface has no calls that change a mailbox.
"""

import base64
import json
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from towpath import fieldmask
from towpath.adapters.errors import CursorExpired, Interrupted, NotFound

READ_CALLS = ("get_profile", "list_messages", "get_message", "get_attachment", "list_history")


def b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class FixtureGmailClient:
    """Serves a generated fixture file in Gmail API response shapes.

    ``ignore_mask`` simulates a provider that returns full bodies despite the
    field mask; ``interrupt_after`` raises ``Interrupted`` after that many
    message reads, to test resumable syncs.
    """

    def __init__(self, path: Path, ignore_mask: bool = False, interrupt_after: int | None = None):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text())
        self.ignore_mask = ignore_mask
        self.interrupt_after = interrupt_after
        self.message_reads = 0
        self.calls: list[tuple] = []

    def get_profile(self) -> dict:
        self.calls.append(("get_profile",))
        return {"emailAddress": self.data["emailAddress"], "historyId": self.data["historyId"]}

    def list_messages(self, page_token: str | None = None, max_results: int = 10) -> dict:
        self.calls.append(("list_messages", page_token))
        ids = sorted(self.data["messages"], reverse=True)  # newest first, as Gmail lists them
        start = int(page_token or 0)
        page = ids[start:start + max_results]
        result = {"messages": [{"id": i, "threadId": self.data["messages"][i]["threadId"]} for i in page]}
        if start + max_results < len(ids):
            result["nextPageToken"] = str(start + max_results)
        return result

    def get_message(self, message_id: str, fields: str | None = None) -> dict:
        self.calls.append(("get_message", message_id, fields))
        if self.interrupt_after is not None and self.message_reads >= self.interrupt_after:
            raise Interrupted(f"simulated interruption after {self.message_reads} reads")
        self.message_reads += 1
        try:
            message = dict(self.data["messages"][message_id])
        except KeyError:
            raise NotFound(message_id) from None
        message.pop("raw", None)
        if fields and not self.ignore_mask:
            return fieldmask.apply(message, fieldmask.parse(fields))
        return message

    def get_attachment(self, message_id: str, attachment_id: str) -> dict:
        self.calls.append(("get_attachment", message_id, attachment_id))
        data = self.data["attachments"][attachment_id]
        return {"size": len(b64url_decode(data)), "data": data}

    def list_history(self, start_history_id: str) -> dict:
        self.calls.append(("list_history", start_history_id))
        if int(start_history_id) < int(self.data["historyFloor"]):
            raise CursorExpired(start_history_id)
        records = [h for h in self.data["history"] if int(h["id"]) > int(start_history_id)]
        return {"history": records, "historyId": self.data["historyId"]}


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
                "capabilities": ["enumerate", "fetch_part", "labels", "incremental_cursor"],
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

    def enumerate(self, cursor: str | None, full_sync_history_id: str | None = None,
                  already_seen: set[str] | None = None):
        """Yield index events. Incremental when a cursor is usable, full otherwise.

        Events: ("full_start", history_id), ("item", record), ("deleted", native_id),
        ("labels", native_id, labels), ("done", {"kind", "cursor", "complete"}).
        """
        if cursor and not full_sync_history_id:
            try:
                response = self.client.list_history(cursor)
            except CursorExpired:
                yield ("cursor_expired", cursor)
            else:
                for record in response.get("history", []):
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
                            yield ("labels", change["message"]["id"], sorted(change["message"]["labelIds"]))
                yield ("done", {"kind": "incremental", "cursor": response["historyId"], "complete": True})
                return

        history_id = full_sync_history_id or self.client.get_profile()["historyId"]
        yield ("full_start", history_id)
        seen = set(already_seen or ())
        token = None
        while True:
            page = self.client.list_messages(page_token=token)
            for ref in page.get("messages", []):
                if ref["id"] in seen:
                    continue
                msg = self.client.get_message(ref["id"], fields=self.mask)
                seen.add(ref["id"])
                yield ("item", self._record(msg))
            token = page.get("nextPageToken")
            if not token:
                break
        yield ("done", {"kind": "full", "cursor": history_id, "complete": True})

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

"""A client that reads a synthetic account from a fixture file, in the shape of Gmail's API."""

import base64
import json
from pathlib import Path

from towpath import fieldmask
from towpath.adapters.errors import CursorExpired, Interrupted, InvalidPageToken, NotFound


def b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class FixtureGmailClient:
    """Serves a generated fixture file in Gmail API response shapes.

    ``ignore_mask`` simulates a provider that returns full bodies despite the
    field mask; ``interrupt_after`` raises ``Interrupted`` after that many
    message reads, to test resumable syncs.
    """

    def __init__(self, path: Path, ignore_mask: bool = False, interrupt_after: int | None = None,
                 invalid_page_tokens: set[str] | None = None):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text())
        self.ignore_mask = ignore_mask
        self.interrupt_after = interrupt_after
        self.invalid_page_tokens = set(invalid_page_tokens or ())
        self.message_reads = 0
        self.calls: list[tuple] = []

    def get_profile(self) -> dict:
        self.calls.append(("get_profile",))
        return {"emailAddress": self.data["emailAddress"], "historyId": self.data["historyId"]}

    def list_messages(self, page_token: str | None = None, max_results: int = 10, q: str | None = None) -> dict:
        """``q`` here is plain words, all required, matched case-insensitively against the subject, sender
        and text parts of the synthetic message. A test aid: Gmail's own search syntax is far richer."""
        self.calls.append(("list_messages", page_token) if q is None else ("list_messages", page_token, q))
        if page_token in self.invalid_page_tokens:
            raise InvalidPageToken("synthetic invalid page token")
        ids = sorted(self.data["messages"], reverse=True)  # newest first, as Gmail lists them
        if q is not None:
            ids = [i for i in ids if self._matches(self.data["messages"][i], q)]
        start = int(page_token or 0)
        page = ids[start:start + max_results]
        result = {"messages": [{"id": i, "threadId": self.data["messages"][i]["threadId"]} for i in page]}
        if start + max_results < len(ids):
            result["nextPageToken"] = str(start + max_results)
        if q is not None:
            result["resultSizeEstimate"] = len(ids)
        return result

    @staticmethod
    def _matches(message: dict, q: str) -> bool:
        from email import message_from_bytes, policy

        parsed = message_from_bytes(b64url_decode(message.get("raw", "")), policy=policy.default)
        texts = [str(parsed.get("Subject", "")), str(parsed.get("From", ""))]
        texts += [p.get_content() for p in parsed.walk() if p.get_content_type() == "text/plain"]
        haystack = "\n".join(texts).lower()
        return all(word.lower() in haystack for word in q.split())

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

    def list_history(self, start_history_id: str, page_token: str | None = None, max_results: int = 100) -> dict:
        self.calls.append(("list_history", start_history_id, page_token))
        if int(start_history_id) < int(self.data["historyFloor"]):
            raise CursorExpired(start_history_id)
        records = [h for h in self.data["history"] if int(h["id"]) > int(start_history_id)]
        start = int(page_token or 0)
        page = {"history": records[start:start + max_results], "historyId": self.data["historyId"]}
        if start + max_results < len(records):
            page["nextPageToken"] = str(start + max_results)
        return page

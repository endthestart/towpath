"""The Gmail client: read calls only, with errors classified and every request paced."""

import json
import random
import re
from email.utils import parsedate_to_datetime
from pathlib import Path

from towpath.adapters.errors import (
    AuthStop,
    CursorExpired,
    InvalidPageToken,
    NotFound,
    PermissionStop,
    QuotaStop,
    RequestStop,
    ServerStop,
)
from towpath.adapters.google_gmail.authorizing import load_credentials
from towpath.quota import NullLimiter

QUOTA_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "dailyLimitExceeded",
                 "RATE_LIMIT_EXCEEDED", "RESOURCE_EXHAUSTED"}
AUTH_REASONS = {"authError", "UNAUTHENTICATED", "invalid_grant"}


def error_details(exc) -> tuple[int, str]:
    """Status code and a sanitized reason code from a Google API error. Never the message or URL."""
    status = int(getattr(exc.resp, "status", 0) or 0)
    reason = ""
    try:
        content = exc.content.decode() if isinstance(exc.content, bytes) else exc.content
        error = json.loads(content).get("error", {})
        errors = error.get("errors") or []
        reason = (errors[0].get("reason") if errors else "") or ""
        if not reason:
            reason = next((d.get("reason") for d in error.get("details") or [] if d.get("reason")), "") or ""
        reason = reason or error.get("status", "") or ""
    except (ValueError, AttributeError, TypeError, IndexError):
        pass
    return status, re.sub(r"[^A-Za-z0-9_]", "", str(reason))[:64]


def classify(status: int, reason: str) -> str:
    """One of: not_found, quota, auth, permission, request, server."""
    if status == 404:
        return "not_found"
    if status == 429 or reason in QUOTA_REASONS:
        return "quota"
    if status == 401 or reason in AUTH_REASONS:
        return "auth"
    if status == 403:
        return "permission"
    if status >= 500 or status == 0:
        return "server"
    return "request"


def retry_after_seconds(exc, now: float) -> float | None:
    value = exc.resp.get("retry-after") if hasattr(exc.resp, "get") else None
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - now)
        except (TypeError, ValueError):
            return None


class GoogleGmailClient:
    """Read-only Gmail API calls. There is deliberately no call that changes a mailbox.

    Every attempt, including retries, goes through the limiter. The API
    client's own retries are disabled so none escape the budget.
    """

    def __init__(self, service, page_size: int = 500, limiter=None, rng=None):
        self.service = service
        self.page_size = page_size
        self.limiter = limiter or NullLimiter()
        self.rng = rng or random.Random()

    @classmethod
    def from_token(cls, token_path: Path, **kwargs) -> "GoogleGmailClient":
        from googleapiclient.discovery import build

        creds = load_credentials(token_path)
        return cls(build("gmail", "v1", credentials=creds, cache_discovery=False), **kwargs)

    def close(self) -> None:
        self.limiter.close()

    def _backoff(self, attempt: int) -> float:
        s = self.limiter.settings
        ceiling = min(s.backoff_max_seconds, s.backoff_base_seconds * 2 ** (attempt - 1))
        return ceiling / 2 + self.rng.uniform(0, ceiling / 2)

    def _execute(self, make_request, method: str, page_token: str | None = None):
        import httplib2
        from google.auth.exceptions import RefreshError, TransportError
        from googleapiclient.errors import HttpError

        settings = self.limiter.settings
        for attempt in range(1, settings.max_attempts + 1):
            self.limiter.acquire(method)
            retry_after = None
            try:
                return make_request().execute(num_retries=0)
            except HttpError as exc:
                status, reason = error_details(exc)
                label = f"HTTP {status}" + (f" {reason}" if reason else "")
                kind = classify(status, reason)
                if kind == "not_found":
                    raise NotFound(label) from None
                if status == 400 and page_token:
                    raise InvalidPageToken(label) from None
                if kind == "auth":
                    raise AuthStop(f"Gmail rejected the authorization ({label}); "
                                   "run `towpath connect auth <source>`") from None
                if kind == "permission":
                    raise PermissionStop(f"Gmail refused access ({label}); check that the token grants "
                                         "gmail.readonly and the Gmail API is enabled") from None
                if kind == "request":
                    raise RequestStop(f"Gmail rejected {method} as invalid ({label})") from None
                retry_after = retry_after_seconds(exc, self.limiter.clock() if hasattr(self.limiter, "clock")
                                                  else 0.0)
            except RefreshError:
                raise AuthStop("the saved authorization expired or was revoked; "
                               "run `towpath connect auth <source>`") from None
            except (TransportError, httplib2.HttpLib2Error, TimeoutError, ConnectionError, OSError) as exc:
                kind, label = "server", f"network error {exc.__class__.__name__}"
            stop = QuotaStop if kind == "quota" else ServerStop
            if attempt == settings.max_attempts:
                raise stop(f"{method}: {label} persisted after {attempt} attempts; progress is saved and "
                           "the next run resumes")
            delay = retry_after if retry_after is not None else self._backoff(attempt)
            if delay > settings.backoff_max_seconds:
                raise stop(f"{method}: {label}; Gmail asked to wait {delay:.0f} s, longer than "
                           "backoff_max_seconds; progress is saved and the next run resumes")
            self.limiter.wait(delay, f"retry {attempt} after {label}")
        raise AssertionError("unreachable")

    def get_profile(self) -> dict:
        return self._execute(lambda: self.service.users().getProfile(userId="me"), "users.getProfile")

    def list_messages(self, page_token: str | None = None, max_results: int | None = None,
                      q: str | None = None) -> dict:
        """One page of message IDs. ``q`` is Gmail's own search syntax, under the same read-only scope
        and the same per-call cost and pacing as an unfiltered listing."""
        kwargs = {"userId": "me", "pageToken": page_token, "maxResults": max_results or self.page_size,
                  "includeSpamTrash": False, "fields": "messages(id,threadId),nextPageToken"}
        if q:
            kwargs["q"] = q
            kwargs["fields"] += ",resultSizeEstimate"
        return self._execute(lambda: self.service.users().messages().list(**kwargs), "users.messages.list",
                             page_token)

    def get_message(self, message_id: str, fields: str | None = None) -> dict:
        kwargs = {"userId": "me", "id": message_id, "format": "full"}
        if fields:
            kwargs["fields"] = fields
        return self._execute(lambda: self.service.users().messages().get(**kwargs), "users.messages.get")

    def get_attachment(self, message_id: str, attachment_id: str) -> dict:
        return self._execute(lambda: self.service.users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id), "users.messages.attachments.get")

    def list_history(self, start_history_id: str, page_token: str | None = None) -> dict:
        """One page of history. Raises CursorExpired when Gmail no longer accepts the start ID."""
        try:
            return self._execute(lambda: self.service.users().history().list(
                userId="me", startHistoryId=start_history_id, pageToken=page_token, maxResults=self.page_size),
                "users.history.list", page_token)
        except NotFound:
            raise CursorExpired(start_history_id) from None


def verify_structure(client, mask: str, sample: int = 25) -> dict:
    """Decision D16 check: does the field mask keep body data out of responses?

    Reads ``sample`` recent messages with the structural mask and reports any
    body data, truncation, and inline attachments. Stores nothing.
    """
    if sample < 1:
        raise ValueError("sample must be at least 1")
    ids: list[str] = []
    token = None
    while len(ids) < sample:
        page = client.list_messages(page_token=token)
        ids.extend(m["id"] for m in page.get("messages", []))
        token = page.get("nextPageToken")
        if not token:
            break
    report = {"messages_checked": 0, "messages_with_body_data": 0, "body_data_chars": 0, "truncated": 0,
              "max_depth": 0, "parts": 0, "attachments_by_id": 0, "attachments_inline": 0}

    def walk(node, depth):
        if "mimeType" not in node:
            report["truncated"] += 1
            return 0
        body = node.get("body") or {}
        chars = len(body.get("data", ""))
        report["parts"] += 1
        report["max_depth"] = max(report["max_depth"], depth)
        if node.get("filename"):
            key = "attachments_by_id" if body.get("attachmentId") else "attachments_inline"
            report[key] += 1
        return chars + sum(walk(child, depth + 1) for child in node.get("parts") or [])

    for message_id in ids[:sample]:
        message = client.get_message(message_id, fields=mask)
        chars = walk(message.get("payload") or {}, 0)
        report["messages_checked"] += 1
        report["body_data_chars"] += chars
        report["messages_with_body_data"] += bool(chars)
    report["passed"] = (report["messages_checked"] > 0
                        and report["messages_with_body_data"] == 0 and report["truncated"] == 0)
    if not report["messages_checked"]:
        report["reason"] = "no messages were checked; structure verification needs a non-empty mailbox"
    return report

"""The real Gmail API client and OAuth helpers (optional: pip install "towpath[gmail]").

The client implements the same five read-only calls as the fixture client.
Authorization requests only ``gmail.readonly``, disables incremental
authorization, and refuses any token that carries a broader grant.
"""

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

from towpath.adapters.errors import CursorExpired, NotFound

READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
ALLOWED_SCOPES = {READONLY_SCOPE}
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


class ScopeError(PermissionError):
    pass


def check_scopes(granted: set[str]) -> None:
    """Refuse a token unless its grant is exactly read-only Gmail access."""
    if READONLY_SCOPE not in granted:
        raise ScopeError("the token does not include gmail.readonly")
    extra = granted - ALLOWED_SCOPES
    if extra:
        raise ScopeError(f"the token carries broader scopes than Towpath allows: {sorted(extra)}. "
                         "Use a Google Cloud project used only by Towpath, revoke this grant, and authorize again.")


def tokeninfo_scopes(access_token: str) -> set[str]:
    """Ask Google which scopes an access token actually carries."""
    url = TOKENINFO_URL + "?" + urllib.parse.urlencode({"access_token": access_token})
    with urllib.request.urlopen(url, timeout=20) as resp:  # noqa: S310 - fixed https URL
        info = json.load(resp)
    return set(info.get("scope", "").split())


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def authorize(client_secrets: Path, token_path: Path, open_browser: bool = True) -> dict:
    """Run Google's installed-app consent flow and store a verified read-only token."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), scopes=[READONLY_SCOPE])
    creds = flow.run_local_server(port=0, open_browser=open_browser, include_granted_scopes="false",
                                  access_type="offline", prompt="consent")
    granted = tokeninfo_scopes(creds.token)
    check_scopes(granted)
    _write_private(Path(token_path), creds.to_json())
    return {"granted_scopes": sorted(granted), "token": str(token_path)}


def load_credentials(token_path: Path, verify_online: bool = True):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token_path = Path(token_path)
    if not token_path.is_file():
        raise FileNotFoundError(f"no token at {token_path}; run `towpath connect auth <source>` first")
    creds = Credentials.from_authorized_user_file(str(token_path), scopes=[READONLY_SCOPE])
    if not creds.valid:
        creds.refresh(Request())
        _write_private(token_path, creds.to_json())
    if verify_online:
        check_scopes(tokeninfo_scopes(creds.token))
    return creds


class GoogleGmailClient:
    """Read-only Gmail API calls. There is deliberately no call that changes a mailbox."""

    def __init__(self, service, page_size: int = 100, num_retries: int = 5):
        self.service = service
        self.page_size = page_size
        self.num_retries = num_retries

    @classmethod
    def from_token(cls, token_path: Path, **kwargs) -> "GoogleGmailClient":
        from googleapiclient.discovery import build

        creds = load_credentials(token_path)
        return cls(build("gmail", "v1", credentials=creds, cache_discovery=False), **kwargs)

    def _execute(self, request):
        from googleapiclient.errors import HttpError

        try:
            return request.execute(num_retries=self.num_retries)
        except HttpError as exc:
            if exc.resp.status == 404:
                raise NotFound(str(exc)) from None
            raise

    def get_profile(self) -> dict:
        return self._execute(self.service.users().getProfile(userId="me"))

    def list_messages(self, page_token: str | None = None, max_results: int | None = None) -> dict:
        return self._execute(self.service.users().messages().list(
            userId="me", pageToken=page_token, maxResults=max_results or self.page_size, includeSpamTrash=False,
            fields="messages(id,threadId),nextPageToken"))

    def get_message(self, message_id: str, fields: str | None = None) -> dict:
        kwargs = {"userId": "me", "id": message_id, "format": "full"}
        if fields:
            kwargs["fields"] = fields
        return self._execute(self.service.users().messages().get(**kwargs))

    def get_attachment(self, message_id: str, attachment_id: str) -> dict:
        return self._execute(self.service.users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id))

    def list_history(self, start_history_id: str) -> dict:
        records: list[dict] = []
        token = None
        latest = start_history_id
        while True:
            try:
                page = self._execute(self.service.users().history().list(
                    userId="me", startHistoryId=start_history_id, pageToken=token))
            except NotFound:
                raise CursorExpired(start_history_id) from None
            records.extend(page.get("history", []))
            latest = page.get("historyId", latest)
            token = page.get("nextPageToken")
            if not token:
                return {"history": records, "historyId": latest}


def verify_structure(client, mask: str, sample: int = 25) -> dict:
    """Decision D16 check: does the field mask keep body data out of responses?

    Reads ``sample`` recent messages with the structural mask and reports any
    body data, truncation, and inline attachments. Stores nothing.
    """
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
    report["passed"] = report["messages_with_body_data"] == 0 and report["truncated"] == 0
    return report

"""Turning a message's headers and text part into what mail management reads. No network, no stores."""

import base64
import binascii
import html
import json
import quopri
import re
from email.header import decode_header, make_header
from email.parser import BytesHeaderParser
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from datetime import timezone

HEADER_FIELDS = ("FROM", "TO", "CC", "REPLY-TO", "SUBJECT", "DATE", "MESSAGE-ID", "IN-REPLY-TO", "REFERENCES",
                 "LIST-ID", "LIST-UNSUBSCRIBE", "LIST-UNSUBSCRIBE-POST", "AUTO-SUBMITTED", "PRECEDENCE")
TEXT_LIMIT = 20_000  # characters kept per message
MESSAGE_ID = re.compile(r"<[^<>\s]+>")

# Where a reply's quoted earlier message starts. Lines after the first match are dropped.
QUOTE_STARTS = (
    re.compile(r"^On .{0,200}(wrote|writes):\s*$", re.I),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}\s*$", re.I),
    re.compile(r"^-{2,}\s*Forwarded message\s*-{2,}\s*$", re.I),
    re.compile(r"^From:\s.+$"),  # an Outlook-style header block; only counted when Sent: or Date: follows
)


def _decoded(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return str(make_header(decode_header(value))).strip()
    except (LookupError, ValueError, UnicodeError):
        return value.strip()


def _ids(value: str | None) -> list[str]:
    return MESSAGE_ID.findall(value or "")


class _Fields:
    """Header access that always yields text: the parser returns a ``Header`` object, not a string, for a field
    carrying raw 8-bit bytes."""

    def __init__(self, message):
        self._m = message

    def get(self, name):
        value = self._m.get(name)
        return None if value is None else str(value)

    def get_all(self, name, default=()):
        return [str(v) for v in self._m.get_all(name, [])] or list(default)

    def __getitem__(self, name):
        return self.get(name)


def headers(raw: bytes) -> dict:
    """The header fields mail management uses, decoded, with a list of problems instead of exceptions."""
    h = _Fields(BytesHeaderParser().parsebytes(raw or b""))
    problems = []
    date_utc = None
    if h.get("Date"):
        try:
            date_utc = parsedate_to_datetime(h["Date"]).astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            problems.append("invalid date header")
    name, addr = parseaddr(_decoded(h.get("From")) or "")
    refs = _ids(h.get("References"))
    in_reply_to = (_ids(h.get("In-Reply-To")) or [None])[0]
    return {
        "from_name": name or None, "from_addr": addr.lower() or None,
        "to_addrs": [a.lower() for _, a in getaddresses([_decoded(v) or "" for v in h.get_all("To", [])]) if a],
        "cc_addrs": [a.lower() for _, a in getaddresses([_decoded(v) or "" for v in h.get_all("Cc", [])]) if a],
        "reply_to": parseaddr(_decoded(h.get("Reply-To")) or "")[1].lower() or None,
        "subject": _decoded(h.get("Subject")), "date_utc": date_utc,
        "message_id": (_ids(h.get("Message-ID")) or [None])[0], "in_reply_to": in_reply_to, "refs": refs,
        "list_id": _decoded(h.get("List-ID")), "list_unsubscribe": _decoded(h.get("List-Unsubscribe")),
        "list_unsubscribe_post": _decoded(h.get("List-Unsubscribe-Post")),
        "auto_submitted": _decoded(h.get("Auto-Submitted")), "precedence": _decoded(h.get("Precedence")),
        "problems": problems,
    }


def thread_key(fields: dict, server_thread: str | None, item_id: str) -> str:
    """The server's thread ID when it has one (RFC 8474 THREADID); otherwise the first message the headers name."""
    if server_thread:
        return f"t:{server_thread}"
    root = fields["refs"][0] if fields["refs"] else fields["in_reply_to"] or fields["message_id"]
    return f"m:{root}" if root else f"i:{item_id}"


def decode_part(raw: bytes, encoding: str, charset: str | None, truncated: bool) -> str:
    """A text part's characters. A truncated base64 read is cut back to whole 4-character groups first."""
    if encoding == "base64":
        compact = re.sub(rb"\s+", b"", raw)
        if truncated:
            compact = compact[:len(compact) // 4 * 4]
        try:
            raw = base64.b64decode(compact, validate=False)
        except (binascii.Error, ValueError):
            raw = b""
    elif encoding == "quoted-printable":
        raw = quopri.decodestring(raw)
    try:
        return raw.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def html_to_text(text: str) -> str:
    text = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", text)
    text = re.sub(r"(?is)<blockquote.*?</blockquote>", "\n> [quoted]\n", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>|</h\d>", "\n", text)
    return html.unescape(re.sub(r"(?s)<[^>]+>", " ", text))


def without_quotes(text: str) -> tuple[str, bool]:
    """The new text of a message: quoted lines (``>``) and the quoted earlier message removed."""
    lines = text.splitlines()
    kept, removed = [], False
    for i, line in enumerate(lines):
        stripped = line.strip()
        start = any(p.match(stripped) for p in QUOTE_STARTS[:3])
        if not start and QUOTE_STARTS[3].match(stripped):
            following = " ".join(lines[i + 1:i + 4])
            start = bool(re.search(r"^\s*(Sent|Date):", following, re.M | re.I)) or bool(
                re.search(r"\b(Sent|Date):", following))
        if start:
            removed = True
            break
        if stripped.startswith(">"):
            removed = True
            continue
        kept.append(line)
    return "\n".join(kept), removed


def clean(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v ​]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def message_text(raw: bytes, part: dict, truncated: bool) -> tuple[str, bool, bool]:
    """(text, truncated, quotes removed) for one text part."""
    text = decode_part(raw, part.get("encoding") or "7bit", part.get("charset"), truncated)
    if part["mime_type"] == "text/html":
        text = html_to_text(text)
    text, removed = without_quotes(text)
    text = clean(text)
    if len(text) > TEXT_LIMIT:
        text, truncated = text[:TEXT_LIMIT], True
    return text, truncated, removed


def text_part(parts: list[dict]) -> dict | None:
    """The message's body text: plain text first, then HTML, never an attachment."""
    body = [p for p in parts if p["disposition"] != "attachment" and p["mime_type"] in ("text/plain", "text/html")]
    body.sort(key=lambda p: (p["mime_type"] != "text/plain", p["depth"], p["part_id"]))
    return body[0] if body else None


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)

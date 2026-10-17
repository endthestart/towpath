"""A Facebook export ("Download your information", JSON format) as a read-only source.

The export is a folder on the server. Towpath reads two kinds of item from it:

- **posts** the owner wrote (``your_posts*.json``), one item each;
- **messages** (``messages/inbox/<thread>/message_N.json`` and archived threads), one item per conversation per
  day, so a day's chat reads as one piece of evidence.

Both are owner-authored or owner-addressed, so they are labelled ``SENT`` where the owner wrote, which the life story
counts as likely. Facebook writes text as UTF-8 bytes escaped as Latin-1 characters; that is undone. Nothing in the
export is changed.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from towpath.adapters.errors import NotFound

MESSAGE_DIRS = ("inbox", "archived_threads", "e2ee_cutover", "filtered_threads", "message_requests")


def fix(text):
    """Facebook's mojibake (UTF-8 read as Latin-1) back to text; other strings are left as they are."""
    if not isinstance(text, str):
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


class FacebookConnector:
    kind = "mail-provider"

    def __init__(self, source_id: str, root: Path):
        self.source_id = source_id
        self.root = Path(root)
        self._items: dict[str, dict] | None = None
        self.owner: str | None = None

    def describe(self) -> dict:
        return {"schema": "towpath.source/0", "source_id": self.source_id, "kind": self.kind,
                "connector": "facebook-export/0", "authority": "read", "capabilities": ["enumerate", "fetch_part"],
                "retention": "export"}

    # -- reading the export -------------------------------------------------------------------------------------

    def _owner_name(self) -> str | None:
        for profile in self.root.rglob("profile_information.json"):
            try:
                data = json.loads(profile.read_text(encoding="utf-8"))
                return fix(data.get("profile_v2", data.get("profile", {})).get("name", {}).get("full_name"))
            except (ValueError, OSError, AttributeError):
                continue
        return None

    def _posts(self):
        for path in sorted(self.root.rglob("your_posts*.json")):
            try:
                entries = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if isinstance(entries, dict):
                entries = entries.get("status_updates_v2") or entries.get("posts") or []
            for i, entry in enumerate(entries):
                texts = [fix(d.get("post")) for d in entry.get("data", []) if isinstance(d, dict) and d.get("post")]
                title = fix(entry.get("title"))
                places = [fix(a.get("data", [{}])[0].get("place", {}).get("name"))
                          for a in entry.get("attachments", []) if a.get("data")]
                body = "\n\n".join(t for t in [*texts, *(f"At {p}" for p in places if p)] if t) or title
                if not body or not entry.get("timestamp"):
                    continue
                yield {"native_id": f"post:{entry['timestamp']}:{i}", "date": _iso(entry["timestamp"]),
                       "subject": (texts[0] if texts else title or "Post")[:120], "from": self.owner,
                       "text": body, "labels": ["Posts", "SENT"], "thread": None}

    def _messages(self):
        for folder in MESSAGE_DIRS:
            for thread in sorted(p for p in self.root.rglob(folder) if p.is_dir()):
                for convo in sorted(p for p in thread.iterdir() if p.is_dir()):
                    days: dict[str, list] = {}
                    title, names = None, []
                    for part in sorted(convo.glob("message_*.json")):
                        try:
                            data = json.loads(part.read_text(encoding="utf-8"))
                        except (ValueError, OSError):
                            continue
                        title = fix(data.get("title")) or title
                        names = [fix(p.get("name")) for p in data.get("participants", [])] or names
                        for m in data.get("messages", []):
                            if not m.get("timestamp_ms"):
                                continue
                            when = datetime.fromtimestamp(m["timestamp_ms"] / 1000, timezone.utc)
                            days.setdefault(when.date().isoformat(), []).append((when, fix(m.get("sender_name")),
                                                                                fix(m.get("content")) or ""))
                    for day, msgs in days.items():
                        msgs.sort()
                        text = "\n".join(f"{w:%H:%M} {s}: {c}" for w, s, c in msgs if c)
                        if not text:
                            continue
                        mine = bool(self.owner) and any(s == self.owner for _, s, _ in msgs)
                        yield {"native_id": f"chat:{convo.name}:{day}", "date": msgs[0][0].isoformat(),
                               "subject": f"Messages with {title or ', '.join(n for n in names if n != self.owner)}",
                               "from": next((s for _, s, _ in msgs if s != self.owner), None), "text": text,
                               "labels": ["Messages", *(["SENT"] if mine else [])], "thread": f"chat:{convo.name}"}

    def _load(self) -> dict[str, dict]:
        if self._items is None:
            if not self.root.is_dir():
                raise NotFound(f"the export folder {self.root} isn't there")
            self.owner = self._owner_name()
            self._items = {i["native_id"]: i for i in (*self._posts(), *self._messages())}
        return self._items

    def _record(self, item: dict) -> dict:
        size = len(item["text"].encode("utf-8"))
        return {"native_id": item["native_id"], "thread_id": item["thread"], "labels": item["labels"],
                "internal_date": None, "size_estimate": size, "rfc_message_id": None, "subject": item["subject"],
                "from_addr": item["from"], "date_header": item["date"], "date_utc": item["date"],
                "parts": [{"part_id": "1", "depth": 0, "mime_type": "text/plain", "charset": "utf-8",
                           "filename": None, "disposition": None, "size": size, "attachment_id": None}],
                "structure_truncated": False, "inline_data_discarded": 0, "problems": []}

    # -- the sync engine's interface ----------------------------------------------------------------------------

    def enumerate(self, cursor, full_state=None, already_seen=None, **_):
        items = self._load()
        seen = set(already_seen or ())
        if not full_state:
            yield ("full_start", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        for native_id, item in items.items():
            if native_id not in seen:
                yield ("item", self._record(item))
        yield ("listed", "reconcile", list(items))
        yield ("confirm", None)
        yield ("done", {"kind": "full", "cursor": json.dumps({"items": len(items)}), "complete": True})

    def confirm(self, native_id: str) -> dict | None:
        item = self._load().get(native_id)
        return self._record(item) if item else None

    def fetch(self, native_id: str, part_id: str) -> bytes:
        item = self._load().get(native_id)
        if item is None or part_id != "1":
            raise NotFound(native_id)
        return item["text"].encode("utf-8")

    def close(self) -> None:
        self._items = None


def looks_like_export(path: Path) -> bool:
    """Whether a folder holds a Facebook JSON export (a posts file or a message thread)."""
    path = Path(path)
    return any(path.rglob("your_posts*.json")) or any(path.rglob("message_1.json"))


def safe_name(text: str) -> str:
    return re.sub(r"[^\w .-]", "", text)[:80]

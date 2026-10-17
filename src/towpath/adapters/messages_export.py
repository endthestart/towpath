"""Apple Messages, from an export made with imessage-exporter (``-f txt``), as a read-only source.

The owner runs the exporter on a Mac (or against an iPhone backup) now and then, into a folder on the server. It
writes one text file per conversation, named for the people in it; each message is a timestamp line in the Mac's
local time (``May 17, 2022  6:35:50 PM``, perhaps followed by a note such as when it was read), a line naming the
sender (``Me`` for the owner), and the message's text. Towpath reads each conversation as one item per day, so a
day's chat reads as one piece of evidence, as for the Facebook export. Nothing in the export is changed.

Messages get the same protections as mail: only models the owner allowed read them, keep-from-cloud marks hold for a
conversation's senders, and they reach the life story only once the owner allows this source for it.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from towpath.adapters.errors import NotFound

STAMP = re.compile(r"^([A-Z][a-z]{2} \d{1,2}, \d{4}\s+\d{1,2}:\d{2}:\d{2}\s*[AP]M)\b")
OWNER = "Me"
TIMEZONE = "America/Chicago"  # the Mac's local time, as the exporter writes it


def _when(stamp: str) -> datetime | None:
    try:
        local = datetime.strptime(" ".join(stamp.split()), "%b %d, %Y %I:%M:%S %p")
    except ValueError:
        return None
    return local.replace(tzinfo=ZoneInfo(TIMEZONE)).astimezone(timezone.utc)


def parse(text: str) -> list[tuple[datetime, str, str]]:
    """(when, sender, text) for each message in one conversation file, in file order."""
    out, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        found = STAMP.match(lines[i].strip())
        when = _when(found.group(1)) if found else None
        if when is None or i + 1 >= len(lines):
            i += 1
            continue
        sender, body, j = lines[i + 1].strip(), [], i + 2
        while j < len(lines) and not (STAMP.match(lines[j].strip()) and (j == 0 or not lines[j - 1].strip())):
            body.append(lines[j])
            j += 1
        out.append((when, sender, "\n".join(body).strip()))
        i = j
    return out


class MessagesExportConnector:
    kind = "mail-provider"

    def __init__(self, source_id: str, root: Path):
        self.source_id = source_id
        self.root = Path(root)
        self._items: dict[str, dict] | None = None

    def describe(self) -> dict:
        return {"schema": "towpath.source/0", "source_id": self.source_id, "kind": self.kind,
                "connector": "imessage-export/0", "authority": "read", "capabilities": ["enumerate", "fetch_part"],
                "retention": "export"}

    def _conversations(self):
        for path in sorted(self.root.rglob("*.txt")):
            try:
                messages = parse(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            name = path.stem
            days: dict[str, list] = {}
            for when, sender, text in messages:
                days.setdefault(when.astimezone(ZoneInfo(TIMEZONE)).date().isoformat(), []).append(
                    (when, sender, text))  # a day as the owner lived it
            for day, msgs in days.items():
                msgs.sort(key=lambda m: m[0])
                body = "\n".join(f"{w.astimezone(ZoneInfo(TIMEZONE)):%H:%M} {s}: {t}" for w, s, t in msgs if t)
                if not body:
                    continue
                mine = any(s == OWNER for _, s, _ in msgs)
                yield {"native_id": f"chat:{name}:{day}", "date": msgs[0][0].isoformat(),
                       "subject": f"Messages with {name}", "text": body, "thread": f"chat:{name}",
                       "from": next((s for _, s, _ in msgs if s != OWNER), None),
                       "labels": ["Messages", *(["SENT"] if mine else [])]}

    def _load(self) -> dict[str, dict]:
        if self._items is None:
            if not self.root.is_dir():
                raise NotFound(f"the export folder {self.root} isn't there")
            self._items = {i["native_id"]: i for i in self._conversations()}
        return self._items

    def _record(self, item: dict) -> dict:
        size = len(item["text"].encode("utf-8"))
        return {"native_id": item["native_id"], "thread_id": item["thread"], "labels": item["labels"],
                "internal_date": None, "size_estimate": size, "rfc_message_id": None, "subject": item["subject"],
                "from_addr": item["from"], "date_header": item["date"], "date_utc": item["date"],
                "parts": [{"part_id": "1", "depth": 0, "mime_type": "text/plain", "charset": "utf-8",
                           "filename": None, "disposition": None, "size": size, "attachment_id": None}],
                "structure_truncated": False, "inline_data_discarded": 0, "problems": []}

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
    """Whether a folder holds an imessage-exporter text export (a .txt file that starts with a message)."""
    for f in list(Path(path).rglob("*.txt"))[:50]:
        try:
            head = f.read_text(encoding="utf-8", errors="replace")[:2000].splitlines()
        except OSError:
            continue
        if any(STAMP.match(line.strip()) for line in head[:5]):
            return True
    return False

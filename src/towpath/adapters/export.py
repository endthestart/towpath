"""The sync engine's interface for read-only exports whose items are plain text (Facebook, Apple Messages).

A subclass reads the export folder in ``_read()``, yielding items with ``native_id``, ``thread``, ``labels``,
``subject``, ``from``, ``date`` and ``text``. Each item is one text part; nothing in the export is changed.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from towpath.adapters.errors import NotFound


class ExportConnector:
    kind = "mail-provider"
    connector: str

    def __init__(self, source_id: str, root: Path):
        self.source_id = source_id
        self.root = Path(root)
        self._items: dict[str, dict] | None = None

    def describe(self) -> dict:
        return {"schema": "towpath.source/0", "source_id": self.source_id, "kind": self.kind,
                "connector": self.connector, "authority": "read", "capabilities": ["enumerate", "fetch_part"],
                "retention": "export"}

    def _read(self):
        raise NotImplementedError

    def _load(self) -> dict[str, dict]:
        if self._items is None:
            if not self.root.is_dir():
                raise NotFound(f"the export folder {self.root} isn't there")
            self._items = {i["native_id"]: i for i in self._read()}
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

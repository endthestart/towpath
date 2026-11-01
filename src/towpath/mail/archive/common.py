"""Limits, and where the archive lives."""

import re
from datetime import datetime, timezone
from pathlib import Path

ROLE = "connect"
SLICE_SECONDS = 90
BATCH = 25
MAX_ITEMS = 20_000  # per request
FROM_LINE = re.compile(rb"^>*From ")
ESCAPED = re.compile(rb"^>+From ")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def root(store_dir) -> Path:
    """The archive folder: archive/mail/ in the data folder (the stores' folder is the data folder's state/)."""
    return Path(store_dir).parent / "archive" / "mail"

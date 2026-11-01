"""The archive file: appending messages, reading them back, and verifying a copy."""

import hashlib
import os
from contextlib import closing
from datetime import datetime, timezone
from email.parser import BytesHeaderParser
from pathlib import Path

from towpath.mail.archive.common import ESCAPED, FROM_LINE
from towpath.mail.text import MESSAGE_ID
from towpath.stores import open_store, store_path


def _lines(data: bytes) -> list[bytes]:
    """Lines ending at LF only (mbox readers split there; a lone CR is part of a line)."""
    parts = data.split(b"\n")
    return [p + b"\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def escape(raw: bytes) -> bytes:
    return b"".join(b">" + line if FROM_LINE.match(line) else line for line in _lines(raw))


def unescape(data: bytes) -> bytes:
    return b"".join(line[1:] if ESCAPED.match(line) else line for line in _lines(data))


def _sync(f) -> None:
    """Flush to the disk itself (on macOS fsync alone leaves it in the drive's cache)."""
    f.flush()
    try:
        import fcntl

        if hasattr(fcntl, "F_FULLFSYNC"):
            fcntl.fcntl(f.fileno(), fcntl.F_FULLFSYNC)
            return
    except OSError:
        pass
    os.fsync(f.fileno())


def append(path: Path, raw: bytes) -> tuple[int, int]:
    """Append one message; returns (offset, length) of its escaped bytes, after its From line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = escape(raw)
    stamp = datetime.now(timezone.utc).strftime("%a %b %d %H:%M:%S %Y").encode()
    with open(path, "ab") as f:
        f.write(b"From MAILER-DAEMON " + stamp + b"\n")
        offset = f.tell()
        f.write(body)
        f.write(b"\n" if body.endswith(b"\n") else b"\n\n")
        _sync(f)
    return offset, len(body)


def read_back(path: Path, offset: int, length: int) -> bytes:
    with open(path, "rb") as f:
        f.seek(offset)
        return unescape(f.read(length))


def verify(path: Path, offset: int, length: int, sha256: str, size: int | None, message_id: str | None) -> str | None:
    """Why the copy in the file doesn't match, or None when it does."""
    copy = read_back(path, offset, length)
    if hashlib.sha256(copy).hexdigest() != sha256:
        return "the copy's bytes differ"
    if size is not None and len(copy) != size:
        return "the copy's size differs from the server's"
    if message_id:
        found = MESSAGE_ID.findall(str(BytesHeaderParser().parsebytes(copy).get("Message-ID") or ""))
        if not found or found[0] != message_id:
            return "the copy's Message-ID differs"
    return None


def check_copy(store_dir, item_id: str, role: str = "act") -> str | None:
    """Why a message's archived copy can't be relied on now (re-read from its file), or None."""
    if not store_path(store_dir, "mail").exists():
        return "there is no record of an archive copy"
    with closing(open_store(store_dir, "mail", role)) as db:
        row = db.execute("SELECT path, offset, length, sha256, message_id FROM archived WHERE item_id = ?",
                         (item_id,)).fetchone()
    if row is None:
        return "there is no record of an archive copy"
    try:
        return verify(Path(row[0]), row[1], row[2], row[3], None, row[4])
    except OSError as exc:
        return f"the archive file couldn't be read ({type(exc).__name__})"

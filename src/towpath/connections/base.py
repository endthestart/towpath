"""What every kind of connection shares: providers, secrets in the credentials folder, and the connections store."""

import json
import os
import re
import secrets
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from towpath.stores import open_store

ROLE = "connect"
# Providers whose app passwords are shown in groups (spaces dropped) and whose sign-in is the full address.
APP_PASSWORDS = ("fastmail", "gmail_imap", "icloud_imap", "yahoo_imap")
PROVIDERS = {
    "gmail": {"label": "Gmail", "host": None, "port": None, "security": None,
              "help_url": "https://myaccount.google.com/connections"},
    "fastmail": {"label": "Fastmail", "host": "imap.fastmail.com", "port": 993, "security": "tls",
                 "help_url": "https://www.fastmail.help/hc/en-us/articles/360058752854-App-passwords"},
    "gmail_imap": {"label": "Gmail (IMAP)", "host": "imap.gmail.com", "port": 993, "security": "tls",
                   "help_url": "https://myaccount.google.com/apppasswords"},
    "icloud_imap": {"label": "iCloud Mail", "host": "imap.mail.me.com", "port": 993, "security": "tls",
                    "help_url": "https://support.apple.com/en-us/102654"},
    "yahoo_imap": {"label": "Yahoo Mail", "host": "imap.mail.yahoo.com", "port": 993, "security": "tls",
                   "help_url": "https://help.yahoo.com/kb/SLN15241.html"},
    "imap": {"label": "Email (IMAP)", "host": None, "port": 993, "security": "tls", "help_url": None},
    "fastmail_dav": {"label": "Fastmail contacts and calendars", "host": None, "port": None, "security": None,
                     "help_url": "https://www.fastmail.help/hc/en-us/articles/360058752854-App-passwords"},
    "icloud_dav": {"label": "iCloud contacts and calendars", "host": None, "port": None, "security": None,
                   "help_url": "https://support.apple.com/en-us/102654"},
    "facebook": {"label": "Facebook export", "host": None, "port": None, "security": None,
                 "help_url": "https://www.facebook.com/dyi"},
    "imessage": {"label": "Apple Messages export", "host": None, "port": None, "security": None,
                 "help_url": "https://github.com/ReagentX/imessage-exporter"},
    "folders": {"label": "Folders on this server", "host": None, "port": None, "security": None, "help_url": None},
    "model": {"label": "AI model on your network", "host": None, "port": None, "security": None, "help_url": None},
}


class ConnectionProblem(Exception):
    """A plain-language reason the connection could not be made or used. Never contains a secret."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def secret_path(credentials_dir: Path, source_id: str) -> Path:
    return Path(credentials_dir) / f"{source_id}.password"


def write_secret_path(credentials_dir: Path, source_id: str) -> Path:
    """The second app password that mail management's action runner uses to change mail (towpath.mail.executor)."""
    return Path(credentials_dir) / f"{source_id}.act-password"


GOOGLE_CLIENT_FILE = "google-web-client.json"


def token_path(credentials_dir: Path, c: dict) -> Path:
    """A Gmail connection's OAuth token file, inside the credentials folder."""
    return Path(credentials_dir) / c["settings"]["token"]


def has_credential(credentials_dir: Path, c: dict) -> bool:
    if c["adapter"] in ("recoll", "facebook", "imessage"):
        return True  # folders and exports on the server need no sign-in
    path = token_path(credentials_dir, c) if c["adapter"] == "gmail" else secret_path(credentials_dir, c["source_id"])
    return path.is_file()


def _write_secret(path: Path, value: str) -> None:
    """Atomically replace ``path`` with ``value``, readable only by this service."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _row(row) -> dict:
    out = dict(row)
    for key in ("settings", "folders", "progress"):
        out[key] = json.loads(out[key]) if out.get(key) else None
    return out


def all_connections(store_dir: Path, role: str = ROLE) -> list[dict]:
    with closing(open_store(store_dir, "connections", role)) as db:
        return [_row(r) for r in db.execute("SELECT * FROM connections ORDER BY created_at")]


def get(store_dir: Path, source_id: str, role: str = ROLE) -> dict | None:
    with closing(open_store(store_dir, "connections", role)) as db:
        row = db.execute("SELECT * FROM connections WHERE source_id = ?", (source_id,)).fetchone()
    return _row(row) if row else None


def _update(store_dir: Path, source_id: str, event: str | None = None, detail: dict | None = None, **fields):
    with closing(open_store(store_dir, "connections", ROLE)) as db:
        values = {k: json.dumps(v) if k in ("settings", "folders", "progress") and v is not None else v
                  for k, v in fields.items()}
        values["updated_at"] = _now()
        db.execute(f"UPDATE connections SET {', '.join(f'{k} = ?' for k in values)} WHERE source_id = ?",
                   (*values.values(), source_id))
        if event:
            db.execute("INSERT INTO connection_events (source_id, at, event, detail) VALUES (?, ?, ?, ?)",
                       (source_id, _now(), event, json.dumps(detail) if detail else None))
        db.commit()


def _new_id(store_dir: Path, provider: str, taken: set[str]) -> str:
    taken = taken | {c["source_id"] for c in all_connections(store_dir)}
    base = re.sub(r"[^a-z0-9-]", "-", provider.lower())
    n, sid = 1, base
    while sid in taken:
        n += 1
        sid = f"{base}-{n}"
    return sid


def _require(store_dir: Path, source_id: str) -> dict:
    found = get(store_dir, source_id)
    if found is None:
        raise ConnectionProblem("No such connection.")
    return found


def disconnect(store_dir: Path, credentials_dir: Path, source_id: str) -> None:
    """Delete the stored secret. The index stays, labelled disconnected; nothing is deleted at the provider."""
    c = _require(store_dir, source_id)
    (token_path(credentials_dir, c) if c["adapter"] == "gmail" else secret_path(credentials_dir, source_id)).unlink(
        missing_ok=True)
    write_secret_path(credentials_dir, source_id).unlink(missing_ok=True)
    _update(store_dir, source_id, "disconnected", state="disconnected", indexing="idle",
            settings={k: v for k, v in c["settings"].items() if k != "write"})

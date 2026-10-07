"""Accounts a person adds in the UI. Runs only in towpath-connect, the service that holds credentials.

Non-secret settings (provider, address, server, chosen folders), indexing state and progress live in the
``connections`` store, which the web service may read. Each secret lives in the connector's credentials
folder as one file, mode 0600, and is never stored elsewhere, displayed, logged or returned: it can only be
replaced or deleted. See docs/specs/connections-in-the-ui.md.

Indexing runs only after the owner starts it. The request worker runs it in bounded slices, so searches and
content requests are not held up; Pause takes effect within seconds and every stop is resumable.
"""

import dataclasses
import json
import os
import re
import secrets
import socket
import ssl
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from towpath.stores import open_store

ROLE = "connect"
PROVIDERS = {
    "fastmail": {"label": "Fastmail", "host": "imap.fastmail.com", "port": 993, "security": "tls",
                 "help_url": "https://www.fastmail.help/hc/en-us/articles/360058752854-App-passwords"},
    "imap": {"label": "Email (IMAP)", "host": None, "port": 993, "security": "tls", "help_url": None},
}
# Folders left out by default: special use (RFC 6154) first, then common names for servers that omit it.
SKIP_SPECIAL = {"\\trash", "\\junk", "\\drafts"}
SKIP_NAMES = {"trash", "deleted items", "deleted messages", "spam", "junk", "junk mail", "junk e-mail", "drafts"}
SLICE_SECONDS = 300
CHECK_EVERY_SECONDS = 3.0


class ConnectionProblem(Exception):
    """A plain-language reason the connection could not be made or used. Never contains a secret."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def secret_path(credentials_dir: Path, source_id: str) -> Path:
    return Path(credentials_dir) / f"{source_id}.password"


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


def _imap_source(sid: str, settings: dict, credential: str):
    from towpath import config as config_mod

    raw = {"id": sid, "kind": "mail-provider", "adapter": "imap", "host": settings["host"],
           "port": settings["port"], "security": settings["security"], "username": settings["username"],
           "password": credential, "timeout_seconds": settings.get("timeout_seconds", 60)}
    if settings.get("mailboxes"):
        raw["mailboxes"] = list(settings["mailboxes"])
    try:
        return config_mod._imap_source(sid, raw, Path("/"))
    except config_mod.ConfigError as exc:
        raise ConnectionProblem(str(exc).split(": ", 1)[-1].capitalize() + ".") from None


def _default_include(name: str, flags: list[str]) -> bool:
    if {f.lower() for f in flags} & SKIP_SPECIAL:
        return False
    return name.rsplit("/", 1)[-1].rsplit(".", 1)[-1].lower() not in SKIP_NAMES


def test_imap(settings: dict, password: str) -> list[dict]:
    """Log in, list folders and open each read-only. Returns folders with message counts, or raises
    ConnectionProblem with a reason a person can act on."""
    from towpath.adapters.imap import ReadOnlyViolation, connect_session

    try:
        from imapclient.exceptions import IMAPClientError, LoginError
    except ImportError:
        raise ConnectionProblem("This image lacks IMAP support.") from None
    if not password:
        raise ConnectionProblem("Enter the app password.")
    source = _imap_source("setup-test", {**settings, "timeout_seconds": 20}, "none")
    where = f"{source.host}:{source.port}"
    try:
        session = connect_session(source, password)
    except LoginError:
        raise ConnectionProblem("The server rejected the address or password. Check both; for Fastmail, use an "
                                "app password, not your account password.") from None
    except socket.gaierror:
        raise ConnectionProblem(f"Couldn't find the server {source.host}. Check the server name.") from None
    except ssl.SSLError:
        raise ConnectionProblem(f"Couldn't make a secure connection to {where}.") from None
    except (TimeoutError, socket.timeout):
        raise ConnectionProblem(f"{where} didn't answer in time.") from None
    except (OSError, IMAPClientError):
        raise ConnectionProblem(f"Couldn't connect to {where}.") from None
    try:
        folders = []
        for name, flags in session.folders():
            info = session.examine(name)
            special = next((f for f in flags if f.lower() in {"\\trash", "\\junk", "\\drafts", "\\sent",
                                                               "\\archive", "\\all", "\\flagged"}), None)
            folders.append({"name": name, "messages": int(info["exists"] or 0),
                            "special": special.lstrip("\\") if special else None,
                            "include": _default_include(name, flags)})
    except ReadOnlyViolation:
        raise ConnectionProblem("The server would not open folders read-only, so Towpath won't use it.") from None
    except (OSError, IMAPClientError):
        raise ConnectionProblem(f"The connection to {where} failed while listing folders. Try again.") from None
    finally:
        session.logout()
    if not folders:
        raise ConnectionProblem("The account has no folders Towpath can read.")
    return folders


# -- the connections store ------------------------------------------------------------------------------


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


def add_imap(store_dir: Path, credentials_dir: Path, provider: str, settings: dict, password: str,
             folders: list[dict], taken: set[str] = frozenset()) -> str:
    """Record a tested account and store its password. Folders are chosen next; nothing is indexed yet."""
    if provider not in PROVIDERS:
        raise ConnectionProblem("Unknown provider.")
    sid = _new_id(store_dir, provider, set(taken))
    _imap_source(sid, settings, "none")  # validates the settings before anything is written
    _write_secret(secret_path(credentials_dir, sid), password)
    label = PROVIDERS[provider]["label"] if provider != "imap" else settings["host"]
    now = _now()
    with closing(open_store(store_dir, "connections", ROLE)) as db:
        db.execute("""INSERT INTO connections (source_id, provider, adapter, display_name, settings, folders,
                      state, indexing, created_at, updated_at)
                      VALUES (?, ?, 'imap', ?, ?, ?, 'choose-folders', 'idle', ?, ?)""",
                   (sid, provider, label, json.dumps(settings), json.dumps(folders), now, now))
        db.execute("INSERT INTO connection_events (source_id, at, event) VALUES (?, ?, 'added')", (sid, now))
        db.commit()
    return sid


def _require(store_dir: Path, source_id: str) -> dict:
    found = get(store_dir, source_id)
    if found is None:
        raise ConnectionProblem("No such connection.")
    return found


def replace_password(store_dir: Path, credentials_dir: Path, source_id: str, password: str,
                     folders: list[dict]) -> None:
    """Store a new, already tested password; refresh the folder list."""
    _require(store_dir, source_id)
    _write_secret(secret_path(credentials_dir, source_id), password)
    _update(store_dir, source_id, "password-replaced", folders=folders, last_error=None,
            state="ready" if get(store_dir, source_id)["settings"].get("mailboxes") else "choose-folders")


def choose_folders(store_dir: Path, source_id: str, names: list[str]) -> None:
    found = _require(store_dir, source_id)
    known = {f["name"] for f in found["folders"] or ()}
    chosen = [n for n in names if n in known]
    if not chosen:
        raise ConnectionProblem("Choose at least one folder.")
    settings = {**found["settings"], "mailboxes": chosen}
    _update(store_dir, source_id, "folders-chosen", {"folders": len(chosen)}, settings=settings, state="ready")


def start_indexing(store_dir: Path, credentials_dir: Path, source_id: str) -> None:
    found = _require(store_dir, source_id)
    if found["state"] != "ready":
        raise ConnectionProblem("Choose folders first." if found["state"] == "choose-folders"
                                else "Reconnect the account first.")
    if not secret_path(credentials_dir, source_id).is_file():
        raise ConnectionProblem("The password is missing; replace it first.")
    if found["indexing"] not in ("requested", "running"):
        _update(store_dir, source_id, "indexing-requested", indexing="requested", last_error=None)


def pause_indexing(store_dir: Path, source_id: str) -> None:
    if _require(store_dir, source_id)["indexing"] in ("requested", "running"):
        _update(store_dir, source_id, "indexing-paused", indexing="paused")


def disconnect(store_dir: Path, credentials_dir: Path, source_id: str) -> None:
    """Delete the stored secret. The index stays, labelled disconnected; nothing is deleted at the provider."""
    _require(store_dir, source_id)
    secret_path(credentials_dir, source_id).unlink(missing_ok=True)
    _update(store_dir, source_id, "disconnected", state="disconnected", indexing="idle")


def merged(config, credentials_dir: Path | None, role: str = ROLE):
    """``config`` with every connection added as a source. Without a credentials folder (the web service) the
    sources carry no credential reference at all, so nothing there can resolve a secret."""
    added = {}
    for c in all_connections(config.store_dir, role):
        if c["source_id"] in config.sources or c["adapter"] != "imap":
            continue
        ref = f"file:{secret_path(credentials_dir, c['source_id'])}" if credentials_dir else "none"
        added[c["source_id"]] = _imap_source(c["source_id"], c["settings"], ref)
    return dataclasses.replace(config, sources={**config.sources, **added}) if added else config


# -- indexing (request worker) --------------------------------------------------------------------------


def _plain_stop(termination: str, reason: str | None) -> str:
    if termination == "auth-stop":
        return "The server rejected the stored password. Replace it to continue."
    if termination == "server-stop":
        return "The server or network stopped answering. Indexing will resume when you start it again."
    return reason or termination


def _tracker(store_dir: Path, sid: str, deadline: float, clock):
    """Progress that saves counts (never content) for the UI and ends the slice on Pause or at the deadline."""
    from towpath.connect import Capped
    from towpath.progress import Progress

    class Tracker(Progress):
        stop = None

        def __init__(self):
            super().__init__(sid, self.record, every_items=25, every_seconds=5.0)
            self.last_check = clock()

        def item(self):
            super().item()
            now = clock()
            if now - self.last_check >= CHECK_EVERY_SECONDS:
                self.last_check = now
                if (get(store_dir, sid) or {}).get("indexing") != "running":
                    self.stop = "paused"
                    raise Capped("paused by the owner; resume to continue")
            if now >= deadline:
                self.stop = "slice"
                raise Capped("slice finished; indexing continues")

        @staticmethod
        def record(snapshot):
            with closing(open_store(store_dir, "source", "connect")) as db:
                total = db.execute("SELECT COUNT(*) FROM items WHERE source_id = ? AND absent_since_run IS NULL",
                                   (sid,)).fetchone()[0]
            _update(store_dir, sid, progress={"phase": snapshot["phase"], "processed": snapshot["processed"],
                                              "rate": snapshot["rate"], "indexed": total, "at": _now()})

    return Tracker()


def index_pending(config, credentials_dir: Path, slice_seconds: float = SLICE_SECONDS,
                  clock=time.monotonic) -> list[dict]:
    """Run one bounded slice for each connection the owner asked to index. Returns a summary per slice."""
    from towpath import connect

    store_dir = config.store_dir
    summaries = []
    for c in all_connections(store_dir):
        if c["indexing"] not in ("requested", "running") or c["state"] != "ready":
            continue
        sid = c["source_id"]
        _update(store_dir, sid, "indexing-slice" if c["indexing"] == "running" else "indexing-started",
                indexing="running")
        tracker = _tracker(store_dir, sid, clock() + slice_seconds, clock)
        try:
            result = connect.sync(merged(config, credentials_dir), sid, progress=tracker)
        except Exception as exc:  # noqa: BLE001 - recorded by sync; keep data out of the store
            _update(store_dir, sid, "indexing-error", {"type": type(exc).__name__}, indexing="idle",
                    last_error="Indexing stopped on an unexpected error. Progress is saved; start it again.")
            summaries.append({"source_id": sid, "termination": "error"})
            continue
        termination = result["termination"]
        tracker.record({"phase": "finished", "processed": tracker.processed, "rate": tracker.rate()})
        if termination == "complete":
            _update(store_dir, sid, "indexed", {"total": result["indexed_total"]}, indexing="idle")
        elif not (termination == "capped" and tracker.stop in ("slice", "paused")):
            _update(store_dir, sid, "indexing-stopped", {"termination": termination}, indexing="idle",
                    last_error=_plain_stop(termination, result.get("reason")))
        summaries.append({"source_id": sid, "termination": termination, "slice": tracker.stop,
                          "indexed_total": result["indexed_total"]})
    return summaries

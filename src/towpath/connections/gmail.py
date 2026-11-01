"""Gmail connections: the instance's Google client, stored tokens, pacing, and sources from the configuration file."""

import dataclasses
import json
import os
from contextlib import closing
from pathlib import Path

from towpath.connections.base import (
    GOOGLE_CLIENT_FILE,
    ROLE,
    ConnectionProblem,
    _new_id,
    _now,
    _require,
    _update,
    _write_secret,
    all_connections,
    token_path,
)
from towpath.stores import open_store

def _setting(store_dir: Path, key: str, role: str = ROLE):
    import sqlite3

    with closing(open_store(store_dir, "connections", role)) as db:
        try:
            row = db.execute("SELECT value FROM instance_settings WHERE key = ?", (key,)).fetchone()
        except sqlite3.OperationalError:  # a store written before this setting existed
            return None
    return json.loads(row[0]) if row else None


def gmail_pacing(config, role: str = ROLE):
    """The Gmail pacing in force: set on the Connections page, else the configuration file's."""
    from towpath.quota import PacingSettings

    saved = _setting(config.store_dir, "gmail_pacing", role)
    return PacingSettings.from_table(saved) if saved else config.gmail_pacing


def set_verified_quota(config, verified: int | None) -> None:
    """Pace Gmail from the per-user quota shown in Google Cloud Console: 30% of it, never above 1,800 units a
    minute. ``None`` returns to Google's default pacing. Invalid values are refused, not adjusted."""
    from towpath.quota import PacingSettings

    current = dataclasses.asdict(gmail_pacing(config))
    if verified is None:
        defaults = dataclasses.asdict(PacingSettings())
        table = {**current, "verified_units_per_minute": None, "units_per_minute": defaults["units_per_minute"],
                 "min_interval_seconds": defaults["min_interval_seconds"]}
    else:
        table = {**current, "verified_units_per_minute": verified,
                 "units_per_minute": min(1800, verified * 3 // 10),
                 "min_interval_seconds": min(current["min_interval_seconds"], 0.667)}
    table = {k: v for k, v in table.items() if v is not None}
    try:
        PacingSettings.from_table(table)
    except ValueError as exc:
        raise ConnectionProblem(str(exc).split(" (got")[0].capitalize() + ".") from None
    with closing(open_store(config.store_dir, "connections", ROLE)) as db:
        db.execute("INSERT OR REPLACE INTO instance_settings (key, value, updated_at) VALUES ('gmail_pacing', ?, ?)",
                   (json.dumps(table), _now()))
        db.commit()


def import_configured(config, credentials_dir: Path) -> list[str]:
    """Bring Gmail sources and Gmail pacing from the configuration file onto the Connections page, once. The
    index, source ID, quota history and token stay as they are: no re-consent and no re-indexing."""
    import shutil

    from towpath.quota import PacingSettings

    if _setting(config.store_dir, "gmail_pacing") is None and config.gmail_pacing != PacingSettings():
        table = {k: v for k, v in dataclasses.asdict(config.gmail_pacing).items() if v is not None}
        with closing(open_store(config.store_dir, "connections", ROLE)) as db:
            db.execute("INSERT OR IGNORE INTO instance_settings (key, value, updated_at) VALUES ('gmail_pacing', ?, ?)",
                       (json.dumps(table), _now()))
            db.commit()
    imported = []
    known = {c["source_id"] for c in all_connections(config.store_dir)}
    for sid, source in config.sources.items():
        if source.adapter != "gmail" or sid in known or source.token is None:
            continue
        token = Path(source.token)
        if token.parent.resolve() == Path(credentials_dir).resolve():
            name = token.name
        else:
            name = f"{sid}.token.json"
            if token.is_file():
                shutil.copy2(token, Path(credentials_dir) / name)
                os.chmod(Path(credentials_dir) / name, 0o600)
        with closing(open_store(config.store_dir, "source", "connect")) as db:
            total = db.execute("SELECT COUNT(*) FROM items WHERE source_id = ? AND absent_since_run IS NULL",
                               (sid,)).fetchone()[0]
            last = db.execute("SELECT MAX(finished_at) FROM runs WHERE source_id = ? AND complete = 1",
                              (sid,)).fetchone()[0]
        now = _now()
        progress = {"phase": "imported", "processed": 0, "rate": None, "indexed": total, "at": last or now}
        with closing(open_store(config.store_dir, "connections", ROLE)) as db:
            db.execute("""INSERT OR IGNORE INTO connections (source_id, provider, adapter, display_name, settings,
                          state, indexing, progress, created_at, updated_at)
                          VALUES (?, 'gmail', 'gmail', 'Gmail', ?, 'ready', 'idle', ?, ?, ?)""",
                       (sid, json.dumps({"token": name, "origin": "config", "username": None}), json.dumps(progress),
                        now, now))
            db.execute("INSERT INTO connection_events (source_id, at, event) VALUES (?, ?, 'imported')", (sid, now))
            db.commit()
        imported.append(sid)
    return imported


def google_client(credentials_dir: Path) -> dict | None:
    """The instance's Google OAuth web client, if one was entered (its ID is shown; its secret never is)."""
    path = Path(credentials_dir) / GOOGLE_CLIENT_FILE
    return json.loads(path.read_text())["web"] if path.is_file() else None


def save_google_client(credentials_dir: Path, client_id: str, client_secret: str, redirect_uri: str) -> None:
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id.endswith(".apps.googleusercontent.com"):
        raise ConnectionProblem("That doesn't look like a Google client ID; it ends in .apps.googleusercontent.com.")
    if not client_secret:
        raise ConnectionProblem("Enter the client secret.")
    _write_secret(Path(credentials_dir) / GOOGLE_CLIENT_FILE, json.dumps({"web": {
        "client_id": client_id, "client_secret": client_secret, "redirect_uris": [redirect_uri],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}))


def save_gmail(store_dir: Path, credentials_dir: Path, token_json: str, address: str | None,
               reconnect: str | None = None, taken: set[str] = frozenset()) -> str:
    """Store a verified read-only Gmail token as a new connection, or as a reconnection of ``reconnect``."""
    if reconnect is not None:
        c = _require(store_dir, reconnect)
        known = c["settings"].get("username")
        if known and address and known.lower() != address.lower():
            raise ConnectionProblem(f"That signed in as {address}, but this connection is {known}. "
                                    "Sign in with the same Google account, or add it as a new connection.")
        _write_secret(token_path(credentials_dir, c), token_json)
        settings = {**c["settings"], "username": address or known}
        _update(store_dir, reconnect, "reconnected", settings=settings, state="ready", last_error=None)
        return reconnect
    sid = _new_id(store_dir, "gmail", set(taken))
    name = f"{sid}.token.json"
    _write_secret(Path(credentials_dir) / name, token_json)
    now = _now()
    with closing(open_store(store_dir, "connections", ROLE)) as db:
        db.execute("""INSERT INTO connections (source_id, provider, adapter, display_name, settings, state, indexing,
                      created_at, updated_at) VALUES (?, 'gmail', 'gmail', 'Gmail', ?, 'ready', 'idle', ?, ?)""",
                   (sid, json.dumps({"token": name, "username": address}), now, now))
        db.execute("INSERT INTO connection_events (source_id, at, event) VALUES (?, ?, 'added')", (sid, now))
        db.commit()
    return sid

"""Read-only exports in the server's library (Facebook, Apple Messages), recorded as sources."""

import json
from contextlib import closing
from pathlib import Path

from towpath.connections.base import ROLE, ConnectionProblem, _new_id, _now
from towpath.stores import open_store

def add_messages(store_dir: Path, path: Path, taken: set[str] = frozenset()) -> str:
    """Record an Apple Messages export folder (imessage-exporter, text format) as a source; nothing is read yet."""
    from towpath import folders
    from towpath.adapters.messages_export import looks_like_export

    base = folders.library().resolve()
    path = Path(path).resolve()
    if not folders._inside(base, path) or not path.is_dir():
        raise ConnectionProblem("Choose a folder on the server's library.")
    if not looks_like_export(path):
        raise ConnectionProblem("That folder doesn't look like an imessage-exporter export in text format (no "
                                "conversation files).")
    sid = _new_id(store_dir, "messages", set(taken))
    now = _now()
    with closing(open_store(store_dir, "connections", ROLE)) as db:
        db.execute("""INSERT INTO connections (source_id, provider, adapter, display_name, settings, folders, state,
                      indexing, created_at, updated_at)
                      VALUES (?, 'imessage', 'imessage', 'Apple Messages export', ?, NULL, 'ready', 'idle', ?, ?)""",
                   (sid, json.dumps({"path": str(path), "username": path.name}), now, now))
        db.execute("INSERT INTO connection_events (source_id, at, event) VALUES (?, ?, 'added')", (sid, now))
        db.commit()
    return sid


def add_facebook(store_dir: Path, path: Path, taken: set[str] = frozenset()) -> str:
    """Record a Facebook export folder (under the read-only library) as a source; nothing is read yet."""
    from towpath import folders
    from towpath.adapters.facebook import looks_like_export

    base = folders.library().resolve()
    path = Path(path).resolve()
    if not folders._inside(base, path) or not path.is_dir():
        raise ConnectionProblem("Choose a folder on the server's library.")
    if not looks_like_export(path):
        raise ConnectionProblem("That folder doesn't look like a Facebook export in JSON format (no posts or "
                                "message files).")
    sid = _new_id(store_dir, "facebook", set(taken))
    now = _now()
    with closing(open_store(store_dir, "connections", ROLE)) as db:
        db.execute("""INSERT INTO connections (source_id, provider, adapter, display_name, settings, folders, state,
                      indexing, created_at, updated_at)
                      VALUES (?, 'facebook', 'facebook', 'Facebook export', ?, NULL, 'ready', 'idle', ?, ?)""",
                   (sid, json.dumps({"path": str(path), "username": path.name}), now, now))
        db.execute("INSERT INTO connection_events (source_id, at, event) VALUES (?, ?, 'added')", (sid, now))
        db.commit()
    return sid

"""Fail-closed consumer grants for mail sources in context packets.

A mail source contributes nothing to a context packet until the owner grants that purpose on it
(``agent-context`` or ``life-evidence``), and contributes excerpt text only with ``excerpt`` as
well. No row means no. File roots keep their own grants (towpath.discovery.policy), which already
cover the same purposes per root. Grants live in the decisions store, written by the web role and
logged in ``decision_log``.
"""

import json
from contextlib import closing
from datetime import datetime, timezone

from towpath.stores import open_store
from towpath.unified.contracts import check_source_id

FEATURES = ("agent-context", "life-evidence", "excerpt")
PURPOSES = ("agent-context", "life-evidence")
WRITER = "web"
READER = "connect"


class Denied(PermissionError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check(source_id: str, feature: str) -> None:
    check_source_id(source_id)
    if feature not in FEATURES:
        raise ValueError(f"feature must be one of {', '.join(FEATURES)}")


def grant(store_dir, source_id: str, feature: str, author: str = "local-user") -> None:
    _check(source_id, feature)
    with closing(open_store(store_dir, "decisions", WRITER)) as db:
        db.execute("INSERT OR REPLACE INTO source_grants VALUES (?,?,?,?)", (source_id, feature, author, _now()))
        db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES ('source-grant',?,?,?,?)",
                   (f"source:{source_id}", json.dumps({"feature": feature}), author, _now()))
        db.commit()


def revoke(store_dir, source_id: str, feature: str, author: str = "local-user") -> bool:
    _check(source_id, feature)
    with closing(open_store(store_dir, "decisions", WRITER)) as db:
        removed = db.execute("DELETE FROM source_grants WHERE source_id = ? AND feature = ?",
                             (source_id, feature)).rowcount
        db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES ('source-revoke',?,?,?,?)",
                   (f"source:{source_id}", json.dumps({"feature": feature}), author, _now()))
        db.commit()
    return bool(removed)


def granted(store_dir) -> dict[str, set[str]]:
    with closing(open_store(store_dir, "decisions", READER)) as db:
        rows = db.execute("SELECT source_id, feature FROM source_grants").fetchall()
    out: dict[str, set[str]] = {}
    for row in rows:
        if row["feature"] in FEATURES:
            out.setdefault(row["source_id"], set()).add(row["feature"])
    return out


def item_settings(store_dir, *targets: str) -> dict:
    """Owner settings (audience, model use) recorded for any of ``targets`` (an item ID or a reference)."""
    wanted = [t for t in targets if t]
    if not wanted:
        return {}
    with closing(open_store(store_dir, "decisions", READER)) as db:
        rows = db.execute(f"SELECT key, value FROM settings WHERE target_id IN ({','.join('?' for _ in wanted)}) "
                          "ORDER BY at", wanted).fetchall()
    return {row["key"]: row["value"] for row in rows}

"""Fail-closed grants for file discovery, per root and feature.

Grants live in the decisions store (written by the web role, logged in
``decision_log``). No row means no. A ``search`` grant allows nothing else:
excerpts, recovery, agent context, and life evidence each need their own.
Model-endpoint grants are separate again; file discovery calls no model.
"""

import json
from datetime import datetime, timezone

from towpath.stores import open_store

FEATURES = ("search", "excerpt", "recover", "agent-context", "life-evidence")
WRITER = "web"
READER = "connect"


class Denied(PermissionError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check(config, root: str, feature: str) -> None:
    if feature not in FEATURES:
        raise ValueError(f"feature must be one of {', '.join(FEATURES)}")
    if config.files is None or root not in config.files.roots:
        raise ValueError(f"unknown files root {root!r}")


def grant(config, root: str, feature: str, author: str = "local-user") -> None:
    _check(config, root, feature)
    db = open_store(config.store_dir, "decisions", WRITER)
    db.execute("INSERT OR REPLACE INTO file_grants (root_alias, feature, author, at) VALUES (?,?,?,?)",
               (root, feature, author, _now()))
    db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES ('file-grant', ?, ?, ?, ?)",
               (f"root:{root}", json.dumps({"feature": feature}), author, _now()))
    db.commit()
    db.close()


def revoke(config, root: str, feature: str, author: str = "local-user") -> bool:
    _check(config, root, feature)
    db = open_store(config.store_dir, "decisions", WRITER)
    removed = db.execute("DELETE FROM file_grants WHERE root_alias = ? AND feature = ?", (root, feature)).rowcount
    db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES ('file-revoke', ?, ?, ?, ?)",
               (f"root:{root}", json.dumps({"feature": feature}), author, _now()))
    db.commit()
    db.close()
    return bool(removed)


def granted(config) -> dict[str, set[str]]:
    """Root alias -> granted features, read-only. Unknown roots and features are ignored."""
    db = open_store(config.store_dir, "decisions", READER)
    try:
        rows = db.execute("SELECT root_alias, feature FROM file_grants").fetchall()
    finally:
        db.close()
    out: dict[str, set[str]] = {}
    for row in rows:
        if config.files and row["root_alias"] in config.files.roots and row["feature"] in FEATURES:
            out.setdefault(row["root_alias"], set()).add(row["feature"])
    return out


def allowed(config, root: str, feature: str) -> bool:
    return feature in granted(config).get(root, set())


def require(config, root: str, feature: str) -> None:
    if not allowed(config, root, feature):
        raise Denied(f"no {feature} grant for files root {root}")

"""The web role's writes: human decisions, kept apart from rebuildable data."""

import json
from datetime import datetime, timezone

from towpath.stores import open_store

ROLE = "web"
SETTINGS = {
    "audience": {"owner", "shareable"},
    "model_use": {"follow-grants", "local-only", "excluded"},
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dismiss(config, proposal_id: str, reason: str = "", author: str = "local-user") -> str:
    derived = open_store(config.store_dir, "derived", ROLE)
    row = derived.execute("SELECT match_key FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
    derived.close()
    if row is None:
        raise KeyError(f"no proposal {proposal_id}")
    db = open_store(config.store_dir, "decisions", ROLE)
    db.execute("INSERT OR REPLACE INTO dismissals (match_key, proposal_id, reason, author, at) VALUES (?,?,?,?,?)",
               (row["match_key"], proposal_id, reason, author, now()))
    db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES ('dismiss', ?, ?, ?, ?)",
               (proposal_id, json.dumps({"match_key": row["match_key"], "reason": reason}), author, now()))
    db.commit()
    db.close()
    return row["match_key"]


def set_item(config, item_id: str, author: str = "local-user", **values: str | None) -> dict:
    changed = {}
    db = open_store(config.store_dir, "decisions", ROLE)
    for key, value in values.items():
        if value is None:
            continue
        if value not in SETTINGS[key]:
            raise ValueError(f"{key} must be one of {sorted(SETTINGS[key])}")
        db.execute("INSERT OR REPLACE INTO settings (target_id, key, value, author, at) VALUES (?,?,?,?,?)",
                   (item_id, key, value, author, now()))
        db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES ('setting', ?, ?, ?, ?)",
                   (item_id, json.dumps({key: value}), author, now()))
        changed[key] = value
    db.commit()
    db.close()
    return changed


def grant_model(config, endpoint_id: str, data_class: str, author: str = "local-user") -> str:
    """Allow a data class on one endpoint, tied to its current fingerprint."""
    from towpath.config import DATA_CLASSES
    from towpath.models.profiles import fingerprint

    if data_class not in DATA_CLASSES:
        raise ValueError(f"data class must be one of {sorted(DATA_CLASSES)}")
    endpoint = config.endpoints[endpoint_id]
    fp = fingerprint(endpoint)
    db = open_store(config.store_dir, "decisions", ROLE)
    db.execute("INSERT OR REPLACE INTO model_grants (endpoint_id, fingerprint, data_class, author, at) "
               "VALUES (?,?,?,?,?)", (endpoint_id, fp, data_class, author, now()))
    db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES ('model-grant', ?, ?, ?, ?)",
               (endpoint_id, json.dumps({"fingerprint": fp, "data_class": data_class,
                                         "destination": endpoint.destination}), author, now()))
    db.commit()
    db.close()
    return fp

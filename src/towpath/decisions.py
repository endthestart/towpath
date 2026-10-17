"""The web role's writes: human decisions, kept apart from rebuildable data."""

import json
from datetime import datetime, timezone

from towpath.stores import open_store

ROLE = "web"
SETTINGS = {
    "audience": {"owner", "shareable"},
    "model_use": {"follow-grants", "no-cloud", "local-only", "excluded"},
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


def mark_folder(store_dir, provider_id: str, root: str, path: str, keep: bool, author: str = "local-user") -> None:
    """Keep a folder off Space's clutter list (``keep``), or undo that. Clutter inside it is still shown. The
    connector measures that root again on its next poll."""
    from towpath import space

    target = space.keep_target(provider_id, root, path)
    db = open_store(store_dir, "decisions", ROLE)
    try:
        if keep:
            db.execute("INSERT OR REPLACE INTO settings (target_id, key, value, author, at) VALUES (?,?,?,?,?)",
                       (target, space.KEEP_KEY, space.KEEP_VALUE, author, now()))
        else:
            db.execute("DELETE FROM settings WHERE target_id = ? AND key = ?", (target, space.KEEP_KEY))
        db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES (?, ?, ?, ?, ?)",
                   ("space-keep" if keep else "space-unkeep", target,
                    json.dumps({"provider": provider_id, "root": root, "path": path}), author, now()))
        db.commit()
    finally:
        db.close()


def revoke_model(config, endpoint_id: str, data_class: str, author: str = "local-user") -> bool:
    """Withdraw a data class from one endpoint, whatever fingerprint it was granted under."""
    db = open_store(config.store_dir, "decisions", ROLE)
    removed = db.execute("DELETE FROM model_grants WHERE endpoint_id = ? AND data_class = ?",
                         (endpoint_id, data_class)).rowcount
    db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES ('model-revoke', ?, ?, ?, ?)",
               (endpoint_id, json.dumps({"data_class": data_class}), author, now()))
    db.commit()
    db.close()
    return removed > 0


def model_granted(config, endpoint_id: str, data_class: str, role: str = ROLE) -> bool:
    """Whether the owner allowed a data class on the endpoint as it is configured now (read-only for other roles)."""
    from towpath.models.profiles import fingerprint

    endpoint = config.endpoints.get(endpoint_id)
    if endpoint is None:
        return False
    db = open_store(config.store_dir, "decisions", role)
    try:
        return db.execute("SELECT 1 FROM model_grants WHERE endpoint_id = ? AND fingerprint = ? AND data_class = ?",
                          (endpoint_id, fingerprint(endpoint), data_class)).fetchone() is not None
    finally:
        db.close()


MODELS_TARGET, TASKS_KEY = "models", "tasks"


def task_models(store_dir, role: str = ROLE) -> dict[str, str]:
    """Which model endpoint the owner chose for each task, where they chose one."""
    from contextlib import closing

    from towpath.stores import store_path

    if not store_path(store_dir, "decisions").exists():
        return {}
    with closing(open_store(store_dir, "decisions", role)) as db:
        row = db.execute("SELECT value FROM settings WHERE target_id = ? AND key = ?",
                         (MODELS_TARGET, TASKS_KEY)).fetchone()
    return json.loads(row[0]) if row else {}


def set_task_model(store_dir, task: str, endpoint_id: str | None, author: str = "local-user") -> dict:
    """Choose the model for one task (None: back to the default, the first model connection)."""
    chosen = {k: v for k, v in task_models(store_dir).items() if k != task}
    if endpoint_id:
        chosen[task] = endpoint_id
    db = open_store(store_dir, "decisions", ROLE)
    try:
        db.execute("INSERT OR REPLACE INTO settings (target_id, key, value, author, at) VALUES (?,?,?,?,?)",
                   (MODELS_TARGET, TASKS_KEY, json.dumps(chosen, sort_keys=True), author, now()))
        db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES ('model-task', ?, ?, ?, ?)",
                   (MODELS_TARGET, json.dumps({"task": task, "endpoint": endpoint_id}), author, now()))
        db.commit()
    finally:
        db.close()
    return chosen


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

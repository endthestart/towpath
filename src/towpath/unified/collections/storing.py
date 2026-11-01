"""Collections as the owner keeps them: creating, reading, adding and removing, and accepting an evaluation."""

import json
import uuid
from contextlib import closing
from datetime import datetime, timezone

from towpath.stores import open_store
from towpath.unified.contracts import Filters, Reference

SCHEMA = "towpath.collection/0"
EVALUATION_SCHEMA = "towpath.collection-evaluation/0"
KINDS = ("query", "set", "photos")
MAX_PHOTOS = 2000
WRITER = "web"
READER = "connect"  # any non-writer opens the decisions store read-only
MAX_PER_SOURCE = 500
MAX_NAME = 120


class CollectionError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(db, kind: str, cid: str, detail: dict, author: str) -> None:
    db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES (?, ?, ?, ?, ?)",
               (kind, f"collection:{cid}", json.dumps(detail, sort_keys=True), author, _now()))


def photo_filters(given: dict) -> dict:
    """A Photos-page search, cleaned: only its own fields, as short strings (``located`` as a flag)."""
    from towpath import photos

    clean = {}
    for key in photos.FILTERS:
        value = given.get(key)
        if key == "located":
            if value in (True, "1", 1):
                clean[key] = True
        elif isinstance(value, str) and value.strip():
            clean[key] = " ".join(value.split())[:200]
    if not clean:
        raise CollectionError("a photo collection needs a search (a camera, lens, dates, place or words)")
    return clean


def create(store_dir, name: str, kind: str, filters: Filters | None = None, author: str = "local-user",
           photos: dict | None = None) -> dict:
    name = (name or "").strip()
    if not name or len(name) > MAX_NAME:
        raise CollectionError(f"a collection needs a name of 1-{MAX_NAME} characters")
    if kind not in KINDS:
        raise CollectionError("kind must be query or set")
    if (kind == "query") != (filters is not None) or (kind == "photos") != (photos is not None):
        raise CollectionError("a query collection needs filters, a photo collection a photo search; a set takes none")
    cid = "col_" + uuid.uuid4().hex[:12]
    definition = json.dumps(filters.to_dict() if filters else photo_filters(photos), sort_keys=True) \
        if filters or photos else None
    with closing(open_store(store_dir, "decisions", WRITER)) as db:
        if db.execute("SELECT 1 FROM collections WHERE name = ?", (name,)).fetchone():
            raise CollectionError(f"a collection named {name!r} already exists")
        db.execute("INSERT INTO collections VALUES (?,?,?,?,?,?,?)", (cid, name, kind, definition, author, _now(),
                                                                       _now()))
        _log(db, "collection-create", cid, {"name": name, "kind": kind, "definition": definition}, author)
        db.commit()
    return get(store_dir, cid)


def _row(db, key: str):
    return db.execute("SELECT * FROM collections WHERE collection_id = ? OR name = ?", (key, key)).fetchone()


def get(store_dir, key: str, role: str = READER) -> dict:
    with closing(open_store(store_dir, "decisions", role)) as db:
        row = _row(db, key)
        if row is None:
            raise CollectionError(f"no collection {key!r}")
        items = [dict(r) for r in db.execute("SELECT * FROM collection_items WHERE collection_id = ? "
                                             "ORDER BY added_at, ref", (row["collection_id"],))]
    data = dict(row)
    data["schema"] = SCHEMA
    data["filters"] = json.loads(data.pop("definition")) if data.get("definition") else None
    data["members"] = [i for i in items if i["role"] == "member"]
    data["baseline"] = [i for i in items if i["role"] == "baseline"]
    return data


def list_all(store_dir, role: str = READER) -> list[dict]:
    with closing(open_store(store_dir, "decisions", role)) as db:
        rows = db.execute("""SELECT c.*, (SELECT count(*) FROM collection_items i WHERE i.collection_id =
                             c.collection_id AND i.role = 'member') AS members, (SELECT count(*) FROM
                             collection_items i WHERE i.collection_id = c.collection_id AND i.role = 'baseline')
                             AS baseline FROM collections c ORDER BY c.name""").fetchall()
    return [dict(r) for r in rows]


def add(store_dir, key: str, ref: str, version: str | None = None, title: str | None = None,
        source_type: str | None = None, note: str | None = None, author: str = "local-user") -> dict:
    """Add one reference to a set collection (or update its note). The version is what the owner saw."""
    Reference.parse(ref)
    with closing(open_store(store_dir, "decisions", WRITER)) as db:
        row = _row(db, key)
        if row is None:
            raise CollectionError(f"no collection {key!r}")
        if row["kind"] != "set":
            raise CollectionError("references are added to set collections; a query collection evaluates its query")
        db.execute("""INSERT INTO collection_items VALUES (?,?,'member',?,?,?,?,?,?)
                      ON CONFLICT (collection_id, ref, role) DO UPDATE SET note = excluded.note""",
                   (row["collection_id"], ref, version, (title or "")[:300] or None, source_type,
                    (note or "")[:1000] or None, author, _now()))
        db.execute("UPDATE collections SET updated_at = ? WHERE collection_id = ?", (_now(), row["collection_id"]))
        _log(db, "collection-add", row["collection_id"], {"ref": ref, "version": version}, author)
        db.commit()
        return {"collection_id": row["collection_id"], "ref": ref}


def remove(store_dir, key: str, ref: str, author: str = "local-user") -> bool:
    with closing(open_store(store_dir, "decisions", WRITER)) as db:
        row = _row(db, key)
        if row is None:
            raise CollectionError(f"no collection {key!r}")
        removed = db.execute("DELETE FROM collection_items WHERE collection_id = ? AND ref = ? AND role = 'member'",
                             (row["collection_id"], ref)).rowcount
        _log(db, "collection-remove", row["collection_id"], {"ref": ref}, author)
        db.commit()
    return bool(removed)


def accept(store_dir, key: str, evaluation: dict, author: str = "local-user") -> int:
    """Make an evaluation's current results the query collection's baseline (an owner decision)."""
    with closing(open_store(store_dir, "decisions", WRITER)) as db:
        row = _row(db, key)
        if row is None:
            raise CollectionError(f"no collection {key!r}")
        if row["kind"] not in ("query", "photos") or evaluation.get("collection_id") != row["collection_id"]:
            raise CollectionError("only a query or photo collection's own evaluation can become its baseline")
        current = [i for i in evaluation["items"] if i["status"] in {"added", "unchanged", "changed", "unverified"}]
        previous = {item["ref"]: dict(item) for item in db.execute(
            "SELECT * FROM collection_items WHERE collection_id = ? AND role = 'baseline'",
            (row["collection_id"],))}
        # A partial answer cannot establish that an unavailable baseline member stopped matching.
        retained = [previous[i["ref"]] for i in evaluation["items"]
                    if i["status"] == "unavailable" and i["ref"] in previous]
        db.execute("DELETE FROM collection_items WHERE collection_id = ? AND role = 'baseline'",
                   (row["collection_id"],))
        for item in current:
            db.execute("INSERT INTO collection_items VALUES (?,?,'baseline',?,?,?,?,?,?)",
                       (row["collection_id"], item["ref"], item["version_now"], item.get("title"),
                        item.get("source_type"), None, author, _now()))
        for item in retained:
            db.execute("INSERT INTO collection_items VALUES (?,?,'baseline',?,?,?,?,?,?)",
                       (row["collection_id"], item["ref"], item["version"], item["title"],
                        item["source_type"], item["note"], item["author"], item["added_at"]))
        db.execute("UPDATE collections SET updated_at = ? WHERE collection_id = ?", (_now(), row["collection_id"]))
        _log(db, "collection-accept", row["collection_id"],
             {"items": len(current) + len(retained), "retained_unavailable": len(retained),
              "partial": evaluation["partial"], "evaluated_at": evaluation["evaluated_at"]},
             author)
        db.commit()
    return len(current) + len(retained)

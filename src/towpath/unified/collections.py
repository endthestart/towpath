"""Durable collections: an owner-defined query, or an explicit set of references.

Collections live in the decisions store (written by the web role, every change logged in
``decision_log``) and hold references and versions only, never contents. Re-syncing, re-importing
or deleting a regenerated index leaves them untouched, and references are stable source identities,
so a collection survives a reindex. Evaluating a collection reports, per reference, whether it is
unchanged, changed, added, removed or unavailable, and whether the evaluation itself was partial.

- A ``set`` collection's members are explicit references, each with the version seen when it was added.
- A ``query`` collection stores its filters. Its ``baseline`` is the result set the owner last
  accepted; evaluation compares the current results with it.
"""

import json
import uuid
from contextlib import closing
from datetime import datetime, timezone

from towpath.stores import open_store
from towpath.unified import federation
from towpath.unified.contracts import ContractError, Filters, Reference, decode_cursor

SCHEMA = "towpath.collection/0"
EVALUATION_SCHEMA = "towpath.collection-evaluation/0"
KINDS = ("query", "set")
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


def create(store_dir, name: str, kind: str, filters: Filters | None = None, author: str = "local-user") -> dict:
    name = (name or "").strip()
    if not name or len(name) > MAX_NAME:
        raise CollectionError(f"a collection needs a name of 1-{MAX_NAME} characters")
    if kind not in KINDS:
        raise CollectionError("kind must be query or set")
    if (kind == "query") != (filters is not None):
        raise CollectionError("a query collection needs filters; a set collection takes none")
    cid = "col_" + uuid.uuid4().hex[:12]
    definition = json.dumps(filters.to_dict(), sort_keys=True) if filters else None
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
        if row["kind"] != "query" or evaluation.get("collection_id") != row["collection_id"]:
            raise CollectionError("only a query collection's own evaluation can become its baseline")
        current = [i for i in evaluation["items"] if i["status"] in {"added", "unchanged", "changed"}]
        db.execute("DELETE FROM collection_items WHERE collection_id = ? AND role = 'baseline'",
                   (row["collection_id"],))
        for item in current:
            db.execute("INSERT INTO collection_items VALUES (?,?,'baseline',?,?,?,?,?,?)",
                       (row["collection_id"], item["ref"], item["version_now"], item.get("title"),
                        item.get("source_type"), None, author, _now()))
        db.execute("UPDATE collections SET updated_at = ? WHERE collection_id = ?", (_now(), row["collection_id"]))
        _log(db, "collection-accept", row["collection_id"],
             {"items": len(current), "partial": evaluation["partial"], "evaluated_at": evaluation["evaluated_at"]},
             author)
        db.commit()
    return len(current)


# -- evaluation ---------------------------------------------------------------------------------------


def _compare(then: str | None, now: str | None) -> tuple[str, str | None]:
    if then is None or now is None:
        return "unverified", "no version token to compare; the reference resolves, but sameness is not proven"
    return ("unchanged", None) if then == now else ("changed", "the source reports a different version")


def _evaluate_set(adapters, collection: dict) -> tuple[list, list]:
    items, reasons = [], []
    for member in collection["members"]:
        entry = {"ref": member["ref"], "title": member["title"], "source_type": member["source_type"],
                 "version_then": member["version"], "version_now": None, "note": member["note"]}
        try:
            described = federation.describe(adapters, member["ref"])
        except federation.UnknownReference as exc:
            entry.update(status="unavailable", reason=str(exc))
        except Exception as exc:  # noqa: BLE001 - one member's source failing is reported, not fatal
            page = federation.error_page(Reference.parse(member["ref"]).source_id, exc)
            entry.update(status="unavailable", reason=f"{page.error.code}: {page.error.message}")
        else:
            result = described["result"]
            entry["version_now"] = result.get("version")
            state = described.get("state")
            if state in {"absent", "missing", "unavailable"} or result.get("availability") in {"absent", "missing"}:
                entry.update(status="unavailable", reason=described.get("reason") or f"the source reports it {state}")
            elif state == "changed":
                entry.update(status="changed", reason=described.get("reason"))
            else:
                status, reason = _compare(member["version"], entry["version_now"])
                if state == "not-checked":
                    reason = "checked against the local catalog only; the source itself was not asked"
                entry.update(status=status, reason=reason)
        items.append(entry)
    if any(i["status"] == "unavailable" for i in items):
        reasons.append("some members are unavailable now")
    return items, reasons


def _evaluate_query(adapters, collection: dict, max_per_source: int) -> tuple[list, list]:
    filters = Filters.from_dict(collection["filters"])
    current: dict[str, dict] = {}
    page_status: dict[str, dict] = {}
    cursor, reasons = None, []
    for _ in range(max_per_source // federation.MAX_LIMIT + 1):
        response = federation.search(adapters, filters, federation.MAX_LIMIT, cursor).to_dict()
        for page in response["sources"]:
            seen = page_status.setdefault(page["source_id"], {"status": page["status"], "count": 0})
            seen["count"] += page["result_count"]
            if page["status"] != "ok":
                seen["status"] = page["status"]
        for result in response["results"]:
            current.setdefault(result["ref"], result)
        if not response["complete"]:
            reasons = response["incomplete_because"]
        cursor = response["next_cursor"]
        if cursor is None:
            break
    else:
        reasons = [*reasons, f"stopped after about {max_per_source} results per source; more may exist"]
        for source in decode_cursor(cursor):
            page_status[source]["status"] = "capped"
    baseline = {b["ref"]: b for b in collection["baseline"]}
    items = []
    for ref, result in current.items():
        entry = {"ref": ref, "title": result["title"], "source_type": result["source_type"],
                 "version_now": result["version"]}
        if ref in baseline:
            status, reason = _compare(baseline[ref]["version"], result["version"])
            entry.update(version_then=baseline[ref]["version"], status=status, reason=reason)
        else:
            entry.update(version_then=None, status="added", reason="matches now; not in the accepted results")
        items.append(entry)
    for ref, base in baseline.items():
        if ref in current:
            continue
        source = Reference.parse(ref).source_id
        answered = page_status.get(source, {}).get("status")
        if answered != "ok":
            status, reason = "unavailable", f"its source did not answer completely ({answered or 'not searched'})"
        else:
            status, reason = "removed", "no longer among the results (gone from the source, or no longer matches)"
        items.append({"ref": ref, "title": base["title"], "source_type": base["source_type"],
                      "version_then": base["version"], "version_now": None, "status": status, "reason": reason})
    return items, list(reasons)


def evaluate(adapters, collection: dict, mode: str = "local", max_per_source: int = MAX_PER_SOURCE) -> dict:
    """Re-check a collection against its sources now. Never changes the collection."""
    try:
        if collection["kind"] == "set":
            items, reasons = _evaluate_set(adapters, collection)
        else:
            items, reasons = _evaluate_query(adapters, collection, max_per_source)
    except ContractError as exc:
        raise CollectionError(f"collection definition is no longer valid: {exc}") from None
    counts: dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {"schema": EVALUATION_SCHEMA, "collection_id": collection["collection_id"], "name": collection["name"],
            "kind": collection["kind"], "filters": collection["filters"], "evaluated_at": _now(), "mode": mode,
            "items": items, "counts": counts, "partial": bool(reasons), "incomplete_because": reasons,
            "notes": ["Collections hold references and versions only; nothing was copied.",
                      "In local mode, references are checked against local catalogs, not the sources themselves."
                      if mode == "local" else "Sources were asked directly where they allow it."]}

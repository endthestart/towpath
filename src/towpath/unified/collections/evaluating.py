"""Comparing a collection with what its sources hold now."""

from towpath.unified import federation
from towpath.unified.collections.storing import (
    EVALUATION_SCHEMA,
    MAX_PER_SOURCE,
    MAX_PHOTOS,
    CollectionError,
    _now,
    photo_filters,
)
from towpath.unified.contracts import ContractError, Filters, Reference, decode_cursor


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


def _evaluate_photos(store_dir, collection: dict, config=None) -> tuple[list, list]:
    from towpath import photos

    if store_dir is None:
        raise CollectionError("a photo collection is evaluated against this Towpath's photo catalog")
    rows, total = photos.search(store_dir, photo_filters(collection["filters"]), limit=MAX_PHOTOS, config=config)
    reasons = [f"showing the first {MAX_PHOTOS} of {total} photos"] if total > len(rows) else []
    baseline = {b["ref"]: b for b in collection["baseline"]}
    items, current = [], set()
    for r in rows:
        ref = f"{r['provider_id']}:{r['occurrence_id']}"
        current.add(ref)
        name = r["rel_path"].rsplit("/", 1)[-1]
        entry = {"ref": ref, "title": f"{name} · {(r.get('taken') or '')[:10]}".rstrip(" ·"), "source_type": "photo",
                 "version_now": str(r.get("size")), "occurrence_id": r["occurrence_id"]}
        if ref in baseline:
            status, reason = _compare(baseline[ref]["version"], entry["version_now"])
            entry.update(version_then=baseline[ref]["version"], status=status, reason=reason)
        else:
            entry.update(version_then=None, status="added", reason="matches now; not in the accepted results")
        items.append(entry)
    gone = sum(1 for ref in baseline if ref not in current)
    if gone and not reasons:  # counted, never listed: one may be a photo hidden since
        reasons.append(f"{gone} accepted photo{'s' if gone != 1 else ''} no longer match (hidden, removed or changed "
                       "since)")
    return items, reasons


def evaluate(adapters, collection: dict, mode: str = "local", max_per_source: int = MAX_PER_SOURCE,
             store_dir=None, config=None) -> dict:
    """Re-check a collection against its sources now. Never changes the collection."""
    try:
        if collection["kind"] == "photos":
            items, reasons = _evaluate_photos(store_dir, collection, config)
        elif collection["kind"] == "set":
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

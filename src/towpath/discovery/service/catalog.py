"""Probing providers, reporting status, bounded search, and importing references into the catalog."""

import json
import time
from collections import Counter

from towpath.discovery import freshness, policy, refs
from towpath.discovery import store as fstore
from towpath.discovery.providers.base import LimitExceeded, Unavailable
from towpath.discovery.records import Occurrence
from towpath.discovery.service.common import (
    DiscoveryError,
    NotFound,
    _accept,
    error_code,
    files_config,
    _limit,
    provider,
)


def probe(config, provider_id: str | None = None) -> list[dict]:
    fc = files_config(config)
    ids = [provider_id] if provider_id else list(fc.providers)
    out = []
    for pid in ids:
        prov = provider(config, pid)
        entry = {"provider": pid, "adapter": prov.adapter, "roots": list(prov.roots),
                 "capabilities": dict(prov.capabilities)}
        try:
            entry.update(available=True, **prov.probe(fc.limits["timeout_seconds"]))
        except Unavailable as exc:
            entry.update(available=False, reason=str(exc))
        out.append(entry)
    return out


def status(config) -> dict:
    """Configuration, grants, and catalog state. Calls no provider and reads no source file."""
    if config.files is None:
        return {"configured": False}
    fc = config.files
    grants = policy.granted(config)
    db = fstore.connect_ro(config)
    try:
        counts = [dict(r) for r in db.execute(
            "SELECT provider_id, root_alias, count(*) AS occurrences, sum(missing_since_run IS NOT NULL) AS missing "
            "FROM occurrences GROUP BY provider_id, root_alias")]
        runs = [dict(r) for r in db.execute(
            "SELECT run_id, provider_id, kind, started_at, finished_at, termination, reason, items_seen FROM runs "
            "WHERE kind = 'import' ORDER BY started_at DESC LIMIT 5")]
        coverage = [dict(r) | {"by_status": json.loads(r["by_status"])} for r in db.execute(
            "SELECT * FROM coverage ORDER BY rowid DESC LIMIT 5")]
    finally:
        db.close()
    return {
        "configured": True, "enabled": fc.enabled, "limits": fc.limits,
        "roots": {a: {"exclude": list(r.exclude), "grants": sorted(grants.get(a, ()))} for a, r in fc.roots.items()},
        "providers": {p.id: {"adapter": p.adapter, "roots": list(p.roots)} for p in fc.providers.values()},
        "catalog": counts, "recent_imports": runs, "recent_coverage": coverage,
    }


def _result(occ: Occurrence, passage: dict | None) -> dict:
    data = occ.to_dict()
    data["passage"] = passage
    return data


def search(config, query: str, provider_id: str | None = None, limit: int | None = None,
           offset: int = 0, require: tuple[str, ...] = ()) -> dict:
    """Bounded search over roots with a search grant (and every feature in ``require``)."""
    fc = files_config(config)
    if not query.strip():
        raise DiscoveryError("empty query")
    if offset < 0 or offset > fc.limits["max_scan"]:
        raise DiscoveryError(f"offset must be between 0 and {fc.limits['max_scan']}")
    prov = provider(config, provider_id)
    limit = _limit(fc, "max_results", limit)
    grants = policy.granted(config)
    needed = {"search", *require}
    roots = {a for a in prov.roots if needed <= grants.get(a, set())}
    if not roots:
        raise policy.Denied(f"no root of provider {prov.id} has grants for {', '.join(sorted(needed))}")
    timeout = fc.limits["timeout_seconds"]
    deadline = time.monotonic() + timeout
    db = fstore.connect_rw(config)
    run = fstore.begin_run(db, prov.id, "search")
    results, permitted, more, stopped = [], 0, False, None
    try:
        listing = prov.search(query, [prov.roots[a] for a in sorted(roots)], fc.limits["max_scan"], timeout)
        for hit in listing.hits:
            occ = _accept(hit, prov, roots)
            if occ is None:
                continue
            permitted += 1
            if permitted <= offset:
                continue
            if len(results) >= limit:
                more = True
                break
            fstore.observe(db, occ, run)
            result = _result(occ, hit.passage)
            source, _, source_reason = freshness.compare(
                hit.source_stamp, freshness.live_stamp(refs.resolve_in_root(prov.roots[occ.locator.root],
                                                                             occ.locator.path)))
            result["source"] = {"state": source, "reason": source_reason}
            results.append(result)
            if time.monotonic() > deadline:
                stopped, more = "time limit reached", True
                break
        # Unless the provider confirmed its raw rows ran out, permitted results may lie beyond them.
        # (Counting filtered hits instead would mistake a capped listing for a complete one.)
        more = more or listing.exhausted is not True
        db.commit()
        fstore.finish_run(db, run, "complete" if stopped is None else "partial", len(results), stopped)
    except KeyboardInterrupt:
        db.commit()
        fstore.finish_run(db, run, "interrupted", len(results), "cancelled")
        raise
    except (Unavailable, LimitExceeded) as exc:
        fstore.finish_run(db, run, "failed", len(results), error_code(exc))
        raise
    finally:
        db.close()
    return {"query": query, "provider": prov.id, "roots": sorted(roots), "offset": offset, "limit": limit,
            "results": results, "more_may_exist": more, "stopped": stopped, "run_id": run,
            "note": "results carry no file text; use 'files excerpt' (needs an excerpt grant)"}


def stored_record(config, occurrence_id: str, feature: str):
    """The stored occurrence, if its root still exists, is not excluded, and has ``feature`` granted."""
    fc = files_config(config)
    db = fstore.connect_ro(config)
    try:
        row = fstore.get(db, occurrence_id)
    finally:
        db.close()
    if row is None or row["root_alias"] not in fc.roots or row["provider_id"] not in fc.providers:
        raise NotFound(f"no occurrence {occurrence_id}")
    if refs.excluded(fc.roots[row["root_alias"]], row["rel_path"]):
        raise NotFound(f"no occurrence {occurrence_id}")
    policy.require(config, row["root_alias"], feature)
    return fstore.row_to_dict(row)


def import_catalog(config, provider_id: str | None = None, root: str | None = None,
                   max_items: int = 10000, page_size: int | None = None, progress=None, should_stop=None,
                   deadline_seconds: float | None = None) -> list[dict]:
    """Copy references (never text) for granted roots into files.db, recording coverage honestly.

    By default one listing of up to ``max_items`` rows within the per-call time limit. With ``page_size``,
    a large root is listed page by page within one run: each page has the per-call limit, the whole run has
    ``deadline_seconds`` (none if unset), ``progress(seen)`` is called after every page, and
    ``should_stop()`` is checked between pages. Absence is established only by a complete listing."""
    fc = files_config(config)
    prov = provider(config, provider_id)
    if root is not None:
        if root not in prov.roots:
            raise NotFound(f"provider {prov.id} has no root {root!r}")
        policy.require(config, root, "search")
        targets = [root]
    else:
        grants = policy.granted(config)
        targets = [a for a in prov.roots if "search" in grants.get(a, set())]
        if not targets:
            raise policy.Denied(f"no search grant for any root of provider {prov.id}")
    reports = []
    for alias in targets:
        db = fstore.connect_rw(config)
        if page_size:  # a large catalog: keep its indexes' pages in memory while rows go in by ID, not in order
            db.execute("PRAGMA cache_size = -262144")
        run = fstore.begin_run(db, prov.id, "import")
        statuses, refused, seen, changed = Counter(), 0, 0, 0
        complete, reason, termination = True, None, "complete"
        limit = deadline_seconds if page_size else fc.limits["timeout_seconds"]
        deadline = time.monotonic() + limit if limit is not None else None
        offset, cursor = 0, None
        folders = refs.Folders()
        try:
            while True:
                page = min(page_size, max_items - offset) if page_size else max_items
                position = {"cursor": cursor} if cursor is not None else {"offset": offset} if offset else {}
                # Completeness comes from the provider's raw rows, never from how many hits survive filtering.
                listing = prov.enumerate(prov.roots[alias], page, fc.limits["timeout_seconds"], **position)
                for hit in listing.hits:
                    if deadline is not None and time.monotonic() > deadline:
                        complete, reason = False, "time limit reached"
                        break
                    try:
                        located, rel = refs.locate(hit.url, prov.roots, folders)
                    except refs.BadReference:
                        refused += 1
                        continue
                    if located == alias and refs.excluded(prov.roots[alias], rel):
                        continue
                    occ = _accept(hit, prov, {alias}, located=(located, rel))
                    if occ is None:
                        refused += 1
                        continue
                    seen += 1
                    statuses[occ.extraction.status] += 1
                    changed += fstore.observe(db, occ, run) == "changed"
                offset += listing.raw_rows
                cursor = listing.cursor
                if not complete:
                    break
                if listing.exhausted is None:
                    complete, reason = False, "the provider did not confirm that the listing was exhaustive"
                    break
                if listing.exhausted:
                    break
                if not page_size or offset >= max_items:
                    complete, reason = False, f"the provider holds more than max_items={max_items} rows"
                    break
                db.commit()
                if progress is not None:
                    progress(seen)
                if should_stop is not None and should_stop():
                    complete, reason, termination = False, "paused", "interrupted"
                    break
        except KeyboardInterrupt:
            complete, reason, termination = False, "cancelled", "interrupted"
            db.commit()
            fstore.record_coverage(db, run, prov.id, alias, False, seen, dict(statuses), reason)
            fstore.finish_run(db, run, termination, seen, reason)
            db.close()
            raise
        except (Unavailable, LimitExceeded) as exc:
            complete, reason, termination = False, f"{error_code(exc)}: {exc}", "failed"
        if termination == "complete" and not complete:
            termination = "partial"
        missing = fstore.mark_missing(db, prov.id, alias, run) if complete else 0
        db.commit()
        fstore.refresh_counts(db, prov.id, alias)
        fstore.record_coverage(db, run, prov.id, alias, complete, seen, dict(statuses), reason)
        fstore.finish_run(db, run, termination, seen, reason)
        db.close()
        reports.append({"provider": prov.id, "root": alias, "run_id": run, "termination": termination,
                        "complete": complete, "reason": reason, "occurrences_seen": seen, "changed": changed,
                        "by_status": dict(statuses), "refused_references": refused, "marked_missing": missing,
                        "absence_established": complete})
    return reports

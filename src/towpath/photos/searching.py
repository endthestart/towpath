"""Searching photos by their metadata."""

import time
from contextlib import closing

from towpath.photos.extraction import VISIBLE, is_private, private_in
from towpath.stores import open_store, store_path

FILTERS = ("camera", "lens", "after", "before", "located", "text")


def facets(store_dir, role: str = "web") -> dict:
    """Cameras and lenses with how many photos each, the years photos were taken, and the days with the most
    (counted at most every ten minutes: they change only as photos are read)."""
    if not store_path(store_dir, "files").exists():
        return {"cameras": [], "lenses": [], "years": [], "days": []}
    key = str(store_dir)
    if key in _facets and time.monotonic() - _facets[key][0] < FACETS_SECONDS:
        return _facets[key][1]
    found = _count_facets(store_dir, role)
    _facets[key] = (time.monotonic(), found)
    return found


FACETS_SECONDS = 600
_facets: dict[str, tuple[float, dict]] = {}


def _count_facets(store_dir, role: str) -> dict:
    with closing(open_store(store_dir, "files", role)) as db:
        return {
            "cameras": [tuple(r) for r in db.execute(
                "SELECT trim(coalesce(make, '') || ' ' || model), count(*) FROM photo_meta WHERE model IS NOT NULL "
                f"AND {VISIBLE} GROUP BY 1 ORDER BY 2 DESC LIMIT 60")],
            "lenses": [tuple(r) for r in db.execute(f"SELECT lens, count(*) FROM photo_meta WHERE lens IS NOT NULL "
                                                    f"AND {VISIBLE} GROUP BY 1 ORDER BY 2 DESC LIMIT 60")],
            "years": [tuple(r) for r in db.execute(f"SELECT substr(taken, 1, 4), count(*) FROM photo_meta WHERE taken "
                                                   f"IS NOT NULL AND {VISIBLE} GROUP BY 1 ORDER BY 1 DESC")],
            "days": [tuple(r) for r in db.execute(f"SELECT substr(taken, 1, 10), count(*) FROM photo_meta WHERE taken "
                                                  f"IS NOT NULL AND {VISIBLE} GROUP BY 1 ORDER BY 2 DESC LIMIT 12")]}


def search(store_dir, f: dict, limit: int = 200, offset: int = 0, role: str = "web",
           config=None) -> tuple[list[dict], int]:
    """Photos matching typed filters, newest taken first, and how many match in all. With the configuration, a file
    is tested for the private export on its full path (its root may be the export or sit inside it)."""
    if not store_path(store_dir, "files").exists():
        return [], 0
    where, args = [], []
    if f.get("camera"):
        where.append("lower(trim(coalesce(p.make, '') || ' ' || coalesce(p.model, ''))) LIKE ?")
        args.append(f"%{f['camera'].lower()}%")
    if f.get("lens"):
        where.append("lower(coalesce(p.lens, '')) LIKE ?")
        args.append(f"%{f['lens'].lower()}%")
    if f.get("after"):
        where.append("p.taken >= ?")
        args.append(f["after"])
    if f.get("before"):
        where.append("p.taken <= ?")
        args.append(f["before"] + "T99")
    if f.get("located"):
        where.append("p.lat IS NOT NULL")
    if f.get("text"):
        where.append("lower(o.rel_path) LIKE ?")
        args.append(f"%{f['text'].lower()}%")
    where.append("p." + VISIBLE)
    where.append("NOT towpath_private(o.root_alias, o.rel_path)")  # the purge takes these out; never list one
    clause = " AND ".join(where)
    with closing(open_store(store_dir, "files", role)) as db:
        db.create_function("towpath_private", 2, (lambda alias, rel: private_in(config, alias, rel)) if config
                           else (lambda alias, rel: is_private(rel)), deterministic=True)
        total = db.execute(f"SELECT count(*) FROM photo_meta p JOIN occurrences o USING (occurrence_id) WHERE "
                           f"{clause} AND p.problem IS NULL AND o.missing_since_run IS NULL", args).fetchone()[0]
        rows = [dict(r) for r in db.execute(
            f"SELECT p.*, o.provider_id, o.root_alias, o.rel_path, o.size FROM photo_meta p JOIN occurrences o "
            f"USING (occurrence_id) "
            f"WHERE {clause} AND p.problem IS NULL AND o.missing_since_run IS NULL ORDER BY p.taken DESC LIMIT ? "
            f"OFFSET ?", (*args, limit, offset))]
    return rows, total

"""Days with many photos as life evidence.

A day on which the owner took many photos usually marks something: a trip, a party, a first day. For the folders the
owner allowed on the Life page, each such day becomes a proposed event ("Took 34 photos", with the folder most of
them are in), citing the first photo, dated by the camera. Photos taken support that the owner was there; what
happened is for the owner to say when they review it. The private hidden-photo export is never counted.
"""

from collections import Counter
from contextlib import closing

from towpath import life
from towpath.photos import PRIVATE
from towpath.stores import open_store, store_path
from towpath.unified.claims import Citation, Claim, ClaimError, When

RUN = "photos"
MIN_PHOTOS = 15
NOTABLE = 30


def propose(config) -> list[dict]:
    from towpath.unified import grants

    allowed = {sid for sid, f in grants.granted(config.store_dir).items() if "life-evidence" in f}
    if not allowed or not store_path(config.store_dir, "files").exists():
        return []
    days: dict[tuple[str, str], list] = {}
    with closing(open_store(config.store_dir, "files", "worker")) as db:
        for provider, day, occ, rel in db.execute(
                "SELECT o.provider_id, substr(p.taken, 1, 10), p.occurrence_id, o.rel_path FROM photo_meta p JOIN "
                "occurrences o USING (occurrence_id) WHERE p.taken IS NOT NULL AND o.missing_since_run IS NULL "
                "ORDER BY p.taken"):
            if provider in allowed and not PRIVATE.search(rel):
                days.setdefault((provider, day), []).append((occ, rel))
    made = 0
    with closing(open_store(config.store_dir, "life", life.WRITER)) as db:
        for (provider, day), shots in days.items():
            if len(shots) < MIN_PHOTOS:
                continue
            folder = Counter(rel.rpartition("/")[0] for _, rel in shots).most_common(1)[0][0]
            place = folder.rpartition("/")[2] or folder
            statement = f"Took {len(shots)} photos" + (f" ({place})" if place else "")
            ref = f"{provider}:{shots[0][0]}"
            try:
                claim = Claim(life.claim_id(statement, "occurred", [ref]), "activity", statement, "occurred",
                              When(day, "day", day, day), (Citation(ref=ref, captured_excerpt=folder or None),),
                              "photos/1", significance="notable" if len(shots) >= NOTABLE else "routine")
            except ClaimError:
                continue
            made += life.save(db, claim, provider, RUN)
        db.commit()
    return [{"step": "life-photos", "proposed": made}] if made else []

"""Bookings as life evidence: purchases (read from receipts by mail management) that are flights, stays or tickets.

For mail accounts the owner allows for the life story, each such purchase becomes a proposed event on the day it was
bought ("Booked a flight with Example Air: Denver"), citing the receipt. When the trip or the show happened is for
the owner to say, or for the occasions the model gathers; the booking itself is what the receipt shows.
"""

import json
import re
from contextlib import closing

from towpath import life
from towpath.stores import open_store, store_path
from towpath.unified.claims import Citation, Claim, ClaimError, When

RUN = "bookings"
KINDS = {
    "flight": re.compile(r"\b(air(lines?|ways)?|flight|airport|jet ?blue|boarding)\b", re.I),
    "stay": re.compile(r"\b(hotel|inn|resort|airbnb|booking\.com|lodge|motel|hostel|vrbo|suites?)\b", re.I),
    "ticket": re.compile(r"\b(tickets?|ticketmaster|eventbrite|concert|theat(re|er)|festival|admission)\b", re.I),
    "train": re.compile(r"\b(amtrak|rail|train)\b", re.I),
}
WORDS = {"flight": "a flight", "stay": "a stay", "ticket": "tickets", "train": "a train"}


def kind_of(merchant: str, items: list[str]) -> str | None:
    text = " ".join([merchant or "", *items])
    return next((k for k, pattern in KINDS.items() if pattern.search(text)), None)


def propose(config) -> list[dict]:
    from towpath.unified import grants

    allowed = {sid for sid, f in grants.granted(config.store_dir).items() if "life-evidence" in f}
    if not allowed or not store_path(config.store_dir, "mailintel").exists():
        return []
    found = []
    with closing(open_store(config.store_dir, "mailintel", "worker")) as intel:
        marks = ",".join("?" for _ in allowed)
        for sid, item_id, merchant, day, items in intel.execute(
                f"SELECT source_id, item_id, merchant, day, items FROM purchases WHERE purchase = 1 AND day IS NOT "
                f"NULL AND source_id IN ({marks})", sorted(allowed)):
            items = json.loads(items or "[]")
            kind = kind_of(merchant or "", items)
            if kind:
                found.append((sid, item_id, merchant, day, items, kind))
    if not found:
        return []
    made = 0
    with closing(open_store(config.store_dir, "source", "worker")) as src, \
            closing(open_store(config.store_dir, "life", life.WRITER)) as db:
        for sid, item_id, merchant, day, items, kind in found:
            row = src.execute("SELECT native_id, subject FROM items WHERE item_id = ?", (item_id,)).fetchone()
            if row is None:
                continue
            what = f": {', '.join(items[:2])}" if items else ""
            statement = f"Booked {WORDS[kind]} with {merchant}{what}"[:200]
            ref = f"{sid}:{row[0]}"
            try:
                claim = Claim(life.claim_id(statement, "occurred", [ref]), "activity", statement, "occurred",
                              When(day, "day", day, day), (Citation(ref=ref, captured_excerpt=row[1] or None),),
                              "bookings/1", significance="notable")
            except ClaimError:
                continue
            made += life.save(db, claim, sid, RUN)
        db.commit()
    return [{"step": "life-bookings", "proposed": made}] if made else []

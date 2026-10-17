"""Calendar events as life-story evidence.

For a contacts-and-calendars connection the owner allowed on the Life page, each past event that doesn't repeat
becomes a proposed claim: a planned event (a calendar entry shows that something was planned, not that it happened),
dated as the calendar has it, citing the entry. Events with a place or other people are notable; the rest wait under
Minor. No model is involved, and the owner reviews them like any other proposal.
"""

from contextlib import closing
from datetime import datetime, timezone

from towpath import life
from towpath.stores import open_store, store_path
from towpath.unified.claims import Citation, Claim, ClaimError, When

RUN = "calendar"


def _claim(sid: str, e: dict) -> Claim | None:
    day = (e["start"] or "")[:10]
    end = (e["end"] or e["start"] or "")[:10]
    if not day or not e["summary"]:
        return None
    statement = e["summary"] + (f" at {e['location']}" if e["location"] else "")
    ref = f"{sid}:{e['href']}#{e['n']}"
    multi = e["all_day"] and end > day
    when = When(day if not multi else f"{day}/{end}", "range" if multi else "day", day, end if multi else day)
    significance = "notable" if e["location"] or [a for a in e["attendees"] if a != e["organizer"]] else "routine"
    try:
        return Claim(life.claim_id(statement, "plan", [ref]), "event", statement[:200], "plan", when,
                     (Citation(ref=ref, captured_excerpt=e["summary"][:300]),),
                     "calendar/1", significance=significance,
                     uncertainty="A calendar entry shows a plan, not that it happened.")
    except ClaimError:
        return None


def propose(config) -> list[dict]:
    from towpath import connections, dav_sync
    from towpath.unified import grants

    allowed = {sid for sid, f in grants.granted(config.store_dir).items() if "life-evidence" in f}
    sources = [c["source_id"] for c in connections.all_connections(config.store_dir, role="worker")
               if c["adapter"] == "dav" and c["source_id"] in allowed]
    if not sources or not store_path(config.store_dir, "dav").exists():
        return []
    today = datetime.now(timezone.utc).date().isoformat()
    made = 0
    with closing(open_store(config.store_dir, "life", life.WRITER)) as db:
        for sid in sources:
            for e in dav_sync.events(config.store_dir, sid, before=today, role="worker"):
                if e["recurring"]:
                    continue
                c = _claim(sid, e)
                if c is not None:
                    made += life.save(db, c, sid, RUN)
        db.commit()
    return [{"step": "life-calendar", "proposed": made}] if made else []

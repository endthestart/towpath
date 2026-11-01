"""Proposing memories: people, trips from photos, chapters and occasions, in the worker's slice."""

import json
import time
from contextlib import closing
from datetime import date

from towpath import life
from towpath.canonical import digest
from towpath.life_groups.common import INSTRUCTION, MIN_PERSON_EVENTS, PROMPT, SCHEMA, SLICE_SECONDS, TASK, WRITER, now
from towpath.life_groups.owner_choices import _period, profile
from towpath.stores import open_store, store_path


def _live_claims(store_dir) -> list:
    return [c for c in life.claims(store_dir, limit=10**6, role=WRITER) if c.state != "rejected"]


def _save(db, gid: str, kind: str, title: str, summary: str | None, members: list[str], spans: dict,
          producer: str) -> None:
    earliest, latest = _period(spans, members)
    db.execute("INSERT OR REPLACE INTO groups VALUES (?,?,?,?,?,?,?,?,?)",
               (gid, kind, title, summary, earliest, latest, json.dumps(sorted(set(members))), producer, now()))


def _people(config, db, claims_, spans) -> int:
    """A group per person the owner corresponds with, of the events found in mail with them."""
    from towpath.mail import senders as mail_senders

    by_ref: dict[str, list[str]] = {}
    for c in claims_:
        for cit in c.citations:
            by_ref.setdefault(cit.ref, []).append(c.claim_id)
    people: dict[tuple, set] = {}
    names: dict[tuple, str] = {}
    with closing(open_store(config.store_dir, "source", WRITER)) as src:
        mail = open_store(config.store_dir, "mail", WRITER) if store_path(config.store_dir, "mail").exists() else None
        try:
            for ref, ids in by_ref.items():
                sid, _, native = ref.partition(":")
                row = src.execute("SELECT item_id, from_addr FROM items WHERE source_id = ? AND native_id = ?",
                                  (sid, native)).fetchone()
                if row is None:
                    continue
                addrs = {row[1]} if row[1] else set()
                if mail is not None:
                    m = mail.execute("SELECT from_name, to_addrs FROM messages WHERE item_id = ?", (row[0],)).fetchone()
                    if m:
                        addrs |= set(json.loads(m[1]))
                        if m[0] and row[1]:
                            names[(sid, row[1])] = m[0]
                kinds = mail_senders.categories(config.store_dir, sid, WRITER)
                for a in addrs:
                    if a and kinds.get(a) == "person":
                        people.setdefault((sid, a), set()).update(ids)
        finally:
            if mail is not None:
                mail.close()
    made = 0
    for (sid, address), ids in people.items():
        if len(ids) < MIN_PERSON_EVENTS:
            continue
        gid = "grp_person_" + digest([sid, address])[:16]
        _save(db, gid, "person", f"With {names.get((sid, address)) or address}", None, sorted(ids), spans,
              "rule:people")
        made += 1
    return made


def _photo_trips(db, claims_, spans) -> int:
    """Runs of days with many photos (two or more days in a row, or a day apart) as one trip each, named for the
    folder most of them are in: one memory instead of a separate event per day."""
    import re
    from collections import Counter
    from datetime import date as date_

    days = sorted((date_.fromisoformat(spans[c.claim_id][0][:10]), c) for c in claims_
                  if c.citations and c.citations[0].ref.startswith("folders:") and spans.get(c.claim_id, (None,))[0])
    runs, current = [], []
    for day, c in days:
        if current and (day - current[-1][0]).days > 2:
            runs.append(current)
            current = []
        current.append((day, c))
    if current:
        runs.append(current)
    made = 0
    for run in runs:
        if len({d for d, _ in run}) < 2:
            continue
        places = Counter(m.group(1) for _, c in run for m in [re.search(r"\(([^)]+)\)\s*$", c.statement)] if m)
        span = f"{run[0][0]:%b %d}–{run[-1][0]:%b %d, %Y}"
        title = f"Trip: {places.most_common(1)[0][0]}" if places else f"Days with many photos, {span}"
        members = [c.claim_id for _, c in run]
        _save(db, "grp_trip_" + digest(sorted(members))[:16], "trip", title,
              f"{len(run)} days with many photos, {span}.", members, spans, "rule:photo-trips")
        made += 1
    return made


def _chapters(config, db, claims_, spans) -> int:
    birthday = profile(config.store_dir, WRITER).get("birthday")
    if not birthday:
        return 0
    born = date.fromisoformat(birthday)
    decades: dict[int, list[str]] = {}
    for c in claims_:
        if c.significance == "routine" or not spans.get(c.claim_id, (None,))[0]:
            continue
        when = date.fromisoformat(spans[c.claim_id][0][:10])
        age = when.year - born.year - ((when.month, when.day) < (born.month, born.day))
        if age >= 0:
            decades.setdefault(age // 10 * 10, []).append(c.claim_id)
    for decade, ids in decades.items():
        title = "Childhood" if decade == 0 else ("Your teens" if decade == 10 else f"Your {decade}s")
        _save(db, f"grp_age_{decade:02d}", "chapter", title, None, ids, spans, "rule:chapters")
    return len(decades)


def _occasions(config, db, claims_, spans, deadline, clock) -> tuple[int, str | None]:
    """The model gathers each month's events (with ten days either side) into occasions; months whose events
    haven't changed aren't asked about again, and months still being read wait until their run finishes."""
    from towpath.mail.threads import _failure
    from towpath.models import gateway

    months: dict[str, list] = {}
    for c in claims_:
        if spans.get(c.claim_id, (None,))[0]:
            months.setdefault(spans[c.claim_id][0][:7], []).append(c)
    reading = [(s["after"][:7], s["before"][:7]) for s in
               (json.loads(r[0]) for r in db.execute("SELECT slice FROM runs WHERE state != 'done'"))]
    made = 0
    for month in sorted(months, reverse=True):
        if clock() >= deadline:
            return made, None
        if any(first <= month <= last for first, last in reading):
            continue
        first = date.fromisoformat(month + "-01")
        nearby = [c for c in claims_ if spans.get(c.claim_id, (None,))[0] and
                  abs((date.fromisoformat(spans[c.claim_id][0][:10]) - first).days - 15) <= 25]
        if len(nearby) < 2:
            continue
        key = f"occasions:{month}"
        member_digest = digest(sorted(c.claim_id for c in nearby))
        done = db.execute("SELECT digest FROM group_runs WHERE key = ?", (key,)).fetchone()
        if done and done[0] == member_digest:
            continue
        from towpath.mail import privacy
        from towpath.mail.threads import item_refused

        named = privacy.ref_targets(config.store_dir, [cit.ref for c in nearby for cit in c.citations], WRITER)

        def names(cs, named=named):
            return sorted({t for c in cs for cit in c.citations for t in named.get(cit.ref, [])})

        for _ in range(2):  # a second time without events from mail kept from this model, if it refused for them
            listed = "\n".join(f"- {c.claim_id} | {spans[c.claim_id][0][:10]} | {c.statement}" for c in nearby[:80])
            result = gateway.infer(config, TASK, f"{INSTRUCTION}\n\n<events>\n{listed}\n</events>", "content",
                                   SCHEMA, names(nearby[:80]), prompt_version=PROMPT, background=True)
            if not item_refused(result):
                break
            kept = privacy.kept(config.store_dir, names(nearby), role=WRITER)
            nearby = [c for c in nearby if not set(names([c])) & kept]
            if len(nearby) < 2:
                result = {"status": "skipped"}
                break
        if result["status"] == "ok":
            known = {c.claim_id for c in nearby}
            db.execute("DELETE FROM groups WHERE producer = ?", (f"model:{key}",))
            for g in result["output"]["groups"]:
                members = [m for m in g["members"] if m in known]
                if len(members) < 2:
                    continue
                gid = "grp_occ_" + digest([g["kind"], sorted(members)])[:16]
                _save(db, gid, g["kind"], g["title"].strip(), (g.get("summary") or "").strip() or None, members,
                      spans, f"model:{key}")
                made += 1
            db.execute("INSERT OR REPLACE INTO group_runs VALUES (?,?,?)", (key, member_digest, now()))
            db.commit()
        elif result["status"] in ("refused", "disabled"):
            return made, "refused"
        elif _failure(result):
            return made, _failure(result)
        else:  # skipped (all kept from this model) or an unusable answer: not asked again until the events change
            db.execute("INSERT OR REPLACE INTO group_runs VALUES (?,?,?)", (key, member_digest, now()))
            db.commit()
    return made, None


def propose_groups(config, slice_seconds: float = SLICE_SECONDS, clock=time.monotonic) -> list[dict]:
    """One bounded slice: people and chapter groups by rule, then occasions through the model the owner allowed."""
    from towpath import decisions as owner_decisions
    from towpath.mail import threads

    if not store_path(config.store_dir, "life").exists():
        return []
    claims_ = _live_claims(config.store_dir)
    if not claims_:
        return []
    deadline = clock() + slice_seconds
    db = open_store(config.store_dir, "life", WRITER)
    try:
        spans = {r[0]: (r[1], r[2]) for r in db.execute("SELECT claim_id, earliest, latest FROM claims")}
        key = digest(sorted(c.claim_id for c in claims_) + [json.dumps(profile(config.store_dir, WRITER),
                                                                       sort_keys=True)])
        made = {"people": 0, "chapters": 0, "trips": 0, "occasions": 0}
        last = db.execute("SELECT digest FROM group_runs WHERE key = 'rules'").fetchone()
        if not last or last[0] != key:
            db.execute("DELETE FROM groups WHERE producer IN ('rule:people', 'rule:chapters', 'rule:photo-trips')")
            made["people"] = _people(config, db, claims_, spans)
            made["trips"] = _photo_trips(db, claims_, spans)
            made["chapters"] = _chapters(config, db, claims_, spans)
            db.execute("INSERT OR REPLACE INTO group_runs VALUES ('rules', ?, ?)", (key, now()))
            db.commit()
        endpoint = config.tasks.get(TASK)
        if endpoint and owner_decisions.model_granted(config, endpoint, "content", role=WRITER):
            if threads.ready(config, TASK):
                made["occasions"], _ = _occasions(config, db, claims_, spans, deadline, clock)
        db.commit()
    finally:
        db.close()
    return [{"step": "life-groups", **made}] if any(made.values()) else []

"""Which messages are likely to hold an event, and turning a model's answer into claims."""

import hashlib
import html
import json
import re
from contextlib import closing
from datetime import datetime, timedelta, timezone

from towpath import life
from towpath.life_propose.common import NOT_ABOUT_THE_OWNER, SKIP_LABELS, WORDS
from towpath.stores import open_store, store_path
from towpath.unified.claims import Citation, Claim, ClaimError, DateBasis, When


def _people(config, source_id: str) -> tuple[set[str], set[str]]:
    """People the owner corresponds with (senders mail management counts as people) and the account's sent
    folders, where mail management knows them; empty otherwise."""
    from towpath.mail import senders, threads

    people = {a for a, c in senders.categories(config.store_dir, source_id, "worker").items() if c == "person"}
    sent = threads.sent_mailboxes(config.store_dir, source_id, "worker") | {"SENT"}
    return people, sent


def _likely(subject: str | None, sender: str | None, labels: list[str], people: set[str], sent: set[str]) -> bool:
    """A message that may record something in the owner's life: a telling subject, mail from or to a person the
    owner corresponds with, or mail the owner sent."""
    low = (subject or "").lower()
    return any(w in low for w in WORDS) or (sender or "").lower() in people or bool(set(labels) & sent)


def _candidates(config, value: dict, examined: set[str], limit: int) -> list[dict]:
    people, sent = _people(config, value["source_id"])
    with closing(open_store(config.store_dir, "source", "worker")) as db:
        rows = db.execute(
            "SELECT item_id, native_id, subject, from_addr, date_utc FROM items WHERE source_id = ? "
            "AND absent_since_run IS NULL AND date_utc IS NOT NULL AND substr(date_utc, 1, 10) BETWEEN ? AND ? "
            "ORDER BY date_utc DESC", (value["source_id"], value["after"], value["before"])).fetchall()
        out = []
        for row in rows:
            if row[0] in examined:
                continue
            seen = db.execute("SELECT labels FROM observations WHERE item_id = ? ORDER BY observed_at DESC LIMIT 1",
                              (row[0],)).fetchone()
            if not _likely(row[2], row[3], json.loads(seen[0]) if seen else [], people, sent):
                continue
            part = db.execute("SELECT part_id, mime_type, charset FROM parts WHERE item_id = ? AND mime_type IN "
                              "('text/plain', 'text/html') AND (disposition IS NULL OR disposition != 'attachment') "
                              "ORDER BY mime_type != 'text/plain', part_id LIMIT 1", (row[0],)).fetchone()
            labels = db.execute("SELECT labels FROM observations WHERE item_id = ? ORDER BY observed_at DESC LIMIT 1",
                                (row[0],)).fetchone()
            out.append({"item_id": row[0], "native_id": row[1], "subject": row[2], "from": row[3], "date": row[4],
                        "part": dict(zip(("part_id", "mime_type", "charset"), part, strict=True)) if part else None,
                        "advertising": bool(labels and SKIP_LABELS & set(json.loads(labels[0])))})
            if len(out) >= limit:
                break
        return out


def _stored_text(config, item_id: str) -> str | None:
    """The message's text from mail management's copy (quotes already removed), when it has read it."""
    if not store_path(config.store_dir, "mail").exists():
        return None
    with closing(open_store(config.store_dir, "mail", "worker")) as db:
        row = db.execute("SELECT text FROM messages WHERE item_id = ?", (item_id,)).fetchone()
    return row[0] if row and row[0] else None


def _text(raw: bytes, part: dict) -> str:
    text = raw.decode(part.get("charset") or "utf-8", errors="replace")
    if part["mime_type"] == "text/html":
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
        text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", text))
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()


def claims_from(answer: dict, message: dict, text: str, source_id: str, producer: str) -> list[Claim]:
    """Claims for the events the model listed, keeping only those whose quote appears in the message, and none from
    an email the model judged to be marketing or spam."""
    out = []
    if answer.get("email") in NOT_ABOUT_THE_OWNER:
        return out
    flat = " ".join(text.split())
    for event in answer.get("events", []):
        quote = " ".join(event["excerpt"].split())
        start = flat.find(quote) if quote else -1
        if start < 0 or not event["statement"].strip():
            continue  # no verbatim evidence: not proposed
        ref = f"{source_id}:{message['native_id']}"
        day = message["date"][:10]
        try:
            same_day = event["precision"] == "day" and event["earliest"] == event["latest"] == day
            when = When(event["expression"] or event["earliest"], event["precision"], event["earliest"],
                        event["latest"], (DateBasis(0, "message-date"),) if same_day else ())
            when.bounds()
            citation = Citation(ref=ref, span=(start, start + len(quote)), captured_excerpt=quote,
                                excerpt_sha256=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
                                date_facts=({"meaning": "message-date", "value": message["date"]},))
            out.append(Claim(life.claim_id(event["statement"], event["modality"], [ref]), event["kind"],
                             event["statement"].strip(), event["modality"], when, (citation,), producer,
                             uncertainty=("A booking or reservation supports a plan, not that it happened."
                                          if event["modality"] == "plan" else None),
                             significance=event.get("significance")))
        except (ClaimError, ValueError, TypeError):
            continue
    return out


def _earlier(db, store_dir, source_id: str, ref: str, rid: str) -> tuple[list[str], bool]:
    """Proposals earlier runs made from this message: the unreviewed ones, and whether the owner reviewed any."""
    ids = [r[0] for r in db.execute("SELECT claim_id FROM claims WHERE source_id = ? AND run_id != ? AND "
                                    "json_extract(body, '$.citations[0].ref') = ?", (source_id, rid, ref))]
    found = life.reviews(store_dir, ids, role=life.WRITER)
    decided = {c for c, d in found.items() if d.get("state") != "proposed"}
    return [c for c in ids if c not in decided], bool(decided)


def _stop(db, rid: str, state: dict, reason: str, retry_seconds: int, requested_at: str) -> list[dict]:
    """Stop the run with a plain reason, to be tried again later or as soon as the owner changes the request."""
    retry = datetime.now(timezone.utc) + timedelta(seconds=retry_seconds)
    state = {**state, "reason": reason, "stopped_at": life.now(), "retry_at": retry.isoformat(timespec="seconds"),
             "request_at": requested_at}
    db.execute("UPDATE runs SET state = 'stopped', progress = ? WHERE run_id = ?", (json.dumps(state), rid))
    db.commit()
    return [{"step": "stopped", "reason": reason}]

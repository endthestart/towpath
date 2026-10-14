"""Proposing life-story events from mail the owner chose, through the AI model the owner allowed.

The owner asks on the Life page for one mail account and period (a decision in the decisions store). Each poll,
the worker reads a few likely messages from that slice (found by subject words, newest first), fetches only their
text through the account's read-only connector, and asks the model, through the inference gateway and its grants,
for the events each message gives evidence of. An event is kept only when its supporting quote appears verbatim in
the message, so every proposed claim cites real text. Proposals are resumable: each message examined is recorded.
"""

import hashlib
import html
import json
import re
import time
from contextlib import closing
from datetime import date

from towpath import life
from towpath.canonical import digest
from towpath.stores import open_store, store_path
from towpath.unified.claims import Citation, Claim, ClaimError, DateBasis, When

TASK = "life.propose"
PROMPT_VERSION = "life.propose/1"
REQUEST_TARGET, REQUEST_KEY = "life:request", "slice"
SLICE_SECONDS = 90  # per worker poll, so mail checks and searches keep running
BATCH = 20  # candidate messages read from the index at a time
TEXT_LIMIT = 12_000  # characters of a message sent to the model
EXCERPT_LIMIT = 300

# Subject words that usually mark a message about something in the owner's life rather than advertising.
WORDS = ("booking", "reservation", "itinerary", "flight", "boarding", "hotel", "check-in", "confirmed",
         "confirmation", "your order", "receipt", "invoice", "shipped", "appointment", "offer letter", "welcome to",
         "admission", "accepted", "graduation", "congratulations", "wedding", "birthday", "lease", "closing",
         "moving", "interview", "tickets", "trip", "travel", "visa", "passport", "registration", "enrollment",
         "certificate", "diploma", "adoption", "birth", "condolence")

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["events"],
    "properties": {"events": {"type": "array", "maxItems": 5, "items": {
        "type": "object", "additionalProperties": False,
        "required": ["statement", "modality", "kind", "expression", "earliest", "latest", "precision", "excerpt"],
        "properties": {
            "statement": {"type": "string", "maxLength": 200},
            "modality": {"type": "string", "enum": ["plan", "occurred"]},
            "kind": {"type": "string", "enum": ["event", "activity", "accomplishment"]},
            "expression": {"type": "string", "maxLength": 80},
            "earliest": {"type": "string", "maxLength": 10},
            "latest": {"type": "string", "maxLength": 10},
            "precision": {"type": "string", "enum": ["day", "month", "year", "range"]},
            "excerpt": {"type": "string", "maxLength": EXCERPT_LIMIT}}}}}}

INSTRUCTION = """You read one email from the owner's own mailbox and list the events in the owner's life that it is
evidence of: trips planned or taken, notable purchases, appointments, jobs, education, moves, celebrations,
family events. Ignore advertising, newsletters and anything not about the owner. Most emails contain none:
then return {"events": []}.

For each event give:
- statement: one short sentence, such as "Booked a flight to Example City" or "Ordered a new bicycle".
- modality: "plan" when the email arranges something for later (a booking, reservation or appointment);
  "occurred" when it confirms something happened (an order placed, a payment made, a degree awarded).
- kind: "event", "activity" or "accomplishment".
- expression, earliest, latest, precision: when the event is or was, as stated in the email; dates as YYYY-MM-DD;
  precision "day", "month", "year" or "range". If the email gives no other date, use the email's date.
- excerpt: an exact quote copied from the email (under 300 characters) that supports the event."""


def request(store_dir) -> dict | None:
    """The slice the owner asked for on the Life page, if any."""
    if not store_path(store_dir, "decisions").exists():
        return None
    with closing(open_store(store_dir, "decisions", "worker")) as db:
        row = db.execute("SELECT value FROM settings WHERE target_id = ? AND key = ?",
                         (REQUEST_TARGET, REQUEST_KEY)).fetchone()
    return json.loads(row[0]) if row else None


def set_request(store_dir, source_id: str, after: str, before: str, author: str = "owner") -> dict:
    """The owner's choice of slice (web role). Changing it starts a new run; the claims already proposed stay."""
    for value in (after, before):
        date.fromisoformat(value)
    if after > before:
        raise ValueError("the period ends before it starts")
    value = {"source_id": source_id, "after": after, "before": before, "state": "running"}
    _write(store_dir, value, author)
    return value


def pause(store_dir, paused: bool, author: str = "owner") -> None:
    value = request(store_dir)
    if value is not None:
        _write(store_dir, {**value, "state": "paused" if paused else "running"}, author)


def _write(store_dir, value: dict, author: str) -> None:
    from towpath.decisions import now

    with closing(open_store(store_dir, "decisions", "web")) as db:
        db.execute("INSERT OR REPLACE INTO settings (target_id, key, value, author, at) VALUES (?,?,?,?,?)",
                   (REQUEST_TARGET, REQUEST_KEY, json.dumps(value, sort_keys=True), author, now()))
        db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES ('life-request', ?, ?, ?, ?)",
                   (REQUEST_TARGET, json.dumps(value, sort_keys=True), author, now()))
        db.commit()


def run_id(value: dict) -> str:
    return "lrun_" + digest({k: value[k] for k in ("source_id", "after", "before")})[:16]


def blocked(config, value: dict | None) -> str | None:
    """Why proposing can't run now, in plain words, or None."""
    from towpath import decisions
    from towpath.unified import grants

    if value is None:
        return "Choose a mail account and a period to start."
    if value.get("state") == "paused":
        return "Paused."
    if "life-evidence" not in grants.granted(config.store_dir).get(value["source_id"], set()):
        return "That account isn't allowed for your life story."
    endpoint_id = config.tasks.get(TASK)
    if endpoint_id is None:
        return "No AI model is connected."
    if not decisions.model_granted(config, endpoint_id, "content"):
        return "The AI model isn't allowed to read email text yet."
    if value["source_id"] not in config.sources:
        return "That mail account isn't connected."
    return None


def progress(store_dir, role: str = "web") -> dict | None:
    if not store_path(store_dir, "life").exists():
        return None
    with closing(open_store(store_dir, "life", role)) as db:
        row = db.execute("SELECT run_id, state, progress, started_at, finished_at FROM runs ORDER BY started_at DESC "
                         "LIMIT 1").fetchone()
    return {**json.loads(row[2] or "{}"), "run_id": row[0], "state": row[1], "started_at": row[3],
            "finished_at": row[4]} if row else None


# -- the worker side ------------------------------------------------------------------------------------------


def _candidates(config, value: dict, examined: set[str], limit: int) -> list[dict]:
    likes = " OR ".join("lower(coalesce(subject, '')) LIKE ?" for _ in WORDS)
    with closing(open_store(config.store_dir, "source", "worker")) as db:
        rows = db.execute(
            "SELECT item_id, native_id, subject, from_addr, date_utc FROM items WHERE source_id = ? "
            "AND absent_since_run IS NULL AND date_utc IS NOT NULL AND substr(date_utc, 1, 10) BETWEEN ? AND ? "
            f"AND ({likes}) ORDER BY date_utc DESC",
            (value["source_id"], value["after"], value["before"], *(f"%{w}%" for w in WORDS))).fetchall()
        out = []
        for row in rows:
            if row[0] in examined:
                continue
            part = db.execute("SELECT part_id, mime_type, charset FROM parts WHERE item_id = ? AND mime_type IN "
                              "('text/plain', 'text/html') AND (disposition IS NULL OR disposition != 'attachment') "
                              "ORDER BY mime_type != 'text/plain', part_id LIMIT 1", (row[0],)).fetchone()
            out.append({"item_id": row[0], "native_id": row[1], "subject": row[2], "from": row[3], "date": row[4],
                        "part": dict(zip(("part_id", "mime_type", "charset"), part, strict=True)) if part else None})
            if len(out) >= limit:
                break
        return out


def _text(raw: bytes, part: dict) -> str:
    text = raw.decode(part.get("charset") or "utf-8", errors="replace")
    if part["mime_type"] == "text/html":
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
        text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", text))
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()


def claims_from(answer: dict, message: dict, text: str, source_id: str, producer: str) -> list[Claim]:
    """Claims for the events the model listed, keeping only those whose quote appears in the message."""
    out = []
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
                                          if event["modality"] == "plan" else None)))
        except (ClaimError, ValueError, TypeError):
            continue
    return out


def propose_pending(config, credentials_dir, slice_seconds: float = SLICE_SECONDS, clock=time.monotonic) -> list[dict]:
    """One bounded slice of proposing for the owner's request; nothing happens unless everything is allowed."""
    from towpath.adapters import build_connector
    from towpath.models import gateway

    value = request(config.store_dir)
    if blocked(config, value) is not None:
        return []
    rid = run_id(value)
    endpoint_id = config.tasks[TASK]
    if gateway.capability_report(config, endpoint_id) is None:
        gateway.probe(config, endpoint_id)  # synthetic prompts only; needed once per model profile
    deadline = clock() + slice_seconds
    db = open_store(config.store_dir, "life", life.WRITER)
    connector = None
    try:
        now = life.now()
        db.execute("INSERT OR IGNORE INTO runs (run_id, source_id, slice, state, progress, started_at) "
                   "VALUES (?,?,?,?,?,?)", (rid, value["source_id"], json.dumps(value, sort_keys=True), "running",
                                            json.dumps({"examined": 0, "proposed": 0}), now))
        state = json.loads(db.execute("SELECT progress FROM runs WHERE run_id = ?", (rid,)).fetchone()[0])
        examined = {r[0] for r in db.execute("SELECT item_id FROM examined WHERE run_id = ?", (rid,))}
        batch = _candidates(config, value, examined, BATCH)
        if not batch:
            db.execute("UPDATE runs SET state = 'done', finished_at = coalesce(finished_at, ?) WHERE run_id = ?",
                       (now, rid))
            db.commit()
            return []
        connector = build_connector(config.sources[value["source_id"]], config)
        producer = f"model:{config.endpoints[endpoint_id].model}/{PROMPT_VERSION}"
        done = []
        for message in batch:
            if clock() > deadline or (request(config.store_dir) or {}).get("state") == "paused":
                break
            outcome, proposed = "no-text", 0
            if message["part"] is not None:
                raw = connector.fetch(message["native_id"], message["part"]["part_id"])
                text = _text(raw, message["part"])[:TEXT_LIMIT]
                prompt = (f"{INSTRUCTION}\n\nEmail date: {message['date']}\nFrom: {message['from']}\n"
                          f"Subject: {message['subject']}\n\n{text}")
                result = gateway.infer(config, TASK, prompt, "content", SCHEMA, [message["item_id"]],
                                       prompt_version=PROMPT_VERSION)
                outcome = result["status"]
                if outcome == "ok":
                    found = claims_from(result["output"], message, text, value["source_id"], producer)
                    proposed = sum(life.save(db, c, value["source_id"], rid) for c in found)
                elif outcome in ("refused", "disabled"):
                    break  # a grant or the model changed: stop, and say why on the Life page
            db.execute("INSERT OR REPLACE INTO examined (run_id, item_id, outcome, at) VALUES (?,?,?,?)",
                       (rid, message["item_id"], outcome, life.now()))
            state = {"examined": state["examined"] + 1, "proposed": state["proposed"] + proposed,
                     "last": message["date"][:10]}
            db.execute("UPDATE runs SET progress = ? WHERE run_id = ?", (json.dumps(state), rid))
            db.commit()
            done.append(outcome)
        return [{"source_id": value["source_id"], "step": "proposing", "examined": len(done),
                 "proposed_total": state["proposed"]}] if done else []
    finally:
        db.close()
        if connector is not None and hasattr(connector, "close"):
            connector.close()

"""Proposing life-story events from mail the owner chose, through the AI model the owner allowed.

The owner asks on the Life page for one mail account and period (a decision in the decisions store). Each poll,
the worker reads a few likely messages from that slice (found by subject words, newest first), fetches only their
text through the account's read-only connector, and asks the model, through the inference gateway and its grants,
for the events each message gives evidence of. An event is kept only when its supporting quote appears verbatim in
the message, so every proposed claim cites real text. Proposals are resumable: each message examined is recorded.

Advertising never reaches the model when the provider has already filed it (Gmail's Promotions), and the model
says what kind of email it read: nothing is proposed from marketing or spam. Each event carries the model's view
of its significance, so everyday purchases can wait under Minor on the Life page. A new prompt version reads the
slice again: the owner's reviewed proposals stay as they are, and unreviewed ones are proposed afresh.
"""

import hashlib
import html
import json
import re
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

from towpath import life
from towpath.canonical import digest
from towpath.stores import open_store, store_path
from towpath.unified.claims import Citation, Claim, ClaimError, DateBasis, When

TASK = "life.propose"
PROMPT_VERSION = "life.propose/2"
REQUEST_TARGET, REQUEST_KEY = "life:request", "slice"
AUTO_TARGET, AUTO_KEY = "life:auto", "state"
SLICE_SECONDS = 90  # per worker poll, so mail checks and searches keep running
RETRY_SECONDS = 30 * 60  # after the mail account stopped (a sign-in that expired, a server error)
MODEL_RETRY_SECONDS = 10 * 60  # after the model couldn't be reached
BUSY_RETRY_SECONDS = 60  # a gateway serving one request at a time answers 409 while it's busy
BATCH = 20  # candidate messages read from the index at a time
TEXT_LIMIT = 12_000  # characters of a message sent to the model
EXCERPT_LIMIT = 300
SKIP_LABELS = {"CATEGORY_PROMOTIONS", "SPAM", "TRASH"}  # filed as advertising or junk by the provider
NOT_ABOUT_THE_OWNER = ("marketing", "spam")

# Subject words that usually mark a message about something in the owner's life rather than advertising.
WORDS = ("booking", "reservation", "itinerary", "flight", "boarding", "hotel", "check-in", "confirmed",
         "confirmation", "your order", "receipt", "invoice", "shipped", "appointment", "offer letter", "welcome to",
         "admission", "accepted", "graduation", "congratulations", "wedding", "birthday", "lease", "closing",
         "moving", "interview", "tickets", "trip", "travel", "visa", "passport", "registration", "enrollment",
         "certificate", "diploma", "adoption", "birth", "condolence")

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["email", "events"],
    "properties": {"email": {"type": "string", "enum": ["personal", "transaction", "notification", "marketing",
                                                        "spam"]},
                   "events": {"type": "array", "maxItems": 5, "items": {
        "type": "object", "additionalProperties": False,
        "required": ["statement", "significance", "modality", "kind", "expression", "earliest", "latest", "precision",
                     "excerpt"],
        "properties": {
            "statement": {"type": "string", "maxLength": 200},
            "significance": {"type": "string", "enum": ["milestone", "notable", "routine"]},
            "modality": {"type": "string", "enum": ["plan", "occurred"]},
            "kind": {"type": "string", "enum": ["event", "activity", "accomplishment"]},
            "expression": {"type": "string", "maxLength": 80},
            "earliest": {"type": "string", "maxLength": 10},
            "latest": {"type": "string", "maxLength": 10},
            "precision": {"type": "string", "enum": ["day", "month", "year", "range"]},
            "excerpt": {"type": "string", "maxLength": EXCERPT_LIMIT}}}}}}

INSTRUCTION = """You read one email from the owner's own mailbox and decide whether it records something in the
owner's life.

First say what kind of email it is (email):
- "personal": written by a person to the owner.
- "transaction": a record of something the owner arranged or did: a booking, ticket, order, receipt, appointment,
  application, contract or payment.
- "notification": an automated notice: shipping and delivery updates, sign-ups and welcomes, password and security
  alerts, reminders, statements.
- "marketing": advertising, newsletters, offers, sales, surveys, recommendations, points and rewards.
- "spam": unsolicited or suspicious mail: prizes, scams, phishing, pre-approved offers, mail from strangers.
Marketing and spam are not about the owner's life: give no events for them.

Then list the events in the owner's life the email is evidence of. Most emails contain none: then "events" is [].

For each event give:
- statement: one short sentence, such as "Booked a flight to Example City" or "Started a job at Example Lock".
- significance: "milestone" for what changes the course of a life (a new job or leaving one, a move or new home,
  a degree, a marriage, a birth or death, a serious illness, buying a home or car); "notable" for what someone
  would tell a friend about (a trip, a concert, show or game, a wedding or party, a course, a big or meaningful
  purchase, a medical procedure); "routine" for everyday things (ordinary purchases such as a phone case or
  groceries, deliveries, bills, renewals, subscriptions, sign-ups, routine appointments).
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


def _requested_at(store_dir) -> str:
    """Which change of the owner's request is current: the decision log's sequence, which only grows."""
    with closing(open_store(store_dir, "decisions", "worker")) as db:
        row = db.execute("SELECT max(seq) FROM decision_log WHERE kind = 'life-request'").fetchone()
    return str(row[0] or 0)


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


def auto(store_dir, role: str = "worker") -> bool:
    """Whether the owner turned on finding events by itself, across every account the life story may use."""
    if not store_path(store_dir, "decisions").exists():
        return False
    with closing(open_store(store_dir, "decisions", role)) as db:
        row = db.execute("SELECT value FROM settings WHERE target_id = ? AND key = ?",
                         (AUTO_TARGET, AUTO_KEY)).fetchone()
    return bool(row and json.loads(row[0]) == "running")


def set_auto(store_dir, on: bool, author: str = "owner") -> None:
    from towpath.decisions import now

    value = json.dumps("running" if on else "paused")
    with closing(open_store(store_dir, "decisions", "web")) as db:
        db.execute("INSERT OR REPLACE INTO settings (target_id, key, value, author, at) VALUES (?,?,?,?,?)",
                   (AUTO_TARGET, AUTO_KEY, value, author, now()))
        db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES ('life-request', ?, ?, ?, ?)",
                   (AUTO_TARGET, value, author, now()))
        db.commit()


def run_id(value: dict) -> str:
    """One run per slice and prompt version: a new prompt reads the slice again."""
    return "lrun_" + digest({**{k: value[k] for k in ("source_id", "after", "before")}, "prompt": PROMPT_VERSION})[:16]


def blocked(config, value: dict | None, role: str = "worker") -> str | None:
    """Why proposing can't run now, in plain words, or None."""
    from towpath import decisions
    from towpath.unified import grants

    if value is None:
        return "Turn on finding events, or choose a mail account and a period."
    if value.get("state") == "paused":
        return "Paused."
    if "life-evidence" not in grants.granted(config.store_dir).get(value["source_id"], set()):
        return "That account isn't allowed for your life story."
    endpoint_id = config.tasks.get(TASK)
    if endpoint_id is None:
        return "No AI model is connected."
    if not decisions.model_granted(config, endpoint_id, "content", role=role):
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


def auto_progress(store_dir, role: str = "web") -> dict:
    """Finding by itself: messages read and events proposed so far, the years finished, and the latest stop."""
    out = {"examined": 0, "proposed": 0, "years": [], "reason": None, "retry_at": None}
    if not store_path(store_dir, "life").exists():
        return out
    with closing(open_store(store_dir, "life", role)) as db:
        for slice_, state, raw in db.execute("SELECT slice, state, progress FROM runs ORDER BY started_at"):
            value, p = json.loads(slice_), json.loads(raw or "{}")
            if not value.get("auto"):
                continue
            out["examined"] += p.get("examined", 0)
            out["proposed"] += p.get("proposed", 0)
            if state == "done":
                out["years"].append(f"{value['source_id']} {value['after'][:4]}")
            if state == "stopped":
                out["reason"], out["retry_at"] = p.get("reason"), p.get("retry_at")
    return out


# -- the worker side ------------------------------------------------------------------------------------------


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


RECHECK_SECONDS = 600  # how often finding by itself looks for new mail in years it has finished
_rechecked: dict[str, float] = {}


def _pending_request(config, value: dict | None) -> bool:
    """Whether the owner's own period request still has work (it goes before finding by itself)."""
    if value is None or value.get("state") == "paused":
        return False
    if not store_path(config.store_dir, "life").exists():
        return True
    with closing(open_store(config.store_dir, "life", "worker")) as db:
        row = db.execute("SELECT state FROM runs WHERE run_id = ?", (run_id(value),)).fetchone()
    return row is None or row[0] != "done"


def _years(config, source_id: str) -> list[str]:
    with closing(open_store(config.store_dir, "source", "worker")) as db:
        return [r[0] for r in db.execute("SELECT DISTINCT substr(date_utc, 1, 4) FROM items WHERE source_id = ? AND "
                                         "date_utc IS NOT NULL AND absent_since_run IS NULL ORDER BY 1 DESC",
                                         (source_id,))]


def _next_auto(config, clock=time.monotonic) -> dict | None:
    """Finding by itself: the newest year, in any account the life story may use, that still has unread likely
    messages. Finished years are looked at again now and then for newly arrived mail."""
    from towpath.unified import grants

    granted = grants.granted(config.store_dir)
    # Accounts whose text mail management already stores come first (no provider calls), then the others.
    allowed = sorted((sid for sid, f in granted.items() if "life-evidence" in f and sid in config.sources),
                     key=lambda sid: ("mail-manage" not in granted[sid], sid))
    done: dict[str, str] = {}
    if store_path(config.store_dir, "life").exists():
        with closing(open_store(config.store_dir, "life", "worker")) as db:
            done = {r[0]: r[1] for r in db.execute("SELECT run_id, state FROM runs")}
    current = str(date.today().year)
    for sid in allowed:
        for year in _years(config, sid):
            value = {"source_id": sid, "after": f"{year}-01-01", "before": f"{year}-12-31", "state": "running",
                     "auto": True}
            rid = run_id(value)
            if done.get(rid) == "done":
                if year != current or clock() - _rechecked.get(rid, float("-inf")) < RECHECK_SECONDS:
                    continue
                _rechecked[rid] = clock()
            return value
    return None


def propose_pending(config, credentials_dir, slice_seconds: float = SLICE_SECONDS, clock=time.monotonic) -> list[dict]:
    """One bounded slice of proposing for the owner's request; nothing happens unless everything is allowed.

    Never raises for a source or model problem: the run stops with a plain reason, shown on the Life page, and
    is tried again after a while or as soon as the owner resumes."""
    from towpath.adapters import build_connector
    from towpath.adapters.errors import SyncStop
    from towpath.mail.threads import _failure
    from towpath.models import gateway

    value = request(config.store_dir)
    if not _pending_request(config, value):
        value = _next_auto(config, clock) if auto(config.store_dir) else None
    if blocked(config, value) is not None or gateway.background_wait(config, TASK):
        return []  # not allowed, or background model work waits its turn: no mail is fetched
    rid = run_id(value)
    endpoint_id = config.tasks[TASK]
    deadline = clock() + slice_seconds
    db = open_store(config.store_dir, "life", life.WRITER)
    connector = None
    try:
        now = life.now()
        db.execute("INSERT OR IGNORE INTO runs (run_id, source_id, slice, state, progress, started_at) "
                   "VALUES (?,?,?,?,?,?)", (rid, value["source_id"], json.dumps(value, sort_keys=True), "running",
                                            json.dumps({"examined": 0, "proposed": 0}), now))
        run_state, raw_state = db.execute("SELECT state, progress FROM runs WHERE run_id = ?", (rid,)).fetchone()
        state = {k: v for k, v in json.loads(raw_state).items()
                 if k not in ("reason", "stopped_at", "retry_at", "request_at")}
        requested_at = _requested_at(config.store_dir)
        if run_state == "stopped":
            stopped = json.loads(raw_state)
            if now < stopped.get("retry_at", "") and requested_at == stopped.get("request_at"):
                return []  # wait for the retry time, or for the owner to resume
            db.execute("UPDATE runs SET state = 'running', progress = ? WHERE run_id = ?", (json.dumps(state), rid))
            db.commit()
        report = gateway.capability_report(config, endpoint_id)
        if report is None or not report["capabilities"].get("chat"):
            gateway.probe(config, endpoint_id)  # synthetic prompts only
            report = gateway.capability_report(config, endpoint_id)
            if report is None or not report["capabilities"].get("chat"):
                return _stop(db, rid, state, "Towpath's worker couldn't reach the AI model.", MODEL_RETRY_SECONDS,
                             requested_at)
        examined = {r[0] for r in db.execute("SELECT item_id FROM examined WHERE run_id = ?", (rid,))}
        batch = _candidates(config, value, examined, BATCH)
        if not batch:
            db.execute("UPDATE runs SET state = 'done', finished_at = coalesce(finished_at, ?) WHERE run_id = ?",
                       (now, rid))
            db.commit()
            return []
        producer = f"model:{config.endpoints[endpoint_id].model}/{PROMPT_VERSION}"
        done = []
        for message in batch:
            if clock() > deadline or (request(config.store_dir) or {}).get("state") == "paused" or \
                    gateway.background_wait(config, TASK):
                break
            outcome, proposed = "no-text", 0
            ref = f"{value['source_id']}:{message['native_id']}"
            unreviewed, decided = _earlier(db, config.store_dir, value["source_id"], ref, rid)
            if decided:
                outcome = "reviewed"  # the owner already decided on this message's proposals
            elif message["advertising"]:
                outcome = "advertising"
                db.executemany("DELETE FROM claims WHERE claim_id = ?", [(c,) for c in unreviewed])
            elif message["part"] is not None or _stored_text(config, message["item_id"]):
                text = _stored_text(config, message["item_id"])  # what mail management already read
                if text is None:
                    try:
                        if connector is None:
                            connector = build_connector(config.sources[value["source_id"]], config)
                        raw = connector.fetch(message["native_id"], message["part"]["part_id"])
                    except SyncStop as exc:
                        return _stop(db, rid, state, f"The mail account stopped: {exc.reason}", RETRY_SECONDS,
                                     requested_at)
                    except Exception as exc:  # noqa: BLE001 - never stop the worker; keep data out of the reason
                        return _stop(db, rid, state, f"Reading mail stopped on an unexpected error "
                                                     f"({type(exc).__name__}).", RETRY_SECONDS, requested_at)
                    text = _text(raw, message["part"])
                text = text[:TEXT_LIMIT]
                prompt = (f"{INSTRUCTION}\n\nEmail date: {message['date']}\nFrom: {message['from']}\n"
                          f"Subject: {message['subject']}\n\n{text}")
                from email.utils import parseaddr

                from towpath.mail.privacy import targets
                from towpath.mail.threads import item_refused

                raw = str(message.get("from") or "")  # an address, or a name or number from a chat export
                sender = (parseaddr(raw)[1].lower() if "@" in raw else raw) or None
                result = gateway.infer(config, TASK, prompt, "content", SCHEMA,
                                       targets(value["source_id"], [message["item_id"]], [sender]),
                                       prompt_version=PROMPT_VERSION, background=True)
                outcome = result["status"]
                if item_refused(result):  # this message or its sender is kept from this model: the rest go on
                    outcome = "kept"
                elif outcome == "ok":
                    db.executemany("DELETE FROM claims WHERE claim_id = ?", [(c,) for c in unreviewed])
                    found = claims_from(result["output"], message, text, value["source_id"], producer)
                    proposed = sum(life.save(db, c, value["source_id"], rid) for c in found)
                elif outcome in ("refused", "disabled"):
                    break  # a grant or the model changed: the Life page says why
                elif _failure(result) == "paced":
                    break  # background model work waits its turn: this message stays unread
                elif _failure(result) == "busy":
                    return _stop(db, rid, state, "The AI model is busy with another request; Towpath tries again in "
                                                 "a minute.", BUSY_RETRY_SECONDS, requested_at)  # stays unread
                elif _failure(result) == "transient":
                    return _stop(db, rid, state, "The AI model didn't answer; Towpath tries again shortly.",
                                 MODEL_RETRY_SECONDS, requested_at)  # this message stays unread, to be tried again
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

"""The worker's slice: read likely messages, newest years first, within its time and the model's share."""

import json
import time
from contextlib import closing
from datetime import date

from towpath import life
from towpath.life_propose.candidates import _candidates, claims_from, _earlier, _stop, _stored_text, _text
from towpath.life_propose.choices import auto, blocked, request, _requested_at, run_id
from towpath.life_propose.common import (
    BATCH,
    BUSY_RETRY_SECONDS,
    INSTRUCTION,
    MODEL_RETRY_SECONDS,
    PROMPT_VERSION,
    RETRY_SECONDS,
    SCHEMA,
    SLICE_SECONDS,
    TASK,
    TEXT_LIMIT,
)
from towpath.stores import open_store, store_path

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


def _allowed(config) -> list[str]:
    """Accounts the life story may use: those whose text mail management already stores come first (no provider
    calls), then the others."""
    from towpath.unified import grants

    granted = grants.granted(config.store_dir)
    return sorted((sid for sid, f in granted.items() if "life-evidence" in f and sid in config.sources),
                  key=lambda sid: ("mail-manage" not in granted[sid], sid))


def _year_slice(source_id: str, year: str) -> dict:
    return {"source_id": source_id, "after": f"{year}-01-01", "before": f"{year}-12-31", "state": "running",
            "auto": True}


def _next_auto(config, clock=time.monotonic) -> dict | None:
    """Finding by itself: the newest year, in any account the life story may use, that still has unread likely
    messages. Finished years are looked at again now and then for newly arrived mail."""
    done: dict[str, str] = {}
    if store_path(config.store_dir, "life").exists():
        with closing(open_store(config.store_dir, "life", "worker")) as db:
            done = {r[0]: r[1] for r in db.execute("SELECT run_id, state FROM runs")}
    current = str(date.today().year)
    for sid in _allowed(config):
        for year in _years(config, sid):
            value = _year_slice(sid, year)
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
    # A request that is paused or can't go on holds nothing else up.
    if not _pending_request(config, value) or blocked(config, value) is not None:
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
            paused = not value.get("auto") and (request(config.store_dir) or {}).get("state") == "paused"
            if clock() > deadline or paused or gateway.background_wait(config, TASK):
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

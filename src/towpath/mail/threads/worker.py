"""The worker's slice: rebuild conversations, ask the model, write drafts."""

import json
import time
from datetime import datetime, timedelta, timezone

from towpath.mail.threads.building import owner, _rebuild
from towpath.mail.threads.classify import blocked, _classify, ready, _settle_by_sender
from towpath.mail.threads.common import (
    BUSY_SECONDS,
    DRAFT_DAYS,
    RETRY_SECONDS,
    ROLE,
    SLICE_SECONDS,
    TASK_DRAFT,
    TASK_STATE,
    now,
)
from towpath.mail.threads.drafts import _draft
from towpath.stores import open_store, store_path


def _progress(intel, source_id: str) -> dict:
    row = intel.execute("SELECT state FROM progress WHERE source_id = ?", (source_id,)).fetchone()
    return json.loads(row[0]) if row else {}


def _set_progress(intel, source_id: str, state: dict) -> None:
    intel.execute("INSERT INTO progress (source_id, watermark, state, updated_at) VALUES (?, 0, ?, ?) ON CONFLICT "
                  "(source_id) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at",
                  (source_id, json.dumps(state), now()))
    intel.commit()


def track_pending(config, slice_seconds: float = SLICE_SECONDS, clock=time.monotonic) -> list[dict]:
    """One bounded slice per managed account: rebuild changed conversations, then the model's decisions and
    drafts. Never raises for a model problem; it waits and tries again."""
    from towpath.mail import analysis

    summaries = []
    for sid in analysis.eligible(config):
        if not store_path(config.store_dir, "mail").exists():
            continue
        deadline = clock() + slice_seconds
        intel = open_store(config.store_dir, "mailintel", ROLE)
        mail = open_store(config.store_dir, "mail", ROLE)
        try:
            me = owner(config.store_dir, sid, mail)
            rebuilt = _rebuild(intel, mail, config.store_dir, sid, me, deadline, clock)
            _settle_by_sender(config, intel, mail, sid, me)
            state = _progress(intel, sid)
            classified = drafted = 0
            stop = None
            if not (state.get("retry_at") and now() < state["retry_at"]):
                # Recent conversations first, then their drafts, then the rest of the history.
                state_ok = blocked(config, sid, TASK_STATE) is None and ready(config, TASK_STATE)
                if blocked(config, sid, TASK_STATE) is None and not state_ok:
                    stop = "transient"
                if state_ok:
                    classified, stop = _classify(config, intel, mail, sid, me, deadline, clock, days=DRAFT_DAYS)
                if stop is None and blocked(config, sid, TASK_DRAFT) is None:
                    if ready(config, TASK_DRAFT):
                        drafted, stop = _draft(config, intel, mail, sid, me, deadline, clock)
                    else:
                        stop = "transient"
                if stop is None and state_ok:
                    more, stop = _classify(config, intel, mail, sid, me, deadline, clock)
                    classified += more
                wait = {"busy": BUSY_SECONDS, "transient": RETRY_SECONDS}.get(stop)
                state = {**{k: v for k, v in state.items() if k not in ("retry_at", "reason")},
                         **({"retry_at": (datetime.now(timezone.utc) + timedelta(seconds=wait)).isoformat(
                             timespec="seconds"), "reason": "The AI model was busy or didn't answer; Towpath tries "
                                                            "again shortly."} if wait else {})}
                _set_progress(intel, sid, state)
            if rebuilt or classified or drafted:
                summaries.append({"source_id": sid, "step": "mail-conversations", "rebuilt": rebuilt,
                                  "classified": classified, "drafted": drafted})
        finally:
            intel.close()
            mail.close()
    return summaries

"""Single connector process for explicitly queued requests, owner-started indexing, and mail management for the
accounts the owner chose (reading, watching for new mail, carrying out approved changes); never schedules sync or
models on its own."""

import fcntl
import json
import signal
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from pathlib import Path

from towpath import connect, connections, folders
from towpath.unified import requests


@contextmanager
def singleton(store_dir):
    with (store_dir / "request-worker.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another request worker holds this store") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _owner_facing(config, credentials_dir) -> list[dict]:
    """What the owner is waiting on, quick when nothing is queued: changes they asked for (and approved ones),
    plain-English searches, photo requests, rule drafts and questions asked on the Life page. Run between the long
    model slices so none waits for a whole poll."""
    from towpath import life_ask, photos, search_translate
    from towpath.mail import executor, rule_drafts, search_ask

    out = executor.run_pending(config, credentials_dir)
    out += search_translate.translate_pending(config)
    out += search_ask.translate_pending(config)
    out += photos.translate_pending(config)
    out += rule_drafts.draft_pending(config)
    out += life_ask.answer_pending(config)
    return out


def run_once(config, credentials_dir=None):
    """Existing queue receipts survive restart; idle polling makes no provider calls.

    Accounts added in the UI are read afresh each poll, so a new connection needs no restart. Indexing runs
    only for connections the owner started or put on a schedule, one bounded slice each, after searches and
    content requests."""
    if credentials_dir is not None:
        connections.import_configured(config, credentials_dir)
        config = connections.merged(config, credentials_dir)
    search = requests.run_searches(config, limit=20)
    fetched = connect.fetch_requests(config)
    indexing = []
    if credentials_dir is not None:  # schedules the owner chose start connections first, then they run as if started
        indexing += connections.start_scheduled(config.store_dir, credentials_dir)
        indexing += connections.index_pending(config, credentials_dir)
        from towpath import dav_sync  # contacts and calendars

        indexing += dav_sync.sync_pending(config, credentials_dir)
    if credentials_dir is not None:  # folders, when this connector has a data folder
        indexing += folders.index_pending(config, Path(config.store_dir).parent)
        from towpath import photos  # photo headers for photo search, and plain-English photo requests

        indexing += photos.purge_private(config)
        indexing += photos.extract_pending(config)
        from towpath import apple_photos, thumbs

        indexing += apple_photos.mark_hidden(config)  # before previews: hidden photos never get one
        indexing += thumbs.thumbs_pending(config)
        from towpath import space_hash

        indexing += space_hash.confirm_pending(config)
        indexing += _owner_facing(config, credentials_dir)
        from towpath import life_propose  # life-story proposals, only once the owner allowed them

        indexing += life_propose.propose_pending(config, credentials_dir)
        from towpath import life_calendar

        indexing += life_calendar.propose(config)
        from towpath import life_photos

        indexing += life_photos.propose(config)
        from towpath import life_documents

        indexing += life_documents.propose(config)
        from towpath import export_text, life_bookings

        indexing += export_text.index_pending(config)

        indexing += life_bookings.propose(config)
        from towpath import life_evidence

        indexing += life_evidence.keep_pending(config)
        from towpath import life_groups

        indexing += life_groups.propose_groups(config)
        from towpath import life_ask

        indexing += life_ask.answer_pending(config)
        indexing += _owner_facing(config, credentials_dir)
        from towpath.mail import analysis, executor, watch  # mail management, for accounts the owner chose

        watch.ensure(config, analysis.eligible(config))
        indexing += executor.run_pending(config, credentials_dir)
        indexing += analysis.build_search(config)
        indexing += analysis.analyze_pending(config)
        indexing += _owner_facing(config, credentials_dir)
        from towpath.mail import threads

        indexing += threads.track_pending(config)
        indexing += _owner_facing(config, credentials_dir)
        from towpath.mail import rules, senders

        indexing += senders.categorize_pending(config)
        indexing += rules.evaluate_pending(config)
        indexing += _owner_facing(config, credentials_dir)
        from towpath.mail import purchases

        indexing += purchases.read_pending(config)
        indexing += _owner_facing(config, credentials_dir)
        from towpath.mail import archive

        indexing += archive.archive_pending(config)
        from towpath.mail import filing

        indexing += filing.file_pending(config)
        from towpath.mail import notify

        indexing += notify.alerts_pending(config)
        indexing += notify.follow_ups_pending(config)
        indexing += notify.reply_labels_pending(config)
        indexing += notify.digests_pending(config)
    return {"searches": search["searches_run"], "fetched": fetched["fetched"],
            "failed": fetched["failed"], "presence_checked": fetched.get("presence_checked", 0),
            "termination": fetched["termination"], "indexing": indexing}


def run(config, poll_seconds=10, once=False, credentials_dir=None):
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        with singleton(config.store_dir):
            while not stopping:
                result = run_once(config, credentials_dir)
                if any(result[k] for k in ("searches", "fetched", "failed", "presence_checked", "indexing")) or (
                        result["termination"] != "complete"):
                    print(json.dumps({"at": datetime.now(timezone.utc).isoformat(), **result}), flush=True)
                if once:
                    return
                # Quota/auth/server stops must not become a tight retry loop.
                delay = poll_seconds if result["termination"] == "complete" else max(poll_seconds, 300)
                deadline = time.monotonic() + delay
                while not stopping and time.monotonic() < deadline:
                    time.sleep(min(1, max(0, deadline - time.monotonic())))
    finally:
        from towpath.mail import watch

        watch.stop_all()
        for sig, handler in previous.items():
            signal.signal(sig, handler)

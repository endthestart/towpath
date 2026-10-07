"""Single connector process for explicitly queued requests and owner-started indexing; never schedules
sync or models on its own."""

import fcntl
import json
import signal
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from towpath import connect, connections
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


def run_once(config, credentials_dir=None):
    """Existing queue receipts survive restart; idle polling makes no provider calls.

    Accounts added in the UI are read afresh each poll, so a new connection needs no restart. Indexing runs
    only for connections the owner started, one bounded slice each, after searches and content requests."""
    if credentials_dir is not None:
        connections.import_configured(config, credentials_dir)
        config = connections.merged(config, credentials_dir)
    search = requests.run_searches(config, limit=20)
    fetched = connect.fetch_requests(config)
    indexing = connections.index_pending(config, credentials_dir) if credentials_dir is not None else []
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
        for sig, handler in previous.items():
            signal.signal(sig, handler)

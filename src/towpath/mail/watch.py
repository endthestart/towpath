"""Learning of new mail soon after it arrives: IDLE (RFC 2177) on each managed account's inbox.

One thread per managed IMAP account, in the connector, over its own read-only connection. When the server reports
a change, the watcher asks for the account to be indexed (as the Index button does), and the next worker poll
indexes it and reads the new messages. The watcher records only its state and times, never message data. It leaves
an account alone while its indexing is paused, and backs off after a failure.
"""

import threading
import time
from contextlib import closing

from towpath.stores import open_store

ROLE = "connect"
IDLE_SECONDS = 120.0  # each IDLE lasts at most this long, so a stop is noticed and the connection stays fresh
SETTLE_SECONDS = 5.0  # wait for a burst of arrivals to finish before asking for indexing
BACKOFF_SECONDS = (30, 60, 300, 900)

_watchers: dict[str, "Watcher"] = {}


def _record(store_dir, source_id: str, state: str, detail: str | None = None) -> None:
    from towpath.mail.analysis import now

    with closing(open_store(store_dir, "mail", ROLE)) as db:
        db.execute("INSERT OR REPLACE INTO watch (source_id, state, detail, at) VALUES (?,?,?,?)",
                   (source_id, state, detail, now()))
        db.commit()


def state(store_dir, source_id: str, role: str = "web") -> dict | None:
    from towpath.stores import store_path

    if not store_path(store_dir, "mail").exists():
        return None
    import sqlite3

    try:
        with closing(open_store(store_dir, "mail", role)) as db:
            row = db.execute("SELECT state, detail, at FROM watch WHERE source_id = ?", (source_id,)).fetchone()
    except sqlite3.OperationalError:  # the connector is creating the store right now
        return None
    return dict(row) if row else None


def request_indexing(store_dir, source_id: str) -> bool:
    """Ask for indexing as the Index button would, unless it is running, requested or paused."""
    from towpath import connections

    c = connections.get(store_dir, source_id)
    if c is None or c["state"] != "ready" or c["indexing"] != "idle":
        return False
    connections._update(store_dir, source_id, "indexing-new-mail", indexing="requested", last_error=None)
    return True


class Watcher(threading.Thread):
    def __init__(self, config, source_id: str, idle_seconds: float = IDLE_SECONDS,
                 settle_seconds: float = SETTLE_SECONDS, backoff=BACKOFF_SECONDS):
        super().__init__(name=f"mail-watch-{source_id}", daemon=True)
        self.config, self.source_id = config, source_id
        self.idle_seconds, self.settle_seconds, self.backoff = idle_seconds, settle_seconds, backoff
        self.stopping = threading.Event()

    def stop(self) -> None:
        self.stopping.set()

    def _mailbox(self, connector) -> str | None:
        boxes = connector._mailboxes()
        return next((b for b in boxes if b.upper() == "INBOX"), boxes[0] if boxes else None)

    def run(self) -> None:
        from towpath import connections
        from towpath.adapters import build_connector
        from towpath.adapters.imap import _mapped_errors

        failures = 0
        store_dir = self.config.store_dir
        while not self.stopping.is_set():
            c = connections.get(store_dir, self.source_id)
            if c is None or c["state"] != "ready" or c["indexing"] == "paused":
                _record(store_dir, self.source_id, "waiting", "indexing is paused or the account isn't ready")
                self.stopping.wait(60)
                continue
            connector = None
            try:
                with _mapped_errors():
                    connector = build_connector(self.config.sources[self.source_id], self.config)
                    session = connector.session
                    if "IDLE" not in {x.upper() for x in session.capabilities()}:
                        _record(store_dir, self.source_id, "unsupported", "the server doesn't offer IDLE")
                        self.stopping.wait(3600)
                        continue
                    mailbox = self._mailbox(connector)
                    if mailbox is None:
                        self.stopping.wait(300)
                        continue
                    session.examine(mailbox)
                    _record(store_dir, self.source_id, "watching", mailbox)
                    failures = 0
                    while not self.stopping.is_set():
                        if session.wait_for_changes(self.idle_seconds):
                            self.stopping.wait(self.settle_seconds)
                            if request_indexing(store_dir, self.source_id):
                                _record(store_dir, self.source_id, "watching", f"{mailbox}: new mail, indexing")
                        if (connections.get(store_dir, self.source_id) or {}).get("indexing") == "paused":
                            break
            except Exception as exc:  # noqa: BLE001 - record the type only, then back off
                failures += 1
                wait = self.backoff[min(failures, len(self.backoff)) - 1]
                _record(store_dir, self.source_id, "stopped", f"{type(exc).__name__}; trying again in {wait} s")
                self.stopping.wait(wait)
            finally:
                if connector is not None:
                    connector.close()


def ensure(config, eligible: list[str], factory=Watcher) -> list[str]:
    """Start a watcher for each eligible account without a live one; stop the rest. Returns those running."""
    for sid in list(_watchers):
        w = _watchers[sid]
        if sid not in eligible or not w.is_alive() or w.config.sources.get(sid) != config.sources.get(sid):
            _watchers.pop(sid).stop()  # gone, ended, or its connection settings changed
    for sid in eligible:
        if sid not in _watchers:
            _watchers[sid] = factory(config, sid)
            _watchers[sid].start()
    return sorted(_watchers)


def stop_all(timeout: float = 5.0) -> None:
    for watcher in list(_watchers.values()):
        watcher.stop()
    deadline = time.monotonic() + timeout
    for sid, watcher in list(_watchers.items()):
        watcher.join(max(0.0, deadline - time.monotonic()))
        _watchers.pop(sid, None)

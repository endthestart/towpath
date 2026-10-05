"""Gmail request pacing with a persistent, shared quota budget.

Every request attempt, including retries, books its method's quota cost here
before it is sent. The budget is stored in SQLite (``quota.db`` in the store
directory), so restarting a command or switching commands neither resets the
daily total nor allows a burst. An exclusive file lock per budget keeps two
Towpath commands from spending the same budget at once.

These are Towpath's own conservative budgets, not claims about Google's
limits. The defaults are also the maximums: a deployment may set lower values
when its project's actual quotas require, never higher without a code change.
"""

import fcntl
import os
import re
import time
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

from towpath.adapters.errors import DailyBudgetStop, LockBusy
from towpath.stores import open_store

# Published Gmail quota-unit costs per method (checked 2026-10-02; see docs/evaluations/gmail-sync-hardening.md).
METHOD_COSTS = {
    "users.getProfile": 1,
    "users.messages.list": 5,
    "users.messages.get": 20,
    "users.messages.attachments.get": 20,
    "users.history.list": 2,
}
MINUTE = 60.0


@dataclass(frozen=True)
class PacingSettings:
    budget_id: str = "default"
    min_interval_seconds: float = 1.0
    units_per_minute: int = 1200
    daily_units: int = 1_800_000
    day_timezone: str = "America/Los_Angeles"
    max_attempts: int = 6
    backoff_base_seconds: float = 2.0
    backoff_max_seconds: float = 300.0
    checkpoint_every: int = 25

    # (minimum, maximum) for each numeric setting. Defaults are the maximum rates.
    LIMITS = {
        "min_interval_seconds": (1.0, 3600.0),
        "units_per_minute": (max(METHOD_COSTS.values()), 1200),
        "daily_units": (100, 1_800_000),
        "max_attempts": (1, 10),
        "backoff_base_seconds": (0.5, 60.0),
        "backoff_max_seconds": (1.0, 3600.0),
        "checkpoint_every": (1, 1000),
    }

    @classmethod
    def from_table(cls, table: dict) -> "PacingSettings":
        known = {f.name for f in fields(cls)}
        unknown = set(table) - known
        if unknown:
            raise ValueError(f"unknown key(s) in [gmail_pacing]: {', '.join(sorted(unknown))}")
        settings = cls(**table)
        settings.validate()
        return settings

    def validate(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.budget_id):
            raise ValueError("budget_id must be 1-64 letters, digits, '-' or '_'")
        for name, (low, high) in self.LIMITS.items():
            value = getattr(self, name)
            if not (low <= value <= high):
                raise ValueError(f"{name} must be between {low} and {high} (got {value}); "
                                 "Towpath's defaults are its maximum rates")
        if self.backoff_max_seconds < self.backoff_base_seconds:
            raise ValueError("backoff_max_seconds must be at least backoff_base_seconds")
        _zone(self.day_timezone)


def _zone(name: str):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown day_timezone {name!r}") from None


class BudgetLock:
    """An exclusive, process-wide lock per budget, shared by limiters in one process."""

    _held: dict[str, list] = {}

    def __init__(self, path: Path):
        self.path = str(path)

    @classmethod
    def acquire(cls, store_dir: Path, budget_id: str) -> "BudgetLock":
        path = Path(store_dir) / "locks" / f"gmail-{budget_id}.lock"
        key = str(path)
        if key in cls._held:
            cls._held[key][1] += 1
            return cls(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise LockBusy(f"another Towpath command is using Gmail budget '{budget_id}'; "
                           "wait for it to finish (one Gmail command per budget at a time)") from None
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        cls._held[key] = [fd, 1]
        return cls(path)

    def release(self) -> None:
        entry = self._held.get(self.path)
        if entry is None:
            return
        entry[1] -= 1
        if entry[1] == 0:
            fcntl.flock(entry[0], fcntl.LOCK_UN)
            os.close(entry[0])
            del self._held[self.path]

    @staticmethod
    def is_held_elsewhere(store_dir: Path, budget_id: str) -> bool:
        path = Path(store_dir) / "locks" / f"gmail-{budget_id}.lock"
        if str(path) in BudgetLock._held or not path.exists():
            return False
        fd = os.open(path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True
        finally:
            os.close(fd)


def _default_sleep(seconds: float) -> None:
    # Short slices keep Ctrl-C responsive during long waits.
    end = time.monotonic() + seconds
    while (remaining := end - time.monotonic()) > 0:
        time.sleep(min(remaining, 0.5))


def budget_day(at: float, zone) -> str:
    return datetime.fromtimestamp(at, timezone.utc).astimezone(zone).date().isoformat()


def budget_usage(db, settings: PacingSettings, account: str, now: float) -> dict:
    """Units used in the last minute (this account) and today (whole budget). Takes no lock."""
    budget = settings.budget_id
    day = budget_day(now, _zone(settings.day_timezone))
    minute = db.execute("SELECT COALESCE(SUM(units), 0) FROM attempts WHERE budget_id = ? AND account = ? "
                        "AND at > ?", (budget, account, now - MINUTE)).fetchone()[0]
    daily = db.execute("SELECT COALESCE(SUM(units), 0) FROM attempts WHERE budget_id = ? AND day = ?",
                       (budget, day)).fetchone()[0]
    return {"budget_id": budget, "minute_used": minute, "minute_limit": settings.units_per_minute,
            "day": day, "day_used": daily, "day_limit": settings.daily_units, "day_timezone": settings.day_timezone}


# Module-level so tests can substitute a fake clock; the CLI always uses these.
CLOCK = time.time
SLEEP = _default_sleep


def limiter_for(config, account: str, on_wait=None) -> "QuotaLimiter":
    """The limiter every real Gmail connection uses: persistent budget plus exclusive lock."""
    return QuotaLimiter(config.store_dir, config.gmail_pacing, account, clock=CLOCK, sleep=SLEEP, on_wait=on_wait)


class QuotaLimiter:
    """Books every attempt against a persistent budget, waiting as needed.

    ``clock`` returns epoch seconds; ``sleep`` waits and may raise
    KeyboardInterrupt to cancel. ``on_wait(seconds, reason)`` reports waits.
    """

    def __init__(self, store_dir: Path, settings: PacingSettings, account: str, clock=time.time,
                 sleep=_default_sleep, on_wait=None, lock: bool = True):
        settings.validate()
        self.settings = settings
        self.account = account
        self.clock = clock
        self._sleep = sleep
        self.on_wait = on_wait
        self.zone = _zone(settings.day_timezone)
        self.lock = BudgetLock.acquire(store_dir, settings.budget_id) if lock else None
        self.db = open_store(store_dir, "quota", "connect")
        self.attempts = 0
        self.units = 0

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None
        if self.lock is not None:
            self.lock.release()
            self.lock = None

    def day(self, at: float) -> str:
        return budget_day(at, self.zone)

    def usage(self) -> dict:
        return budget_usage(self.db, self.settings, self.account, self.clock())

    def wait(self, seconds: float, reason: str) -> None:
        """A cancellable wait that reports itself (also used for retry backoff)."""
        if seconds <= 0:
            return
        if self.on_wait:
            self.on_wait(seconds, reason)
        self._sleep(seconds)

    def _required_wait(self, now: float, cost: int) -> tuple[float, str]:
        s, budget = self.settings, self.settings.budget_id
        last = self.db.execute("SELECT MAX(at) FROM attempts WHERE budget_id = ?", (budget,)).fetchone()[0]
        wait, reason = 0.0, ""
        if last is not None:
            gap = s.min_interval_seconds - max(0.0, now - last)  # a clock moved backwards waits a full interval
            if gap > wait:
                wait, reason = gap, "minimum interval between requests"
        rows = self.db.execute("SELECT at, units FROM attempts WHERE budget_id = ? AND account = ? AND at > ? "
                               "ORDER BY at", (budget, self.account, now - MINUTE)).fetchall()
        used = sum(r[1] for r in rows)
        if used + cost > s.units_per_minute:
            remaining = used
            for at, units in rows:
                remaining -= units
                if remaining + cost <= s.units_per_minute:
                    gap = at + MINUTE - now
                    if gap > wait:
                        wait, reason = gap, "per-minute budget"
                    break
        return wait, reason

    def acquire(self, method: str) -> None:
        cost = METHOD_COSTS[method]
        s = self.settings
        while True:
            now = self.clock()
            day = self.day(now)
            daily = self.db.execute("SELECT COALESCE(SUM(units), 0) FROM attempts WHERE budget_id = ? AND day = ?",
                                    (s.budget_id, day)).fetchone()[0]
            if daily + cost > s.daily_units:
                raise DailyBudgetStop(f"Towpath's daily budget of {s.daily_units} units for '{s.budget_id}' is used "
                                      f"({daily} units on {day}, {s.day_timezone}); the next run after the day "
                                      "changes resumes where this one stopped")
            wait, reason = self._required_wait(now, cost)
            if wait > 0:
                self.wait(wait, reason)
                continue
            self.db.execute("INSERT INTO attempts (at, budget_id, account, method, units, day) VALUES (?,?,?,?,?,?)",
                            (now, s.budget_id, self.account, method, cost, day))
            self.db.execute("DELETE FROM attempts WHERE at < ?", (now - 3 * 86400,))
            self.db.commit()
            self.attempts += 1
            self.units += cost
            return


class NullLimiter:
    """No pacing. Only for synthetic fixtures and tests that construct clients directly."""

    settings = PacingSettings()
    attempts = units = 0
    clock = staticmethod(time.time)

    def acquire(self, method: str) -> None:
        METHOD_COSTS[method]  # still rejects unknown methods

    def wait(self, seconds: float, reason: str) -> None:
        pass

    def close(self) -> None:
        pass

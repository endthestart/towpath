"""Background model work yields to everyone else using the model.

Background calls ask the gateway not to keep them waiting in line (a busy model answers at once), take at most a
quarter of any hour of the model, and after a busy or failed call every background task waits together, longer
after each failure in a row. The owner's own questions are never held back. State lives in the worker's memory,
per data folder and endpoint, so a restart starts afresh.
"""

import time

SHARE_SECONDS = 900  # a quarter of an hour
WINDOW_SECONDS = 3600
BACKOFF_SECONDS = (60, 300, 900, 3600)  # after one, two, three and more failures in a row
HEADERS = {"Poundlock-Priority": "background", "Poundlock-Wait": "0"}

_state: dict[tuple[str, str], dict] = {}


def _entry(store_dir, endpoint_id: str) -> dict:
    return _state.setdefault((str(store_dir), endpoint_id), {"calls": [], "failures": 0, "until": float("-inf")})


def wait(store_dir, endpoint_id: str, timeout: float, clock=time.monotonic) -> float:
    """Seconds before the next background call may start, 0 when it may start now.

    A call may start only if, with the time background calls ended in the last hour, even a call that runs to its
    timeout stays within the share: so no hour holds more than the share."""
    entry, now = _entry(store_dir, endpoint_id), clock()
    entry["calls"] = [(end, seconds) for end, seconds in entry["calls"] if end > now - WINDOW_SECONDS]
    used, share_wait = sum(seconds for _, seconds in entry["calls"]), 0.0
    for end, seconds in entry["calls"]:
        if used + timeout <= SHARE_SECONDS:
            break
        used -= seconds
        share_wait = end + WINDOW_SECONDS - now
    return max(0.0, entry["until"] - now, share_wait)


def record(store_dir, endpoint_id: str, started: float, failure: str | None, clock=time.monotonic) -> None:
    """A background call that has ended; a failure (busy, or no answer) makes every background task wait."""
    entry, now = _entry(store_dir, endpoint_id), clock()
    entry["calls"].append((now, now - started))
    if failure is None:
        entry["failures"], entry["until"] = 0, float("-inf")
    else:
        entry["failures"] += 1
        entry["until"] = now + BACKOFF_SECONDS[min(entry["failures"], len(BACKOFF_SECONDS)) - 1]

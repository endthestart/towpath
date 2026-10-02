"""Bounded, privacy-safe sync progress.

Snapshots contain counts, phase names, wait reasons, and measured rates only:
never subjects, addresses, message IDs, URLs, tokens, or content. Rates are
measurements over a recent window, reported with their spread; they are not
promises about Gmail's ordering or about completion time.
"""

import time

WINDOW_SECONDS = 600


class Progress:
    def __init__(self, source_id: str, callback=None, clock=time.time, every_items: int = 100,
                 every_seconds: float = 30.0):
        self.source_id = source_id
        self.callback = callback
        self.clock = clock
        self.every_items = every_items
        self.every_seconds = every_seconds
        self.started = clock()
        self.phase = "starting"
        self.processed = 0
        self.indexed_total = None
        self._times: list[float] = []
        self._last_emit = self.started
        self._last_count = 0

    def set_phase(self, phase: str) -> None:
        if phase != self.phase:
            self.phase = phase
            self._emit("phase")

    def item(self) -> None:
        now = self.clock()
        self.processed += 1
        self._times.append(now)
        self._times = [t for t in self._times if t > now - WINDOW_SECONDS]
        if (self.processed - self._last_count >= self.every_items
                or now - self._last_emit >= self.every_seconds):
            self._emit("progress")

    def wait(self, seconds: float, reason: str) -> None:
        self._emit("waiting", wait_seconds=round(seconds, 1), wait_reason=reason)

    def finish(self, termination: str, reason: str | None = None) -> None:
        self._emit("finished", termination=termination, reason=reason)

    def rate(self) -> dict:
        """Messages per minute over the recent window, with the spread across whole minutes."""
        now = self.clock()
        times = [t for t in self._times if t > now - WINDOW_SECONDS]
        if len(times) < 2:
            return {"per_minute": None, "low": None, "high": None, "minutes_measured": 0}
        span = max(now - times[0], 1.0)
        per_minute = len(times) * 60.0 / span
        minutes = int(span // 60)
        if minutes >= 3:
            buckets = [sum(1 for t in times if now - (m + 1) * 60 < t <= now - m * 60) for m in range(minutes)]
            low, high = min(buckets), max(buckets)
        else:
            low = high = None
        return {"per_minute": round(per_minute, 1), "low": low, "high": high, "minutes_measured": minutes}

    def _emit(self, event: str, **extra) -> None:
        self._last_emit = self.clock()
        self._last_count = self.processed
        if self.callback is None:
            return
        snapshot = {"event": event, "source": self.source_id, "phase": self.phase, "processed": self.processed,
                    "elapsed_seconds": round(self._last_emit - self.started, 1), "rate": self.rate(), **extra}
        self.callback(snapshot)


def describe(snapshot: dict) -> str:
    """One line for a terminal."""
    rate = snapshot["rate"]
    if rate["per_minute"] is None:
        rate_text = "rate not yet measured"
    elif rate["low"] is not None:
        rate_text = (f"measured {rate['per_minute']}/min over {rate['minutes_measured']} min "
                     f"(minutes ranged {rate['low']}-{rate['high']}; may change)")
    else:
        rate_text = f"measured {rate['per_minute']}/min over a short window (uncertain)"
    line = (f"[towpath] {snapshot['source']}: {snapshot['event']}, phase {snapshot['phase']}, "
            f"{snapshot['processed']} processed this run, {rate_text}")
    if snapshot["event"] == "waiting":
        line += f"; waiting {snapshot['wait_seconds']} s ({snapshot['wait_reason']})"
    if snapshot["event"] == "finished":
        line += f"; {snapshot['termination']}" + (f": {snapshot['reason']}" if snapshot.get("reason") else "")
    return line

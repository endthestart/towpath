"""A clock for the quota limiter's CLOCK and SLEEP, so pacing is real but waits take no time."""


class FakeClock:
    def __init__(self, now: float):
        self.now = now
        self.sleeps: list[float] = []
        self.cancel_on_sleep: int | None = None

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        if self.cancel_on_sleep is not None and len(self.sleeps) >= self.cancel_on_sleep:
            raise KeyboardInterrupt
        self.now += seconds

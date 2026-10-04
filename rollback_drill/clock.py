"""Clock abstraction.

Time is *always* supplied through a clock; the production runner never calls
``time.time()`` directly.  Tests inject :class:`FakeClock` to make deadlines,
drift windows and schedules deterministic.
"""

import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """Current wall-clock time, in epoch seconds."""

    def sleep(self, seconds: float) -> None:
        """Advance real (or simulated) time."""


class SystemClock:
    """Production clock backed by the standard library."""

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class FakeClock:
    """Deterministic clock injected by tests.

    ``now`` starts at ``start`` and is advanced explicitly.  ``charge`` lets a
    simulated slow I/O operation add elapsed time without doing the work.
    """

    def __init__(self, start: float = 1_000_000.0):
        self._now = float(start)

    def now(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("cannot rewind the clock")
        self._now += seconds

    def charge(self, seconds: float) -> None:
        self.advance(seconds)

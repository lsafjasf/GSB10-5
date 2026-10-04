"""Periodic drill scheduling.

The scheduler owns *when* drills run; the runner owns *how*.  Time comes from
the injected clock, so tests can simulate days passing in microseconds.
"""

from dataclasses import dataclass, field
from typing import Callable, Optional

from .clock import Clock, SystemClock


@dataclass
class ScheduleResult:
    due: bool
    run_id: Optional[str] = None
    reason: str = ""


class DrillScheduler:
    def __init__(self, interval_s: float, clock: Optional[Clock] = None,
                 jitter_s: float = 0.0):
        self.interval_s = interval_s
        self.clock = clock or SystemClock()
        self.jitter_s = jitter_s
        self._last_run: Optional[float] = None
        self.history: list[float] = []

    def due(self) -> bool:
        if self._last_run is None:
            return True
        return (self.clock.now() - self._last_run) >= self.interval_s

    def maybe_run(self, run_fn: Callable[[], str]) -> ScheduleResult:
        if not self.due():
            return ScheduleResult(due=False, reason="interval not reached")
        run_id = run_fn()
        self._last_run = self.clock.now()
        self.history.append(self._last_run)
        return ScheduleResult(due=True, run_id=run_id, reason="executed")

    def seconds_until_due(self) -> float:
        if self._last_run is None:
            return 0.0
        remaining = self.interval_s - (self.clock.now() - self._last_run)
        return max(0.0, remaining)

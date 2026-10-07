"""Bounded catch-up executor (discrete-event simulation over injected time).

Compensation work is drained under two hard bounds:

* rate limit   -- a token bucket allows at most ``rate_per_sec`` job *starts*
                  per virtual second (burst = one second's worth of tokens);
* concurrency  -- at most ``max_concurrency`` jobs in flight at any moment.

The executor is a deterministic event-driven simulation: all times come from
the plan and the injected ``duration`` function, never from the wall clock,
so tests are exact and reproducible.

Idempotency: before a job starts, its key is checked against the journal;
already-done keys are skipped without consuming a worker or a token. The
effect is applied at job start (keyed upsert) and the journal is marked at
job completion. With ``stop_at`` set (simulating a second downtime), jobs
still in flight are killed: their journal marks never happen, so the resume
planner owes them again, while their keyed effects make the retry a no-op.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, List, Optional

from .effects import EffectLog
from .journal import Journal
from .model import MissedFire

EPS = 1e-9


@dataclass
class JobTrace:
    key: str
    task_id: str
    fire_at: float
    start: float
    end: float
    status: str  # "done" | "skipped" | "aborted"


@dataclass
class RunResult:
    traces: List[JobTrace] = field(default_factory=list)
    done: int = 0
    skipped: int = 0
    aborted: int = 0
    end_time: float = 0.0
    peak_inflight: int = 0

    def by_status(self, status: str) -> List[JobTrace]:
        return [t for t in self.traces if t.status == status]


def run_compensation(
    plan_items: Iterable[MissedFire],
    journal: Journal,
    effects: EffectLog,
    rate_per_sec: float,
    max_concurrency: int,
    duration: Callable[[MissedFire], float] = lambda fire: 0.0,
    now: float = 0.0,
    stop_at: Optional[float] = None,
) -> RunResult:
    """Drain ``plan_items`` under the rate/concurrency bounds.

    ``stop_at`` simulates the lights going out again: nothing starts at or
    after it, and jobs still running are aborted (killed mid-flight).
    """
    if rate_per_sec <= 0 or max_concurrency <= 0:
        raise ValueError("rate_per_sec and max_concurrency must be positive")

    rate = float(rate_per_sec)
    items = iter(plan_items)
    exhausted = False
    result = RunResult()
    running: List = []  # heap of (end, seq, fire, start)
    seq = 0
    tokens = rate  # burst of one second
    # The simulation clock runs as an offset from ``now`` so that large epoch
    # values never degrade float precision; traces are stamped back in
    # absolute time when recorded.
    clock = 0.0
    last_tick = 0.0
    stop_offset = None if stop_at is None else float(stop_at) - float(now)

    def pull() -> Optional[MissedFire]:
        nonlocal exhausted
        if exhausted:
            return None
        for fire in items:
            if fire.key in journal:
                result.skipped += 1
                result.traces.append(
                    JobTrace(fire.key, fire.task_id, fire.fire_at,
                             now + clock, now + clock, "skipped")
                )
                continue
            return fire
        exhausted = True
        return None

    pending = pull()
    while True:
        # 1. Finish every job whose end has been reached.
        while running and running[0][0] <= clock + EPS:
            end, _, fire, started = heapq.heappop(running)
            journal.mark_done(fire.key)
            result.done += 1
            result.traces.append(
                JobTrace(fire.key, fire.task_id, fire.fire_at,
                         now + started, now + end, "done")
            )

        # 2. Refill the token bucket up to the current moment.
        if clock > last_tick:
            tokens = min(rate, tokens + (clock - last_tick) * rate)
            last_tick = clock
            if rate - tokens < EPS:
                tokens = rate

        # 3. Start as many jobs as the bounds allow right now.
        while (
            pending is not None
            and len(running) < max_concurrency
            and tokens >= 1.0 - EPS
            and (stop_offset is None or clock < stop_offset - EPS)
        ):
            tokens -= 1.0
            fire = pending
            effects.apply(fire.key, fire.task_id, int(fire.fire_at))
            dur = max(0.0, float(duration(fire)))
            heapq.heappush(running, (clock + dur, seq, fire, clock))
            seq += 1
            result.peak_inflight = max(result.peak_inflight, len(running))
            pending = pull()

        if pending is None and not running:
            break

        # 4. Jump to the next interesting moment.
        candidates = []
        if running:
            candidates.append(running[0][0])
        if pending is not None and tokens < 1.0 - EPS:
            candidates.append(clock + (1.0 - tokens) / rate)
        if stop_offset is not None and (not candidates or min(candidates) > stop_offset + EPS):
            clock = stop_offset  # lights out before anything else can happen
            while running:
                _, _, fire, started = heapq.heappop(running)
                result.aborted += 1
                result.traces.append(
                    JobTrace(fire.key, fire.task_id, fire.fire_at,
                             now + started, now + clock, "aborted")
                )
            break
        if not candidates:
            break
        clock = min(candidates)

    result.end_time = now + clock
    return result

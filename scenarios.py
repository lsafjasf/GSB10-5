"""Canonical scenarios shared by the self-tests and the demo report generator.

All times are injected: BASE is a fixed epoch and every downtime / stop time
is an offset from it, so runs are fully deterministic.
"""
from __future__ import annotations

from datetime import datetime, timezone

from catchup import Task

BASE = 1_772_928_000  # 2026-03-08T00:00:00Z (verified in selftest)
DAY = 86_400


def fmt(ts: float) -> str:
    """Unix seconds -> ISO8601 UTC string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# S1: no missed window -- downtime falls entirely between two fires.
S1_TASKS = [Task("s1-report", period=300, anchor=BASE)]
S1_DOWN = (BASE + 301, BASE + 599)

# S2: exactly one missed window -- downtime covers exactly one fire.
S2_TASKS = [Task("s2-sync", period=300, anchor=BASE)]
S2_DOWN = (BASE + 300, BASE + 600)

# S3: very long downtime (30 days).
S3_TASKS = [Task("s3-hourly", period=3600, anchor=BASE),
            Task("s3-daily", period=DAY, anchor=BASE)]
S3_DOWN = (BASE, BASE + 30 * DAY)

# S4: second downtime while compensating.
S4_TASKS = [Task("s4-a", period=300, anchor=BASE),
            Task("s4-b", period=600, anchor=BASE)]
S4_DOWN1 = (BASE + 900, BASE + 1900)
S4_STOP_AT = BASE + 1902          # lights out again, mid-compensation
S4_DOWN2 = (BASE + 1902, BASE + 2200)
S4_RATE1 = 2.0
S4_CONC = 4
S4_RATE2 = 10.0


def s4_duration(fire) -> float:
    return 5.0 if fire.task_id == "s4-b" else 0.5


# S5: idempotency replay + concurrency-bound load curve.
S5_TASKS = [Task(f"s5-t{i}", period=60, anchor=BASE) for i in range(3)]
S5_DOWN = (BASE, BASE + 1800)
S5_RATE = 5.0
S5_CONC = 3
S5_DURATION = 2.0

# Load curve (rate-bound): S3 plan drained at 10 starts/s.
CURVE_RATE = 10.0
CURVE_CONC = 8
CURVE_DURATION = 0.5

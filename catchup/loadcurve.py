"""Load-curve derivation from run traces.

Buckets traces per virtual second and reports, for each second of the
compensation window:

* started    -- jobs launched in that second (== rate-limited offered load);
* completed  -- jobs finished in that second;
* inflight   -- simultaneous jobs in flight at the bucket boundary (peak
                concurrency actually reached, never above the configured cap).
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from typing import Iterable, List

from .executor import JobTrace


@dataclass
class CurvePoint:
    second: int
    started: int
    completed: int
    inflight_peak: int


def build_curve(traces: Iterable[JobTrace], start_at: float = 0.0) -> List[CurvePoint]:
    traces = list(traces)
    if not traces:
        return []
    active = [t for t in traces if t.status in ("done", "aborted")]
    end_time = max((t.end for t in active), default=start_at)
    n_buckets = max(1, int(math.floor(end_time - start_at)) + 1)
    started = [0] * n_buckets
    completed = [0] * n_buckets
    events = []  # (sort_time, delta); ends are nudged so a job ending exactly
    # on a second boundary counts towards the earlier bucket, matching the
    # executor's "finish before start" rule at equal timestamps.

    for trace in traces:
        if trace.status == "skipped":
            continue
        s = int(math.floor(trace.start - start_at))
        if 0 <= s < n_buckets:
            started[s] += 1
        if trace.status == "done":
            e = int(math.floor(trace.end - start_at))
            if 0 <= e < n_buckets:
                completed[e] += 1
        if trace.status in ("done", "aborted"):
            events.append((trace.start - start_at, 1))
            nudge = 1e-9 if trace.end > trace.start else 0.0
            events.append((trace.end - start_at - nudge, -1))

    events.sort(key=lambda ev: (ev[0], -ev[1]))  # starts before ends at ties
    curve: List[CurvePoint] = []
    inflight = 0
    ei = 0
    for sec in range(n_buckets):
        peak = inflight  # carried into this second
        while ei < len(events) and events[ei][0] < sec + 1:
            inflight += events[ei][1]
            ei += 1
            if inflight > peak:
                peak = inflight
        curve.append(CurvePoint(sec, started[sec], completed[sec], max(0, peak)))
    return curve


def write_curve_csv(path: str, curve: List[CurvePoint]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["second", "started", "completed", "inflight_peak"])
        for point in curve:
            writer.writerow([point.second, point.started, point.completed, point.inflight_peak])

"""Missed-window arithmetic.

A downtime is the half-open interval [down_at, up_at): the scheduler is dead
from ``down_at`` (inclusive) until ``up_at`` (exclusive). A fire time t is
missed iff down_at <= t < up_at. Fires at exactly ``up_at`` are served live
by the recovered scheduler and are NOT compensated.
"""
from __future__ import annotations

from typing import Iterator

from .model import MissedFire, Task


def ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def missed_count(task: Task, down_at: int, up_at: int) -> int:
    """Number of fire times of ``task`` inside [down_at, up_at). O(1)."""
    if up_at <= down_at:
        return 0
    first = ceil_div(down_at - task.anchor, task.period)
    last = ceil_div(up_at - task.anchor, task.period)  # first k with fire >= up_at
    return max(0, last - first)


def missed_fires(task: Task, down_at: int, up_at: int) -> Iterator[MissedFire]:
    """Yield the missed fires of one task, oldest first. Lazy (generator)."""
    first = ceil_div(down_at - task.anchor, task.period)
    last = ceil_div(up_at - task.anchor, task.period)
    for k in range(first, last):
        yield MissedFire(fire_at=task.anchor + k * task.period, task_id=task.task_id)

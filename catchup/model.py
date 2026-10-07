"""Core data model for downtime catch-up compensation.

All times are plain numbers (Unix seconds) supplied by the caller, so tests
inject time explicitly; the library never reads the wall clock.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Task:
    """A periodic task anchored at ``anchor`` firing every ``period`` seconds.

    Fire times are anchor, anchor+period, anchor+2*period, ...
    """
    task_id: str
    period: int
    anchor: int = 0

    def __post_init__(self) -> None:
        if self.period <= 0:
            raise ValueError("period must be positive")


@dataclass(frozen=True, order=True)
class MissedFire:
    """One owed execution: task ``task_id`` that should have fired at ``fire_at``.

    ``key`` is the idempotency / dedup identity of the compensation unit.
    """
    fire_at: float
    task_id: str

    @property
    def key(self) -> str:
        return f"{self.task_id}@{int(self.fire_at)}"

"""Compensation-plan construction (initial build and resume after re-down).

Plans are stored as one iterator per task ("streams") of :class:`MissedFire`,
never as one giant list. ``iter_plan`` merges the streams lazily with a heap,
so even a 100k-task / ~27M-fire backlog costs O(number_of_tasks) memory.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, List, Optional, Sequence, Tuple

from .model import MissedFire, Task
from .window import missed_count, missed_fires


@dataclass
class TaskBacklog:
    """Per-task compensation summary: how many fires and at which times (lazily)."""
    task: Task
    down_at: int
    up_at: int

    @property
    def count(self) -> int:
        return missed_count(self.task, self.down_at, self.up_at)

    def fires(self) -> Iterator[MissedFire]:
        return missed_fires(self.task, self.down_at, self.up_at)


@dataclass
class CompensationPlan:
    backlogs: List[TaskBacklog] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(b.count for b in self.backlogs)

    def streams(self) -> List[Iterator[MissedFire]]:
        return [b.fires() for b in self.backlogs if b.count]

    def iter_plan(self) -> Iterator[MissedFire]:
        """All owed fires in global (fire_at, task_id) order.

        One heap entry per *task* (not per fire): each pop yields one fire and
        advances that task's arithmetic stream with a single ``heapreplace``.
        Memory stays O(num_tasks) even when a task missed millions of fires.
        """
        heap: List[tuple] = []
        for idx, backlog in enumerate(self.backlogs):
            k = _first_k(backlog.task, backlog.down_at)
            fire_at = backlog.task.anchor + k * backlog.task.period
            heapq.heappush(heap, (fire_at, backlog.task.task_id, idx, k))
        while heap:
            fire_at, task_id, idx, k = heap[0]
            yield MissedFire(fire_at=fire_at, task_id=task_id)
            backlog = self.backlogs[idx]
            next_at = fire_at + backlog.task.period
            if next_at < backlog.up_at:
                heapq.heapreplace(heap, (next_at, task_id, idx, k + 1))
            else:
                heapq.heappop(heap)

    def per_task(self) -> List[Tuple[str, int, int, int]]:
        """(task_id, count, first_fire_at, last_fire_at) per task."""
        rows = []
        for b in self.backlogs:
            if b.count:
                first_k = _first_k(b.task, b.down_at)
                first_at = b.task.anchor + first_k * b.task.period
                last_at = b.task.anchor + (first_k + b.count - 1) * b.task.period
                rows.append((b.task.task_id, b.count, int(first_at), int(last_at)))
        return rows


def _first_k(task: Task, down_at: int) -> int:
    return -(-(down_at - task.anchor) // task.period)


def build_initial(tasks: Iterable[Task], down_at: int, up_at: int) -> CompensationPlan:
    """Identify the missed window of one downtime and build the catch-up plan.

    O(num_tasks) work and memory regardless of how many fires each task missed.
    """
    plan = CompensationPlan()
    for task in tasks:
        if missed_count(task, down_at, up_at):
            plan.backlogs.append(TaskBacklog(task, down_at, up_at))
    return plan


def iter_resume(
    tasks: Iterable[Task],
    down1: int,
    up1: int,
    down2: int,
    up2: int,
    is_done: Callable[[str], bool],
) -> Iterator[MissedFire]:
    """Plan after a second downtime during compensation.

    Owed set = (fires missed in downtime 1 not already finished, journal keys
    via ``is_done``) UNION (fires missed in downtime 2). The union is a key
    union, so an overlap window is never double-counted. Emitted in global
    time order via lazy heap merge; memory stays O(num_tasks).
    """
    tasks = list(tasks)
    streams: List[Iterator[MissedFire]] = []

    def remaining(down: int, up: int) -> Iterator[MissedFire]:
        for task in tasks:
            for fire in missed_fires(task, down, up):
                if not is_done(fire.key):
                    yield fire

    streams.append(remaining(down1, down2))
    streams.append(remaining(down2, up2))
    return heapq.merge(*streams)


def build_resume_summary(
    tasks: Sequence[Task],
    down1: int,
    up1: int,
    down2: int,
    up2: int,
    is_done: Callable[[str], bool],
) -> List[Tuple[str, int]]:
    """(task_id, owed_count_after_resume) counts for reporting."""
    rows: dict = {}
    for down, up in ((down1, down2), (down2, up2)):
        for task in tasks:
            for fire in missed_fires(task, down, up):
                if not is_done(fire.key):
                    rows[task.task_id] = rows.get(task.task_id, 0) + 1
    return sorted(rows.items())

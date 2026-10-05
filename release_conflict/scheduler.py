"""排期：为每个发布单找"最早可安排时间"。

规则（详见 RULES.md）：
- 按 (期望开始时间, 发布单 id) 升序依次安排；
- 每个发布单安排在不早于其期望开始时间、且所有资源容量都满足的最早时刻；
- 已排期的发布单占用其资源，后续发布单需要避开。

正确性依据：对某个发布单，任一资源上"其他发布单占用量之和"
只会在已有区间的开始/结束时刻变化，且开始时刻只会上升、
结束时刻只会下降；因此若存在最早可行开始时刻 t，则 t 要么是
期望开始时间，要么恰好是某个已排期区间的结束时刻。只需检查这些候选点。
"""

from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .model import Release, Resource, ScheduledRelease


def schedule(
    releases: Sequence[Release],
    resources: Optional[Mapping[str, Resource]] = None,
) -> List[ScheduledRelease]:
    """返回每个发布单的最早可安排窗口，顺序与输入一致。"""
    resources = dict(resources) if resources is not None else {}

    order = sorted(releases, key=lambda r: (r.start, r.id))
    intervals: Dict[str, List[Tuple[int, int, int]]] = {}
    result: Dict[str, ScheduledRelease] = {}

    for rel in order:
        duration = rel.duration
        t = _earliest_start(rel, duration, intervals, resources)
        for rid, amount in rel.resource_demands.items():
            intervals.setdefault(rid, []).append((t, t + duration, amount))
        result[rel.id] = ScheduledRelease(release=rel, start=t, end=t + duration)

    return [result[rel.id] for rel in releases]


def _earliest_start(
    rel: Release,
    duration: int,
    intervals: Dict[str, List[Tuple[int, int, int]]],
    resources: Mapping[str, Resource],
) -> int:
    candidates = {rel.start}
    for rid in rel.resource_demands:
        for s, e, _ in intervals.get(rid, ()):  # 结束时刻才可能让出容量
            if e >= rel.start:
                candidates.add(e)

    for t in sorted(candidates):
        if _feasible(rel, t, duration, intervals, resources):
            return t
    raise AssertionError("候选点中必然存在可行解（如所有区间结束之后）")


def _feasible(
    rel: Release,
    start: int,
    duration: int,
    intervals: Dict[str, List[Tuple[int, int, int]]],
    resources: Mapping[str, Resource],
) -> bool:
    end = start + duration
    for rid, amount in rel.resource_demands.items():
        capacity = resources[rid].capacity if rid in resources else 1
        if amount > capacity:
            return False
        if _peak_others(intervals.get(rid, ()), start, end) + amount > capacity:
            return False
    return True


def _peak_others(
    intervals: Sequence[Tuple[int, int, int]], start: int, end: int
) -> int:
    """[start, end) 内其他发布单占用量之和的峰值（扫描线）。"""
    events: List[Tuple[int, int]] = []
    for s, e, amount in intervals:
        if s < end and e > start:
            events.append((max(s, start), amount))
            events.append((min(e, end), -amount))
    events.sort(key=lambda ev: (ev[0], ev[1]))  # 同一时刻先降后升
    peak = 0
    cur = 0
    for _, delta in events:
        cur += delta
        if cur > peak:
            peak = cur
    return peak

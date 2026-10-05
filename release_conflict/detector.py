"""冲突检测：扫描线 + 按资源提取"最小冲突集"。

判定依据（详见 RULES.md）：
把每个发布单看成区间 [start, end) 上对资源的占用。
在资源 r 上，令 A_r(t) = {所有在时刻 t 占用 r 的发布单}。
当且仅当某一时刻存在子集 S ⊆ A_r(t) 满足
    sum(需求量) > capacity(r)
且 S 极小（去掉任意一个发布单就不再超载）时，报告 S 为一条冲突。

推论：
- 时间重叠但资源集合不相交 -> 永不冲突；
- 资源相交但占用时段不相交（含端点相接，区间半开）-> 不冲突；
- 独占资源 capacity=1：任意两个同时占用即冲突（两两冲突）；
- 共享资源 capacity>=2：可出现 a-b 不冲突、b-c 不冲突、a-c 不冲突，
  但 {a,b,c} 同时占用时冲突的"传递性冲突"（三元最小冲突集）。

每个最小冲突集只在其"最后一个发布单开始占用"的时刻首次形成，
故按开始时刻分组扫描可保证无重复、无遗漏。
"""

from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .model import Conflict, Release, Resource


def detect_conflicts(
    releases: Sequence[Release],
    resources: Optional[Mapping[str, Resource]] = None,
    exhaustive_capacity_threshold: int = 8,
) -> List[Conflict]:
    """检测全部最小冲突集。

    resources 缺省时，出现的资源一律按独占资源（capacity=1）处理。
    exhaustive_capacity_threshold：容量较小时对超载事件穷举全部最小冲突集
    （精确结果）；容量很大时退化为"每次超载至少报一个最小冲突集"，
    防止极端数据下组合爆炸。演示与自测使用小容量，结果是精确完备的。
    """
    declared = resources is not None
    resources = _normalize_resources(resources)

    by_resource: Dict[str, List[Release]] = {}
    for rel in releases:
        for rid in rel.resource_demands:
            if declared and rid not in resources:
                raise ValueError(f"发布单 {rel.id} 使用了未声明的资源 {rid}")
            by_resource.setdefault(rid, []).append(rel)

    conflicts: List[Conflict] = []
    for rid, rels in by_resource.items():
        cap = resources[rid].capacity if rid in resources else 1
        normal: List[Release] = []
        for rel in rels:
            demand = rel.resource_demands[rid]
            if demand > cap:
                # 单独占用量就超容量：任何时刻都与自己冲突，直接报告。
                conflicts.append(
                    Conflict(
                        resource=rid,
                        releases=(rel.id,),
                        overlap=(rel.start, rel.end),
                        demand=demand,
                        capacity=cap,
                    )
                )
            else:
                normal.append(rel)
        if cap == 1:
            conflicts.extend(_sweep_exclusive(rid, normal))
        else:
            conflicts.extend(
                _sweep_capacity(rid, normal, cap, exhaustive_capacity_threshold)
            )

    conflicts.sort(key=lambda c: (c.overlap[0], c.resource, c.releases))
    return conflicts


def _normalize_resources(
    resources: Optional[Mapping[str, Resource]]
) -> Dict[str, Resource]:
    if resources is None:
        return {}
    out = dict(resources)
    return out


def _sweep_exclusive(resource_id: str, rels: Sequence[Release]) -> List[Conflict]:
    """独占资源（capacity=1）：任意同时占用即两两冲突。"""
    events: List[Tuple[int, int, Release]] = []
    for rel in rels:
        events.append((rel.start, 1, rel))  # 1 = 开始
        events.append((rel.end, 0, rel))  # 0 = 结束，端点相接不算重叠
    events.sort(key=lambda e: (e[0], e[1]))

    conflicts: List[Conflict] = []
    active: Dict[str, Release] = {}
    seen: Set[Tuple[str, ...]] = set()
    for t, kind, rel in events:
        if kind == 0:
            active.pop(rel.id, None)
            continue
        for other in active.values():
            lo = max(rel.start, other.start)
            hi = min(rel.end, other.end)
            if lo < hi:
                key = tuple(sorted((rel.id, other.id)))
                if key in seen:
                    continue
                seen.add(key)
                conflicts.append(
                    Conflict(
                        resource=resource_id,
                        releases=key,
                        overlap=(lo, hi),
                        demand=2,
                        capacity=1,
                    )
                )
        active[rel.id] = rel
    return conflicts


def _sweep_capacity(
    resource_id: str,
    rels: Sequence[Release],
    capacity: int,
    exhaustive_threshold: int,
) -> List[Conflict]:
    """共享资源：按开始时刻分组扫描，提取最小超载子集。"""
    rels = sorted(rels, key=lambda r: (r.start, r.id))

    starts: Dict[int, List[Release]] = {}
    for rel in rels:
        starts.setdefault(rel.start, []).append(rel)

    conflicts: List[Conflict] = []
    seen: Set[Tuple[str, ...]] = set()
    active: Dict[str, Release] = {}

    for t in sorted(starts):
        for rel in list(active.values()):
            if rel.end <= t:
                del active[rel.id]

        incoming = starts[t]
        for rel in incoming:
            active[rel.id] = rel

        for subset in _overloaded_subsets(
            resource_id, incoming, active, capacity, exhaustive_threshold
        ):
            ids = tuple(sorted(r.id for r in subset))
            if ids in seen:
                continue
            seen.add(ids)
            lo = max(r.start for r in subset)
            hi = min(r.end for r in subset)
            demand = sum(r.resource_demands[resource_id] for r in subset)
            conflicts.append(
                Conflict(
                    resource=resource_id,
                    releases=ids,
                    overlap=(lo, hi),
                    demand=demand,
                    capacity=capacity,
                )
            )
    return conflicts


def _overloaded_subsets(
    resource_id: str,
    incoming: Sequence[Release],
    active: Mapping[str, Release],
    capacity: int,
    exhaustive_threshold: int,
) -> Iterable[List[Release]]:
    """在当前活跃集合上枚举最小超载子集，必须含至少一个新开始的发布单。"""
    others = sorted(
        (rel for rid, rel in active.items() if rid not in {r.id for r in incoming}),
        key=lambda r: r.id,
    )
    incoming_sorted = sorted(incoming, key=lambda r: r.id)

    if capacity <= exhaustive_threshold:
        yielded: Set[Tuple[str, ...]] = set()
        for seed in incoming_sorted:
            candidates = others + [r for r in incoming_sorted if r.id != seed.id]
            candidates.sort(key=lambda r: r.id)
            for subset in _minimal_subsets(resource_id, seed, candidates, capacity):
                key = tuple(r.id for r in subset)
                if key not in yielded:
                    yielded.add(key)
                    yield subset
    else:
        # 大容量退化策略：每次新开始若引发超载，贪心找一个最小超载子集。
        pool = sorted(others + incoming_sorted, key=lambda r: r.id)
        for seed in incoming_sorted:
            found = _greedy_minimal(resource_id, seed, pool, capacity)
            if found is not None:
                yield found


def _demand_of(resource_id: str, rel: Release) -> int:
    return rel.resource_demands[resource_id]


def _minimal_subsets(
    resource_id: str,
    seed: Release,
    candidates: Sequence[Release],
    capacity: int,
) -> Iterable[List[Release]]:
    """枚举包含 seed 的全部最小超载子集（候选集合按序扩展）。

    剪枝：
    - 当前需求 + 剩余全部需求仍不超载 -> 该分支无解；
    - 找到超载集合后做最小性校验（去掉任意一个都不超载才是最小冲突集）。
    """
    seed_demand = _demand_of(resource_id, seed)
    dem = [_demand_of(resource_id, r) for r in candidates]
    n = len(candidates)

    suffix = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix[i] = suffix[i + 1] + dem[i]

    picked: List[int] = []

    def dfs(pos: int, total: int) -> Iterable[List[Release]]:
        if total > capacity:
            chosen = [seed] + [candidates[i] for i in picked]
            if _is_minimal(resource_id, chosen, capacity):
                yield chosen
            return
        if total + suffix[pos] <= capacity:
            return
        for i in range(pos, n):
            picked.append(i)
            yield from dfs(i + 1, total + dem[i])
            picked.pop()

    yield from dfs(0, seed_demand)


def _is_minimal(resource_id: str, subset: Sequence[Release], capacity: int) -> bool:
    total = sum(_demand_of(resource_id, r) for r in subset)
    if total <= capacity:
        return False
    for i, rel in enumerate(subset):
        without = total - _demand_of(resource_id, rel)
        if without > capacity:
            return False
    return True


def _greedy_minimal(
    resource_id: str,
    seed: Release,
    pool: Sequence[Release],
    capacity: int,
) -> Optional[List[Release]]:
    """大容量场景：贪心取需求量大的活跃发布单凑成超载，再逐个剔除至最小。"""
    total = _demand_of(resource_id, seed)
    chosen = [seed]
    for rel in sorted(pool, key=lambda r: -_demand_of(resource_id, r)):
        if rel.id == seed.id:
            continue
        if total > capacity:
            break
        chosen.append(rel)
        total += _demand_of(resource_id, rel)
    if total <= capacity:
        return None
    # 剔除后仍超载的成员，得到极小集。
    minimal = list(chosen)
    for rel in list(minimal):
        trial = [r for r in minimal if r.id != rel.id]
        if sum(_demand_of(resource_id, r) for r in trial) > capacity:
            minimal = trial
    return minimal

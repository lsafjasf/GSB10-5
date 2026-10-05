"""发布窗口冲突检测库（仅标准库）。

判定规则（见 README.md 的完整论证）：
    两个发布单冲突 <=> 时间窗口有交集 且 占用资源集合有交集。
    - 时间不重叠：同一资源可被先后串行复用，不冲突。
    - 资源不重叠：各自动各的资源，互不干扰，不冲突。
    - 两者同时成立：同一资源被两个发布同时占用，写入互相覆盖，
      撞上只能互相回滚，判定冲突。

时间窗口为半开区间 [start, end)，单位为分钟（相对任意零点）。
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Release:
    """一个发布单。"""

    rid: str
    start: int  # 窗口起点（分钟，含）
    end: int    # 窗口终点（分钟，不含），要求 end > start
    resources: frozenset  # 本发布要占用的资源集合

    def __post_init__(self):
        if self.end <= self.start:
            raise ValueError(f"{self.rid}: end 必须大于 start")
        object.__setattr__(self, "resources", frozenset(self.resources))

    @property
    def duration(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class Conflict:
    """一对冲突：两个发布单 + 它们争抢的共享资源。"""

    rid_a: str
    rid_b: str
    shared_resources: tuple  # 排序后的共享资源，定位到具体资源


def _overlaps(a: Release, b: Release) -> bool:
    return a.start < b.end and b.start < a.end


def detect_conflicts(releases):
    """扫描线算法检测全部两两冲突。

    朴素两两比较是 O(n^2)。这里按 start 排序做扫描线，
    用"资源 -> 活跃发布"倒排索引把候选对限制在
    "时间重叠且共享资源"的发布之间，复杂度约为
    O(n log n + 总资源引用数 + 冲突数)。

    返回 List[Conflict]，按 (rid_a, rid_b) 排序，可复现。
    """
    order = sorted(range(len(releases)),
                   key=lambda i: (releases[i].start, releases[i].rid))
    active_heap = []          # (end, idx)，用于过期清理
    res_index = {}            # resource -> set(活跃 idx)
    conflicts = []

    for i in order:
        cur = releases[i]
        # 过期清理：end <= cur.start 的发布与 cur 不可能时间重叠
        while active_heap and active_heap[0][0] <= cur.start:
            _, j = heapq.heappop(active_heap)
            for res in releases[j].resources:
                bucket = res_index.get(res)
                if bucket is not None:
                    bucket.discard(j)
                    if not bucket:
                        del res_index[res]
        # 候选 = 与 cur 共享资源的活跃发布（时间重叠已由扫描线保证）
        candidates = set()
        for res in cur.resources:
            bucket = res_index.get(res)
            if bucket:
                candidates |= bucket
        for j in candidates:
            other = releases[j]
            shared = tuple(sorted(cur.resources & other.resources))
            a, b = sorted((cur.rid, other.rid))
            conflicts.append(Conflict(a, b, shared))
        # cur 进入活跃集合
        heapq.heappush(active_heap, (cur.end, i))
        for res in cur.resources:
            res_index.setdefault(res, set()).add(i)

    conflicts.sort(key=lambda c: (c.rid_a, c.rid_b))
    return conflicts


class _UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        parent = self.parent
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:  # 路径压缩
            parent[x], x = root, parent[x]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra

    def add(self, x):
        if x not in self.parent:
            self.parent[x] = x


def conflict_groups(releases, conflicts):
    """传递性冲突分组（冲突图的连通分量）。

    冲突关系本身不具备传递性：甲乙冲突、乙丙冲突，甲丙可能并不
    直接冲突。但回滚会沿冲突边级联（乙回滚影响甲和丙，等于甲丙
    也被牵连），因此排期与回滚影响面必须以连通分量整体看待。

    返回 List[frozenset(rid)]，只含大小 >= 2 的组，按组内最小 rid 排序。
    """
    uf = _UnionFind()
    for r in releases:
        uf.add(r.rid)
    for c in conflicts:
        uf.union(c.rid_a, c.rid_b)
    groups = {}
    for rid in uf.parent:
        groups.setdefault(uf.find(rid), set()).add(rid)
    result = [frozenset(g) for g in groups.values() if len(g) >= 2]
    result.sort(key=lambda g: min(g))
    return result


def schedule_earliest(releases):
    """贪心排期：给出每个发布单可行的最早开始时间。

    资源是互斥单位：同一资源任意时刻只允许一个发布占用。
    按期望开始时间升序处理，每个发布的最早开始时间为
        max(期望开始, 其每个资源的最早空闲时刻)
    然后把窗口平移到该时刻（时长不变），并更新资源空闲时刻。
    不冲突的发布保持原窗口不动。

    返回 dict: rid -> (scheduled_start, scheduled_end)。
    """
    order = sorted(releases, key=lambda r: (r.start, r.rid))
    res_free_at = {}          # resource -> 该资源下次空闲时刻
    plan = {}
    for r in order:
        est = r.start
        for res in r.resources:
            free = res_free_at.get(res)
            if free is not None and free > est:
                est = free
        scheduled_end = est + r.duration
        plan[r.rid] = (est, scheduled_end)
        for res in r.resources:
            res_free_at[res] = scheduled_end
    return plan


# ---------------------------------------------------------------- 演示与基准

BASE = datetime(2026, 10, 6, 0, 0)  # 仅用于把分钟格式化成可读时间


def fmt(minutes: int) -> str:
    return (BASE + timedelta(minutes=minutes)).strftime("%m-%d %H:%M")


def demo_releases():
    """覆盖四种典型情形的小样例（单位：分钟，相对 2026-10-06 00:00）。"""
    h = 60
    return [
        # 甲-乙：时间重叠 + 共享资源 db-order -> 直接冲突
        Release("甲", 9 * h, 11 * h, frozenset({"db-order", "svc-pay"})),
        Release("乙", 10 * h, 12 * h, frozenset({"db-order", "cache-user"})),
        # 乙-丙：时间重叠 + 共享资源 cache-user -> 直接冲突
        # 甲-丙：资源不相交 -> 不直接冲突，但经乙传递成组 {甲,乙,丙}
        Release("丙", 11 * h + 30, 13 * h, frozenset({"cache-user"})),
        # 丁：与甲时间重叠但资源不相交 -> 不冲突（纯时间重叠不算冲突）
        Release("丁", 9 * h + 30, 10 * h + 30, frozenset({"svc-search"})),
        # 戊：与乙资源相同但时间不重叠 -> 不冲突（串行复用同一资源）
        Release("戊", 14 * h, 15 * h, frozenset({"db-order"})),
        # 己：完全不相干
        Release("己", 20 * h, 21 * h, frozenset({"svc-report"})),
    ]


def run_demo():
    releases = demo_releases()
    conflicts = detect_conflicts(releases)
    groups = conflict_groups(releases, conflicts)
    plan = schedule_earliest(releases)

    print("== 冲突清单（定位到共享资源与两个发布单）==")
    if not conflicts:
        print("  （无冲突）")
    for c in conflicts:
        print(f"  {c.rid_a} x {c.rid_b}  共享资源: {', '.join(c.shared_resources)}")

    print("\n== 传递性冲突组（连通分量，回滚影响面）==")
    if not groups:
        print("  （无）")
    for g in groups:
        members = "、".join(sorted(g))
        print(f"  {{{members}}}")

    print("\n== 可排期方案（每个发布的最早可安排时间）==")
    by_rid = {r.rid: r for r in releases}
    for rid in sorted(plan, key=lambda x: (plan[x][0], x)):
        s, e = plan[rid]
        want = by_rid[rid]
        note = "" if s == want.start else f"  （期望 {fmt(want.start)}，因资源互斥顺延）"
        print(f"  {rid}: {fmt(s)} ~ {fmt(e)}{note}")


def generate_releases(n, seed=42):
    """确定性生成 n 个发布单，用于基准测试。"""
    import random

    rng = random.Random(seed)
    pool = [f"res-{k}" for k in range(2000)]
    horizon = 60 * 24 * 28  # 4 周，单位分钟
    releases = []
    for i in range(n):
        start = rng.randrange(horizon)
        duration = rng.randrange(30, 240)
        k = rng.randrange(1, 6)
        releases.append(Release(f"R{i:06d}", start, start + duration,
                                frozenset(rng.sample(pool, k))))
    return releases


def run_bench(n=100_000):
    import resource
    import time
    import tracemalloc

    releases = generate_releases(n)
    print(f"发布单数量: {n}")

    tracemalloc.start()
    t0 = time.perf_counter()
    conflicts = detect_conflicts(releases)
    t1 = time.perf_counter()
    groups = conflict_groups(releases, conflicts)
    t2 = time.perf_counter()
    plan = schedule_earliest(releases)
    t3 = time.perf_counter()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"两两冲突数: {len(conflicts)}")
    print(f"传递性冲突组数: {len(groups)}")
    print(f"排期条目数: {len(plan)}")
    print(f"耗时: 检测 {t1 - t0:.3f}s | 分组 {t2 - t1:.3f}s | "
          f"排期 {t3 - t2:.3f}s | 合计 {t3 - t0:.3f}s")
    print(f"内存峰值(tracemalloc): {peak / 1024 / 1024:.1f} MiB")
    print(f"进程峰值 RSS: {rss_kb / 1024:.1f} MiB")


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "demo"
    if cmd == "demo":
        run_demo()
    elif cmd == "bench":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 100_000
        run_bench(n)
    else:
        print("用法: python3 release_window.py [demo|bench [N]]")
        sys.exit(2)

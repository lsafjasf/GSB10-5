"""故障注入影响评估（确定性模型）。

推导模型（与 README.md 的推导说明对应）：

1. 故障传播（服务可用性）
   - 直接被注入故障域的服务标记为 down。
   - 服务的每个依赖组需要 min_healthy 个健康副本；健康副本数不足时，
     该组失效。失效组的流量占比之和 >= 1 时，上游服务整体 down；
     占比之和 < 1 时服务存活但按比例损失请求（部分降级）。
   - 通过“每个组存活副本计数”事件驱动传播，复杂度 O(V + E)。

2. 请求损失比例（流量比例推导）
   - 对未 down 的服务，沿依赖图求一条请求最终撞上 down 节点的概率。
   - 单副本/失效组：损失 = 1。
   - 健康冗余组（存活数 >= min_healthy）：流量在存活副本间均衡，
     组损失 = 存活副本损失的均值；演练评估假设故障切换完美
     （fail_over_fail=0），与实际演练的切换失败率构成偏差来源。
   - 服务损失 = Σ 组流量占比 × 组损失。
   - 受影响用户比例 = Σ 入口权重 × 入口服务损失。

3. 关键路径：入口出发、终止于第一个失效点（失效组指向 down 节点的边）
   的请求路径；中间边可以是健康/降级组。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from .topology import DependencyGroup, Service, Topology

EPS = 1e-12
PATH_CAP = 100_000


@dataclass
class GroupState:
    group: DependencyGroup
    owner: str
    alive: int
    down_providers: list[str]
    failed: bool
    degraded: bool


@dataclass
class AssessmentResult:
    faulted: set[str]
    down: set[str]
    impacted: set[str]
    service_loss: dict[str, float]
    affected_user_fraction: float
    critical_path_count: int
    critical_paths_truncated: bool
    group_states: dict[str, GroupState]
    defeated_redundancy: list[dict]
    single_point_failures: list[str]
    protected_redundancy: list[str]
    knock_group: dict[str, str]

    @property
    def affected_services(self) -> int:
        return len(self.impacted)

    @property
    def total_services(self) -> int:
        return len(self.service_loss)

    def summary(self) -> dict:
        return {
            "services_total": self.total_services,
            "services_faulted_directly": len(self.faulted),
            "services_down": len(self.down),
            "services_impacted": len(self.impacted),
            "affected_user_fraction": round(self.affected_user_fraction, 6),
            "critical_path_count": self.critical_path_count,
            "critical_paths_truncated": self.critical_paths_truncated,
            "redundancy_defeated_groups": len(self.defeated_redundancy),
            "redundancy_protected_groups": len(self.protected_redundancy),
            "single_point_failure_groups": len(self.single_point_failures),
        }

    def detail(self) -> dict:
        return {
            "faulted_directly": sorted(self.faulted),
            "down": sorted(self.down),
            "impacted": sorted(self.impacted),
            "service_loss": {k: round(v, 6) for k, v in
                             sorted(self.service_loss.items()) if v > EPS},
            "defeated_redundancy": self.defeated_redundancy,
            "protected_redundancy": self.protected_redundancy,
            "single_point_failures": self.single_point_failures,
        }


def _build_reverse(topo: Topology) -> dict[str, list[tuple[str, DependencyGroup]]]:
    reverse: dict[str, list[tuple[str, DependencyGroup]]] = {s: [] for s in topo.services}
    for owner, svc in topo.services.items():
        for grp in svc.deps:
            for pid in grp.providers:
                reverse[pid].append((owner, grp))
    return reverse


def _propagate(topo: Topology, faulted: set[str]):
    """事件驱动的故障传播，返回 (down, states, knock_group)。"""
    states: dict[str, GroupState] = {}
    alive: dict[str, int] = {}
    for owner, svc in topo.services.items():
        for grp in svc.deps:
            down_in = [p for p in grp.providers if p in faulted]
            st = GroupState(
                group=grp, owner=owner,
                alive=len(grp.providers) - len(down_in),
                down_providers=down_in,
                failed=False, degraded=False,
            )
            states[grp.gid] = st
            alive[grp.gid] = st.alive

    down = set(faulted)
    reverse = _build_reverse(topo)
    knock_group: dict[str, str] = {}
    failed_share: dict[str, float] = {s: 0.0 for s in topo.services}

    def mark_failed(st: GroupState) -> None:
        st.failed = True
        st.degraded = False
        failed_share[st.owner] += st.group.traffic_share
        owner = st.owner
        if failed_share[owner] >= 1.0 - EPS and owner not in down:
            down.add(owner)
            knock_group[owner] = st.group.gid
            queue.append(owner)

    queue: list[str] = []
    for st in states.values():
        if st.alive < st.group.min_healthy:
            mark_failed(st)
        elif st.alive < len(st.group.providers):
            st.degraded = True

    queue.extend(sorted(faulted))
    head = 0
    while head < len(queue):
        pid = queue[head]
        head += 1
        for owner, grp in reverse[pid]:
            st = states[grp.gid]
            if pid not in st.down_providers:
                st.down_providers.append(pid)
                st.alive -= 1
            if st.failed:
                continue
            if st.alive < grp.min_healthy:
                mark_failed(st)
            elif st.alive < len(grp.providers):
                st.degraded = True
    return down, states, knock_group


def _evaluation_order(topo: Topology) -> tuple[list[str], set[str]]:
    """provider -> owner 方向的 Kahn 拓扑序；返回 (有序节点, 环上节点)。"""
    reverse = _build_reverse(topo)
    indeg: dict[str, int] = {}
    for sid, svc in topo.services.items():
        indeg[sid] = sum(len(g.providers) for g in svc.deps)
    queue = [s for s, d in indeg.items() if d == 0]
    order: list[str] = []
    head = 0
    while head < len(queue):
        node = queue[head]
        head += 1
        order.append(node)
        for owner, _grp in reverse[node]:
            indeg[owner] -= 1
            if indeg[owner] == 0:
                queue.append(owner)
    cyclic = set(topo.services) - set(order)
    return order, cyclic


def _group_loss(grp: DependencyGroup, st: GroupState,
                loss: dict[str, float]) -> float:
    if st.failed:
        return 1.0
    survivors = [p for p in grp.providers if p not in st.down_providers]
    return sum(loss[p] for p in survivors) / len(survivors)


def assess(topo: Topology, injected_domains: set[str]) -> AssessmentResult:
    if sys.getrecursionlimit() < len(topo.services) + 100:
        sys.setrecursionlimit(len(topo.services) + 100)
    faulted = {sid for sid, svc in topo.services.items()
               if svc.fail_domain in injected_domains}
    down, states, knock_group = _propagate(topo, faulted)

    order, cyclic = _evaluation_order(topo)
    loss: dict[str, float] = {s: (1.0 if s in down else 0.0)
                              for s in topo.services}

    def recompute(sid: str) -> float:
        svc = topo.services[sid]
        total = 0.0
        for grp in svc.deps:
            total += grp.traffic_share * _group_loss(grp, states[grp.gid], loss)
        return min(1.0, total)

    for sid in order:
        if sid not in down and topo.services[sid].deps:
            loss[sid] = recompute(sid)

    for _ in range(64):
        delta = 0.0
        for sid in cyclic:
            if sid in down:
                continue
            new_val = recompute(sid)
            delta = max(delta, abs(new_val - loss[sid]))
            loss[sid] = new_val
        if delta < 1e-9:
            break

    user_fraction = sum(
        svc.entry_weight * loss[sid]
        for sid, svc in topo.services.items() if svc.entry
    )
    impacted = {sid for sid, v in loss.items() if v > EPS} | faulted

    critical_count, truncated = _count_critical_paths(topo, states, down)
    defeated, protected, single_points = _classify_groups(
        topo, states, faulted, knock_group)

    return AssessmentResult(
        faulted=faulted, down=down, impacted=impacted,
        service_loss=loss, affected_user_fraction=user_fraction,
        critical_path_count=critical_count,
        critical_paths_truncated=truncated,
        group_states=states, defeated_redundancy=defeated,
        single_point_failures=single_points,
        protected_redundancy=protected, knock_group=knock_group,
    )


def _count_critical_paths(topo: Topology, states, down: set[str]):
    """统计入口出发、终止于第一个失效点（失效组指向 down 节点的边）的路径数。

    用记忆化 DP 计数：f(node) 为 node 到失效点的路径数，
    DAG 上复杂度 O(V + E)；含环时回边按 0 处理（断环），环上计数是
    保守下界。总数达到 PATH_CAP 后截断并置标记。
    """
    memo: dict[str, int] = {}
    visiting: set[str] = set()
    truncated = False

    def count_from(node: str) -> int:
        nonlocal truncated
        if node in memo:
            return memo[node]
        if node in visiting:
            return 0
        visiting.add(node)
        total = 0
        for grp in topo.services[node].deps:
            st = states[grp.gid]
            for p in grp.providers:
                if st.failed and p in down:
                    total += 1
                elif p not in down:
                    total += count_from(p)
                if total >= PATH_CAP:
                    total = PATH_CAP
                    truncated = True
                    break
            if truncated:
                break
        visiting.discard(node)
        memo[node] = total
        return total

    total = 0
    for entry in topo.entry_services:
        total += count_from(entry)
        if total >= PATH_CAP:
            total = PATH_CAP
            truncated = True
            break
    return total, truncated


def _classify_groups(topo: Topology, states, faulted: set[str], knock_group):
    defeated: list[dict] = []
    protected: list[str] = []
    single_points: list[str] = []

    for owner, svc in topo.services.items():
        for grp in svc.deps:
            st = states[grp.gid]
            replicas = len(grp.providers)
            if replicas == 1 and st.failed:
                single_points.append(grp.gid)
            if replicas >= 2 and st.degraded and not st.failed:
                protected.append(grp.gid)
            if replicas >= 2 and st.failed and len(st.down_providers) >= 2:
                defeated.append({
                    "gid": grp.gid,
                    "owner": owner,
                    "providers": list(grp.providers),
                    "down_providers": sorted(st.down_providers),
                    "min_healthy": grp.min_healthy,
                    "cause": _common_cause(grp, st, topo, faulted, knock_group),
                })
    return defeated, protected, single_points


def _common_cause(grp: DependencyGroup, st: GroupState,
                  topo: Topology, faulted: set[str], knock_group) -> str:
    domains = {topo.services[p].fail_domain for p in st.down_providers}
    if len(domains) == 1 and next(iter(domains)) is not None:
        domain = next(iter(domains))
        return f"冗余副本共属故障域 {domain}，被同一刀同时打挂"
    cascaded = [p for p in st.down_providers if p not in faulted]
    if cascaded:
        causes = {knock_group.get(p) for p in cascaded}
        if len(causes) == 1 and next(iter(causes)):
            return f"级联汇聚：共同依赖组 {next(iter(causes))} 失效"
    return "多个副本在同一演练窗口内分别失效"

"""
影响面推导引擎

推导规则（README 中有完整说明与依据）：

1. 每个服务有健康度 health ∈ [0,1]，1=完全正常，0=完全不可用。
   被注入故障的服务 health = 0。
2. 强依赖（hard）：调用方损失 = ratio × (1 - health(被调方))。
   弱依赖（soft）：调用方损失 = min(ratio, degraded_penalty) × (1 - health(被调方))。
3. 冗余组：成员按容量份额归一化。失败份额 failed = Σ(健康度≈0 的成员份额)，
   退化份额 degraded = Σ(部分受损成员份额 × 受损程度)。
   failed ≥ fail_threshold → 整组失效（组健康度 0）；
   否则组健康度 = 1 - degraded（存活成员接管失败成员的流量）。
   调用方把"组"当作一个依赖按规则 2 计算损失。
4. 服务健康度 = 所有依赖/冗余组留存率的乘积（各依赖损失近似独立）。
5. 受影响用户比例 = Σ(入口权重 × (1-health)) / Σ(入口权重)。
   依据：入口权重即该入口承载的用户流量份额，健康度即请求成功概率。

无环图按逆拓扑序一次传播即可（O(V+E)）；有环时在剩余环上迭代收敛（≤50 轮）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Topology
from .paths import count_critical_paths

EPS = 1e-9
DOWN_EPS = 1e-6          # health < 该值视为"不可用"
DEGRADED_EPS = 1e-3      # health < 1-该值 视为"受影响（含降级）"


@dataclass
class BrokenGroup:
    group_id: str
    caller: str
    failed_share: float
    members_down: list[str]
    correlated: bool                # 多个成员同时被故障注入命中
    shared_zone: str | None         # 若成员同处一个故障域，给出该域


@dataclass
class ImpactReport:
    faults: list[str]
    health: dict[str, float]
    down: list[str]                 # 完全不可用服务
    degraded: list[str]             # 受影响（含不可用与降级）
    affected_user_ratio: float      # 受影响用户比例 0~1
    affected_entries: list[str]     # 受影响的入口服务
    critical_path_count: int        # 关键路径数量（饱和封顶见 paths.CAP）
    critical_path_capped: bool
    critical_path_log10: float      # 数量级估计（路径数爆炸时仍有参考价值）
    critical_path_exact: bool       # False 表示图有环，按强连通分量宏路径计数
    broken_groups: list[BrokenGroup] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "faults": self.faults,
            "affected_user_ratio": round(self.affected_user_ratio, 6),
            "services_down": len(self.down),
            "services_affected": len(self.degraded),
            "down": sorted(self.down),
            "degraded": sorted(self.degraded),
            "affected_entries": sorted(self.affected_entries),
            "critical_path_count": self.critical_path_count,
            "critical_path_capped": self.critical_path_capped,
            "critical_path_log10": round(self.critical_path_log10, 2),
            "critical_path_exact": self.critical_path_exact,
            "broken_groups": [
                {
                    "group_id": g.group_id,
                    "caller": g.caller,
                    "failed_share": round(g.failed_share, 4),
                    "members_down": sorted(g.members_down),
                    "correlated": g.correlated,
                    "shared_zone": g.shared_zone,
                }
                for g in self.broken_groups
            ],
        }


def _dep_factor(kind: str, ratio: float, penalty: float, target_health: float) -> float:
    loss = 1.0 - target_health
    if loss <= EPS:
        return 1.0
    if kind == "hard":
        return 1.0 - ratio * loss
    return 1.0 - min(ratio, penalty) * loss


def estimate(topo: Topology, faults: list[str] | set[str]) -> ImpactReport:
    faults = list(dict.fromkeys(faults))
    for f in faults:
        if f not in topo.services:
            raise ValueError(f"故障注入目标不存在: {f}")
    fault_set = set(faults)

    # ---- 1. 健康度传播 ----
    health = {sid: (0.0 if sid in fault_set else 1.0) for sid in topo.services}

    # Kahn 逆拓扑序：被调方先算，调用方后算；剩下的节点即在环上
    indeg = {sid: 0 for sid in topo.services}
    callers_of: dict[str, list[str]] = {sid: [] for sid in topo.services}
    for sid, sv in topo.services.items():
        for d in sv.deps:
            indeg[sid] += 1
            callers_of[d.target].append(sid)
        for g in sv.groups:
            for m in g.members:
                indeg[sid] += 1
                callers_of[m.service].append(sid)
    # 注意：indeg 这里是"出度"语义（依赖数），从叶子（无依赖）开始传播
    order: list[str] = []
    stack = [sid for sid, dg in indeg.items() if dg == 0]
    remaining = dict(indeg)
    while stack:
        node = stack.pop()
        order.append(node)
        for caller in callers_of[node]:
            remaining[caller] -= 1
            if remaining[caller] == 0:
                stack.append(caller)
    cyclic = [sid for sid in topo.services if remaining[sid] > 0]

    def sweep(nodes) -> bool:
        """按当前 health 重算一批节点，返回是否有变化。"""
        changed = False
        for sid in nodes:
            if sid in fault_set:
                continue
            sv = topo.services[sid]
            h = 1.0
            for d in sv.deps:
                h *= _dep_factor(d.kind, d.ratio, d.degraded_penalty, health[d.target])
            for g in sv.groups:
                fracs = g.normalized_fractions()
                failed = sum(f for m, f in fracs.items() if health[m] < DOWN_EPS)
                degraded = sum(
                    f * (1.0 - health[m]) for m, f in fracs.items()
                    if DOWN_EPS <= health[m] < 1.0 - DOWN_EPS
                )
                g_health = 0.0 if failed >= g.fail_threshold - EPS else 1.0 - degraded
                h *= _dep_factor(g.kind, g.ratio, g.degraded_penalty, g_health)
            if abs(h - health[sid]) > 1e-12:
                health[sid] = h
                changed = True
        return changed

    sweep(order)
    for _ in range(50):
        if not cyclic or not sweep(cyclic):
            break

    # ---- 2. 受影响集合与用户比例 ----
    down = sorted(s for s, h in health.items() if h < DOWN_EPS)
    degraded = sorted(s for s, h in health.items() if h < 1.0 - DEGRADED_EPS)
    total_entry = topo.total_entry_weight()
    affected_user_ratio = 0.0
    affected_entries = []
    if total_entry > 0:
        loss = sum(
            sv.entry_weight * (1.0 - health[sid])
            for sid, sv in topo.services.items() if sv.entry_weight > 0
        )
        affected_user_ratio = loss / total_entry
    affected_entries = sorted(
        sid for sid, sv in topo.services.items()
        if sv.entry_weight > 0 and health[sid] < 1.0 - DEGRADED_EPS
    )

    # ---- 3. 冗余组失效识别（含"冗余被同时影响"场景）----
    broken_groups: list[BrokenGroup] = []
    for sid, sv in topo.services.items():
        for g in sv.groups:
            fracs = g.normalized_fractions()
            members_down = [m for m in fracs if health[m] < DOWN_EPS]
            failed = sum(fracs[m] for m in members_down)
            if failed >= g.fail_threshold - EPS:
                injected = [m for m in members_down if m in fault_set]
                zones = {topo.services[m].zone for m in members_down}
                shared_zone = zones.pop() if len(zones) == 1 else None
                broken_groups.append(BrokenGroup(
                    group_id=g.id,
                    caller=sid,
                    failed_share=failed,
                    members_down=sorted(members_down),
                    correlated=len(injected) >= 2,
                    shared_zone=shared_zone,
                ))

    # ---- 4. 关键路径：受影响入口 -> 故障点 的活跃传播路径 ----
    # 活跃边 (调用方 -> 被调方)：被调方受损且调用方也受损（影响真的沿这条边传导了）
    active_adj: dict[str, list[str]] = {sid: [] for sid in topo.services}
    for sid, sv in topo.services.items():
        if health[sid] >= 1.0 - DEGRADED_EPS:
            continue
        for d in sv.deps:
            if health[d.target] < 1.0 - DEGRADED_EPS:
                active_adj[sid].append(d.target)
        for g in sv.groups:
            for m in g.members:
                if health[m.service] < 1.0 - DEGRADED_EPS:
                    active_adj[sid].append(m.service)
    path_count, capped, log10_paths, exact = count_critical_paths(
        active_adj, affected_entries, fault_set)

    return ImpactReport(
        faults=faults,
        health=health,
        down=down,
        degraded=degraded,
        affected_user_ratio=affected_user_ratio,
        affected_entries=affected_entries,
        critical_path_count=path_count,
        critical_path_capped=capped,
        critical_path_log10=log10_paths,
        critical_path_exact=exact,
        broken_groups=broken_groups,
    )

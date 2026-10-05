"""
估算结果 vs 实际演练结果的对比

输出三类偏差，并对用户比例偏差做来源归因：

1. 用户比例偏差（百分点 pp）= 实测 - 估算。
2. 服务影响面偏差：以"受影响服务集合"计算 precision/recall/F1。
3. 关键路径数量偏差（实测可选填）。

归因方法（基于反事实可达性的启发式，README 中有说明）：
- 实测受损但模型没算到的入口：
  · 若它能（沿调用链）反向触达同故障域内被连带打挂的冗余成员，
    归为"共故障域连带 / 冗余同时失效"；
  · 其余归为"依赖图漂移 / 流量比例失真 / 观测噪声"。
- 模型预测受损但实测没事的入口：
  · 若其链路上有模型判定整组失效的冗余组，归为"切流/降级预案实际生效"；
  · 其余归为"流量比例或弱依赖系数偏高"。
每个来源按入口权重折算成 pp，各项之和等于总偏差（残差闭合成 100%）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .engine import ImpactReport, DEGRADED_EPS
from .model import Topology


@dataclass
class DrillResult:
    affected_services: set[str]           # 演练中实际受影响的服务（入口/内部都填）
    user_ratio: float                     # 实测受影响用户比例 0~1
    entry_health: dict[str, float] = field(default_factory=dict)
    critical_path_count: int | None = None
    fault_domain: str | None = None       # 演练实际炸掉的故障域（如 az-a）


@dataclass
class AttributionItem:
    source: str
    share_pp: float
    entries: list[str]


@dataclass
class Comparison:
    predicted_user_ratio: float
    observed_user_ratio: float
    gap_pp: float                         # 实测 - 估算，正=低估，负=高估
    precision: float
    recall: float
    f1: float
    predicted_only: list[str]             # 估算有、实测无（高估）
    observed_only: list[str]              # 实测有、估算无（低估）
    predicted_paths: int
    observed_paths: int | None
    path_gap_pct: float | None
    attribution: list[AttributionItem]

    def to_dict(self) -> dict:
        return {
            "predicted_user_ratio": round(self.predicted_user_ratio, 6),
            "observed_user_ratio": round(self.observed_user_ratio, 6),
            "gap_pp": round(self.gap_pp, 3),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "predicted_only": sorted(self.predicted_only),
            "observed_only": sorted(self.observed_only),
            "predicted_paths": self.predicted_paths,
            "observed_paths": self.observed_paths,
            "path_gap_pct": (
                None if self.observed_paths in (None, 0)
                else round(self.path_gap_pct, 1)),
            "attribution": [
                {"source": a.source,
                 "share_pp": round(a.share_pp, 3),
                 "entries": sorted(a.entries)}
                for a in self.attribution
            ],
        }


def _reverse_callers(topo: Topology) -> dict[str, set[str]]:
    """被调方 -> 所有（传递）调用方集合，用于判断某个入口能否触达某条链路。"""
    direct: dict[str, set[str]] = {sid: set() for sid in topo.services}
    for sid, sv in topo.services.items():
        for d in sv.deps:
            direct[d.target].add(sid)
        for g in sv.groups:
            for m in g.members:
                direct[m.service].add(sid)
    return direct


def _can_reach(direct_callers: dict[str, set[str]], sources: set[str]) -> set[str]:
    """返回 sources 沿 '被调方->调用方' 方向（逆调用链）能到达的全部节点。"""
    reached = set(sources)
    stack = list(sources)
    while stack:
        node = stack.pop()
        for caller in direct_callers.get(node, ()):
            if caller not in reached:
                reached.add(caller)
                stack.append(caller)
    return reached


def compare(topo: Topology, report: ImpactReport, drill: DrillResult) -> Comparison:
    predicted = set(report.degraded)
    observed = set(drill.affected_services)
    inter = predicted & observed
    precision = len(inter) / len(predicted) if predicted else 1.0
    recall = len(inter) / len(observed) if observed else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 1.0

    total_entry = topo.total_entry_weight()
    direct_callers = _reverse_callers(topo)

    # 故障域连带候选：实测受损但模型未预测、且同处实际故障域的服务
    same_domain_unpredicted = {
        sid for sid in observed - predicted
        if drill.fault_domain and topo.services[sid].zone == drill.fault_domain
    }
    domain_reachable = _can_reach(direct_callers, same_domain_unpredicted)

    # 模型判整组失效的成员 -> 其所有调用方（预案生效可救回的链路）
    broken_members = {
        m for g in report.broken_groups for m in g.members_down
    }
    broken_reachable = _can_reach(direct_callers, broken_members)

    buckets = {
        "共故障域连带/冗余同时失效（估算遗漏）": [],
        "依赖图漂移/流量比例失真/观测噪声（估算遗漏）": [],
        "切流/降级预案实际生效（估算高估）": [],
        "流量比例或弱依赖系数偏高（估算高估）": [],
    }

    for sid, sv in topo.services.items():
        if sv.entry_weight <= 0:
            continue
        obs_health = drill.entry_health.get(
            sid, 0.0 if sid in observed else 1.0)
        pred_loss = 1.0 - report.health.get(sid, 1.0)
        obs_loss = 1.0 - obs_health
        d = obs_loss - pred_loss
        if abs(d) <= DEGRADED_EPS or total_entry == 0:
            continue
        pp = d * sv.entry_weight / total_entry * 100.0
        if pp > 0:
            key = ("共故障域连带/冗余同时失效（估算遗漏）"
                   if sid in domain_reachable or sv.zone == drill.fault_domain
                   else "依赖图漂移/流量比例失真/观测噪声（估算遗漏）")
        else:
            key = ("切流/降级预案实际生效（估算高估）"
                   if sid in broken_reachable
                   else "流量比例或弱依赖系数偏高（估算高估）")
        buckets[key].append((sid, pp))

    attribution = [
        AttributionItem(
            source=key,
            share_pp=round(sum(pp for _, pp in items), 3),
            entries=[e for e, _ in items],
        )
        for key, items in buckets.items() if items
    ]

    path_gap = None
    if drill.critical_path_count:
        path_gap = ((report.critical_path_count - drill.critical_path_count)
                    / drill.critical_path_count * 100.0)

    return Comparison(
        predicted_user_ratio=report.affected_user_ratio,
        observed_user_ratio=drill.user_ratio,
        gap_pp=(drill.user_ratio - report.affected_user_ratio) * 100.0,
        precision=precision,
        recall=recall,
        f1=f1,
        predicted_only=sorted(predicted - observed),
        observed_only=sorted(observed - predicted),
        predicted_paths=report.critical_path_count,
        observed_paths=drill.critical_path_count,
        path_gap_pct=path_gap,
        attribution=attribution,
    )

"""
故障演练影响评估 - 数据模型

只依赖 Python 标准库。模型里的每一个数值都可以从线上观测数据得到：

- service.entry_weight   : 入口流量权重（QPS / UV / 网关采样，口径自定，按比例归一）
- dep.ratio              : 调用占比（调用方分给该被调方的流量份额，0~1）
- dep.kind               : hard=同步强依赖（挂了直接损失对应比例流量）
                           soft=异步弱依赖（挂了只损失 degraded_penalty 比例）
- dep.degraded_penalty   : 弱依赖全挂时调用方的健康损失，默认 0.05
- group.member.fraction  : 该副本承担的容量份额（等权时 1/N，也可按容量填）
- group.fail_threshold   : 失败容量份额达到多少视为整组失效（1.0=全部副本挂才整组挂）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class Dependency:
    target: str                 # 被调服务
    ratio: float = 1.0          # 占调用方流量的比例
    kind: str = "hard"          # hard / soft
    degraded_penalty: float = 0.05

    def __post_init__(self) -> None:
        if not (0.0 <= self.ratio <= 1.0):
            raise ValueError(f"依赖流量比例超出 [0,1]: {self.ratio}")
        if self.kind not in ("hard", "soft"):
            raise ValueError(f"依赖类型只能是 hard/soft: {self.kind}")


@dataclass(frozen=True)
class Member:
    service: str
    fraction: float = 1.0       # 未归一化也行，构建时会按组归一化


@dataclass
class RedundancyGroup:
    """
    冗余关系：一组互为副本的服务（主备/多活/同构分片的同一逻辑依赖）。
    调用方依赖的是"组"而不是组内单个成员，单成员挂掉由其余成员承担。
    """
    id: str
    members: list[Member]
    ratio: float = 1.0                      # 这组依赖占调用方流量的比例
    kind: str = "hard"
    degraded_penalty: float = 0.05
    fail_threshold: float = 1.0             # 累计失败容量份额 >= 该值才整组挂

    def normalized_fractions(self) -> dict[str, float]:
        total = sum(m.fraction for m in self.members) or 1.0
        return {m.service: m.fraction / total for m in self.members}


@dataclass
class Service:
    id: str
    entry_weight: float = 0.0               # 0 表示纯内部服务
    zone: str | None = None                 # 故障域（机房/AZ/机架）
    deps: list[Dependency] = field(default_factory=list)
    groups: list[RedundancyGroup] = field(default_factory=list)


@dataclass
class Topology:
    services: dict[str, Service]

    # ---------- 构建辅助 ----------
    @classmethod
    def from_dict(cls, raw: dict) -> "Topology":
        services: dict[str, Service] = {}
        for sid, sv in raw.get("services", {}).items():
            sv = sv or {}
            services[sid] = Service(
                id=sid,
                entry_weight=float(sv.get("entry_weight", 0.0)),
                zone=sv.get("zone"),
                deps=[
                    Dependency(
                        target=d["target"],
                        ratio=float(d.get("ratio", 1.0)),
                        kind=d.get("kind", "hard"),
                        degraded_penalty=float(d.get("degraded_penalty", 0.05)),
                    )
                    for d in sv.get("deps", [])
                ],
            )
        # 冗余组单独解析（声明在调用方 services[*].groups 上）
        for sid, sv in raw.get("services", {}).items():
            for g in (sv or {}).get("groups", []):
                members = [
                    Member(service=m["service"],
                           fraction=float(m.get("fraction", 1.0)))
                    for m in g["members"]
                ]
                services[sid].groups.append(RedundancyGroup(
                    id=g["id"],
                    members=members,
                    ratio=float(g.get("ratio", 1.0)),
                    kind=g.get("kind", "hard"),
                    degraded_penalty=float(g.get("degraded_penalty", 0.05)),
                    fail_threshold=float(g.get("fail_threshold", 1.0)),
                ))
        topo = cls(services)
        topo.validate()
        return topo

    def validate(self) -> None:
        for sid, sv in self.services.items():
            dep_sum = sum(d.ratio for d in sv.deps)
            grp_sum = sum(g.ratio for g in sv.groups)
            if dep_sum + grp_sum > 1.0000001:
                raise ValueError(
                    f"服务 {sid} 的依赖流量比例之和超过 1: {dep_sum + grp_sum:.3f}")
            for d in sv.deps:
                if d.target not in self.services:
                    raise ValueError(f"服务 {sid} 依赖了不存在的服务 {d.target}")
            for g in sv.groups:
                for m in g.members:
                    if m.service not in self.services:
                        raise ValueError(
                            f"服务 {sid} 的冗余组 {g.id} 含不存在的成员 {m.service}")

    # ---------- 图视图 ----------
    def edges(self) -> Iterable[tuple[str, Dependency]]:
        for sid, sv in self.services.items():
            for d in sv.deps:
                yield sid, d

    def total_entry_weight(self) -> float:
        return sum(sv.entry_weight for sv in self.services.values())

    def group_index(self) -> dict[str, list[tuple[str, RedundancyGroup]]]:
        """成员服务 -> 所有 (调用方, 组)，用于按故障域反查整组影响。"""
        index: dict[str, list[tuple[str, RedundancyGroup]]] = {}
        for sid, sv in self.services.items():
            for g in sv.groups:
                for m in g.members:
                    index.setdefault(m.service, []).append((sid, g))
        return index

    def clone(self) -> "Topology":
        import copy
        return copy.deepcopy(self)

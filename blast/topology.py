"""服务依赖拓扑模型、标准场景与规模化数据生成。

模型约定（推导依据见 README.md）：

- Service：服务节点。entry=True 的服务是用户流量入口，entry_weight 表示
  该入口承接的用户请求比例；非入口服务 entry_weight 必须为 0。
- DependencyGroup：某个上游服务对一个“逻辑依赖”的调用组。组内 provider
  是冗余副本（对等实例/对等集群），traffic_share 是该依赖占上游全部下游
  调用的流量权重（非入口服务必须有上游，由它承接上游流量）。
- 组状态：healthy 表示需要 min_healthy 个健康副本，否则该组失效；
  degraded 表示存活副本数 >= min_healthy 但未全部存活，流量可完全切走，
  演练评估中不视为失效（冗余生效），只做记录。
- fail_domain：故障域标签（如 host/az/zone）。注入故障即按标签打挂节点。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class DependencyGroup:
    gid: str
    providers: tuple[str, ...]
    traffic_share: float = 1.0
    min_healthy: int = 1
    fail_over_fail: float = 0.0

    def __post_init__(self) -> None:
        if not self.providers:
            raise ValueError(f"group {self.gid}: providers 不能为空")
        if self.min_healthy < 1 or self.min_healthy > len(self.providers):
            raise ValueError(f"group {self.gid}: min_healthy 越界")
        if not 0.0 <= self.fail_over_fail < 1.0:
            raise ValueError(f"group {self.gid}: fail_over_fail 越界")
        self.providers = tuple(self.providers)


@dataclass
class Service:
    sid: str
    entry: bool = False
    entry_weight: float = 0.0
    fail_domain: str | None = None
    deps: list[DependencyGroup] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.entry:
            if self.entry_weight <= 0:
                raise ValueError(f"service {self.sid}: 入口权重必须为正")
        elif self.entry_weight:
            raise ValueError(f"service {self.sid}: 非入口不能有入口权重")


class Topology:
    def __init__(self, services: list[Service]):
        self.services: dict[str, Service] = {}
        for svc in services:
            if svc.sid in self.services:
                raise ValueError(f"重复的服务 id: {svc.sid}")
            self.services[svc.sid] = svc
        for svc in services:
            for grp in svc.deps:
                for pid in grp.providers:
                    if pid not in self.services:
                        raise ValueError(
                            f"service {svc.sid} 依赖不存在的 provider {pid}"
                        )
        if not any(s.entry for s in services):
            raise ValueError("拓扑至少需要一个入口服务")
        total = sum(s.entry_weight for s in services)
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"入口权重之和必须为 1.0，当前为 {total}")
        self.entry_services = tuple(
            s.sid for s in sorted(services, key=lambda x: x.sid) if s.entry
        )

    def add_group(
        self,
        owner: str,
        providers: list[str],
        traffic_share: float = 1.0,
        min_healthy: int = 1,
        fail_over_fail: float = 0.0,
    ) -> str:
        for pid in providers:
            if pid not in self.services:
                raise ValueError(f"service {owner} 依赖不存在的 provider {pid}")
        idx = len(self.services[owner].deps)
        gid = f"{owner}->g{idx}"
        self.services[owner].deps.append(
            DependencyGroup(gid, tuple(providers), traffic_share,
                            min_healthy, fail_over_fail)
        )
        return gid

    def provider_count(self) -> int:
        return sum(len(g.providers) for s in self.services.values() for g in s.deps)


# ---------------------------------------------------------------------------
# 标准演练场景：无影响 / 单点 / 级联 / 冗余同时失效 / 冗余生效
# ---------------------------------------------------------------------------

def scenario_no_impact() -> tuple[Topology, set[str]]:
    topo = Topology([
        Service("gateway", entry=True, entry_weight=1.0),
        Service("svc_a"),
        Service("svc_b", fail_domain="host-x"),
        Service("offline"),
    ])
    topo.add_group("gateway", ["svc_a"], traffic_share=0.8)
    topo.add_group("gateway", ["svc_b"], traffic_share=0.2)
    topo.add_group("svc_a", ["svc_b"])
    return topo, {"offline"}


def scenario_single_point() -> tuple[Topology, set[str]]:
    topo = Topology([
        Service("gateway", entry=True, entry_weight=1.0),
        Service("svc_a", fail_domain="host-a"),
        Service("svc_b"),
    ])
    topo.add_group("gateway", ["svc_a"], traffic_share=0.7)
    topo.add_group("gateway", ["svc_b"], traffic_share=0.3)
    topo.add_group("svc_a", ["svc_b"])
    return topo, {"host-a"}


def scenario_cascade() -> tuple[Topology, set[str]]:
    topo = Topology([
        Service("gateway", entry=True, entry_weight=0.6),
        Service("web", entry=True, entry_weight=0.4),
        Service("svc_a"),
        Service("svc_b"),
        Service("svc_c", fail_domain="host-c"),
    ])
    topo.add_group("gateway", ["svc_a"])
    topo.add_group("web", ["svc_b"], traffic_share=0.5)
    topo.add_group("svc_a", ["svc_b"])
    topo.add_group("svc_b", ["svc_c"])
    return topo, {"host-c"}


def scenario_redundancy_holds() -> tuple[Topology, set[str]]:
    topo = Topology([
        Service("gateway", entry=True, entry_weight=1.0),
        Service("svc_primary", fail_domain="host-a"),
        Service("svc_replica", fail_domain="host-b"),
        Service("backend"),
    ])
    topo.add_group("gateway", ["svc_primary", "svc_replica"], min_healthy=1)
    topo.add_group("svc_primary", ["backend"])
    topo.add_group("svc_replica", ["backend"])
    return topo, {"host-a"}


def scenario_redundancy_defeated() -> tuple[Topology, set[str]]:
    topo = Topology([
        Service("gateway", entry=True, entry_weight=1.0),
        Service("svc_primary", fail_domain="az-1"),
        Service("svc_replica", fail_domain="az-1"),
        Service("backend", fail_domain="az-1"),
    ])
    topo.add_group("gateway", ["svc_primary", "svc_replica"], min_healthy=1)
    topo.add_group("svc_primary", ["backend"])
    topo.add_group("svc_replica", ["backend"])
    return topo, {"az-1"}


def scenario_mixed() -> tuple[Topology, set[str]]:
    """两个入口 + 单副本依赖 + 跨 AZ 冗余 + 公共依赖。"""
    topo = Topology([
        Service("gw1", entry=True, entry_weight=0.7),
        Service("gw2", entry=True, entry_weight=0.3),
        Service("a1", fail_domain="host-a1"),
        Service("a2", fail_domain="host-a2"),
        Service("b", fail_domain="host-b"),
        Service("cache1", fail_domain="az-1"),
        Service("cache2", fail_domain="az-2"),
        Service("db"),
    ])
    topo.add_group("gw1", ["a1", "a2"], min_healthy=1,
                    traffic_share=0.9, fail_over_fail=0.2)
    topo.add_group("gw1", ["b"], traffic_share=0.1)
    topo.add_group("gw2", ["b"])
    topo.add_group("a1", ["cache1", "cache2"], min_healthy=1)
    topo.add_group("a2", ["cache1", "cache2"], min_healthy=1)
    topo.add_group("b", ["db"])
    return topo, {"host-a1", "host-b"}


SCENARIOS = {
    "no_impact": scenario_no_impact,
    "single_point": scenario_single_point,
    "cascade": scenario_cascade,
    "redundancy_holds": scenario_redundancy_holds,
    "redundancy_defeated": scenario_redundancy_defeated,
    "mixed": scenario_mixed,
}


# ---------------------------------------------------------------------------
# 规模化随机拓扑：默认 1 万服务、10 万条 provider 依赖边
# ---------------------------------------------------------------------------

def generate_large_topology(
    n_services: int = 10_000,
    n_edges: int = 100_000,
    n_entries: int = 50,
    redundant_ratio: float = 0.15,
    n_fail_domains: int = 2_000,
    seed: int = 42,
) -> Topology:
    rng = random.Random(seed)
    ids = [f"s{i:05d}" for i in range(n_services)]
    entry_ids = set(ids[:n_entries])
    weights = [rng.random() for _ in range(n_entries)]
    wsum = sum(weights)

    services: list[Service] = []
    for i, sid in enumerate(ids):
        if sid in entry_ids:
            services.append(
                Service(sid, entry=True,
                        entry_weight=weights[i] / wsum,
                        fail_domain=f"fd{rng.randrange(n_fail_domains)}")
            )
        else:
            services.append(
                Service(sid, fail_domain=f"fd{rng.randrange(n_fail_domains)}")
            )
    topo = Topology(services)

    n_redundant = int(n_edges * redundant_ratio)
    n_single = n_edges - n_redundant - n_redundant // 2
    edge_budget = n_single + (n_redundant // 2) * 3

    def downstream_of(idx: int) -> int:
        return idx + 1 + int((rng.random() ** 2) * (n_services - idx - 1))

    remaining = n_edges
    for idx, sid in enumerate(ids[:-1]):
        if remaining <= 0:
            break
        if rng.random() < 0.85:
            use_redundant = n_redundant > 0 and remaining >= 3 and rng.random() < 0.2
            if use_redundant:
                j1 = min(downstream_of(idx), n_services - 1)
                j2 = min(j1 + 1, n_services - 1)
                topo.add_group(sid, [ids[j1], ids[j2]], min_healthy=1)
                n_redundant -= 2
                remaining -= 2
            else:
                j = downstream_of(idx)
                topo.add_group(sid, [ids[j]])
                remaining -= 1

    for sid, grps in topo.services.items() if False else []:
        pass
    for svc in list(topo.services.values()):
        if svc.deps:
            total = len(svc.deps)
            for k, grp in enumerate(svc.deps):
                grp.traffic_share = 1.0 if total == 1 else 1.0 / total

    assert topo.provider_count() >= n_edges * 0.9
    return topo
# ---------------------------------------------------------------------------
# 规模化随机拓扑：默认 1 万服务、10 万条 provider 依赖边
# ---------------------------------------------------------------------------

def generate_large_topology(
    n_services: int = 10_000,
    n_edges: int = 100_000,
    n_entries: int = 50,
    redundant_ratio: float = 0.2,
    n_fail_domains: int = 2_000,
    seed: int = 42,
) -> Topology:
    """生成边数（provider membership）近似 n_edges 的随机有向无环拓扑。

    边方向：小编号 -> 大编号（调用方 -> 被调方），天然无环。
    约 redundant_ratio 比例的边以双副本冗余组形式出现（一个组贡献 2 条边）。
    """
    rng = random.Random(seed)
    ids = [f"s{i:05d}" for i in range(n_services)]
    raw_weights = [rng.random() for _ in range(n_entries)]
    wsum = sum(raw_weights)

    services: list[Service] = []
    for i, sid in enumerate(ids):
        if i < n_entries:
            services.append(
                Service(sid, entry=True,
                        entry_weight=raw_weights[i] / wsum,
                        fail_domain=f"fd{rng.randrange(n_fail_domains)}")
            )
        else:
            services.append(
                Service(sid, fail_domain=f"fd{rng.randrange(n_fail_domains)}")
            )
    topo = Topology(services)

    def downstream_of(idx: int) -> int:
        span = n_services - idx - 1
        if span <= 1:
            return n_services - 1
        return idx + 1 + int((rng.random() ** 2) * span)

    target_redundant = int(n_edges * redundant_ratio)
    target_redundant -= target_redundant % 2
    remaining = n_edges
    redundant_left = target_redundant

    for idx, sid in enumerate(ids[:-1]):
        if remaining <= 0:
            break
        expected = remaining / (n_services - idx)
        k = max(0, int(rng.gauss(expected, expected ** 0.5 + 0.5)))
        for _ in range(k):
            if remaining <= 0:
                break
            can_redundant = redundant_left >= 2 and remaining >= 2
            if can_redundant and rng.random() < 0.25:
                j1 = downstream_of(idx)
                j2 = min(j1 + rng.randrange(1, 4), n_services - 1)
                if ids[j1] == ids[j2]:
                    topo.add_group(sid, [ids[j1]])
                    remaining -= 1
                else:
                    topo.add_group(sid, [ids[j1], ids[j2]], min_healthy=1)
                    redundant_left -= 2
                    remaining -= 2
            else:
                topo.add_group(sid, [ids[downstream_of(idx)]])
                remaining -= 1

    for svc in topo.services.values():
        total = len(svc.deps)
        if total > 1:
            for grp in svc.deps:
                grp.traffic_share = 1.0 / total

    return topo

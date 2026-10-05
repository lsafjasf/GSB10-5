"""
大规模合成拓扑生成器：用于性能基准（1 万服务 / 10 万依赖）。

生成规则（确定性，固定随机种子可复现）：
- 服务分 20 层，入口服务集中在第 0 层，依赖只从浅层指向深层（DAG）。
- 每个服务随机挂 1~3 个故障域（az-a/b/c）之一；冗余组成员按
  "跨 AZ 部署"（默认，真实多活）或"同 AZ 部署"（correlated=True，
  模拟隐性耦合）两种方式分布。
- 依赖分普通依赖与冗余组依赖，比例之和 <= 1。
"""

from __future__ import annotations

import random

from .model import Dependency, Member, RedundancyGroup, Service, Topology


def generate_scale_topology(
        n_services: int = 10_000,
        n_deps: int = 100_000,
        n_entries: int = 300,
        n_groups: int = 600,
        group_size: int = 3,
        correlated_group_ratio: float = 0.1,
        seed: int = 42) -> Topology:
    rng = random.Random(seed)
    layers = 20
    layer_of = [min(layers - 1, int(rng.expovariate(0.35))) for _ in range(n_services)]
    by_layer: list[list[int]] = [[] for _ in range(layers)]
    for i, ly in enumerate(layer_of):
        by_layer[ly].append(i)
    zones = ["az-a", "az-b", "az-c"]

    services: dict[str, Service] = {}
    for i in range(n_services):
        sid = f"svc-{i:05d}"
        services[sid] = Service(
            id=sid,
            entry_weight=(1.0 if i < n_entries else 0.0),
            zone=zones[i % 3],
        )

    def deeper_nodes(i: int) -> list[int]:
        ly = layer_of[i]
        pool: list[int] = []
        for deeper in range(ly + 1, min(layers, ly + 4)):
            pool.extend(by_layer[deeper])
        return pool

    # 先放冗余组（每组占 group_size 条依赖边）
    group_edges = 0
    callers = rng.sample(range(n_entries, n_services), k=min(n_groups, n_services - n_entries))
    for gi, caller_idx in enumerate(callers):
        caller = f"svc-{caller_idx:05d}"
        pool = deeper_nodes(caller_idx)
        if len(pool) < group_size:
            continue
        members = rng.sample(pool, k=group_size)
        correlated = rng.random() < correlated_group_ratio
        if correlated:
            # 隐性耦合：把成员全部挪到同一故障域
            z = rng.choice(zones)
            for m in members:
                services[f"svc-{m:05d}"].zone = z
        else:
            # 真实多活：成员打散到不同 AZ
            for k, m in enumerate(members):
                services[f"svc-{m:05d}"].zone = zones[k % 3]
        grp = RedundancyGroup(
            id=f"grp-{gi:04d}",
            members=[Member(service=f"svc-{m:05d}") for m in members],
            ratio=round(rng.uniform(0.2, 0.6), 3),
            kind="hard",
        )
        services[caller].groups.append(grp)
        group_edges += group_size

    # 普通依赖：给每个有下游候选的调用方按指数分布定出度（均值凑足 n_deps），
    # 比例用随机权重在预算内一次性归一，避免拒绝采样。
    eligible = [(i, p) for i in range(n_services) if (p := deeper_nodes(i))]
    mean_k = n_deps / len(eligible)
    made = 0
    for i, pool in eligible:
        sv = services[f"svc-{i:05d}"]
        group_budget = sum(g.ratio for g in sv.groups)
        dep_budget = 0.95 - group_budget
        k = min(len(pool), max(1, int(rng.expovariate(1.0 / mean_k))))
        targets = rng.sample(pool, k=k)
        weights = [rng.random() for _ in targets]
        wsum = sum(weights) or 1.0
        spend = dep_budget * rng.uniform(0.6, 1.0)
        for j, w in zip(targets, weights):
            ratio = round(w / wsum * spend, 4)
            if ratio <= 0.0:
                continue
            kind = "soft" if rng.random() < 0.15 else "hard"
            sv.deps.append(Dependency(
                target=f"svc-{j:05d}", ratio=ratio, kind=kind))
            made += 1
    # 补足轮：指数取整会略低于目标，逐节点补边直到达到 n_deps
    for i, pool in eligible:
        if made >= n_deps:
            break
        sv = services[f"svc-{i:05d}"]
        existing = {d.target for d in sv.deps}
        used = sum(d.ratio for d in sv.deps) + sum(g.ratio for g in sv.groups)
        for j in pool:
            if made >= n_deps or used >= 0.95:
                break
            target = f"svc-{j:05d}"
            if target in existing:
                continue
            ratio = min(round(rng.uniform(0.01, 0.1), 4), 0.95 - used)
            if ratio <= 0.0:
                break
            sv.deps.append(Dependency(target=target, ratio=ratio, kind="hard"))
            existing.add(target)
            used += ratio
            made += 1

    topo = Topology(services)
    topo.validate()
    return topo

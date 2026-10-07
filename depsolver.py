"""部署顺序依赖推导库（仅标准库）。

三类依赖的语义见 RULES.md，简要：
  strong   强依赖：依赖方必须在被依赖方之后部署，强依赖参与成环，环必须拆开。
  weak     弱依赖：尽量保证先后顺序；若与强依赖分层结果冲突则记录为“延迟满足”，不阻断部署。
  optional 可选依赖：可部署可不部署，不参与排序与成环，仅登记。

边的方向统一为 dep -> dependent（被依赖者 -> 依赖者）。
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Dict, FrozenSet, Iterable, List, Optional, Set, Tuple


Edge = Tuple[str, str]  # (dep, dependent)


@dataclass
class Plan:
    steps: List[List[str]]
    cut_edges: List[Edge] = field(default_factory=list)
    cycles: List[List[str]] = field(default_factory=list)
    weak_satisfied: List[Edge] = field(default_factory=list)
    weak_deferred: List[Edge] = field(default_factory=list)
    optional_edges: List[Edge] = field(default_factory=list)
    missing_nodes: List[str] = field(default_factory=list)

    @property
    def component_count(self) -> int:
        return sum(len(s) for s in self.steps)

    def index(self) -> Dict[str, int]:
        return {c: i for i, layer in enumerate(self.steps) for c in layer}

    def to_dict(self) -> dict:
        d = asdict(self)
        d["component_count"] = self.component_count
        d["step_count"] = len(self.steps)
        return d


@dataclass
class Verification:
    strong_edges_total: int
    strong_edges_ordered: int
    strong_edges_cut: int
    violations: List[Edge]
    layer_internal_edges: List[Edge]
    all_components_placed: bool
    ok: bool

    def to_dict(self) -> dict:
        return asdict(self)


class DepGraph:
    """依赖图。deps[X] 表示 X 依赖的组件集合（X 必须在它们之后部署）。"""

    def __init__(
        self,
        strong: Optional[Dict[str, Iterable[str]]] = None,
        weak: Optional[Dict[str, Iterable[str]]] = None,
        optional: Optional[Dict[str, Iterable[str]]] = None,
    ):
        self.strong: Dict[str, Set[str]] = {k: set(v) for k, v in (strong or {}).items()}
        self.weak: Dict[str, Set[str]] = {k: set(v) for k, v in (weak or {}).items()}
        self.optional: Dict[str, Set[str]] = {k: set(v) for k, v in (optional or {}).items()}

    @classmethod
    def from_dict(cls, data: dict) -> "DepGraph":
        comps = data.get("components", data)
        strong, weak, optional = {}, {}, {}
        for name, spec in comps.items():
            if isinstance(spec, dict):
                strong[name] = spec.get("strong", [])
                weak[name] = spec.get("weak", [])
                optional[name] = spec.get("optional", [])
            else:
                strong[name] = spec or []
        return cls(strong, weak, optional)

    @classmethod
    def from_json(cls, text: str) -> "DepGraph":
        return cls.from_dict(json.loads(text))

    def nodes(self) -> List[str]:
        out: Set[str] = set(self.strong) | set(self.weak) | set(self.optional)
        for table in (self.strong, self.weak, self.optional):
            for deps in table.values():
                out.update(deps)
        return sorted(out)

    def strong_edges(self) -> List[Edge]:
        return [(d, c) for c, deps in sorted(self.strong.items()) for d in sorted(deps)]


def _strong_adj(nodes: List[str], g: DepGraph) -> Dict[str, List[str]]:
    """被依赖 -> 依赖者 的邻接表（强依赖边，缺失节点不产生边）。"""
    nset = set(nodes)
    adj: Dict[str, List[str]] = {n: [] for n in nodes}
    for dependent, deps in g.strong.items():
        if dependent not in nset:
            continue
        for dep in deps:
            if dep in nset and dep != dependent:
                adj[dep].append(dependent)
    for n in nodes:
        adj[n].sort()
    return adj


def tarjan_scc(nodes: List[str], adj: Dict[str, List[str]]) -> List[List[str]]:
    """迭代版 Tarjan，返回 SCC 列表（逆拓扑序，此处不依赖该顺序）。"""
    index = {}
    low = {}
    onstack: Set[str] = set()
    stack: List[str] = []
    sccs: List[List[str]] = []
    counter = 0

    for root in nodes:
        if root in index:
            continue
        work: List[Tuple[str, int]] = [(root, 0)]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        onstack.add(root)
        while work:
            v, pi = work[-1]
            if pi < len(adj[v]):
                w = adj[v][pi]
                work[-1] = (v, pi + 1)
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    onstack.add(w)
                    work.append((w, 0))
                elif w in onstack:
                    low[v] = min(low[v], index[w])
            else:
                if low[v] == index[v]:
                    comp = []
                    while True:
                        w = stack.pop()
                        onstack.discard(w)
                        comp.append(w)
                        if w == v:
                            break
                    sccs.append(comp)
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[v])
    return sccs


def _break_scc(component: List[str], adj: Dict[str, List[str]]) -> Tuple[List[str], List[Edge]]:
    """对一个强连通分量贪心拆环。

    规则：不断取出“分量内剩余前驱均已放置”的节点（其强依赖已满足）；
    若不存在这样的节点（必然仍在环上），选择“未满足前驱最少”的节点，
    把指向它的这些入边标记为割边后放置。割边数有上界 |SCC|-1，
    因为每割一条边至少解开一个环，且该启发式优先选入度最小的节点。
    """
    members = set(component)
    remaining = set(component)
    remaining_pred: Dict[str, Set[str]] = {
        n: {p for p, succs in adj.items() if n in succs and p in members} for n in component
    }
    order: List[str] = []
    cut: List[Edge] = []
    while remaining:
        ready = sorted(n for n in remaining if not remaining_pred[n])
        if ready:
            for n in ready:
                order.append(n)
                remaining.discard(n)
                for m in remaining:
                    remaining_pred[m].discard(n)
        else:
            n = min(remaining, key=lambda x: (len(remaining_pred[x]), x))
            for dep in sorted(remaining_pred[n]):
                cut.append((dep, n))
            remaining_pred[n].clear()
            order.append(n)
            remaining.discard(n)
            for m in remaining:
                remaining_pred[m].discard(n)
    return order, cut


def layered_kahn(nodes: List[str], adj: Dict[str, List[str]]) -> List[List[str]]:
    """按最长依赖链分层；同层节点互相之间没有边，可以并行。"""
    nset = set(nodes)
    indeg = {n: 0 for n in nodes}
    for p, succs in adj.items():
        for s in succs:
            if s in nset:
                indeg[s] += 1
    current = sorted(n for n in nodes if indeg[n] == 0)
    layers: List[List[str]] = []
    placed = 0
    while current:
        layers.append(current)
        placed += len(current)
        nxt_set: Set[str] = set()
        for n in current:
            for s in adj[n]:
                if s in nset:
                    indeg[s] -= 1
                    if indeg[s] == 0:
                        nxt_set.add(s)
        current = sorted(nxt_set)
    if placed != len(nodes):
        raise ValueError("Kahn 分层失败：图中仍有环（拆环有 bug）")
    return layers


def derive(g: DepGraph) -> Plan:
    """主入口：推导部署分层计划。"""
    nodes = g.nodes()
    nset = set(nodes)
    adj = _strong_adj(nodes, g)

    declared: Set[str] = set(g.strong) | set(g.weak) | set(g.optional)
    missing = sorted(nset - declared)

    # 1. 找环（仅强依赖成环）。
    sccs = tarjan_scc(nodes, adj)
    cycles = [sorted(c) for c in sccs if len(c) > 1 or (c and c[0] in adj[c[0]])]

    # 2. 每个非平凡 SCC 贪心割边。
    cut: Set[Edge] = set()
    for comp in sccs:
        if len(comp) == 1 and comp[0] not in adj[comp[0]]:
            continue
        _, edges = _break_scc(comp, adj)
        cut.update(edges)

    # 3. 去掉割边后图必为 DAG，Kahn 分层。
    dag_adj: Dict[str, List[str]] = {n: [] for n in nodes}
    for dep, succs in adj.items():
        for dep_dent in succs:
            if (dep, dep_dent) not in cut:
                dag_adj[dep].append(dep_dent)
    steps = layered_kahn(nodes, dag_adj)
    layer_index = {c: i for i, layer in enumerate(steps) for c in layer}

    # 4. 弱依赖只做一致性检查；可选依赖只登记。
    weak_ok, weak_deferred = [], []
    for dependent, deps in g.weak.items():
        for dep in deps:
            edge: Edge = (dep, dependent)
            if dep not in nset:
                continue
            if layer_index[dep] < layer_index[dependent]:
                weak_ok.append(edge)
            else:
                weak_deferred.append(edge)

    optional_edges = [
        (dep, dependent)
        for dependent, deps in sorted(g.optional.items())
        for dep in sorted(deps)
        if dep in nset
    ]

    return Plan(
        steps=[sorted(s) for s in steps],
        cut_edges=sorted(cut),
        cycles=sorted(cycles, key=lambda c: c[0]),
        weak_satisfied=sorted(weak_ok),
        weak_deferred=sorted(weak_deferred),
        optional_edges=sorted(optional_edges),
        missing_nodes=missing,
    )


def verify(g: DepGraph, plan: Plan) -> Verification:
    """校验：除已声明割边外，每条强依赖边 dep -> dependent 必须 dep 层号严格更小。"""
    idx = plan.index()
    cutset: FrozenSet[Edge] = frozenset(plan.cut_edges)
    total = ordered = n_cut = 0
    violations: List[Edge] = []
    internal: List[Edge] = []
    for dep, dependent in g.strong_edges():
        total += 1
        if (dep, dependent) in cutset:
            n_cut += 1
            continue
        li, lj = idx.get(dep), idx.get(dependent)
        if li is None or lj is None:
            violations.append((dep, dependent))
        elif li >= lj:
            violations.append((dep, dependent))
            if li == lj:
                internal.append((dep, dependent))
        else:
            ordered += 1
    all_placed = set(idx) == set(g.nodes())
    return Verification(
        strong_edges_total=total,
        strong_edges_ordered=ordered,
        strong_edges_cut=n_cut,
        violations=sorted(violations),
        layer_internal_edges=sorted(internal),
        all_components_placed=all_placed,
        ok=not violations and all_placed,
    )


def break_plan_detail(plan: Plan) -> dict:
    """把割边方案展开成“分几步部署、影响谁”的可读结构。"""
    idx = plan.index()
    affected = sorted({c for e in plan.cut_edges for c in e})
    phase_a: Set[str] = set(affected)
    redeploy = sorted(dependent for _, dependent in plan.cut_edges)
    return {
        "cut_count": len(plan.cut_edges),
        "cuts": [
            {
                "edge": list(edge),
                "meaning": f"先部署依赖方 {edge[1]}（临时不使用/兼容模式使用 {edge[0]}），"
                           f"{edge[0]} 在第 {idx[edge[0]] + 1} 层上线，"
                           f"{edge[1]} 位于第 {idx[edge[1]] + 1} 层",
                "dep": edge[0],
                "dependent": edge[1],
            }
            for edge in plan.cut_edges
        ],
        "affected_components": affected,
        "phases": [
            {
                "phase": 1,
                "action": "按分层顺序整体部署；割边的依赖方以兼容/降级模式运行，不调用被依赖方的新版本能力",
                "components_temporarily_degraded": sorted(phase_a),
            },
            {
                "phase": 2,
                "action": "被依赖方上线后，对割边依赖方做滚动重启/二次发布，激活完整能力",
                "components_to_redeploy": redeploy,
            },
        ],
    }


# ---------------------------------------------------------------- CLI

def _gen_bench_graph(n: int, seed: int = 42) -> DepGraph:
    """生成 n 节点的基准图：约 80% 组件带 1~3 条指向更早节点的强依赖（DAG），
    另撒少量弱依赖、可选依赖，以及 20 条回边制造若干强连通分量。"""
    import random

    rng = random.Random(seed)
    strong: Dict[str, List[str]] = {}
    weak: Dict[str, List[str]] = {}
    optional: Dict[str, List[str]] = {}
    names = [f"svc-{i:05d}" for i in range(n)]
    for i, name in enumerate(names):
        s: List[str] = []
        if i > 0 and rng.random() < 0.8:
            for _ in range(rng.randint(1, 3)):
                s.append(names[rng.randrange(i)])
        strong[name] = sorted(set(s))
        if i > 10 and rng.random() < 0.1:
            weak[name] = [names[rng.randrange(i)]]
        if i > 10 and rng.random() < 0.05:
            optional[name] = [names[rng.randrange(i)]]
    # 回边：让部分节点强依赖更晚的节点，形成环
    for _ in range(20):
        i = rng.randrange(n // 2, n)
        j = rng.randrange(0, i)
        strong[names[j]] = sorted(set(strong[names[j]] + [names[i]]))
    return DepGraph(strong, weak, optional)


def _cmd_bench(argv: List[str]) -> int:
    import resource
    import time
    import tracemalloc

    n = int(argv[0]) if argv else 10000
    g = _gen_bench_graph(n)

    tracemalloc.start()
    t0 = time.perf_counter()
    plan = derive(g)
    ver = verify(g, plan)
    elapsed = time.perf_counter() - t0
    _, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    report = {
        "components": n,
        "strong_edges": len(g.strong_edges()),
        "steps": len(plan.steps),
        "cycles_found": len(plan.cycles),
        "cut_edges": len(plan.cut_edges),
        "weak_satisfied": len(plan.weak_satisfied),
        "weak_deferred": len(plan.weak_deferred),
        "optional_edges": len(plan.optional_edges),
        "verify_ok": ver.ok,
        "derive_seconds": round(elapsed, 4),
        "python_heap_peak_mb": round(py_peak / 1024 / 1024, 2),
        "process_rss_peak_mb": round(rss_kb / 1024, 2),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if ver.ok else 1


def _cmd_solve(argv: List[str]) -> int:
    if not argv:
        print("用法: python3 depsolver.py solve <deps.json> [--full]", file=sys.stderr)
        return 2
    with open(argv[0], encoding="utf-8") as fh:
        g = DepGraph.from_json(fh.read())
    full = "--full" in argv
    plan = derive(g)
    ver = verify(g, plan)
    out = {
        "steps": plan.steps,
        "cycles": plan.cycles,
        "cut_edges": [list(e) for e in plan.cut_edges],
        "break_plan": break_plan_detail(plan) if plan.cut_edges else None,
        "weak_satisfied": [list(e) for e in plan.weak_satisfied],
        "weak_deferred": [list(e) for e in plan.weak_deferred],
        "optional_edges": [list(e) for e in plan.optional_edges],
        "missing_nodes": plan.missing_nodes,
        "verification": ver.to_dict(),
    }
    if not full:
        out["steps"] = [f"第{i+1}层: {', '.join(s)}" for i, s in enumerate(plan.steps)]
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if ver.ok else 1


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print(__doc__)
        print("命令: solve <deps.json> [--full] | bench [N]", file=sys.stderr)
        sys.exit(2)
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "solve":
        sys.exit(_cmd_solve(rest))
    if cmd == "bench":
        sys.exit(_cmd_bench(rest))
    print(f"未知命令: {cmd}", file=sys.stderr)
    sys.exit(2)

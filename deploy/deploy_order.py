"""部署顺序依赖推导库（仅使用 Python 标准库）。

依赖分级规则（依据详见 DESIGN.md）：
- strong（强依赖）：a 强依赖 b，则 b 必须完整部署完成后 a 才能开始部署。
  强依赖构成硬约束，参与分层；强依赖成环时无法整体满足，必须断边拆解。
- weak（弱依赖）：期望 b 先于 a，但允许违反。仅当弱依赖与强依赖分层
  不冲突（不引入环、不破坏已有先后关系）时才采纳，否则记入 deferred 列表。
- optional（可选依赖）：运行期特性开关，完全不参与部署排序，仅在报告中列出。
  同一对组件出现多种依赖时，按 strong > weak > optional 取最高级别。
"""

from collections import defaultdict
from dataclasses import dataclass, field

STRONG = "strong"
WEAK = "weak"
OPTIONAL = "optional"
_KINDS = (STRONG, WEAK, OPTIONAL)


class CycleError(Exception):
    """在应当无环的图上检测到环。"""


class DependencyGraph:
    """组件依赖图。add_dependency(a, b) 表示 a 依赖 b（b 需先于 a）。"""

    def __init__(self):
        self._nodes = []
        self._node_set = set()
        # kind -> {dependent: set(dependency)}
        self._edges = {k: defaultdict(set) for k in _KINDS}

    def add_component(self, name):
        if name not in self._node_set:
            self._node_set.add(name)
            self._nodes.append(name)

    def add_dependency(self, dependent, dependency, kind=STRONG):
        if kind not in _KINDS:
            raise ValueError(f"unknown dependency kind: {kind}")
        self.add_component(dependent)
        self.add_component(dependency)
        self._edges[kind][dependent].add(dependency)

    @property
    def nodes(self):
        return list(self._nodes)

    def edges(self, kind=STRONG):
        """返回 [(dependent, dependency), ...]，按名字排序保证确定性。"""
        out = []
        for a, bs in self._edges[kind].items():
            for b in bs:
                out.append((a, b))
        out.sort()
        return out

    def effective_edges(self):
        """按 strong > weak > optional 去重后的全部依赖对。"""
        seen = set()
        result = {k: [] for k in _KINDS}
        for kind in _KINDS:
            for a, b in self.edges(kind):
                if (a, b) not in seen:
                    seen.add((a, b))
                    result[kind].append((a, b))
        return result


def _strongly_connected_components(nodes, succ):
    """迭代版 Tarjan 强连通分量。succ: node -> 后继集合。返回分量列表。"""
    index_of = {}
    lowlink = {}
    stack = []
    on_stack = set()
    result = []
    idx = 0
    for start in nodes:
        if start in index_of:
            continue
        call = [(start, iter(sorted(succ.get(start, ()))))]
        while call:
            node, it = call[-1]
            if node not in index_of:
                index_of[node] = lowlink[node] = idx
                idx += 1
                stack.append(node)
                on_stack.add(node)
            descended = False
            for nxt in it:
                if nxt not in index_of:
                    call.append((nxt, iter(sorted(succ.get(nxt, ())))))
                    descended = True
                    break
                if nxt in on_stack and index_of[nxt] < lowlink[node]:
                    lowlink[node] = index_of[nxt]
            if descended:
                continue
            call.pop()
            if call:
                parent = call[-1][0]
                if lowlink[node] < lowlink[parent]:
                    lowlink[parent] = lowlink[node]
            if lowlink[node] == index_of[node]:
                scc = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    scc.append(w)
                    if w == node:
                        break
                result.append(sorted(scc))
    return result


def _layering(nodes, deps):
    """Kahn 最长路分层。deps: a -> {b} 表示 a 依赖 b（b 必须先部署）。

    返回 (layers, layer_of)。layer i 中所有节点的强依赖都在 < i 的层，
    因此同层节点之间不存在任何先后约束，可以并行部署。
    图必须有环检测：若存在环则抛 CycleError。
    """
    indeg = {n: 0 for n in nodes}
    dependents = defaultdict(set)
    for a in nodes:
        for b in deps.get(a, ()):
            if a == b:
                raise CycleError(f"self-loop on {a}")
            if a not in dependents[b]:
                dependents[b].add(a)
                indeg[a] += 1
    layers = []
    layer_of = {}
    current = sorted(n for n in nodes if indeg[n] == 0)
    depth = 0
    while current:
        layers.append(current)
        for n in current:
            layer_of[n] = depth
        nxt = []
        for n in current:
            for m in dependents[n]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    nxt.append(m)
        current = sorted(nxt)
        depth += 1
    if len(layer_of) != len(nodes):
        leftover = sorted(n for n in nodes if n not in layer_of)
        raise CycleError(f"cycle detected among: {leftover}")
    return layers, layer_of


def _feedback_order(scc, deps):
    """Eades-Lin-Smyth 贪心启发式，在 SCC 内求近似最小反馈边集。

    返回部署顺序 order：对依赖 (a 依赖 b)，若 b 在 order 中不先于 a，
    则该边为需要断开的边。时间复杂度 O(n^2)，SCC 通常很小。
    """
    members = set(scc)
    succ = {n: set() for n in scc}   # b -> {a}: b 部署后 a 才能部署
    pred = {n: set() for n in scc}
    for a in scc:
        for b in deps.get(a, ()):
            if b in members and b != a:
                succ[b].add(a)
                pred[a].add(b)
    remaining = set(scc)
    head, tail = [], []
    while remaining:
        moved = False
        sinks = sorted(n for n in remaining if not (succ[n] & remaining))
        for n in sinks:
            tail.append(n)
            remaining.discard(n)
            moved = True
        sources = sorted(n for n in remaining if not (pred[n] & remaining))
        for n in sources:
            head.append(n)
            remaining.discard(n)
            moved = True
        if not moved:
            pick = max(
                remaining,
                key=lambda x: (len(succ[x] & remaining) - len(pred[x] & remaining), x),
            )
            head.append(pick)
            remaining.discard(pick)
    return head + tail[::-1]


@dataclass
class BreakPlan:
    """单个强连通分量的拆解方案。"""
    components: list
    cut_edges: list          # [(dependent, dependency)] 被断开的强依赖
    steps: list              # 断开后的分步部署层（分量内部）
    affected: list           # 受断边影响、需降级启动的组件（cut 边的依赖方）


@dataclass
class Plan:
    layers: list             # 全局并行分层
    layer_of: dict
    cut_edges: list          # 全部被断开的强依赖
    breaks: list             # 每个环的 BreakPlan
    weak_honored: list       # 被采纳的弱依赖
    weak_deferred: list      # 因冲突被放弃的弱依赖
    optional: list           # 可选依赖（仅记录，不参与排序）

    @property
    def cyclic(self):
        return bool(self.cut_edges)


def plan(graph):
    """推导部署计划。总是成功：无环直接分层，有环自动断边后分层。"""
    nodes = graph.nodes
    effective = graph.effective_edges()
    strong_deps = defaultdict(set)
    for a, b in effective[STRONG]:
        strong_deps[a].add(b)

    # 1) 自环：必然断开
    cut = set()
    for a in nodes:
        if a in strong_deps[a]:
            strong_deps[a].discard(a)
            cut.add((a, a))

    # 2) SCC + 反馈边集，拆解规模 > 1 的环
    succ = defaultdict(set)
    for a, bs in strong_deps.items():
        for b in bs:
            succ[b].add(a)
    breaks = []
    for scc in _strongly_connected_components(nodes, succ):
        if len(scc) < 2:
            continue
        order = _feedback_order(scc, strong_deps)
        pos = {n: i for i, n in enumerate(order)}
        scc_cut = []
        members = set(scc)
        for a in scc:
            for b in strong_deps[a] & members:
                if pos[b] >= pos[a]:  # b 未能排在 a 之前，必须断开
                    scc_cut.append((a, b))
                    cut.add((a, b))
        for a, b in scc_cut:
            strong_deps[a].discard(b)
        local_layers, _ = _layering(scc, {n: strong_deps.get(n, set()) & members for n in scc})
        affected = sorted({a for a, _ in scc_cut})
        breaks.append(BreakPlan(
            components=scc,
            cut_edges=sorted(scc_cut),
            steps=local_layers,
            affected=affected,
        ))

    # 3) 全局分层（强依赖 - 已断边）
    layers, layer_of = _layering(nodes, strong_deps)

    # 4) 弱依赖：仅采纳与现有分层方向一致的（layer[b] < layer[a]），
    #    采纳后不会改变分层（最长路不变），保证不引入环。
    weak_honored, weak_deferred = [], []
    strong_pairs = set(effective[STRONG])
    for a, b in effective[WEAK]:
        if (a, b) in strong_pairs or a == b:
            continue
        if layer_of[b] < layer_of[a]:
            weak_honored.append((a, b))
        else:
            weak_deferred.append((a, b))

    optional = [(a, b) for a, b in effective[OPTIONAL]
                if (a, b) not in strong_pairs and a != b]

    return Plan(
        layers=layers,
        layer_of=layer_of,
        cut_edges=sorted(cut),
        breaks=breaks,
        weak_honored=weak_honored,
        weak_deferred=weak_deferred,
        optional=optional,
    )


def validate(graph, p):
    """校验推导结果，返回可核对的校验数据字典。

    不变式：每条未被显式断开的强依赖 (a 依赖 b) 都满足 layer[b] < layer[a]；
    被断开的边必须全部出现在 plan.cut_edges 中；被采纳的弱依赖同样满足先后。
    """
    effective = graph.effective_edges()
    cut = set(p.cut_edges)
    strong_total = len(effective[STRONG])
    violations = []
    satisfied = 0
    for a, b in effective[STRONG]:
        if (a, b) in cut:
            continue
        if p.layer_of[b] < p.layer_of[a]:
            satisfied += 1
        else:
            violations.append((a, b))
    weak_violations = [(a, b) for a, b in p.weak_honored
                       if not p.layer_of[b] < p.layer_of[a]]
    # 每条边恰好落在一层；层数与节点数一致
    all_nodes = sorted(n for layer in p.layers for n in layer)
    data = {
        "components": len(all_nodes),
        "nodes_unique": len(all_nodes) == len(set(all_nodes)),
        "layers": len(p.layers),
        "max_layer_width": max((len(l) for l in p.layers), default=0),
        "strong_total": strong_total,
        "strong_satisfied": satisfied,
        "strong_cut": len(p.cut_edges),
        "strong_violations": violations,
        "weak_honored": len(p.weak_honored),
        "weak_honored_violations": weak_violations,
        "weak_deferred": len(p.weak_deferred),
        "optional_ignored": len(p.optional),
    }
    data["ok"] = (not violations and not weak_violations
                  and data["nodes_unique"]
                  and satisfied + len(p.cut_edges) == strong_total)
    return data

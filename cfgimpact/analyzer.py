"""影响面分析: 从变更配置出发, 在反向引用图上做 BFS, 分层输出直接/间接影响及推导路径。"""

from collections import deque
from dataclasses import dataclass, field

from .graph import pattern_struct_match


@dataclass
class ImpactItem:
    node: str           # 受影响节点(配置项/模块/服务)
    depth: int          # 距变更项的最短跳数
    path: list          # 推导路径, 如 [变更项, 中间配置, 服务]


@dataclass
class ManualItem:
    source: str         # 发起动态引用的服务/模块
    pattern: str        # 无法静态判定的模式, 如 feature.{flag}.enabled
    matched_key: str    # 结构匹配上的变更项


@dataclass
class ImpactReport:
    changed: list = field(default_factory=list)
    direct: list = field(default_factory=list)
    indirect: list = field(default_factory=list)
    manual_confirm: list = field(default_factory=list)
    unresolved_inventory: int = 0

    @property
    def affected(self):
        return {i.node for i in self.direct} | {i.node for i in self.indirect}


class ImpactAnalyzer:
    def __init__(self, graph):
        if not graph._finalized:
            graph.finalize()
        self.g = graph

    def analyze(self, changed):
        g = self.g
        visited = {}  # node_id -> (depth, parent_id)
        dq = deque()
        for c in changed:
            cid = g._ids.get(c)
            if cid is None or cid in visited:
                continue
            visited[cid] = (0, -1)
            dq.append(cid)

        while dq:
            u = dq.popleft()
            du = visited[u][0]
            for v in g._rev[u]:
                if v not in visited:
                    visited[v] = (du + 1, u)
                    dq.append(v)

        rep = ImpactReport(changed=list(changed))
        for node, (depth, parent) in visited.items():
            if depth == 0:
                continue
            path = []
            cur = node
            while cur != -1:
                path.append(g.names[cur])
                cur = visited[cur][1]
            path.reverse()
            item = ImpactItem(node=g.names[node], depth=depth, path=path)
            (rep.direct if depth == 1 else rep.indirect).append(item)

        rep.direct.sort(key=lambda i: i.node)
        rep.indirect.sort(key=lambda i: (i.depth, i.node))

        # 未解析动态引用: 结构匹配上变更项的列入待人工确认, 其余仍计入清单总数
        for src, pattern in g.unresolved:
            for c in changed:
                if pattern_struct_match(pattern, c):
                    rep.manual_confirm.append(ManualItem(src, pattern, c))
                    break
        rep.unresolved_inventory = len(g.unresolved)
        return rep


def format_report(rep):
    lines = ["变更项: " + ", ".join(rep.changed)]
    lines.append(f"[直接引用] {len(rep.direct)} 项")
    for it in rep.direct:
        lines.append(f"  - {it.node}  (推导路径: {' -> '.join(it.path)})")
    lines.append(f"[间接受影响] {len(rep.indirect)} 项")
    for it in rep.indirect:
        lines.append(
            f"  - {it.node}  (深度{it.depth}, 推导路径: {' -> '.join(it.path)})"
        )
    lines.append(
        f"[动态引用待人工确认] {len(rep.manual_confirm)} 项 "
        f"(未解析动态引用库存总数: {rep.unresolved_inventory})"
    )
    for m in rep.manual_confirm:
        lines.append(
            f"  - {m.source} 的模式 {m.pattern} 可能命中变更项 {m.matched_key}"
        )
    return "\n".join(lines)

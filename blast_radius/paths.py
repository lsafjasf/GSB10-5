"""
关键路径计数

定义：一条"关键路径" = 从某个受影响入口服务出发，沿活跃依赖边
（影响真实传导经过的边）到达某个故障注入点的路径。它回答的是
"这次故障通过多少条不同的传播链路烧到了用户入口"。

算法：
- 先裁剪到相关子图（入口可达 ∩ 能到达故障点）。
- 无环：逆拓扑序 DP，paths(v) = [v是故障点] + Σ paths(后继)，精确计数。
- 有环：迭代版 Kosaraju 缩点成 DAG 后按宏路径计数，exact=False
  （环内多条等价链路只算一条，属于有意的保守近似，避免指数爆炸）。
- 计数在 CAP=1e18 处饱和并置 capped 标志；同时用对数域累加给出
  log10 数量级估计，路径数天文数字时仍有参考价值。
"""

from __future__ import annotations

import math

CAP = 10 ** 18


def _logadd(a: float, b: float) -> float:
    """log10 域加法：返回 log10(10^a + 10^b)，-inf 表示 0。"""
    if a == -math.inf:
        return b
    if b == -math.inf:
        return a
    hi, lo = max(a, b), min(a, b)
    return hi + math.log10(1.0 + 10.0 ** (lo - hi))


def _condense(nodes: set[str], adj: dict[str, list[str]]) -> tuple[
        dict[int, list[int]], dict[str, int], dict[int, list[str]]]:
    """迭代版 Kosaraju 强连通分量缩点。返回 (DAG邻接, 节点->分量, 分量->成员)。"""
    # 第一遍：原图 DFS 得完成顺序
    visited: set[str] = set()
    finish_order: list[str] = []
    for start in nodes:
        if start in visited:
            continue
        stack = [(start, iter(adj.get(start, ())))]
        visited.add(start)
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if nxt in nodes and nxt not in visited:
                    visited.add(nxt)
                    stack.append((nxt, iter(adj.get(nxt, ()))))
                    advanced = True
                    break
            if not advanced:
                finish_order.append(node)
                stack.pop()
    # 反向图
    radj: dict[str, list[str]] = {n: [] for n in nodes}
    for n in nodes:
        for m in adj.get(n, ()):
            if m in nodes:
                radj[m].append(n)
    # 第二遍：按完成逆序在反图上 DFS 划分分量
    comp_of: dict[str, int] = {}
    members: dict[int, list[str]] = {}
    for start in reversed(finish_order):
        if start in comp_of:
            continue
        cid = len(members)
        members[cid] = []
        stack = [start]
        comp_of[start] = cid
        while stack:
            node = stack.pop()
            members[cid].append(node)
            for nxt in radj[node]:
                if nxt not in comp_of:
                    comp_of[nxt] = cid
                    stack.append(nxt)
    dag: dict[int, set[int]] = {c: set() for c in members}
    for n in nodes:
        for m in adj.get(n, ()):
            if m in nodes:
                cn, cm = comp_of[n], comp_of[m]
                if cn != cm:
                    dag[cn].add(cm)
    return ({c: sorted(s) for c, s in dag.items()}, comp_of, members)


def count_critical_paths(
        adj: dict[str, list[str]],
        entries: list[str],
        faults: set[str]) -> tuple[int, bool, float, bool]:
    """
    返回 (路径数, 是否饱和封顶, log10数量级, 是否精确计数)。
    """
    if not faults:
        return 0, False, -math.inf, True

    # 相关子图：从入口正向可达 ∩ 能到达故障点
    fwd: set[str] = set()
    stack = [e for e in entries if e in adj]
    fwd.update(stack)
    while stack:
        node = stack.pop()
        for nxt in adj.get(node, ()):
            if nxt not in fwd:
                fwd.add(nxt)
                stack.append(nxt)
    radj: dict[str, list[str]] = {}
    for n in fwd:
        for m in adj.get(n, ()):
            if m in fwd:
                radj.setdefault(m, []).append(n)
    bwd: set[str] = set()
    stack = [f for f in faults if f in fwd]
    bwd.update(stack)
    while stack:
        node = stack.pop()
        for nxt in radj.get(node, ()):
            if nxt not in bwd:
                bwd.add(nxt)
                stack.append(nxt)
    relevant = fwd & bwd
    if not relevant:
        return 0, False, -math.inf, True

    sub_adj = {n: [m for m in adj.get(n, ()) if m in relevant] for n in relevant}
    sub_faults = faults & relevant
    sub_entries = [e for e in entries if e in relevant]

    # 检测环：Kahn 拓扑，若不能全部出列则有环
    indeg = {n: 0 for n in relevant}
    for n in relevant:
        for m in sub_adj[n]:
            indeg[m] += 1
    queue = [n for n, d in indeg.items() if d == 0]
    topo_order: list[str] = []
    rem = dict(indeg)
    while queue:
        node = queue.pop()
        topo_order.append(node)
        for m in sub_adj[node]:
            rem[m] -= 1
            if rem[m] == 0:
                queue.append(m)
    exact = len(topo_order) == len(relevant)

    if exact:
        # 无环：逆拓扑 DP，paths(v) = [v是故障点] + Σ paths(后继)
        count: dict[str, int] = {}
        logc: dict[str, float] = {}
        capped = False
        for node in reversed(topo_order):
            total = 1 if node in sub_faults else 0
            ltotal = 0.0 if node in sub_faults else -math.inf
            for m in sub_adj[node]:
                total += count[m]
                ltotal = _logadd(ltotal, logc[m])
            if total > CAP:
                total = CAP
                capped = True
            count[node] = total
            logc[node] = ltotal
        grand = sum(count[e] for e in sub_entries)
        glog = -math.inf
        for e in sub_entries:
            glog = _logadd(glog, logc[e])
        if grand > CAP:
            grand = CAP
            capped = True
        return grand, capped, glog, True

    # 有环：缩点后按宏路径计数
    dag, comp_of, members = _condense(relevant, sub_adj)
    fault_comps = {comp_of[f] for f in sub_faults}
    entry_comps = sorted({comp_of[e] for e in sub_entries})
    # 分量 DAG 上逆拓扑 DP
    cindeg = {c: 0 for c in members}
    for c, succs in dag.items():
        for s in succs:
            cindeg[s] += 1
    order: list[int] = []
    queue = [c for c, d in cindeg.items() if d == 0]
    crem = dict(cindeg)
    while queue:
        c = queue.pop()
        order.append(c)
        for s in dag[c]:
            crem[s] -= 1
            if crem[s] == 0:
                queue.append(s)
    count: dict[int, int] = {}
    logc: dict[int, float] = {}
    capped = False
    for c in reversed(order):
        total = 1 if c in fault_comps else 0
        ltotal = 0.0 if c in fault_comps else -math.inf
        for s in dag[c]:
            total += count[s]
            ltotal = _logadd(ltotal, logc[s])
        if total > CAP:
            total = CAP
            capped = True
        count[c] = total
        logc[c] = ltotal
    grand = sum(count[c] for c in entry_comps)
    glog = -math.inf
    for c in entry_comps:
        glog = _logadd(glog, logc[c])
    if grand > CAP:
        grand = CAP
        capped = True
    return grand, capped, glog, False

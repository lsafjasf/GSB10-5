"""最短路径预处理加速库（仅用标准库）。

提供三种策略，查询接口统一为 ``query(s, t)``：

1. :class:`NaivePreprocessor` —— 不预处理，每次查询跑一次朴素 Dijkstra。
2. :class:`APSPPreprocessor`   —— 全点对距离表，查询 O(1)，表内存约 n^2*8 字节。
3. :class:`ALTPreprocessor`    —— ALT 路标预处理（k 个路标的正向/反向全源
   Dijkstra），查询用 A*，表内存约 2*k*n*8 字节。

内存预算由 ``memory_limit_bytes`` 控制：预算不足时按
``on_overflow="degrade"``（降级）或 ``"raise"``（拒绝，抛
:class:`MemoryError`）处理，绝不无界申请内存。

另外给出成本模型原语：:func:`total_cost` 与 :func:`break_even_queries`。
"""
from __future__ import annotations

import array
import heapq
import math
import random

INF = math.inf

_EIGHT_BYTES = 8


class Graph:
    """带非负权的有向图，邻接表存储，同时保存反向邻接表。"""

    __slots__ = ("n", "adj", "rad")

    def __init__(self, n):
        if n < 0:
            raise ValueError("n must be non-negative")
        self.n = n
        self.adj = [[] for _ in range(n)]
        self.rad = [[] for _ in range(n)]

    def add_edge(self, u, v, weight):
        if not (0 <= u < self.n and 0 <= v < self.n):
            raise ValueError("endpoint out of range")
        if weight < 0:
            raise ValueError("negative weights are not supported")
        self.adj[u].append((v, float(weight)))
        self.rad[v].append((u, float(weight)))

    @property
    def m(self):
        return sum(len(a) for a in self.adj)


def dijkstra_all(graph, source, reverse=False):
    """从 source 出发的全源 Dijkstra，结果用 array('d') 返回。

    reverse=True 时在反向图上跑，得到的是“到 source 的距离”。
    """
    adj = graph.rad if reverse else graph.adj
    dist = array.array("d", [INF]) * graph.n
    dist[source] = 0.0
    heap = [(0.0, source)]
    pop = heapq.heappop
    push = heapq.heappush
    while heap:
        d, u = pop(heap)
        if d != dist[u]:
            continue
        for v, w in adj[u]:
            nd = d + w
            if nd < dist[v]:
                dist[v] = nd
                push(heap, (nd, v))
    return dist


def dijkstra_pair(graph, source, target):
    """朴素单次查询：Dijkstra 命中 target 即提前退出。"""
    if source == target:
        return 0.0
    adj = graph.adj
    dist = {source: 0.0}
    heap = [(0.0, source)]
    pop = heapq.heappop
    push = heapq.heappush
    while heap:
        d, u = pop(heap)
        if d != dist[u]:
            continue
        if u == target:
            return d
        for v, w in adj[u]:
            nd = d + w
            if nd < dist.get(v, INF):
                dist[v] = nd
                push(heap, (nd, v))
    return INF


class NaivePreprocessor:
    """零预处理基线：查询退化为朴素 Dijkstra（内存受限时的最终兜底）。"""

    mode = "none"

    def __init__(self, graph):
        self.graph = graph
        self.k = 0
        self.preprocess_seconds = 0.0
        self.table_bytes = 0

    def query(self, source, target):
        return dijkstra_pair(self.graph, source, target)


class APSPPreprocessor:
    """全点对距离表：n 次 Dijkstra 预处理，查询 O(1)。"""

    mode = "apsp"

    TABLE_BYTES_PER_NODE = _EIGHT_BYTES

    def __init__(self, graph, memory_limit_bytes=None, on_overflow="degrade"):
        n = graph.n
        need = n * n * _EIGHT_BYTES
        self.table_bytes = need
        if memory_limit_bytes is not None and need > memory_limit_bytes:
            if on_overflow == "raise":
                raise MemoryError(
                    "APSP table needs %d bytes, limit is %d" % (need, memory_limit_bytes)
                )
            raise _DegradationRequested(need)
        import time

        self.graph = graph
        self.k = n
        start = time.perf_counter()
        self.table = [dijkstra_all(graph, s) for s in range(n)]
        self.preprocess_seconds = time.perf_counter() - start

    def query(self, source, target):
        return self.table[source][target]


class ALTPreprocessor:
    """ALT 路标 A* 预处理。

    每个路标跑一次正向 Dijkstra（得 d(L,*)）和一次反向 Dijkstra（得 d(*,L)）。
    路标用最远点策略逐个选出（上一次 Dijkstra 的距离直接复用，不额外花钱）。
    """

    mode = "alt"
    BYTES_PER_LANDMARK_PER_NODE = 2 * _EIGHT_BYTES

    def __init__(self, graph, k=16, memory_limit_bytes=None,
                 on_overflow="degrade", seed=0):
        import time

        self.graph = graph
        n = graph.n
        per_landmark = n * self.BYTES_PER_LANDMARK_PER_NODE
        wanted = k
        if memory_limit_bytes is not None and k * per_landmark > memory_limit_bytes:
            k = memory_limit_bytes // per_landmark
            if wanted > 0 and k < wanted and on_overflow == "raise":
                raise MemoryError(
                    "ALT with k=%d needs %d bytes, limit is %d"
                    % (wanted, wanted * per_landmark, memory_limit_bytes)
                )
        self.k = k
        self.memory_limit_bytes = memory_limit_bytes
        self.requested_k = wanted
        self.table_bytes = k * per_landmark
        self.fwd = []
        self.bwd = []
        self.landmarks = []
        self.preprocess_seconds = 0.0
        if k == 0:
            return

        start = time.perf_counter()
        rng = random.Random(seed)
        first = rng.randrange(n)
        first_fwd = dijkstra_all(graph, first, reverse=False)
        self.landmarks.append(first)
        self.fwd.append(first_fwd)
        self.bwd.append(dijkstra_all(graph, first, reverse=True))
        mins = array.array("d", first_fwd)
        for _ in range(1, k):
            nxt = self._pick_farthest(mins)
            fwd_d = dijkstra_all(graph, nxt, reverse=False)
            bwd_d = dijkstra_all(graph, nxt, reverse=True)
            self.landmarks.append(nxt)
            self.fwd.append(fwd_d)
            self.bwd.append(bwd_d)
            for i in range(n):
                if fwd_d[i] < mins[i]:
                    mins[i] = fwd_d[i]
        self.preprocess_seconds = time.perf_counter() - start

    @staticmethod
    def _pick_farthest(mins):
        best_node = 0
        best_dist = mins[0]
        for i in range(1, len(mins)):
            d = mins[i]
            if d > best_dist:
                best_dist = d
                best_node = i
        return best_node

    def query(self, source, target):
        if self.k == 0:
            return dijkstra_pair(self.graph, source, target)
        if source == target:
            return 0.0
        fwd = self.fwd
        bwd = self.bwd
        k = self.k
        ft = [fwd[l][target] for l in range(k)]
        bt = [bwd[l][target] for l in range(k)]

        def heuristic(v):
            best = 0.0
            for l in range(k):
                bv = bwd[l][v]
                btl = bt[l]
                if bv != INF and btl != INF:
                    val = bv - btl
                    if val > best:
                        best = val
                fv = fwd[l][v]
                ftl = ft[l]
                if fv != INF and ftl != INF:
                    val = ftl - fv
                    if val > best:
                        best = val
            return best

        g = {source: 0.0}
        heap = [(heuristic(source), 0.0, source)]
        pop = heapq.heappop
        push = heapq.heappush
        visited = 0
        while heap:
            f, d, u = pop(heap)
            if d != g.get(u):
                continue
            visited += 1
            if u == target:
                self.last_visited_nodes = visited
                return d
            for v, w in self.graph.adj[u]:
                nd = d + w
                if nd < g.get(v, INF):
                    g[v] = nd
                    push(heap, (nd + heuristic(v), nd, v))
        self.last_visited_nodes = visited
        return INF


class _DegradationRequested(Exception):
    def __init__(self, need_bytes):
        self.need_bytes = need_bytes


def build_preprocessor(graph, mode="auto", k=16, memory_limit_bytes=None,
                       on_overflow="degrade", seed=0):
    """按内存预算构建预处理器。

    mode:
      - "apsp": 全点对表；装不下则按 on_overflow 拒绝或降级到 ALT；
      - "alt":  路标 A*；装不下则削减 k，连 1 个路标都放不下时降级到朴素；
      - "none": 直接朴素；
      - "auto": 能放 APSP（且 n<=4096）就用 APSP，否则用 ALT。

    返回的对象一定可用；on_overflow="raise" 时在彻底无解的情况下抛
    MemoryError，且不申请超限内存。
    """
    if on_overflow not in ("degrade", "raise"):
        raise ValueError("on_overflow must be 'degrade' or 'raise'")
    n = graph.n

    if mode == "none":
        return NaivePreprocessor(graph)

    if mode in ("apsp", "auto"):
        apsp_wanted = mode == "apsp"
        fits = memory_limit_bytes is None or n * n * _EIGHT_BYTES <= memory_limit_bytes
        if fits and (apsp_wanted or n <= 4096):
            return APSPPreprocessor(graph, memory_limit_bytes, on_overflow)
        if apsp_wanted and on_overflow == "raise":
            raise MemoryError(
                "APSP needs %d bytes, limit is %s"
                % (n * n * _EIGHT_BYTES, memory_limit_bytes)
            )

    per_landmark = n * ALTPreprocessor.BYTES_PER_LANDMARK_PER_NODE
    feasible_k = k
    if memory_limit_bytes is not None:
        feasible_k = memory_limit_bytes // per_landmark
    if feasible_k >= 1:
        chosen_k = min(k, feasible_k) if memory_limit_bytes is not None else k
        return ALTPreprocessor(graph, chosen_k, memory_limit_bytes,
                               on_overflow, seed)

    if on_overflow == "raise":
        raise MemoryError(
            "not enough budget for even 1 landmark (%d bytes, limit %s)"
            % (per_landmark, memory_limit_bytes)
        )
    return NaivePreprocessor(graph)


def total_cost(query_count, preprocess_cost, query_cost):
    """总成本 = 预处理代价 + 查询次数 * 单次查询代价（时间或钱，单位一致即可）。"""
    return preprocess_cost + query_count * query_cost


def break_even_queries(preprocess_cost, naive_query_cost, fast_query_cost):
    """临界查询次数：低于它不划算，高于它预处理回本。

    解 P + q*A = q*D，得 q* = P / (D - A)。加速为零或更慢时返回 inf。
    """
    gain = naive_query_cost - fast_query_cost
    if gain <= 0:
        return INF
    return preprocess_cost / gain

"""ALT preprocessing: A* + Landmarks + Triangle inequality.

Preprocessing picks L landmarks (farthest-point sampling) and stores, for
every landmark l and vertex v, the exact distances d(l, v) and d(v, l).
A point-to-point query is then A* whose heuristic

    h(v, t) = max( max_l d(l,t) - d(l,v),  max_l d(v,l) - d(t,l),  0 )

is consistent (triangle inequality), so A* returns the *exact* shortest
distance while settling far fewer vertices than plain Dijkstra.

Memory cost is 2 * L * n * 8 bytes (float64 tables) plus the CSR graph.
A memory budget can be enforced up front; on overflow the index either
reduces L, falls back to naive Dijkstra, or raises -- never gets OOM-killed.
"""
import heapq
import random
from array import array

from .dijkstra import INF, dijkstra

BYTES_PER_DIST = 8  # array('d')


class MemoryBudgetExceeded(Exception):
    """Raised when the estimated index size exceeds the configured budget
    and the policy is ``raise``."""


def estimate_index_bytes(num_nodes, num_landmarks):
    """Resident bytes of the two landmark distance tables."""
    return 2 * num_landmarks * num_nodes * BYTES_PER_DIST


def estimate_total_bytes(graph, num_landmarks):
    """Index tables + CSR graph + one scratch distance row used at build."""
    return (estimate_index_bytes(graph.n, num_landmarks)
            + _fixed_bytes(graph))


def _fixed_bytes(graph):
    """Graph + build-time scratch row + persistent query scratch buffers."""
    return (graph.memory_bytes()
            + graph.n * BYTES_PER_DIST
            + 2 * graph.n * (BYTES_PER_DIST + 8))


class ALTIndex:
    """Preprocessed exact shortest-path oracle.

    Parameters
    ----------
    graph : Graph
    num_landmarks : int
    mem_budget : int or None
        Byte budget for (index + graph + scratch). None = unlimited.
    on_exceed : "reduce" | "fallback" | "raise"
        reduce   -> shrink L to the largest value fitting the budget
                    (L == 0 degrades to naive Dijkstra, same as fallback)
        fallback -> keep L = 0, i.e. answer with naive Dijkstra
        raise    -> raise MemoryBudgetExceeded
    seed : int
        RNG seed for landmark selection (reproducibility).
    """

    def __init__(self, graph, num_landmarks=16, mem_budget=None,
                 on_exceed="reduce", seed=12345):
        if on_exceed not in ("reduce", "fallback", "raise"):
            raise ValueError("unknown on_exceed policy")
        self.graph = graph
        self.degraded = False
        self.landmarks = []
        self.d_from = []  # d_from[i][v] = dist(landmark_i, v)
        self.d_to = []    # d_to[i][v]   = dist(v, landmark_i)
        # persistent per-query scratch (stamped, avoids O(n) reset per query)
        self._dist = None
        self._dist_gen = None
        self._hval = None
        self._hval_gen = None
        self._gen = 0
        self._tables_finite = False

        requested = max(0, int(num_landmarks))
        if mem_budget is not None:
            affordable = self._max_affordable_landmarks(graph, mem_budget)
            if requested > affordable:
                if on_exceed == "raise":
                    raise MemoryBudgetExceeded(
                        "need ~%d bytes for %d landmarks, budget is %d"
                        % (estimate_total_bytes(graph, requested),
                           requested, mem_budget))
                if on_exceed == "fallback":
                    requested = 0
                else:  # reduce
                    requested = affordable
        if requested == 0 and num_landmarks > 0:
            self.degraded = True
        self._build(requested, random.Random(seed))

    @staticmethod
    def _max_affordable_landmarks(graph, budget):
        per_lm = 2 * graph.n * BYTES_PER_DIST
        fixed = _fixed_bytes(graph)
        if budget < fixed:
            return 0
        return (budget - fixed) // per_lm

    # ------------------------------------------------------------------ build
    def _build(self, num_landmarks, rng):
        g = self.graph
        n = g.n
        if num_landmarks == 0:
            return
        # Farthest-point (maxmin) landmark selection. The forward Dijkstra
        # run during selection doubles as the d_from table: 2L Dijkstras
        # total instead of 3L.
        first = rng.randrange(n)
        self.landmarks.append(first)
        d = dijkstra(g, first)
        self.d_from.append(array('d', d))
        min_dist = list(d)
        while len(self.landmarks) < num_landmarks:
            best_v, best_d = -1, -1.0
            for v in range(n):
                dv = min_dist[v]
                if dv == INF:
                    best_v = v          # unreachable: perfect new landmark
                    break
                if dv > best_d:
                    best_v, best_d = v, dv
            if best_v < 0:
                break
            self.landmarks.append(best_v)
            d = dijkstra(g, best_v)
            self.d_from.append(array('d', d))
            if best_d != INF:
                for v in range(n):
                    if d[v] < min_dist[v]:
                        min_dist[v] = d[v]
            else:
                min_dist[best_v] = 0.0
        for lm in self.landmarks:
            self.d_to.append(array('d', dijkstra(g, lm, reverse=True)))
        # if no landmark distance is infinite, queries can skip inf checks
        self._tables_finite = all(
            INF not in row for row in self.d_from + self.d_to)

    @property
    def num_landmarks(self):
        return len(self.landmarks)

    def memory_bytes(self):
        return estimate_index_bytes(self.graph.n, self.num_landmarks)

    # ------------------------------------------------------------------ query
    def query(self, s, t):
        """Exact shortest distance s -> t (inf if unreachable)."""
        if s == t:
            return 0.0
        if not self.landmarks:
            return dijkstra(self.graph, s, target=t)
        return self._astar(s, t)

    def _ensure_scratch(self):
        n = self.graph.n
        if self._dist is None or len(self._dist) != n:
            self._dist = array('d', [INF]) * n
            self._dist_gen = array('q', [0]) * n
            self._hval = array('d', [0.0]) * n
            self._hval_gen = array('q', [0]) * n
            self._gen = 0

    def _astar(self, s, t):
        self._ensure_scratch()
        g = self.graph
        self._gen += 1
        gen = self._gen
        dist = self._dist
        dist_gen = self._dist_gen
        hval = self._hval
        hval_gen = self._hval_gen
        d_from, d_to = self.d_from, self.d_to
        # per-query constants: landmark distances to/from the target
        terms = [(df, dt, df[t], dt[t]) for df, dt in zip(d_from, d_to)]

        if self._tables_finite:
            def heuristic(v):
                best = 0.0
                for df, dt, xf, xt in terms:
                    diff = xf - df[v]
                    if diff > best:
                        best = diff
                    diff = dt[v] - xt
                    if diff > best:
                        best = diff
                return best
        else:
            def heuristic(v):
                best = 0.0
                for df, dt, xf, xt in terms:
                    dfv = df[v]
                    if dfv != INF and xf != INF:
                        diff = xf - dfv
                        if diff > best:
                            best = diff
                    dtv = dt[v]
                    if dtv != INF and xt != INF:
                        diff = dtv - xt
                        if diff > best:
                            best = diff
                return best

        dist_gen[s] = gen
        dist[s] = 0.0
        pq = [(heuristic(s), 0.0, s)]
        hval_gen[s] = gen
        hval[s] = pq[0][0]
        push = heapq.heappush
        pop = heapq.heappop
        offsets, targets, weights = g.offsets, g.targets, g.weights
        while pq:
            _, du, u = pop(pq)
            if dist_gen[u] != gen or du > dist[u]:
                continue
            if u == t:
                return du
            for i in range(offsets[u], offsets[u + 1]):
                nd = du + weights[i]
                v = targets[i]
                if dist_gen[v] != gen or nd < dist[v]:
                    dist_gen[v] = gen
                    dist[v] = nd
                    if hval_gen[v] != gen:
                        hval_gen[v] = gen
                        hv = heuristic(v)
                        hval[v] = hv
                    else:
                        hv = hval[v]
                    push(pq, (nd + hv, nd, v))
        return INF

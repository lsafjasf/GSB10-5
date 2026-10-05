"""最大割 (Max-Cut) 问题与邻域。

解：x[i] ∈ {0,1}，目标 max sum_{(u,v,w) in edges} w * [x[u] != x[v]]。

两种邻域：
- FlipNeighborhood   (N1)：单点翻转，枚举序 i=0..n-1，大小 n。
- FlipPairNeighborhood (N2)：单点翻转 + 同时翻转两个不同点，
  枚举序为 (i) 升序，随后 (i,j) 按 i 升、j 升（i<j），
  大小 n + C(n,2) = n(n+1)/2（N1 ⊂ N2）。

两者都用 first-improvement：沿固定枚举序返回第一个严格改进移动；
因此平局永远选枚举序最小者，规则确定、同种子可复现。
"""

from .engine import BudgetExceeded


class MaxCut:
    def __init__(self, n, edges):
        # edges: [(u, v, weight)]，顶点编号 0..n-1
        self.n = n
        self.edges = edges
        self.adj = [[] for _ in range(n)]
        for u, v, w in edges:
            self.adj[u].append((v, w))
            self.adj[v].append((u, w))

    def objective(self, x):
        n, adj = self.n, self.adj
        total = 0
        for u in range(n):
            xu = x[u]
            for v, w in adj[u]:
                if v > u and xu != x[v]:
                    total += w
        return total

    def gain_flip(self, x, i):
        g = 0
        xi = x[i]
        for v, w in self.adj[i]:
            if xi == x[v]:
                g += w   # 同边变割边
            else:
                g -= w   # 割边变同边
        return g

    def flip(self, x, i):
        x[i] ^= 1

    def random_solution(self, rng):
        return [rng.randrange(2) for _ in range(self.n)]

    def make_flip(self, x):
        return FlipNeighborhood(self, x)

    def make_pair(self, x):
        return FlipPairNeighborhood(self, x)


class FlipNeighborhood:
    """N1 单点翻转，大小 n，first-improvement。"""

    name = "flip(n)"

    def __init__(self, problem, x):
        self.p = problem
        self.x = x

    def enum_size(self):
        return self.p.n

    def find_move(self, probe):
        x, p = self.x, self.p
        for i in range(p.n):
            probe.tick()
            d = p.gain_flip(x, i)
            if d > 0:
                return (i,), d
        return None

    def apply(self, move):
        (i,) = move
        self.x[i] ^= 1


class FlipPairNeighborhood:
    """N2 单点翻转 + 双点翻转，大小 n(n+1)/2，first-improvement。"""

    name = "flip+pair(n(n+1)/2)"

    def __init__(self, problem, x):
        self.p = problem
        self.x = x

    def enum_size(self):
        return self.p.n * (self.p.n + 1) // 2

    def find_move(self, probe):
        x, p, n, adj = self.x, self.p, self.p.n, self.p.adj
        # 先扫描单点翻转，保持与 N1 相同的路径
        for i in range(n):
            probe.tick()
            d = p.gain_flip(x, i)
            if d > 0:
                return (i,), d
        # 再扫描双点翻转；u,v 之间若有边，该边始终不被割到，权重要扣掉
        for u in range(n - 1):
            xu = x[u]
            au = adj[u]
            for v in range(u + 1, n):
                probe.tick()
                xv = x[v]
                d = 0
                for q, w in au:
                    if q == v:
                        continue
                    if xu == x[q]:
                        d += w
                    else:
                        d -= w
                for q, w in adj[v]:
                    if q == u:
                        continue        # (u,v) 边两端同时翻转，割/同状态不变
                    elif xv == x[q]:
                        d += w
                    else:
                        d -= w
                if d > 0:
                    return (u, v), d
        return None

    def apply(self, move):
        for i in move:
            self.x[i] ^= 1


def exhaustive_best(problem):
    """小规模穷举最优（n<=24 左右），以及一个最优解。格雷码顺序 O(2^n)。"""
    n = problem.n
    x = [0] * n
    best = problem.objective(x)
    best_x = x[:]
    for code in range(1, 1 << n):
        bit = (code & -code).bit_length() - 1   # 本步翻转的位
        x[bit] ^= 1
        val = problem.objective(x)
        if val > best:
            best = val
            best_x = x[:]
    return best, best_x


def edwards_erdos_bound(problem):
    """Edwards-Erdos 下界：每个非平凡连通分量至少 floor((m+w? )/2...)。

    无权图（边权均为 1）：分量 (v 点,e 边) 至少 floor(e/2)+(v-1)//4（整数形式
    的 m/2+(n-1)/4 下界）。加权图没有简单整数结论时返回总边权/2 的保底下界。
    """
    n, adj = problem.n, problem.adj
    if any(w != 1 for _, _, w in problem.edges):
        return edge_weight_half(problem)   # 加权图退回保底下界
    seen = [False] * n
    total_bound = 0
    for s in range(n):
        if seen[s]:
            continue
        seen[s] = True
        stack = [s]
        verts = 0
        edge_weight = 0
        while stack:
            u = stack.pop()
            verts += 1
            for v, w in adj[u]:
                edge_weight += w
                if not seen[v]:
                    seen[v] = True
                    stack.append(v)
        e = edge_weight // 2  # 每条边被两个端点各计一次
        if verts >= 2:
            total_bound += e // 2 + (verts - 1) // 4
    return total_bound


def edge_weight_half(problem):
    return sum(w for _, _, w in problem.edges) // 2


def random_er_graph(n, p, seed, max_weight=1):
    """Erdos-Renyi 随机图（简单图），权重 1..max_weight。"""
    import random
    rng = random.Random(seed)
    edges = []
    for u in range(n):
        for v in range(u + 1, n):
            if rng.random() < p:
                w = rng.randrange(1, max_weight + 1)
                edges.append((u, v, w))
    return MaxCut(n, edges)

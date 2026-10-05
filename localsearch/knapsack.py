"""0-1 背包问题与邻域（强约束情形：可行性约束 sum w_i x_i <= C）。

解：x[i] ∈ {0,1}，目标 max sum v_i x_i，s.t. sum w_i x_i <= C。

两种邻域（只含可行移动，first-improvement，枚举序固定）：
- AddNeighborhood  (K1)：把一个未选物品加入背包（需不超重），大小 n-k。
- SwapNeighborhood (K2)：K1 的加一操作 + 用未选物品 j 替换已选物品 i，
  枚举序为所有 add(j)（j 升序），随后 swap(i,j)（i 升、j 升），
  大小 (n-k) + k(n-k) = (n-k)(k+1)（K1 ⊂ K2）。

初始解：随机子集 + 修复（按价值/重量比从低到高删除直至可行），
保证初始解可行且留有松弛，使两种邻域从同一起点出发、路径可比。
"""

from fractions import Fraction


class Knapsack:
    def __init__(self, values, weights, capacity):
        assert len(values) == len(weights)
        self.n = len(values)
        self.values = list(values)
        self.weights = list(weights)
        self.capacity = capacity

    def objective(self, x):
        return sum(v for v, xi in zip(self.values, x) if xi)

    def total_weight(self, x):
        return sum(w for w, xi in zip(self.weights, x) if xi)

    def is_feasible(self, x):
        return self.total_weight(x) <= self.capacity

    def random_solution(self, rng):
        """随机子集 + 按性价比从低到高修复到可行。"""
        n = self.n
        x = [1 if rng.random() < 0.5 else 0 for _ in range(n)]
        load = self.total_weight(x)
        if load > self.capacity:
            order = sorted((i for i in range(n) if x[i]),
                           key=lambda i: (Fraction(self.values[i], self.weights[i]), i))
            for i in order:
                if load <= self.capacity:
                    break
                x[i] = 0
                load -= self.weights[i]
        return x

    def lp_bound(self):
        """LP 松弛上界（分数背包），用 Fraction 精确计算。"""
        order = sorted(range(self.n),
                       key=lambda i: (Fraction(self.values[i], self.weights[i]), -i),
                       reverse=True)
        bound = Fraction(0)
        remaining = self.capacity
        for i in order:
            w = self.weights[i]
            if w <= remaining:
                bound += self.values[i]
                remaining -= w
            else:
                bound += Fraction(self.values[i] * remaining, w)
                break
        return bound

    def make_add(self, x):
        return AddNeighborhood(self, x)

    def make_swap(self, x):
        return SwapNeighborhood(self, x)


class _KnapsackBase:
    def __init__(self, problem, x):
        self.p = problem
        self.x = x
        self.load = problem.total_weight(x)
        self._refresh()

    def _refresh(self):
        self.selected = [i for i in range(self.p.n) if self.x[i]]
        self.unselected = [i for i in range(self.p.n) if not self.x[i]]

    def apply(self, move):
        x, p = self.x, self.p
        if len(move) == 1:
            (j,) = move
            x[j] = 1
            self.load += p.weights[j]
        else:
            i, j = move
            x[i] = 0
            x[j] = 1
            self.load += p.weights[j] - p.weights[i]
        self._refresh()


class AddNeighborhood(_KnapsackBase):
    """K1 加一邻域，大小 n-k，first-improvement。"""

    name = "add(n-k)"

    def enum_size(self):
        return len(self.unselected)

    def find_move(self, probe):
        p, slack = self.p, self.p.capacity - self.load
        values, weights = p.values, p.weights
        for j in self.unselected:
            probe.tick()
            if weights[j] <= slack and values[j] > 0:
                return (j,), values[j]
        return None


class SwapNeighborhood(_KnapsackBase):
    """K2 加一 + 交换邻域，大小 (n-k)(k+1)，first-improvement。"""

    name = "add+swap((n-k)(k+1))"

    def enum_size(self):
        return len(self.unselected) * (len(self.selected) + 1)

    def find_move(self, probe):
        p, slack = self.p, self.p.capacity - self.load
        values, weights = p.values, p.weights
        unselected = self.unselected
        # 先扫描所有加一移动，保持与 K1 相同的路径
        for j in unselected:
            probe.tick()
            if weights[j] <= slack and values[j] > 0:
                return (j,), values[j]
        # 再扫描交换移动：delta = v_j - v_i，可行当 load - w_i + w_j <= C
        selected = self.selected
        for i in selected:
            vi, wi = values[i], weights[i]
            room = slack + wi
            for j in unselected:
                probe.tick()
                if weights[j] <= room:
                    d = values[j] - vi
                    if d > 0:
                        return (i, j), d
        return None


def exhaustive_best(problem):
    """小规模穷举最优（DFS + 简单剪枝），返回 (最优值, 一个最优解)。"""
    n = problem.n
    values, weights, capacity = problem.values, problem.weights, problem.capacity
    best = [0]
    best_x = [None]
    x = [0] * n

    def dfs(i, load, value):
        if value > best[0] and load <= capacity:
            best[0] = value
            best_x[0] = x[:]
        if i == n:
            return
        # 不选 i
        dfs(i + 1, load, value)
        # 选 i
        if load + weights[i] <= capacity:
            x[i] = 1
            dfs(i + 1, load + weights[i], value + values[i])
            x[i] = 0

    dfs(0, 0, 0)
    return best[0], best_x[0]


def random_instance(n, seed, capacity_ratio=0.5, correlated=False):
    """随机背包实例。correlated=True 时价值与重量强相关（更难）。"""
    import random
    rng = random.Random(seed)
    weights = [rng.randrange(1, 101) for _ in range(n)]
    if correlated:
        values = [w + rng.randrange(1, 11) for w in weights]
    else:
        values = [rng.randrange(1, 101) for _ in range(n)]
    capacity = max(1, int(sum(weights) * capacity_ratio))
    return Knapsack(values, weights, capacity)

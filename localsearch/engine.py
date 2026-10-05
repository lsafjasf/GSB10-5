"""局部搜索通用引擎（仅标准库）。

设计约定（保证同种子可复现）：
- 所有邻域按固定顺序枚举移动；first-improvement 返回扫描到的第一个改进移动；
  best-improvement 在所有改进移动中取 delta 最大者，delta 平局时取枚举序最小者。
- 停止条件分两类：
  * max_evals：确定性预算（按评估次数），同种子结果完全可复现；
  * deadline ：墙钟时间预算（秒），结果与机器速度有关，不保证跨机复现。
- 预算检查按批进行（每 CHECK_EVERY 次评估检查一次），避免每次循环都调时钟。
"""

from dataclasses import dataclass, field
from fractions import Fraction
import statistics
import time

CHECK_EVERY = 4096  # 每多少次评估检查一次预算


class BudgetExceeded(Exception):
    """预算耗尽时由邻域内部抛出，引擎捕获后返回当前解。"""


@dataclass
class Budget:
    max_evals: int = None      # 确定性评估次数预算（None = 不限）
    deadline: float = None     # 墙钟截止时间（time.perf_counter 基准，None = 不限）

    @classmethod
    def from_seconds(cls, seconds):
        return cls(deadline=time.perf_counter() + seconds)


@dataclass
class SearchResult:
    solution: object
    value: object
    evaluations: int = 0
    moves: int = 0
    converged: bool = False    # True = 自然收敛到局部最优；False = 被预算截断
    wall_time: float = 0.0


class Probe:
    """邻域在扫描过程中用来记账与检查预算的探针。"""

    __slots__ = ("budget", "evaluations")

    def __init__(self, budget):
        self.budget = budget
        self.evaluations = 0

    def tick(self, n=1):
        self.evaluations += n
        b = self.budget
        # 评估次数预算是确定性的，逐次精确检查（整数比较，开销可忽略）
        if b.max_evals is not None and self.evaluations >= b.max_evals:
            raise BudgetExceeded
        # 墙钟检查按批进行，避免频繁调时钟
        if b.deadline is not None and self.evaluations % CHECK_EVERY == 0:
            self.check()

    def check(self):
        b = self.budget
        if b.max_evals is not None and self.evaluations >= b.max_evals:
            raise BudgetExceeded
        if b.deadline is not None and time.perf_counter() >= b.deadline:
            raise BudgetExceeded


def local_search(problem, solution, neighborhood, budget=None):
    """通用爬山：反复向邻域请求一个改进移动并应用，直到无改进或预算耗尽。

    problem      : 提供 objective(solution)
    solution     : 初始解（就地修改，调用方如需保留请先复制）
    neighborhood : 提供 find_move(probe) -> (move, delta) 或 None；apply(move)
    """
    budget = budget or Budget()
    probe = Probe(budget)
    value = problem.objective(solution)
    moves = 0
    converged = False
    t0 = time.perf_counter()
    try:
        while True:
            found = neighborhood.find_move(probe)
            if found is None:
                converged = True
                break
            move, delta = found
            neighborhood.apply(move)
            value += delta
            moves += 1
    except BudgetExceeded:
        pass
    return SearchResult(solution=solution, value=value,
                        evaluations=probe.evaluations, moves=moves,
                        converged=converged, wall_time=time.perf_counter() - t0)


def multistart(problem, neighborhood_factory, restarts, seed, budget_per_start=None):
    """多次随机重启局部搜索，返回 SearchResult 列表（长度 = restarts）。

    neighborhood_factory(rng) -> (initial_solution, neighborhood)
    每次重启用 seed+i 派生独立随机流，保证同 seed 下整批结果可复现。
    """
    import random
    results = []
    for i in range(restarts):
        rng = random.Random(f"{seed}:{i}")
        sol, nb = neighborhood_factory(rng)
        results.append(local_search(problem, sol, nb, budget_per_start))
    return results


def summarize(results):
    """多次运行的结果分布：最好 / 最差 / 中位数 / 均值 / 标准差 / 总耗时。"""
    values = [r.value for r in results]
    return {
        "runs": len(results),
        "best": max(values),
        "worst": min(values),
        "median": statistics.median(values),
        "mean": statistics.mean(values),
        "stdev": statistics.pstdev(values) if len(values) > 1 else 0.0,
        "total_wall": sum(r.wall_time for r in results),
        "evaluations": [r.evaluations for r in results],
    }

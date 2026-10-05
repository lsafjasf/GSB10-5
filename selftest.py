#!/usr/bin/env python3
"""局部搜索库自测：邻域对比、结果分布、与下界/最优值的差距、耗时对比。

运行：python3 selftest.py
场景：
  S1 小规模可穷举（Max-Cut n=16 / 背包 n=18，与穷举最优对比，含可复现性校验）
  S2 中等规模（Max-Cut n=200 对 Edwards-Erdos 下界 / 背包 n=300 对 LP 上界）
  S3 强约束（背包 n=200，容量比 0.1，价值重量强相关）
  S4 1000 个变量：不同邻域收敛后的耗时与目标值对比
  S5 时间预算很紧（Max-Cut n=1000，每次重启仅 1 秒墙钟预算）
"""

import random
import statistics
import time

from localsearch import Budget, local_search, maxcut, knapsack


def run_scenario(problem, make_nb, restarts, seed, budget_factory=None):
    """多次随机重启；返回 (results, 末态邻域枚举大小列表)。"""
    results, sizes = [], []
    for i in range(restarts):
        rng = random.Random(f"{seed}:{i}")
        sol = problem.random_solution(rng)
        nb = make_nb(sol)
        budget = budget_factory() if budget_factory else None
        results.append(local_search(problem, sol, nb, budget))
        sizes.append(nb.enum_size())
    return results, sizes


def dist(values):
    return (max(values), min(values), statistics.median(values))


def print_rows(tag, neighborhoods, reference_desc, reference, gap_kind):
    """打印一个场景的对比表。gap_kind: 'opt' | 'lower' | 'upper'"""
    print(f"\n### {tag}")
    print(f"    参照: {reference_desc} = {reference}")
    header = (f"    {'邻域':<22}{'枚举大小(末态中位)':>16}{'best':>10}{'worst':>10}"
              f"{'median':>10}{'差距(median)':>14}{'相对差距':>10}{'总耗时':>9}")
    print(header)
    for name, results, sizes in neighborhoods:
        values = [r.value for r in results]
        best, worst, med = dist(values)
        if gap_kind == 'lower':      # 参照是下界：解在其上方，差距 = median - ref
            gap = med - reference
        else:                        # 参照是上界或最优：差距 = ref - median
            gap = reference - med
        rel = abs(gap) / reference if reference else 0.0
        total_t = sum(r.wall_time for r in results)
        med_size = statistics.median(sizes)
        print(f"    {name:<22}{med_size:>16.0f}{best:>10}{worst:>10}{med:>10.1f}"
              f"{gap:>14.1f}{rel:>9.2%}{total_t:>8.2f}s")


def verify_converged(problem, results, make_nb_from):
    """校验：目标值记账正确、收敛解确为局部最优。"""
    for r in results:
        assert r.converged
        assert problem.objective(r.solution) == r.value, "增量记账与重算不一致"
        nb = make_nb_from(r.solution)
        class _NoBudget:
            evaluations = 0
            def tick(self, n=1):
                self.evaluations += n
            def check(self):
                pass
        assert nb.find_move(_NoBudget()) is None, "收敛解仍存在改进移动"


def s1_small_exhaustive():
    print("=" * 78)
    print("S1 小规模可穷举：与穷举最优值对比 + 可复现性校验")
    print("=" * 78)

    # --- Max-Cut n=16 ---
    g = maxcut.random_er_graph(16, 0.5, seed=101)
    opt, _ = maxcut.exhaustive_best(g)
    seed = 9001
    rows = []
    for name, mk in [("flip(n)", g.make_flip), ("flip+pair", g.make_pair)]:
        res, sizes = run_scenario(g, mk, restarts=10, seed=seed)
        res2, _ = run_scenario(g, mk, restarts=10, seed=seed)
        assert [r.value for r in res] == [r.value for r in res2], "同种子结果不可复现"
        assert all(r.solution == r2.solution for r, r2 in zip(res, res2)), "同种子解不一致"
        verify_converged(g, res, mk)
        rows.append((name, res, sizes))
    print_rows("Max-Cut n=16, p=0.5, 10 次重启", rows, "穷举最优 OPT", opt, 'opt')

    # 确定性评估次数预算：同种子 + 同 max_evals 必须逐解一致
    def _budgeted(seed_str):
        rng = random.Random(seed_str)
        x = g.random_solution(rng)
        return local_search(g, x, g.make_pair(x), Budget(max_evals=120)).solution
    a, b = _budgeted("budget-x:0"), _budgeted("budget-x:0")
    assert a == b, "固定评估次数预算下同种子结果不可复现"

    # --- 背包 n=18 ---
    kp = knapsack.random_instance(18, seed=202)
    optk, _ = knapsack.exhaustive_best(kp)
    rows = []
    for name, mk in [("add(n-k)", kp.make_add), ("add+swap", kp.make_swap)]:
        res, sizes = run_scenario(kp, mk, restarts=10, seed=seed)
        res2, _ = run_scenario(kp, mk, restarts=10, seed=seed)
        assert [r.value for r in res] == [r.value for r in res2], "同种子结果不可复现"
        verify_converged(kp, res, mk)
        rows.append((name, res, sizes))
    print_rows("背包 n=18, 容量比 0.5, 10 次重启", rows, "穷举最优 OPT", optk, 'opt')
    print(f"    (参考: LP 松弛上界 = {float(kp.lp_bound()):.1f})")


def s2_medium():
    print("\n" + "=" * 78)
    print("S2 中等规模：与已知界对比（Max-Cut 对 Edwards-Erdos 下界，背包对 LP 上界）")
    print("=" * 78)

    g = maxcut.random_er_graph(200, 0.1, seed=303)
    lb = maxcut.edwards_erdos_bound(g)
    rows = []
    for name, mk in [("flip(n)", g.make_flip), ("flip+pair", g.make_pair)]:
        res, sizes = run_scenario(g, mk, restarts=10, seed=9101)
        verify_converged(g, res, mk)
        rows.append((name, res, sizes))
    print_rows(f"Max-Cut n=200, p=0.1, m={len(g.edges)}, 10 次重启",
               rows, "Edwards-Erdos 下界 LB", lb, 'lower')

    kp = knapsack.random_instance(300, seed=404)
    ub = kp.lp_bound()
    rows = []
    for name, mk in [("add(n-k)", kp.make_add), ("add+swap", kp.make_swap)]:
        res, sizes = run_scenario(kp, mk, restarts=10, seed=9201)
        verify_converged(kp, res, mk)
        rows.append((name, res, sizes))
    print_rows("背包 n=300, 容量比 0.5, 10 次重启", rows,
               "LP 松弛上界 UB", float(ub), 'upper')


def s3_strong_constraint():
    print("\n" + "=" * 78)
    print("S3 强约束：背包 n=200，容量比 0.1，价值与重量强相关")
    print("=" * 78)
    kp = knapsack.random_instance(200, seed=505, capacity_ratio=0.1, correlated=True)
    ub = kp.lp_bound()
    rows = []
    for name, mk in [("add(n-k)", kp.make_add), ("add+swap", kp.make_swap)]:
        res, sizes = run_scenario(kp, mk, restarts=10, seed=9301)
        verify_converged(kp, res, mk)
        rows.append((name, res, sizes))
    print_rows("背包 n=200, 容量比 0.1, 强相关, 10 次重启", rows,
               "LP 松弛上界 UB", float(ub), 'upper')


def s4_thousand_vars():
    print("\n" + "=" * 78)
    print("S4 1000 个变量：不同邻域收敛后的耗时与目标值对比")
    print("=" * 78)

    g = maxcut.random_er_graph(1000, 0.05, seed=606)
    lb = maxcut.edwards_erdos_bound(g)
    rows = []
    for name, mk in [("flip(n)", g.make_flip), ("flip+pair", g.make_pair)]:
        res, sizes = run_scenario(g, mk, restarts=5, seed=9401)
        rows.append((name, res, sizes))
    print_rows(f"Max-Cut n=1000, p=0.05, m={len(g.edges)}, 5 次重启（均收敛）",
               rows, "Edwards-Erdos 下界 LB", lb, 'lower')
    for name, res, _ in rows:
        ts = [r.wall_time for r in res]
        print(f"    {name:<22}单次耗时 median={statistics.median(ts):.2f}s "
              f"min={min(ts):.2f}s max={max(ts):.2f}s")

    kp = knapsack.random_instance(1000, seed=707, capacity_ratio=0.5)
    ub = kp.lp_bound()
    rows = []
    for name, mk in [("add(n-k)", kp.make_add), ("add+swap", kp.make_swap)]:
        res, sizes = run_scenario(kp, mk, restarts=5, seed=9501)
        rows.append((name, res, sizes))
    print_rows("背包 n=1000, 容量比 0.5, 5 次重启（均收敛）", rows,
               "LP 松弛上界 UB", float(ub), 'upper')
    for name, res, _ in rows:
        ts = [r.wall_time for r in res]
        print(f"    {name:<22}单次耗时 median={statistics.median(ts):.2f}s "
              f"min={min(ts):.2f}s max={max(ts):.2f}s")


def s5_tight_budget():
    print("\n" + "=" * 78)
    print("S5 时间预算很紧：Max-Cut n=1000，每次重启仅 1.0 秒墙钟预算")
    print("    （墙钟预算与机器速度有关，不保证跨机复现；固定评估次数预算才可复现）")
    print("=" * 78)
    g = maxcut.random_er_graph(1000, 0.05, seed=606)
    lb = maxcut.edwards_erdos_bound(g)
    rows = []
    for name, mk in [("flip(n)", g.make_flip), ("flip+pair", g.make_pair)]:
        res, sizes = run_scenario(g, mk, restarts=5, seed=9601,
                                  budget_factory=lambda: Budget.from_seconds(1.0))
        rows.append((name, res, sizes))
    print_rows(f"Max-Cut n=1000, p=0.05, 5 次重启 x 1.0s 预算", rows,
               "Edwards-Erdos 下界 LB", lb, 'lower')
    for name, res, _ in rows:
        conv = sum(r.converged for r in res)
        evals = statistics.median(r.evaluations for r in res)
        print(f"    {name:<22}收敛次数 {conv}/5, 单次评估次数中位 {evals:.0f}")


def main():
    t0 = time.perf_counter()
    s1_small_exhaustive()
    s2_medium()
    s3_strong_constraint()
    s4_thousand_vars()
    s5_tight_budget()
    print(f"\n全部自测通过，总耗时 {time.perf_counter() - t0:.1f}s")


if __name__ == "__main__":
    main()

"""基准：10 万点 / 50 万边 的预处理耗时、查询耗时、内存峰值，
以及链状图、内存受限、查询极少/极多场景，输出总代价曲线与对拍数据。

运行：python3 benchmark.py
输出：results/benchmark_report.md, results/cost_curve.csv, results/cross_check.csv
"""
import csv
import math
import os
import random
import resource
import time
import tracemalloc

from sppre import (
    Graph,
    break_even_queries,
    build_preprocessor,
    dijkstra_pair,
    total_cost,
)

N_BIG, M_BIG = 100_000, 500_000
K_LANDMARKS = 16
NAIVE_SAMPLES = 20      # 大图上朴素 Dijkstra 很慢，抽样后取均值
FAST_SAMPLES = 500
RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


def peak_rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def random_graph(n, m, seed):
    g = Graph(n)
    rng = random.Random(seed)
    order = list(range(n))
    rng.shuffle(order)
    for i in range(n - 1):  # 保证存在一条贯穿全图的路径
        g.add_edge(order[i], order[i + 1], rng.randint(1, 100))
    for _ in range(m - (n - 1)):
        g.add_edge(rng.randrange(n), rng.randrange(n), rng.randint(1, 100))
    return g


def chain_graph(n, seed, extra_ratio=0.01):
    g = Graph(n)
    rng = random.Random(seed)
    for i in range(n - 1):
        w = rng.randint(1, 10)
        g.add_edge(i, i + 1, w)
        g.add_edge(i + 1, i, w)
    for _ in range(int(n * extra_ratio)):
        g.add_edge(rng.randrange(n), rng.randrange(n), rng.randint(1, 50))
    return g


def sample_pairs(n, count, seed):
    rng = random.Random(seed)
    return [(rng.randrange(n), rng.randrange(n)) for _ in range(count)]


def bench_queries(graph, pairs, query_fn):
    """返回 (平均秒, 逐对结果列表)。"""
    results = []
    start = time.perf_counter()
    for s, t in pairs:
        results.append(query_fn(s, t))
    elapsed = time.perf_counter() - start
    return elapsed / len(pairs), results


def run_scenario(name, graph, k, memory_limit=None, on_overflow="degrade",
                 naive_samples=NAIVE_SAMPLES, fast_samples=FAST_SAMPLES):
    print("=" * 70)
    print("场景: %s  (n=%d, m=%d)" % (name, graph.n, graph.m))
    rss0 = peak_rss_mb()
    tracemalloc.start()
    t0 = time.perf_counter()
    pre = build_preprocessor(graph, mode="alt", k=k,
                             memory_limit_bytes=memory_limit,
                             on_overflow=on_overflow, seed=42)
    pre_time = time.perf_counter() - t0
    _, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss1 = peak_rss_mb()
    print("  模式=%s  实际路标数 k=%d" % (pre.mode, pre.k))
    print("  预处理耗时: %.2f s" % pre_time)
    print("  预处理内存: 距离表 %d B (%.1f MB), tracemalloc 峰值 %.1f MB, "
          "进程 RSS 增量 %.1f MB"
          % (pre.table_bytes, pre.table_bytes / 1e6, py_peak / 1e6, rss1 - rss0))

    naive_pairs = sample_pairs(graph.n, naive_samples, seed=1)
    fast_pairs = sample_pairs(graph.n, fast_samples, seed=2)

    naive_avg, naive_res = bench_queries(graph, naive_pairs, lambda s, t: dijkstra_pair(graph, s, t))
    if pre.mode == "none":
        # 已退化为朴素：同一批点对、同一算法，直接复用实测耗时
        fast_pairs = naive_pairs
        fast_avg, fast_res = naive_avg, naive_res
    else:
        fast_avg, fast_res = bench_queries(graph, fast_pairs, pre.query)

    # 对拍：同一批 naive_pairs 用两种算法各跑一遍
    mismatches = 0
    cross_rows = []
    for (s, t), expected in zip(naive_pairs, naive_res):
        got = pre.query(s, t)
        ok = (expected == got) or (math.isinf(expected) and math.isinf(got))
        mismatches += 0 if ok else 1
        cross_rows.append((name, s, t, expected, got, ok))
    print("  朴素 Dijkstra 单次均值: %.4f s (%d 次抽样)" % (naive_avg, naive_samples))
    print("  加速查询单次均值:     %.6f s (%d 次抽样)" % (fast_avg, fast_samples))
    if fast_avg > 0:
        print("  单次加速比: %.1fx" % (naive_avg / fast_avg))
    print("  对拍: %d/%d 一致, 不一致 %d" % (naive_samples - mismatches, naive_samples, mismatches))

    q_star = break_even_queries(pre_time, naive_avg, fast_avg)
    if math.isinf(q_star):
        print("  临界查询次数: 无（加速为零，预处理永远不划算）")
    else:
        print("  临界查询次数 q* = %.0f （查询少于此值预处理不划算）" % q_star)
    return {
        "name": name, "mode": pre.mode, "k": pre.k,
        "preprocess_s": pre_time, "table_bytes": pre.table_bytes,
        "py_peak_mb": py_peak / 1e6, "rss_delta_mb": rss1 - rss0,
        "naive_s": naive_avg, "fast_s": fast_avg, "q_star": q_star,
        "mismatches": mismatches, "cross_rows": cross_rows,
    }


def write_cost_curve(stats, path):
    """总代价曲线：T_naive(q)=q*D 与 T_pre(q)=P+q*A，q 取对数刻度。"""
    P, D, A = stats["preprocess_s"], stats["naive_s"], stats["fast_s"]
    rows = []
    q = 1
    while q <= 1_000_000:
        rows.append((q, total_cost(q, 0.0, D), total_cost(q, P, A)))
        q = int(q * 1.5) + 1
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["queries", "naive_total_s", "preprocessed_total_s"])
        w.writerows(rows)
    return rows


def main():
    os.makedirs(RESULTS, exist_ok=True)
    all_stats = []
    all_cross = []

    # 场景 1：10 万点 / 50 万边随机图
    print("生成随机图 n=%d, m=%d ..." % (N_BIG, M_BIG))
    g = random_graph(N_BIG, M_BIG, seed=2026)
    all_stats.append(run_scenario("random-100k-500k", g, K_LANDMARKS))

    # 场景 2：接近链状图（n=10 万，m≈20 万）
    print("生成链状图 n=%d ..." % N_BIG)
    gc = chain_graph(N_BIG, seed=7)
    all_stats.append(run_scenario("chain-100k", gc, K_LANDMARKS))

    # 场景 3：预处理内存不足（预算 4MB，远小于 k=16 所需的 25.6MB）
    all_stats.append(run_scenario("random-100k-lowmem-4MB", g, K_LANDMARKS,
                                  memory_limit=4 * 1024 * 1024,
                                  fast_samples=50, naive_samples=10))

    # 场景 3b：预算连 1 个路标都放不下 -> 彻底退化为朴素；raise 模式则拒绝
    all_stats.append(run_scenario("random-100k-lowmem-500KB", g, K_LANDMARKS,
                                  memory_limit=500 * 1024,
                                  fast_samples=20, naive_samples=10))
    try:
        build_preprocessor(g, mode="alt", k=K_LANDMARKS,
                           memory_limit_bytes=500 * 1024, on_overflow="raise")
        raise SystemExit("应当拒绝但没有拒绝")
    except MemoryError as exc:
        print("=" * 70)
        print("场景: raise 模式拒绝预处理（内存预算 500KB）")
        print("  MemoryError:", exc)

    # 场景 4：查询极少 vs 极多（用场景 1 的实测单价推算总成本）
    s1 = all_stats[0]
    P, D, A = s1["preprocess_s"], s1["naive_s"], s1["fast_s"]
    print("=" * 70)
    print("场景: 查询极少 vs 查询极多（基于 random-100k-500k 实测单价）")
    print("  %-10s %-16s %-16s %s" % ("查询次数 q", "朴素总成本(s)", "预处理总成本(s)", "更划算"))
    scenario_rows = []
    for q in (1, 10, 100, 1_000, 10_000, 100_000, 1_000_000):
        tn, tp = total_cost(q, 0.0, D), total_cost(q, P, A)
        winner = "朴素" if tn <= tp else "预处理"
        scenario_rows.append((q, tn, tp, winner))
        print("  %-10d %-16.2f %-16.2f %s" % (q, tn, tp, winner))

    # 输出文件
    curve_path = os.path.join(RESULTS, "cost_curve.csv")
    write_cost_curve(s1, curve_path)
    cross_path = os.path.join(RESULTS, "cross_check.csv")
    with open(cross_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scenario", "source", "target", "naive_dist", "preprocessed_dist", "match"])
        for st in all_stats:
            w.writerows(st["cross_rows"])
            all_cross.extend(st["cross_rows"])

    report_path = os.path.join(RESULTS, "benchmark_report.md")
    with open(report_path, "w") as f:
        f.write("# 基准报告\n\n")
        f.write("| 场景 | 模式 | k | 预处理(s) | 表内存(MB) | tracemalloc峰值(MB) | RSS增量(MB) | 朴素单次(s) | 加速单次(s) | 加速比 | 临界q* | 对拍不一致 |\n")
        f.write("|---|---|---|---|---|---|---|---|---|---|---|---|\n")
        for st in all_stats:
            speedup = st["naive_s"] / st["fast_s"] if st["fast_s"] > 0 else float("inf")
            qs = "%.0f" % st["q_star"] if not math.isinf(st["q_star"]) else "inf"
            f.write("| %s | %s | %d | %.2f | %.1f | %.1f | %.1f | %.4f | %.6f | %.1fx | %s | %d |\n"
                    % (st["name"], st["mode"], st["k"], st["preprocess_s"],
                       st["table_bytes"] / 1e6, st["py_peak_mb"], st["rss_delta_mb"],
                       st["naive_s"], st["fast_s"], speedup, qs, st["mismatches"]))
        f.write("\n## 查询极少 vs 极多（random-100k-500k 实测单价推算总成本）\n\n")
        f.write("| 查询次数 q | 朴素总成本(s) | 预处理总成本(s) | 更划算 |\n|---|---|---|---|\n")
        for q, tn, tp, winner in scenario_rows:
            f.write("| %d | %.2f | %.2f | %s |\n" % (q, tn, tp, winner))
        f.write("\n对拍总样本 %d 对，不一致 %d 对。\n"
                % (len(all_cross), sum(1 for r in all_cross if not r[5])))

    print("=" * 70)
    print("已写出: %s, %s, %s" % (report_path, curve_path, cross_path))
    print("进程峰值 RSS: %.1f MB" % peak_rss_mb())


if __name__ == "__main__":
    main()

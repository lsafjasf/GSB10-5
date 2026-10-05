"""基准：100 万点 + 10 万查询，输出耗时、内存峰值、单次查询耗时曲线。

运行：python3 benchmark.py
产出：data/latency_curve.csv（每次查询耗时）、data/benchmark_summary.json
"""

import csv
import json
import os
import random
import resource
import time

from range_count import RangeCounter, count_brute

N_POINTS = 1_000_000
N_QUERIES = 100_000
COORD = 1_000_000
SEED = 20261005
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def gen_points(n, rng):
    return [(rng.randint(-COORD, COORD), rng.randint(-COORD, COORD))
            for _ in range(n)]


def gen_queries(n, rng):
    """80% 普通 / 5% 空 / 10% 退化 / 5% 贴边界（坐标取点集已有值）。"""
    queries, kinds = [], []
    for _ in range(n):
        x1, x2 = sorted((rng.randint(-COORD, COORD), rng.randint(-COORD, COORD)))
        y1, y2 = sorted((rng.randint(-COORD, COORD), rng.randint(-COORD, COORD)))
        r = rng.random()
        if r < 0.05:
            x1, x2 = x2 + rng.randint(1, 1000), x2
            kind = "empty"
        elif r < 0.10:
            x2 = x1
            kind = "degenerate_w0"
        elif r < 0.15:
            y2 = y1
            kind = "degenerate_h0"
        elif r < 0.20:
            x1, x2 = -COORD, COORD   # 恰好贴住坐标全域边界
            kind = "boundary_hug"
        else:
            kind = "normal"
        queries.append((x1, y1, x2, y2))
        kinds.append(kind)
    return queries, kinds


def percentile(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = min(len(sorted_vals) - 1, max(0, int(round(p / 100.0 * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    rng = random.Random(SEED)

    t0 = time.perf_counter()
    points = gen_points(N_POINTS, rng)
    gen_s = time.perf_counter() - t0
    rss_before = rss_mb()

    t0 = time.perf_counter()
    rc = RangeCounter(points)
    build_s = time.perf_counter() - t0
    rss_after_build = rss_mb()

    queries, kinds = gen_queries(N_QUERIES, rng)

    # 抽样暴力校验（全量 1e5 x 1e6 暴力不可行，抽样 40 条 + 单测/对拍脚本兜底）
    rng2 = random.Random(SEED + 1)
    sample_idx = sorted(rng2.sample(range(N_QUERIES), 40))
    sample_checks = []
    for i in sample_idx:
        q = queries[i]
        a = rc.count(*q)
        b = count_brute(points, *q)
        assert a == b, (i, q, a, b)
        sample_checks.append({"index": i, "query": list(q), "index_result": a,
                              "brute_result": b})

    lat_us = []
    counts = []
    t_all = time.perf_counter()
    for q in queries:
        t = time.perf_counter()
        counts.append(rc.count(*q))
        lat_us.append((time.perf_counter() - t) * 1e6)
    query_s = time.perf_counter() - t_all
    rss_final = rss_mb()

    srt = sorted(lat_us)
    stats = {
        "n_points": N_POINTS, "n_queries": N_QUERIES, "seed": SEED,
        "coord_range": [-COORD, COORD],
        "gen_points_seconds": round(gen_s, 3),
        "build_seconds": round(build_s, 3),
        "query_total_seconds": round(query_s, 3),
        "query_mean_us": round(sum(lat_us) / len(lat_us), 2),
        "query_p50_us": round(percentile(srt, 50), 2),
        "query_p90_us": round(percentile(srt, 90), 2),
        "query_p99_us": round(percentile(srt, 99), 2),
        "query_max_us": round(srt[-1], 2),
        "rss_before_build_mb": round(rss_before, 1),
        "rss_after_build_mb": round(rss_after_build, 1),
        "rss_peak_mb": round(rss_final, 1),
        "index_structure_mb": round(rc.index_bytes() / 1e6, 1),
        "sample_brute_checks": sample_checks,
        "kind_counts": {k: kinds.count(k) for k in sorted(set(kinds))},
        "nonzero_results": sum(1 for c in counts if c > 0),
    }

    csv_path = os.path.join(DATA_DIR, "latency_curve.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["query_index", "kind", "latency_us", "result_count"])
        for i, (k, lat, c) in enumerate(zip(kinds, lat_us, counts)):
            w.writerow([i, k, f"{lat:.2f}", c])

    # 每 1000 个查询一个桶的均值曲线（用于报告里的耗时曲线）
    bins = []
    for start in range(0, N_QUERIES, 1000):
        chunk = lat_us[start:start + 1000]
        bins.append(round(sum(chunk) / len(chunk), 2))
    stats["latency_curve_bin1000_mean_us"] = bins

    with open(os.path.join(DATA_DIR, "benchmark_summary.json"), "w") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    print(json.dumps({k: v for k, v in stats.items()
                      if k != "sample_brute_checks"}, indent=2, ensure_ascii=False))
    print(f"sample brute checks: {len(sample_checks)} all match")
    print(f"written {csv_path} and benchmark_summary.json")


if __name__ == "__main__":
    main()

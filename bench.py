# -*- coding: utf-8 -*-
"""性能基准：默认 100 万点、10 万次查询。

输出（--outdir，默认 results/）：
  bench_summary.txt   构建/查询耗时、吞吐、分位数、内存峰值
  query_times.csv     每次查询耗时（微秒）
  query_time_curve.svg 单次查询耗时曲线（每桶均值 + 峰值）
并抽样用暴力扫描验证正确性（--verify 条，默认 10）。

用法: python3 bench.py [--n 1000000] [--q 100000] [--seed 42]
"""

import argparse
import os
import random
import resource
import time

from rcount import RangeCounter, brute_force_count


def rss_mb():
    # Linux: ru_maxrss 单位 KB
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def gen_points(n, seed):
    rng = random.Random(seed)
    return [(rng.uniform(0.0, 1000.0), rng.uniform(0.0, 1000.0))
            for _ in range(n)]


def gen_queries(q, seed):
    rng = random.Random(seed)
    queries = []
    for i in range(q):
        x1, x2 = rng.uniform(0, 1000), rng.uniform(0, 1000)
        y1, y2 = rng.uniform(0, 1000), rng.uniform(0, 1000)
        r = i % 20
        if r == 0:                       # 5% 零宽
            x2 = x1
        elif r == 1:                     # 5% 零高
            y2 = y1
        elif r == 2:                     # 5% 空矩形
            x1, x2 = max(x1, x2), min(x1, x2)
        elif r == 3:                     # 5% 全平面
            x1, y1, x2, y2 = 0.0, 0.0, 1000.0, 1000.0
        queries.append((min(x1, x2), min(y1, y2),
                        max(x1, x2), max(y1, y2)))
    return queries


def percentile(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = min(len(sorted_vals) - 1, max(0, int(p / 100.0 * len(sorted_vals))))
    return sorted_vals[k]


def write_svg(path, times_us, buckets=400, width=1000, height=320):
    n = len(times_us)
    step = max(1, n // buckets)
    means, maxs = [], []
    for s in range(0, n, step):
        chunk = times_us[s:s + step]
        means.append(sum(chunk) / len(chunk))
        maxs.append(max(chunk))
    top = max(maxs) or 1.0
    left, right, up, down = 60, 20, 20, 40
    pw, ph = width - left - right, height - up - down

    def pts(vals):
        out = []
        for i, v in enumerate(vals):
            x = left + pw * i / max(1, len(vals) - 1)
            y = up + ph * (1.0 - v / top)
            out.append(f"{x:.1f},{y:.1f}")
        return " ".join(out)

    grid = []
    for i in range(5):
        gy = up + ph * i / 4
        val = top * (1 - i / 4)
        grid.append(
            f'<line x1="{left}" y1="{gy:.1f}" x2="{left + pw}" y2="{gy:.1f}" '
            f'stroke="#ddd" stroke-width="1"/>'
            f'<text x="{left - 6}" y="{gy + 4:.1f}" font-size="11" '
            f'text-anchor="end" fill="#555">{val:.0f}</text>')
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" font-family="sans-serif">
<rect width="{width}" height="{height}" fill="white"/>
<text x="{left}" y="14" font-size="13" fill="#222">per-query time (us), {n} queries, bucket mean (blue) / bucket max (orange)</text>
{''.join(grid)}
<polyline fill="none" stroke="#d62728" stroke-width="1" points="{pts(maxs)}"/>
<polyline fill="none" stroke="#1f77b4" stroke-width="1.5" points="{pts(means)}"/>
<text x="{left + pw / 2:.0f}" y="{height - 8}" font-size="12" text-anchor="middle" fill="#555">query index (time order)</text>
</svg>'''
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1_000_000)
    ap.add_argument("--q", type=int, default=100_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--verify", type=int, default=10,
                    help="抽样用暴力扫描验证的查询条数")
    ap.add_argument("--outdir", default="results")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    print(f"generating {args.n} points ...")
    t0 = time.perf_counter()
    points = gen_points(args.n, args.seed)
    t_gen = time.perf_counter() - t0

    print("building index ...")
    rss0 = rss_mb()
    t0 = time.perf_counter()
    rc = RangeCounter(points)
    t_build = time.perf_counter() - t0
    rss1 = rss_mb()

    print(f"generating {args.q} queries ...")
    queries = gen_queries(args.q, args.seed + 1)

    # 抽样正确性验证（在计时前完成，避免污染曲线）
    mismatches = 0
    for rect in queries[:args.verify]:
        if rc.count(*rect) != brute_force_count(points, *rect):
            mismatches += 1

    print("running timed queries ...")
    times_us = []
    total_count = 0
    t_all0 = time.perf_counter()
    for rect in queries:
        tq0 = time.perf_counter()
        total_count += rc.count(*rect)
        times_us.append((time.perf_counter() - tq0) * 1e6)
    t_queries = time.perf_counter() - t_all0
    rss2 = rss_mb()

    st = sorted(times_us)
    avg = sum(times_us) / len(times_us)
    summary = [
        f"points_n            {args.n}",
        f"queries_q           {args.q}",
        f"point_gen_s         {t_gen:.3f}",
        f"build_s             {t_build:.3f}",
        f"queries_total_s     {t_queries:.3f}",
        f"queries_per_s       {args.q / t_queries:.0f}",
        f"query_avg_us        {avg:.2f}",
        f"query_p50_us        {percentile(st, 50):.2f}",
        f"query_p90_us        {percentile(st, 90):.2f}",
        f"query_p99_us        {percentile(st, 99):.2f}",
        f"query_max_us        {st[-1]:.2f}",
        f"rss_peak_mb         {rss2:.1f}",
        f"rss_before_build_mb {rss0:.1f}",
        f"rss_after_build_mb  {rss1:.1f}",
        f"verify_sample       {args.verify} queries, mismatches={mismatches}",
        f"total_count_check   {total_count}",
    ]
    text = "\n".join(summary) + "\n"
    print(text)

    with open(os.path.join(args.outdir, "bench_summary.txt"), "w",
              encoding="utf-8") as f:
        f.write(text)

    with open(os.path.join(args.outdir, "query_times.csv"), "w",
              encoding="utf-8") as f:
        f.write("query_index,time_us\n")
        for i, t in enumerate(times_us):
            f.write(f"{i},{t:.2f}\n")

    svg_path = os.path.join(args.outdir, "query_time_curve.svg")
    write_svg(svg_path, times_us)
    print("written: bench_summary.txt, query_times.csv, query_time_curve.svg")


if __name__ == "__main__":
    main()

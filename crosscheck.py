# -*- coding: utf-8 -*-
"""逐点暴力扫描对拍：多场景随机查询，输出对拍数据文件。

场景：均匀随机、点全在界内、点全在界外、大量边界点（整数网格）、
重复点密集、退化/空矩形混合，以及一组 10 万点的较大规模对拍。

用法: python3 crosscheck.py [--outdir results]
"""

import argparse
import os
import random

from rcount import RangeCounter, brute_force_count

EDGE_POINTS = [
    (0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0),   # 四个角
    (0.5, 0.0), (0.5, 1.0), (0.0, 0.5), (1.0, 0.5),   # 四条边中点
    (0.5, 0.5),                                        # 内部
]
EDGE_CASES = [
    # (描述, 矩形 x1,y1,x2,y2, 期望计数)
    ("闭矩形整体（四角四边都在内）", (0.0, 0.0, 1.0, 1.0), 9),
    ("下边退化线段", (0.0, 0.0, 1.0, 0.0), 3),
    ("上边退化线段", (0.0, 1.0, 1.0, 1.0), 3),
    ("左边退化线段", (0.0, 0.0, 0.0, 1.0), 3),
    ("右边退化线段", (1.0, 0.0, 1.0, 1.0), 3),
    ("左上角点矩形", (0.0, 1.0, 0.0, 1.0), 1),
    ("中心点矩形", (0.5, 0.5, 0.5, 0.5), 1),
    ("空矩形 x1>x2", (1.0, 0.0, 0.0, 1.0), 0),
    ("空矩形 y1>y2", (0.0, 1.0, 1.0, 0.0), 0),
    ("内部小闭矩形（含边界点）", (0.0, 0.0, 0.5, 0.5), 4),
]


def make_rect(rng, lo, hi, mode):
    x1, x2 = rng.uniform(lo, hi), rng.uniform(lo, hi)
    y1, y2 = rng.uniform(lo, hi), rng.uniform(lo, hi)
    if mode == 0:
        return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)
    if mode == 1:      # 零宽竖直线段
        x2 = x1
    elif mode == 2:    # 零高水平线段
        y2 = y1
    elif mode == 3:    # 点矩形
        x2, y2 = x1, y1
    elif mode == 4:    # 空（反向端点）
        return max(x1, x2), max(y1, y2), min(x1, x2), min(y1, y2)
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def scenario_points(name, seed):
    rng = random.Random(seed)
    if name == "uniform":
        return [(rng.uniform(-500, 500), rng.uniform(-500, 500))
                for _ in range(10000)]
    if name == "all_inside":
        return [(rng.uniform(-10, 10), rng.uniform(-10, 10))
                for _ in range(10000)]
    if name == "all_outside":
        return [(rng.uniform(80, 100), rng.uniform(80, 100))
                for _ in range(10000)]
    if name == "boundary_grid":
        # 整数网格点：查询边界对齐网格时大量点恰好落在边上。
        return [(float(rng.randint(0, 99)), float(rng.randint(0, 99)))
                for _ in range(10000)]
    if name == "duplicates":
        hot = [(1.0, 1.0), (2.0, 2.0), (-3.0, 3.0)]
        return [rng.choice(hot) for _ in range(10000)]
    if name == "large":
        return [(rng.uniform(-1e4, 1e4), rng.uniform(-1e4, 1e4))
                for _ in range(100000)]
    raise ValueError(name)


def scenario_query(name, rng, i):
    if name == "uniform":
        return make_rect(rng, -600, 600, i % 5)
    if name == "all_inside":
        return make_rect(rng, -20, 20, i % 5)            # 必然全覆盖
    if name == "all_outside":
        return make_rect(rng, -10, 70, i % 5)            # 必然全不覆盖
    if name == "boundary_grid":
        x1, x2 = sorted((rng.randint(0, 99), rng.randint(0, 99)))
        y1, y2 = sorted((rng.randint(0, 99), rng.randint(0, 99)))
        return float(x1), float(y1), float(x2), float(y2)
    if name == "duplicates":
        return make_rect(rng, -4, 4, i % 5)
    if name == "large":
        return make_rect(rng, -2e4, 2e4, i % 5)
    raise ValueError(name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="results")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    # ---- 边界用例（确定性）----
    rc = RangeCounter(EDGE_POINTS)
    edge_path = os.path.join(args.outdir, "edge_cases.tsv")
    with open(edge_path, "w", encoding="utf-8") as f:
        f.write("description\tx1\ty1\tx2\ty2\tfast_count\tbrute_count\tmatch\n")
        for desc, (x1, y1, x2, y2), expected in EDGE_CASES:
            got = rc.count(x1, y1, x2, y2)
            brute = brute_force_count(EDGE_POINTS, x1, y1, x2, y2)
            ok = (got == brute == expected)
            f.write(f"{desc}\t{x1}\t{y1}\t{x2}\t{y2}\t{got}\t{brute}\t{ok}\n")

    # ---- 随机对拍 ----
    configs = [
        ("uniform",       11, 1200),
        ("all_inside",    22, 300),
        ("all_outside",   33, 300),
        ("boundary_grid", 44, 1200),
        ("duplicates",    55, 1200),
        ("large",         66, 300),
    ]
    rows = []
    summary = []
    total_cmp = 0
    for name, seed, q in configs:
        points = scenario_points(name, seed)
        rc = RangeCounter(points)
        qrng = random.Random(seed + 10000)
        mismatches = 0
        for i in range(q):
            rect = scenario_query(name, qrng, i)
            fast = rc.count(*rect)
            brute = brute_force_count(points, *rect)
            match = (fast == brute)
            total_cmp += 1
            if not match:
                mismatches += 1
            rows.append((name, i, *rect, fast, brute, match))
        summary.append((name, len(points), q, mismatches))

    xcheck_path = os.path.join(args.outdir, "crosscheck.tsv")
    with open(xcheck_path, "w", encoding="utf-8") as f:
        f.write("scenario\tquery\tx1\ty1\tx2\ty2\tfast_count\t"
                "brute_count\tmatch\n")
        for r in rows:
            name, i, x1, y1, x2, y2, fast, brute, match = r
            f.write(f"{name}\t{i}\t{x1:.6f}\t{y1:.6f}\t{x2:.6f}\t{y2:.6f}"
                    f"\t{fast}\t{brute}\t{match}\n")

    sum_path = os.path.join(args.outdir, "crosscheck_summary.txt")
    with open(sum_path, "w", encoding="utf-8") as f:
        f.write("对拍汇总：RangeCounter vs 逐点暴力扫描\n")
        f.write("scenario\tn_points\tn_queries\tmismatches\n")
        for name, n, q, m in summary:
            f.write(f"{name}\t{n}\t{q}\t{m}\n")
        f.write(f"total_queries_compared\t{total_cmp}\n")
        f.write("verdict\t" + ("ALL MATCH" if all(m == 0 for _, _, _, m in
                                                  summary) else "MISMATCH")
                + "\n")

    for name, n, q, m in summary:
        print(f"{name:14s} n={n:6d} q={q:5d} mismatches={m}")
    print(f"compared {total_cmp} queries ->",
          "ALL MATCH" if all(m == 0 for _, _, _, m in summary) else "MISMATCH")
    print("written:", edge_path, xcheck_path, sum_path)


if __name__ == "__main__":
    main()

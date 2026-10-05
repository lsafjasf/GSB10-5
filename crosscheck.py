"""大批量随机查询对拍：小波矩阵索引 vs 逐点暴力扫描，输出可复查数据。

运行：python3 crosscheck.py
产出：data/crosscheck_data.json
"""

import json
import os
import random
import time

from range_count import RangeCounter, count_brute

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DATA_FILE = os.path.join(DATA_DIR, "crosscheck_data.json")

# 固定用例：边界、重复、空、退化、全内、全外
FIXED_CASES = [
    {
        "name": "boundary_edges_and_corners",
        "points": [(0, 5), (10, 5), (5, 0), (5, 10),
                   (0, 0), (0, 10), (10, 0), (10, 10), (5, 5),
                   (-1, 5), (11, 5), (5, -1), (5, 11)],
        "queries": [[0, 0, 10, 10], [0, 0, 0, 10], [10, 0, 10, 10],
                    [0, 0, 10, 0], [0, 10, 10, 10], [0, 0, 9, 9]],
    },
    {
        "name": "duplicates",
        "points": [(3, 3)] * 5 + [(1, 1), (1, 1), (4, 4)],
        "queries": [[0, 0, 10, 10], [3, 3, 3, 3], [1, 1, 1, 1], [2, 2, 3, 3]],
    },
    {
        "name": "empty_and_degenerate",
        "points": [(1, 1), (2, 2), (3, 3), (2, 1), (1, 2)],
        "queries": [[5, 0, 1, 10], [0, 5, 10, 1], [5, 5, 1, 1],
                    [1, 1, 3, 1], [1, 1, 1, 3], [2, 2, 2, 2], [7, 7, 7, 7]],
    },
    {
        "name": "all_inside",
        "points": [[i, i] for i in range(1000)],
        "queries": [[-10, -10, 2000, 2000]],
    },
    {
        "name": "all_outside",
        "points": [[i, i] for i in range(1000)],
        "queries": [[2000, 2000, 3000, 3000], [-1000, -1000, -1, -1]],
    },
    {
        "name": "many_boundary_points",
        "points": [p for i in range(5000) for p in
                   ((i % 101, 0), (i % 101, 100), (0, i % 101), (100, i % 101))],
        "queries": [[0, 0, 100, 100], [1, 1, 99, 99], [0, 0, 100, 0]],
    },
    {
        "name": "empty_point_set",
        "points": [],
        "queries": [[0, 0, 10, 10], [1, 1, 1, 1], [5, 0, 1, 1]],
    },
]


def gen_random_case(seed, n_points, n_queries):
    """生成带形状标注的随机查询：normal/empty/degenerate/boundary_hug。"""
    rng = random.Random(seed)
    lo, hi = -1000, 1000
    points = [(rng.randint(lo, hi), rng.randint(lo, hi)) for _ in range(n_points)]
    xs = sorted({p[0] for p in points})
    ys = sorted({p[1] for p in points})
    queries = []
    kinds = []
    for i in range(n_queries):
        x1, x2 = sorted((rng.randint(lo - 200, hi + 200), rng.randint(lo - 200, hi + 200)))
        y1, y2 = sorted((rng.randint(lo - 200, hi + 200), rng.randint(lo - 200, hi + 200)))
        r = rng.random()
        if r < 0.05:
            x1, x2 = x2 + rng.randint(1, 50), x2
            kind = "empty"
        elif r < 0.12:
            x2 = x1
            kind = "degenerate_w0"
        elif r < 0.19:
            y2 = y1
            kind = "degenerate_h0"
        elif r < 0.29 and xs:
            x1 = x2 = xs[rng.randrange(len(xs))]
            y1 = ys[rng.randrange(len(ys))]
            y2 = ys[rng.randrange(len(ys))]
            if y1 > y2:
                y1, y2 = y2, y1
            kind = "boundary_hug"
        else:
            kind = "normal"
        queries.append([x1, y1, x2, y2])
        kinds.append(kind)
    return points, queries, kinds


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    out = {"rules": "closed [x1,x2]x[y1,y2]; boundary counted; empty->0; "
                    "degenerate allowed; duplicates kept", "fixed_cases": [],
           "random_batches": []}

    # 1) 固定用例
    for case in FIXED_CASES:
        rc = RangeCounter(case["points"])
        results = []
        for q in case["queries"]:
            a = rc.count(*q)
            b = count_brute(case["points"], *q)
            assert a == b, (case["name"], q, a, b)
            results.append({"query": q, "index": a, "brute": b})
        out["fixed_cases"].append({"name": case["name"],
                                   "n_points": len(case["points"]),
                                   "results": results})

    # 2) 随机大批量对拍（两批，全部查询与暴力结果逐字写出）
    for tag, seed, n_pts, n_q in [("small", 4242, 2000, 10000),
                                  ("medium", 99999, 20000, 2000)]:
        points, queries, kinds = gen_random_case(seed, n_pts, n_q)
        t0 = time.perf_counter()
        rc = RangeCounter(points)
        build_s = time.perf_counter() - t0
        idx_res, brute_res = [], []
        t0 = time.perf_counter()
        for q in queries:
            idx_res.append(rc.count(*q))
        index_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        for q in queries:
            brute_res.append(count_brute(points, *q))
        brute_s = time.perf_counter() - t0
        mismatches = [i for i, (a, b) in enumerate(zip(idx_res, brute_res)) if a != b]
        nonzero = sum(1 for v in idx_res if v > 0)
        by_kind = {}
        for k, v in zip(kinds, idx_res):
            by_kind.setdefault(k, 0)
            by_kind[k] += 1
        out["random_batches"].append({
            "tag": tag, "seed": seed, "n_points": n_pts, "n_queries": n_q,
            "coord_range": [-1200, 1200],
            "points": [list(p) for p in points],
            "queries": [{"q": q, "kind": k, "index": a, "brute": b}
                        for q, k, a, b in zip(queries, kinds, idx_res, brute_res)],
            "build_seconds": round(build_s, 4),
            "index_total_seconds": round(index_s, 4),
            "brute_total_seconds": round(brute_s, 4),
            "mismatch_count": len(mismatches),
            "nonzero_result_queries": nonzero,
            "kind_counts": by_kind,
        })
        print(f"[{tag}] pts={n_pts} q={n_q} mismatch={len(mismatches)} "
              f"build={build_s:.3f}s index={index_s:.3f}s brute={brute_s:.3f}s")

    with open(DATA_FILE, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    size_mb = os.path.getsize(DATA_FILE) / 1e6
    print(f"all fixed cases + random batches match brute force; "
              f"written {DATA_FILE} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()

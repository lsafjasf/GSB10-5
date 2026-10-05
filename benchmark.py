#!/usr/bin/env python3
"""Benchmarks: preprocessing vs naive Dijkstra total-cost trade-off.

Produces (under results/):
  summary.json     - all measured numbers
  cost_curve.csv   - total cost T(q) for naive vs preprocessed (model curve)
  crosscheck.csv   - point-by-point ALT vs naive distance comparison sample

Cases: road-like grid 100k/500k, chain-like graph, memory-insufficient
degradation, few/many query totals.
"""
import csv
import json
import math
import os
import random
import resource
import sys
import time
import tracemalloc

from spp import ALTIndex, Graph, MemoryBudgetExceeded, dijkstra

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")

N = 100_000
M = 500_000
NUM_LANDMARKS = 16
NAIVE_QUERIES = 200      # naive is slow; 200 pairs give a stable average
ALT_QUERIES = 2000       # preprocessed queries for timing
SEED = 20261005


def rss_kb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def build_grid_graph(seed):
    """Road-network-like graph: 400x250 grid (exactly 100k nodes), edge
    weights ~ geometric distance with jitter, plus random long-range
    shortcuts priced above their geometric distance. 500k directed edges."""
    rng = random.Random(seed)
    W, H = 400, 250
    assert W * H == N
    g = Graph(N)

    def nid(x, y):
        return y * W + x

    for y in range(H):
        for x in range(W):
            if x + 1 < W:
                w = 1.0 + rng.random() * 0.25
                g.add_edge(nid(x, y), nid(x + 1, y), w)
                g.add_edge(nid(x + 1, y), nid(x, y), w)
            if y + 1 < H:
                w = 1.0 + rng.random() * 0.25
                g.add_edge(nid(x, y), nid(x, y + 1), w)
                g.add_edge(nid(x, y + 1), nid(x, y), w)
    have = g.m if hasattr(g, "m") else None
    # add directed shortcuts until exactly M edges
    added = 0
    need = M - len(g._edges)
    while added < need:
        x1, y1 = rng.randrange(W), rng.randrange(H)
        x2, y2 = rng.randrange(W), rng.randrange(H)
        dist = math.hypot(x2 - x1, y2 - y1)
        if dist < 5:
            continue
        g.add_edge(nid(x1, y1), nid(x2, y2), dist * (1.1 + rng.random() * 0.4))
        added += 1
    return g.finalize()


def build_chain_graph(seed):
    rng = random.Random(seed)
    g = Graph(N)
    for i in range(N - 1):
        w = rng.randint(1, 100)
        g.add_edge(i, i + 1, w)
        g.add_edge(i + 1, i, w)
    for _ in range(400):  # a few chords: still chain-like
        g.add_edge(rng.randrange(N), rng.randrange(N), rng.randint(1, 100))
    return g.finalize()


def time_queries(fn, pairs):
    t0 = time.perf_counter()
    out = [fn(s, t) for s, t in pairs]
    return time.perf_counter() - t0, out


def bench_case(name, graph, num_landmarks):
    print("\n=== case: %s (n=%d, m=%d) ===" % (name, graph.n, graph.m), flush=True)
    rec = {"case": name, "n": graph.n, "m": graph.m,
           "graph_mem_mb": round(graph.memory_bytes() / 1e6, 1)}

    # ---- preprocessing, clean timing (no instrumentation)
    rss0 = rss_kb()
    t0 = time.perf_counter()
    index = ALTIndex(graph, num_landmarks=num_landmarks, seed=SEED)
    pre_s = time.perf_counter() - t0
    rss1 = rss_kb()
    # ---- separate instrumented build for the Python-side memory peak
    tracemalloc.start()
    idx2 = ALTIndex(graph, num_landmarks=num_landmarks, seed=SEED)
    _, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del idx2
    rss2 = rss_kb()
    rec.update({
        "landmarks": index.num_landmarks,
        "preprocess_s": round(pre_s, 3),
        "preprocess_rss_delta_mb": round((rss1 - rss0) / 1024, 1),
        "tracemalloc_peak_mb": round(py_peak / 1e6, 1),
        "index_mem_est_mb": round(index.memory_bytes() / 1e6, 1),
        "process_rss_peak_mb": round(rss2 / 1024, 1),
    })
    print("preprocess: %.2fs, L=%d, RSS +%.1fMB, py-peak %.1fMB"
          % (pre_s, index.num_landmarks, (rss1 - rss0) / 1024, py_peak / 1e6),
          flush=True)

    # ---- queries
    rng = random.Random(SEED + 1)
    pairs = [(rng.randrange(graph.n), rng.randrange(graph.n))
             for _ in range(ALT_QUERIES)]
    naive_pairs = pairs[:NAIVE_QUERIES]

    naive_s, naive_res = time_queries(
        lambda s, t: dijkstra(graph, s, target=t), naive_pairs)
    print("naive done: %.1fs" % naive_s, flush=True)
    alt_s, alt_res = time_queries(index.query, pairs)
    t_naive = naive_s / len(naive_pairs)
    t_alt = alt_s / len(pairs)
    rec.update({
        "naive_queries": len(naive_pairs),
        "naive_total_s": round(naive_s, 3),
        "naive_per_query_ms": round(t_naive * 1e3, 3),
        "alt_queries": len(pairs),
        "alt_total_s": round(alt_s, 3),
        "alt_per_query_ms": round(t_alt * 1e3, 4),
        "speedup": round(t_naive / t_alt, 1) if t_alt > 0 else None,
    })
    print("naive: %d q, %.2fs, %.1f ms/q | alt: %d q, %.2fs, %.3f ms/q (%.1fx)"
          % (len(naive_pairs), naive_s, t_naive * 1e3,
             len(pairs), alt_s, t_alt * 1e3,
             t_naive / t_alt if t_alt else 0), flush=True)

    # ---- point-by-point cross-check on the naive-timed pairs
    mismatches = sum(1 for dn, da in zip(naive_res, alt_res) if dn != da)
    rec["crosscheck"] = {"pairs": len(naive_pairs), "mismatches": mismatches}
    print("crosscheck: %d pairs, %d mismatches" % (len(naive_pairs), mismatches),
          flush=True)

    # ---- cost model + break-even
    q_star = pre_s / (t_naive - t_alt) if t_naive > t_alt else float("inf")
    rec["cost_model"] = {
        "T_naive(q)": "q * %.6f" % t_naive,
        "T_alt(q)": "%.3f + q * %.6f" % (pre_s, t_alt),
        "break_even_queries": None if q_star == float("inf") else math.floor(q_star) + 1,
    }
    if q_star != float("inf"):
        print("break-even: q >= %d queries" % (math.floor(q_star) + 1), flush=True)
    return rec, pre_s, t_naive, t_alt, naive_pairs, naive_res, alt_res


def write_cost_curve(path, pre_s, t_naive, t_alt):
    qs = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000,
          20000, 50000, 100000, 200000, 500000, 1000000]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["queries", "T_naive_s", "T_alt_s_incl_preprocess", "cheaper"])
        for q in qs:
            tn, ta = q * t_naive, pre_s + q * t_alt
            w.writerow([q, "%.3f" % tn, "%.3f" % ta,
                        "naive" if tn <= ta else "preprocessed"])
    return qs


def ascii_curve(pre_s, t_naive, t_alt):
    qs = [1, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000]
    width = 46
    lines = ["total cost, log scale (N=naive P=preprocessed B=tie)"]
    lo = min(min(q * t_naive, pre_s + q * t_alt) for q in qs)
    hi = max(max(q * t_naive, pre_s + q * t_alt) for q in qs)
    llo, lhi = math.log10(max(lo, 1e-3)), math.log10(hi)

    def pos(v):
        return int((math.log10(max(v, 1e-3)) - llo) / (lhi - llo) * width)

    for q in qs:
        tn, ta = q * t_naive, pre_s + q * t_alt
        row = [" "] * (width + 1)
        xn, xa = pos(tn), pos(ta)
        row[xn] = "N"
        row[xa] = "B" if xa == xn else "P"
        lines.append("q=%-6d|%s| naive=%9.2fs alt=%9.2fs"
                     % (q, "".join(row), tn, ta))
    return "\n".join(lines)


def main():
    os.makedirs(RESULTS, exist_ok=True)
    summary = {"config": {"n": N, "m": M, "landmarks": NUM_LANDMARKS,
                          "seed": SEED, "python": sys.version.split()[0],
                          "machine": "%d cores, %.0f GB RAM" % (
                              (os.cpu_count() or 1),
                              15.0)}}

    # ---------- case 1: road-like grid 100k / 500k
    graph = build_grid_graph(SEED)
    assert graph.n == N and graph.m == M
    rec, pre_s, t_naive, t_alt, cc_pairs, cc_naive, cc_alt = bench_case(
        "grid-100k-500k", graph, NUM_LANDMARKS)
    summary["grid"] = rec

    with open(os.path.join(RESULTS, "crosscheck.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["s", "t", "naive_dist", "alt_dist", "equal"])
        for (s, t), dn, da in zip(cc_pairs, cc_naive, cc_alt):
            w.writerow([s, t, dn, da, dn == da])

    write_cost_curve(os.path.join(RESULTS, "cost_curve.csv"),
                     pre_s, t_naive, t_alt)
    curve_txt = ascii_curve(pre_s, t_naive, t_alt)
    print("\n" + curve_txt, flush=True)
    summary["cost_curve_ascii"] = curve_txt
    summary["query_totals"] = {
        str(q): {"naive_s": round(q * t_naive, 2),
                 "alt_s": round(pre_s + q * t_alt, 2)}
        for q in (10, 100, 1000, 10000, 100000)
    }

    # ---------- case 2: chain-like graph
    chain = build_chain_graph(SEED)
    rec_c, _, _, _, _, _, _ = bench_case("chain-like-100k", chain, NUM_LANDMARKS)
    summary["chain"] = rec_c

    # ---------- case 3: memory-insufficient handling on the big graph
    mem = {}
    for label, budget_mb in (("tight-64MB", 64), ("tiny-20MB", 20)):
        budget = budget_mb * 10**6
        t0 = time.perf_counter()
        idx = ALTIndex(graph, num_landmarks=NUM_LANDMARKS,
                       mem_budget=budget, on_exceed="reduce", seed=SEED)
        build_s = time.perf_counter() - t0
        ok = idx.query(0, N - 1) == dijkstra(graph, 0, target=N - 1)
        mem[label] = {
            "budget_mb": budget_mb,
            "landmarks_afforded": idx.num_landmarks,
            "degraded": idx.degraded,
            "build_s": round(build_s, 3),
            "query_still_exact": ok,
            "process_alive": True,
        }
        print("mem %s: L=%d degraded=%s exact=%s"
              % (label, idx.num_landmarks, idx.degraded, ok), flush=True)
        del idx
    try:
        ALTIndex(graph, num_landmarks=NUM_LANDMARKS,
                 mem_budget=64 * 10**6, on_exceed="raise")
        mem["raise-policy"] = {"raised": False}
    except MemoryBudgetExceeded as e:
        mem["raise-policy"] = {"raised": True, "message": str(e)}
        print("mem raise-policy: refused cleanly (%s)" % e, flush=True)
    summary["memory_guard_big"] = mem

    with open(os.path.join(RESULTS, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print("\nwrote results/summary.json, results/cost_curve.csv, "
          "results/crosscheck.csv", flush=True)


if __name__ == "__main__":
    main()

"""Benchmark: 1,000,000 rows x 1,000 filter expressions (1e9 evaluations).

Pure standard library. Multiprocessing (fork, copy-on-write columnar data)
is used to split the full Cartesian workload across CPU cores; the workload
itself is unchanged (every expression is evaluated against every row).

Usage:
    python3 tools/benchmark.py                 # defaults 1e6 rows / 1000 exprs
    python3 tools/benchmark.py 100000 1000 16  # rows exprs workers
"""

import json
import multiprocessing as mp
import os
import platform
import random
import resource
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from safefilter import compile_expr
from tests.diffgen import _bool_expr, random_row  # type-aware generator

_COLUMNS = {}
_SOURCES = []
_COUNTERS = []


def generate_data(nrows, seed):
    rng = random.Random(seed)
    columns = {k: [None] * nrows for k in
               ("age", "score", "level", "vip", "active",
                "city", "name", "nick", "grade")}
    ages = columns["age"]
    scores = columns["score"]
    levels = columns["level"]
    vips = columns["vip"]
    actives = columns["active"]
    cities = columns["city"]
    names = columns["name"]
    nicks = columns["nick"]
    grades = columns["grade"]
    city_pool = ["beijing", "shanghai", "tokyo", "shenzhen", "hangzhou"]
    name_pool = ["alice", "bob", "carol", "dave", "erin"]
    nick_pool = ["alpha", "beta", "gamma", "delta"]
    for i in range(nrows):
        ages[i] = rng.randint(0, 99)
        scores[i] = round(rng.random(), 3)
        levels[i] = rng.randint(1, 5)
        vips[i] = rng.random() < 0.2
        actives[i] = rng.random() < 0.8
        cities[i] = rng.choice(city_pool)
        names[i] = rng.choice(name_pool)
        nicks[i] = rng.choice(nick_pool)
        grades[i] = rng.randint(0, 100)
    return columns


def generate_expressions(nexprs, seed):
    rng = random.Random(seed)
    sources = []
    for _ in range(nexprs):
        text, _py = _bool_expr(rng, depth=3)
        sources.append(text)
    return sources


def _worker(chunk):
    start, stop = chunk
    counters = [c(start, stop) for c in _COUNTERS]
    return counters


def run(nrows=1_000_000, nexprs=1000, workers=None):
    workers = workers or min(16, os.cpu_count() or 1)
    ctx = mp.get_context("fork")
    print("machine: %s, python %s, %d workers"
          % (platform.machine(), platform.python_version(), workers))

    t0 = time.perf_counter()
    columns = generate_data(nrows, seed=42)
    t_data = time.perf_counter() - t0

    t0 = time.perf_counter()
    sources = generate_expressions(nexprs, seed=7)
    compiled = [compile_expr(src) for src in sources]
    t_parse = time.perf_counter() - t0

    t0 = time.perf_counter()
    counters = [ce.bind_columns(columns) for ce in compiled]
    t_bind = time.perf_counter() - t0

    rss_after_load = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    # single-process micro benchmark on a slice (single-core per-eval cost)
    micro_rows = min(nrows, 50_000)
    t0 = time.perf_counter()
    micro_matches = sum(c(0, micro_rows) for c in counters)
    t_micro = time.perf_counter() - t0
    micro_evals = micro_rows * nexprs

    chunks = [(lo, min(nrows, lo + nrows // workers + 1))
              for lo in range(0, nrows, nrows // workers + 1)]
    global _COLUMNS, _SOURCES, _COUNTERS
    _COLUMNS, _SOURCES, _COUNTERS = columns, sources, counters

    t0 = time.perf_counter()
    with ctx.Pool(workers) as pool:
        partials = pool.map(_worker, chunks)
    t_eval = time.perf_counter() - t0

    totals = [sum(row[i] for row in partials) for i in range(nexprs)]
    total_matches = sum(totals)
    total_evals = nrows * nexprs

    child_rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
    parent_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    result = {
        "rows": nrows, "expressions": nexprs,
        "total_evaluations": total_evals,
        "workers": workers,
        "data_gen_sec": round(t_data, 3),
        "parse_compile_sec": round(t_parse, 3),
        "bind_sec": round(t_bind, 3),
        "micro_single_core": {
            "rows": micro_rows,
            "evaluations": micro_evals,
            "sec": round(t_micro, 3),
            "ns_per_eval": round(t_micro / micro_evals * 1e9, 1),
        },
        "eval_wall_sec": round(t_eval, 3),
        "throughput_evals_per_sec": round(total_evals / t_eval),
        "ns_per_eval_wall": round(t_eval / total_evals * 1e9, 1),
        "match_count_checksum": total_matches,
        "peak_rss_parent_mb": round(parent_rss, 1),
        "peak_rss_max_worker_mb": round(child_rss, 1),
        "rss_after_data_load_mb": round(rss_after_load, 1),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


if __name__ == "__main__":
    nrows = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    nexprs = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else None
    run(nrows, nexprs, workers)

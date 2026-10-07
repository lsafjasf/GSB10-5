"""Benchmark: 100k tasks, 24h downtime. Run: python3 bench.py

Measures plan-generation wall time and memory peak (tracemalloc), plus a
full streaming merge of the plan to prove the backlog can be drained without
materialising it. Writes reports/bench_100k.json.
"""
from __future__ import annotations

import json
import os
import resource
import sys
import time
import tracemalloc

import scenarios as sc
from catchup import MissedFire, Task, build_initial

NUM_TASKS = 100_000
DOWN = (sc.BASE, sc.BASE + sc.DAY)  # one full day


def make_tasks(n=NUM_TASKS):
    """Mixed fleet: 10% every minute, 40% every 5min, 40% hourly, 10% daily."""
    tasks = []
    for i in range(n):
        r = i % 10
        period = 60 if r == 0 else 300 if r < 5 else 3600 if r < 9 else sc.DAY
        tasks.append(Task(f"task-{i:06d}", period=period, anchor=i % period))
    return tasks


def measure(fn):
    tracemalloc.start()
    t0 = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, elapsed, peak


def main():
    tasks = make_tasks()

    plan, build_s, build_peak = measure(lambda: build_initial(tasks, *DOWN))
    total, count_s, _ = measure(lambda: plan.total)

    def drain():
        n = 0
        last = None
        for fire in plan.iter_plan():
            n += 1
            last = fire
        return n, last

    # Timed without tracemalloc (its per-allocation tracing would dominate);
    # memory peak taken from the process high-water mark instead.
    gc_baseline = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.perf_counter()
    merged, last_fire = drain()
    merge_s = time.perf_counter() - t0
    merge_peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    merge_peak = max(0, merge_peak_kb - gc_baseline) * 1024
    assert merged == total, (merged, total)

    naive_bytes = total * (sys.getsizeof(MissedFire(0, "x")) + 8)
    payload = {
        "scenario": "100k tasks, 24h downtime",
        "python": sys.version.split()[0],
        "num_tasks": NUM_TASKS,
        "fleet_mix": {"every_60s": 10000, "every_300s": 40000,
                      "every_3600s": 40000, "every_86400s": 10000},
        "window": {"down_at_utc": sc.fmt(DOWN[0]), "up_at_utc": sc.fmt(DOWN[1]),
                   "hours": 24},
        "total_compensations": total,
        "plan_generation": {
            "wall_seconds": round(build_s, 4),
            "memory_peak_bytes": build_peak,
            "memory_peak_mb": round(build_peak / 1e6, 2),
            "note": "O(num_tasks): one backlog descriptor per task, fires stay lazy",
        },
        "counting": {"wall_seconds": round(count_s, 4)},
        "full_streaming_merge": {
            "wall_seconds": round(merge_s, 3),
            "memory_peak_bytes": merge_peak,
            "memory_peak_mb": round(merge_peak / 1e6, 2),
            "items_streamed": merged,
            "note": "one heap entry per task, advanced with heapreplace; memory "
                    "stays O(num_tasks), no 27M-item list is ever built "
                    "(memory delta measured via ru_maxrss high-water mark)",
        },
        "naive_materialisation_estimate": {
            "bytes": naive_bytes,
            "mb": round(naive_bytes / 1e6, 1),
            "note": "what a list of all MissedFire objects would cost (estimated)",
        },
        "drain_time_examples": {
            "at_1000_per_sec_hours": round(total / 1000 / 3600, 2),
            "at_10000_per_sec_hours": round(total / 10000 / 3600, 2),
            "note": "why the rate cap matters: the backlog is ~311x the live "
                    "load, uncapped catch-up would be a self-inflicted spike",
        },
        "last_fire": {"key": last_fire.key, "fire_at": int(last_fire.fire_at)},
    }
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "reports", "bench_100k.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()

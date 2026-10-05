"""1000 个并行用例的准备/回收耗时与内存峰值基准，产出 reports/bench_1000.json。"""

import json
import os
import resource
import shutil
import sys
import tempfile
import threading
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from testenv import IsolatedEnv, destroy_all, sweep_residue

N = 1000
WORKERS = 64
REPORTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")


def peak_rss_mb():
    # Linux: ru_maxrss 单位 KB
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def run_once(base):
    tracemalloc.start()
    rss_before = peak_rss_mb()

    # ---- 准备阶段：1000 个用例并行创建环境、分配端口、写私有状态 ----
    t0 = time.perf_counter()
    def prepare(i):
        env = IsolatedEnv(base_dir=base)
        env.allocate_port(hold=True)
        with open(os.path.join(env.state_dir, "state"), "w") as fh:
            fh.write(f"case-{i}")
        return env

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        envs = list(pool.map(prepare, range(N)))
    prepare_s = time.perf_counter() - t0
    current_py, peak_py_prepare = tracemalloc.get_traced_memory()
    rss_after_prepare = peak_rss_mb()

    # 校验隔离：命名空间/目录唯一
    namespaces = {e.namespace for e in envs}
    paths = {e.path for e in envs}

    # ---- 回收阶段 ----
    t1 = time.perf_counter()
    stats = destroy_all()
    reclaim_s = time.perf_counter() - t1
    _, peak_py_total = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_peak = peak_rss_mb()

    # ---- 复核：无残留 ----
    sweep = sweep_residue(base, min_age_s=0)
    dirs_left = sum(os.path.exists(e.path) for e in envs)

    return {
        "cases": N,
        "workers": WORKERS,
        "prepare_s": round(prepare_s, 4),
        "prepare_per_case_ms": round(prepare_s / N * 1000, 4),
        "reclaim_s": round(reclaim_s, 4),
        "reclaim_per_case_ms": round(reclaim_s / N * 1000, 4),
        "unique_namespaces": len(namespaces) == N,
        "unique_dirs": len(paths) == N,
        "degraded": stats["degraded"],
        "reclaim_errors": len(stats["errors"]),
        "dirs_left_after_reclaim": dirs_left,
        "residue_sweep_removed": len(sweep.removed),
        "residue_sweep_failed": len(sweep.failed),
        "mem": {
            "rss_peak_mb": round(rss_peak, 2),
            "rss_delta_during_prepare_mb": round(rss_after_prepare - rss_before, 2),
            "python_heap_peak_prepare_mb": round(peak_py_prepare / 1024 / 1024, 2),
            "python_heap_peak_total_mb": round(peak_py_total / 1024 / 1024, 2),
        },
    }


def main():
    os.makedirs(REPORTS, exist_ok=True)
    base = tempfile.mkdtemp(prefix="testenv_bench_")
    try:
        result = run_once(base)
    finally:
        sweep_residue(base, min_age_s=0)
        shutil.rmtree(base, ignore_errors=True)
    out = os.path.join(REPORTS, "bench_1000.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()

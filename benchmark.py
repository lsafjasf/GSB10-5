"""基准：1000 本手册 × 10 步 = 10,000 步，统计耗时与内存峰值。

运行: python3 benchmark.py [--books N] [--steps M]
结果写入 artifacts/benchmark/benchmark.json
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from runbook import AutoOperator, Executor, Runbook, Step, StepKind, TraceRecorder
from runbook import actions

ART = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "artifacts", "benchmark")


def build_book(i: int, n_steps: int) -> Runbook:
    steps = [
        Step(f"s{k}", f"自动步骤 {k}", StepKind.AUTO, actions.noop)
        for k in range(1, n_steps + 1)
    ]
    return Runbook(book_id=f"bench-{i:04d}", name=f"基准手册 {i}", steps=steps)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--books", type=int, default=1000)
    ap.add_argument("--steps", type=int, default=10)
    args = ap.parse_args()

    work = os.path.join(ART, "work")
    if os.path.exists(work):
        shutil.rmtree(work)
    os.makedirs(work, exist_ok=True)
    trace_path = os.path.join(ART, "trace.jsonl")
    if os.path.exists(trace_path):
        os.remove(trace_path)

    total_steps = args.books * args.steps
    tracemalloc.start()
    t0 = time.perf_counter()
    states = {}
    for i in range(args.books):
        book = build_book(i, args.steps)
        trace = TraceRecorder(trace_path, f"bench-run-{i}", book.book_id)
        ex = Executor(book, AutoOperator(), trace, checkpoint_dir=work)
        state = ex.run()
        trace.close()
        states[state.value] = states.get(state.value, 0) + 1
    wall = time.perf_counter() - t0
    _, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    maxrss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # 统计完成后清理每本手册的检查点（benchmark.json / trace.jsonl 保留）
    shutil.rmtree(work, ignore_errors=True)

    result = {
        "runbooks": args.books,
        "steps_per_runbook": args.steps,
        "total_steps": total_steps,
        "final_states": states,
        "wall_time_sec": round(wall, 3),
        "steps_per_sec": round(total_steps / wall, 1),
        "avg_runbook_ms": round(wall / args.books * 1000, 3),
        "py_alloc_peak_mb": round(py_peak / 1024 / 1024, 2),
        "process_maxrss_mb": round(maxrss_kb / 1024, 2),
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }
    os.makedirs(ART, exist_ok=True)
    with open(os.path.join(ART, "benchmark.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

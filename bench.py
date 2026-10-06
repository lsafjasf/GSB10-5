"""压测：1000 本手册 x 10 步 = 10000 步，输出耗时与内存峰值到 artifacts/benchmark.json。"""
import json
import resource
import shutil
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from runbook import Executor, Runbook, Step, StepKind, action

N_RUNBOOKS = 1000
STEPS_PER = 10


@action("bench_op")
def _bench(params, ctx):
    return {"n": params["n"], "ok": True}


def main():
    tmp = Path(tempfile.mkdtemp(prefix="rb-bench-"))
    ex = Executor(tmp / "ckpt", tmp / "trace.jsonl")
    runbooks = [
        Runbook(f"bench-{i}",
                [Step(f"s{j}", f"step-{j}", StepKind.AUTO, "bench_op", {"n": j})
                 for j in range(STEPS_PER)],
                {"idx": i})
        for i in range(N_RUNBOOKS)
    ]

    tracemalloc.start()
    t0 = time.perf_counter()
    for rb in runbooks:
        st = ex.start(rb)
        assert st["status"] == "completed", st["status"]
    elapsed = time.perf_counter() - t0
    _, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # Linux: KiB

    total_steps = N_RUNBOOKS * STEPS_PER
    report = {
        "runbooks": N_RUNBOOKS,
        "steps_total": total_steps,
        "wall_time_sec": round(elapsed, 3),
        "steps_per_sec": round(total_steps / elapsed, 1),
        "avg_runbook_ms": round(elapsed / N_RUNBOOKS * 1000, 3),
        "avg_step_ms": round(elapsed / total_steps * 1000, 4),
        "peak_python_alloc_mb": round(py_peak / 1024 / 1024, 2),
        "peak_rss_mb": round(rss_kb / 1024, 2),
        "python": sys.version.split()[0],
    }
    out = Path(__file__).parent / "artifacts" / "benchmark.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()

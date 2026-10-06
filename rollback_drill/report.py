"""生成演练交付数据：逐步结果、路径对照、100 次演练耗时/资源数据。

运行：python -m rollback_drill.report --out data
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import time
import tracemalloc
from typing import Dict, List

from .core import (
    Faults, ManualClock, build_env, compare_paths, run_pipeline, STEP_SPECS,
)


def _records(n: int) -> Dict[str, str]:
    return {f"key-{i:04d}": f"value-{i:04d}" for i in range(n)}


def scenario_runs() -> List[Dict]:
    """四种必覆盖情形，各自逐步给出数据与判定。"""
    scenarios = []

    # 1) 全部成功
    clk = ManualClock()
    rep = run_pipeline(build_env("drill", _records(50), clk))
    scenarios.append({"scenario": "all_success", "verdict": rep.outcome,
                      "description": "无故障、无新写入、未超时", "report": rep.to_dict()})

    # 2) 单步失败（以 restore_backup 后的 verify_restore 失败为例）
    clk = ManualClock()
    rep = run_pipeline(build_env(
        "drill", _records(50), clk, faults=Faults(fail_step="verify_restore",
                                                  fail_message="checksum mismatch (injected)")))
    scenarios.append({"scenario": "single_step_failure", "verdict": rep.outcome,
                      "description": "verify_restore 校验失败：中止 + 逆序补偿",
                      "report": rep.to_dict()})

    # 3) 演练中途有新写入（restore_backup 执行期间线上有新写）
    clk = ManualClock()
    rep = run_pipeline(build_env(
        "drill", _records(50), clk, faults=Faults(write_during_step="restore_backup")))
    scenarios.append({"scenario": "new_writes_mid_drill", "verdict": rep.outcome,
                      "description": "检测到演练期间的新写入：标记 tainted + 中止 + 补偿",
                      "report": rep.to_dict()})

    # 4) 演练超时（restore_backup 慢 100s，截止 2s）
    clk = ManualClock()
    rep = run_pipeline(build_env(
        "drill", _records(50), clk,
        faults=Faults(extra_delay={"restore_backup": 100.0}), timeout_after=2.0))
    scenarios.append({"scenario": "timeout", "verdict": rep.outcome,
                      "description": "超过注入截止时间：中止 + 逆序补偿",
                      "report": rep.to_dict()})

    return scenarios


def benchmark(n: int = 100, seed: int = 20261006) -> Dict:
    """跑 n 次演练（确定性注入时钟 + 随机数据规模/慢步骤），统计耗时与资源占用。

    业务耗时用注入时钟测量（确定、可复现）；
    wall 耗时与内存是本机实际资源占用，用于容量参考。
    """
    rng = random.Random(seed)
    tracemalloc.start()
    runs: List[Dict] = []
    wall_start = time.perf_counter()
    outcome_counts: Dict[str, int] = {}

    for i in range(n):
        records = _records(rng.randint(10, 200))
        faults = Faults()
        roll = rng.random()
        if roll < 0.15:
            faults.fail_step = rng.choice([s.name for s in STEP_SPECS])
        elif roll < 0.30:
            faults.write_during_step = "restore_backup"
        extra = {s.name: round(rng.uniform(0.0, 0.05), 4) for s in STEP_SPECS}
        faults.extra_delay = extra
        clk = ManualClock(start=1_000_000.0 + i * 10_000)

        w0 = time.perf_counter()
        rep = run_pipeline(build_env("drill", records, clk, faults=faults))
        wall = time.perf_counter() - w0

        outcome_counts[rep.outcome] = outcome_counts.get(rep.outcome, 0) + 1
        runs.append({"index": i + 1, "records": len(records),
                     "outcome": rep.outcome, "tainted": rep.tainted,
                     "simulated_duration_s": round(rep.duration, 4),
                     "wall_duration_ms": round(wall * 1000, 4),
                     "steps_executed": len([s for s in rep.steps if s.status == "ok"])})

    total_wall = time.perf_counter() - wall_start
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    sim = [r["simulated_duration_s"] for r in runs]
    wall_ms = [r["wall_duration_ms"] for r in runs]

    def stats(xs: List[float]) -> Dict:
        xs = sorted(xs)
        return {"min": round(min(xs), 4), "max": round(max(xs), 4),
                "mean": round(statistics.fmean(xs), 4),
                "median": round(statistics.median(xs), 4),
                "p95": round(xs[int(len(xs) * 0.95) - 1], 4)}

    return {
        "runs": n, "seed": seed,
        "outcome_distribution": outcome_counts,
        "simulated_duration_s": stats(sim),       # 注入时钟：确定、可复现
        "wall_duration_ms": stats(wall_ms),       # 真实墙钟
        "total_wall_ms": round(total_wall * 1000, 3),
        "peak_python_memory_kib": round(peak_bytes / 1024, 2),  # tracemalloc 峰值
        "per_run": runs,
    }


def generate(out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    data = {
        "scenario_results": scenario_runs(),
        "path_comparison": compare_paths(_records(50)),
        "benchmark_100": benchmark(100),
    }
    for name, payload in data.items():
        path = os.path.join(out_dir, f"{name}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    print("written:", ", ".join(sorted(os.listdir(out_dir))))


def main() -> None:
    ap = argparse.ArgumentParser(description="生成回滚演练验证数据")
    ap.add_argument("--out", default="data", help="输出目录")
    args = ap.parse_args()
    generate(args.out)


if __name__ == "__main__":
    main()

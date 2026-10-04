"""Run 100 drills with the real clock and record timing + resource data.

Usage: python3 tools/benchmark.py [runs] [files] [payload_bytes]
Outputs: reports/benchmark_100.csv and docs/PERF.md
"""

import json
import statistics
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rollback_drill import DrillRunner, FileDataTarget, FileEnv, Status, make_drill_plan
from rollback_drill.target import build_steps

STEP_IDS = [s.step_id for s in build_steps()]


def seed_env(root: Path, n_files: int, payload: int) -> FileEnv:
    live = {f"db/table-{i:04d}.dat": bytes([i % 251]) * payload for i in range(n_files)}
    snap = {f"db/table-{i:04d}.dat": b"S" + bytes([i % 251]) * (payload - 1)
            for i in range(n_files)}
    env = FileEnv.create(root, live, snap)
    fp = FileDataTarget(env.live, env.snap, env.meta).snapshot_fingerprint()
    (env.snap / "manifest.json").write_text(json.dumps({"fingerprint": fp}))
    return env


def pct(sorted_vals, q):
    return sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * q) - 1)]


def main() -> int:
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    n_files = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    payload = int(sys.argv[3]) if len(sys.argv) > 3 else 4096

    tmp = Path(tempfile.mkdtemp(prefix="drill-bench-"))
    env = seed_env(tmp / "prod", n_files, payload)
    rows = []
    failures = 0
    for i in range(runs):
        clone = env.clone_to(tmp / f"drill-{i:03d}")
        res = DrillRunner().run(make_drill_plan(clone, run_id_seed=f"bench-{i}"))
        if res.overall is not Status.SUCCESS:
            failures += 1
        step_ms = {s.step_id: s.duration_ms for s in res.steps}
        rows.append({
            "run": i,
            "overall": res.overall.value,
            "total_ms": round(res.total_ms, 3),
            "bytes_copied": int(res.resources["bytes_copied"]),
            "files_touched": int(res.resources["files_touched"]),
            "rss_peak_bytes": int(res.resources["rss_peak_bytes"]),
            **{f"{k}_ms": round(v, 3) for k, v in step_ms.items()},
        })

    out_dir = Path(__file__).resolve().parent.parent
    csv_path = out_dir / "reports" / "benchmark_100.csv"
    csv_path.parent.mkdir(exist_ok=True)
    header = list(rows[0].keys())
    with open(csv_path, "w") as fh:
        fh.write(",".join(header) + "\n")
        for r in rows:
            fh.write(",".join(str(r[h]) for h in header) + "\n")

    totals = sorted(r["total_ms"] for r in rows)
    stats = {
        "runs": runs, "failures": failures,
        "dataset": f"{n_files} files x {payload} B = {n_files*payload/1024:.0f} KiB",
        "min_ms": round(totals[0], 3), "p50_ms": round(statistics.median(totals), 3),
        "p95_ms": round(pct(totals, 0.95), 3), "p99_ms": round(pct(totals, 0.99), 3),
        "max_ms": round(totals[-1], 3), "mean_ms": round(statistics.mean(totals), 3),
        "stdev_ms": round(statistics.stdev(totals), 3),
        "bytes_copied_per_run": rows[0]["bytes_copied"],
        "rss_peak_bytes_max": max(r["rss_peak_bytes"] for r in rows),
    }
    per_step = {sid: round(statistics.mean(r[f"{sid}_ms"] for r in rows), 3)
                for sid in STEP_IDS}

    perf_md = out_dir / "docs" / "PERF.md"
    lines = [
        "# 100 次演练耗时与资源占用",
        "",
        f"- 运行次数: {stats['runs']}，失败: {stats['failures']}",
        f"- 数据集: {stats['dataset']}（真实数据副本，逐次全新克隆）",
        f"- 时钟: 真实系统时钟（非注入），CPython 标准库，单进程",
        "",
        "## 总耗时（毫秒）",
        "",
        "| min | p50 | p95 | p99 | max | mean | stdev |",
        "|-----|-----|-----|-----|-----|------|-------|",
        f"| {stats['min_ms']} | {stats['p50_ms']} | {stats['p95_ms']} | "
        f"{stats['p99_ms']} | {stats['max_ms']} | {stats['mean_ms']} | {stats['stdev_ms']} |",
        "",
        "## 资源占用",
        "",
        f"- 每次演练拷贝字节数: {stats['bytes_copied_per_run']} "
        f"（安全副本 + 快照恢复，两次全量拷贝）",
        f"- RSS 峰值（100 次中最大）: {stats['rss_peak_bytes_max']} 字节",
        "",
        "## 分步平均耗时（毫秒）",
        "",
        "| step_id | 平均耗时ms |",
        "|---------|-----------|",
    ]
    lines += [f"| {sid} | {ms} |" for sid, ms in per_step.items()]
    lines += ["", f"逐次原始数据见 `reports/benchmark_100.csv`。", ""]
    perf_md.write_text("\n".join(lines))

    print(json.dumps(stats, indent=2))
    print(f"csv -> {csv_path}")
    print(f"md  -> {perf_md}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

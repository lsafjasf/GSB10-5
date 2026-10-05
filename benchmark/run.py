"""10 万条译文占位符校验基准：分类一致性 + 耗时 + 内存峰值。

运行（仓库根目录）：
    python3 -m benchmark.run --n 100000
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import sys
import time
import tracemalloc

from phcheck import restore, validate

from .gen import Pair, generate


def check_agreement(pairs):
    """按场景做结构化断言（独立于库内部实现），统计不一致条数。"""
    mismatches = []
    drift_rows = 0
    drift_detected = 0
    for p in pairs:
        report = validate(p.source, p.translation)
        s = p.scenario
        ok = False
        if s in ("ok_same", "ok_reorder"):
            ok = report.ok
        elif s == "missing":
            ok = (
                not report.ok
                and len(report.missing) == 1
                and not report.extra
                and not report.count_mismatch
                and not report.invalid
            )
        elif s == "extra":
            ok = (
                not report.ok
                and len(report.extra) == 1
                and not report.missing
                and not report.count_mismatch
                and not report.invalid
            )
        elif s == "count_mismatch":
            ok = (
                not report.ok
                and len(report.count_mismatch) == 1
                and not report.missing
                and not report.extra
                and not report.invalid
            )
        elif s == "invalid":
            ok = not report.ok and len(report.invalid) >= 1
        elif s == "drift":
            drift_rows += 1
            strict = validate(p.source, p.translation, strict_format=True)
            drift_detected += 1 if strict.format_drift else 0
            ok = report.ok
        if not ok:
            mismatches.append(
                {"family": p.family, "scenario": s,
                 "source": p.source, "translation": p.translation}
            )
    return {
        "total": len(pairs),
        "mismatches": len(mismatches),
        "mismatch_samples": mismatches[:5],
        "drift_rows": drift_rows,
        "drift_detected": drift_detected,
    }


def measure(pairs, runs: int):
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        for p in pairs:
            validate(p.source, p.translation)
        times.append(time.perf_counter() - start)

    # 还原基准：只测顺序保持的行（ok_same），保证绑值一定正确
    ok_rows = [p for p in pairs if p.scenario == "ok_same"]
    start = time.perf_counter()
    for p in ok_rows:
        restore(p.source, p.translation, p.values)
    restore_seconds = time.perf_counter() - start

    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    tracemalloc.start()
    for p in pairs:
        validate(p.source, p.translation)
    _, traced_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    best = min(times)
    return {
        "validate_runs_seconds": [round(t, 4) for t in times],
        "validate_best_seconds": round(best, 4),
        "validate_per_second": round(len(pairs) / best),
        "restore_rows": len(ok_rows),
        "restore_seconds": round(restore_seconds, 4),
        "restore_per_second": round(len(ok_rows) / restore_seconds),
        "tracemalloc_peak_mb": round(traced_peak / 1024 / 1024, 3),
        "maxrss_before_mb": round(rss_before / 1024, 1),
        "maxrss_after_mb": round(rss_after / 1024, 1),
        "maxrss_delta_mb": round((rss_after - rss_before) / 1024, 1),
    }


def render_markdown(result: dict) -> str:
    perf = result["perf"]
    agree = result["agreement"]
    times = " / ".join("{:.4f}s".format(t) for t in perf["validate_runs_seconds"])
    return """# 10 万条译文占位符校验基准

> 由 `python3 -m benchmark.run --n {n} --runs {runs}` 生成，随机种子 {seed}。

## 环境

- Python: {python}
- 平台: {platform}
- CPU: {cpu_count} 核（基准为单线程）

## 数据构成

- 总量: {n} 条译文（命名 / 位置 / 裸占位符三种语法家族轮流）
- 场景: 顺序保持 30%、顺序调整 30%、缺失 10%、多余 10%、重复次数不一致 10%、非法 5%、格式漂移 5%
- 分类一致性核对: 不一致 {mismatches} 条；格式漂移行 {drift_rows}，strict 模式检出 {drift_detected}

## 耗时

| 项目 | 结果 |
| --- | --- |
| 校验各轮耗时 | {times} |
| 校验最好耗时 | {best}s |
| 校验吞吐 | {throughput} 条/秒 |
| 还原（{restore_rows} 条顺序保持行） | {restore_seconds}s（{restore_throughput} 条/秒） |

## 内存峰值（单遍校验、不保留报告）

| 指标 | 值 | 说明 |
| --- | --- | --- |
| tracemalloc Python 对象峰值 | {trace} MB | 校验循环内的增量分配峰值 |
| 进程 RSS（校验前 / 后 / 增量） | {before} / {after} / +{delta} MB | 含 10 万条数据本身与解释器 |
""".format(
        n=result["n"], runs=result["runs"], seed=result["seed"],
        python=result["python"], platform=result["platform"], cpu_count=result["cpu_count"],
        mismatches=agree["mismatches"], drift_rows=agree["drift_rows"],
        drift_detected=agree["drift_detected"], times=times, best=perf["validate_best_seconds"],
        throughput="{:,}".format(perf["validate_per_second"]),
        restore_rows=perf["restore_rows"], restore_seconds=perf["restore_seconds"],
        restore_throughput="{:,}".format(perf["restore_per_second"]),
        trace=perf["tracemalloc_peak_mb"], before=perf["maxrss_before_mb"],
        after=perf["maxrss_after_mb"], delta=perf["maxrss_delta_mb"],
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="占位符校验基准")
    parser.add_argument("--n", type=int, default=100000)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--json", dest="json_path")
    parser.add_argument("--md", dest="md_path")
    args = parser.parse_args(argv)

    pairs = generate(args.n, args.seed)
    agreement = check_agreement(pairs)
    perf = measure(pairs, args.runs)
    result = {
        "n": args.n,
        "runs": args.runs,
        "seed": args.seed,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "agreement": agreement,
        "perf": perf,
    }

    print("分类一致性: {} 条中不一致 {}".format(agreement["total"], agreement["mismatches"]))
    print("strict 格式漂移: {}/{}".format(agreement["drift_detected"], agreement["drift_rows"]))
    print("校验耗时(秒): {} -> 最好 {}s，{:,} 条/秒".format(
        perf["validate_runs_seconds"], perf["validate_best_seconds"], perf["validate_per_second"]))
    print("还原: {} 行 {:.4f}s（{:,} 条/秒）".format(
        perf["restore_rows"], perf["restore_seconds"], perf["restore_per_second"]))
    print("内存: tracemalloc 峰值 {} MB；RSS {} -> {} MB（+{}）".format(
        perf["tracemalloc_peak_mb"], perf["maxrss_before_mb"],
        perf["maxrss_after_mb"], perf["maxrss_delta_mb"]))

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    if args.md_path:
        with open(args.md_path, "w", encoding="utf-8") as f:
            f.write(render_markdown(result))
    return 0 if agreement["mismatches"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

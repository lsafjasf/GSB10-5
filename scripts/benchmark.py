#!/usr/bin/env python3
"""基准：1000 个构建脚本的分析耗时与内存峰值。

运行：python3 scripts/benchmark.py [--pairs 500]
说明：--pairs N 表示生成 2N 个构建脚本（N 对 A/B），默认 500 对 = 1000 个脚本。
输出：终端汇总 + results/benchmark_report.json
"""
from __future__ import annotations

import argparse
import json
import os
import random
import resource
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from imglayer_cache import BuildContext, FileRecord, LayerCache, analyze, parse_script

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")


def make_script(rng: random.Random, idx: int) -> str:
    lines = [
        "FROM base:1.0",
        "WORKDIR /app",
        f"COPY src_{idx % 8}.py /app/src_{idx % 8}.py",
        f"RUN write /app/cfg_{idx}.txt profile={idx % 5}",
        f"RUN mkdir /app/data_{idx % 3} && write /app/data_{idx % 3}/seed.txt v{idx % 7}",
        f"ENV APP_ENV env{idx % 4}",
        f"RUN chmod 755 /app/src_{idx % 8}.py",
    ]
    return "\n".join(lines) + "\n"


def make_variant(rng: random.Random, base: str, idx: int) -> str:
    """按场景类型生成 B 脚本：0 完全相同 1 顺序调整 2 中间层变化 3 时间戳/所有者。"""
    kind = idx % 4
    lines = base.splitlines()
    if kind == 0:
        return base
    if kind == 1:
        lines[2], lines[3] = lines[3], lines[2]
        return "\n".join(lines) + "\n"
    if kind == 2:
        return base.replace(f"profile={idx % 5}", f"profile={(idx + 1) % 5}")
    return base.replace(
        f"RUN chmod 755 /app/src_{idx % 8}.py",
        f"RUN chmod 755 /app/src_{idx % 8}.py && touch /app/cfg_{idx}.txt && chown svc /app/cfg_{idx}.txt",
    )


def make_context(rng: random.Random, idx: int, jitter_meta: bool) -> BuildContext:
    files = {}
    for src in range(8):
        uid = rng.randint(0, 65535) if jitter_meta else 1000
        mtime = rng.randint(0, 10**12) if jitter_meta else 100
        files[f"src_{src}.py"] = FileRecord(
            content=f"print('src {src} build {idx % 5}')".encode(),
            mode=0o644,
            uid=uid,
            gid=uid,
            mtime_ns=mtime,
        )
    return BuildContext(files)


def rss_bytes() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", type=int, default=500, help="A/B 对数（脚本总数 = 2 * pairs）")
    args = parser.parse_args()
    pairs = args.pairs

    rng = random.Random(42)
    jobs = []
    for i in range(pairs):
        script_a = make_script(rng, i)
        script_b = make_variant(rng, script_a, i)
        ctx_a = make_context(rng, i, jitter_meta=False)
        ctx_b = make_context(rng, i, jitter_meta=(i % 4 == 3))
        jobs.append((script_a, ctx_a, script_b, ctx_b))

    rss_before = rss_bytes()
    tracemalloc.start()
    start = time.perf_counter()

    total_layers = reused_layers = 0
    for script_a, ctx_a, script_b, ctx_b in jobs:
        cache = LayerCache()
        report = analyze(
            parse_script(script_b),
            ctx_b,
            cache,
            script_a=parse_script(script_a),
            context_a=ctx_a,
        )
        total_layers += report.total_layers
        reused_layers += report.reused_layers

    elapsed = time.perf_counter() - start
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = rss_bytes()

    scripts = pairs * 2
    summary = {
        "pairs": pairs,
        "scripts": scripts,
        "total_layers_analyzed": total_layers,
        "reused_layers": reused_layers,
        "overall_layer_reuse_ratio": round(reused_layers / total_layers, 4),
        "wall_time_seconds": round(elapsed, 4),
        "avg_ms_per_pair": round(elapsed / pairs * 1000, 3),
        "avg_ms_per_script": round(elapsed / scripts * 1000, 3),
        "pairs_per_second": round(pairs / elapsed, 1),
        "tracemalloc_peak_mb": round(peak / 1024 / 1024, 2),
        "tracemalloc_current_mb": round(current / 1024 / 1024, 2),
        "process_maxrss_mb": round(rss_after / 1024 / 1024, 2),
        "process_maxrss_delta_mb": round((rss_after - rss_before) / 1024 / 1024, 2),
    }

    print(f"构建脚本总数      : {scripts}（{pairs} 对 A/B）")
    print(f"分析层总数        : {total_layers}（复用 {reused_layers}，复用率 {summary['overall_layer_reuse_ratio']:.1%}）")
    print(f"总耗时            : {summary['wall_time_seconds']} s")
    print(f"平均每对          : {summary['avg_ms_per_pair']} ms（每脚本 {summary['avg_ms_per_script']} ms）")
    print(f"吞吐              : {summary['pairs_per_second']} 对/秒")
    print(f"tracemalloc 峰值  : {summary['tracemalloc_peak_mb']} MB")
    print(f"进程 MaxRSS       : {summary['process_maxrss_mb']} MB（Δ {summary['process_maxrss_delta_mb']} MB）")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out = os.path.join(RESULTS_DIR, "benchmark_report.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(f"已写出 {out}")


if __name__ == "__main__":
    main()

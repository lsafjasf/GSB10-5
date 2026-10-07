"""在基准工程上运行检查器，输出耗时、内存峰值、召回率、误报率、定位精度。

用法: python3 tools/run_benchmark.py [benchmark 目录]
"""
import json
import os
import resource
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from codereview import check_paths


def main():
    bench = sys.argv[1] if len(sys.argv) > 1 else "benchmark"
    with open(os.path.join(bench, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    expected = {(e["rule_id"], e["file"], e["line"]) for e in manifest}

    n_files = 0
    n_lines = 0
    for name in os.listdir(bench):
        if name.endswith(".py"):
            n_files += 1
            with open(os.path.join(bench, name), encoding="utf-8") as fh:
                n_lines += sum(1 for _ in fh)

    tracemalloc.start()
    start = time.perf_counter()
    findings = check_paths([bench])
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    actual = {(f.rule_id, os.path.basename(f.path), f.line) for f in findings}
    tp = len(actual & expected)
    fp = len(actual - expected)
    fn = len(expected - actual)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    exact_line = tp / len(expected) if expected else 1.0

    print(f"规模: {n_files} 个文件, {n_lines} 行")
    print(f"耗时: {elapsed:.3f} s ({n_lines / elapsed:,.0f} 行/s)")
    print(f"内存峰值(tracemalloc): {peak / 1024 / 1024:.1f} MiB")
    print(f"进程峰值 RSS(ru_maxrss): {max_rss / 1024:.1f} MiB")
    print(f"注入问题: {len(expected)} 个, 报警: {len(actual)} 条")
    print(f"TP={tp} FP={fp} FN={fn}")
    print(f"precision={precision:.4f} recall={recall:.4f}")
    print(f"定位精度(行号完全一致): {exact_line * 100:.2f}%")


if __name__ == "__main__":
    main()

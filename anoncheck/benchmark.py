"""大数据量基准：100 万条记录的检查耗时与内存峰值。

耗时用 time.perf_counter 测量“检查阶段”（QI 识别 + 全部组合评估 +
最高风险组合的记录级定位）；内存同时报告
- tracemalloc 峰值：检查阶段 Python 对象分配峰值；
- 进程 RSS 峰值（resource.ru_maxrss）：操作系统口径，含数据集。
"""

import os
import platform
import resource
import time
import tracemalloc

from .dataset import generate
from .quasi import identify_quasi_identifiers, enumerate_combinations
from .risk import evaluate_combination, find_risky_records


def run_benchmark(n=1_000_000, k_threshold=5, max_order=3,
                  qi_sample=50000, seed=20261005, out_dir="out"):
    gen_start = time.perf_counter()
    headers, rows = generate(n, seed=seed)
    gen_elapsed = time.perf_counter() - gen_start

    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024

    tracemalloc.start()
    start = time.perf_counter()

    qis = identify_quasi_identifiers(headers, rows[:qi_sample])
    combos = enumerate_combinations(qis, max_order=max_order)
    evaluations = []
    for names, indices in combos:
        metrics = evaluate_combination(rows, indices, k_threshold)
        evaluations.append((names, indices, metrics))
    worst_names, worst_indices, worst_metrics = max(
        evaluations, key=lambda item: item[2]["rare_record_count"])
    risky_records = find_risky_records(rows, worst_indices, k_threshold)

    elapsed = time.perf_counter() - start
    _, traced_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024

    result = {
        "n": n,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "generation_seconds": gen_elapsed,
        "check_seconds": elapsed,
        "tracemalloc_peak_mib": traced_peak / 1024 / 1024,
        "rss_before_check_mib": rss_before / 1024 / 1024,
        "rss_peak_mib": rss_peak / 1024 / 1024,
        "records_per_second": n / elapsed,
        "qi_columns": [q["column"] for q in qis],
        "combination_count": len(combos),
        "worst_combo": " + ".join(worst_names),
        "worst_min_class_size": worst_metrics["min_class_size"],
        "worst_unique_classes": worst_metrics["unique_class_count"],
        "worst_rare_records": worst_metrics["rare_record_count"],
        "worst_risky_list_size": len(risky_records),
    }

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "benchmark.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_render(result))
    return result


def _render(r):
    return (
        "# 100 万条记录基准测试\n\n"
        f"- 环境：Python {r['python']} / {r['platform']}\n"
        f"- 记录数：{r['n']:,}\n"
        f"- 准标识符（{len(r['qi_columns'])} 列）：{', '.join(r['qi_columns'])}\n"
        f"- 评估组合数（1..3 阶）：{r['combination_count']}\n"
        f"- 数据生成耗时：{r['generation_seconds']:.2f} s\n"
        f"- **检查耗时：{r['check_seconds']:.2f} s**"
        f"（约 {r['records_per_second']:,.0f} 条/秒）\n"
        f"- **检查阶段 tracemalloc 峰值：{r['tracemalloc_peak_mib']:.1f} MiB**\n"
        f"- 检查前进程 RSS：{r['rss_before_check_mib']:.1f} MiB\n"
        f"- **进程 RSS 峰值（含数据集）：{r['rss_peak_mib']:.1f} MiB**\n"
        f"- 风险最高组合：`{r['worst_combo']}`，"
        f"最小等价类 {r['worst_min_class_size']}，"
        f"唯一组合 {r['worst_unique_classes']:,} 个，"
        f"稀有记录 {r['worst_rare_records']:,} 条，"
        f"清单 {r['worst_risky_list_size']:,} 行\n"
    )

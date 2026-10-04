"""基准：10 万次协商的耗时与内存峰值。

用法: python3 benchmark.py [次数]
输出: docs/benchmark.json（同时打印到 stdout）
"""

import json
import os
import resource
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from export_negotiation import negotiate

CASES = [
    ("json", [("id", "int"), ("name", "str"), ("created", "date")]),
    ("csv", [("id", "int"), ("tags", "array"), ("meta", "object")]),
    ("xlsx", [("id", "int"), ("name", "str"), ("created", "date")]),
    ("xlsx", [("id", "int"), ("tags", "array"), ("meta", "object")]),
    ("yaml", [("id", "int"), ("name", "str"), ("created", "date")]),
    ("parquet", [("blob", "binary")]),
]


def main(iterations=100_000):
    # 预热 + 校验确定性：同一请求两次结果必须一致
    for fmt, fields in CASES:
        assert negotiate(fmt, fields) == negotiate(fmt, fields)

    tracemalloc.start()
    start = time.perf_counter()
    for i in range(iterations):
        fmt, fields = CASES[i % len(CASES)]
        negotiate(fmt, fields)
    elapsed = time.perf_counter() - start
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # 进程级峰值 RSS（含解释器与全部分配）
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024

    report = {
        "iterations": iterations,
        "elapsed_seconds": round(elapsed, 4),
        "per_negotiation_us": round(elapsed / iterations * 1e6, 2),
        "python_peak_bytes": peak,
        "python_peak_mb": round(peak / 1024 / 1024, 2),
        "process_peak_rss_mb": round(peak_rss / 1024 / 1024, 2),
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
    }
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "docs", "benchmark.json")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
    main(n)

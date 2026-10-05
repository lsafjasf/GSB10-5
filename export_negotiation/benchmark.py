"""基准：10 万次协商的耗时与内存峰值。

耗时与内存分开测量（tracemalloc 本身有开销，不计入耗时）：
  - 耗时：无追踪下跑 10 万次协商
  - 内存：tracemalloc 下重放同一工作负载，取 Python 分配峰值；
          另报进程 RSS 峰值（ru_maxrss）

运行：python -m export_negotiation.benchmark
"""

import json
import random
import resource
import time
import tracemalloc

from . import negotiate, FORMATS, FIELD_TYPES

N = 100_000
MASK64 = (1 << 64) - 1


def build_workload(seed=7):
    rng = random.Random(seed)
    tokens = list(FORMATS) + ["docx", "parquet", "avro", "xls", "JSON", " yml "]
    workload = []
    for _ in range(N):
        fmt = rng.choice(tokens)
        fields = [
            {"name": "f%d" % i, "type": rng.choice(FIELD_TYPES)}
            for i in range(rng.randint(1, 8))
        ]
        workload.append((fmt, fields))
    return workload


def run_pass(workload):
    statuses = {}
    digest = 0
    for fmt, fields in workload:
        r = negotiate(fmt, fields)
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
        digest = ((digest * 1315423911
                   ^ hash((r["status"], r["selected_format"],
                           tuple(m["name"] for m in r["missing_fields"]))))
                  & MASK64)
    return statuses, digest


def main():
    workload = build_workload()
    for fmt, fields in workload[:500]:  # 预热，避免冷启动计入
        negotiate(fmt, fields)

    t0 = time.perf_counter()
    statuses, digest = run_pass(workload)
    elapsed = time.perf_counter() - t0

    tracemalloc.start()
    run_pass(workload)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    print("协商次数:         %d" % N)
    print("总耗时:           %.3f s" % elapsed)
    print("单次平均:         %.2f us" % (elapsed / N * 1e6))
    print("吞吐:             %.0f 次/s" % (N / elapsed))
    print("tracemalloc 峰值:  %.2f MiB（单次协商均摊 %.0f B）"
          % (peak / 1024 / 1024, peak / N))
    print("进程 RSS 峰值:    %.2f MiB" % (rss_peak_kb / 1024))
    print("状态分布:         %s" % json.dumps(statuses, sort_keys=True))
    print("结果摘要哈希:     %d（防优化占位）" % digest)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""scope.py 性能基准：100 万次校验的耗时与内存峰值。

运行：python3 bench.py

场景模拟真实鉴权热路径：令牌授权作用域在签发时解析一次并缓存，
每次请求解析请求作用域并逐条比对。另测纯 covers() 热路径。
"""

import gc
import resource
import sys
import time
import tracemalloc

from scope import check, covers, parse_scope

N = 1_000_000

GRANTS = [
    "files.**:readwrite",
    "users.*.settings:read",
    "billing.invoices:read",
    "logs.*:read",
    "admin.audit.**:*",
]
REQUEST = "files.docs.2024.q3.report:read"


def bench_time() -> float:
    grants = [parse_scope(g) for g in GRANTS]
    start = time.perf_counter()
    hits = 0
    for _ in range(N):
        req = parse_scope(REQUEST, allow_wildcard=False)
        for g in grants:
            if covers(g, req):
                hits += 1
                break
    elapsed = time.perf_counter() - start
    assert hits == N
    return elapsed


def bench_time_parsed() -> float:
    """授权与请求均已解析的纯比对热路径。"""
    grants = [parse_scope(g) for g in GRANTS]
    req = parse_scope(REQUEST, allow_wildcard=False)
    start = time.perf_counter()
    hits = 0
    for _ in range(N):
        for g in grants:
            if covers(g, req):
                hits += 1
                break
    elapsed = time.perf_counter() - start
    assert hits == N
    return elapsed


def bench_memory():
    gc.collect()
    tracemalloc.start()
    baseline, _ = tracemalloc.get_traced_memory()
    hits = 0
    for _ in range(N):
        d = check(GRANTS, REQUEST)
        hits += d.allowed
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert hits == N
    return peak - baseline


def main() -> None:
    print(f"Python {sys.version.split()[0]}，校验次数 N = {N:,}")
    print(f"授权作用域 {len(GRANTS)} 条，请求作用域 {REQUEST!r}")
    print("-" * 60)

    t1 = bench_time()
    print(f"[耗时] 端到端（每次解析请求 + 逐条 covers）：{t1:.3f} s"
          f"（{N / t1:,.0f} 次/s，{t1 / N * 1e6:.3f} us/次）")

    t2 = bench_time_parsed()
    print(f"[耗时] 纯 covers 热路径（双方已解析）：      {t2:.3f} s"
          f"（{N / t2:,.0f} 次/s，{t2 / N * 1e6:.3f} us/次）")

    peak = bench_memory()
    print(f"[内存] 100 万次 check() 的 Python 堆峰值增量（tracemalloc）："
          f"{peak / 1024:.1f} KiB")

    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"[内存] 进程峰值 RSS（ru_maxrss，含解释器）：{rss_kb / 1024:.1f} MiB")


if __name__ == "__main__":
    main()

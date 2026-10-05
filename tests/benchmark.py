"""10 万次键计算的耗时与内存峰值。

运行: python3 tests/benchmark.py [N]
"""
import os
import resource
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from buildcache import BuildInputs, compute_key

N = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000

inputs = BuildInputs(
    sources={
        "main.c": b"int main(){return 0;}\n",
        "lib/util.c": b"int util(int x){return x+1;}\n",
    },
    toolchain={"gcc": "13.2.0", "ld": "2.41"},
    params={"opt": "-O2", "std": "c11"},
    env={"CC": "gcc", "CFLAGS": "-O2", "LANG": "C"},
    platform_info=BuildInputs.detect_platform(),
)

# 预热 + 正确性确认
first = compute_key(inputs)

tracemalloc.start()
t0 = time.perf_counter()
for _ in range(N):
    k = compute_key(inputs)
elapsed = time.perf_counter() - t0
cur, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
assert k == first

rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(f"次数 N            : {N:,}")
print(f"键                : {first}")
print(f"总耗时            : {elapsed:.3f} s")
print(f"单次均值          : {elapsed / N * 1e6:.2f} µs")
print(f"吞吐              : {N / elapsed:,.0f} keys/s")
print(f"tracemalloc 峰值  : {peak / 1024:.1f} KiB（含基准循环与解释器基线）")
print(f"进程 RSS 峰值     : {rss_kb / 1024:.1f} MiB")

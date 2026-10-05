"""性能测试：100 万次插值的耗时与内存峰值。

耗时与内存分开测：tracemalloc 会显著拖慢循环，不能同时测。
"""

import random
import resource
import time
import tracemalloc

from slerp import slerp

N = 1_000_000
POOL = 4096

rng = random.Random(42)
lats1 = [rng.uniform(-90, 90) for _ in range(POOL)]
lons1 = [rng.uniform(-180, 180) for _ in range(POOL)]
lats2 = [rng.uniform(-90, 90) for _ in range(POOL)]
lons2 = [rng.uniform(-180, 180) for _ in range(POOL)]
ts = [rng.random() for _ in range(POOL)]

# 1) 耗时：100 万次，无内存追踪
t0 = time.perf_counter()
checksum = 0.0
for i in range(N):
    j = i & (POOL - 1)
    lat, lon = slerp(lats1[j], lons1[j], lats2[j], lons2[j], ts[j])
    checksum += lat + lon
elapsed = time.perf_counter() - t0

# 2) 内存：同样循环跑 10 万次，观察循环内新增分配峰值（不随次数增长）
tracemalloc.start()
for i in range(N // 10):
    j = i & (POOL - 1)
    lat, lon = slerp(lats1[j], lons1[j], lats2[j], lons2[j], ts[j])
    checksum += lat + lon
_, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()

rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(f"插值次数:      {N}")
print(f"总耗时:        {elapsed:.3f} s")
print(f"单次均耗:      {elapsed / N * 1e6:.3f} us")
print(f"吞吐:          {N / elapsed / 1e6:.3f} M 次/s")
print(f"内存峰值(tracemalloc, 循环内新增分配): {peak / 1024:.1f} KiB")
print(f"进程 RSS 峰值: {rss_kb / 1024:.1f} MiB")
print(f"校验和(防优化): {checksum:.6f}")

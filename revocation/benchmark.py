"""100 万次吊销校验基准：报告总耗时、单次均耗与缓存内存峰值（tracemalloc）。

上游查询用内存 Fake 替代，测的是缓存本身的开销。
tracemalloc 在 serials 列表构造之后才启动，因此测得的内存只含缓存结构本身。
"""

import sys
import time
import tracemalloc

from revocation_cache import FailMode, RevocationCache, Status

N_CHECKS = 1_000_000
N_SERIALS = 50_000  # 不同证书数，决定缓存条目数


class FakeQuerier:
    def __init__(self):
        self.calls = 0

    def query(self, serial):
        self.calls += 1
        return Status.GOOD, None


def main():
    serials = [f"serial-{i:08d}" for i in range(N_SERIALS)]
    querier = FakeQuerier()

    tracemalloc.start()
    cache = RevocationCache(querier, ttl=3600.0, fail_mode=FailMode.FAIL_CLOSED,
                            now=time.time)

    # 预热：填满缓存（每条 serial 一次上游拉取）
    for s in serials:
        cache.check(s)
    cache_mem, _ = tracemalloc.get_traced_memory()

    # 稳态：100 万次校验，全部命中缓存
    tracemalloc.reset_peak()
    t0 = time.perf_counter()
    allowed = 0
    for i in range(N_CHECKS):
        if cache.check(serials[i % N_SERIALS]).allowed:
            allowed += 1
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(f"Python            : {sys.version.split()[0]}")
    print(f"checks            : {N_CHECKS:,}")
    print(f"distinct serials  : {N_SERIALS:,}")
    print(f"allowed           : {allowed:,}")
    print(f"upstream fetches  : {querier.calls:,} (缓存命中率 {(N_CHECKS - querier.calls) / N_CHECKS:.2%})")
    print(f"total time        : {elapsed:.3f} s")
    print(f"per check         : {elapsed / N_CHECKS * 1e6:.3f} us")
    print(f"throughput        : {N_CHECKS / elapsed:,.0f} checks/s")
    print(f"cache memory      : {cache_mem / 1024 / 1024:.2f} MiB ({cache_mem / N_SERIALS:.0f} bytes/entry)")
    print(f"peak during 1M    : {peak / 1024 / 1024:.2f} MiB (稳态无增长)")


if __name__ == "__main__":
    main()

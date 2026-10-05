"""基准：100 万次 check() 的耗时与缓存内存峰值（tracemalloc）。"""

import tracemalloc
import time

from revocation_cache import RevocationCache, Status

N = 1_000_000


def fake_clock():
    return 0.0  # 时间冻结：纯缓存命中路径，不触发刷新


def run(label, serials):
    fetcher_calls = [0]

    def fetcher(serial):
        fetcher_calls[0] += 1
        return Status.GOOD

    cache = RevocationCache(fetcher, ttl=3600.0, clock=fake_clock)
    tracemalloc.start()
    t0 = time.perf_counter()
    allowed = 0
    for i in range(N):
        if cache.check(serials[i % len(serials)]).allowed:
            allowed += 1
    dt = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"{label}: {N} 次校验, 耗时 {dt:.3f}s "
          f"({N / dt / 1e6:.2f}M 次/s), 内存峰值 {peak / 1024 / 1024:.2f} MiB, "
          f"fetcher 调用 {fetcher_calls[0]} 次, 放行 {allowed}")
    assert allowed == N


if __name__ == "__main__":
    run("场景1 单证书(热缓存)  ", ["A"])
    run("场景2 10万证书(内存峰值)", [f"serial-{i}" for i in range(100_000)])

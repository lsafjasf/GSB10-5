"""100 万次口令校验的耗时与内存峰值压测（仅标准库）。

运行: python3 benchmark.py [次数，默认 1000000]
"""

import gc
import resource
import sys
import time
import tracemalloc

from password_policy import DEFAULT_COMMON_PASSWORDS, PasswordPolicy

POLICY = PasswordPolicy(
    min_length=8,
    max_length=64,
    require_lower=True,
    require_upper=True,
    require_digit=True,
    require_symbol=True,
    no_whitespace=True,
    max_repeat=3,
    common_passwords=DEFAULT_COMMON_PASSWORDS,
    username="alice",
)

# 混合样本：通过、单条违规、多条违规、多字节、边界长度
SAMPLES = [
    "Str0ng!pass",
    "Strong!pw",
    "alice 1",
    "口令口令Aa1!",
    "Aa1!" + "b" * 8,
    "Aa1!" + "c" * 61,
    "password",
    "éxample1A",
]


def peak_rss_mb():
    # Linux 下 ru_maxrss 单位是 KB
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    gc.collect()
    rss_before = peak_rss_mb()

    # 阶段 1：纯计时（不开 tracemalloc，避免其插桩拖慢测量）
    t0 = time.perf_counter()
    ok_count = 0
    for i in range(n):
        res = POLICY.validate(SAMPLES[i % len(SAMPLES)])
        ok_count += res.ok
    elapsed = time.perf_counter() - t0

    # 阶段 2：tracemalloc 测 Python 堆峰值（用 1/10 样本，插桩有额外开销）
    m = max(n // 10, 1)
    gc.collect()
    tracemalloc.start()
    for i in range(m):
        POLICY.validate(SAMPLES[i % len(SAMPLES)])
    py_current, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = peak_rss_mb()

    print(f"校验次数:        {n:,}")
    print(f"总耗时:          {elapsed:.3f} s")
    print(f"单次均耗:        {elapsed / n * 1e6:.2f} µs")
    print(f"吞吐量:          {n / elapsed:,.0f} 次/秒")
    print(f"通过/未通过:     {ok_count:,} / {n - ok_count:,}")
    print(f"Python 堆峰值:   {py_peak / 1024 / 1024:.2f} MiB (tracemalloc)")
    print(f"进程 RSS 峰值:   {rss_after:.1f} MiB (起始 {rss_before:.1f} MiB)")


if __name__ == "__main__":
    main()

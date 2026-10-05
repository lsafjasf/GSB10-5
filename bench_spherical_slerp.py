"""100 万次插值基准：耗时与内存峰值。

运行：python3 bench_spherical_slerp.py
"""

import resource
import time
import tracemalloc

from spherical_slerp import slerp

N = 1_000_000


def make_inputs(seed=42):
    """固定 4096 组输入循环使用，避免随机数生成干扰计时与内存。"""
    import random
    rng = random.Random(seed)
    return [
        (rng.uniform(-90, 90), rng.uniform(-180, 180),
         rng.uniform(-90, 90), rng.uniform(-180, 180), rng.random())
        for _ in range(4096)
    ]


def run(n, inputs):
    acc = 0.0
    m = len(inputs)
    for i in range(n):
        la1, lo1, la2, lo2, t = inputs[i % m]
        r = slerp(la1, lo1, la2, lo2, t)
        acc += r[0] + r[1]
    return acc


def main():
    inputs = make_inputs()

    # 预热
    run(10_000, inputs)

    # 1) 纯耗时
    t0 = time.perf_counter()
    acc = run(N, inputs)
    dt = time.perf_counter() - t0
    print(f"[耗时] {N:,} 次插值: {dt:.3f} s  "
          f"({dt / N * 1e6:.3f} us/次, {N / dt / 1e6:.3f} 百万次/s)  checksum={acc:.6f}")

    # 2) 内存峰值（tracemalloc 统计 Python 堆）
    tracemalloc.start()
    acc = run(N, inputs)
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"[内存] tracemalloc 峰值: {peak / 1024:.1f} KiB "
          f"(结束时 {cur / 1024:.1f} KiB)  checksum={acc:.6f}")

    # 3) 进程峰值 RSS（含解释器与输入数据）
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"[内存] 进程峰值 RSS: {rss_kb / 1024:.1f} MiB "
          f"(含解释器与 4096 组预生成输入；插值本身零额外分配)")


if __name__ == "__main__":
    main()

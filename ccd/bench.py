"""bench.py — 10 万次移动检测耗时与内存峰值。运行：python3 bench.py"""

import random
import time
import tracemalloc

from ccd import Circle, sweep_circle_circle

N = 100_000


def build(rng):
    return [
        Circle(rng.uniform(-50, 50), rng.uniform(-50, 50),
               rng.uniform(-20, 20), rng.uniform(-20, 20),
               rng.uniform(0.3, 2.0))
        for _ in range(N)
    ]


def main():
    # 口径一：检测环节本身（对象预先建好）
    rng = random.Random(7)
    movers = build(rng)
    targets = build(rng)
    hits = 0
    tracemalloc.start()
    t0 = time.perf_counter()
    for a, b in zip(movers, targets):
        ev = sweep_circle_circle(a, b, dt=1.0)
        if ev is not None:
            hits += 1
    elapsed = time.perf_counter() - t0
    _, peak_detect = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # 口径二：整体（生成 20 万个圆对象 + 检测）
    tracemalloc.start()
    rng2 = random.Random(7)
    m2 = build(rng2)
    t2 = build(rng2)
    for a, b in zip(m2, t2):
        sweep_circle_circle(a, b, dt=1.0)
    _, peak_total = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(f"移动次数              : {N}")
    print(f"命中(碰撞/擦边/重叠)  : {hits}")
    print(f"检测总耗时            : {elapsed:.3f} s")
    print(f"单次平均              : {elapsed / N * 1e6:.3f} us")
    print(f"内存峰值·仅检测环节   : {peak_detect / 1024:.1f} KiB（纯标量计算，几乎零分配）")
    print(f"内存峰值·含20万对象   : {peak_total / 1024 / 1024:.2f} MiB（每圆 5 字段, __slots__）")


if __name__ == "__main__":
    main()

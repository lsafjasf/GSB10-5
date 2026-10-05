"""基准：10 万次移动（每次对整场障碍做 CCD 扫描）的耗时与内存峰值。

运行：python3 benchmark.py
"""

import random
import resource
import time
import tracemalloc

from ccd import HIT, TANGENT, sweep_circle_circle, sweep_circle_segment

N_MOVES = 100_000
N_CIRCLES = 30
N_SEGMENTS = 10


def main():
    rng = random.Random(389)
    circles = [(rng.uniform(0, 100), rng.uniform(0, 100), rng.uniform(0.5, 3.0))
               for _ in range(N_CIRCLES)]
    segments = [(rng.uniform(0, 100), rng.uniform(0, 100),
                 rng.uniform(0, 100), rng.uniform(0, 100))
                for _ in range(N_SEGMENTS)]
    # 每次移动：随机位置 + 随机速度（含高速穿透情形），帧长 dt=1/60
    dt = 1.0 / 60.0
    moves = [(rng.uniform(0, 100), rng.uniform(0, 100),
              rng.uniform(-300, 300), rng.uniform(-300, 300),
              rng.uniform(0.2, 1.5)) for _ in range(N_MOVES)]

    tracemalloc.start()
    t0 = time.perf_counter()
    n_hit = n_tangent = 0
    for px, py, vx, vy, r in moves:
        best_t = None
        for cx, cy, cr in circles:
            res = sweep_circle_circle(px, py, vx, vy, r, cx, cy, cr, dt)
            if res is not None and (best_t is None or res[0] < best_t):
                best_t = res[0]
                if res[1] == HIT:
                    n_hit += 1
                elif res[1] == TANGENT:
                    n_tangent += 1
        for ax, ay, bx, by in segments:
            res = sweep_circle_segment(px, py, vx, vy, r, ax, ay, bx, by, dt)
            if res is not None and (best_t is None or res[0] < best_t):
                best_t = res[0]
                if res[1] == HIT:
                    n_hit += 1
                elif res[1] == TANGENT:
                    n_tangent += 1
    t1 = time.perf_counter()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    total = t1 - t0
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    n_checks = N_MOVES * (N_CIRCLES + N_SEGMENTS)
    print(f"场景：{N_CIRCLES} 静止圆 + {N_SEGMENTS} 线段，帧长 dt={dt:.6f}s")
    print(f"移动次数：{N_MOVES:,}（每次扫描 {N_CIRCLES + N_SEGMENTS} 个障碍，"
          f"共 {n_checks:,} 次扫掠查询）")
    print(f"总耗时：{total:.3f} s")
    print(f"每次移动平均：{total / N_MOVES * 1e6:.2f} µs"
          f"（{total / n_checks * 1e9:.1f} ns/查询）")
    print(f"吞吐：{N_MOVES / total:,.0f} 次移动/秒")
    print(f"检出：hit={n_hit:,}  tangent={n_tangent:,}")
    print(f"内存峰值（tracemalloc，循环内 Python 分配）：{peak / 1024:.1f} KiB")
    print(f"进程常驻内存峰值（ru_maxrss）：{rss_kb / 1024:.1f} MiB")


if __name__ == "__main__":
    main()

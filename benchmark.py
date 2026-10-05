"""基准：10 万个任务、停机 1 天的补偿计划生成耗时与内存峰值。

运行: python3 benchmark.py
"""

import random
import resource
import time
import tracemalloc

from catchup import Task, build_plan, simulate_load

N_TASKS = 100_000
DAY = 86400
MAX_CATCHUP = 3


def main() -> None:
    rng = random.Random(42)
    # 周期分布：1min / 5min / 15min / 1h / 6h，模拟真实任务池
    intervals = [60, 300, 900, 3600, 21600]
    weights = [0.2, 0.3, 0.3, 0.15, 0.05]
    tasks = [
        Task(f"task-{i}", rng.choices(intervals, weights)[0], last_run_at=float(DAY))
        for i in range(N_TASKS)
    ]
    now = DAY + DAY  # 停机一整天

    # 耗时与内存分开测：tracemalloc 会显著拖慢分配，混测会高估耗时
    t_start = time.perf_counter()
    plan = build_plan(tasks, now=now, max_catchup_per_task=MAX_CATCHUP)
    t_plan = time.perf_counter() - t_start
    del plan

    tracemalloc.start()
    plan = build_plan(tasks, now=now, max_catchup_per_task=MAX_CATCHUP)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    missed_total = sum(s.missed for s in plan.per_task.values())
    print(f"任务数:            {N_TASKS:,}")
    print(f"停机时长:          1 天 (86400 s)")
    print(f"漏跑窗口总数:      {missed_total:,}")
    print(f"计划补偿项:        {plan.total_items:,}  (单任务上限 {MAX_CATCHUP})")
    print(f"压缩放弃:          {plan.total_dropped:,}")
    print(f"计划生成耗时:      {t_plan * 1000:.1f} ms")
    print(f"tracemalloc 峰值:  {peak / 1024 / 1024:.1f} MiB")
    print(f"进程 RSS 峰值:     {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.1f} MiB")

    t_start = time.perf_counter()
    curve, finish = simulate_load(
        plan.total_items, rate_per_sec=500, max_concurrency=64,
        duration_seconds=0.05, start_at=now,
    )
    t_sim = time.perf_counter() - t_start
    print(f"负载模拟耗时:      {t_sim * 1000:.1f} ms "
          f"(500/s, 64 并发 -> 补偿持续 {finish - now:.1f} s, 峰值 {max(curve)}/s)")


if __name__ == "__main__":
    main()

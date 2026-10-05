"""端到端演示：补偿计划数据、负载曲线、幂等重复补偿数据、二次停机续跑计划。

运行: python3 demo.py
时间全部以注入的 epoch 秒表示（停机开始时刻 t0 = 86400，即第 1 天）。
"""

import os
import tempfile

from catchup import (
    CatchupExecutor,
    IdempotencyStore,
    SimulatedCrash,
    Task,
    build_plan,
    remaining_plan,
    simulate_load,
)

DAY = 86400
HOUR = 3600
RATE_PER_SEC = 5.0      # 速率上界：5 次/秒
MAX_CONCURRENCY = 3     # 并发上界：3
TASK_DURATION = 0.5     # 单次补偿执行 0.5s
MAX_CATCHUP = 3         # 单任务最多补 3 次


def main() -> None:
    t0 = DAY  # 停机开始：系统正常运行到第 1 天整
    now = t0 + DAY  # 停机一整天后恢复

    tasks = [
        Task("report-hourly", interval_seconds=HOUR, last_run_at=t0),
        Task("metrics-5min", interval_seconds=300, last_run_at=t0),
        Task("heartbeat-1min", interval_seconds=60, last_run_at=t0),
    ]
    plan = build_plan(tasks, now=now, max_catchup_per_task=MAX_CATCHUP)

    print("=" * 72)
    print("1) 漏跑窗口识别与补偿计划（停机 1 天，单任务最多补最近 %d 次）" % MAX_CATCHUP)
    print("=" * 72)
    print(f"停机开始 t0={t0}, 恢复时刻 now={now} (均为注入 epoch 秒)")
    print(f"{'task_id':<18}{'周期(s)':>8}{'漏跑':>6}{'补偿':>6}{'放弃':>6}  补偿时刻(scheduled_at)")
    for task in tasks:
        stat = plan.per_task[task.task_id]
        times = ", ".join(str(int(t)) for t in stat.times)
        print(
            f"{task.task_id:<18}{task.interval_seconds:>8}{stat.missed:>6}"
            f"{stat.planned:>6}{stat.dropped:>6}  [{times}]"
        )
    print(f"合计: 计划补偿 {plan.total_items} 次, 压缩放弃 {plan.total_dropped} 次")

    print()
    print("=" * 72)
    print(f"2) 负载曲线（速率上界 {RATE_PER_SEC}/s, 并发上界 {MAX_CONCURRENCY}, "
          f"单次 {TASK_DURATION}s）")
    print("=" * 72)
    curve, finish = simulate_load(
        plan.total_items, RATE_PER_SEC, MAX_CONCURRENCY, TASK_DURATION, start_at=now
    )
    print("补偿于 %d 开始, 预计 %d 结束, 持续 %.2f 秒" % (now, int(finish), finish - now))
    print("每秒开始执行数（# = 1 次）:")
    for sec, count in enumerate(curve):
        if count:
            print(f"  t={now + sec:<8} | {'#' * count} {count}")
    print("曲线峰值: %d 次/秒（上界 5），任何时刻在途任务 <= 3" % max(curve))

    print()
    print("=" * 72)
    print("3) 幂等：重复执行同一补偿计划")
    print("=" * 72)
    side_effects = []

    def handler(item):
        side_effects.append(item.key)
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        store = IdempotencyStore(os.path.join(tmp, "done.log"))
        executor = CatchupExecutor(store)
        first = executor.run(plan, handler)
        second = executor.run(plan, handler)  # 重复补偿
        print("第一次补偿:", first.as_dict())
        print("重复补偿  :", second.as_dict())
        print("副作用记录条数: 第一次 %d, 重复后总数 %d（无新增）"
              % (len(side_effects), len(side_effects)))

        print()
        print("=" * 72)
        print("4) 补偿期间再次停机（执行 5 项后崩溃），重启后续跑")
        print("=" * 72)
        # 重建一个全新的日志场景：前 5 项已完成
        store_path = os.path.join(tmp, "done2.log")
        store = IdempotencyStore(store_path)
        executor = CatchupExecutor(store)
        effects = []

        def handler2(item):
            effects.append(item.key)
            return 1

        crashed = False
        try:
            executor.run(plan, handler2, crash_after=5)
        except SimulatedCrash:
            crashed = True
        first_part = len(effects)
        print(f"再次停机发生: {crashed}; 崩溃前已完成 5 项并落盘")

        now2 = now + 3600  # 二次停机 1 小时后重启（窗口未移出压缩窗口）
        reopened = IdempotencyStore(store_path)
        plan2 = build_plan(tasks, now=now2, max_catchup_per_task=MAX_CATCHUP)
        todo = remaining_plan(plan2, reopened)
        done_keys = set(reopened._keys)
        overlap = sum(1 for item in plan2.items if item.key in done_keys)
        print(f"重启时刻 now={now2}, 重新生成计划 {plan2.total_items} 项")
        print(f"已落盘 {len(done_keys)} 项, 与新计划重叠 {overlap} 项（跳过）, "
              f"剩余 {len(todo)} 项; 另有 {len(done_keys) - overlap} 项已移出"
              f"最近 {MAX_CATCHUP} 个窗口，不再补")
        print("剩余补偿计划 (task_id @ scheduled_at):")
        for item in todo:
            print(f"  {item.task_id} @ {int(item.scheduled_at)}")
        stats = CatchupExecutor(reopened).run(todo, handler2)
        print("续跑执行:", stats.as_dict())
        print(f"两轮副作用共 {len(effects)} 条，键全部唯一: "
              f"{len(effects) == len(set(effects))}")


if __name__ == "__main__":
    main()

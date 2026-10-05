"""补偿库自测：无漏跑 / 漏跑一个窗口 / 停机很久 / 补偿中再次停机 / 幂等 / 上界。"""

import os
import tempfile
import unittest

from catchup import (
    CatchupExecutor,
    CatchupItem,
    IdempotencyStore,
    SimulatedCrash,
    Task,
    build_plan,
    missed_slots,
    remaining_plan,
    simulate_load,
)

HOUR = 3600
DAY = 86400


def side_effect_recorder():
    calls = []

    def handler(item: CatchupItem) -> int:
        calls.append(item.key)
        return 1  # 每次执行产生 1 条副作用

    return calls, handler


class TestMissedWindow(unittest.TestCase):
    def test_no_miss(self):
        # 上次执行就在当前时刻，无漏跑
        task = Task("t1", HOUR, last_run_at=10 * HOUR)
        plan = build_plan([task], now=10 * HOUR)
        self.assertEqual(plan.total_items, 0)
        self.assertEqual(plan.per_task["t1"].missed, 0)

    def test_no_miss_within_window(self):
        # 距上次执行不足一个周期
        task = Task("t1", HOUR, last_run_at=10 * HOUR)
        plan = build_plan([task], now=10 * HOUR + 1800)
        self.assertEqual(plan.total_items, 0)

    def test_exactly_one_missed_window(self):
        task = Task("t1", HOUR, last_run_at=10 * HOUR)
        plan = build_plan([task], now=11 * HOUR)
        self.assertEqual(plan.total_items, 1)
        self.assertEqual(plan.items[0].scheduled_at, 11 * HOUR)
        stat = plan.per_task["t1"]
        self.assertEqual((stat.missed, stat.planned, stat.dropped), (1, 1, 0))

    def test_missed_slots_alignment(self):
        # 计划时刻严格对齐 interval 整数倍，last_run_at 未对齐也能正确识别
        self.assertEqual(missed_slots(60, 90, 300), [120.0, 180.0, 240.0, 300.0])
        self.assertEqual(missed_slots(60, 120, 119), [])


class TestLongDowntime(unittest.TestCase):
    def test_day_down_capped(self):
        # 停机一天、每分钟一次的任务：漏跑 1440 次，只补最近 3 次
        task = Task("cron-1min", 60, last_run_at=0.0)
        plan = build_plan([task], now=DAY, max_catchup_per_task=3)
        stat = plan.per_task["cron-1min"]
        self.assertEqual(stat.missed, 1440)
        self.assertEqual(stat.planned, 3)
        self.assertEqual(stat.dropped, 1437)
        self.assertEqual(stat.times, [DAY - 120.0, DAY - 60.0, float(DAY)])

    def test_long_downtime_keeps_most_recent(self):
        task = Task("hourly", HOUR, last_run_at=0.0)
        plan = build_plan([task], now=30 * DAY, max_catchup_per_task=5)
        stat = plan.per_task["hourly"]
        self.assertEqual(stat.missed, 720)
        self.assertEqual(stat.planned, 5)
        self.assertEqual(stat.times[-1], 30.0 * DAY)


class TestIdempotency(unittest.TestCase):
    def test_repeat_run_no_duplicate_side_effects(self):
        tasks = [Task(f"t{i}", 60, last_run_at=0.0) for i in range(10)]
        plan = build_plan(tasks, now=DAY, max_catchup_per_task=3)
        with tempfile.TemporaryDirectory() as tmp:
            store = IdempotencyStore(os.path.join(tmp, "done.log"))
            executor = CatchupExecutor(store)

            calls1, handler1 = side_effect_recorder()
            first = executor.run(plan, handler1)
            self.assertEqual(first.executed, 30)
            self.assertEqual(first.side_effects, 30)
            self.assertEqual(first.skipped_duplicate, 0)

            # 重复执行同一计划：全部命中幂等日志，零副作用
            calls2, handler2 = side_effect_recorder()
            second = executor.run(plan, handler2)
            self.assertEqual(second.executed, 0)
            self.assertEqual(second.side_effects, 0)
            self.assertEqual(second.skipped_duplicate, 30)
            self.assertEqual(calls2, [])
            self.assertEqual(len(store), 30)

    def test_store_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "done.log")
            store = IdempotencyStore(path)
            store.mark("a@100")
            store.mark("a@200")
            reopened = IdempotencyStore(path)
            self.assertIn("a@100", reopened)
            self.assertIn("a@200", reopened)
            self.assertFalse(reopened.mark("a@100"))


class TestCrashDuringCatchup(unittest.TestCase):
    def test_second_downtime_resumes_remaining(self):
        # 补偿执行到一半再次停机，短暂停机后重启：续上原计划剩余部分
        tasks = [Task(f"t{i}", 60, last_run_at=0.0) for i in range(10)]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "done.log")
            store = IdempotencyStore(path)
            executor = CatchupExecutor(store)

            plan1 = build_plan(tasks, now=DAY, max_catchup_per_task=3)
            self.assertEqual(plan1.total_items, 30)

            # 补偿执行到第 12 项时再次停机
            calls, handler = side_effect_recorder()
            with self.assertRaises(SimulatedCrash):
                executor.run(plan1, handler, crash_after=12)
            self.assertEqual(len(calls), 12)

            # 二次停机 30 秒后重启：漏跑窗口不变，重新生成的计划与原计划一致
            now2 = DAY + 30
            store2 = IdempotencyStore(path)  # 重启后重新加载日志
            plan2 = build_plan(tasks, now=now2, max_catchup_per_task=3)
            remaining = remaining_plan(plan2, store2)

            executor2 = CatchupExecutor(store2)
            calls2, handler2 = side_effect_recorder()
            stats = executor2.run(remaining, handler2)

            # 第一轮已完成 12 项；第二轮计划 30 项中有 12 项的键已在日志里
            self.assertEqual(plan2.total_items, 30)
            self.assertEqual(len(remaining), 18)
            self.assertEqual(stats.executed, 18)
            self.assertEqual(stats.side_effects, 18)
            # 两轮合计恰好 30 次副作用，无重复
            self.assertEqual(len(set(calls + calls2)), 30)
            self.assertEqual(len(store2), 30)

            # 再次重复执行：零副作用
            calls3, handler3 = side_effect_recorder()
            stats3 = executor2.run(remaining_plan(plan2, store2), handler3)
            self.assertEqual(stats3.executed, 0)
            self.assertEqual(calls3, [])

    def test_long_second_downtime_windows_shift(self):
        # 二次停机很久：漏跑窗口整体后移，新计划时刻与原计划不重叠，
        # 幂等日志保证两部分都不会重复执行
        tasks = [Task(f"t{i}", 60, last_run_at=0.0) for i in range(10)]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "done.log")
            store = IdempotencyStore(path)
            executor = CatchupExecutor(store)

            plan1 = build_plan(tasks, now=DAY, max_catchup_per_task=3)
            calls, handler = side_effect_recorder()
            with self.assertRaises(SimulatedCrash):
                executor.run(plan1, handler, crash_after=12)

            # 二次停机 2 小时：新计划只保留最新 3 个窗口
            now2 = DAY + 2 * HOUR
            store2 = IdempotencyStore(path)
            plan2 = build_plan(tasks, now=now2, max_catchup_per_task=3)
            remaining = remaining_plan(plan2, store2)
            self.assertEqual(len(remaining), 30)
            self.assertEqual(remaining[0].scheduled_at, now2 - 120.0)

            executor2 = CatchupExecutor(store2)
            calls2, handler2 = side_effect_recorder()
            stats = executor2.run(remaining, handler2)
            self.assertEqual(stats.executed, 30)
            # 两轮共 42 次副作用，键无一重复
            self.assertEqual(len(set(calls + calls2)), 42)


class TestLoadBounds(unittest.TestCase):
    def test_rate_bound(self):
        curve, _ = simulate_load(
            n_items=1000, rate_per_sec=100, max_concurrency=1000,
            duration_seconds=0.001, start_at=0.0,
        )
        self.assertTrue(curve)
        self.assertLessEqual(max(curve), 100)

    def test_concurrency_bound(self):
        # 并发 2、单任务 1s：即使速率上界是 1000/s，每秒最多完成 2 个开始
        curve, finish = simulate_load(
            n_items=10, rate_per_sec=1000, max_concurrency=2,
            duration_seconds=1.0, start_at=0.0,
        )
        self.assertLessEqual(max(curve), 2)
        self.assertGreaterEqual(finish, 5.0)  # 10 个任务 / 2 并发 * 1s

    def test_empty_load(self):
        curve, finish = simulate_load(0, 100, 10, 0.1, start_at=5.0)
        self.assertEqual(curve, [])
        self.assertEqual(finish, 5.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

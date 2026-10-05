"""窗口对齐自测（标准库 unittest，时间全部由测试注入）。

运行：python3 -m unittest -v test_windowing
"""

import random
import unittest

from windowing import (
    ON_TIME, LATE, DROPPED, WindowAssigner, WindowedCounter,
)


class TestBoundaryOwnership(unittest.TestCase):
    """半开 [start, end)：边界值归属哪一侧必须明确。"""

    def setUp(self):
        self.a = WindowAssigner(size_ms=10)

    def test_inside_belongs_left_window(self):
        # [0,10)：0 与 9 都在窗口 [0,10)
        for ts in (0, 1, 5, 9):
            self.assertEqual(self.a.window_of(ts), (0, 10), f"ts={ts}")

    def test_right_boundary_belongs_next_window(self):
        # 恰好落在右边界 10 上 -> 归属【下一个】窗口 [10,20)（右侧）
        self.assertEqual(self.a.window_of(10), (10, 20))
        self.assertEqual(self.a.window_of(20), (20, 30))

    def test_partition_is_complete_and_disjoint(self):
        # 覆盖一大段连续整数，每个时间恰好落到一个窗口，无缝、无重叠
        seen = {}
        for ts in range(-30, 1000):
            start, end = self.a.window_of(ts)
            self.assertTrue(start <= ts < end, f"ts={ts} 不在自己的窗口内")
            self.assertEqual(end - start, 10)
            seen.setdefault(start, set()).add(ts)
        for start, members in seen.items():
            self.assertEqual(members, set(range(start, start + 10)))

    def test_offset_alignment(self):
        a = WindowAssigner(size_ms=10, offset_ms=3)
        self.assertEqual(a.window_of(3), (3, 13))
        self.assertEqual(a.window_of(12), (3, 13))
        self.assertEqual(a.window_of(13), (13, 23))  # 右边界 -> 下一窗


class TestLateness(unittest.TestCase):
    def test_on_time_late_dropped_classification(self):
        # size=10，允许迟到 5；水位线 25 由测试注入
        a = WindowAssigner(size_ms=10, allowed_lateness_ms=5)

        # [20,30) 的 end=30 > 25 -> on_time
        self.assertEqual(a.classify(24, 25).status, ON_TIME)
        # 9 属于 [0,10)，end=10 <= wm25；end+lateness=15 <= 25 -> 超迟到丢弃
        d = a.classify(9, 25)
        self.assertEqual(d.status, DROPPED)
        self.assertEqual((d.window_start, d.window_end), (0, 10))
        # 21 属于 [20,30) end=30>25 正常，不算迟到（迟到看窗口而非单条事件）
        self.assertEqual(a.classify(21, 25).status, ON_TIME)

    def test_late_merged_and_marked(self):
        # size=10, allowed_lateness=20, wm=25
        a = WindowAssigner(size_ms=10, allowed_lateness_ms=20)
        c = WindowedCounter(a)
        # ts=9 窗口 [0,10)，end+lat=30 > 25 -> LATE，并入原窗口并标记
        r = c.add(9, 25)
        self.assertEqual(r.status, LATE)
        self.assertEqual(r.window_start, 0)
        res = c.result()
        self.assertEqual(res["counts"], {0: 1})      # 已并入原窗口（重算后）
        self.assertEqual(res["late_counts"], {0: 1})
        self.assertEqual(res["dropped"], 0)

    def test_super_late_dropped_to_side_output(self):
        a = WindowAssigner(size_ms=10, allowed_lateness_ms=5)
        c = WindowedCounter(a)
        c.add(0, 25)   # [0,10)，end+lat=15 <= 25 -> 超迟到
        c.add(20, 25)  # [20,30) 正常
        res = c.result()
        self.assertEqual(res["counts"], {20: 1})
        self.assertEqual(res["dropped"], 1)          # 侧输出可对账
        # 总量守恒：计入窗口 + 丢弃 = 输入总数
        self.assertEqual(sum(res["counts"].values()) + res["dropped"], 2)

    def test_no_allowed_lateness_boundary_is_strict(self):
        a = WindowAssigner(size_ms=10, allowed_lateness_ms=0)
        # end <= wm 立即丢弃；wm 恰好等于 end 时（wm=10, 窗口[0,10)）即丢
        self.assertEqual(a.classify(9, 10).status, DROPPED)
        self.assertEqual(a.classify(10, 10).status, ON_TIME)  # 下一窗 end=20>10


class TestClockSkew(unittest.TestCase):
    """时钟偏移：事件时间可能为负、可能远早/远晚于处理时钟。

    归属只依赖事件时间本身，处理时钟（注入的 watermark）只影响迟到判定。
    """

    def test_negative_event_times(self):
        a = WindowAssigner(size_ms=10)
        self.assertEqual(a.window_of(-1), (-10, 0))
        self.assertEqual(a.window_of(-10), (-10, 0))  # 右边界规则对负数一致
        self.assertEqual(a.window_of(-11), (-20, -10))
        self.assertEqual(a.window_of(0), (0, 10))

    def test_skewed_device_way_behind(self):
        # 设备时钟比服务器慢 1 小时，产生"看起来很早"的事件
        a = WindowAssigner(size_ms=60_000, allowed_lateness_ms=60_000)
        skew_ts = 0
        wm = 3_600_000
        # 偏移事件窗口 [0,60000)，end+lat=120000 <= 3_600_000 -> 超迟到标记
        self.assertEqual(a.classify(skew_ts, wm).status, DROPPED)

    def test_skewed_device_way_ahead(self):
        # 设备时钟快，事件时间远在未来：永远 on_time，窗口照常对齐
        a = WindowAssigner(size_ms=10)
        r = a.classify(10**9, 1000)
        self.assertEqual(r.status, ON_TIME)
        self.assertTrue(r.window_start <= 10**9 < r.window_end)

    def test_processing_clock_does_not_move_assignment(self):
        # 同一事件在不同注入时钟下，窗口归属不变，只有迟到状态可能变
        a = WindowAssigner(size_ms=10, allowed_lateness_ms=100)
        w1 = a.classify(15, 0)
        w2 = a.classify(15, 500)
        self.assertEqual((w1.window_start, w1.window_end), (10, 20))
        self.assertEqual((w2.window_start, w2.window_end), (10, 20))
        self.assertNotEqual(w1.status, w2.status)


class TestOrderIndependence(unittest.TestCase):
    """结果与到达顺序无关：乱序到达对拍。"""

    DATA = [0, 5, 10, 19, 20, 20, 29, 3, 100, 7, 9, 41, 30, 99, 1, 49]

    def _run(self, order, watermark):
        a = WindowAssigner(size_ms=10, allowed_lateness_ms=15)
        c = WindowedCounter(a)
        c.add_batch(order, watermark=watermark)
        return c.result()

    def test_shuffled_orders_identical(self):
        watermark = 100  # 固定注入水位线，排除时间推进的干扰
        orders = [
            sorted(self.DATA),
            sorted(self.DATA, reverse=True),
            self.DATA,
        ]
        rng = random.Random(42)
        for _ in range(20):
            shuffled = self.DATA[:]
            rng.shuffle(shuffled)
            orders.append(shuffled)

        baseline = self._run(orders[0], watermark)
        for i, order in enumerate(orders[1:], start=1):
            self.assertEqual(self._run(order, watermark), baseline, f"第{i}种顺序结果不同")

    def test_batch_watermark_is_order_free(self):
        # 不显式注入 watermark 时取 max(event_time)，仍然与顺序无关
        def run(order):
            a = WindowAssigner(size_ms=10, allowed_lateness_ms=15)
            c = WindowedCounter(a)
            c.add_batch(order)
            return c.result()
        rng = random.Random(7)
        o1 = self.DATA[:]
        o2 = self.DATA[:]
        rng.shuffle(o1)
        rng.shuffle(o2)
        self.assertEqual(run(o1), run(o2))

    def test_assignments_are_per_record_and_total_conserved(self):
        a = WindowAssigner(size_ms=10, allowed_lateness_ms=15)
        assigned = a.assign_batch(self.DATA, watermark=100)
        # 每条数据都有判定结果；归属区间半开覆盖；状态仅三种
        self.assertEqual(len(assigned), len(self.DATA))
        statuses = set()
        for x in assigned:
            self.assertTrue(x.window_start <= x.event_time < x.window_end)
            self.assertEqual(x.window_end - x.window_start, 10)
            statuses.add(x.status)
        self.assertTrue(statuses <= {ON_TIME, LATE, DROPPED})


if __name__ == "__main__":
    unittest.main(verbosity=2)

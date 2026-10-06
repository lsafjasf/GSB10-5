"""批处理窗口对齐自测：python3 -m unittest tests.test_window_align -v

覆盖：边界归属、迟到并入并重算、超迟到丢弃标记、时钟偏移修正、
负时间唯一归属、到达顺序无关（乱序对拍）、注入时钟的流式行为。
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from window_align import (
    ON_TIME, LATE_MERGED, TOO_LATE_DROPPED,
    Record, window_of, classify, batch_align, WindowAligner,
)

W = 60
ORIGIN = 1000  # 窗口起点不对齐 epoch 0，避免"碰巧通过"


def snapshot(assignments, results):
    """可比较的结果快照：每条数据去向 + 每个窗口聚合 + 丢弃列表。"""
    a = sorted((x.corrected_event_time,
                x.window.start if x.window else None,
                x.status, x.lateness) for x in assignments)
    r = [x.as_tuple() for x in results]
    dropped = sorted(x.record.event_time for x in assignments
                     if x.status == TOO_LATE_DROPPED)
    return a, r, dropped


class TestBoundary(unittest.TestCase):
    def test_left_boundary_belongs_to_this_window(self):
        win = window_of(ORIGIN + W, W, ORIGIN)
        self.assertEqual(win.start, ORIGIN + W)
        self.assertEqual(win.end, ORIGIN + 2 * W)
        self.assertTrue(win.contains(ORIGIN + W))      # 左边界在窗口内
        self.assertFalse(win.contains(ORIGIN + 2 * W))  # 右边界归下一窗

    def test_right_boundary_goes_to_next_window(self):
        a = window_of(ORIGIN + 2 * W - 1, W, ORIGIN)
        b = window_of(ORIGIN + 2 * W, W, ORIGIN)
        self.assertEqual(b.index, a.index + 1)

    def test_origin_and_negative_times_have_unique_window(self):
        self.assertEqual(window_of(ORIGIN, W, ORIGIN).index, 0)
        prev = None
        for t in range(ORIGIN - W - 1, ORIGIN + 2 * W + 2):
            win = window_of(t, W, ORIGIN)
            self.assertTrue(win.contains(t))  # 每条数据都落在窗口里
            self.assertEqual(win.end - win.start, W)
            if prev is not None and win.index != prev.index:
                self.assertEqual(win.start - prev.start, W)  # 跨窗首尾相接
            prev = win

    def test_windows_tile_without_gap_or_overlap(self):
        starts = {window_of(t, W, ORIGIN).start for t in
                  range(ORIGIN - 1000, ORIGIN + 1000)}
        s = sorted(starts)
        for x, y in zip(s, s[1:]):
            self.assertEqual(y - x, W)  # 相邻窗口首尾相接


class TestLateness(unittest.TestCase):
    def test_on_time(self):
        status, lateness = classify(100, watermark=100, allowed_lateness=10)
        self.assertEqual(status, ON_TIME)
        self.assertEqual(lateness, 0)

    def test_late_merged_and_revised(self):
        records = [Record(event_time=1000, value=2.0),
                   Record(event_time=1070, value=3.0),
                   Record(event_time=1005, value=7.0)]  # 迟到但在宽限内
        assignments, results = batch_align(
            records, W, watermark=1060, allowed_lateness=60, origin=ORIGIN)
        statuses = {a.record.event_time: a.status for a in assignments}
        self.assertEqual(statuses[1005], LATE_MERGED)
        win_1000 = [r for r in results
                    if r.window.start <= 1005 < r.window.end][0]
        self.assertEqual(win_1000.count, 2)       # 1000 + 1005 同窗
        self.assertAlmostEqual(win_1000.total, 9.0)
        self.assertTrue(win_1000.revised)         # 触发重算标记
        win_1070 = [r for r in results
                    if r.window.start <= 1070 < r.window.end][0]
        self.assertFalse(win_1070.revised)

    def test_too_late_dropped_with_reason(self):
        records = [Record(event_time=1000), Record(event_time=900)]
        assignments, results = batch_align(
            records, W, watermark=1005, allowed_lateness=10, origin=ORIGIN)
        dropped = [a for a in assignments if a.status == TOO_LATE_DROPPED]
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0].record.event_time, 900)
        self.assertGreater(dropped[0].lateness, 10)
        self.assertIsNone(dropped[0].window)
        self.assertTrue(all(r.count == 1 for r in results))

    def test_lateness_boundary_is_inclusive(self):
        # lateness 恰好等于 allowed_lateness -> 允许并入（宽限取闭区间）
        self.assertEqual(classify(1000, 1060, 60)[0], LATE_MERGED)
        self.assertEqual(classify(1000, 1061, 60)[0], TOO_LATE_DROPPED)


class TestClockSkew(unittest.TestCase):
    def test_corrected_event_time_used_for_assignment(self):
        records = [Record(event_time=995)]  # 源时钟慢 30，实际 1025
        assignments, _ = batch_align(
            records, W, watermark=1020, allowed_lateness=0,
            clock_offset=30, origin=ORIGIN)
        a = assignments[0]
        self.assertEqual(a.corrected_event_time, 1025)
        self.assertEqual(a.window.start, 1000)
        self.assertEqual(a.status, ON_TIME)

    def test_skew_turns_apparent_late_into_on_time(self):
        records = [Record(event_time=1000)]
        a_no_fix, _ = batch_align(records, W, 1025, allowed_lateness=0,
                                  clock_offset=0, origin=ORIGIN)
        a_fix, _ = batch_align(records, W, 1025, allowed_lateness=0,
                               clock_offset=30, origin=ORIGIN)
        self.assertEqual(a_no_fix[0].status, TOO_LATE_DROPPED)
        self.assertEqual(a_fix[0].status, ON_TIME)


class TestOrderIndependence(unittest.TestCase):
    # 固定对拍数据：含边界点、跨多窗、迟到、超迟到
    FIXTURE = [
        Record(event_time=1000, value=1.0),   # 左边界
        Record(event_time=1059, value=2.0),
        Record(event_time=1060, value=3.0),   # 下一窗左边界
        Record(event_time=1119, value=4.0),   # 右边界前一刻
        Record(event_time=1120, value=5.0),
        Record(event_time=1001, value=6.0),   # 迟到但宽限内
        Record(event_time=900, value=9.0),    # 超迟到
    ]
    WATERMARK = 1060  # 1000 恰好迟到 60（宽限边界），900 超迟到
    GRACE = 60

    def _run(self, data):
        return batch_align(data, W, self.WATERMARK, self.GRACE, origin=ORIGIN)

    def test_shuffled_arrivals_identical_results(self):
        expected = snapshot(*self._run(self.FIXTURE))
        rng = random.Random(42)
        for _ in range(20):
            shuffled = self.FIXTURE[:]
            rng.shuffle(shuffled)
            self.assertEqual(snapshot(*self._run(shuffled)), expected)

    def test_every_record_accounted_for(self):
        assignments, results = self._run(self.FIXTURE)
        in_windows = sum(r.count for r in results)
        n_dropped = sum(1 for a in assignments
                        if a.status == TOO_LATE_DROPPED)
        self.assertEqual(in_windows + n_dropped, len(self.FIXTURE))

    def test_reverse_order(self):
        s1 = snapshot(*self._run(self.FIXTURE))
        s2 = snapshot(*self._run(list(reversed(self.FIXTURE))))
        self.assertEqual(s1, s2)


class TestStreamingWithInjectedClock(unittest.TestCase):
    def test_injected_clock_drives_watermark(self):
        ticks = iter([1000, 1000, 1060])
        aligner = WindowAligner(W, clock=lambda: next(ticks),
                                allowed_lateness=10, origin=ORIGIN)
        a1 = aligner.ingest(Record(event_time=1000))
        a2 = aligner.ingest(Record(event_time=1030))
        a3 = aligner.ingest(Record(event_time=1000))  # 迟到 60，超宽限
        self.assertEqual(a1.status, ON_TIME)
        self.assertEqual(a2.status, ON_TIME)
        self.assertEqual(a3.status, TOO_LATE_DROPPED)
        self.assertEqual(len(aligner.dropped), 1)
        starts = [r.window.start for r in aligner.results()]
        self.assertEqual(starts, sorted(starts))

    def test_late_merge_marks_window_revised(self):
        now = [1000]
        aligner = WindowAligner(W, clock=lambda: now[0],
                                allowed_lateness=60, origin=ORIGIN)
        aligner.ingest(Record(event_time=1000, value=1.0))
        now[0] = 1050
        aligner.ingest(Record(event_time=1010, value=2.0))  # 迟到并入
        r = aligner.results()[0]
        self.assertEqual(r.count, 2)
        self.assertAlmostEqual(r.total, 3.0)
        self.assertTrue(r.revised)


if __name__ == "__main__":
    unittest.main(verbosity=2)

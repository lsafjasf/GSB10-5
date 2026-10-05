"""Debouncer 自测：时间全部由测试注入（显式 ts / now），不依赖真实时钟。"""

import unittest

from debouncer import (
    Debouncer,
    REASON_DIR_COLLAPSE,
    REASON_MAX_WAIT,
    REASON_QUIET,
    REASON_SEQ_GAP,
)

WINDOW = 0.05      # 50ms 静默窗口
MAX_WAIT = 0.5     # 500ms 最大等待
DIR_THRESHOLD = 8  # 同目录 >=8 个到期 path 折叠为目录事件


def make(**kw):
    kw.setdefault("window", WINDOW)
    kw.setdefault("max_wait", MAX_WAIT)
    kw.setdefault("dir_threshold", DIR_THRESHOLD)
    return Debouncer(**kw)


class TestSingleFileWrite(unittest.TestCase):
    """情形 1：单文件一次逻辑写入（编辑器保存常拆成 create+modify*3+attrib）。"""

    def test_one_logical_write_fires_once(self):
        d = make()
        raw = [
            ("/w/a.txt", "create", 1.000),
            ("/w/a.txt", "modify", 1.002),
            ("/w/a.txt", "modify", 1.005),
            ("/w/a.txt", "attrib", 1.007),
            ("/w/a.txt", "modify", 1.009),
        ]
        for path, kind, ts in raw:
            d.feed(path, kind, ts)

        self.assertEqual(d.tick(1.010), [])          # 静默窗口未满，不触发
        self.assertEqual(d.tick(1.040), [])          # 距最后事件 31ms < 50ms
        fired = d.tick(1.060)                        # 距最后事件 51ms >= 50ms
        self.assertEqual(len(fired), 1)              # 一次逻辑写入只触发一次
        ev = fired[0]
        self.assertEqual(ev.key, "/w/a.txt")
        self.assertEqual(ev.count, 5)                # 5 个原始事件合并
        self.assertEqual(ev.kinds, {"create", "modify", "attrib"})
        self.assertEqual(ev.reason, REASON_QUIET)
        self.assertEqual(d.tick(2.0), [])            # 不重复触发
        self.assertEqual(d.events_in, 5)
        self.assertEqual(d.events_out, 1)


class TestBatchWrite(unittest.TestCase):
    """情形 2：批量写入——50 个文件分布在 10 个目录（每目录 5 个 < 阈值）。"""

    def test_batch_write_fires_per_path(self):
        d = make()
        n_files, events_per_file = 50, 5
        t = 10.0
        for i in range(n_files):
            path = f"/w/dir{i % 10}/f{i}.txt"
            for j in range(events_per_file):
                d.feed(path, "modify", t + i * 0.001 + j * 0.0001)

        fired = d.tick(11.0)
        self.assertEqual(len(fired), n_files)        # 每文件恰好 1 次
        self.assertTrue(all(not e.is_dir_event for e in fired))
        self.assertEqual(sum(e.count for e in fired), n_files * events_per_file)
        self.assertEqual(d.events_in, 250)
        self.assertEqual(d.events_out, 50)


class TestSustainedAppend(unittest.TestCase):
    """情形 3：持续追加——每 10ms 写一次，持续 5s。静默窗口永远不满足，
    必须由 max_wait 兜底：每 500ms 确定触发一次，共 10 次。"""

    def test_sustained_append_fires_every_max_wait(self):
        d = make()
        fired_total = []
        for t_ms in range(0, 5000, 10):              # 整数毫秒，避免浮点漂移
            d.feed("/w/log.txt", "modify", t_ms / 1000.0)
            fired_total.extend(d.tick(t_ms / 1000.0))
        fired_total.extend(d.tick(5.0))              # 收尾

        self.assertEqual(len(fired_total), 10)       # 5s / 500ms = 10 次
        self.assertTrue(all(e.reason == REASON_MAX_WAIT for e in fired_total))
        self.assertEqual(sum(e.count for e in fired_total), 500)
        self.assertTrue(all(e.count == 50 for e in fired_total))
        # 触发时刻确定：0.5, 1.0, ..., 5.0
        for i, ev in enumerate(fired_total, start=1):
            self.assertAlmostEqual(ev.first_ts + MAX_WAIT, i * MAX_WAIT, places=6)


class TestDirBatchReplace(unittest.TestCase):
    """情形 4：目录整批替换——同目录 20 个文件 delete+create+modify。"""

    def test_dir_replace_collapses_to_one_event(self):
        d = make()
        t = 20.0
        for i in range(20):
            path = f"/w/assets/f{i}.dat"
            d.feed(path, "delete", t + i * 0.001)
            d.feed(path, "create", t + i * 0.001 + 0.0005)
            d.feed(path, "modify", t + i * 0.001 + 0.0008)

        fired = d.tick(21.0)
        self.assertEqual(len(fired), 1)              # 60 个原始事件 -> 1 次触发
        ev = fired[0]
        self.assertTrue(ev.is_dir_event)
        self.assertEqual(ev.key, "/w/assets")
        self.assertEqual(ev.count, 60)
        self.assertEqual(ev.kinds, {"delete", "create", "modify"})
        self.assertEqual(ev.reason, REASON_DIR_COLLAPSE)
        self.assertEqual(d.events_in, 60)
        self.assertEqual(d.events_out, 1)

    def test_below_threshold_no_collapse(self):
        d = make()
        for i in range(DIR_THRESHOLD - 1):           # 7 个 < 阈值 8
            d.feed(f"/w/small/f{i}.dat", "modify", 30.0)
        fired = d.tick(31.0)
        self.assertEqual(len(fired), DIR_THRESHOLD - 1)
        self.assertTrue(all(not e.is_dir_event for e in fired))


class TestOutOfOrder(unittest.TestCase):
    """乱序事件：绝不丢弃，仍合并为一次触发。"""

    def test_out_of_order_events_not_lost(self):
        d = make()
        # 同一 path 的 6 个事件，时间戳打乱喂入
        shuffled = [1.020, 1.000, 1.015, 1.005, 1.025, 1.010]
        for ts in shuffled:
            d.feed("/w/a.txt", "modify", ts)

        fired = d.tick(1.04)                         # 距最大 ts(1.025) 仅 15ms
        self.assertEqual(fired, [])
        fired = d.tick(1.08)                         # 距最大 ts 55ms >= 窗口
        self.assertEqual(len(fired), 1)
        ev = fired[0]
        self.assertEqual(ev.count, 6)                # 乱序事件全部计入
        self.assertEqual(ev.first_ts, 1.000)
        self.assertEqual(ev.last_ts, 1.025)


class TestEventLoss(unittest.TestCase):
    """丢失补齐：序列号断档立即触发；无序列号时快照对账补齐。"""

    def test_seq_gap_forces_immediate_flush(self):
        d = make()
        d.feed("/w/a.txt", "modify", 1.000, seq=1)
        d.feed("/w/a.txt", "modify", 1.001, seq=2)
        d.feed("/w/a.txt", "modify", 1.002, seq=4)   # seq=3 丢失 -> 断档
        d.feed("/w/a.txt", "modify", 1.003, seq=5)

        self.assertEqual(d.gaps_detected, 1)
        fired = d.tick(1.004)                        # 距最后事件仅 1ms，远小于窗口
        self.assertEqual(len(fired), 1)              # 仍立即触发，不漏
        self.assertEqual(fired[0].reason, REASON_SEQ_GAP)
        self.assertEqual(fired[0].count, 4)

    def test_reconcile_backfills_lost_events(self):
        d = make()
        d.reconcile(["/w/keep.txt"], now=1.0)        # 初始快照
        # 底层监听整体丢失：/w/new.txt 被创建、/w/keep.txt 被删除，无任何事件
        fired = d.reconcile(["/w/new.txt"], now=2.0)
        keys = {(e.key, tuple(sorted(e.kinds))) for e in fired}
        self.assertIn(("/w/new.txt", ("create",)), keys)
        self.assertIn(("/w/keep.txt", ("delete",)), keys)

    def test_reconcile_no_change_no_fire(self):
        d = make()
        d.reconcile(["/w/a.txt"], now=1.0)
        self.assertEqual(d.reconcile(["/w/a.txt"], now=2.0), [])


class TestNoDuplicateFire(unittest.TestCase):
    def test_pending_cleared_after_fire(self):
        d = make()
        d.feed("/w/a.txt", "modify", 1.0)
        self.assertEqual(len(d.tick(1.1)), 1)
        self.assertEqual(d.pending_count(), 0)
        self.assertEqual(d.tick(2.0), [])
        self.assertEqual(d.tick(3.0), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""test_debounce.py — 去抖库自测（unittest，仅标准库，时间由 FakeClock 注入）。

运行：python3 -m unittest -v
每个场景都对「触发次数」做精确断言，并校验不漏触发。
"""

import os
import unittest

from debounce import (
    ATTRIB,
    CREATE,
    DELETE,
    MODIFY,
    Debouncer,
    Event,
)


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def feed(dbc, clock, ts, path, kind=MODIFY, seq=None):
    clock.t = ts
    dbc.feed(Event(path=path, kind=kind, seq=seq))
    return dbc.tick()


def run_to_idle(dbc, clock, step=0.05, max_t=1000.0):
    """把假时钟推到所有桶静默到期，收集所有触发。"""
    out = []
    while dbc.pending_dirs and clock.t < max_t:
        clock.advance(step)
        out.extend(dbc.tick())
    return out


class DebounceTests(unittest.TestCase):

    # ------------------------------------------------ 场景 1：单文件写入
    def test_single_file_write_one_trigger(self):
        """编辑器一次 save：create + 4*modify + attrib，共 6 条原始事件
        在 10ms 内到达。预期：恰好 1 次触发，1 条路径。"""
        clock = FakeClock()
        dbc = Debouncer(window=0.5, max_wait=5.0, clock=clock)
        path = "/data/a.txt"
        raw = [
            (0.000, CREATE),
            (0.002, MODIFY),
            (0.004, MODIFY),
            (0.006, MODIFY),
            (0.008, MODIFY),
            (0.010, ATTRIB),
        ]
        triggers = []
        for ts, kind in raw:
            triggers += feed(dbc, clock, ts, path, kind)
        self.assertEqual(triggers, [], "静默窗口内不应触发")
        triggers += run_to_idle(dbc, clock)

        self._assert_coverage({path}, triggers)
        self.assertEqual(len(triggers), 1, "一次逻辑写入必须只触发一次")
        self.assertEqual(len(triggers[0].paths), 1)
        self.assertEqual(triggers[0].paths[path], "created")

    # ------------------------------------------------ 场景 2：批量写入
    def test_batch_write_collapses_to_one_trigger_per_dir(self):
        """一次批量写 30 个文件、每文件 3 条原始事件（共 90 条）。
        预期：恰好 1 次触发，含 30 条路径。"""
        clock = FakeClock()
        dbc = Debouncer(window=0.5, max_wait=5.0, clock=clock)
        n_files = 30
        paths = [f"/data/batch/f{i:03d}.bin" for i in range(n_files)]
        triggers = []
        for j, kind in enumerate([CREATE, MODIFY, ATTRIB]):
            for i, p in enumerate(paths):
                triggers += feed(dbc, clock, 0.0 + j * 0.05 + i * 0.001, p, kind)
        triggers += run_to_idle(dbc, clock)

        self._assert_coverage(set(paths), triggers)
        self.assertEqual(len(triggers), 1, "同目录批量写入必须合并为 1 次触发")
        self.assertEqual(len(triggers[0].paths), n_files)

    def test_batch_write_two_dirs_two_triggers(self):
        """两个不同目录同时批量写入：不互相牵连，预期 2 次触发。"""
        clock = FakeClock()
        dbc = Debouncer(window=0.5, max_wait=5.0, clock=clock)
        a = [f"/dirA/f{i}" for i in range(10)]
        b = [f"/dirB/f{i}" for i in range(10)]
        triggers = []
        for t, paths in ((0.0, a), (0.1, b)):
            for i, p in enumerate(paths):
                triggers += feed(dbc, clock, t + i * 0.002, p, CREATE)
                triggers += feed(dbc, clock, t + i * 0.002, p, MODIFY)
        triggers += run_to_idle(dbc, clock)

        self._assert_coverage(set(a + b), triggers)
        self.assertEqual(len(triggers), 2)
        self.assertEqual(sorted(t.directory for t in triggers), ["/dirA", "/dirB"])

    # ------------------------------------------------ 场景 3：持续追加
    def test_continuous_append_bounded_by_max_wait(self):
        """持续追加 10 秒，每 0.3s 一条（静默窗口 0.5s 永远不满足）。
        最大等待 M=2.0s 强制周期触发。
        强制触发间隔 = max_wait(2.0s) + 事件间隔(0.3s) = 2.1s 周期。
        预期：强制触发 4 次（各桶首事件时间 0.0/2.4/4.8/7.2s），
        停写后静默触发 1 次，共恰好 5 次。"""
        window, max_wait = 0.5, 2.0
        clock = FakeClock()
        dbc = Debouncer(window=window, max_wait=max_wait, clock=clock)
        path = "/data/log/app.log"
        triggers = []
        t = 0.0
        while t <= 9.9:
            triggers += feed(dbc, clock, round(t, 3), path, MODIFY)
            t += 0.3
        forced = len(triggers)
        forced_times = [t.first_ts for t in triggers]
        triggers += run_to_idle(dbc, clock)

        self.assertEqual(forced, 4, "10s 持续写入应被 max_wait 强制触发 4 次")
        self.assertEqual(forced_times, [0.0, 2.4, 4.8, 7.2])
        self.assertEqual(len(triggers), 5, "持续写入总触发次数必须确定为 5")
        self.assertTrue(all(not t.resync for t in triggers))
        self.assertTrue(all(path in t.paths for t in triggers))

    def test_continuous_append_never_stalls_forever(self):
        """更长的持续写入（60s, 每 0.1s 一条）：触发数严格有界且可预测。
        强制触发周期 = max_wait(2.0s) + 事件间隔(0.1s) = 2.1s。
        预期：强制触发 28 次（60/2.1 向下取整）+ 停写后 1 次 = 29 次。"""
        clock = FakeClock()
        dbc = Debouncer(window=0.5, max_wait=2.0, clock=clock)
        path = "/x.log"
        triggers = []
        t = 0.0
        while t < 60.0:
            triggers += feed(dbc, clock, round(t, 3), path, MODIFY)
            t += 0.1
        triggers += run_to_idle(dbc, clock)
        self.assertEqual(len(triggers), 29)

    # ------------------------------------------------ 场景 4：目录整批替换
    def test_directory_full_replacement_one_trigger(self):
        """目录整批替换：50 个旧文件全删 + 50 个新文件建（路径相同），
        共 100 条原始事件在 0.2s 内到达。
        预期：恰好 1 次触发，50 条路径，合并类型为 churn（窗口内既建又删）。"""
        clock = FakeClock()
        dbc = Debouncer(window=0.5, max_wait=5.0, clock=clock)
        paths = [f"/data/release/f{i:03d}.so" for i in range(50)]
        triggers = []
        for i, p in enumerate(paths):
            triggers += feed(dbc, clock, 0.0 + i * 0.002, p, DELETE)
        for i, p in enumerate(paths):
            triggers += feed(dbc, clock, 0.1 + i * 0.002, p, CREATE)
        triggers += run_to_idle(dbc, clock)

        self._assert_coverage(set(paths), triggers)
        self.assertEqual(len(triggers), 1, "目录整批替换必须合并为 1 次触发")
        self.assertEqual(len(triggers[0].paths), 50)
        self.assertTrue(all(v == "churn" for v in triggers[0].paths.values()))

    def test_directory_clear_then_refill_disjoint_one_trigger(self):
        """整批替换的另一种形态：删 40 个旧名 + 建 40 个新名（不重合）。
        预期仍是 1 次触发，共 80 条路径，删除/创建类型分别正确。"""
        clock = FakeClock()
        dbc = Debouncer(window=0.5, max_wait=5.0, clock=clock)
        old = [f"/d/old{i}" for i in range(40)]
        new = [f"/d/new{i}" for i in range(40)]
        triggers = []
        for i, p in enumerate(old):
            triggers += feed(dbc, clock, i * 0.001, p, DELETE)
        for i, p in enumerate(new):
            triggers += feed(dbc, clock, 0.05 + i * 0.001, p, CREATE)
        triggers += run_to_idle(dbc, clock)

        self._assert_coverage(set(old + new), triggers)
        self.assertEqual(len(triggers), 1)
        self.assertEqual(len(triggers[0].paths), 80)
        merged = triggers[0].paths
        self.assertTrue(all(merged[p] == "deleted" for p in old))
        self.assertTrue(all(merged[p] == "created" for p in new))

    # ------------------------------------------------ 乱序与丢失
    def test_out_of_order_does_not_lose_trigger(self):
        """乱序：同路径先收到 DELETE 后收到 CREATE（顺序颠倒）。
        合并规则与顺序无关，预期 1 次触发，路径仍在，类型 churn。"""
        clock = FakeClock()
        dbc = Debouncer(window=0.5, max_wait=5.0, clock=clock)
        path = "/data/swap.txt"
        triggers = []
        triggers += feed(dbc, clock, 0.0, path, DELETE)
        triggers += feed(dbc, clock, 0.1, path, CREATE)
        triggers += run_to_idle(dbc, clock)

        self._assert_coverage({path}, triggers)
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0].paths[path], "churn")

    def test_out_of_order_seq_is_not_resync(self):
        """seq 3 先于 seq 2 到达（乱序但不缺号）：不应标记 resync。"""
        clock = FakeClock()
        dbc = Debouncer(window=0.5, max_wait=5.0, clock=clock)
        p1, p2 = "/d/a", "/d/b"
        triggers = []
        triggers += feed(dbc, clock, 0.0, p1, MODIFY, seq=1)
        triggers += feed(dbc, clock, 0.1, p2, MODIFY, seq=3)  # 乱序
        triggers += feed(dbc, clock, 0.2, p2, MODIFY, seq=2)  # 迟到补齐
        triggers += run_to_idle(dbc, clock)
        self.assertEqual(len(triggers), 1)
        self.assertFalse(triggers[0].resync)

    def test_lost_events_trigger_resync(self):
        """丢包：seq 1、2 之后直接收到 seq 5（丢了 3、4）。
        预期：触发带 resync=True（通知下游全量重扫该目录），
        且已知路径 1、2 不丢，仍然出现在触发里。"""
        clock = FakeClock()
        dbc = Debouncer(window=0.5, max_wait=5.0, clock=clock)
        p1, p2, p5 = "/d/a", "/d/b", "/d/c"
        triggers = []
        triggers += feed(dbc, clock, 0.0, p1, MODIFY, seq=1)
        triggers += feed(dbc, clock, 0.1, p2, MODIFY, seq=2)
        triggers += feed(dbc, clock, 0.2, p5, MODIFY, seq=5)  # 空洞：3、4 丢失
        triggers += run_to_idle(dbc, clock)

        self._assert_coverage({p1, p2, p5}, triggers)
        self.assertEqual(len(triggers), 1)
        self.assertTrue(triggers[0].resync, "seq 空洞必须触发 resync 补齐机制")

    def test_drain_never_loses_pending_events(self):
        """watcher 关闭时未到静默窗口的事件：drain() 必须全部交出。"""
        clock = FakeClock()
        dbc = Debouncer(window=0.5, max_wait=5.0, clock=clock)
        paths = {f"/shutdown/f{i}" for i in range(10)}
        for i, p in enumerate(sorted(paths)):
            clock.t = i * 0.01
            dbc.feed(Event(p, MODIFY))
        self.assertEqual(dbc.pending_paths, 10)
        triggers = dbc.drain()
        self._assert_coverage(paths, triggers)
        self.assertEqual(len(triggers), 1)
        self.assertEqual(dbc.pending_dirs, 0)

    def test_quiet_window_actually_separates_writes(self):
        """两次间隔大于静默窗口的独立写入：应触发 2 次，不被错误合并。"""
        clock = FakeClock()
        dbc = Debouncer(window=0.5, max_wait=5.0, clock=clock)
        p1, p2 = "/d/a", "/d/b"
        triggers = []
        triggers += feed(dbc, clock, 0.0, p1, MODIFY)
        clock.t = 0.6  # 超过静默窗口
        triggers += dbc.tick()
        triggers += feed(dbc, clock, 1.0, p2, MODIFY)
        triggers += run_to_idle(dbc, clock)
        self.assertEqual(len(triggers), 2)
        self.assertEqual(list(triggers[0].paths), [p1])
        self.assertEqual(list(triggers[1].paths), [p2])

    # ------------------------------------------------ 校验器
    def _assert_coverage(self, fed_paths, triggers):
        """不漏触发校验：所有喂入路径，恰好出现在某次触发中。"""
        seen = []
        for trg in triggers:
            seen.extend(trg.paths)
        triggered = set(seen)
        self.assertTrue(
            fed_paths <= triggered,
            f"漏触发路径: {sorted(fed_paths - triggered)}",
        )
        self.assertEqual(
            len(seen), len(triggered),
            "同一路径在同一次触发中出现多次",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

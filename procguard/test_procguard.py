"""procguard 自测：注入时钟的策略单测 + 真实进程集成测试（仅标准库）。"""
import os
import signal
import sys
import tempfile
import threading
import time
import unittest

from procguard import (
    CleanupResult,
    FakeClock,
    ProcInfo,
    ProcessTree,
    become_subreaper,
    list_pgroup,
    pid_alive,
)

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = [sys.executable, os.path.join(HERE, "child_worker.py")]

become_subreaper()  # 让本进程能回收过继的孤儿子孙


def _info(pid, pgid=9999, state="S", ppid=1):
    return ProcInfo(pid=pid, pgid=pgid, ppid=ppid, state=state, comm="t")


class FakeBackend:
    """假的进程组后端：记录发给进程组的信号，按测试脚本返回存活者。"""

    def __init__(self):
        self.sent = []
        self.members = {100}  # 初始有一个 pid=100 的成员

    def killpg(self, pgid, signum):
        self.sent.append(signum)
        if signum == signal.SIGKILL:
            self.members.clear()

    def lister(self, pgid):
        return [_info(pid, pgid) for pid in sorted(self.members)]

    def prober(self, pgid):
        return bool(self.members)

    def noop_reap(self):
        pass


class InjectedClockTests(unittest.TestCase):
    """时间完全由 FakeClock 注入：验证宽限/升级判定，不做任何真实等待。"""

    def _tree(self, clock, backend, **kw):
        t = ProcessTree(
            ["fake"], clock=clock, lister=backend.lister, prober=backend.prober,
            group_killer=backend.killpg, reaper=backend.noop_reap, **kw
        )
        t.pgid = 9999
        return t

    def test_term_succeeds_no_escalation(self):
        clock, backend = FakeClock(), FakeBackend()

        def prober(pgid):
            # 收到 TERM 后整组立即消失
            if signal.SIGTERM in backend.sent:
                backend.members.clear()
            return bool(backend.members)

        t = ProcessTree(["fake"], clock=clock, lister=backend.lister,
                        prober=prober, group_killer=backend.killpg,
                        reaper=backend.noop_reap, term_timeout=1.0)
        t.pgid = 9999
        r = t.cleanup()
        self.assertTrue(r.completed, r.summary())
        self.assertFalse(r.escalated)
        self.assertEqual(backend.sent, [signal.SIGTERM])
        self.assertEqual(r.duration_seconds, 0.0)

    def test_escalate_to_kill_exactly_at_timeout(self):
        clock, backend = FakeClock(), FakeBackend()  # 成员永不因 TERM 消失
        t = self._tree(clock, backend, term_timeout=0.5,
                       kill_timeout=0.3, poll_interval=0.1)
        r = t.cleanup()
        self.assertTrue(r.completed, r.summary())
        self.assertTrue(r.escalated)
        self.assertEqual(backend.sent, [signal.SIGTERM, signal.SIGKILL])
        self.assertAlmostEqual(r.term_phase_seconds, 0.5)
        self.assertLessEqual(r.kill_phase_seconds, 0.0)
        self.assertAlmostEqual(clock.now(), 0.5)  # KILL 后成员即刻消失
        self.assertEqual(r.survivors, [])

    def test_reentrant_signal_during_cleanup_is_recorded(self):
        clock, backend = FakeClock(), FakeBackend()
        t = self._tree(clock, backend, term_timeout=0.2,
                       poll_interval=0.05)

        def prober(pgid):
            # TERM 阶段首次轮询时，模拟“清理途中又收到一个信号”
            if backend.sent == [signal.SIGTERM] and not t._signals_during_cleanup:
                t._handle_signal(signal.SIGINT, None)
                # 并发清理请求必须被拒绝，不能重启流程
                raced = t.cleanup()
                self.assertFalse(raced.completed)
            return bool(backend.members)

        t._prober = prober
        r = t.cleanup()
        self.assertTrue(r.completed, r.summary())
        self.assertTrue(r.escalated)
        self.assertEqual(r.signals_during_cleanup, [signal.SIGINT])
        # TERM 只发一次，KILL 只发一次：清理没有被第二个信号打乱
        self.assertEqual(backend.sent.count(signal.SIGTERM), 1)
        self.assertEqual(backend.sent.count(signal.SIGKILL), 1)


class RealProcessTests(unittest.TestCase):
    """真实进程集成测试（时钟为真实单调时钟）。"""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        os.unlink(self.tmp.name)
        self.pidsfile = self.tmp.name + ".pids"

    def tearDown(self):
        for p in (self.pidsfile,):
            try:
                os.unlink(p)
            except OSError:
                pass

    def _read_pids(self):
        with open(self.pidsfile) as f:
            return [int(x) for x in f.read().split()]

    def _assert_all_dead(self, pids, result):
        # 给回收留一点真实时间窗口
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if not any(pid_alive(p, allow_zombie=True) for p in pids):
                break
            time.sleep(0.02)
        leftovers = [(p, pid_alive(p, allow_zombie=True)) for p in pids]
        self.assertTrue(
            not any(alive for _, alive in leftovers),
            "leftover processes: %s | %s" % (leftovers, result.summary()),
        )
        self.assertEqual(result.survivors, [], result.summary())
        self.assertTrue(result.completed, result.summary())

    def test_single_process(self):
        tree = ProcessTree(WORKER + ["sleep"], term_timeout=2.0,
                           poll_interval=0.01)
        tree.start()
        self.assertEqual(list_pgroup(tree.pgid)[0].pid, tree.pgid)
        result = tree.cleanup()
        self.assertFalse(result.escalated, result.summary())
        self._assert_all_dead([tree.pgid], result)
        print("\n[single] " + result.summary())

    def test_multilevel_derivation(self):
        # depth=2 fanout=3 -> 1 + 3 + 9 = 13 个进程，跨 3 层
        tree = ProcessTree(
            WORKER + ["tree", "2", "3", self.pidsfile],
            term_timeout=2.0, poll_interval=0.01,
        )
        tree.start()
        pids = []
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and len(pids) < 13:
            pids = self._read_pids() if os.path.exists(self.pidsfile) else []
            time.sleep(0.02)
        self.assertEqual(len(set(pids)), 13, pids)
        members = list_pgroup(tree.pgid)
        self.assertEqual(len(members), 13, [p.pid for p in members])
        result = tree.cleanup()
        self.assertFalse(result.escalated, result.summary())
        self._assert_all_dead(pids, result)
        print("\n[multilevel n=13] " + result.summary())
        print("  清理前 pgid=%d 内进程: %s" %
              (tree.pgid, ",".join(str(p.pid) for p in members)))
        print("  清理后存活清单: %s" %
              [p.pid for p in list_pgroup(tree.pgid)])

    def test_child_ignores_gentle_signal_force_kill(self):
        # 父子均 SIG_IGN；必须靠 SIGKILL 兜底，记录用时
        tree = ProcessTree(
            WORKER + ["ignore", self.pidsfile],
            term_timeout=0.3, kill_timeout=2.0, poll_interval=0.02,
        )
        tree.start()
        pids = []
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and len(pids) < 2:
            pids = self._read_pids() if os.path.exists(self.pidsfile) else []
            time.sleep(0.02)
        self.assertEqual(len(set(pids)), 2, pids)
        result = tree.cleanup()
        self.assertTrue(result.escalated, result.summary())
        self._assert_all_dead(pids, result)
        print("\n[ignore-term -> SIGKILL] " + result.summary())
        print("  TERM 阶段用时 %.3fs（宽限期 0.3s 内无进程退出）"
              % result.term_phase_seconds)

    def test_second_signal_during_cleanup(self):
        # 子进程忽略 TERM：清理进入 0.8s 宽限；0.15s 后监管进程收到第二个信号
        tree = ProcessTree(
            WORKER + ["ignore", self.pidsfile],
            term_timeout=0.8, kill_timeout=2.0, poll_interval=0.02,
            forwarded_signals=(signal.SIGINT,),
        )
        tree.start()
        pids = []
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and len(pids) < 2:
            pids = self._read_pids() if os.path.exists(self.pidsfile) else []
            time.sleep(0.02)
        self.assertEqual(len(set(pids)), 2, pids)
        tree.install_signal_handlers()
        outcome = {}

        def run_cleanup():
            outcome["r"] = tree.cleanup()

        th = threading.Thread(target=run_cleanup)
        th.start()
        time.sleep(0.15)  # 确保已在清理途中
        self.assertTrue(tree._cleaning)
        os.kill(os.getpid(), signal.SIGINT)  # 处理器在主线程执行
        th.join(timeout=5.0)
        tree.restore_signal_handlers()
        self.assertFalse(th.is_alive(), "清理线程中途失败/卡住")
        result = outcome["r"]
        self.assertTrue(result.completed, result.summary())
        self.assertTrue(result.escalated, result.summary())
        self.assertEqual(result.signals_during_cleanup, [signal.SIGINT])
        self._assert_all_dead(pids, result)
        print("\n[signal-during-cleanup] " + result.summary())


if __name__ == "__main__":
    unittest.main(verbosity=2)

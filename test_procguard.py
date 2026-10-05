"""procguard 自测：单进程 / 多层派生 / 忽略温和信号 / 清理中再次收到信号 / 转发验证。

运行: python3 -m unittest -v test_procguard
时间注入：test_ignore_term_escalation 使用假时钟，其余场景用真实时钟。
"""
import os
import signal
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from procguard import Supervisor, list_session_members  # noqa: E402

HELPERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "helpers")
PY = sys.executable


def helper(name):
    return os.path.join(HELPERS, name)


def wait_until(predicate, timeout=5.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def wait_file(path):
    return wait_until(lambda: os.path.exists(path))


def print_report(title, report):
    print(f"\n== {title} ==")
    print("清理前存活进程清单:")
    for m in report.members_before:
        print(f"  {m}")
    print(f"温和信号目标 pids : {report.term_sent_to}")
    print(f"强制 KILL 目标pids: {report.kill_sent_to}")
    print(f"是否触发强制兜底   : {report.escalated}")
    print(f"清理期间再次收信号 : {report.signals_during_cleanup}")
    print(f"清理后存活清单     : {[str(m) for m in report.survivors]}")
    print(f"清理用时           : {report.duration:.4f} 秒（注入时钟）")


class FakeClock:
    """测试注入的时间源：sleep 只推进假时钟，不花真实时间。"""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self._sup = None

    def tearDown(self):
        # 兜底：防止测试失败时泄漏进程
        if self._sup is not None:
            try:
                if self._sup.members():
                    self._sup.terminate()
            finally:
                self._sup.restore_handlers()

    def _make(self, *args, **kwargs):
        self._sup = Supervisor(*args, **kwargs)
        self._sup.start()
        return self._sup

    # 情形 1：单进程，经信号转发路径清理 -----------------------------------
    def test_single_process(self):
        sup = self._make([PY, "-c", "import time; time.sleep(3600)"], grace=1.0)
        self.assertTrue(wait_until(lambda: len(sup.members()) == 1))
        self.assertEqual(len(sup.members()), 1)

        os.kill(os.getpid(), signal.SIGTERM)  # 触发转发 + 清理
        report = sup.last_report
        print_report("情形1 单进程（信号转发路径）", report)

        self.assertIsNotNone(report)
        self.assertEqual(len(report.members_before), 1)
        self.assertEqual(report.survivors, [])
        self.assertEqual(sup.members(), [])
        self.assertFalse(report.escalated)
        self.assertEqual(list_session_members(sup.sid), [])

    # 情形 2：多层派生（主->孙->曾孙->玄孙），共 4 个进程 --------------------
    def test_multi_layer(self):
        sup = self._make([PY, helper("chain.py"), "3"], grace=1.0)
        self.assertTrue(wait_until(lambda: len(sup.members()) == 4),
                        f"期望派生为 4 个进程，实际 {[str(m) for m in sup.members()]}")

        report = sup.terminate()
        print_report("情形2 多层派生（4 个进程，同会话/同进程组）", report)

        self.assertEqual(len(report.members_before), 4)
        self.assertEqual(report.survivors, [])
        self.assertEqual(sup.members(), [])
        self.assertFalse(report.escalated)
        self.assertEqual(list_session_members(sup.sid), [])

    # 情形 3：子进程忽略 SIGTERM -> 强制兜底（时间由测试注入） ---------------
    def test_ignore_term_escalation(self):
        with tempfile.TemporaryDirectory() as tmp:
            ready = os.path.join(tmp, "ready")
            fake = FakeClock()
            sup = self._make(
                [PY, helper("ignore_term.py"), ready],
                grace=2.0, clock=fake, sleep=fake.sleep,
            )
            self.assertTrue(wait_file(ready))  # 确保 SIG_IGN 已安装

            real_start = time.perf_counter()
            report = sup.terminate()
            real_elapsed = time.perf_counter() - real_start
            print_report("情形3 忽略温和信号（假时钟 grace=2.0s）", report)
            print(f"真实墙钟用时       : {real_elapsed:.4f} 秒")

            self.assertTrue(report.escalated)
            self.assertEqual(report.survivors, [])
            self.assertEqual(sup.members(), [])
            # 宽限由注入时钟精确计量：温和信号无效后等待满 grace 才升级
            self.assertAlmostEqual(report.duration, 2.0, delta=0.05)
            self.assertIn(sup._proc.pid, report.kill_sent_to)

    # 情形 4：清理进行中再次收到信号，清理不得半途而废 ----------------------
    def test_signal_during_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            ready = os.path.join(tmp, "ready")
            sup = self._make([PY, helper("ignore_term.py"), ready],
                             grace=0.8, poll_interval=0.01)
            self.assertTrue(wait_file(ready))

            def send_second_signal():
                time.sleep(0.3)  # 在清理宽限窗口内再次发信号
                os.kill(os.getpid(), signal.SIGINT)

            t = threading.Thread(target=send_second_signal)
            t.start()
            os.kill(os.getpid(), signal.SIGTERM)  # 转发 SIGTERM 并开始清理
            t.join()

            report = sup.last_report
            print_report("情形4 清理中再次收到 SIGINT（真实时钟 grace=0.8s）", report)

            self.assertIsNotNone(report)
            self.assertIn(signal.SIGINT, report.signals_during_cleanup)
            self.assertTrue(report.escalated)
            self.assertEqual(report.survivors, [])
            self.assertEqual(sup.members(), [])
            self.assertGreaterEqual(report.duration, 0.79)

    # 附：显式验证信号确实转发到了子进程（子进程记录后自行退出） -------------
    def test_signal_forwarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, "marker")
            ready = os.path.join(tmp, "ready")
            sup = self._make(
                [PY, helper("term_marker.py"), marker, ready], grace=1.0,
            )
            self.assertTrue(wait_file(ready))
            os.kill(os.getpid(), signal.SIGTERM)
            report = sup.last_report
            print_report("附：信号转发验证（子进程收到 SIGTERM 后自行退出）", report)

            self.assertTrue(wait_file(marker))
            with open(marker) as fh:
                self.assertEqual(int(fh.read().strip()), signal.SIGTERM)
            self.assertEqual(report.survivors, [])
            self.assertFalse(report.escalated)


if __name__ == "__main__":
    unittest.main(verbosity=2)

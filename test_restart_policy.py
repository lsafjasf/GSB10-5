"""restart_policy 自测（python3 -m unittest -v 或直接运行）。

时间全部注入（now 参数），无任何真实 sleep。
"""

import sys
import time
import tracemalloc
import unittest

from restart_policy import (
    Action,
    Config,
    Decision,
    RestartSupervisor,
    State,
)


def make(**kw):
    defaults = dict(
        initial_backoff=1.0,
        backoff_multiplier=2.0,
        max_backoff=60.0,
        min_alive_duration=30.0,
        max_fast_failures=5,
    )
    defaults.update(kw)
    return RestartSupervisor(Config(**defaults))


def crash_cycle(sup, start_at, uptime):
    """模拟一次：start_at 拉起，uptime 后以退出码 1 崩溃。返回崩溃决策。"""
    sup.on_started(start_at)
    return sup.on_exit(start_at + uptime, code=1)


class TestFirstCrash(unittest.TestCase):
    def test_first_crash_schedules_restart_after_initial_backoff(self):
        sup = make()
        sup.on_started(100.0)
        d = sup.on_exit(105.0, code=1)          # 起来 5s 就崩
        self.assertEqual(d.action, Action.RESTART)
        self.assertEqual(d.until, 106.0)         # 105 + initial_backoff(1s)
        self.assertEqual(sup.restart_times, [106.0])
        self.assertEqual(sup.state, State.WAITING_RESTART)


class TestConsecutiveCrashes(unittest.TestCase):
    def test_exponential_backoff_sequence(self):
        """连续“起来就崩”，重启时刻序列严格按指数退避推进。"""
        sup = make()
        t = 0.0
        expected_restarts = []
        expected_backoffs = [1.0, 2.0, 4.0, 8.0]   # 第 5 次触发无效判定
        for backoff in expected_backoffs:
            sup.on_started(t)
            d = sup.on_exit(t + 5.0, code=1)       # 每次只活 5s
            self.assertEqual(d.action, Action.RESTART)
            t = t + 5.0 + backoff
            expected_restarts.append(t)
            self.assertAlmostEqual(d.until, t)
        self.assertEqual(sup.restart_times, expected_restarts)
        # 序列: 6, 13, 22, 35
        self.assertEqual(sup.restart_times, [6.0, 13.0, 22.0, 35.0])

    def test_backoff_capped_at_max(self):
        sup = make(max_backoff=4.0, max_fast_failures=10)
        t = 0.0
        backoffs = []
        for _ in range(6):
            sup.on_started(t)
            d = sup.on_exit(t + 1.0, code=1)
            backoffs.append(d.until - (t + 1.0))
            t = d.until
        self.assertEqual(backoffs, [1.0, 2.0, 4.0, 4.0, 4.0, 4.0])


class TestQuickCrashAfterStart(unittest.TestCase):
    def test_ineffective_restart_detection(self):
        """连续 max_fast_failures 次起来就崩 => 判定重启无效并终止。"""
        sup = make(max_fast_failures=5)
        t = 0.0
        for i in range(4):
            sup.on_started(t)
            d = sup.on_exit(t + 2.0, code=1)
            self.assertEqual(d.action, Action.RESTART, f"第{i+1}次应仍重启")
            t = d.until
        sup.on_started(t)
        d = sup.on_exit(t + 2.0, code=1)
        self.assertEqual(d.action, Action.STOP)
        self.assertEqual(sup.state, State.STOPPED_INEFFECTIVE)
        self.assertIn("无效", d.reason)
        # 终态后不再调度任何重启
        d2 = sup.on_exit(t + 4.0, code=1)
        self.assertEqual(d2.action, Action.NONE)
        self.assertEqual(len(sup.restart_times), 4)

    def test_health_check_failure_before_min_alive_counts_as_fast_fail(self):
        """存活窗口内健康检查失败，等价于起来就崩。"""
        sup = make(max_fast_failures=2)
        sup.on_started(0.0)
        d = sup.on_health_check(ok=False, now=10.0)   # 10s < 30s
        self.assertEqual(d.action, Action.RESTART)
        sup.on_started(d.until)
        d = sup.on_health_check(ok=False, now=d.until + 10.0)
        self.assertEqual(d.action, Action.STOP)
        self.assertEqual(sup.state, State.STOPPED_INEFFECTIVE)


class TestLivenessCriteria(unittest.TestCase):
    def test_health_ok_before_min_alive_is_not_enough(self):
        """只看“起来了/检查通过”不算存活：未满最小存活时长不算健康。"""
        sup = make()
        sup.on_started(0.0)
        d = sup.on_health_check(ok=True, now=10.0)    # 通过但太早
        self.assertEqual(sup.state, State.STARTING)   # 仍未判定存活
        self.assertEqual(sup.health_marks, 0)
        d = sup.on_health_check(ok=True, now=31.0)    # 满 30s 且通过
        self.assertEqual(sup.state, State.HEALTHY)
        self.assertEqual(sup.health_marks, 1)

    def test_healthy_service_crash_resets_backoff(self):
        """健康存活后的崩溃是新故障：退避重置，且不计入无效重启。"""
        sup = make()
        # 先制造 3 次快速失败
        t = 0.0
        for _ in range(3):
            sup.on_started(t)
            d = sup.on_exit(t + 1.0, code=1)
            t = d.until
        self.assertEqual(sup.fast_failures, 3)
        # 这次健康存活 100s 后崩溃
        sup.on_started(t)
        sup.on_health_check(ok=True, now=t + 31.0)
        d = sup.on_exit(t + 100.0, code=1)
        self.assertEqual(sup.fast_failures, 0)                 # 计数清零
        self.assertAlmostEqual(d.until, t + 100.0 + 1.0)       # 退避回到 1s

    def test_healthy_then_health_check_fails_is_crash(self):
        sup = make()
        sup.on_started(0.0)
        sup.on_health_check(ok=True, now=31.0)
        d = sup.on_health_check(ok=False, now=40.0)
        self.assertEqual(d.action, Action.RESTART)
        self.assertEqual(d.until, 41.0)


class TestDependencyUnavailable(unittest.TestCase):
    def test_no_restart_while_dependency_down(self):
        """依赖长期不可用：不重启服务本体，只对依赖做退避探活。"""
        sup = make()
        sup.cfg.dependency_available = False
        sup.on_started(0.0)
        d = sup.on_exit(5.0, code=1)
        self.assertEqual(d.action, Action.PROBE_DEPENDENCY)
        self.assertEqual(sup.state, State.DEP_WAIT)
        self.assertEqual(sup.restart_times, [])          # 一次都没重启
        # 探活退避：5, 10, 20, 40 ...
        probes = []
        t = d.until
        for expected in [5.0, 10.0, 20.0, 40.0]:
            probes.append(t)
            d = sup.on_dependency_probe(available=False, now=t)
            self.assertEqual(d.action, Action.PROBE_DEPENDENCY)
            self.assertAlmostEqual(d.until - t, [10.0, 20.0, 40.0, 80.0][len(probes) - 1])
            t = d.until
        self.assertEqual(sup.restart_times, [])          # 长时间仍不重启
        self.assertEqual(sup.state, State.DEP_WAIT)

    def test_dependency_probe_backoff_capped(self):
        sup = make()
        sup.cfg.dependency_available = False
        sup.on_started(0.0)
        d = sup.on_exit(1.0, code=1)
        t = d.until
        for _ in range(20):
            d = sup.on_dependency_probe(available=False, now=t)
            t = d.until
        # 探活间隔最终封顶 dep_probe_max=300
        d = sup.on_dependency_probe(available=False, now=t)
        self.assertAlmostEqual(d.until - t, 300.0)

    def test_restart_resumes_when_dependency_recovers(self):
        sup = make()
        sup.cfg.dependency_available = False
        sup.on_started(0.0)
        d = sup.on_exit(5.0, code=1)
        d = sup.on_dependency_probe(available=False, now=d.until)
        d = sup.on_dependency_probe(available=True, now=d.until)
        self.assertEqual(d.action, Action.RESTART)
        self.assertEqual(d.until, sup.restart_times[-1])
        # 恢复后立即重启（delay=0）
        self.assertEqual(sup.restart_times, [d.until])


class TestHardLimits(unittest.TestCase):
    def test_max_total_restarts(self):
        sup = make(max_fast_failures=100, max_total_restarts=3)
        t = 0.0
        for _ in range(3):
            sup.on_started(t)
            d = sup.on_exit(t + 1.0, code=1)
            self.assertEqual(d.action, Action.RESTART)
            t = d.until
        sup.on_started(t)
        d = sup.on_exit(t + 1.0, code=1)
        self.assertEqual(d.action, Action.STOP)
        self.assertEqual(sup.state, State.STOPPED_MAX_RESTARTS)

    def test_manual_reset_recovers(self):
        sup = make(max_fast_failures=2)
        sup.on_started(0.0)
        sup.on_exit(1.0, code=1)
        sup.on_started(2.0)
        d = sup.on_exit(3.0, code=1)
        self.assertEqual(sup.state, State.STOPPED_INEFFECTIVE)
        sup.reset(now=10.0)
        sup.on_started(10.0)
        d = sup.on_exit(11.0, code=1)
        self.assertEqual(d.action, Action.RESTART)


class TestBenchmark(unittest.TestCase):
    """10 万次故障事件：调度耗时与状态内存占用。"""

    N = 100_000

    def _run_benchmark(self, keep_history):
        cfg = Config(min_alive_duration=30.0, max_fast_failures=5,
                     keep_history=keep_history)
        sup = RestartSupervisor(cfg)
        t = 0.0
        start = time.perf_counter()
        for i in range(self.N):
            sup.on_started(t)
            if i % 6 == 5:
                # 每 6 次让服务健康存活一次，避免触发无效终止，
                # 同时覆盖 fast_failures 清零路径
                sup.on_health_check(True, t + 31.0)
                sup.on_exit(t + 100.0, code=1)
                t += 101.0
            else:
                d = sup.on_exit(t + 1.0, code=1)
                t = d.until if d.until is not None else t + 1.0
        elapsed = time.perf_counter() - start
        return sup, elapsed

    def test_benchmark_100k_events(self):
        # 模式 A：保留完整事件日志（默认，用于诊断）
        sup_a, elapsed_a = self._run_benchmark(keep_history=True)
        # 模式 B：关闭日志，计时与测内存分开跑，避免 tracemalloc 污染耗时
        sup_b, elapsed_b = self._run_benchmark(keep_history=False)
        tracemalloc.start()
        sup_b2, _ = self._run_benchmark(keep_history=False)
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        core_state_bytes = sys.getsizeof(sup_b2) + sys.getsizeof(vars(sup_b2))

        n = self.N
        print("\n================ 10 万次故障事件基准 ================")
        print(f"事件总数:                 {n:,}")
        print(f"[A 保留日志] 总调度耗时:   {elapsed_a*1000:8.1f} ms "
              f"({elapsed_a/n*1e6:6.2f} us/事件)")
        print(f"[B 关闭日志] 总调度耗时:   {elapsed_b*1000:8.1f} ms "
              f"({elapsed_b/n*1e6:6.2f} us/事件)")
        print(f"[B 关闭日志] 核心状态大小: {core_state_bytes:8d} B"
              f"（O(1)，与事件数无关）")
        print(f"[B 关闭日志] tracemalloc 峰值: {peak/1024:10.1f} KiB")
        print(f"[A 保留日志] 事件日志条数: {len(sup_a.events):,} "
              f"（诊断开销，线性增长，可关闭）")
        print("=====================================================")

        # 功能性断言：10 万事件全部正确处理
        self.assertGreater(sup_a.restart_count, n // 2)
        self.assertEqual(sup_a.state, State.WAITING_RESTART)
        self.assertEqual(sup_b.state, State.WAITING_RESTART)
        self.assertEqual(sup_b.restart_times, [])  # 关闭日志后不落序列
        # 性能 sanity：纯 Python 也应远快于 1us/事件 的数量级下限
        self.assertLess(elapsed_a, 30.0, "调度耗时异常")


if __name__ == "__main__":
    unittest.main(verbosity=2)

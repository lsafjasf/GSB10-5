"""Self-tests for restart_policy (stdlib unittest). Run: python3 -m unittest -v"""

import unittest

from restart_policy import Action, Decision, Policy, RestartManager


class VirtualClock:
    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def make(**kw):
    clock = VirtualClock(0.0)
    return RestartManager(Policy(**kw), clock=clock), clock


class FirstCrashTests(unittest.TestCase):
    def test_first_crash_restarts_after_base_delay(self):
        mgr, clock = make(base_delay_seconds=1.0, backoff_factor=2.0)
        self.assertEqual(mgr.on_started(0.0).action, Action.NONE)

        decision = mgr.on_exited(1.0, exit_code=137)
        self.assertEqual(decision.action, Action.RESTART)
        self.assertAlmostEqual(decision.at, 2.0)          # crash t=1 + base 1s
        self.assertEqual(mgr.consecutive_failures, 1)
        self.assertEqual(mgr.total_restarts, 1)

    def test_stray_exit_ignored(self):
        mgr, _ = make()
        self.assertEqual(mgr.on_exited(0.0).action, Action.NONE)


class ConsecutiveCrashTests(unittest.TestCase):
    def test_backoff_sequence_cap_and_ineffective_stop(self):
        # base=1, factor=2, cap=16, at most 5 failed runs in a row.
        mgr, clock = make(max_delay_seconds=16.0,
                          max_consecutive_failures=5)
        mgr.on_started(0.0)

        restart_times = []
        t = 0.0
        decision = None
        # Every restarted process crashes instantly (quick crash).
        for _ in range(6):
            decision = mgr.on_exited(t, exit_code=1)
            if decision.action == Action.RESTART:
                restart_times.append(decision.at)
                t = decision.at
                mgr.on_started(t)
            else:
                break

        # delays 1, 2, 4, 8, 16
        self.assertEqual(restart_times, [1.0, 3.0, 7.0, 15.0, 31.0])
        self.assertEqual(decision.action, Action.STOP)
        self.assertEqual(mgr.state, RestartManager.FAILED)
        self.assertIn("ineffective", decision.reason)
        self.assertEqual(mgr.consecutive_failures, 6)

    def test_healthy_run_resets_backoff(self):
        mgr, clock = make(min_alive_seconds=10.0,
                          base_delay_seconds=1.0, backoff_factor=2.0)
        mgr.on_started(0.0)
        d = mgr.on_exited(1.0)                       # quick crash
        self.assertAlmostEqual(d.at, 2.0)
        mgr.on_started(2.0)
        d = mgr.on_exited(2.5)                       # another quick crash
        self.assertAlmostEqual(d.at, 4.5)            # delay grew to 2s

        # Third run stays alive >= min_alive: streak resets.
        mgr.on_started(4.5)
        d = mgr.on_exited(30.0)
        self.assertEqual(d.action, Action.RESTART)
        self.assertAlmostEqual(d.at, 31.0)           # back to base delay
        self.assertEqual(mgr.consecutive_failures, 0)


class QuickCrashButUpTests(unittest.TestCase):
    """Process comes up but dies almost immediately: restart loop is caught."""

    def test_quick_crash_counts_as_failure(self):
        mgr, clock = make(min_alive_seconds=10.0)
        mgr.on_started(0.0)
        mgr.on_exited(0.2)
        self.assertEqual(mgr.consecutive_failures, 1)
        mgr.on_started(0.2)
        mgr.on_exited(0.3)
        self.assertEqual(mgr.consecutive_failures, 2)

    def test_process_up_but_health_checks_keep_failing(self):
        mgr, clock = make(min_alive_seconds=10.0,
                          health_check_max_failures=2,
                          max_consecutive_failures=3)
        mgr.on_started(0.0)
        self.assertEqual(mgr.on_health_check(False, now=2.0).action, Action.NONE)
        decision = mgr.on_health_check(False, now=4.0)
        self.assertEqual(decision.action, Action.RESTART)       # declared dead
        self.assertEqual(mgr.consecutive_failures, 1)
        # A passing check clears the consecutive failure counter.
        mgr.on_started(decision.at)
        self.assertEqual(mgr.on_health_check(True, now=100.0).action, Action.NONE)
        self.assertEqual(mgr.health_failures, 0)


class DependencyDownTests(unittest.TestCase):
    def test_no_restarts_while_dependency_down_then_give_up(self):
        mgr, clock = make(dependency_probe_interval_seconds=30.0,
                          dependency_max_wait_seconds=600.0,
                          max_consecutive_failures=10)
        mgr.on_started(0.0)
        # Dependency goes down at t=100, service crashes at t=120.
        mgr.on_dependency_check(False, now=100.0)
        d = mgr.on_exited(120.0)
        self.assertEqual(d.action, Action.PROBE_DEPENDENCY)
        self.assertAlmostEqual(d.at, 150.0)
        restarts_while_down = 0

        # Probe every 30s; dependency never comes back.
        for t in range(150, 721, 30):
            d = mgr.on_dependency_check(False, now=float(t))
            if d.action == Action.RESTART:
                restarts_while_down += 1
            if d.action == Action.STOP:
                stop_at = float(t)
                break

        self.assertEqual(restarts_while_down, 0)        # no service restarts
        self.assertEqual(d.action, Action.STOP)
        self.assertEqual(mgr.state, RestartManager.FAILED)
        self.assertGreaterEqual(stop_at - 100.0, 600.0)  # deadline enforced

    def test_dependency_recovers_before_deadline(self):
        mgr, clock = make(dependency_probe_interval_seconds=30.0,
                          dependency_max_wait_seconds=600.0)
        mgr.on_started(0.0)
        mgr.on_dependency_check(False, now=0.0)
        d = mgr.on_exited(5.0)
        self.assertEqual(d.action, Action.PROBE_DEPENDENCY)
        d = mgr.on_dependency_check(True, now=35.0)
        self.assertEqual(d.action, Action.RESTART)      # resumes once
        self.assertGreaterEqual(d.at, 35.0)


class PerformanceTests(unittest.TestCase):
    """100k fault events: scheduling must be fast and state O(1)."""

    def test_100k_events(self):
        import sys
        import time

        mgr = RestartManager(Policy(), clock=lambda: 0.0)
        n = 100_000
        t = 0.0

        # Snapshot fixed state size (no event history is retained).
        state_bytes = sum(
            sys.getsizeof(v) for v in vars(mgr).values()
        ) + sys.getsizeof(vars(mgr))

        start = time.perf_counter()
        for i in range(n):
            mgr.on_started(t)
            if i % 3 == 0:
                t += 0.1                              # quick crash
            else:
                t += 60.0                             # healthy long run
            d = mgr.on_exited(t)
            if d.action == Action.STOP:
                self.fail("should never stop in mixed workload")
            t = d.at if d.at is not None else t
        elapsed = time.perf_counter() - start

        # State must not grow with the number of events: no container
        # attribute may accumulate entries, and the scalar footprint may
        # only vary by a few bytes (int/float digit growth).
        state_bytes_after = sum(
            sys.getsizeof(v) for v in vars(mgr).values()
        ) + sys.getsizeof(vars(mgr))
        for name, value in vars(mgr).items():
            if isinstance(value, (list, dict, set)):
                self.assertEqual(len(value), 0, f"{name} grew unboundedly")
        self.assertLessEqual(state_bytes_after - state_bytes, 64)
        self.assertLess(elapsed, 5.0)
        print(f"\n100k fault events: {elapsed*1000:.1f} ms total, "
              f"{elapsed/n*1e6:.3f} us/event; fixed state {state_bytes} B")


if __name__ == "__main__":
    unittest.main(verbosity=2)

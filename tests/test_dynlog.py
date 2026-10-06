"""动态日志级别的自测（标准库 unittest）。

覆盖：
  1. 单次调整：生效时机、版本、审计记录；
  2. 反复来回调整：快速 flip-flop 后所有节点收敛到最终期望；
  3. 乱序投递：先投递的是新事件时旧事件被丢弃，last-write-wins；
  4. 高频日志源：最吵的源被守卫优先收敛；
  5. 调整后日志量超限：全量调低级别 -> 超限 -> 自动收敛 ->
     调回 -> 守卫释放 -> 一致性为 0 失配；
  6. 审计可追溯；
  7. 一致性校验能识别未生效节点。
"""

import unittest

from dynlog import (
    DEBUG,
    INFO,
    WARN,
    ERROR,
    AuditLog,
    LogLevelManager,
    VolumeGuard,
    Workload,
)


class SingleAdjustmentTest(unittest.TestCase):
    def test_level_takes_effect_only_after_delivery(self):
        manager = LogLevelManager(delay_fn=lambda source: 0.01)
        manager.add_source("a", INFO)

        version = manager.set_level("a", DEBUG, operator="oncall-li", reason="incident")

        self.assertEqual(manager.effective_level("a"), INFO)
        self.assertEqual(manager.state("a").version, version)

        manager.advance(0.005)
        self.assertEqual(manager.effective_level("a"), INFO)

        manager.advance(0.006)
        self.assertEqual(manager.effective_level("a"), DEBUG)
        self.assertEqual(manager.state("a").applied_version, version)

    def test_audit_records_who_when_what(self):
        manager = LogLevelManager(delay_fn=lambda source: 0.0)
        manager.add_source("a", INFO)

        manager.set_level("a", ERROR, operator="oncall-li", reason="disk 95%")

        record = manager.audit.records[-1]
        self.assertEqual(record.operator, "oncall-li")
        self.assertEqual(record.source, "a")
        self.assertEqual((record.old_level, record.new_level), ("INFO", "ERROR"))
        self.assertEqual(record.reason, "disk 95%")
        self.assertGreaterEqual(record.ts, 0)


class FlipFlopTest(unittest.TestCase):
    def test_rapid_flip_flop_converges_every_node(self):
        manager = LogLevelManager(delay_fn=lambda source: 0.02)
        for index in range(50):
            manager.add_source("src-%02d" % index, INFO)

        for round_index in range(20):
            target = DEBUG if round_index % 2 == 0 else INFO
            for index in range(50):
                manager.set_level("src-%02d" % index, target, operator="op")
            manager.advance(0.001)

        pending_report = manager.consistency_check()
        self.assertFalse(pending_report.ok)

        manager.advance(1.0)
        report = manager.consistency_check()
        self.assertTrue(report.ok, report.mismatches[:3])
        for index in range(50):
            self.assertEqual(manager.effective_level("src-%02d" % index), INFO)
            state = manager.state("src-%02d" % index)
            self.assertEqual(state.change_seq, 20)
            self.assertEqual(state.applied_seq, state.change_seq)
            self.assertEqual(state.applied_version, state.version)


class OutOfOrderDeliveryTest(unittest.TestCase):
    def test_late_stale_event_is_dropped(self):
        delay = {"v": 0.05}
        manager = LogLevelManager(delay_fn=lambda source: delay["v"])
        manager.add_source("a", INFO)

        manager.set_level("a", DEBUG, operator="op")
        manager.advance(0.01)
        self.assertEqual(manager.effective_level("a"), INFO)

        delay["v"] = 0.001
        manager.set_level("a", WARN, operator="op")
        manager.advance(0.002)
        self.assertEqual(manager.effective_level("a"), WARN)

        manager.advance(1.0)
        self.assertEqual(manager.effective_level("a"), WARN)


class HighFrequencySourceTest(unittest.TestCase):
    def test_loudest_source_is_escalated_first(self):
        manager = LogLevelManager(delay_fn=lambda source: 0.0)
        manager.add_source("hot", INFO)
        for index in range(10):
            manager.add_source("normal-%02d" % index, INFO)

        base_rates = {"hot": 1000.0}
        base_rates.update({"normal-%02d" % index: 1.0 for index in range(10)})
        workload = Workload(manager, base_rates)
        guard = VolumeGuard(manager, cap=2000.0, cooldown=2)

        manager.set_level("hot", DEBUG, operator="op")
        for name in [n for n in manager.sources() if n != "hot"]:
            manager.set_level(name, DEBUG, operator="op")

        totals = []
        for _ in range(10):
            manager.advance(0.1)
            totals.append(guard.observe(workload.tick()))

        self.assertGreater(totals[0], 2000.0)
        self.assertLessEqual(totals[-1], 2000.0)
        self.assertIsNotNone(manager.state("hot").guard_level)
        hot_audit = [
            r
            for r in manager.audit.records
            if r.source == "hot" and r.operator == "auto-guard"
        ]
        self.assertTrue(hot_audit)


class CapAndRevertTest(unittest.TestCase):
    def setUp(self):
        self.manager = LogLevelManager(delay_fn=lambda source: 0.0)
        for index in range(100):
            self.manager.add_source("src-%03d" % index, INFO)
        self.workload = Workload(
            self.manager, {name: 2.0 for name in self.manager.sources()}
        )
        self.guard = VolumeGuard(self.manager, cap=700.0, cooldown=3)

    def _run_ticks(self, count):
        totals = []
        for _ in range(count):
            self.manager.advance(0.1)
            totals.append(self.guard.observe(self.workload.tick()))
        return totals

    def test_auto_converge_when_over_cap(self):
        for name in self.manager.sources():
            self.manager.set_level(name, DEBUG, operator="op")

        totals = self._run_ticks(30)

        self.assertGreater(totals[0], 700.0)
        self.assertLessEqual(totals[-1], 700.0)
        guarded = sum(
            1
            for name in self.manager.sources()
            if self.manager.state(name).guard_level is not None
        )
        self.assertGreater(guarded, 0)

    def test_revert_leaves_no_stale_or_unapplied_nodes(self):
        for name in self.manager.sources():
            self.manager.set_level(name, DEBUG, operator="op")
        self._run_ticks(10)

        for name in self.manager.sources():
            self.manager.set_level(name, INFO, operator="op")
        totals = self._run_ticks(15)

        self.assertLessEqual(totals[-1], 700.0)
        self.assertTrue(self.manager.consistency_check().ok)
        leftovers = [
            name
            for name in self.manager.sources()
            if self.manager.state(name).guard_level is not None
        ]
        self.assertEqual(leftovers, [])
        for name in self.manager.sources():
            self.assertEqual(self.manager.effective_level(name), INFO)


class ConsistencyCheckTest(unittest.TestCase):
    def test_pending_change_is_detected(self):
        manager = LogLevelManager(delay_fn=lambda source: 1.0)
        manager.add_source("a", INFO)
        manager.add_source("b", INFO)

        manager.set_level("a", DEBUG, operator="op")
        report = manager.consistency_check()
        self.assertFalse(report.ok)
        self.assertEqual(len(report.mismatches), 1)
        self.assertEqual(report.mismatches[0]["source"], "a")

        manager.advance(2.0)
        self.assertTrue(manager.consistency_check().ok)


if __name__ == "__main__":
    unittest.main()

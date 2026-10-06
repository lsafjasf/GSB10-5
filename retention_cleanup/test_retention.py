"""保留期清理引擎自测（unittest，时间全部注入，不依赖真实时钟）。"""

import unittest

from engine import (
    Anomaly, Reason, Record, RetentionCleaner, RetentionPolicy, Verdict,
    Violation, apply_cleanup,
)

NOW = 1_759_708_800.0  # 2026-10-06T00:00:00Z，注入的“当前时间”
DAY = 86400.0
POLICY = RetentionPolicy(retention_seconds=90 * DAY, grace_seconds=0.0)


def rec(rid, created, accessed=None, refs=(), hold=False):
    return Record(rid, created, accessed, tuple(refs), hold)


def by_id(report):
    return {d.record_id: d for d in report.decisions}


class TestExpiredRules(unittest.TestCase):
    """情形一：全部到期。"""

    def setUp(self):
        self.cleaner = RetentionCleaner(POLICY)

    def test_old_unreferenced_unheld_record_is_deleted(self):
        report = self.cleaner.evaluate(
            [rec("old", NOW - 120 * DAY)], NOW, apply=True)
        self.assertEqual(report.deleted[0].record_id, "old")
        self.assertIn(Reason.DELETED_IDLE_EXPIRED, report.deleted[0].reasons)

    def test_boundary_idle_equal_to_retention_is_deleted(self):
        report = self.cleaner.evaluate(
            [rec("edge", NOW - 90 * DAY)], NOW)
        self.assertEqual(by_id(report)["edge"].verdict, Verdict.DELETE)

    def test_recent_record_is_active(self):
        report = self.cleaner.evaluate(
            [rec("fresh", NOW - 10 * DAY)], NOW)
        self.assertEqual(by_id(report)["fresh"].verdict, Verdict.ACTIVE)

    def test_recent_access_refreshes_idle_clock(self):
        # 创建很老但最近被访问过：按 max(created, last_access) 计算空闲期
        report = self.cleaner.evaluate(
            [rec("touched", NOW - 200 * DAY, NOW - 5 * DAY)], NOW)
        self.assertEqual(by_id(report)["touched"].verdict, Verdict.ACTIVE)

    def test_grace_period_protects_against_slow_clock(self):
        # 时钟偏慢场景的安全垫：宽限 1 天时，刚过期的记录被保留
        policy = RetentionPolicy(retention_seconds=90 * DAY, grace_seconds=DAY)
        cleaner = RetentionCleaner(policy)
        report = cleaner.evaluate([rec("just", NOW - 90.5 * DAY)], NOW)
        self.assertEqual(by_id(report)["just"].verdict, Verdict.ACTIVE)
        report = self.cleaner.evaluate([rec("just", NOW - 90.5 * DAY)], NOW)
        self.assertEqual(by_id(report)["just"].verdict, Verdict.DELETE)


class TestReferencedSpared(unittest.TestCase):
    """情形二：部分被引用。"""

    def setUp(self):
        self.cleaner = RetentionCleaner(POLICY)

    def test_referenced_record_survives_and_is_listed(self):
        records = [
            rec("hot", NOW - 500 * DAY, refs=("user-1",)),
            rec("user-1", NOW - 10 * DAY),
            rec("junk", NOW - 500 * DAY),
        ]
        report = self.cleaner.evaluate(records, NOW, apply=True)
        self.assertEqual([d.record_id for d in report.spared_referenced], ["hot"])
        self.assertEqual(report.spared_referenced[0].referrers, ("user-1",))
        self.assertEqual([d.record_id for d in report.deleted], ["junk"])

    def test_self_reference_counts_as_reference(self):
        report = self.cleaner.evaluate(
            [rec("selfish", NOW - 500 * DAY, refs=("selfish",))], NOW)
        self.assertEqual(by_id(report)["selfish"].verdict, Verdict.REFERENCED)

    def test_mutual_reference_cycle_survives(self):
        records = [
            rec("a", NOW - 500 * DAY, refs=("b",)),
            rec("b", NOW - 500 * DAY, refs=("a",)),
        ]
        report = self.cleaner.evaluate(records, NOW)
        self.assertEqual(report.deleted, [])
        self.assertEqual(len(report.spared_referenced), 2)

    def test_orphan_after_delete_aborts_instead_of_implicit_cascade(self):
        # child 会被删 -> parent 唯一引用者消失（隐式级联）。
        # 安全策略：中止整批清理，要求人工/显式策略处理，绝不静默级联。
        records = [
            rec("parent", NOW - 500 * DAY, refs=("child",)),
            rec("child", NOW - 500 * DAY),
        ]
        report = self.cleaner.evaluate(records, NOW, apply=True)
        self.assertTrue(report.cleanup_aborted)
        self.assertFalse(report.applied)
        kinds = {v.kind for v in report.violations}
        self.assertIn(Violation.REFERENCED_BY_DELETED, kinds)


class TestHoldPause(unittest.TestCase):
    """情形三：存在暂停标记。"""

    def setUp(self):
        self.cleaner = RetentionCleaner(POLICY)

    def test_held_record_survives_even_if_ancient(self):
        report = self.cleaner.evaluate(
            [rec("legal-hold", NOW - 3000 * DAY, hold=True)], NOW)
        d = by_id(report)["legal-hold"]
        self.assertEqual(d.verdict, Verdict.HELD)
        self.assertIn(Reason.HELD_ACTIVE, d.reasons)

    def test_hold_lifted_allows_cleanup(self):
        cleaner = self.cleaner
        held = cleaner.evaluate([rec("x", NOW - 300 * DAY, hold=True)], NOW)
        self.assertEqual(held.deleted, [])
        lifted = cleaner.evaluate([rec("x", NOW - 300 * DAY, hold=False)], NOW)
        self.assertEqual([d.record_id for d in lifted.deleted], ["x"])


class TestTimeAnomalies(unittest.TestCase):
    """情形四：时间异常。"""

    def setUp(self):
        self.cleaner = RetentionCleaner(POLICY)

    def _verdict(self, record):
        report = self.cleaner.evaluate([record], NOW)
        return by_id(report)[record.record_id]

    def test_future_created_at_is_anomaly_not_deleted(self):
        d = self._verdict(rec("future", NOW + 365 * DAY))
        self.assertEqual(d.verdict, Verdict.ANOMALY)
        self.assertIn(Anomaly.FUTURE_TIMESTAMP, d.anomalies)

    def test_negative_timestamp_is_anomaly(self):
        d = self._verdict(rec("neg", -1))
        self.assertEqual(d.verdict, Verdict.ANOMALY)
        self.assertIn(Anomaly.NEGATIVE_TIMESTAMP, d.anomalies)

    def test_non_finite_timestamp_is_anomaly(self):
        d = self._verdict(rec("nan", float("nan")))
        self.assertEqual(d.verdict, Verdict.ANOMALY)
        self.assertIn(Anomaly.NON_FINITE_TIMESTAMP, d.anomalies)

    def test_access_before_created_is_anomaly(self):
        d = self._verdict(rec("weird", NOW - 10 * DAY, NOW - 20 * DAY))
        self.assertEqual(d.verdict, Verdict.ANOMALY)
        self.assertIn(Anomaly.ACCESS_BEFORE_CREATED, d.anomalies)

    def test_small_future_skew_within_tolerance_is_allowed(self):
        # 时钟轻微超前（< skew 容忍）不算异常，且不会被误删
        d = self._verdict(rec("slight", NOW + 3600))
        self.assertEqual(d.verdict, Verdict.ACTIVE)

    def test_anomaly_takes_priority_over_delete(self):
        d = self._verdict(rec("old-future", NOW + 365 * DAY))
        self.assertEqual(d.verdict, Verdict.ANOMALY)


class TestClockScenarios(unittest.TestCase):
    """时钟不准的两类结果：准确时钟 vs 回拨时钟。"""

    def test_accurate_clock_scenario(self):
        cleaner = RetentionCleaner(POLICY)
        records = [
            rec("victim", NOW - 200 * DAY),
            rec("keeper", NOW - 200 * DAY, refs=("app",)),
            rec("app", NOW - 1 * DAY),
        ]
        report = cleaner.evaluate(records, NOW, apply=True)
        self.assertEqual([d.record_id for d in report.deleted], ["victim"])
        self.assertEqual([d.record_id for d in report.spared_referenced], ["keeper"])

    def test_regressed_clock_keeps_everything(self):
        # 时钟回拨 30 天：空闲期被低估，到期记录全部转为保留，绝不误删
        cleaner = RetentionCleaner(POLICY)
        records = [rec("victim", NOW - 100 * DAY), rec("victim2", NOW - 95 * DAY)]
        skewed_now = NOW - 30 * DAY
        report = cleaner.evaluate(records, skewed_now, apply=True)
        self.assertEqual(report.deleted, [])
        self.assertEqual(len(report.spared_active), 2)


class TestIntegrityValidation(unittest.TestCase):
    """清理前的引用完整性校验。"""

    def setUp(self):
        self.cleaner = RetentionCleaner(POLICY)

    def test_dangling_reference_aborts_cleanup(self):
        records = [
            rec("target", NOW - 500 * DAY, refs=("ghost",)),
            rec("junk", NOW - 500 * DAY),
        ]
        report = self.cleaner.evaluate(records, NOW, apply=True)
        self.assertFalse(report.integrity_ok)
        self.assertTrue(report.cleanup_aborted)
        self.assertFalse(report.applied)
        self.assertEqual(report.decisions, [])
        kinds = {v.kind for v in report.violations}
        self.assertIn(Violation.DANGLING_REFERENCE, kinds)

    def test_duplicate_id_aborts_cleanup(self):
        records = [rec("dup", NOW - 500 * DAY), rec("dup", NOW - 10 * DAY)]
        report = self.cleaner.evaluate(records, NOW, apply=True)
        self.assertTrue(report.cleanup_aborted)
        kinds = {v.kind for v in report.violations}
        self.assertIn(Violation.DUPLICATE_ID, kinds)

    def test_clean_dataset_passes_integrity(self):
        records = [rec("a", NOW - 500 * DAY, refs=("b",)), rec("b", NOW - 1 * DAY)]
        report = self.cleaner.evaluate(records, NOW, apply=True)
        self.assertTrue(report.integrity_ok)
        self.assertFalse(report.cleanup_aborted)


class TestTraceability(unittest.TestCase):
    """删除可追溯：清单 + 理由 + 物理删除的一致性。"""

    def test_manifest_and_physical_delete(self):
        cleaner = RetentionCleaner(POLICY)
        records = [
            rec("gone", NOW - 400 * DAY),
            rec("kept-ref", NOW - 400 * DAY, refs=("svc",)),
            rec("svc", NOW - 2 * DAY),
            rec("kept-hold", NOW - 400 * DAY, hold=True),
        ]
        report = cleaner.evaluate(records, NOW, apply=True)
        manifest = report.manifest()
        by = {e["record_id"]: e for e in manifest["entries"]}
        self.assertEqual(by["gone"]["verdict"], "delete")
        self.assertEqual(by["gone"]["reasons"], [Reason.DELETED_IDLE_EXPIRED])
        self.assertEqual(by["kept-ref"]["verdict"], "referenced")
        self.assertEqual(by["kept-ref"]["referrers"], ["svc"])
        self.assertEqual(by["kept-hold"]["verdict"], "held")
        survivors = {r.record_id for r in apply_cleanup(records, report)}
        self.assertEqual(survivors, {"kept-ref", "svc", "kept-hold"})

    def test_apply_cleanup_refuses_unapplied_report(self):
        cleaner = RetentionCleaner(POLICY)
        records = [rec("gone", NOW - 400 * DAY)]
        report = cleaner.evaluate(records, NOW, apply=False)
        with self.assertRaises(RuntimeError):
            apply_cleanup(records, report)


class TestNoForbiddenDependencies(unittest.TestCase):
    """只允许标准库；引擎不得自行读取系统时钟。"""

    def test_engine_imports_only_stdlib(self):
        import engine
        self.assertFalse(hasattr(engine, "requests"))

    def test_engine_never_reads_wall_clock(self):
        import inspect
        import engine
        src = inspect.getsource(engine)
        for forbidden in ("time.time", "datetime.now", "monotonic"):
            self.assertNotIn(forbidden, src)


if __name__ == "__main__":
    unittest.main(verbosity=2)

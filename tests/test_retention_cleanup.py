"""保留期清理库自测（unittest，时间全部注入）。"""

import unittest

from retention_cleanup import Decision, Policy, Record, evaluate

NOW = 1_000_000_000.0
DAY = 86400.0
RETENTION = 30 * DAY
POLICY = Policy(retention_seconds=RETENTION, clock_skew_tolerance_seconds=300.0)

OLD = NOW - 100 * DAY
FRESH = NOW - 1 * DAY


def run(records, now=NOW, policy=POLICY):
    return evaluate(records, now, policy)


def deleted_ids(report):
    return {e.id for e in report.deletion_manifest}


def kept_ids(report):
    return {e.id for e in report.spared}


class AllExpiredTest(unittest.TestCase):
    """全部到期：无引用、无暂停标记 -> 全部删除，幸免清单为空。"""

    def test_all_expired_deleted(self):
        records = [Record(f"r{i}", OLD, OLD) for i in range(5)]
        report = run(records)
        self.assertEqual(report.delete_count, 5)
        self.assertEqual(deleted_ids(report), {f"r{i}" for i in range(5)})
        self.assertEqual(report.spared, [])

    def test_manifest_is_traceable(self):
        report = run([Record("r1", OLD, OLD)])
        entry = report.deletion_manifest[0]
        self.assertEqual(entry.id, "r1")
        self.assertEqual(entry.evaluated_at, NOW)
        self.assertAlmostEqual(entry.idle_seconds, NOW - OLD)
        self.assertIn("retention", entry.reason)

    def test_boundary_exactly_at_retention_is_expired(self):
        report = run([Record("edge", NOW - RETENTION, NOW - RETENTION)])
        self.assertEqual(report.delete_count, 1)

    def test_just_inside_retention_kept(self):
        report = run([Record("edge", NOW - RETENTION + 1, NOW - RETENTION + 1)])
        self.assertEqual(report.delete_count, 0)
        self.assertEqual(report.spared[0].decision, Decision.KEEP_NOT_EXPIRED)


class PartiallyReferencedTest(unittest.TestCase):
    """部分被引用：被引用的到期记录幸免，其余到期记录删除。"""

    def test_referenced_expired_record_spared(self):
        records = [
            Record("victim", OLD, OLD),
            Record("hero", OLD, OLD),
            Record("referrer", FRESH, FRESH, references=("hero",)),
        ]
        report = run(records)
        self.assertEqual(deleted_ids(report), {"victim"})
        self.assertEqual(kept_ids(report), {"hero", "referrer"})
        self.assertEqual(len(report.spared_referenced), 1)
        spared = report.spared_referenced[0]
        self.assertEqual(spared.id, "hero")
        self.assertEqual(spared.referrers, ("referrer",))

    def test_dangling_reference_does_not_protect(self):
        records = [Record("lonely", OLD, OLD, references=("ghost",))]
        report = run(records)
        self.assertEqual(deleted_ids(report), {"lonely"})
        self.assertEqual(len(report.dangling_references), 1)
        self.assertEqual(report.dangling_references[0].target_id, "ghost")

    def test_reference_from_doomed_record_still_protects_this_round(self):
        # 引用方自己本轮也会被删，但为避免级联误删，被引记录本轮幸免。
        records = [
            Record("old_referrer", OLD, OLD, references=("old_target",)),
            Record("old_target", OLD, OLD),
        ]
        report = run(records)
        self.assertEqual(deleted_ids(report), {"old_referrer"})
        self.assertEqual(kept_ids(report), {"old_target"})


class RetentionHoldTest(unittest.TestCase):
    """暂停标记：无论多旧都不删除。"""

    def test_hold_spared_even_when_ancient(self):
        records = [Record("frozen", OLD, OLD, retention_hold=True)]
        report = run(records)
        self.assertEqual(report.delete_count, 0)
        self.assertEqual(report.spared[0].decision, Decision.KEEP_HOLD)

    def test_hold_beats_everything_except_anomaly(self):
        records = [
            Record("held_and_referenced", OLD, OLD, retention_hold=True),
            Record("referrer", FRESH, FRESH, references=("held_and_referenced",)),
        ]
        report = run(records)
        decisions = {e.id: e.decision for e in report.spared}
        self.assertEqual(decisions["held_and_referenced"], Decision.KEEP_HOLD)


class ClockAnomalyTest(unittest.TestCase):
    """时间异常：时钟不准时一律保守保留，绝不误删。"""

    def test_far_future_timestamp_spared(self):
        records = [Record("future", NOW + 10 * DAY, NOW + 10 * DAY)]
        report = run(records)
        self.assertEqual(report.delete_count, 0)
        self.assertEqual(report.spared[0].decision, Decision.KEEP_CLOCK_ANOMALY)
        self.assertEqual(len(report.anomalies), 1)

    def test_within_tolerance_future_clamped_not_deleted(self):
        # 容忍带内的轻微前拨：钳制为 now，相当于刚活动过，保留但不算异常。
        records = [Record("slightly_fast", NOW - 10 * DAY, NOW + 60.0)]
        report = run(records)
        self.assertEqual(report.delete_count, 0)
        self.assertEqual(report.spared[0].decision, Decision.KEEP_NOT_EXPIRED)
        self.assertEqual(report.anomalies, [])

    def test_access_before_creation_spared(self):
        records = [Record("inverted", OLD, OLD - 5 * DAY)]
        report = run(records)
        self.assertEqual(report.delete_count, 0)
        self.assertEqual(report.spared[0].decision, Decision.KEEP_CLOCK_ANOMALY)

    def test_anomaly_beats_hold_and_reference(self):
        records = [
            Record("weird", NOW + 10 * DAY, NOW + 10 * DAY, retention_hold=True),
            Record("referrer", FRESH, FRESH, references=("weird",)),
        ]
        report = run(records)
        decisions = {e.id: e.decision for e in report.spared}
        self.assertEqual(decisions["weird"], Decision.KEEP_CLOCK_ANOMALY)

    def test_system_clock_moved_backwards(self):
        # 记录时间戳相对“现在”都在未来（时钟回拨场景）：
        # 注入一个比所有时间戳都早的 now，全部按异常/未到期保留，零删除。
        records = [Record(f"r{i}", OLD, OLD) for i in range(3)]
        report = run(records, now=OLD - 1000 * DAY)
        self.assertEqual(report.delete_count, 0)
        self.assertEqual(report.delete_count + len(report.spared), 3)


class MixedScenarioTest(unittest.TestCase):
    """混合场景：与 demo 一致的数据集，验证各判定路径同时工作。"""

    def test_mixed(self):
        records = [
            Record("r1", OLD, OLD),                                  # 删
            Record("r2", OLD, OLD),                                  # 删
            Record("r3", OLD, NOW - 31 * DAY),                       # 删
            Record("r4", OLD, OLD),                                  # 被 r6 引用，幸免
            Record("r5", OLD, OLD, references=("ghost",)),           # 悬空引用，删
            Record("r6", FRESH, FRESH, references=("r4",)),          # 未到期
            Record("r7", OLD, OLD, references=("r11",), retention_hold=True),  # 暂停
            Record("r8", NOW - 10 * DAY, NOW + 60.0),                # 容忍带内，未到期
            Record("r9", NOW + 10 * DAY, NOW + 10 * DAY),            # 异常
            Record("r10", OLD, OLD - 5 * DAY),                       # 异常
            Record("r11", OLD, OLD),                                 # 被 r7 引用，幸免
        ]
        report = run(records)
        self.assertEqual(deleted_ids(report), {"r1", "r2", "r3", "r5"})
        self.assertEqual(kept_ids(report), {"r4", "r6", "r7", "r8", "r9", "r10", "r11"})
        self.assertEqual({e.id for e in report.spared_referenced}, {"r4", "r11"})
        self.assertEqual({e.id for e in report.anomalies}, {"r9", "r10"})
        self.assertEqual(len(report.dangling_references), 1)
        summary = report.summary()
        self.assertEqual(summary["delete"], 4)
        self.assertEqual(summary["kept"], 7)
        self.assertEqual(summary["total_records"], 11)


class LastAccessExtendsLifeTest(unittest.TestCase):
    """最后访问时间参与判定：最近被访问过的老记录不删。"""

    def test_recent_access_keeps_old_record(self):
        records = [Record("old_but_hot", OLD, NOW - 1 * DAY)]
        report = run(records)
        self.assertEqual(report.delete_count, 0)
        self.assertEqual(report.spared[0].decision, Decision.KEEP_NOT_EXPIRED)


if __name__ == "__main__":
    unittest.main()

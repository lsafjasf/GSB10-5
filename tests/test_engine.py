"""自测：单信号、多弱信号叠加、规则冲突、事件升级、SLA 统计、降级迟滞。

运行：python3 -m unittest discover -s tests -v
时钟全部由 FakeClock 注入，无真实时间依赖。
"""
import unittest

from seclib import Asset, Event, Level, SecurityEngine, Signal
from seclib.engine import SLA_SECONDS
from seclib.grading import HYSTERESIS, grade, lower_threshold


class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_event(event_id, strengths, criticality=3, scope=2):
    return Event(
        event_id=event_id,
        asset=Asset(f"asset-{event_id}", criticality),
        scope_level=scope,
        signals=[Signal(f"sig-{i}", s) for i, s in enumerate(strengths)],
    )


class TestGrading(unittest.TestCase):
    def test_single_strong_signal(self):
        # 单条强信号 5，普通资产(3)、小范围(1)：0.5*5+0.3*3+0.2*1=3.6 -> P2
        result = grade(make_event("e1", [5], criticality=3, scope=1))
        self.assertEqual(result.level, Level.P2)
        self.assertAlmostEqual(result.score, 3.6, places=4)
        dims = [c.dimension for c in result.contributions]
        self.assertEqual(dims.count("证据强度"), 1)

    def test_single_weak_signal(self):
        # 单条弱信号 1，普通资产、小范围：0.5+0.9+0.2=1.6 -> P4
        result = grade(make_event("e2", [1], criticality=3, scope=1))
        self.assertEqual(result.level, Level.P4)

    def test_weak_signals_stack_beyond_strong(self):
        # 4 条强度 2 的弱信号聚合 2+3*2*0.6=5.6 > 单条强信号 5.0
        weak = grade(make_event("e3", [2, 2, 2, 2], criticality=3, scope=1))
        strong = grade(make_event("e4", [5], criticality=3, scope=1))
        self.assertGreater(weak.evidence, strong.evidence)   # 5.6 > 5.0
        self.assertGreater(weak.score, strong.score)         # 3.9 > 3.6
        self.assertAlmostEqual(weak.score, 3.9, places=4)

    def test_stacked_weak_on_critical_asset_beats_strong(self):
        # 核心资产(5) 上 4 条弱信号 vs 边缘资产(1) 上 1 条强信号
        weak = grade(make_event("e5", [2, 2, 2, 2], criticality=5, scope=3))
        strong = grade(make_event("e6", [5], criticality=1, scope=1))
        self.assertGreater(weak.score, strong.score)
        self.assertEqual(weak.level, Level.P1)

    def test_contributions_cover_all_dimensions(self):
        result = grade(make_event("e7", [3, 2], criticality=4, scope=2))
        dims = {c.dimension for c in result.contributions}
        self.assertEqual(dims, {"证据强度", "资产重要性", "影响范围"})
        total = sum(c.weighted for c in result.contributions)
        self.assertAlmostEqual(total, result.score, places=4)


class TestEscalation(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.engine = SecurityEngine(clock=self.clock)

    def test_escalate_by_new_signal(self):
        self.engine.ingest(make_event("e1", [2], criticality=3, scope=1))
        before = self.engine.state_of("e1").current.level
        self.engine.add_signal("e1", Signal("sig-x", 5))
        state = self.engine.state_of("e1")
        self.assertLess(state.current.level, before)  # 级别数值变小=更严重
        rules = [t.rule for t in state.trajectory]
        self.assertIn("R1_NEW_SIGNAL", rules)

    def test_escalate_by_scope_expansion(self):
        self.engine.ingest(make_event("e2", [3], criticality=3, scope=1))
        before = self.engine.state_of("e2").current.level
        self.engine.expand_scope("e2", 5)
        state = self.engine.state_of("e2")
        self.assertLessEqual(state.current.level, before)
        self.assertIn("R3_SCOPE_EXPAND", [t.rule for t in state.trajectory])

    def test_escalate_by_sla_warn(self):
        self.engine.ingest(make_event("e3", [2], criticality=3, scope=1))
        level = self.engine.state_of("e3").current.level
        self.clock.advance(SLA_SECONDS[level] * 0.5 + 1)
        fired = self.engine.tick()
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0].rule, "R2_SLA_WARN")
        self.assertEqual(fired[0].new_level, level.higher())

    def test_sla_warn_cooldown(self):
        self.engine.ingest(make_event("e4", [2], criticality=3, scope=1))
        level = self.engine.state_of("e4").current.level
        self.clock.advance(SLA_SECONDS[level] * 0.5 + 1)
        self.engine.tick()
        count_after_first = len(self.engine.state_of("e4").trajectory)
        self.clock.advance(60)  # 冷却期内不再升级
        self.engine.tick()
        self.assertEqual(len(self.engine.state_of("e4").trajectory), count_after_first)

    def test_no_escalation_after_ack(self):
        self.engine.ingest(make_event("e5", [2], criticality=3, scope=1))
        self.clock.advance(100)
        self.engine.acknowledge("e5")
        self.clock.advance(100_000)
        self.assertEqual(self.engine.tick(), [])

    def test_deescalate_with_hysteresis(self):
        # 初评 P2（分接近阈值），移除信号后分数落在迟滞带内 -> 不降级
        self.engine.ingest(make_event("e6", [3, 3], criticality=3, scope=1))
        state = self.engine.state_of("e6")
        level = state.current.level
        self.engine.expire_signals("e6", ["sig-1"])
        state = self.engine.state_of("e6")
        score = state.current.score
        threshold = lower_threshold(level)
        if threshold - HYSTERESIS <= score < threshold:
            self.assertEqual(state.current.level, level)  # 迟滞带内保持
        else:
            self.assertEqual(state.current.level, level.lower())

    def test_deescalate_one_level_per_round(self):
        # 强信号失效后分数跌穿多级，也只降一级
        self.engine.ingest(make_event("e7", [5, 1], criticality=5, scope=5))
        top = self.engine.state_of("e7").current.level
        self.engine.expire_signals("e7", ["sig-0"])
        state = self.engine.state_of("e7")
        self.assertEqual(state.current.level, top.lower())

    def test_conflict_escalation_wins(self):
        # 同一轮：信号失效（应降级）与 SLA 预警（应升级）冲突 -> 升级优先
        self.engine.ingest(make_event("e8", [4, 1], criticality=3, scope=1))
        level = self.engine.state_of("e8").current.level
        self.clock.advance(SLA_SECONDS[level] * 0.5 + 1)
        self.engine.expire_signals("e8", ["sig-1"])
        state = self.engine.state_of("e8")
        self.assertEqual(state.current.level, level.higher())
        last = state.trajectory[-1]
        self.assertEqual(last.rule, "R2_SLA_WARN")
        self.assertIn("升级优先", last.reason)

    def test_trajectory_is_ordered_and_traceable(self):
        self.engine.ingest(make_event("e9", [2], criticality=3, scope=1))
        self.engine.add_signal("e9", Signal("sig-a", 4))
        self.clock.advance(10_000)
        self.engine.tick()
        traj = self.engine.state_of("e9").trajectory
        self.assertGreaterEqual(len(traj), 3)
        for prev, nxt in zip(traj, traj[1:]):
            self.assertLessEqual(prev.ts, nxt.ts)
            self.assertEqual(prev.new_level, nxt.old_level)


class TestResponseSLA(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.engine = SecurityEngine(clock=self.clock)

    def test_response_time_and_overdue(self):
        self.engine.ingest(make_event("fast", [5], criticality=5, scope=5))   # P1
        self.engine.ingest(make_event("slow", [1], criticality=1, scope=1))   # P4
        self.clock.advance(300)
        self.engine.acknowledge("fast")
        self.clock.advance(200_000)
        self.engine.acknowledge("slow")
        self.assertEqual(self.engine.response_seconds("fast"), 300)
        self.assertFalse(self.engine.is_overdue("fast"))
        self.assertTrue(self.engine.is_overdue("slow"))
        stats = self.engine.stats()
        self.assertEqual(stats["overdue"], 1)
        self.assertEqual(stats["acked"], 2)
        self.assertAlmostEqual(stats["avg_response_seconds"], (300 + 200_300) / 2, places=2)

    def test_sla_bound_to_level(self):
        self.assertEqual(SLA_SECONDS[Level.P1], 900)
        self.assertLess(SLA_SECONDS[Level.P1], SLA_SECONDS[Level.P2])
        self.assertLess(SLA_SECONDS[Level.P2], SLA_SECONDS[Level.P3])
        self.assertLess(SLA_SECONDS[Level.P3], SLA_SECONDS[Level.P4])

    def test_sla_checked_against_level_at_ack(self):
        # 响应前被 R2 升级，则按升级后的级别核对时限
        self.engine.ingest(make_event("e1", [2], criticality=3, scope=1))
        level = self.engine.state_of("e1").current.level
        self.clock.advance(SLA_SECONDS[level] * 0.5 + 1)
        self.engine.tick()
        self.engine.acknowledge("e1")
        self.assertEqual(
            self.engine.sla_for("e1"), SLA_SECONDS[level.higher()]
        )


class TestValidation(unittest.TestCase):
    def test_invalid_strength_rejected(self):
        with self.assertRaises(ValueError):
            Signal("bad", 0)

    def test_event_needs_signal(self):
        with self.assertRaises(ValueError):
            Event("e", Asset("a", 3), 2, [])

    def test_duplicate_event_rejected(self):
        engine = SecurityEngine(clock=FakeClock())
        engine.ingest(make_event("dup", [1]))
        with self.assertRaises(KeyError):
            engine.ingest(make_event("dup", [1]))


if __name__ == "__main__":
    unittest.main()

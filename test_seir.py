"""Self-tests. Run: python3 -m unittest -v test_seir"""

import unittest

from seir import (
    DEFAULT_SLA,
    Event,
    Incident,
    Level,
    Rule,
    RuleEngine,
    fuse_signals,
    score_event,
)
from seir.incident import HYSTERESIS, sla_report
from seir.rules import BOOST, FORCE_LEVEL, SUPPRESS
from seir.scoring import Signal


class FakeClock:
    """Deterministic time source injected into Incident."""

    def __init__(self, start=1_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def sig(id_, strength, category="generic", confidence=1.0):
    return Signal(id=id_, category=category, strength=strength, confidence=confidence)


class ScoringTests(unittest.TestCase):
    def test_single_strong_signal_dominates(self):
        """单强信号：一条 90 分信号在核心资产上直达 P1。"""
        event = Event("E1", asset_tier=4, affected=1,
                      signals=[sig("s1", 70, "ransomware")])
        b = score_event(event)
        self.assertEqual(b.fused, 70.0)
        self.assertAlmostEqual(b.score, 70 * 1.4)
        self.assertGreaterEqual(b.score, 85)
        self.assertEqual(len(b.contributions), 1)
        self.assertEqual(b.contributions[0].marginal_points, 70.0)
        self.assertEqual(b.contributions[0].share, 1.0)

    def test_many_weak_signals_stack(self):
        """多弱信号叠加：单条都是 P5，叠加后升到 P3。"""
        weak = [sig(f"w{i}", 18, "suspicious") for i in range(4)]
        one = Event("weak-one", 2, 1, signals=[weak[0]])
        stacked = Event("weak-many", 2, 1, signals=weak)
        b_one, b_stack = score_event(one), score_event(stacked)
        fused, _ = fuse_signals(weak)
        self.assertLess(b_one.score, 25)                       # 18 < P4 线
        self.assertAlmostEqual(fused, 100 * (1 - 0.82 ** 4), places=4)  # 54.8
        self.assertGreaterEqual(b_stack.score, 45)            # 叠加达 P3
        self.assertLess(fused, 4 * 18)                        # 非线性，非条数相加
        self.assertAlmostEqual(
            sum(c.marginal_points for c in b_stack.contributions),
            b_stack.fused,
        )

    def test_alert_count_does_not_dominate(self):
        """规则要求：不能只看条数。0 分告警无论多少条都不抬级别。"""
        noise = [sig(f"n{i}", 0, "noise") for i in range(100)]
        b = score_event(Event("noise", 5, 1000, signals=noise))
        self.assertEqual(b.fused, 0.0)
        self.assertEqual(b.score, 0.0)

    def test_confidence_scales_strength(self):
        b = fuse_signals([sig("c", 50, confidence=0.5)])
        self.assertAlmostEqual(b[0], 25.0)

    def test_scope_and_asset_are_capped(self):
        b = score_event(Event("tiny", 5, 10 ** 6, signals=[sig("s", 30)]))
        self.assertEqual(b.scope_factor, 1.6)
        self.assertEqual(b.asset_factor, 1.6)


class RuleConflictTests(unittest.TestCase):
    def test_suppress_beats_lower_priority_force(self):
        """规则冲突：suppress 优先级 100 战胜 force_level 优先级 10。"""
        rules = [
            Rule("whitelist", 100, lambda e: e.id == "X", SUPPRESS),
            Rule("compliance-escalate", 10, lambda e: True, FORCE_LEVEL, Level.P1),
        ]
        inc = Incident("inc", FakeClock(), rules=RuleEngine(rules))
        a = inc.assess(Event("X", 5, 1, [sig("s", 90)]))
        self.assertTrue(a.suppressed)
        self.assertEqual(a.computed_level, Level.P5)
        self.assertEqual(a.applied_rules, ["whitelist"])

    def test_force_beats_lower_priority_suppress(self):
        """反向冲突：force_level 优先级更高时 force 生效（结果唯一确定）。"""
        rules = [
            Rule("whitelist", 10, lambda e: True, SUPPRESS),
            Rule("regulatory", 100, lambda e: True, FORCE_LEVEL, Level.P2),
        ]
        inc = Incident("inc", FakeClock(), rules=RuleEngine(rules))
        a = inc.assess(Event("Y", 1, 1, [sig("s", 5)]))
        self.assertEqual(a.forced_level, Level.P2)
        self.assertFalse(a.suppressed)

    def test_equal_priority_uses_name_order(self):
        """同优先级冲突：按规则名字典序，结果确定。"""
        rules = [
            Rule("zzz-suppress", 50, lambda e: True, SUPPRESS),
            Rule("aaa-suppress-ok", 50, lambda e: True, FORCE_LEVEL, Level.P3),
        ]
        inc = Incident("inc", FakeClock(), rules=RuleEngine(rules))
        a = inc.assess(Event("Z", 1, 1, []))
        self.assertEqual(a.applied_rules, ["aaa-suppress-ok"])

    def test_boost_then_suppress(self):
        rules = [
            Rule("boost-critical-asset", 50, lambda e: e.asset_tier == 5, BOOST, 10),
            Rule("whitelist", 100, lambda e: e.id == "X", SUPPRESS),
        ]
        inc = Incident("inc", FakeClock(), rules=RuleEngine(rules))
        a = inc.assess(Event("X", 5, 1, [sig("s", 50)]))
        self.assertTrue(a.suppressed)
        self.assertEqual(a.applied_rules, ["whitelist"])


class LifecycleTests(unittest.TestCase):
    def test_escalation_trajectory(self):
        """事件升级：弱信号 -> 强信号 -> 更多强信号，轨迹 P4 -> P2 -> P1。"""
        clk = FakeClock()
        inc = Incident("INC-1", clk)
        t1 = inc.update(Event("INC-1", 3, 1, [sig("w", 22)]))
        clk.advance(300)
        t2 = inc.update(Event("INC-1", 3, 1,
                              [sig("w", 22), sig("strong", 60, "exploit")]))
        clk.advance(60)
        t3 = inc.update(Event("INC-1", 3, 10,
                              [sig("w", 22), sig("strong", 60),
                               sig("lateral", 70, "lateral")]))
        self.assertEqual((t1.to_level, t2.to_level, t3.to_level),
                         (Level.P4, Level.P2, Level.P1))
        reasons = [t.reason for t in inc.trajectory]
        self.assertEqual(reasons, ["initial", "escalate", "escalate"])
        self.assertEqual(len(inc.assessments), 3)

    def test_hysteresis_blocks_flapping(self):
        """降级迟滞：分数略降但不足 5 分阈值时不降级。"""
        clk = FakeClock()
        inc = Incident("INC-2", clk)
        inc.update(Event("INC-2", 2, 1, [sig("a", 68)]))   # 68 -> P2
        self.assertEqual(inc.level, Level.P2)
        t = inc.update(Event("INC-2", 2, 1, [sig("a", 63)]))  # 63, 距 P2 下界 65 仅 2
        self.assertEqual(t.reason, "unchanged")
        self.assertEqual(inc.level, Level.P2)

    def test_degradation_after_full_hysteresis(self):
        clk = FakeClock()
        inc = Incident("INC-3", clk)
        inc.update(Event("INC-2", 2, 1, [sig("a", 68)]))        # P2
        t = inc.update(Event("INC-2", 2, 1, [sig("a", 55)]))    # 55 <= 65-5
        self.assertEqual(t.reason, "degrade")
        self.assertEqual(t.to_level, Level.P3)

    def test_force_pin_then_computed_escalation(self):
        clk = FakeClock()
        rules = RuleEngine([Rule("watch", 10, lambda e: True, FORCE_LEVEL, Level.P3)])
        inc = Incident("INC-4", clk, rules=rules)
        inc.update(Event("INC-4", 1, 1, [sig("a", 5)]))         # P3 下限钉住
        t = inc.update(Event("INC-4", 5, 1, [sig("a", 95)]))    # 计算 P1 更严重
        self.assertEqual(t.reason, "rule:force")
        self.assertEqual(t.to_level, Level.P1)


class SlaTests(unittest.TestCase):
    def test_response_on_time_and_breach(self):
        clk = FakeClock()
        inc = Incident("S1", clk, sla=DEFAULT_SLA)
        inc.update(Event("S1", 5, 1, [sig("a", 90)]))           # P1, 900s
        clk.advance(900)
        elapsed = inc.respond()
        self.assertEqual(elapsed, 900.0)
        self.assertFalse(inc.sla_breached)                       # 等于时限不算超时

        clk2 = FakeClock()
        inc2 = Incident("S2", clk2, sla=DEFAULT_SLA)
        inc2.update(Event("S2", 5, 1, [sig("a", 90)]))
        clk2.advance(901)
        inc2.respond()
        self.assertTrue(inc2.sla_breached)

    def test_escalation_restarts_deadline_and_report(self):
        """升级后按新级别重算时限；sla_report 汇总各级超时。"""
        clk = FakeClock()
        inc = Incident("R1", clk)
        inc.update(Event("R1", 2, 1, [sig("a", 30)]))           # P4
        clk.advance(20 * 3600)                                   # P4 已超时但发生升级
        inc.update(Event("R1", 5, 1, [sig("a", 95)]))           # P1, 时限重置
        clk.advance(600)
        inc.respond()
        self.assertEqual(inc.level, Level.P1)
        self.assertEqual(inc.response_time, 600.0)
        self.assertFalse(inc.sla_breached)

        report = sla_report([inc])
        self.assertEqual(report[Level.P1].total, 1)
        self.assertEqual(report[Level.P1].responded, 1)
        self.assertEqual(report[Level.P1].breached, 0)

    def test_unresponded_breach_counts(self):
        clk = FakeClock()
        inc = Incident("U1", clk)
        inc.update(Event("U1", 5, 1, [sig("a", 90)]))           # P1
        clk.advance(10_000)
        report = sla_report([inc])
        self.assertEqual(report[Level.P1].breached, 1)
        self.assertEqual(report[Level.P1].responded, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

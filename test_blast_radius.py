"""
自测：python3 -m unittest test_blast_radius -v

覆盖：无影响 / 单点影响 / 级联影响 / 冗余同时失效 / 弱依赖降级 /
环状依赖收敛 / 偏差对比与归因 / 入口直连故障 / 输入校验 / 规模冒烟。
"""

from __future__ import annotations

import time
import unittest

from blast_radius import (
    DrillResult, Topology, compare, estimate, generate_scale_topology,
)
from demo import TOPO


def topo() -> Topology:
    return Topology.from_dict(TOPO)


class TestNoImpact(unittest.TestCase):
    def test_redundancy_absorbs_single_member(self):
        r = estimate(topo(), ["stock-a"])
        self.assertEqual(r.affected_user_ratio, 0.0)
        self.assertEqual(r.affected_entries, [])
        self.assertEqual(r.critical_path_count, 0)
        self.assertEqual(r.down, ["stock-a"])       # 副本自身挂了，但被冗余兜住
        self.assertEqual(r.broken_groups, [])


class TestSinglePoint(unittest.TestCase):
    def test_searchdb_only_burns_web_search_traffic(self):
        r = estimate(topo(), ["searchdb"])
        self.assertAlmostEqual(r.affected_user_ratio, 0.15)   # 0.5入口 x 0.3比例
        self.assertEqual(r.affected_entries, ["web"])
        self.assertEqual(set(r.down), {"searchdb", "search"})
        self.assertEqual(r.critical_path_count, 1)            # web->search->searchdb
        self.assertTrue(r.critical_path_exact)


class TestCascade(unittest.TestCase):
    def test_pay_cascades_through_order_to_both_entries(self):
        r = estimate(topo(), ["pay"])
        # order健康0.5 -> web=1-0.5*0.5=0.75, app=1-0.7*0.5=0.65
        self.assertAlmostEqual(r.affected_user_ratio, 0.30)
        self.assertEqual(r.affected_entries, ["app", "web"])
        self.assertEqual(r.critical_path_count, 2)            # 两个入口各一条
        self.assertAlmostEqual(r.health["order"], 0.5)


class TestCorrelatedRedundancy(unittest.TestCase):
    def test_both_replicas_in_same_zone_wipe_out_group(self):
        r = estimate(topo(), ["paydb-1", "paydb-2"])
        self.assertAlmostEqual(r.affected_user_ratio, 0.30)   # 等同 pay 全挂
        self.assertEqual(len(r.broken_groups), 1)
        g = r.broken_groups[0]
        self.assertEqual(g.group_id, "paydb-grp")
        self.assertTrue(g.correlated)                         # 识别"冗余被同时影响"
        self.assertEqual(g.shared_zone, "az-a")               # 指出共同故障域
        self.assertEqual(r.critical_path_count, 4)            # 2入口 x 2副本

    def test_cross_zone_replicas_survive_single_zone_fault(self):
        r = estimate(topo(), ["stock-a"])                     # stock-b 在 az-b 存活
        self.assertEqual(r.broken_groups, [])
        self.assertEqual(r.affected_user_ratio, 0.0)


class TestSoftDependency(unittest.TestCase):
    def test_soft_dep_only_degrades(self):
        r = estimate(topo(), ["feeddb"])
        # app 对 feed 是 soft 0.3，惩罚上限 0.05 -> 用户损失 0.5x0.05
        self.assertAlmostEqual(r.affected_user_ratio, 0.025)
        self.assertNotIn("app", r.down)                       # 降级而非不可用
        self.assertIn("app", r.degraded)


class TestCyclicGraph(unittest.TestCase):
    def test_cycle_converges_and_marks_inexact_paths(self):
        t = Topology.from_dict({"services": {
            "a": {"entry_weight": 1.0, "deps": [{"target": "b", "ratio": 1.0}]},
            "b": {"deps": [{"target": "a", "ratio": 1.0}]},
        }})
        r = estimate(t, ["b"])
        self.assertAlmostEqual(r.health["a"], 0.0)            # 环上迭代收敛
        self.assertAlmostEqual(r.affected_user_ratio, 1.0)
        self.assertFalse(r.critical_path_exact)               # 有环 -> 宏路径计数
        self.assertEqual(r.critical_path_count, 1)


class TestCompare(unittest.TestCase):
    def test_gap_and_attribution(self):
        t = topo()
        predicted = estimate(t, ["paydb-1", "paydb-2"])
        actual = estimate(t, ["paydb-1", "paydb-2", "session"])  # 同AZ连带
        drill = DrillResult(
            affected_services=set(actual.degraded),
            user_ratio=actual.affected_user_ratio,
            entry_health={"web": actual.health["web"], "app": actual.health["app"]},
            critical_path_count=actual.critical_path_count,
            fault_domain="az-a",
        )
        c = compare(t, predicted, drill)
        self.assertAlmostEqual(c.gap_pp, 7.5, places=3)       # 低估 7.5pp
        self.assertLess(c.recall, 1.0)                        # session 漏估
        self.assertAlmostEqual(c.precision, 1.0)
        total_attr = sum(a.share_pp for a in c.attribution)
        self.assertAlmostEqual(total_attr, c.gap_pp, places=3)  # 归因闭合
        self.assertIn("共故障域连带", c.attribution[0].source)

    def test_overestimate_when_fallback_works(self):
        t = topo()
        predicted = estimate(t, ["stock-a", "stock-b"])
        real = Topology.from_dict(TOPO)
        for g in real.services["order"].groups:
            g.kind, g.degraded_penalty = "soft", 0.1          # 实际有降级预案
        actual = estimate(real, ["stock-a", "stock-b"])
        drill = DrillResult(
            affected_services=set(actual.degraded),
            user_ratio=actual.affected_user_ratio,
            entry_health={"web": actual.health["web"], "app": actual.health["app"]},
        )
        c = compare(t, predicted, drill)
        self.assertAlmostEqual(c.gap_pp, -18.0, places=3)     # 高估 18pp
        self.assertIn("切流/降级预案实际生效", c.attribution[0].source)


class TestEntryFault(unittest.TestCase):
    def test_entry_itself_down(self):
        r = estimate(topo(), ["web"])
        self.assertAlmostEqual(r.affected_user_ratio, 0.5)    # web 承载一半用户
        self.assertIn("web", r.down)


class TestValidation(unittest.TestCase):
    def test_ratio_overflow_rejected(self):
        with self.assertRaises(ValueError):
            Topology.from_dict({"services": {
                "a": {"entry_weight": 1.0, "deps": [
                    {"target": "b", "ratio": 0.7},
                    {"target": "c", "ratio": 0.7}]},
                "b": {}, "c": {},
            }})

    def test_unknown_fault_rejected(self):
        with self.assertRaises(ValueError):
            estimate(topo(), ["ghost"])


class TestScaleSmoke(unittest.TestCase):
    def test_estimate_scales(self):
        t = generate_scale_topology(n_services=2000, n_deps=20000, seed=7)
        t0 = time.perf_counter()
        r = estimate(t, ["svc-01000"])
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 2.0)
        self.assertGreaterEqual(r.affected_user_ratio, 0.0)


if __name__ == "__main__":
    unittest.main()

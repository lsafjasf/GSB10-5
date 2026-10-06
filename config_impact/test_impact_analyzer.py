# -*- coding: utf-8 -*-
"""自测：直接引用 / 间接引用 / 同名不同义 / 动态拼接 / 一致率。

运行: python3 -m unittest config_impact.test_impact_analyzer -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_impact.impact_analyzer import ImpactAnalyzer, ConfigKey
from config_impact.demo import build_sample, GROUND_TRUTH


class TestDirectRef(unittest.TestCase):
    def test_direct_consumers_listed(self):
        a = ImpactAnalyzer()
        a.add_config("payment", "retry")
        a.add_static_ref("pay-gateway", "payment", "retry", "a.py:1")
        a.add_static_ref("refund-worker", "payment", "retry", "b.py:2")
        r = a.analyze(ConfigKey("payment", "retry"))
        self.assertEqual({i.consumer for i in r.direct},
                         {"pay-gateway", "refund-worker"})
        self.assertEqual(r.indirect, [])
        self.assertEqual(r.pending, [])

    def test_path_of_direct_is_single_node(self):
        a = ImpactAnalyzer()
        a.add_static_ref("svc", "ns", "k")
        r = a.analyze(ConfigKey("ns", "k"))
        self.assertEqual(r.direct[0].path, ("ns:k",))


class TestIndirectRef(unittest.TestCase):
    def test_two_level_derivation_chain(self):
        a = ImpactAnalyzer()
        a.add_derive_ref("payment", "timeout", "order", "pay_timeout")
        a.add_derive_ref("order", "pay_timeout", "order", "alert")
        a.add_static_ref("checkout", "order", "pay_timeout")
        a.add_static_ref("alerting", "order", "alert")
        r = a.analyze(ConfigKey("payment", "timeout"))
        self.assertEqual({i.consumer for i in r.indirect},
                         {"checkout", "alerting"})

    def test_derivation_path_recorded(self):
        a = ImpactAnalyzer()
        a.add_derive_ref("payment", "timeout", "order", "pay_timeout")
        a.add_derive_ref("order", "pay_timeout", "order", "alert")
        a.add_static_ref("alerting", "order", "alert")
        r = a.analyze(ConfigKey("payment", "timeout"))
        self.assertEqual(
            r.indirect[0].path,
            ("payment:timeout", "order:pay_timeout", "order:alert"))

    def test_cycle_does_not_hang_or_duplicate(self):
        a = ImpactAnalyzer()
        a.add_derive_ref("ns", "a", "ns", "b")
        a.add_derive_ref("ns", "b", "ns", "a")   # 环
        a.add_static_ref("svc", "ns", "b")
        r = a.analyze(ConfigKey("ns", "a"))
        self.assertEqual([i.consumer for i in r.indirect], ["svc"])


class TestSameNameDifferentMeaning(unittest.TestCase):
    def test_same_name_isolated_by_namespace(self):
        a = ImpactAnalyzer()
        a.add_static_ref("pay-gateway", "payment", "timeout")
        a.add_static_ref("search-api", "search", "timeout")
        r_pay = a.analyze(ConfigKey("payment", "timeout"))
        r_search = a.analyze(ConfigKey("search", "timeout"))
        self.assertEqual({i.consumer for i in r_pay.direct}, {"pay-gateway"})
        self.assertEqual({i.consumer for i in r_search.direct}, {"search-api"})
        self.assertFalse(r_pay.affected_consumers()
                         & r_search.affected_consumers())

    def test_dynamic_pattern_does_not_leak_across_namespaces(self):
        a = ImpactAnalyzer()
        # 动态模式只在 infra 命名空间登记，不得命中 payment 的同名键
        a.add_dynamic_ref("job", "infra", "db.*.host", '"db."+r+".host"')
        r = a.analyze(ConfigKey("payment", "db.master.host"))
        self.assertEqual(r.pending, [])


class TestDynamicRef(unittest.TestCase):
    def test_dynamic_hit_goes_to_pending_not_confirmed(self):
        a = ImpactAnalyzer()
        a.add_static_ref("order-db", "infra", "db.master.host")
        a.add_dynamic_ref("multiregion-job", "infra", "db.*.host",
                          '"db." + region + ".host"')
        r = a.analyze(ConfigKey("infra", "db.master.host"))
        # 已确定清单里不得混入动态引用
        self.assertNotIn("multiregion-job", r.affected_consumers())
        # 但必须出现在待人工确认清单，不得遗漏
        self.assertEqual({p.consumer for p in r.pending},
                         {"multiregion-job"})
        self.assertEqual(r.pending[0].expression, '"db." + region + ".host"')

    def test_dynamic_miss_not_listed(self):
        a = ImpactAnalyzer()
        a.add_dynamic_ref("job", "infra", "db.*.host")
        r = a.analyze(ConfigKey("infra", "cache.ttl"))
        self.assertEqual(r.pending, [])

    def test_wildcard_namespace_matches_everywhere(self):
        a = ImpactAnalyzer()
        a.add_dynamic_ref("diag", "*", "*.password", '"*."+user+".password"')
        r = a.analyze(ConfigKey("auth", "admin.password"))
        self.assertEqual({p.consumer for p in r.pending}, {"diag"})


class TestConsistency(unittest.TestCase):
    """与 demo 数据集中人工标注真值对比，一致率必须为 100%。"""

    def test_matches_ground_truth(self):
        a = build_sample()
        for fqn, truth in GROUND_TRUTH.items():
            ns, _, name = fqn.partition(":")
            r = a.analyze(ConfigKey(ns, name))
            got = {
                "direct": {i.consumer for i in r.direct},
                "indirect": {i.consumer for i in r.indirect},
                "pending": {i.consumer for i in r.pending},
            }
            for layer in ("direct", "indirect", "pending"):
                self.assertEqual(got[layer], truth[layer],
                                 f"{fqn} 层 {layer} 与真值不一致")


if __name__ == "__main__":
    unittest.main()

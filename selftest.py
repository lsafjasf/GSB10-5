"""自测: 覆盖 直接引用 / 间接引用 / 同名不同义 / 动态拼接引用 四种情形。

运行: python3 selftest.py [-v]
"""

import unittest

from cfgimpact import ImpactGraph, ImpactAnalyzer


def build_graph():
    g = ImpactGraph()
    # 配置项: 注意 payment.db.timeout 与 search.db.timeout 同名不同义
    g.add_config("payment.db.timeout", 30)
    g.add_config("search.db.timeout", 60)
    g.add_config("payment.db.host", "10.0.0.1")
    g.add_config("payment.db.url", "mysql://${payment.db.host}:3306/pay")
    g.add_config("cache.cn.ttl", 100)
    g.add_config("cache.us.ttl", 100)
    g.add_config("feature.new_ui.enabled", True)
    # 静态引用
    g.add_static("pay-service", "payment.db.timeout")
    g.add_static("pay-service", "payment.db.url")
    g.add_static("search-service", "search.db.timeout")
    # 间接引用: 服务 -> 模块 -> 配置
    g.add_dependency("gateway", "cache-client")
    # 动态拼接: 变量域已知 -> 展开为 cache.cn.ttl / cache.us.ttl
    g.add_dynamic("cache-client", "cache.{region}.ttl", domain={"region": ["cn", "us"]})
    # 动态拼接: 变量域未知 -> 待人工确认
    g.add_dynamic("recommender", "feature.{flag}.enabled")
    g.finalize()
    return g


class TestDirectReference(unittest.TestCase):
    def setUp(self):
        self.analyzer = ImpactAnalyzer(build_graph())

    def test_direct_reference(self):
        rep = self.analyzer.analyze(["payment.db.timeout"])
        self.assertIn("pay-service", {i.node for i in rep.direct})
        self.assertEqual(len(rep.indirect), 0)

    def test_same_name_different_meaning(self):
        # payment.db.timeout 变更不得波及 search.db.timeout 的消费方
        rep = self.analyzer.analyze(["payment.db.timeout"])
        self.assertNotIn("search-service", rep.affected)
        self.assertNotIn("search.db.timeout", rep.affected)
        # 反向同理
        rep2 = self.analyzer.analyze(["search.db.timeout"])
        self.assertIn("search-service", rep2.affected)
        self.assertNotIn("pay-service", rep2.affected)


class TestIndirectReference(unittest.TestCase):
    def setUp(self):
        self.analyzer = ImpactAnalyzer(build_graph())

    def test_config_template_chain(self):
        # payment.db.host -> payment.db.url -> pay-service
        rep = self.analyzer.analyze(["payment.db.host"])
        self.assertIn("payment.db.url", {i.node for i in rep.direct})
        svc = next(i for i in rep.indirect if i.node == "pay-service")
        self.assertEqual(svc.path, ["payment.db.host", "payment.db.url", "pay-service"])

    def test_module_chain(self):
        # cache.cn.ttl -> cache-client -> gateway
        rep = self.analyzer.analyze(["cache.cn.ttl"])
        self.assertIn("cache-client", {i.node for i in rep.direct})
        gw = next(i for i in rep.indirect if i.node == "gateway")
        self.assertEqual(gw.path, ["cache.cn.ttl", "cache-client", "gateway"])


class TestDynamicReference(unittest.TestCase):
    def setUp(self):
        self.analyzer = ImpactAnalyzer(build_graph())

    def test_dynamic_resolved(self):
        # cache.{region}.ttl 变量域已知, 展开后命中 cache.cn.ttl
        rep = self.analyzer.analyze(["cache.cn.ttl"])
        self.assertIn("cache-client", rep.affected)
        rep_us = self.analyzer.analyze(["cache.us.ttl"])
        self.assertIn("cache-client", rep_us.affected)

    def test_dynamic_unresolved_manual_confirm(self):
        # feature.{flag}.enabled 变量域未知: 结构匹配上变更项 -> 待人工确认
        rep = self.analyzer.analyze(["feature.new_ui.enabled"])
        self.assertEqual(len(rep.manual_confirm), 1)
        m = rep.manual_confirm[0]
        self.assertEqual(m.source, "recommender")
        self.assertEqual(m.pattern, "feature.{flag}.enabled")
        # 不得静默丢弃: 库存总数始终上报
        self.assertEqual(rep.unresolved_inventory, 1)

    def test_dynamic_unresolved_not_matching(self):
        # 结构上不匹配的变更不进入待确认列表, 但库存仍在
        rep = self.analyzer.analyze(["payment.db.host"])
        self.assertEqual(len(rep.manual_confirm), 0)
        self.assertEqual(rep.unresolved_inventory, 1)


if __name__ == "__main__":
    unittest.main()

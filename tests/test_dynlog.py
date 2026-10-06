"""动态日志级别调整库自测：python3 -m unittest discover -s tests -v"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dynlog import Manager, LEVELS


class _Base(unittest.TestCase):
    def make(self, nodes=4, **kw):
        m = Manager(rate_limit=1000.0, **kw)
        m.add_source("svc.auth", "INFO")
        latencies = [0.0, 0.02, 0.08, 0.2]
        for i in range(nodes):
            lat = latencies[i] if i < len(latencies) else 0.05 + 0.01 * i
            m.add_node("node-%d" % i, lat)
        return m, latencies[:nodes]


class TestSingleAdjustment(_Base):
    def test_propagates_to_all_nodes_with_version_and_timing(self):
        m, lats = self.make()
        v = m.adjust("svc.auth", "DEBUG", "alice", "排障：认证失败增多")

        # 本地节点立即生效
        self.assertEqual(m.node_level("node-0", "svc.auth"), "DEBUG")

        # 远端节点未送达前仍是旧级别（最终一致，传播期内沿用旧值）
        self.assertEqual(m.node_level("node-1", "svc.auth"), "INFO")

        m.advance(0.21)  # 推进超过最大通道延迟
        report = m.propagation_report(v)
        for row, lat in zip(report["rows"], lats):
            self.assertEqual(row["effective_level"]
                             if "effective_level" in row else
                             m.node_level(row["node"], "svc.auth"), "DEBUG")
            self.assertIsNotNone(row["effective_time"])
            # 生效延迟 == 通道延迟（无队列抖动时上界即 RTT）
            self.assertAlmostEqual(row["effective_delay"], lat, places=6)
            self.assertGreaterEqual(row["effective_time"],
                                    report["publish_time"])
        # 所有节点版本号收敛到同一版本
        ok = m.check_consistency()
        self.assertTrue(ok["ok"], ok["mismatches"])
        self.assertEqual(ok["nodes"], 4)
        self.assertEqual(ok["sources"], 1)

    def test_audit_records_who_when_what(self):
        m, _ = self.make()
        v = m.adjust("svc.auth", "ERROR", "bob", "止血")
        rec = m.audit[-1]
        self.assertEqual(rec.actor, "bob")
        self.assertEqual(rec.source, "svc.auth")
        self.assertEqual((rec.old_level, rec.new_level), ("INFO", "ERROR"))
        self.assertEqual(rec.version, v)
        self.assertEqual(rec.timestamp, 0.0)


class TestFlipFlop(_Base):
    def test_repeated_toggling_leaves_no_stale_node(self):
        m, lats = self.make()
        final = None
        for i in range(50):
            lvl = "ERROR" if i % 2 == 0 else "DEBUG"
            m.adjust("svc.auth", lvl, "ops-bot", "来回调第%d次" % i)
            final = lvl
            m.advance(0.01)  # 每次只推进一点，刻意制造在途旧版本

        # 推进到全部在途变更送达
        m.advance(1.0)
        self.assertTrue(m.all_inflight_delivered())

        rep = m.check_consistency()
        self.assertTrue(rep["ok"], rep["mismatches"][:3])
        for i in range(4):
            self.assertEqual(m.node_level("node-%d" % i, "svc.auth"), final)

        # 审计链路完整：50 条人工调整，old/new 首尾相接
        human = [r for r in m.audit if r.actor == "ops-bot"]
        self.assertEqual(len(human), 50)
        chain = ["INFO"] + [r.new_level for r in human]
        for r, expected_old in zip(human, chain[:-1]):
            self.assertEqual(r.old_level, expected_old)

    def test_stale_node_is_detected_by_consistency_check(self):
        m, _ = self.make()
        m.adjust("svc.auth", "ERROR", "alice")
        m.advance(1.0)
        # 人为制造一个漏达节点
        m._nodes[2].applied["svc.auth"] = (-1, "INFO")
        rep = m.check_consistency()
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["mismatches"][0]["node"], "node-2")


class TestHighFrequencySource(_Base):
    def test_rate_limit_caps_throughput(self):
        m, _ = self.make(nodes=1, auto_converge=False)  # 单测限流本身
        m.adjust("svc.auth", "DEBUG", "alice")
        # 高频源：单窗口突发 10000 条 DEBUG，上限 1000/s
        passed, dropped, filtered = m.emit("svc.auth", "DEBUG", 10000)
        self.assertLessEqual(passed, 1000)          # 突发桶放完即收敛到上限
        self.assertGreater(dropped, 8000)
        self.assertEqual(filtered, 0)

        m.advance(1.0)
        passed2, dropped2, _ = m.emit("svc.auth", "DEBUG", 5000)
        self.assertLessEqual(passed2, 1000)         # 稳态不超 1000/s
        self.assertGreater(dropped2, 3000)

    def test_level_filter_does_not_consume_budget(self):
        m, _ = self.make(nodes=1)
        m.adjust("svc.auth", "ERROR", "alice")
        p, d, f = m.emit("svc.auth", "DEBUG", 10000)
        self.assertEqual((p, d, f), (0, 0, 10000))
        # ERROR 仍然放行
        p, d, f = m.emit("svc.auth", "ERROR", 5)
        self.assertEqual((p, f), (5, 0))


class TestOverLimitAutoConverge(_Base):
    def test_over_limit_auto_converges_and_restores(self):
        # 1000/s 上限；5000/s 高频 DEBUG 必然超限
        m, _ = self.make(nodes=1)
        m.adjust("svc.auth", "DEBUG", "alice", "排障拉满")

        # 窗口 1：超限丢弃 -> 窗口结算时自动抬到 INFO
        m.emit("svc.auth", "DEBUG", 5000)
        m.advance(1.0)
        self.assertEqual(m.effective_level("svc.auth"), "INFO")

        sys_rec = [r for r in m.audit
                   if r.actor == Manager.SYSTEM_ACTOR]
        self.assertTrue(sys_rec)  # 自动收敛必须留痕
        self.assertEqual(sys_rec[-1].old_level, "DEBUG")
        self.assertEqual(sys_rec[-1].new_level, "INFO")

        # DEBUG 全部被级别过滤，磁盘不再被打爆
        p, d, f = m.emit("svc.auth", "DEBUG", 5000)
        self.assertEqual((p, d), (0, 0))
        self.assertEqual(f, 5000)

        # 连续 cooldown 个窗口无丢弃 -> 自动回降到用户设定 DEBUG
        m.advance(1.0)
        m.advance(1.0)
        m.advance(1.0)
        self.assertEqual(m.effective_level("svc.auth"), "DEBUG")
        restore = [r for r in m.audit
                   if r.actor == Manager.RESTORE_ACTOR]
        self.assertTrue(restore)
        # 回降后再次超限仍会收敛（可反复触发）
        m.emit("svc.auth", "DEBUG", 5000)
        m.advance(1.0)
        self.assertEqual(m.effective_level("svc.auth"), "INFO")

    def test_manual_level_rebound_leaves_consistent_state(self):
        """自动收敛叠加 + 人工调回，最终无残留节点。"""
        m, _ = self.make(nodes=4)
        m.adjust("svc.auth", "DEBUG", "alice")
        m.advance(0.25)
        m.emit("svc.auth", "DEBUG", 5000)
        m.advance(1.0)                       # 自动收敛 INFO
        self.assertEqual(m.effective_level("svc.auth"), "INFO")
        m.adjust("svc.auth", "WARNING", "bob", "人工再收紧")
        m.advance(1.0)
        m.adjust("svc.auth", "INFO", "carol", "排障结束调回")
        m.advance(1.0)
        rep = m.check_consistency()
        self.assertTrue(rep["ok"], rep["mismatches"][:3])


if __name__ == "__main__":
    unittest.main(verbosity=2)

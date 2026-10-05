# -*- coding: utf-8 -*-
"""cardinality_guard 自测: 覆盖低基数 / 单高基数标签 / 多标签组合爆炸 /
取值持续增长, 以及"不得误并本该区分维度"的反例验证。

运行: python3 test_cardinality_guard.py
"""
import os
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from cardinality_guard import (  # noqa: E402
    Config, CardinalityGuard,
    KEEP, TRUNCATE, NORMALIZE_PATTERN, NORMALIZE_BUCKET, DROP,
)

METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH"]
STATUSES = ["200", "404", "500"]
REGIONS = ["cn-north", "cn-south", "us-east", "eu-west"]
ENDPOINTS = ["/api/v1/users", "/api/v1/orders", "/api/v1/items",
             "/api/v1/cart", "/api/v1/pay", "/health"]


def low_card_series(n=20_000):
    """场景 1: 全是低基数维度。"""
    out = []
    for i in range(n):
        out.append({
            "method": METHODS[i % len(METHODS)],
            "status": STATUSES[i % len(STATUSES)],
            "region": REGIONS[i % len(REGIONS)],
        })
    return out


class TestLowCardinality(unittest.TestCase):
    def setUp(self):
        self.guard = CardinalityGuard(Config(window=1000))
        self.reports = self.guard.fit(low_card_series())

    def test_all_kept(self):
        for label, report in self.reports.items():
            self.assertEqual(report.rule, KEEP, f"{label} 不应被归并: {report.flags}")
            self.assertFalse(report.flagged())

    def test_reduce_is_identity(self):
        labels = {"method": "GET", "status": "200", "region": "cn-north"}
        self.assertEqual(self.guard.reduce(labels), labels)

    def test_cardinality_unchanged(self):
        series = low_card_series()
        before = {tuple(sorted(s.items())) for s in series}
        after = {tuple(sorted(self.guard.reduce(s).items())) for s in series}
        self.assertEqual(len(before), len(after))
        self.assertEqual(len(before), 5 * 3 * 4)


class TestSingleHighCardinalityLabel(unittest.TestCase):
    """场景 2: 一个订单号标签, 每条序列基本唯一。"""

    N = 20_000

    def setUp(self):
        self.series = []
        for i in range(self.N):
            self.series.append({
                "method": METHODS[i % 5],
                "status": STATUSES[i % 3],
                "order_id": str(900_000_000 + i),   # 单调递增订单号
            })
        self.guard = CardinalityGuard(Config(window=1000))
        self.reports = self.guard.fit(self.series)

    def test_order_id_identified(self):
        r = self.reports["order_id"]
        self.assertIn("HIGH_CARDINALITY", r.flags)
        self.assertIn("UNBOUNDED_GROWTH", r.flags)
        self.assertIn("IDENTIFIER", r.flags)
        self.assertAlmostEqual(r.new_rate, 1.0, places=3)
        self.assertEqual(r.rule, DROP)

    def test_low_card_neighbors_kept(self):
        self.assertEqual(self.reports["method"].rule, KEEP)
        self.assertEqual(self.reports["status"].rule, KEEP)

    def test_cardinality_collapses_to_dimension_product(self):
        before = len({tuple(sorted(s.items())) for s in self.series})
        after = len({tuple(sorted(self.guard.reduce(s).items()))
                     for s in self.series})
        self.assertEqual(before, self.N)
        self.assertEqual(after, 5 * 3)
        self.assertLess(after, before * 0.01)


class TestMultiLabelCombinationExplosion(unittest.TestCase):
    """场景 3: user_id x session_id 两个高基数标签组合爆炸。"""

    N_SESSIONS = 20_000

    def setUp(self):
        self.series = []
        for i in range(self.N_SESSIONS):
            self.series.append({
                "endpoint": ENDPOINTS[i % len(ENDPOINTS)],
                # 每个 user 平均 4 条序列 -> distinct_ratio≈0.25, 有弱分组意义
                "user_id": "u%07d" % (i % 5000),
                "session_id": "sess-%08x-%04x" % (0xA0000000 + i, i & 0xFFFF),
            })
        self.guard = CardinalityGuard(Config(window=1000))
        self.reports = self.guard.fit(self.series)

    def test_both_high_card_labels_detected(self):
        self.assertIn("IDENTIFIER", self.reports["session_id"].flags)
        self.assertEqual(self.reports["session_id"].rule, DROP)
        user_rule = self.reports["user_id"].rule
        self.assertIn(user_rule, (TRUNCATE, NORMALIZE_BUCKET))
        self.assertEqual(self.reports["endpoint"].rule, KEEP)

    def test_combination_explosion_contained(self):
        before = len({tuple(sorted(s.items())) for s in self.series})
        after_keys = [tuple(sorted(self.guard.reduce(s).items()))
                      for s in self.series]
        after = len(set(after_keys))
        self.assertEqual(before, self.N_SESSIONS)
        # 归并后基数受桶数/前缀数约束, 而不是随 session 数线性爆炸
        self.assertLessEqual(after, len(ENDPOINTS) * max(
            self.guard.cfg.hash_buckets, 10 ** 4))
        self.assertLess(after, before * 0.05)


class TestSustainedGrowth(unittest.TestCase):
    """场景 4: 取值持续增长 —— 在绝对取值数尚未爆表时, 靠增长速率提前识别。"""

    def test_growth_rate_triggers_before_volume_does(self):
        # max_values 故意设很大: 取值数量不超标, 只能靠增长速率发现问题
        cfg = Config(max_values=10_000_000, max_new_rate=0.30, window=500)
        guard = CardinalityGuard(cfg)
        for i in range(3000):
            guard.observe({"region": REGIONS[i % 4], "trace_id": f"trace-{i:08d}"})
        reports = guard.finalize()
        self.assertNotIn("HIGH_CARDINALITY", reports["trace_id"].flags)
        self.assertIn("UNBOUNDED_GROWTH", reports["trace_id"].flags)
        self.assertIn("IDENTIFIER", reports["trace_id"].flags)
        self.assertEqual(reports["trace_id"].rule, DROP)
        # 对照: 稳定维度增长速率为 0
        self.assertEqual(reports["region"].new_rate, 0.0)
        self.assertEqual(reports["region"].rule, KEEP)

    def test_growth_stops_then_not_flagged(self):
        """取值池封闭(先增长后稳定)时, 窗口内新值率回落到 0, 不误判无界增长。"""
        cfg = Config(max_values=100, max_new_rate=0.30, window=200)
        guard = CardinalityGuard(cfg)
        for i in range(1000):
            guard.observe({"canary_id": "c%03d" % (i % 50)})
        r = guard.finalize()["canary_id"]
        self.assertNotIn("UNBOUNDED_GROWTH", r.flags)


class TestPatternAndBucketRules(unittest.TestCase):
    """三种归并规则各自的行为。"""

    def test_normalize_pattern_preserves_structure(self):
        import random
        rng = random.Random(42)
        pods = []
        for i in range(3000):
            suffix = "".join(rng.choice("0123456789abcdefghijklmnopqrstuvwxyz")
                             for _ in range(5))
            pods.append(f"web-{i:08x}f-{suffix}")
        # 每个 pod 出现 3 次 -> 不是纯标识符, 但 3000 取值仍属高基数
        rows = [{"pod": pods[i % len(pods)], "app": "web"} for i in range(9000)]
        guard = CardinalityGuard(Config(window=500))
        reports = guard.fit(rows)
        self.assertEqual(reports["pod"].rule, NORMALIZE_PATTERN)
        reduced = guard.reduce(rows[0])
        self.assertEqual(reduced["pod"], "web-:hex:-:token:")
        self.assertEqual(reduced["app"], "web")

    def test_bucket_is_stable_and_bounded(self):
        from cardinality_guard import normalize_bucket
        self.assertEqual(normalize_bucket("u0000001", 16),
                         normalize_bucket("u0000001", 16))
        values = {normalize_bucket(f"u{i:07d}", 16) for i in range(10_000)}
        self.assertLessEqual(len(values), 16)

    def test_truncate_marks_only_long_values(self):
        from cardinality_guard import truncate
        self.assertEqual(truncate("GET", 6), "GET")
        self.assertEqual(truncate("u0001234", 6), "u00012*")


class TestCounterexamples(unittest.TestCase):
    """反例验证: 归并不得把本该区分的维度合并。"""

    def setUp(self):
        cfg = Config(window=1000)
        self.guard = CardinalityGuard(cfg)
        rows = []
        for i in range(20_000):
            rows.append({
                # status 是纯数字! 模式归一化若误作用于它, 200/404/500 会全部
                # 被并成 "#" —— 这是最容易出事的反例。
                "status": STATUSES[i % 3],
                "region": REGIONS[i % 4],
                "method": METHODS[i % 5],
                "endpoint": ENDPOINTS[i % len(ENDPOINTS)],
                "order_id": str(900_000_000 + i),
            })
        self.rows = rows
        self.guard.fit(rows)

    def test_numeric_status_not_pattern_normalized(self):
        for status in STATUSES:
            reduced = self.guard.reduce({"status": status, "order_id": "1"})
            self.assertEqual(reduced["status"], status,
                             "低基数数字状态码被错误归一化")

    def test_status_groups_remain_distinguishable(self):
        """5xx 告警查询: reduce 后仍能按 status 区分出 200/404/500。"""
        groups = Counter()
        for row in self.rows:
            r = self.guard.reduce(row)
            groups[(r["method"], r["status"], r["region"])] += 1
        self.assertEqual(len(groups), 5 * 3 * 4)
        # 每个组合计数都在, 没被合并污染
        self.assertTrue(all(v > 0 for v in groups.values()))

    def test_regions_not_truncated_together(self):
        keys = {self.guard.reduce({"region": r})["region"] for r in REGIONS}
        self.assertEqual(keys, set(REGIONS))

    def test_series_differing_only_in_order_id_merge_intentionally(self):
        a = {"method": "GET", "status": "200", "order_id": "900000001"}
        b = {"method": "GET", "status": "200", "order_id": "900000002"}
        self.assertEqual(self.guard.reduce(a), self.guard.reduce(b))

    def test_series_differing_in_method_must_not_merge(self):
        a = {"method": "GET", "status": "200", "order_id": "900000001"}
        b = {"method": "POST", "status": "200", "order_id": "900000002"}
        self.assertNotEqual(self.guard.reduce(a), self.guard.reduce(b))

    def test_aggregate_preserved_after_drop(self):
        """聚合正确性: 丢弃 order_id 后, 按(method,status)求和与归并前一致。"""
        before = Counter()
        after = Counter()
        for i, row in enumerate(self.rows):
            value = 1 + (i % 7)  # 每条序列带一个样本值
            before[(row["method"], row["status"])] += value
            r = self.guard.reduce(row)
            after[(r["method"], r["status"])] += value
        self.assertEqual(before, after)

    def test_unseen_label_is_kept_by_default(self):
        """fit 时没出现过的新标签默认 KEEP, 不会被贸然丢维度。"""
        reduced = self.guard.reduce({"brand_new_dim": "x", "order_id": "1"})
        self.assertEqual(reduced["brand_new_dim"], "x")
        self.assertNotIn("order_id", reduced)


if __name__ == "__main__":
    unittest.main(verbosity=2)

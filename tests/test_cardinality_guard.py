"""Self-tests for cardinality_guard.  Run: python3 -m unittest discover -s tests -v"""

import unittest

from cardinality_guard import (
    HyperLogLog, SpaceSaving, LabelSketch,
    CardinalityDetector, Thresholds,
    template_of, normalize_path, extract_channel,
    Policy, PolicyError, Rule, merge_labels, auto_plan,
    check_must_distinguish, validate_policy, measure_merged, OTHER,
)
from cardinality_guard.data import generate


class TestSketches(unittest.TestCase):
    def test_hll_accuracy(self):
        hll = HyperLogLog(12)
        n = 100_000
        for i in range(n):
            hll.add(f"value-{i}")
        est = hll.estimate()
        self.assertAlmostEqual(est, n, delta=n * 0.05)

    def test_hll_merge(self):
        a, b = HyperLogLog(10), HyperLogLog(10)
        for i in range(5000):
            a.add(f"x{i}")
        for i in range(2500, 7500):
            b.add(f"x{i}")
        a.merge(b)
        self.assertAlmostEqual(a.estimate(), 7500, delta=7500 * 0.08)

    def test_space_saving_finds_heavy_hitters(self):
        ss = SpaceSaving(10)
        for i in range(10_000):
            ss.add("hot")
            if i % 2:
                ss.add("warm")
            ss.add(f"cold-{i}")
        top = dict(ss.top(2))
        self.assertIn("hot", top)
        self.assertIn("warm", top)
        self.assertGreaterEqual(top["hot"], 10_000)

    def test_label_sketch_promotion_bounds_memory(self):
        sk = LabelSketch("order_id", exact_limit=100)
        for i in range(5000):
            sk.add(f"ORD-{i}", window=i % 4)
        self.assertFalse(sk.is_exact)
        self.assertIsNone(sk.exact)
        self.assertAlmostEqual(sk.distinct(), 5000, delta=5000 * 0.05)
        stats = sk.window_stats({0: 1250, 1: 1250, 2: 1250, 3: 1250})
        self.assertEqual(len(stats), 4)


class TestNormalize(unittest.TestCase):
    def test_templates(self):
        self.assertEqual(template_of("f47ac10b-58cc-4372-a567-0e02b2c3d479"), "<uuid>")
        self.assertEqual(template_of("ORD00001234"), "ORD<digits>")
        self.assertEqual(template_of("10.0.0.7"), "<ip>")
        self.assertIsNone(template_of("200"))
        self.assertIsNone(template_of("GET"))

    def test_channel_extraction(self):
        self.assertEqual(extract_channel("ORD-EU-00001234"), "ORD-EU")
        self.assertEqual(extract_channel("ORD-00001234"), "ORD")
        self.assertIsNone(extract_channel("prod"))

    def test_path_normalization(self):
        self.assertEqual(normalize_path("/api/users/123"), "/api/users/{id}")
        self.assertEqual(
            normalize_path("/api/orders/ORD-991/items"),
            "/api/orders/{id}/items")
        self.assertIsNone(normalize_path("/api/health"))

    def test_static_values_untouched(self):
        # 反例保障：有语义的静态取值不会被归一化合并
        for v in ("200", "500", "404", "GET", "POST", "prod", "stage"):
            self.assertIsNone(template_of(v))
            self.assertIsNone(normalize_path(v))


class TestDetector(unittest.TestCase):
    def _detect(self, scenario, n=20_000, **kw):
        det = CardinalityDetector()
        for window, labels in generate(n, scenario=scenario, **kw):
            det.feed(labels, window)
        return det.report()

    def test_low_cardinality_scenario(self):
        rep = self._detect("low", n=10_000)
        self.assertEqual(rep.high_cardinality_labels, [])
        self.assertLessEqual(rep.labels["status"].distinct, 3)

    def test_single_high_cardinality_label(self):
        rep = self._detect("single", n=20_000)
        self.assertIn("order_id", rep.high_cardinality_labels)
        info = rep.labels["order_id"]
        self.assertGreaterEqual(info.distinct, 1000)          # 取值数量
        self.assertGreater(info.growth_rate, 0.9)             # 增长速率
        self.assertGreater(info.selectivity, 0.9)             # 区分度
        self.assertTrue(info.is_sustained_growth)
        self.assertNotIn("service", rep.high_cardinality_labels)
        self.assertNotIn("status", rep.high_cardinality_labels)

    def test_growth_distinguishes_bounded_from_unbounded(self):
        rep = self._detect("growth", n=16_000, n_windows=8)
        self.assertGreater(rep.labels["order_id"].growth_rate, 0.9)
        self.assertLess(rep.labels["status"].growth_rate, 0.05)
        self.assertLess(rep.labels["region"].growth_rate, 0.05)

    def test_combination_explosion_detected(self):
        rep = self._detect("combo", n=20_000)
        pairs = {tuple(p["pair"]) for p in rep.exploding_pairs}
        self.assertIn(("order_id", "trace_id"), pairs)
        self.assertGreater(rep.combination_product, 10_000)


class TestMergeRules(unittest.TestCase):
    def test_truncate(self):
        pol = Policy(rules={"pod": Rule("truncate", k=2, pinned=("pod-99999",))})
        top = {"pod": {"pod-00001", "pod-00002"}}
        self.assertEqual(merge_labels({"pod": "pod-00001"}, pol, top)["pod"], "pod-00001")
        self.assertEqual(merge_labels({"pod": "pod-12345"}, pol, top)["pod"], OTHER)
        self.assertEqual(merge_labels({"pod": "pod-99999"}, pol, top)["pod"], "pod-99999")

    def test_normalize(self):
        pol = Policy(rules={
            "trace_id": Rule("normalize"),
            "order_id": Rule("normalize", extract="channel"),
            "path": Rule("normalize"),
        })
        out = merge_labels({
            "trace_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
            "order_id": "ORD-EU-00001234",
            "path": "/api/users/123",
        }, pol)
        self.assertEqual(out["trace_id"], "<uuid>")
        self.assertEqual(out["order_id"], "ORD-EU")
        self.assertEqual(out["path"], "/api/users/{id}")

    def test_drop(self):
        pol = Policy(rules={"trace_id": Rule("drop", force=True)})
        out = merge_labels({"trace_id": "abc", "status": "200"}, pol)
        self.assertNotIn("trace_id", out)
        self.assertEqual(out["status"], "200")


class TestCounterExamples(unittest.TestCase):
    """归并不得把本该区分的维度合并 —— 反例验证。"""

    def test_drop_would_merge_distinguishable_series(self):
        a = {"service": "svc", "request_id": "r1"}
        b = {"service": "svc", "request_id": "r2"}
        pol = Policy(
            rules={"request_id": Rule("drop", force=True)},
            must_distinguish=((a, b),))
        with self.assertRaises(PolicyError):
            validate_policy(pol)

    def test_truncate_would_merge_distinguishable_series(self):
        a = {"tenant": "t-small-1"}
        b = {"tenant": "t-small-2"}
        pol = Policy(
            rules={"tenant": Rule("truncate", k=1)},
            must_distinguish=((a, b),))
        with self.assertRaises(PolicyError):
            validate_policy(pol, top_values={"tenant": {"t-hot"}})

    def test_protected_label_rejects_rules(self):
        pol = Policy(
            rules={"tenant": Rule("truncate", k=4)},
            protected_labels=frozenset({"tenant"}))
        with self.assertRaises(PolicyError):
            validate_policy(pol)

    def test_drop_refused_for_low_selectivity_dimension(self):
        det = CardinalityDetector()
        for window, labels in generate(2000, scenario="low"):
            det.feed(labels, window)
        rep = det.report()
        pol = Policy(rules={"status": Rule("drop")})
        with self.assertRaises(PolicyError):
            validate_policy(pol, report=rep)

    def test_normalize_keeps_static_dimensions_distinct(self):
        pol = Policy(rules={"status": Rule("normalize")})
        a = merge_labels({"status": "200"}, pol)
        b = merge_labels({"status": "500"}, pol)
        self.assertNotEqual(a, b)  # 静态取值穿透，不被模板合并

    def test_must_distinguish_passes_when_difference_survives(self):
        a = {"service": "svc", "status": "200", "trace_id": "x" * 32}
        b = {"service": "svc", "status": "500", "trace_id": "y" * 32}
        pol = Policy(
            rules={"trace_id": Rule("drop", force=True)},
            must_distinguish=((a, b),))
        validate_policy(pol)  # 不抛异常：status 差异仍然保留
        self.assertEqual(check_must_distinguish(pol), [])


class TestEndToEnd(unittest.TestCase):
    def test_auto_plan_and_merge_reduces_cardinality(self):
        n = 20_000
        det = CardinalityDetector()
        for window, labels in generate(n, scenario="combo"):
            det.feed(labels, window)
        rep = det.report()
        plan = auto_plan(rep, sketches=det.sketches)
        self.assertTrue(plan)
        rules = {e.label: e.rule for e in plan}
        pol = Policy(rules=rules)
        validate_policy(pol, report=rep)
        top_values = {
            name: {v for v, _ in det.sketches[name].top_values(rules[name].k)}
            for name, rules_ in rules.items() if rules_.action == "truncate"
        }
        merged_n, per_label = measure_merged(
            (labels for _, labels in generate(n, scenario="combo")), pol, top_values)
        self.assertLess(merged_n, n * 0.5)
        self.assertNotIn("trace_id", per_label)


if __name__ == "__main__":
    unittest.main()

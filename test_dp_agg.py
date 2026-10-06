"""dp_agg 自测：单轮、多轮预算内、预算耗尽、重复/相似切片、敏感度与噪声。"""

import math
import unittest

from dp_agg import (
    BudgetExhausted,
    DPAnalytics,
    PrivacyAccountant,
    laplace_noise,
)
import random


def make_records():
    regions = ["华东", "华北", "华南"]
    depts = ["销售", "研发"]
    records = []
    for i in range(300):
        records.append({
            "region": regions[i % 3],
            "dept": depts[i % 2],
            "spend": float(i % 50),
        })
    return records


class TestSingleRound(unittest.TestCase):
    """单轮查询：预算内正常回答，账本记录敏感度与噪声尺度。"""

    def test_single_count_query(self):
        dp = DPAnalytics(make_records(), epsilon_total=1.0, seed=7)
        result = dp.query({"region": "华东"}, agg="count", epsilon=0.5)
        self.assertIsInstance(result, float)
        self.assertEqual(len(dp.ledger), 1)
        entry = dp.ledger[0]
        self.assertAlmostEqual(entry.epsilon_spent, 0.5)
        self.assertAlmostEqual(entry.epsilon_remaining, 0.5)
        self.assertEqual(entry.sensitivity, 1.0)          # count 敏感度 = 1
        self.assertAlmostEqual(entry.noise_scale, 1.0 / 0.5)  # b = Δf/ε
        self.assertFalse(entry.reused)

    def test_sum_sensitivity_uses_bound(self):
        dp = DPAnalytics(make_records(), epsilon_total=1.0,
                         contribution_bounds={"spend": 100.0}, seed=7)
        dp.query({"dept": "销售"}, agg="sum", field="spend", epsilon=0.25)
        entry = dp.ledger[0]
        self.assertEqual(entry.sensitivity, 100.0)        # Δ(sum)=截断上界
        self.assertAlmostEqual(entry.noise_scale, 100.0 / 0.25)

    def test_avg_splits_epsilon(self):
        dp = DPAnalytics(make_records(), epsilon_total=1.0,
                         contribution_bounds={"spend": 100.0}, seed=7)
        dp.query({"dept": "研发"}, agg="avg", field="spend", epsilon=0.4)
        entry = dp.ledger[0]
        self.assertEqual(entry.sensitivity, (100.0, 1.0))
        self.assertAlmostEqual(entry.noise_scale[0], 100.0 / 0.2)
        self.assertAlmostEqual(entry.noise_scale[1], 1.0 / 0.2)


class TestMultiRoundBudget(unittest.TestCase):
    """多轮共享同一份预算：累计消耗按基本组合定理相加。"""

    def test_shared_budget_across_rounds(self):
        dp = DPAnalytics(make_records(), epsilon_total=1.0, seed=1)
        slices = [{"region": r} for r in ("华东", "华北", "华南")]
        for s in slices:
            dp.query(s, agg="count", epsilon=0.3)
        self.assertAlmostEqual(dp.accountant.spent, 0.9)
        self.assertAlmostEqual(dp.accountant.remaining, 0.1)
        spends = [e.epsilon_spent for e in dp.ledger]
        self.assertEqual(spends, [0.3, 0.3, 0.3])

    def test_budget_exhausted_refuses(self):
        dp = DPAnalytics(make_records(), epsilon_total=0.3, seed=1)
        dp.query({"region": "华东"}, agg="count", epsilon=0.2)
        dp.query({"region": "华北"}, agg="count", epsilon=0.1)
        with self.assertRaises(BudgetExhausted):
            dp.query({"region": "华南"}, agg="count", epsilon=0.1)
        # 拒绝为零消耗：账本只记录成功与复用的轮次，预算不变
        self.assertAlmostEqual(dp.accountant.spent, 0.3)
        self.assertEqual(len(dp.ledger), 2)

    def test_exact_fit_last_round(self):
        dp = DPAnalytics(make_records(), epsilon_total=0.3, seed=1)
        dp.query({"region": "华东"}, agg="count", epsilon=0.3)
        with self.assertRaises(BudgetExhausted):
            dp.query({"region": "华北"}, agg="count", epsilon=1e-9)


class TestRepeatAndSimilarSlices(unittest.TestCase):
    """重复查询同一切片零消耗；相似切片被识别但仍消耗预算。"""

    def test_exact_repeat_is_free(self):
        dp = DPAnalytics(make_records(), epsilon_total=0.2, seed=3)
        first = dp.query({"region": "华东"}, agg="count", epsilon=0.2)
        # 预算已用尽，但完全重复查询仍应免费返回同一结果
        second = dp.query({"region": "华东"}, agg="count", epsilon=0.2)
        third = dp.query({"region": "华东"}, agg="count", epsilon=0.2)
        self.assertEqual(first, second)
        self.assertEqual(second, third)
        self.assertAlmostEqual(dp.accountant.spent, 0.2)
        self.assertTrue(dp.ledger[1].reused)
        self.assertTrue(dp.ledger[2].reused)
        self.assertEqual(dp.ledger[1].epsilon_spent, 0.0)

    def test_filter_order_does_not_matter(self):
        dp = DPAnalytics(make_records(), epsilon_total=1.0, seed=3)
        a = dp.query({"region": "华东", "dept": "销售"}, agg="count")
        b = dp.query({"dept": "销售", "region": "华东"}, agg="count")
        self.assertEqual(a, b)
        self.assertTrue(dp.ledger[1].reused)

    def test_similar_slice_detected_but_charged(self):
        dp = DPAnalytics(make_records(), epsilon_total=1.0,
                         similarity_threshold=0.75, seed=3)
        dp.query({"region": "华东", "dept": "销售", "channel": "线上"},
                 agg="count", epsilon=0.1)
        # Jaccard = 3/4 = 0.75 ≥ 阈值 → 识别为相似，但仍扣预算
        dp.query({"region": "华东", "dept": "销售", "channel": "线上",
                  "segment": "新客"}, agg="count", epsilon=0.1)
        entry = dp.ledger[1]
        self.assertIsNotNone(entry.similar_to)
        self.assertAlmostEqual(entry.epsilon_spent, 0.1)
        self.assertFalse(entry.reused)
        self.assertAlmostEqual(dp.accountant.spent, 0.2)

    def test_dissimilar_slice_not_flagged(self):
        dp = DPAnalytics(make_records(), epsilon_total=1.0, seed=3)
        dp.query({"region": "华东"}, agg="count", epsilon=0.1)
        dp.query({"region": "华南", "dept": "研发"}, agg="count", epsilon=0.1)
        self.assertIsNone(dp.ledger[1].similar_to)


class TestNoiseStatistics(unittest.TestCase):
    """噪声与敏感度挂钩：b = Δf/ε；大样本下均值贴近真值、尺度正确。"""

    def test_laplace_scale_matches(self):
        rng = random.Random(42)
        scale = 10.0
        samples = [laplace_noise(scale, rng) for _ in range(200_000)]
        mean_abs = sum(abs(s) for s in samples) / len(samples)
        # Laplace(0,b) 的 E|X| = b
        self.assertAlmostEqual(mean_abs, scale, delta=scale * 0.02)

    def test_noisy_mean_converges_to_truth(self):
        dp = DPAnalytics(make_records(), epsilon_total=1e9, seed=11)
        truth = dp.true_aggregate({"region": "华东"}, agg="count")
        estimates = []
        for i in range(4000):
            fresh = DPAnalytics(make_records(), epsilon_total=1e9, seed=1000 + i)
            estimates.append(fresh.query({"region": "华东"}, agg="count",
                                         epsilon=1.0))
        mean_est = sum(estimates) / len(estimates)
        self.assertAlmostEqual(mean_est, truth, delta=0.15)

    def test_larger_epsilon_less_noise(self):
        # ε 越大噪声尺度越小
        dp = DPAnalytics(make_records(), epsilon_total=10.0, seed=5)
        dp.query({"region": "华东"}, agg="count", epsilon=0.1)
        dp.query({"region": "华北"}, agg="count", epsilon=1.0)
        self.assertGreater(dp.ledger[0].noise_scale, dp.ledger[1].noise_scale)


class TestAccountant(unittest.TestCase):
    def test_invalid_epsilon_rejected(self):
        acc = PrivacyAccountant(1.0)
        with self.assertRaises(ValueError):
            acc.spend(0.0)
        with self.assertRaises(ValueError):
            PrivacyAccountant(0.0)

    def test_overspend_raises_and_keeps_state(self):
        acc = PrivacyAccountant(0.5)
        acc.spend(0.4)
        with self.assertRaises(BudgetExhausted):
            acc.spend(0.2)
        self.assertAlmostEqual(acc.spent, 0.4)


if __name__ == "__main__":
    unittest.main(verbosity=2)

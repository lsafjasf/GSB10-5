"""变更风险评分库自测（标准库 unittest）。

覆盖：
- 单因素极高：一项贡献占绝对主导
- 多项小风险叠加：落入中风险，无单一主导项
- 全部因素很低：低风险，占比有定义
- 临界区间：阈值边界两侧各有样本，且被正确标记
- 权重归一化、权重缺失校验
- 相关性指标：AUC 与点二列相关在合成真值数据上达标
- 敏感度：±20% 权重扰动下结论稳健
"""
import math
import unittest

from risk_scoring.dataset import generate_labeled
from risk_scoring.model import (
    DEFAULT_WEIGHTS,
    FACTOR_NAMES,
    THRESHOLD_HIGH,
    THRESHOLD_MEDIUM,
    Change,
    factor_values,
    score_change,
    score_many,
)
from risk_scoring.sensitivity import global_jitter, spearman_rho
from risk_scoring.validation import auc, point_biserial, validate

SAFE = Change(
    churn_lines=5, files_touched=1, modules_touched=1,
    criticality=0.05, test_coverage=0.98, author_commits=5000,
    ownership_ratio=0.99, hour_local=10,
)
STACKED = Change(
    churn_lines=250, files_touched=8, modules_touched=3,
    criticality=0.45, test_coverage=0.55, author_commits=40,
    ownership_ratio=0.4, hour_local=20,
)


class TestFactorNormalization(unittest.TestCase):
    def test_weights_sum_to_one_and_match_factors(self):
        self.assertAlmostEqual(sum(DEFAULT_WEIGHTS.values()), 1.0)
        self.assertEqual(set(DEFAULT_WEIGHTS), set(FACTOR_NAMES))

    def test_factor_values_in_unit_interval(self):
        for value in factor_values(STACKED).values():
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_monotonicity(self):
        base = factor_values(SAFE)
        big = factor_values(Change(churn_lines=5000))
        self.assertGreater(big["size"], base["size"])
        untested = factor_values(Change(test_coverage=0.0))
        self.assertGreater(untested["test_gap"], base["test_gap"])
        night = factor_values(Change(hour_local=23))
        self.assertGreater(night["off_hours"], base["off_hours"])
        novice = factor_values(Change(author_commits=1))
        self.assertGreater(novice["inexperience"], base["inexperience"])

    def test_off_hours_rules(self):
        self.assertEqual(factor_values(Change(hour_local=23))["off_hours"], 1.0)
        self.assertEqual(factor_values(Change(hour_local=14, is_weekend=False))["off_hours"], 0.0)
        self.assertAlmostEqual(
            factor_values(Change(hour_local=14, is_weekend=True))["off_hours"], 0.4
        )
        # 周末 + 深夜仍然截断在 1.0
        self.assertEqual(
            factor_values(Change(hour_local=23, is_weekend=True))["off_hours"], 1.0
        )


class TestScoreBreakdown(unittest.TestCase):
    def test_score_matches_sum_of_contributions(self):
        for change in (SAFE, STACKED):
            result = score_change(change)
            self.assertAlmostEqual(
                result.score, sum(c.points for c in result.contributions), places=9
            )
            self.assertGreaterEqual(result.score, 0.0)
            self.assertLessEqual(result.score, 100.0)

    def test_shares_sum_to_one_when_score_positive(self):
        result = score_change(STACKED)
        shares = result.shares()
        self.assertAlmostEqual(sum(shares.values()), 1.0, places=9)

    def test_shares_defined_when_score_zero(self):
        # 所有因素都取最低可能值：深夜=0 需要白天；扩散文件1仍有残余，
        # 但 shares 在 score=0 时返回全 0 而不抛异常。
        minimal = Change(churn_lines=0, files_touched=0, modules_touched=0,
                         criticality=0.0, test_coverage=1.0, author_commits=10**9,
                         ownership_ratio=1.0, hour_local=14)
        result = score_change(minimal)
        self.assertEqual(result.score, 0.0)
        self.assertEqual(sum(result.shares().values()), 0.0)

    def test_single_extreme_factor_dominates(self):
        change = Change(churn_lines=20, files_touched=1, modules_touched=1,
                        criticality=0.98, test_coverage=0.95, author_commits=2000,
                        ownership_ratio=0.95, hour_local=11)
        result = score_change(change)
        self.assertLess(result.score, THRESHOLD_MEDIUM)  # 单项极高不该一票升到高危
        top = max(result.contributions, key=lambda c: c.points)
        self.assertEqual(top.factor, "criticality")
        self.assertGreater(result.shares()["criticality"], 0.6)  # 主导项占比 >60%

    def test_many_small_risks_stack(self):
        result = score_change(STACKED)
        self.assertEqual(result.band, "medium")
        shares = result.shares()
        top_share = max(shares.values())
        self.assertLess(top_share, 0.25)  # 没有单项主导（<25%）
        nonzero = sum(1 for c in result.contributions if c.points > 1.0)
        self.assertGreaterEqual(nonzero, 6)  # 至少 6 项有实质贡献

    def test_all_low_factors(self):
        result = score_change(SAFE)
        self.assertEqual(result.band, "low")
        self.assertLess(result.score, 10.0)

    def test_band_thresholds_inclusive_and_boundary_flag(self):
        # 单因素拉满也不应到达 medium：最大单因素贡献 = 最大权重 0.18*100 = 18 分
        single_max = [
            Change(churn_lines=10**6),
            Change(files_touched=10**4, modules_touched=100),
            Change(criticality=1.0),
            Change(test_coverage=0.0),
            Change(author_commits=0),
            Change(ownership_ratio=0.0),
            Change(hour_local=23, is_weekend=True),
        ]
        for change in single_max:
            self.assertEqual(score_change(change).band, "low")
        # 所有因素全满 -> 高分
        worst = Change(churn_lines=10**6, files_touched=1000, modules_touched=100,
                       criticality=1.0, test_coverage=0.0, author_commits=0,
                       ownership_ratio=0.0, hour_local=23, is_weekend=True)
        self.assertEqual(score_change(worst).band, "high")
        self.assertGreater(score_change(worst).score, THRESHOLD_HIGH)
        self.assertFalse(score_change(worst).is_boundary)

    def test_boundary_flag_near_medium_threshold(self):
        # 用临界示例（analyze.py 中的"临界区间"用例）
        change = Change(churn_lines=120, files_touched=4, modules_touched=2,
                        criticality=0.30, test_coverage=0.65, author_commits=80,
                        ownership_ratio=0.55, hour_local=15)
        result = score_change(change)
        self.assertAlmostEqual(result.score, 30.0, delta=5.0)
        self.assertTrue(result.is_boundary)

    def test_weights_are_normalized_even_if_input_unnormalized(self):
        doubled = {k: v * 2 for k, v in DEFAULT_WEIGHTS.items()}
        self.assertAlmostEqual(
            score_change(STACKED, doubled).score, score_change(STACKED).score
        )

    def test_missing_weight_rejected(self):
        bad = dict(DEFAULT_WEIGHTS)
        del bad["size"]
        with self.assertRaises(ValueError):
            score_change(STACKED, bad)

    def test_zero_total_weight_rejected(self):
        with self.assertRaises(ValueError):
            score_change(STACKED, {k: 0.0 for k in FACTOR_NAMES})


class TestValidationMetrics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.changes, cls.outcomes = generate_labeled(20000, seed=2024)
        cls.scores = [b.score for b in score_many(cls.changes)]

    def test_point_biserial_on_perfect_ordering(self):
        scores = [1, 2, 3, 7, 8, 9]
        outcomes = [0, 0, 0, 1, 1, 1]
        self.assertGreater(point_biserial(scores, outcomes), 0.9)

    def test_auc_known_values(self):
        self.assertEqual(auc([1, 2, 3, 4], [0, 0, 1, 1]), 1.0)
        self.assertEqual(auc([4, 3, 2, 1], [0, 0, 1, 1]), 0.0)
        self.assertAlmostEqual(
            auc([1, 1, 1, 2], [0, 0, 1, 1]), 0.75, places=9
        )

    def test_scores_correlate_with_rollback(self):
        r_pb = point_biserial(self.scores, self.outcomes)
        roc = auc(self.scores, self.outcomes)
        self.assertGreater(r_pb, 0.3)
        self.assertGreater(roc, 0.80)

    def test_band_failure_rates_monotone(self):
        report = validate(self.scores, self.outcomes)
        rates = [row["failure_rate"] for row in report["band_failure_rates"]]
        self.assertLess(rates[0], rates[1])
        self.assertLess(rates[1], rates[2])
        self.assertGreater(rates[2] / max(rates[0], 1e-9), 4.0)


class TestSensitivity(unittest.TestCase):
    def test_spearman_perfect_and_reversed(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertAlmostEqual(spearman_rho(a, a), 1.0)
        self.assertAlmostEqual(spearman_rho(a, a[::-1]), -1.0)

    def test_conclusions_robust_to_20pct_weight_jitter(self):
        changes, _ = generate_labeled(5000, seed=2025)
        report = global_jitter(changes, delta=0.2, trials=50, seed=42)
        self.assertGreaterEqual(report["spearman_min"], 0.90)
        self.assertGreaterEqual(report["agreement_mean"], 0.75)


if __name__ == "__main__":
    unittest.main(verbosity=2)

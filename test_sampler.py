"""自测（unittest，仅标准库）：
覆盖均匀采样、按耗时采样、样本量很小、极端长尾四种情形，
并验证加权还原的无偏性与置信区间覆盖率。

运行: python3 -m unittest -v test_sampler
"""
import math
import random
import unittest
from random import Random

from sampler import (DurationSampler, UniformSampler, WeightedSample,
                     MIN_ESS)


def skewed_population(n, seed=0):
    rng = Random(seed)
    pop = []
    for _ in range(n):
        u = rng.random()
        if u < 0.989:
            pop.append(rng.lognormvariate(1.6, 0.9))
        elif u < 0.999:
            pop.append(rng.lognormvariate(4.0, 0.8))
        else:
            pop.append(300.0 * rng.paretovariate(1.5))
    return pop


def collect(pop, sampler):
    s = WeightedSample()
    for v in pop:
        sampler.maybe_add(v, s)
    return s


def nearest_rank_quantile(pop, q):
    x = sorted(pop)
    return x[min(len(x) - 1, max(0, math.ceil(q * len(x)) - 1))]


class TestUnbiasedness(unittest.TestCase):
    def test_uniform_weighted_mean_is_unbiased(self):
        pop = skewed_population(20_000, seed=11)
        truth = math.fsum(pop) / len(pop)
        acc = 0.0
        reps = 200
        for r in range(reps):
            s = collect(pop, UniformSampler(0.02, seed=1000 + r))
            acc += s.mean()
        bias = abs(acc / reps - truth) / truth
        self.assertLess(bias, 0.01, f"均匀采样加权均值偏差 {bias:.4%}")

    def test_duration_weighted_mean_is_unbiased(self):
        pop = skewed_population(20_000, seed=12)
        truth = math.fsum(pop) / len(pop)
        acc = 0.0
        reps = 200
        for r in range(reps):
            s = collect(pop, DurationSampler(0.02, threshold=100.0,
                                             seed=2000 + r))
            acc += s.mean()
        bias = abs(acc / reps - truth) / truth
        self.assertLess(bias, 0.01, f"按耗时采样加权均值偏差 {bias:.4%}")

    def test_ht_total_unbiased(self):
        pop = skewed_population(5_000, seed=13)
        truth = math.fsum(pop)
        acc = 0.0
        reps = 300
        for r in range(reps):
            s = collect(pop, DurationSampler(0.05, threshold=80.0,
                                             seed=3000 + r))
            acc += s.total_estimate()
        bias = abs(acc / reps - truth) / truth
        self.assertLess(bias, 0.03)

    def test_population_count_estimate(self):
        pop = skewed_population(10_000, seed=14)
        s = collect(pop, UniformSampler(0.1, seed=15))
        self.assertAlmostEqual(s.total_weight / len(pop), 1.0, delta=0.05)


class TestQuantileRestoration(unittest.TestCase):
    def test_uniform_quantiles_close_to_full(self):
        pop = skewed_population(200_000, seed=21)
        s = collect(pop, UniformSampler(0.01, seed=22))
        for q, tol in ((0.5, 0.05), (0.9, 0.10), (0.99, 0.25), (0.999, 0.6)):
            est, truth = s.quantile(q), nearest_rank_quantile(pop, q)
            self.assertLess(abs(est - truth) / truth, tol,
                            f"p{q}: est={est}, truth={truth}")

    def test_quantile_ci_coverage(self):
        pop = skewed_population(200_000, seed=23)
        truth = nearest_rank_quantile(pop, 0.99)
        reps = 80
        covered = 0
        for r in range(reps):
            s = collect(pop, UniformSampler(0.01, seed=4000 + r))
            lo, hi = s.quantile_ci(0.99, confidence=0.95)
            covered += lo <= truth <= hi
        self.assertGreaterEqual(covered / reps, 0.88,
                                f"95% CI 实际覆盖率 {covered/reps:.0%}")

    def test_duration_sampling_improves_tail(self):
        pop = skewed_population(500_000, seed=24)
        truth = nearest_rank_quantile(pop, 0.999)
        su = collect(pop, UniformSampler(0.01, seed=25))
        sd = collect(pop, DurationSampler(0.01, threshold=100.0, seed=25))
        eu = abs(su.quantile(0.999) - truth) / truth
        ed = abs(sd.quantile(0.999) - truth) / truth
        self.assertLess(ed, 0.5)
        self.assertLessEqual(ed, eu * 1.5)
        self.assertGreaterEqual(len([w for _, w in sd._vw if w < 50.0]),
                                len([w for _, w in su._vw if w < 50.0]))


class TestSmallSample(unittest.TestCase):
    def test_tiny_sample_flagged_unreliable(self):
        pop = skewed_population(100_000, seed=31)
        s = collect(pop, UniformSampler(0.0001, seed=32))  # 约 10 条
        self.assertLess(len(s), 30)
        self.assertFalse(s.reliable())
        self.assertFalse(s.quantile_reliable(0.999))

    def test_tiny_sample_still_returns_ci(self):
        s = WeightedSample()
        rng = Random(33)
        for _ in range(12):
            s.add(rng.lognormvariate(2, 1), 100.0)
        lo, hi = s.mean_ci()
        qlo, qhi = s.quantile_ci(0.99)
        self.assertTrue(math.isfinite(lo) and math.isfinite(hi))
        self.assertTrue(math.isfinite(qlo) and math.isfinite(qhi))
        self.assertGreaterEqual(hi - lo, 0.0)

    def test_tail_quantile_needs_tail_ess(self):
        pop = skewed_population(200_000, seed=34)
        # 约 200 条样本：整体可靠，但 p99.9 尾部 ESS 不足
        s = collect(pop, UniformSampler(0.001, seed=35))
        self.assertTrue(s.reliable())
        self.assertFalse(s.quantile_reliable(0.999))
        self.assertTrue(s.quantile_reliable(0.5))


class TestExtremeTail(unittest.TestCase):
    def test_extreme_tail_restoration(self):
        rng = Random(41)
        pop = [rng.lognormvariate(1.6, 0.9) for _ in range(100_000)]
        pop += [1000.0 * rng.paretovariate(1.2) for _ in range(200)]
        truth = nearest_rank_quantile(pop, 0.999)
        sd = collect(pop, DurationSampler(0.02, threshold=100.0, seed=42))
        # 极端长尾下按耗时采样：p99.9 仍应在真值的 2 倍以内
        self.assertLess(abs(sd.quantile(0.999) - truth) / truth, 1.0)
        # 最慢的点权重应等于 1（p=1，必然入样）
        slowest = max(sd._vw, key=lambda vw: vw[0])
        self.assertAlmostEqual(slowest[1], 1.0)

    def test_empty_sample(self):
        s = WeightedSample()
        self.assertTrue(math.isnan(s.mean()))
        self.assertTrue(math.isnan(s.quantile(0.99)))
        self.assertEqual(s.effective_sample_size, 0.0)
        self.assertFalse(s.reliable())


if __name__ == "__main__":
    unittest.main()

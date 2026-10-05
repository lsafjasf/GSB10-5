"""slowquery 库的自测（标准库 unittest）。

运行：python3 -m unittest test_slowquery -v
"""

import math
import random
import unittest

import slowquery as sq


def make_lognormal_sample(n, mu, sigma, seed):
    rng = random.Random(seed)
    return [rng.lognormvariate(mu, sigma) for _ in range(n)]


class TestUnbiasedness(unittest.TestCase):
    """加权还原的无偏性：多次独立采样，估计均值应围绕真值波动。"""

    def test_ht_total_unbiased_uniform(self):
        data = make_lognormal_sample(20000, 4.0, 0.8, seed=7)
        true_total = sum(data)
        ests = []
        for rep in range(300):
            s = sq.UniformSampler(0.01, seed=rep)
            for x in data:
                s.offer(x)
            ests.append(sq.estimate_total(s.items))
        mean_est = sum(ests) / len(ests)
        # 300 次平均的相对偏差应远小于单次估计的标准误
        self.assertLess(abs(mean_est - true_total) / true_total, 0.02)

    def test_ht_total_unbiased_pps(self):
        data = make_lognormal_sample(20000, 4.0, 0.8, seed=11)
        true_total = sum(data)
        ests = []
        for rep in range(300):
            s = sq.PPSDurationSampler(0.01, seed=rep, pilot_size=512)
            for x in data:
                s.offer(x)
            ests.append(sq.estimate_total(s.items))
        mean_est = sum(ests) / len(ests)
        self.assertLess(abs(mean_est - true_total) / true_total, 0.02)

    def test_naive_mean_biased_under_pps(self):
        """PPS 样本直接算算术平均（不还原权重）必须显著偏高——证明权重必要。"""
        data = make_lognormal_sample(20000, 4.0, 0.8, seed=13)
        true_mean = sum(data) / len(data)
        s = sq.PPSDurationSampler(0.05, seed=0, pilot_size=512)
        for x in data:
            s.offer(x)
        naive = sum(x for _, x, _ in s.items) / len(s.items)
        restored = sq.estimate_mean(s.items)[0]
        self.assertGreater(naive, true_mean * 1.5)          # 朴素均值显著偏高
        self.assertLess(abs(restored - true_mean) / true_mean, 0.05)  # 还原后贴近


class TestConfidenceIntervals(unittest.TestCase):
    """置信区间的覆盖率应接近名义 95%。"""

    def test_mean_ci_coverage(self):
        data = make_lognormal_sample(50000, 4.0, 0.8, seed=17)
        true_mean = sum(data) / len(data)
        hits = 0
        trials = 200
        for rep in range(trials):
            s = sq.UniformSampler(0.01, seed=1000 + rep)
            for x in data:
                s.offer(x)
            mu, lo, hi, _ = sq.estimate_mean(s.items)
            if lo <= true_mean <= hi:
                hits += 1
        # 二项波动下 200 次试验覆盖率应在 [0.88, 1.0]
        self.assertGreaterEqual(hits / trials, 0.88)

    def test_quantile_ci_coverage(self):
        data = make_lognormal_sample(50000, 4.0, 0.8, seed=19)
        xs = sorted(data)
        true_q90 = sq.exact_quantiles(xs, [0.9])[0]
        hits = 0
        trials = 200
        for rep in range(trials):
            s = sq.UniformSampler(0.01, seed=2000 + rep)
            for x in data:
                s.offer(x)
            wd = sq.WeightedDistribution(s.items)
            lo, hi = wd.quantile_ci(0.9)
            if lo <= true_q90 <= hi:
                hits += 1
        self.assertGreaterEqual(hits / trials, 0.85)


class TestSmallSample(unittest.TestCase):
    """样本量不足时必须给出告警，而不是只报一个数。"""

    def test_tiny_sample_warns(self):
        s = sq.UniformSampler(0.0002, seed=3)
        for x in make_lognormal_sample(50000, 4.0, 0.8, seed=23):
            s.offer(x)
        n = len(s.items)
        ess = sq.effective_sample_size(s.items)
        warns = sq.reliability_warnings(n, ess, [0.5, 0.99, 0.999])
        self.assertTrue(any("样本量" in w for w in warns))
        self.assertTrue(any("0.999" in w for w in warns))

    def test_extreme_quantile_warns(self):
        s = sq.UniformSampler(0.001, seed=5)
        for x in make_lognormal_sample(50000, 4.0, 0.8, seed=29):
            s.offer(x)
        warns = sq.reliability_warnings(len(s.items),
                                        sq.effective_sample_size(s.items),
                                        [0.9999])
        self.assertTrue(any("0.9999" in w for w in warns))


class TestTailCapture(unittest.TestCase):
    """极端长尾下，PPS 应确定性地保留全部超大查询。"""

    def test_pps_captures_all_monsters(self):
        rng = random.Random(31)
        data = [rng.lognormvariate(4.0, 0.5) for _ in range(50000)]
        monsters = [1e5, 2e5, 5e5]  # 三条超大慢查询
        data.extend(monsters)
        s = sq.PPSDurationSampler(0.01, seed=0, pilot_size=2048)
        for x in data:
            s.offer(x)
        kept = sorted(x for _, x, _ in s.items)
        for m in monsters:
            self.assertIn(m, kept)  # 长尾必中
        # 且权重还原后总量仍无偏
        true_total = sum(data)
        est_total = sq.estimate_total(s.items)
        self.assertLess(abs(est_total - true_total) / true_total, 0.3)


class TestStreamingAndAttribution(unittest.TestCase):
    def test_pps_streaming_memory_bounded(self):
        """pilot 之后 sampler 内部只保留样本，不累积全量数据。"""
        s = sq.PPSDurationSampler(0.01, seed=0, pilot_size=128)
        rng = random.Random(37)
        for _ in range(100000):
            s.offer(rng.lognormvariate(4.0, 0.5))
        self.assertIsNone(s._pilot)
        self.assertLess(len(s.items), 5000)  # 约 1%，远小于 10 万

    def test_groupby_unbiased(self):
        rng = random.Random(41)
        data = []
        for _ in range(60000):
            svc = "a" if rng.random() < 0.7 else "b"
            mu = 4.0 if svc == "a" else 5.5
            data.append((rng.lognormvariate(mu, 0.5), svc))
        true_total = {}
        for x, svc in data:
            true_total[svc] = true_total.get(svc, 0.0) + x
        ests = {"a": [], "b": []}
        for rep in range(100):
            s = sq.UniformSampler(0.02, seed=rep)
            for x, svc in data:
                s.offer(x, svc)
            g = sq.groupby(s.items, lambda a: a)
            for svc in ("a", "b"):
                ests[svc].append(g[svc]["est_total_ms"])
        for svc in ("a", "b"):
            mean_est = sum(ests[svc]) / len(ests[svc])
            self.assertLess(abs(mean_est - true_total[svc]) / true_total[svc], 0.05)


if __name__ == "__main__":
    unittest.main()

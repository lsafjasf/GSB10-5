"""dpagg 自测：单轮、多轮预算内、预算耗尽、重复/相似切片、敏感度与噪声。"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dpagg import (BudgetLedger, BudgetExhaustedError, PrivateAggregator,
                   slice_fingerprint, laplace_scale, gaussian_sigma,
                   count_sensitivity, sum_sensitivity, mean_sensitivity,
                   histogram_sensitivity)

ROWS = [
    {"dept": "engineering", "city": "shanghai", "salary": 100.0},
    {"dept": "engineering", "city": "beijing", "salary": 200.0},
    {"dept": "sales", "city": "shanghai", "salary": 150.0},
    {"dept": "sales", "city": "beijing", "salary": 250.0},
    {"dept": "engineering", "city": "shanghai", "salary": 300.0},
]


def make_agg(epsilon_total=1.0, mechanism="laplace", delta=0.0, seed=42):
    ledger = BudgetLedger(epsilon_total, delta_total=delta * 1000)
    return PrivateAggregator(ledger, mechanism=mechanism, delta=delta,
                             seed=seed), ledger


class TestSensitivity(unittest.TestCase):
    def test_count(self):
        self.assertEqual(count_sensitivity(), 1.0)

    def test_sum(self):
        self.assertEqual(sum_sensitivity(0, 500), 500.0)
        with self.assertRaises(ValueError):
            sum_sensitivity(10, 10)

    def test_mean(self):
        s_sum, s_cnt = mean_sensitivity(0, 500)
        self.assertEqual((s_sum, s_cnt), (500.0, 1.0))

    def test_histogram(self):
        self.assertEqual(histogram_sensitivity(), 1.0)


class TestNoiseCalibration(unittest.TestCase):
    def test_laplace_scale(self):
        self.assertAlmostEqual(laplace_scale(1.0, 0.1), 10.0)
        self.assertAlmostEqual(laplace_scale(500.0, 0.5), 1000.0)

    def test_gaussian_sigma(self):
        expected = 1.0 * math.sqrt(2 * math.log(1.25 / 1e-5)) / 0.1
        self.assertAlmostEqual(gaussian_sigma(1.0, 0.1, 1e-5), expected)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            laplace_scale(1.0, 0.0)
        with self.assertRaises(ValueError):
            gaussian_sigma(1.0, 0.1, 1.5)


class TestSingleRound(unittest.TestCase):
    def test_single_count(self):
        agg, ledger = make_agg(epsilon_total=1.0)
        res = agg.ask("count", ROWS, {"dept": "engineering"}, epsilon=0.1)
        self.assertEqual(res["status"], "answered")
        self.assertFalse(res["cached"])
        self.assertEqual(res["charged"]["epsilon"], 0.1)
        self.assertAlmostEqual(ledger.epsilon_spent, 0.1)
        self.assertEqual(len(ledger.records()), 1)
        noise = res["noise"][0]
        self.assertEqual(noise["sensitivity"], 1.0)
        self.assertAlmostEqual(noise["noise_param"], 10.0)  # b = 1/0.1

    def test_single_sum_uses_bounds(self):
        agg, ledger = make_agg()
        res = agg.ask("sum", ROWS, {"dept": "engineering"},
                      epsilon=0.2, value_field="salary", lo=0, hi=500)
        self.assertEqual(res["noise"][0]["sensitivity"], 500.0)
        self.assertAlmostEqual(res["noise"][0]["noise_param"], 2500.0)

    def test_mean_charges_two_halves(self):
        agg, ledger = make_agg()
        res = agg.ask("mean", ROWS, {"city": "shanghai"},
                      epsilon=0.4, value_field="salary", lo=0, hi=500)
        self.assertAlmostEqual(res["charged"]["epsilon"], 0.4)
        self.assertEqual(len(res["noise"]), 2)
        self.assertEqual(res["noise"][0]["sensitivity"], 500.0)
        self.assertEqual(res["noise"][1]["sensitivity"], 1.0)

    def test_histogram_single_charge(self):
        agg, ledger = make_agg()
        res = agg.ask("histogram", ROWS, {"dept": "engineering"},
                      epsilon=0.3, value_field="city",
                      buckets=["shanghai", "beijing"])
        self.assertAlmostEqual(res["charged"]["epsilon"], 0.3)
        self.assertEqual(res["noise"][0]["sensitivity"], 1.0)
        self.assertEqual(set(res["answer"]), {"shanghai", "beijing"})


class TestMultiRoundSharedBudget(unittest.TestCase):
    def test_multi_round_accumulates(self):
        agg, ledger = make_agg(epsilon_total=1.0)
        for i in range(5):
            agg.ask("count", ROWS, {"dept": f"dept{i}"}, epsilon=0.2)
        self.assertAlmostEqual(ledger.epsilon_spent, 1.0)
        self.assertTrue(ledger.exhausted)
        self.assertEqual(len(ledger.records()), 5)

    def test_shared_ledger_across_aggregators(self):
        ledger = BudgetLedger(0.4)
        agg_a = PrivateAggregator(ledger, seed=1)
        agg_b = PrivateAggregator(ledger, seed=2)
        agg_a.ask("count", ROWS, {"dept": "sales"}, epsilon=0.2)
        agg_b.ask("count", ROWS, {"dept": "engineering"}, epsilon=0.2)
        self.assertAlmostEqual(ledger.epsilon_spent, 0.4)
        # 预算已被两个聚合器共同耗尽，第三笔（无论谁发起）必须拒绝
        with self.assertRaises(BudgetExhaustedError):
            agg_b.ask("count", ROWS, {"city": "shanghai"}, epsilon=0.1)
        with self.assertRaises(BudgetExhaustedError):
            agg_a.ask("count", ROWS, {"city": "beijing"}, epsilon=0.1)
        self.assertAlmostEqual(ledger.epsilon_spent, 0.4)


class TestBudgetExhaustion(unittest.TestCase):
    def test_refusal_after_exhaustion(self):
        agg, ledger = make_agg(epsilon_total=0.2)
        agg.ask("count", ROWS, {"dept": "sales"}, epsilon=0.2)
        with self.assertRaises(BudgetExhaustedError) as ctx:
            agg.ask("count", ROWS, {"dept": "engineering"}, epsilon=0.1)
        self.assertAlmostEqual(ctx.exception.remaining, 0.0)
        # 拒绝不扣费
        self.assertAlmostEqual(ledger.epsilon_spent, 0.2)
        self.assertEqual(len(ledger.records()), 1)

    def test_oversized_request_rejected_without_charge(self):
        agg, ledger = make_agg(epsilon_total=0.5)
        with self.assertRaises(BudgetExhaustedError):
            agg.ask("count", ROWS, {"dept": "sales"}, epsilon=0.9)
        self.assertAlmostEqual(ledger.epsilon_spent, 0.0)
        self.assertEqual(len(ledger.records()), 0)


class TestRepeatedAndSimilarSlices(unittest.TestCase):
    def test_exact_repeat_is_free_and_cached(self):
        agg, ledger = make_agg(epsilon_total=0.5)
        first = agg.ask("count", ROWS, {"dept": "engineering"}, epsilon=0.2)
        second = agg.ask("count", ROWS, {"dept": "engineering"}, epsilon=0.2)
        self.assertEqual(second["slice"]["status"], "exact")
        self.assertTrue(second["cached"])
        self.assertEqual(second["charged"]["epsilon"], 0.0)
        self.assertEqual(first["answer"], second["answer"])  # 不重新加噪
        self.assertAlmostEqual(ledger.epsilon_spent, 0.2)    # 只扣了一次
        self.assertEqual(len(ledger.records()), 2)           # 免费也有记录

    def test_whitespace_case_normalized(self):
        fp1 = slice_fingerprint("count", {"dept": "  Engineering "})
        fp2 = slice_fingerprint("count", {"dept": "engineering"})
        self.assertEqual(fp1, fp2)

    def test_similar_slice_detected_but_charged(self):
        agg, ledger = make_agg(epsilon_total=1.0)
        agg.ask("count", ROWS, {"dept": "engineering", "city": "shanghai"},
                epsilon=0.2)
        res = agg.ask("count", ROWS, {"dept": "engineering"},
                      epsilon=0.2)
        self.assertEqual(res["slice"]["status"], "similar")
        self.assertIsNotNone(res["slice"]["similar_to"])
        self.assertGreaterEqual(res["slice"]["similarity"], 0.6)
        self.assertAlmostEqual(res["charged"]["epsilon"], 0.2)  # 相似仍全额扣费
        self.assertAlmostEqual(ledger.epsilon_spent, 0.4)

    def test_new_slice_no_false_positive(self):
        agg, ledger = make_agg(epsilon_total=1.0)
        agg.ask("count", ROWS, {"dept": "engineering"}, epsilon=0.1)
        res = agg.ask("count", ROWS, {"city": "beijing"}, epsilon=0.1)
        self.assertEqual(res["slice"]["status"], "new")


class TestGaussianMechanism(unittest.TestCase):
    def test_gaussian_spends_delta(self):
        ledger = BudgetLedger(1.0, delta_total=1e-3)
        agg = PrivateAggregator(ledger, mechanism="gaussian", delta=1e-5,
                                seed=7)
        res = agg.ask("count", ROWS, {"dept": "sales"}, epsilon=0.5)
        self.assertAlmostEqual(ledger.delta_spent, 1e-5)
        expected_sigma = math.sqrt(2 * math.log(1.25 / 1e-5)) / 0.5
        self.assertAlmostEqual(res["noise"][0]["noise_param"], expected_sigma)


class TestLedgerExport(unittest.TestCase):
    def test_export_json(self):
        import tempfile
        agg, ledger = make_agg(epsilon_total=1.0)
        agg.ask("count", ROWS, {"dept": "sales"}, epsilon=0.1)
        with tempfile.NamedTemporaryFile("r", suffix=".jsonl",
                                         delete=False) as fh:
            path = fh.name
        ledger.export_json(path)
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().strip().splitlines()
        self.assertEqual(len(lines), 2)  # 表头 + 1 条记录
        os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)

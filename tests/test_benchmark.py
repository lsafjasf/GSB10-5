import unittest

from benchmark.gen import generate
from benchmark.run import check_agreement, measure


class TestBenchmark(unittest.TestCase):
    def test_small_agreement_and_measure(self):
        pairs = generate(300, seed=1)
        agreement = check_agreement(pairs)
        self.assertEqual(agreement["mismatches"], 0)
        self.assertGreater(agreement["drift_rows"], 0)
        self.assertEqual(agreement["drift_detected"], agreement["drift_rows"])
        perf = measure(pairs, runs=1)
        self.assertGreater(perf["validate_per_second"], 0)

    def test_scenario_coverage(self):
        pairs = generate(2000, seed=7)
        scenarios = {p.scenario for p in pairs}
        for name in ("ok_same", "ok_reorder", "missing", "extra", "count_mismatch", "invalid", "drift"):
            self.assertIn(name, scenarios)
        families = {p.family for p in pairs}
        self.assertEqual(families, {"named", "positional", "bare"})


if __name__ == "__main__":
    unittest.main()

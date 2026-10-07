"""Self-tests for the change-risk scoring library.

Run:  python3 -m unittest discover -s tests -v
"""
import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from risk_score.factors import (FACTOR_NAMES, FACTORS, ChangeMetrics, FileInfo,
                                fit_ecdf, normalize_vector, raw_factor_vector)
from risk_score.model import (fit_logistic, risk_score, rollback_probability,
                              score_contributions)
from risk_score.sensitivity import monte_carlo_sensitivity, oat_sensitivity
from risk_score.synthetic import TRUE_WEIGHTS, generate_history
from risk_score.validation import (auc_fast, calibration, point_biserial,
                                   risk_bands, spearman)


def quiet_file(**kw):
    base = dict(path="src/a.py", churn_90d=1, test_ratio=0.8, ownership_frac=0.5)
    base.update(kw)
    return FileInfo(**base)


def calm_change(**kw):
    """A change with every factor at its safest end."""
    base = dict(
        lines_added=5, lines_deleted=2,
        files=(quiet_file(test_ratio=0.95),),
        author_prior_changes=200, author_prior_rollbacks=1,
        reviewer_count=3, review_comment_count=12,
        is_emergency=False, is_off_hours=False, is_release_window=False,
    )
    base.update(kw)
    return ChangeMetrics(**base)


class TestFactorSignals(unittest.TestCase):
    def test_all_factors_bounded(self):
        rng = random.Random(0)
        for _ in range(200):
            m = calm_change(
                lines_added=rng.randrange(20000),
                lines_deleted=rng.randrange(20000),
                files=tuple(quiet_file(churn_90d=rng.randrange(100),
                                       test_ratio=rng.random())
                            for _ in range(rng.randrange(1, 6))),
                author_prior_changes=rng.randrange(500),
                author_prior_rollbacks=rng.randrange(50),
                reviewer_count=rng.randrange(5),
                review_comment_count=rng.randrange(30),
                is_emergency=rng.random() < 0.5,
                is_off_hours=rng.random() < 0.5,
                is_release_window=rng.random() < 0.5,
            )
            m = ChangeMetrics(**{**m.__dict__,
                                 "author_prior_rollbacks":
                                 min(m.author_prior_rollbacks, m.author_prior_changes)})
            for v in raw_factor_vector(m):
                self.assertGreaterEqual(v, 0.0)
                self.assertLessEqual(v, 1.0)

    def test_monotonic_directions(self):
        safe = raw_factor_vector(calm_change())
        big = raw_factor_vector(calm_change(lines_added=9000, lines_deleted=9000))
        self.assertGreater(big[0], safe[0])                       # size
        hot = raw_factor_vector(calm_change(files=(quiet_file(churn_90d=40),)))
        self.assertGreater(hot[1], safe[1])                       # hotness
        untested = raw_factor_vector(calm_change(files=(quiet_file(test_ratio=0.0),)))
        self.assertGreater(untested[2], safe[2])                  # test gap
        newbie = raw_factor_vector(calm_change(author_prior_changes=0))
        self.assertGreater(newbie[3], safe[3])                    # inexperience
        unreviewed = raw_factor_vector(calm_change(reviewer_count=0,
                                                   review_comment_count=0))
        self.assertGreater(unreviewed[5], safe[5])                # review gap
        friday = raw_factor_vector(calm_change(is_emergency=True, is_off_hours=True))
        self.assertGreater(friday[6], safe[6])                    # schedule


class TestNormalization(unittest.TestCase):
    def test_ecdf_median_maps_to_half(self):
        raws = [[0.1 * i] * len(FACTORS) for i in range(101)]
        ecdf = fit_ecdf(raws)
        mid = normalize_vector([5.0] * len(FACTORS), ecdf)
        for v in mid:
            self.assertAlmostEqual(v, 0.5, places=2)

    def test_extremes(self):
        raws = [[float(i)] * len(FACTORS) for i in range(11)]
        ecdf = fit_ecdf(raws)
        lo = normalize_vector([0.0] * len(FACTORS), ecdf)
        hi = normalize_vector([10.0] * len(FACTORS), ecdf)
        self.assertTrue(all(v == 0.0 for v in lo))
        self.assertTrue(all(v == 1.0 for v in hi))


class TestModelFit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, cls.X, cls.y, _ = generate_history(4000, seed=42)
        cls.model = fit_logistic(cls.X, cls.y)

    def test_converges(self):
        self.assertTrue(self.model.converged)

    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(self.model.weights), 1.0, places=9)

    def test_recovers_generator_weights(self):
        # The fit must recover the documented latent weights of the synthetic
        # generator within noise -- this is what makes the weights "sourced"
        # rather than arbitrary.
        for fitted, true in zip(self.model.weights, TRUE_WEIGHTS):
            self.assertAlmostEqual(fitted, true, delta=0.08)

    def test_score_bounds(self):
        for x in self.X[:200]:
            s = risk_score(x, self.model.weights)
            self.assertGreaterEqual(s, 0.0)
            self.assertLessEqual(s, 100.0)
            p = rollback_probability(x, self.model)
            self.assertGreaterEqual(p, 0.0)
            self.assertLessEqual(p, 1.0)


class TestContributionDecomposition(unittest.TestCase):
    def test_shares_sum_to_one(self):
        _, X, _, _ = generate_history(50, seed=1)
        model = fit_logistic(X, [0, 1] * 25)
        for x in X:
            parts = score_contributions(x, model.weights)
            self.assertEqual(len(parts), len(FACTORS))
            total_share = sum(p[3] for p in parts)
            if risk_score(x, model.weights) > 0:
                self.assertAlmostEqual(total_share, 1.0, places=9)
            for name, value, weight, share in parts:
                self.assertIn(name, FACTOR_NAMES)
                self.assertGreaterEqual(value, 0.0)
                self.assertGreaterEqual(share, 0.0)


class TestRequiredScenarios(unittest.TestCase):
    """The four mandated edge scenarios."""

    @classmethod
    def setUpClass(cls):
        _, X, y, cls.ecdf = generate_history(4000, seed=42)
        cls.model = fit_logistic(X, y)

    def _score(self, metrics):
        x = normalize_vector(raw_factor_vector(metrics), self.ecdf)
        return risk_score(x, self.model.weights), x

    def test_single_extreme_factor(self):
        # Only schedule pressure is maxed; everything else is minimal.
        m = calm_change(is_emergency=True, is_off_hours=True,
                        is_release_window=True)
        score, x = self._score(m)
        parts = score_contributions(x, self.model.weights)
        top = max(parts, key=lambda p: p[3])
        second = sorted((p[3] for p in parts), reverse=True)[1]
        self.assertEqual(top[0], "schedule_pressure")
        self.assertGreater(top[3], 0.35)          # plurality of the score
        self.assertGreater(top[3], 1.5 * second)  # clearly dominant
        self.assertGreater(score, self._score(calm_change())[0])

    def test_many_small_risks_accumulate(self):
        # No single extreme factor, but every factor is moderately bad.
        m = calm_change(
            lines_added=800, lines_deleted=300,
            files=(quiet_file(churn_90d=12, test_ratio=0.25),),
            author_prior_changes=3, author_prior_rollbacks=1,
            reviewer_count=1, review_comment_count=1,
            is_off_hours=True,
        )
        score, x = self._score(m)
        parts = score_contributions(x, self.model.weights)
        self.assertLess(max(p[3] for p in parts), 0.5)   # no dominant factor
        contributors = sum(1 for p in parts if p[3] > 0.05)
        self.assertGreaterEqual(contributors, 4)         # broad accumulation
        self.assertGreater(score, self._score(calm_change())[0])

    def test_all_factors_low(self):
        score, x = self._score(calm_change())
        self.assertLess(score, 25.0)
        self.assertLess(rollback_probability(x, self.model), 0.10)

    def test_borderline_band(self):
        # Scores near the decision threshold must be classifiable and their
        # classification must be sensitive to weight perturbation (documented
        # behaviour, not silently stable).
        _, X, y, _ = generate_history(2000, seed=99)
        scores = [risk_score(x, self.model.weights) for x in X]
        threshold = sorted(scores)[int(len(scores) * 0.8)]
        band = [s for s in scores if abs(s - threshold) <= 2.0]
        self.assertGreater(len(band), 0)
        oat = oat_sensitivity(X, self.model.weights, threshold)
        self.assertTrue(all(0.0 <= r.spearman <= 1.0 for r in oat))
        self.assertTrue(all(r.fraction_reclassified < 0.5 for r in oat))


class TestValidationMetrics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, X, y, _ = generate_history(4000, seed=42)
        cls.model = fit_logistic(X, y)
        cls.scores = [risk_score(x, cls.model.weights) for x in X]
        cls.y = y

    def test_scores_correlate_with_rollbacks(self):
        rho = spearman(self.scores, self.y)
        self.assertGreater(rho, 0.3)
        r_pb = point_biserial(self.scores, self.y)
        self.assertGreater(r_pb, 0.2)

    def test_auc_beats_trivial_baseline(self):
        a = auc_fast(self.scores, self.y)
        self.assertGreater(a, 0.7)
        # baseline: score by the single "size" factor only
        _, X, _, _ = generate_history(4000, seed=42)
        size_scores = [x[0] for x in X]
        self.assertGreater(a, auc_fast(size_scores, self.y))

    def test_calibration_reasonable(self):
        _, X, y, _ = generate_history(4000, seed=42)
        probs = [rollback_probability(x, self.model) for x in X]
        cal = calibration(probs, y)
        self.assertLess(abs(cal.mean_predicted - cal.observed_rate), 0.05)

    def test_risk_bands_monotonic(self):
        bands = risk_bands(self.scores, self.y, edges=(20, 40, 60, 80))
        rates = [b.rollback_rate for b in bands if b.n > 0]
        self.assertEqual(rates, sorted(rates))
        self.assertAlmostEqual(sum(b.share_of_rollbacks for b in bands), 1.0)


class TestSensitivity(unittest.TestCase):
    def test_conclusion_robust_to_weight_perturbation(self):
        _, X, y, _ = generate_history(2000, seed=42)
        model = fit_logistic(X, y)
        scores = [risk_score(x, model.weights) for x in X]
        threshold = sorted(scores)[int(len(scores) * 0.8)]
        oat = oat_sensitivity(X, model.weights, threshold)
        self.assertEqual(len(oat), 2 * len(FACTORS))
        for r in oat:
            self.assertGreater(r.spearman, 0.9)  # ranking is stable
        mc, _ = monte_carlo_sensitivity(X[:500], model.weights,
                                        model.std_errors, threshold,
                                        draws=100, seed=3, beta=model.beta)
        self.assertGreater(mc.rank_corr_min, 0.85)
        self.assertLess(mc.highrisk_flip_rate, 0.2)


if __name__ == "__main__":
    unittest.main()

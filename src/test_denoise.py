"""Self-tests for the denoise library (stdlib unittest)."""

import math
import os
import tempfile
import unittest

import denoise as dn


class TestPgmIO(unittest.TestCase):
    def test_roundtrip(self):
        pix, w, h = dn.make_image(32, 24, "edges")
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "a.pgm")
            dn.write_pgm(p, pix, w, h)
            pix2, w2, h2, mx = dn.read_pgm(p)
        self.assertEqual((w2, h2, mx), (w, h, 255))
        self.assertEqual(pix, pix2)


class TestNoiseAndSigma(unittest.TestCase):
    def test_sigma_estimator_recovers_gaussian_level(self):
        pix, w, h = dn.make_image(128, 128, "gradient")
        noisy = dn.add_gaussian_noise(pix, 20.0, seed=1)
        est = dn.estimate_sigma(noisy, w, h)
        self.assertAlmostEqual(est, 20.0, delta=3.0)

    def test_impulse_noise_changes_expected_fraction(self):
        pix = [128] * (100 * 100)
        out = dn.add_impulse_noise(pix, 0.1, seed=2)
        changed = sum(1 for a, b in zip(pix, out) if a != b)
        self.assertTrue(600 <= changed <= 1400)  # ~1000 expected


class TestFilters(unittest.TestCase):
    def test_box_filter_constant_image_is_identity(self):
        pix = [77] * (20 * 20)
        self.assertEqual(dn.box_filter(pix, 20, 20, 2), pix)

    def test_box_filter_matches_direct_mean(self):
        pix, w, h = dn.make_image(16, 16, "texture")
        got = dn.box_filter(pix, w, h, 1)
        # direct 3x3 mean at an interior pixel
        x, y = 8, 8
        vals = [pix[(y + dy) * w + (x + dx)]
                for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
        self.assertEqual(got[y * w + x], sum(vals) // 9)

    def test_median_removes_impulse(self):
        pix = [100] * (9 * 9)
        pix[4 * 9 + 4] = 255  # single hot pixel
        out = dn.median_filter(pix, 9, 9, 1)
        self.assertEqual(out[4 * 9 + 4], 100)

    def test_selective_mean_preserves_step_edge_better_than_box(self):
        # vertical step edge 0 | 255, no noise
        w, h = 20, 20
        pix = [0 if x < w // 2 else 255 for y in range(h) for x in range(w)]
        sel = dn.selective_mean(pix, w, h, 2, threshold=30)
        box = dn.box_filter(pix, w, h, 2)
        edge_x = w // 2 - 1  # dark pixel adjacent to the edge
        mid = 10 * w + edge_x
        self.assertLess(abs(sel[mid] - 0), abs(box[mid] - 0))
        self.assertLess(sel[mid], 60)  # box would give ~127

    def test_filters_reduce_gaussian_noise_on_flat_scene(self):
        pix, w, h = dn.make_image(64, 64, "flat")
        noisy = dn.add_gaussian_noise(pix, 25.0, seed=3)
        before = dn.mse(noisy, pix)
        for out in (dn.box_filter(noisy, w, h, 1),
                    dn.median_filter(noisy, w, h, 1),
                    dn.selective_mean(noisy, w, h, 1, 60)):
            self.assertLess(dn.mse(out, pix), before * 0.35)


class TestMetrics(unittest.TestCase):
    def test_psnr_identical_is_inf(self):
        pix, w, h = dn.make_image(16, 16, "edges")
        self.assertEqual(dn.psnr(pix, pix), float("inf"))

    def test_edge_mask_finds_step_edges(self):
        pix, w, h = dn.make_image(64, 64, "edges")
        m = dn.edge_mask(pix, w, h)
        frac = sum(m) / len(m)
        self.assertTrue(0.01 < frac < 0.5)  # sparse but present

    def test_grad_ratio_of_identical_is_one(self):
        pix, w, h = dn.make_image(32, 32, "texture")
        self.assertAlmostEqual(
            dn.gradient_energy_ratio(pix, pix, w, h), 1.0)

    def test_paired_metrics_keys(self):
        clean, w, h = dn.make_image(32, 32, "edges")
        noisy = dn.add_gaussian_noise(clean, 15.0, seed=4)
        den = dn.box_filter(noisy, w, h, 1)
        r = dn.paired_metrics(noisy, den, clean, w, h)
        for k in ("flat_psnr_before", "flat_psnr_after",
                  "edge_psnr_before", "edge_psnr_after", "grad_ratio"):
            self.assertIn(k, r)
        self.assertTrue(math.isfinite(r["flat_psnr_after"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)

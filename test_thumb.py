#!/usr/bin/env python3
"""Self-tests (unittest, stdlib only):

    python3 test_thumb.py -v

Coverage: proportional scaling, extreme aspect ratios, fine-texture
antialiasing, tiny targets, sizing policy determinism, PNG roundtrip,
metric correctness, sharpen-order sanity.
"""

import io
import math
import unittest

from thumb.image import Image
from thumb.pipeline import Policy, fit_size, make_thumbnail
from thumb.resize import resize, unsharp_mask
from thumb.pngcodec import encode_png, decode_png
from thumb.synth import render, SCENES
from thumb import metrics


class SizingPolicyTests(unittest.TestCase):
    def test_proportional_scale(self):
        self.assertEqual(fit_size(400, 300, 100, 100), (100, 75))
        self.assertEqual(fit_size(400, 300, 200, 100), (133, 100))
        dw, dh = fit_size(512, 384, 160, 120)
        self.assertEqual((dw, dh), (160, 120))

    def test_aspect_ratio_preserved(self):
        for (w, h, bw, bh) in [(2000, 100, 160, 160),
                               (100, 2000, 160, 160),
                               (4000, 8, 256, 256),
                               (8, 4000, 128, 128),
                               (513, 257, 97, 33)]:
            dw, dh = fit_size(w, h, bw, bh)
            self.assertGreaterEqual(dw, 1)
            self.assertGreaterEqual(dh, 1)
            self.assertAlmostEqual(dw / dh, w / h, delta=max(w, h) / min(w, h) * 0.12)

    def test_extreme_aspect_not_a_line(self):
        # 20:1 image into 160x160 would fit to 160x8 exactly;
        # ratios more extreme would collapse short side -> policy floor
        dw, dh = fit_size(2000, 100, 160, 160)
        self.assertEqual((dw, dh), (160, 8))
        self.assertGreaterEqual(min(dw, dh), 8)
        dw, dh = fit_size(4000, 8, 256, 256)
        self.assertGreaterEqual(min(dw, dh), 8)
        self.assertLessEqual(max(dw, dh), 8192)
        # a normal box never shrinks the short side below min_side
        for _ in range(200):
            dw, dh = fit_size(5000, 9, 128, 128)
            self.assertGreaterEqual(min(dw, dh), 8)

    def test_tiny_explicit_targets_honored(self):
        self.assertEqual(fit_size(512, 384, 1, 1), (1, 1))
        self.assertEqual(fit_size(512, 384, 3, 2), (3, 2))
        self.assertEqual(fit_size(512, 384, 4, 4), (4, 3))

    def test_no_upscale_by_default(self):
        self.assertEqual(fit_size(10, 10, 100, 100), (10, 10))
        p = Policy(allow_upscale=True)
        self.assertEqual(fit_size(10, 10, 20, 20, p), (20, 20))

    def test_deterministic(self):
        for _ in range(10):
            self.assertEqual(fit_size(333, 177, 160, 120),
                             fit_size(333, 177, 160, 120))


class ResizeTests(unittest.TestCase):
    def test_constant_stays_constant(self):
        img = Image(37, 29, bytes([90, 130, 200]) * (37 * 29))
        out = resize(img, 11, 7, "triangle")
        self.assertTrue(all(v in (89, 90) for v in out.data[0::3]))
        self.assertTrue(all(v in (129, 130, 131) for v in out.data[1::3]))
        self.assertTrue(all(v in (199, 200, 201) for v in out.data[2::3]))

    def test_1x1_is_average(self):
        img = Image(2, 1, bytearray([10, 0, 0, 30, 0, 0]))
        out = resize(img, 1, 1, "triangle")
        self.assertAlmostEqual(out.data[0], 20, delta=1)

    def test_tiny_output_supported(self):
        src = render(SCENES["photo"], 64, 48, ss=1)
        for dw, dh in [(1, 1), (2, 1), (1, 2), (3, 2), (2, 3)]:
            out = resize(src, dw, dh, "triangle")
            self.assertEqual((out.width, out.height), (dw, dh))

    def test_antialias_removes_subpixel_flicker(self):
        # 1px alternating checkerboard at exactly 2:1 must average to
        # mid-gray with a proper low-pass (box/triangle), not alias to
        # a checkerboard or to solid black or white.
        w = h = 64
        px = bytearray(w * h * 3)
        for y in range(h):
            for x in range(w):
                v = 0 if (x + y) % 2 == 0 else 255
                p = (y * w + x) * 3
                px[p] = px[p + 1] = px[p + 2] = v
        src = Image(w, h, px)
        for kernel in ("box", "triangle", "lanczos3"):
            out = resize(src, w // 2, h // 2, kernel)
            luma = metrics.to_luma(out)
            mean = sum(luma) / len(luma)
            var = sum((v - mean) ** 2 for v in luma) / len(luma)
            self.assertAlmostEqual(mean, 127.5, delta=2.0)
            self.assertLess(var, 20.0, "kernel %s did not low-pass" % kernel)

    def test_antialias_fine_texture_vs_ground_truth(self):
        # ground truth is the box-integrated scene, so a widened-box
        # resample is the best possible match; the real check is that
        # every low-pass kernel beats unfiltered point sampling by a
        # wide margin on fine texture
        src = render(SCENES["texture"], 256, 192, ss=1)
        truth = render(SCENES["texture"], 96, 72, ss=6)
        nn = resize(src, 96, 72, "nearest")
        s_nn, p_nn = metrics.ssim(nn, truth), metrics.psnr(nn, truth)
        for kernel in ("box", "triangle", "lanczos3"):
            out = resize(src, 96, 72, kernel)
            s = metrics.ssim(out, truth)
            p = metrics.psnr(out, truth)
            self.assertGreater(s, s_nn + 0.02,
                               "%s SSIM not better than nearest" % kernel)
            self.assertGreater(p, p_nn + 5.0,
                               "%s PSNR not better than nearest" % kernel)
        self.assertLess(s_nn, 0.98)


class PipelineAndMetricsTests(unittest.TestCase):
    def test_png_roundtrip(self):
        img = render(SCENES["photo"], 71, 53, ss=1)
        out = decode_png(encode_png(img))
        self.assertEqual((out.width, out.height), (71, 53))
        self.assertLess(metrics.mae(img, out), 0.02)

    def test_identical_metrics(self):
        img = render(SCENES["edges"], 64, 64, ss=1)
        self.assertAlmostEqual(metrics.ssim(img, img), 1.0, places=6)
        self.assertAlmostEqual(metrics.ssim_global(img, img), 1.0, places=6)
        self.assertEqual(metrics.psnr(img, img), float("inf"))
        self.assertEqual(metrics.mae(img, img), 0.0)

    def test_order_sanity_on_fine_texture(self):
        # sharpen-after-resize should be closer to the true downsampled
        # texture than sharpen-before-resize; baseline keeps highest SSIM
        src = render(SCENES["texture"], 256, 192, ss=1)
        truth = render(SCENES["texture"], 96, 72, ss=6)
        before = make_thumbnail(src, 96, 72, kernel="triangle",
                                sharpen="before", amount=0.8)
        after = make_thumbnail(src, 96, 72, kernel="triangle",
                               sharpen="after", amount=0.8)
        self.assertGreater(metrics.ssim(after, truth),
                           metrics.ssim(before, truth))

    def test_sharpen_adds_edge_energy(self):
        img = render(SCENES["photo"], 96, 72, ss=2)
        sharp = unsharp_mask(img, amount=1.0)
        self.assertGreater(metrics.edge_energy(sharp),
                           metrics.edge_energy(img))

    def test_extreme_pipeline_runs_and_keeps_shape(self):
        # 50:1 source: fit would give a 128x2-3 line, so the policy
        # bumps the short side to min_side=8 and lets the long side
        # overflow the box deterministically
        src = render(SCENES["edges"], 600, 12, ss=1)
        out = make_thumbnail(src, 128, 128, sharpen="after")
        self.assertEqual((out.width, out.height), (400, 8))
        src = render(SCENES["edges"], 12, 600, ss=1)
        out = make_thumbnail(src, 128, 128, sharpen="after")
        self.assertEqual((out.width, out.height), (8, 400))

    def test_roundtrip_through_png_file(self, tmp_path=None):
        import tempfile, os
        img = render(SCENES["photo"], 40, 30, ss=1)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.png")
            with open(p, "wb") as fh:
                fh.write(encode_png(img))
            back = decode_png(open(p, "rb").read())
        self.assertLess(metrics.mae(img, back), 0.02)


if __name__ == "__main__":
    unittest.main(verbosity=2)

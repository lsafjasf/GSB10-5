import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import thumblib as t


def gradient_img(w, h, mode="RGB"):
    img = t.Img.new(w, h, mode)
    n = w * h
    for c in range(img.nch):
        ch = img.channels[c]
        for y in range(h):
            base = y * w
            for x in range(w):
                ch[base + x] = (x * 255 // max(1, w - 1) + c * 40 + y) % 256
    return img


def detailed_img(w, h):
    """Gradient + checker patches + deterministic speckle: has smooth areas,
    edges and fine texture, like a real photo."""
    img = gradient_img(w, h)
    ch = img.channels[0]
    for y in range(h):
        base = y * w
        for x in range(w):
            i = base + x
            if (x // 4 + y // 4) % 2:
                ch[i] = 255 - ch[i]
            ch[i] = (ch[i] + ((x * 7349 + y * 12583) % 23)) % 256
    return img


def checker_img(w, h, period=2):
    img = t.Img.new(w, h, "L")
    ch = img.channels[0]
    for y in range(h):
        base = y * w
        for x in range(w):
            ch[base + x] = 255 if ((x // period) + (y // period)) % 2 else 0
    return img


class TestFitSize(unittest.TestCase):
    def test_proportional(self):
        self.assertEqual(t.fit_size(400, 300, 100, 100), (100, 75))
        self.assertEqual(t.fit_size(2000, 3000, 200, 200), (133, 200))
        self.assertEqual(t.fit_size(100, 100, 640, 480), (100, 100))

    def test_extreme_aspect_not_a_line(self):
        ow, oh = t.fit_size(4000, 30, 200, 200)
        self.assertGreaterEqual(min(ow, oh), 4)
        self.assertAlmostEqual(ow / oh, 4000 / 30, delta=2.0)
        ow, oh = t.fit_size(30, 4000, 200, 200)
        self.assertGreaterEqual(min(ow, oh), 4)
        ow, oh = t.fit_size(100000, 1, 200, 200)
        self.assertGreaterEqual(min(ow, oh), 4)
        self.assertLessEqual(max(ow, oh), 8192)

    def test_tiny_target(self):
        self.assertEqual(t.fit_size(800, 600, 8, 8), (8, 6))
        self.assertEqual(t.fit_size(640, 480, 1, 1), (1, 1))
        self.assertEqual(t.fit_size(640, 480, 2, 2), (2, 2))

    def test_small_source_not_upscaled(self):
        self.assertEqual(t.fit_size(3, 3, 100, 100), (3, 3))
        self.assertEqual(t.fit_size(50, 40, 100, 100), (50, 40))

    def test_deterministic(self):
        for case in [(400, 300, 100, 100), (4000, 30, 200, 200),
                     (999, 1, 7, 7), (1, 999, 7, 7)]:
            self.assertEqual(t.fit_size(*case), t.fit_size(*case))


class TestResize(unittest.TestCase):
    def test_proportional_resize(self):
        img = gradient_img(400, 300)
        out = t.resize(img, 100, 75)
        self.assertEqual((out.width, out.height), (100, 75))
        self.assertEqual(out.mode, "RGB")

    def test_fine_texture_antialias(self):
        """Nyquist checkerboard must blur to ~mid gray (no moire), while
        nearest neighbor aliases badly."""
        img = checker_img(256, 256, period=1)
        ref_mean = 127.5
        for method in ("lanczos", "area"):
            out = t.resize(img, 64, 64, method=method)
            n = out.width * out.height
            mean = sum(out.channels[0]) / n
            var = sum((v - mean) ** 2 for v in out.channels[0]) / n
            self.assertAlmostEqual(mean, ref_mean, delta=1.0)
            self.assertLess(var, 4.0, "%s leaves moire variance" % method)
        near = t.resize(img, 64, 64, method="nearest")
        n = near.width * near.height
        var_n = sum((v - 127.5) ** 2 for v in near.channels[0]) / n
        self.assertGreater(var_n, 1000.0, "nearest should alias hard")

    def test_tiny_target_resize(self):
        img = gradient_img(800, 600)
        out = t.resize(img, 8, 6)
        self.assertEqual((out.width, out.height), (8, 6))
        out = t.resize(img, 1, 1, method="area")
        self.assertEqual((out.width, out.height), (1, 1))

    def test_extreme_aspect_resize(self):
        img = gradient_img(2000, 15)
        ow, oh = t.fit_size(2000, 15, 200, 200)
        out = t.resize(img, ow, oh)
        self.assertEqual((out.width, out.height), (ow, oh))
        self.assertGreaterEqual(min(ow, oh), 4)

    def test_dc_preserved(self):
        img = t.Img(97, 53, "RGB", [bytearray([200]) * (97 * 53) for _ in range(3)])
        for method in ("lanczos", "area", "triangle"):
            out = t.resize(img, 31, 17, method=method)
            for ch in out.channels:
                self.assertTrue(all(v == 200 for v in ch),
                                "%s failed DC preservation" % method)


class TestMetrics(unittest.TestCase):
    def test_identity(self):
        img = gradient_img(64, 48)
        self.assertEqual(t.ssim(img, img), 1.0)
        self.assertEqual(t.psnr(img, img), float("inf"))
        self.assertEqual(t.rmse(img, img), 0.0)

    def test_monotonic_noise(self):
        img = gradient_img(64, 48)
        noisy = img.copy()
        for i in range(0, len(noisy.channels[0]), 7):
            noisy.channels[0][i] = (noisy.channels[0][i] + 60) % 256
        self.assertLess(t.ssim(noisy, img), t.ssim(img, img))
        self.assertLess(t.psnr(noisy, img), 40.0)


class TestPipeline(unittest.TestCase):
    def test_orders_same_geometry(self):
        img = gradient_img(320, 240)
        sizes = set()
        for order in ("sharpen_first", "sharpen_last", "none"):
            th, info = t.make_thumbnail(img, 96, 96, order=order)
            sizes.add((th.width, th.height))
            self.assertEqual(info["order"], order)
        self.assertEqual(len(sizes), 1)

    def test_sharpen_last_closer_to_reference(self):
        """At matched apparent sharpness (gradient energy), sharpening
        after the low-pass downscale must beat sharpening before it."""
        img = detailed_img(300, 220)
        last, _ = t.make_thumbnail(img, 90, 90, order="sharpen_last",
                                   sharpen_amount=0.5)
        target = t.gradient_energy(last)
        lo, hi = 0.0, 16.0
        first = None
        for _ in range(24):
            mid = (lo + hi) / 2
            first, _ = t.make_thumbnail(img, 90, 90, order="sharpen_first",
                                        pre_sharpen_amount=mid)
            if t.gradient_energy(first) < target:
                lo = mid
            else:
                hi = mid
        ref = t.reference_thumbnail(img, last.width, last.height)
        e_first = t.gradient_energy(first)
        self.assertAlmostEqual(e_first, target, delta=target * 0.05)
        self.assertGreater(t.ssim(last, ref), t.ssim(first, ref))

    def test_deterministic_bytes(self):
        img = gradient_img(120, 90)
        a, _ = t.make_thumbnail(img, 40, 40)
        b, _ = t.make_thumbnail(img, 40, 40)
        self.assertEqual(t.encode_png(a), t.encode_png(b))


class TestPng(unittest.TestCase):
    def test_roundtrip_all_modes(self):
        for mode in ("L", "LA", "RGB", "RGBA"):
            img = gradient_img(37, 23, mode=mode)
            back = t.decode_png(t.encode_png(img))
            self.assertEqual((back.width, back.height, back.mode),
                             (37, 23, mode))
            for ca, cb in zip(img.channels, back.channels):
                self.assertEqual(bytes(ca), bytes(cb))

    def test_roundtrip_after_resize(self):
        img = gradient_img(150, 100)
        th, _ = t.make_thumbnail(img, 48, 48)
        back = t.decode_png(t.encode_png(th))
        for ca, cb in zip(th.channels, back.channels):
            self.assertEqual(bytes(ca), bytes(cb))


if __name__ == "__main__":
    unittest.main(verbosity=2)

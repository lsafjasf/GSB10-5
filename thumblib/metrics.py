"""Fidelity and appearance metrics, all computed against a reference image
of identical geometry (the reference is derived from the original, see
pipeline.reference_thumbnail)."""

import math

from .image import Img

_MAX = 255.0
_C1 = (0.01 * _MAX) ** 2
_C2 = (0.03 * _MAX) ** 2


def mse(a, b):
    _check(a, b)
    total = 0.0
    n = 0
    for ca, cb in zip(a.channels, b.channels):
        for x, y in zip(ca, cb):
            d = x - y
            total += d * d
            n += 1
    return total / n


def rmse(a, b):
    return math.sqrt(mse(a, b))


def mae(a, b):
    _check(a, b)
    total = 0
    n = 0
    for ca, cb in zip(a.channels, b.channels):
        for x, y in zip(ca, cb):
            total += abs(x - y)
            n += 1
    return total / n


def psnr(a, b):
    m = mse(a, b)
    if m == 0.0:
        return float("inf")
    return 10.0 * math.log10(_MAX * _MAX / m)


def _gauss2d_weights(size=11, sigma=1.5):
    r = size // 2
    ws = [math.exp(-((x * x)) / (2 * sigma * sigma))
          for x in range(-r, r + 1)]
    total = sum(ws)
    return [w / total for w in ws]


def _ssim_channel(pa, pb, w, h):
    if min(w, h) < 7:
        mean_a = sum(pa) / len(pa)
        mean_b = sum(pb) / len(pb)
        va = sum((v - mean_a) ** 2 for v in pa) / len(pa)
        vb = sum((v - mean_b) ** 2 for v in pb) / len(pb)
        cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(pa, pb)) / len(pa)
        return ((2 * mean_a * mean_b + _C1) * (2 * cov + _C2)
                / ((mean_a ** 2 + mean_b ** 2 + _C1) * (va + vb + _C2)))
    size = 11 if min(w, h) >= 11 else (min(w, h) | 1)
    ws = _gauss2d_weights(size)
    r = size // 2

    def box(src):
        tmp = [0.0] * (w * h)
        for y in range(h):
            base = y * w
            for x in range(w):
                acc = 0.0
                for k, wgt in enumerate(ws):
                    xx = min(w - 1, max(0, x - r + k))
                    acc += src[base + xx] * wgt
                tmp[base + x] = acc
        out = [0.0] * (w * h)
        for x in range(w):
            for y in range(h):
                acc = 0.0
                for k, wgt in enumerate(ws):
                    yy = min(h - 1, max(0, y - r + k))
                    acc += tmp[yy * w + x] * wgt
                out[y * w + x] = acc
        return out

    fa = [float(v) for v in pa]
    fb = [float(v) for v in pb]
    mu_a = box(fa)
    mu_b = box(fb)
    aa = box([v * v for v in fa])
    bb = box([v * v for v in fb])
    ab = box([x * y for x, y in zip(fa, fb)])
    total = 0.0
    n = w * h
    for i in range(n):
        ma = mu_a[i]
        mb = mu_b[i]
        va = aa[i] - ma * ma
        vb = bb[i] - mb * mb
        cov = ab[i] - ma * mb
        total += ((2 * ma * mb + _C1) * (2 * cov + _C2)
                  / ((ma * ma + mb * mb + _C1) * (va + vb + _C2)))
    return total / n


def ssim(a, b):
    """Mean SSIM (Wang et al. 2004, gaussian 11x11 sigma 1.5) over channels."""
    _check(a, b)
    vals = [_ssim_channel(ca, cb, a.width, a.height)
            for ca, cb in zip(a.channels, b.channels)]
    return sum(vals) / len(vals)


def gradient_energy(img):
    """Mean |dx|+|dy| of luma: cheap apparent-sharpness proxy."""
    w, h = img.width, img.height
    lum = img.luma_plane()
    total = 0.0
    n = 0
    for y in range(h):
        base = y * w
        for x in range(w):
            i = base + x
            if x + 1 < w:
                total += abs(lum[i + 1] - lum[i])
                n += 1
            if y + 1 < h:
                total += abs(lum[i + w] - lum[i])
                n += 1
    return total / n if n else 0.0


def clip_fraction(img):
    """Fraction of samples pinned at 0 or 255 (crushed blacks/whites)."""
    n = 0
    hit = 0
    for ch in img.channels:
        for v in ch:
            n += 1
            if v == 0 or v == 255:
                hit += 1
    return hit / n if n else 0.0


def _check(a, b):
    if (a.width, a.height, a.mode) != (b.width, b.height, b.mode):
        raise ValueError("geometry/mode mismatch: %dx%d %s vs %dx%d %s"
                         % (a.width, a.height, a.mode,
                            b.width, b.height, b.mode))

"""Gaussian blur and unsharp masking (separable, float)."""

import math

from .image import Img


def gaussian_weights(sigma, radius=None):
    if radius is None:
        radius = max(1, int(math.ceil(2.5 * sigma)))
    ws = [math.exp(-(x * x) / (2.0 * sigma * sigma))
          for x in range(-radius, radius + 1)]
    total = sum(ws)
    return tuple(w / total for w in ws)


def _blur1d(plane, w, h, ws, horizontal):
    r = (len(ws) - 1) // 2
    out = [0.0] * len(plane)
    if horizontal:
        for y in range(h):
            base = y * w
            for x in range(w):
                acc = 0.0
                lo = x - r
                hi = x + r
                if lo >= 0 and hi < w:
                    for k, wgt in enumerate(ws):
                        acc += plane[base + lo + k] * wgt
                else:
                    for k, wgt in enumerate(ws):
                        xx = x - r + k
                        if xx < 0:
                            xx = -xx
                        elif xx >= w:
                            xx = 2 * w - xx - 2
                            if xx < 0:
                                xx = 0
                        acc += plane[base + xx] * wgt
                out[base + x] = acc
    else:
        for x in range(w):
            for y in range(h):
                acc = 0.0
                for k, wgt in enumerate(ws):
                    yy = y - r + k
                    if yy < 0:
                        yy = -yy
                    elif yy >= h:
                        yy = 2 * h - yy - 2
                        if yy < 0:
                            yy = 0
                    acc += plane[yy * w + x] * wgt
                out[y * w + x] = acc
    return out


def gaussian_blur_planes(img, sigma=1.0):
    if sigma <= 0:
        return img.float_planes()
    ws = gaussian_weights(sigma)
    planes = img.float_planes()
    out = []
    for c in range(img.nch):
        p = _blur1d(planes[c], img.width, img.height, ws, True)
        p = _blur1d(p, img.width, img.height, ws, False)
        out.append(p)
    return out


def gaussian_blur(img, sigma=1.0):
    out = gaussian_blur_planes(img, sigma)
    return Img.from_float_planes(img.width, img.height, img.mode, out)


def unsharp_mask(img, sigma=0.8, amount=0.5, threshold=0):
    """out = in + amount * (in - gauss(in)) above |threshold|, clamped."""
    if amount == 0:
        return img.copy()
    bplanes = gaussian_blur_planes(img, sigma)
    planes = img.float_planes()
    out = []
    for c in range(img.nch):
        src = planes[c]
        b = bplanes[c]
        d = [0.0] * len(src)
        for i, v in enumerate(src):
            diff = v - b[i]
            if abs(diff) > threshold:
                d[i] = v + amount * diff
            else:
                d[i] = v
        out.append(d)
    return Img.from_float_planes(img.width, img.height, img.mode, out)

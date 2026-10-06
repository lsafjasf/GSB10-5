"""Resampling: Lanczos-3 (windowed-sinc low-pass), exact area average (box
low-pass via summed-area table), triangle and nearest.

Antialias policy: when downscaling by factor s = out/in < 1, the
reconstruction kernel is stretched by 1/s so its cutoff tracks the *output*
Nyquist frequency. This is the classic "filter, then sample" scheme
(Nyquist-Shannon): energy above the new Nyquist would alias into moire, so
the kernel acts as the pre-sample low-pass. Lanczos-3 is a sinc windowed by
sinc(x/3), a finite-support approximation of the ideal brick-wall low-pass;
area/box is the exact pixel-footprint integral (what PIL calls BOX/reduce).
"""

import math

from .image import Img


def _sinc(x):
    if x == 0.0:
        return 1.0
    x *= math.pi
    return math.sin(x) / x


def _lanczos(x, a):
    x = abs(x)
    if x >= a:
        return 0.0
    return _sinc(x) * _sinc(x / a)


def _triangle(x):
    x = abs(x)
    return 1.0 - x if x < 1.0 else 0.0


def _contribs(in_size, out_size, kernel, support):
    """Per output index: (first_input_index, tuple_of_weights).

    Kernel is dilated by 1/scale on downscale so it low-passes at the new
    Nyquist before sampling; weights are normalized to sum 1 (DC preserved).
    """
    scale = out_size / in_size
    if scale < 1.0:
        width = support / scale
        kscale = scale
    else:
        width = support
        kscale = 1.0
    out = []
    for i in range(out_size):
        center = (i + 0.5) / scale
        left = int(math.ceil(center - width))
        right = int(math.floor(center + width))
        if left < 0:
            left = 0
        if right > in_size:
            right = in_size
        ws = []
        total = 0.0
        for j in range(left, right):
            w = kernel((j + 0.5 - center) * kscale)
            if w != 0.0:
                ws.append((j, w))
                total += w
        if total == 0.0:
            nearest = min(in_size - 1, max(0, int(center)))
            ws = [(nearest, 1.0)]
            total = 1.0
        inv = 1.0 / total
        out.append((ws[0][0], tuple((j - ws[0][0], w * inv) for j, w in ws)))
    return out


def _resample_axis(planes, w, h, contribs, out_len, horizontal):
    nch = len(planes)
    if horizontal:
        result = []
        for c in range(nch):
            src = planes[c]
            dst = [0.0] * (out_len * h)
            for y in range(h):
                row = src[y * w:(y + 1) * w]
                orow = dst[y * out_len:(y + 1) * out_len]
                for i, (start, ws) in enumerate(contribs):
                    acc = 0.0
                    for off, wgt in ws:
                        acc += row[start + off] * wgt
                    orow[i] = acc
                dst[y * out_len:(y + 1) * out_len] = orow
            result.append(dst)
        return result
    result = []
    for c in range(nch):
        src = planes[c]
        dst = [0.0] * (w * out_len)
        for oy, (start, ws) in enumerate(contribs):
            base = oy * w
            for off, wgt in ws:
                srow = src[(start + off) * w:(start + off + 1) * w]
                if wgt == 1.0 and len(ws) == 1:
                    for x in range(w):
                        dst[base + x] = srow[x]
                else:
                    for x in range(w):
                        dst[base + x] += srow[x] * wgt
        result.append(dst)
    return result


def _resample_kernel(img, ow, oh, kernel, support):
    planes = img.float_planes()
    planes = _resample_axis(planes, img.width, img.height,
                            _contribs(img.width, ow, kernel, support),
                            ow, True)
    planes = _resample_axis(planes, ow, img.height,
                            _contribs(img.height, oh, kernel, support),
                            oh, False)
    return Img.from_float_planes(ow, oh, img.mode, planes)


def _build_sat(plane, w, h):
    sat = [[0] * (w + 1) for _ in range(h + 1)]
    for y in range(h):
        srow = sat[y + 1]
        prow = sat[y]
        run = 0
        base = y * w
        for x in range(w):
            run += plane[base + x]
            srow[x + 1] = prow[x + 1] + run
    return sat


def _sat_at(sat, w, h, x, y):
    """Bilinearly interpolated summed-area value at fractional (x, y)."""
    if x <= 0.0 or y <= 0.0:
        return 0.0
    if x > w:
        x = float(w)
    if y > h:
        y = float(h)
    x0 = int(x)
    y0 = int(y)
    fx = x - x0
    fy = y - y0
    x1 = x0 + 1 if x0 < w else w
    y1 = y0 + 1 if y0 < h else h
    v00 = sat[y0][x0]
    v01 = sat[y0][x1]
    v10 = sat[y1][x0]
    v11 = sat[y1][x1]
    top = v00 + (v01 - v00) * fx
    bot = v10 + (v11 - v10) * fx
    return top + (bot - top) * fy


def _resample_area(img, ow, oh):
    """Exact box average over each output pixel's input footprint."""
    w, h = img.width, img.height
    planes = img.float_planes()
    out_planes = []
    sx = w / ow
    sy = h / oh
    for c in range(img.nch):
        sat = _build_sat(planes[c], w, h)
        dst = [0.0] * (ow * oh)
        for oy in range(oh):
            y0 = oy * sy
            y1 = y0 + sy
            base = oy * ow
            for ox in range(ow):
                x0 = ox * sx
                x1 = x0 + sx
                area = (x1 - x0) * (y1 - y0)
                v = (_sat_at(sat, w, h, x1, y1)
                     - _sat_at(sat, w, h, x0, y1)
                     - _sat_at(sat, w, h, x1, y0)
                     + _sat_at(sat, w, h, x0, y0))
                dst[base + ox] = v / area
        out_planes.append(dst)
    return Img.from_float_planes(ow, oh, img.mode, out_planes)


def _resample_nearest(img, ow, oh):
    w, h = img.width, img.height
    out = []
    for c in range(img.nch):
        src = img.channels[c]
        dst = bytearray(ow * oh)
        for oy in range(oh):
            iy = (oy * h) // oh
            base = oy * ow
            sbase = iy * w
            for ox in range(ow):
                dst[base + ox] = src[sbase + (ox * w) // ow]
        out.append(dst)
    return Img(ow, oh, img.mode, out)


def resize(img, ow, oh, method="lanczos"):
    """Resize to exactly (ow, oh). method: lanczos|area|triangle|nearest."""
    if ow < 1 or oh < 1:
        raise ValueError("target must be >= 1px")
    if ow == img.width and oh == img.height:
        return img.copy()
    if method == "lanczos":
        return _resample_kernel(img, ow, oh, lambda x: _lanczos(x, 3), 3.0)
    if method == "triangle":
        return _resample_kernel(img, ow, oh, _triangle, 1.0)
    if method == "area":
        return _resample_area(img, ow, oh)
    if method == "nearest":
        return _resample_nearest(img, ow, oh)
    raise ValueError("unknown method %r" % (method,))

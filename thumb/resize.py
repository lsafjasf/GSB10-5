"""Resampling and sharpening, pure Python.

Antialiasing model
------------------
Downsampling without pre-filtering aliases: energy above the output
Nyquist frequency folds back and shows up as moire / stairstepping.
The standard fix (and the one used by libjpeg-turbo, PIL, libvips) is a
single resampling pass whose reconstruction kernel is *widened* by the
scale ratio, so the kernel doubles as the low-pass (anti-alias) filter:

    ratio = src_n / dst_n            (> 1 when shrinking)
    radius = kernel_support * max(ratio, 1)
    weight(i, j) = kernel((i + 0.5 - center_j) / max(ratio, 1))

For ratio >= 1 this convolves the source with a kernel whose cutoff is
scaled to the output Nyquist before decimation, i.e. "low-pass first,
then sample". For upscaling (ratio < 1) the kernel keeps its nominal
support and behaves as a plain interpolator.

Kernels: triangle (linear, support 1) and lanczos3 (support 3).
Triangle is the default: near-optimal for area-averaging downscales,
no negative lobes (no ringing), cheap. Lanczos3 keeps slightly more
detail at the cost of halos around hard edges.

All arithmetic is fixed point (Q12 weights, sum == 4096 exactly) so
results are deterministic across platforms and runs.
"""

import math

QBITS = 12
QONE = 1 << QBITS  # 4096


def _kernel_triangle(x):
    ax = abs(x)
    return 1.0 - ax if ax < 1.0 else 0.0


def _sinc(x):
    if x == 0.0:
        return 1.0
    x *= math.pi
    return math.sin(x) / x


def _kernel_lanczos3(x):
    ax = abs(x)
    if ax >= 3.0:
        return 0.0
    return _sinc(x) * _sinc(x / 3.0)


KERNELS = {
    "box": (lambda x: 1.0 if abs(x) < 0.5 else 0.0, 0.5),
    "triangle": (_kernel_triangle, 1.0),
    "lanczos3": (_kernel_lanczos3, 3.0),
}


def _build_nearest(src_n, dst_n):
    """Point sampling, no low-pass: kept as the aliasing counterexample."""
    ratio = src_n / dst_n
    return [([min(src_n - 1, int((j + 0.5) * ratio))], [QONE])
            for j in range(dst_n)]


def _build_filter(src_n, dst_n, kernel, support):
    """Precompute per-output-pixel (indices, weights) in Q12 fixed point."""
    ratio = src_n / dst_n
    scale = ratio if ratio > 1.0 else 1.0  # widen kernel only when shrinking
    radius = support * scale
    filters = []
    for j in range(dst_n):
        center = (j + 0.5) * ratio - 0.5
        lo = int(math.ceil(center - radius))
        hi = int(math.floor(center + radius))
        inds = []
        ws = []
        total = 0.0
        for i in range(lo, hi + 1):
            w = kernel((i - center) / scale)
            if w == 0.0:
                continue
            inds.append(min(max(i, 0), src_n - 1))  # clamp edge (replicate)
            ws.append(w)
            total += w
        # quantize to Q12, force exact sum so constant images stay constant
        qw = [int(round(QONE * w / total)) for w in ws]
        diff = QONE - sum(qw)
        if qw:
            biggest = max(range(len(qw)), key=lambda k: qw[k])
            qw[biggest] += diff
        filters.append((inds, qw))
    return filters


def _vpass(rows, filters):
    """Resample a list of equal-length rows along the row axis.

    rows: sequence of bytes-like rows; filters from _build_filter.
    Returns a list of bytearray rows. Element loops run inside zip()
    comprehensions, which is the fastest pure-Python option.
    """
    out = []
    half = QONE >> 1
    for inds, ws in filters:
        acc = [half] * len(rows[0])
        for i, w in zip(inds, ws):
            r = rows[i]
            acc = [a + w * b for a, b in zip(acc, r)]
        out.append(bytearray(0 if v < 0 else (255 if v > 255 else v)
                             for v in (a >> QBITS for a in acc)))
    return out


def _transpose(rows):
    return [bytearray(col) for col in zip(*rows)]


def resize_rows(rows, src_w, src_h, dst_w, dst_h, kernel="triangle"):
    """Resize interleaved-RGB rows (each src_w*3 bytes) to dst size."""
    if dst_w == src_w and dst_h == src_h:
        return [bytearray(r) for r in rows]
    if kernel == "nearest":
        yfilt = _build_nearest(src_h, dst_h)
        base_xfilt = _build_nearest(src_w, dst_w)
    else:
        kfn, support = KERNELS[kernel]
        yfilt = _build_filter(src_h, dst_h, kfn, support)
        base_xfilt = _build_filter(src_w, dst_w, kfn, support)
    # after transpose, rows are the source byte-columns; build one
    # filter per output byte column (3 per output pixel), mapping pixel
    # indices to the column of the matching channel
    xfilt = []
    for inds, ws in base_xfilt:
        for c in range(3):
            xfilt.append(([3 * i + c for i in inds], list(ws)))
    # vertical first, then horizontal on the transposed intermediate
    stage = _vpass(rows, yfilt) if dst_h != src_h else [bytearray(r) for r in rows]
    if dst_w != src_w:
        stage = _transpose(stage)
        stage = _vpass(stage, xfilt)
        stage = _transpose(stage)
    return stage


def resize(image, dst_w, dst_h, kernel="triangle"):
    from .image import Image

    rows = resize_rows(image.rows(), image.width, image.height,
                       dst_w, dst_h, kernel)
    return Image.from_rows(dst_w, dst_h, rows)


def _blur121_rows(rows):
    """Separable [1,2,1]/4 blur over a list of rows (radius 1 gaussian)."""
    n = len(rows)
    out = []
    for y in range(n):
        up = rows[y - 1] if y > 0 else rows[0]
        mid = rows[y]
        dn = rows[y + 1] if y < n - 1 else rows[n - 1]
        out.append(bytearray((u + 2 * m + d + 2) >> 2
                             for u, m, d in zip(up, mid, dn)))
    return out


def unsharp_mask(image, amount=0.6, threshold=0):
    """Unsharp mask with a radius-1 gaussian blur.

    amount: strength of the added high-frequency component (0.6 default).
    threshold: per-channel minimum delta (0..255) below which no boost is
    applied, to avoid amplifying flat-area noise.
    """
    from .image import Image

    rows = [bytearray(r) for r in image.rows()]
    blurred = _blur121_rows(_transpose(_blur121_rows(_transpose(rows))))
    gain = int(round(amount * 256))
    thr = int(threshold)
    out = bytearray(len(image.data))
    src = image.data
    pos = 0
    for y in range(image.height):
        brow = blurred[y]
        for x in range(image.width * 3):
            s = src[pos]
            d = s - brow[x]
            if d > thr or d < -thr:
                v = s + ((d * gain + 128) >> 8)
                out[pos] = 0 if v < 0 else (255 if v > 255 else v)
            else:
                out[pos] = s
            pos += 1
    return Image(image.width, image.height, out)

"""Quality metrics against a reference image (stdlib only).

- PSNR  : peak signal-to-noise ratio on RGB, dB
- MAE   : mean absolute error on RGB
- SSIM  : structural similarity on luma (Wang/Bovik 2004), uniform
          sliding window of 8x8, computed via integral images so the
          cost is O(N) in pixels; ssim_global() uses one full-image
          window (used by the 10k benchmark's sampled checks).
- edge_energy: mean |gradient| on luma, a sharpness proxy.

Luma uses ITU-R BT.601 fixed-point weights (77,150,29)/256.
"""


def to_luma(image):
    w, h = image.width, image.height
    plane = [0] * (w * h)
    d = image.data
    p = 0
    for i in range(w * h):
        plane[i] = (77 * d[p] + 150 * d[p + 1] + 29 * d[p + 2]) >> 8
        p += 3
    return plane


def mae(a, b):
    da, db = a.data, b.data
    return sum(abs(x - y) for x, y in zip(da, db)) / len(da)


def mse(a, b):
    da, db = a.data, b.data
    return sum((x - y) * (x - y) for x, y in zip(da, db)) / len(da)


def psnr(a, b):
    e = mse(a, b)
    if e == 0.0:
        return float("inf")
    return 10.0 * __import__("math").log10(255.0 * 255.0 / e)


def _integral(plane, w, h):
    """Prefix-sum table size (w+1)*(h+1), row-major."""
    iw = w + 1
    tab = [0] * (iw * (h + 1))
    for y in range(h):
        row_sum = 0
        base = y * w
        ib = (y + 1) * iw + 1
        pb = y * iw + 1
        for x in range(w):
            row_sum += plane[base + x]
            tab[ib + x] = tab[pb + x] + row_sum
    return tab


def _rect_sum(tab, iw, x0, y0, x1, y1):
    # sum over inclusive rectangle [x0,x1] x [y0,y1]
    return (tab[(y1 + 1) * iw + x1 + 1]
            - tab[y0 * iw + x1 + 1]
            - tab[(y1 + 1) * iw + x0]
            + tab[y0 * iw + x0])


def _ssim_core(pa, pb, w, h, win):
    import math

    ia = _integral(pa, w, h)
    ib = _integral(pb, w, h)
    iaa = _integral([v * v for v in pa], w, h)
    ibb = _integral([v * v for v in pb], w, h)
    iab = _integral([a * b for a, b in zip(pa, pb)], w, h)
    iw = w + 1
    n = win * win
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    total = 0.0
    count = 0
    for y0 in range(0, h - win + 1):
        for x0 in range(0, w - win + 1):
            x1 = x0 + win - 1
            y1 = y0 + win - 1
            sa = _rect_sum(ia, iw, x0, y0, x1, y1) / n
            sb = _rect_sum(ib, iw, x0, y0, x1, y1) / n
            sab = _rect_sum(iab, iw, x0, y0, x1, y1) / n
            # second moments: var = E[x^2] - E[x]^2
            s2a = _rect_sum(iaa, iw, x0, y0, x1, y1) / n
            s2b = _rect_sum(ibb, iw, x0, y0, x1, y1) / n
            va = s2a - sa * sa
            vb = s2b - sb * sb
            cov = sab - sa * sb
            num = (2 * sa * sb + c1) * (2 * cov + c2)
            den = (sa * sa + sb * sb + c1) * (va + vb + c2)
            total += num / den
            count += 1
    return total / count if count else 1.0


def ssim(a, b, win=8):
    """Mean SSIM over all valid 8x8 windows (requires equal size)."""
    if a.width != b.width or a.height != b.height:
        raise ValueError("SSIM inputs must have identical size")
    pa, pb = to_luma(a), to_luma(b)
    w, h = a.width, a.height
    win = min(win, w, h)
    return _ssim_core(pa, pb, w, h, win)


def ssim_global(a, b):
    """SSIM with a single full-image window; cheap, used on large batches."""
    if a.width != b.width or a.height != b.height:
        raise ValueError("SSIM inputs must have identical size")
    pa, pb = to_luma(a), to_luma(b)
    n = float(a.width * a.height)
    ma = sum(pa) / n
    mb = sum(pb) / n
    va = sum((v - ma) ** 2 for v in pa) / n
    vb = sum((v - mb) ** 2 for v in pb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(pa, pb)) / n
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    return ((2 * ma * mb + c1) * (2 * cov + c2)
            / ((ma * ma + mb * mb + c1) * (va + vb + c2)))


def edge_energy(image):
    """Mean absolute horizontal+vertical luma gradient (sharpness proxy)."""
    w, h = image.width, image.height
    p = to_luma(image)
    total = 0
    count = 0
    for y in range(h):
        row = y * w
        for x in range(w - 1):
            total += abs(p[row + x + 1] - p[row + x])
            count += 1
    for y in range(h - 1):
        for x in range(w):
            total += abs(p[(y + 1) * w + x] - p[y * w + x])
            count += 1
    return total / count if count else 0.0

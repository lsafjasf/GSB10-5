"""Pure-stdlib image denoising library (grayscale, 8-bit).

Images are flat lists of ints (0..255), row-major, with explicit width/height.
No third-party dependencies: PGM I/O, noise synthesis, three denoising
filters, noise-level estimation and paired quality metrics.
"""

import math
import random


# ---------------------------------------------------------------- PGM I/O

def read_pgm(path):
    """Read a PGM (P2 or P5) file -> (pixels, width, height, maxval)."""
    with open(path, "rb") as fh:
        data = fh.read()
    tokens = []
    i = 0
    # header: magic, width, height, maxval  (comments start with '#')
    while len(tokens) < 4:
        while i < len(data) and data[i:i+1].isspace():
            i += 1
        if data[i:i+1] == b"#":
            while i < len(data) and data[i] != 0x0A:
                i += 1
            continue
        j = i
        while j < len(data) and not data[j:j+1].isspace():
            j += 1
        tokens.append(data[i:j])
        i = j
    magic = tokens[0]
    w, h, maxval = int(tokens[1]), int(tokens[2]), int(tokens[3])
    i += 1  # single whitespace after maxval
    if magic == b"P5":
        if maxval < 256:
            pix = list(data[i:i + w * h])
        else:
            pix = [(data[k] << 8) | data[k + 1]
                   for k in range(i, i + 2 * w * h, 2)]
    elif magic == b"P2":
        body = data[i:].split()
        pix = [int(t) for t in body[: w * h]]
    else:
        raise ValueError("not a PGM file: %r" % magic)
    if len(pix) != w * h:
        raise ValueError("PGM size mismatch")
    return pix, w, h, maxval


def write_pgm(path, pix, w, h, maxval=255):
    """Write 8-bit binary PGM (P5)."""
    header = b"P5\n%d %d\n%d\n" % (w, h, maxval)
    body = bytes(max(0, min(maxval, int(round(v)))) for v in pix)
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(body)


# ------------------------------------------------------- synthetic images

def make_image(w, h, kind):
    """Clean test scenes: flat / edges / texture / gradient."""
    pix = [0] * (w * h)
    if kind == "flat":
        return [128] * (w * h), w, h
    if kind == "edges":
        # strong step edges: vertical + horizontal steps and a bright square
        for y in range(h):
            for x in range(w):
                v = 40 if x < w // 3 else (220 if x < 2 * w // 3 else 90)
                if y >= h // 2:
                    v = 255 - v
                pix[y * w + x] = v
        x0, y0, s = w // 4, h // 4, w // 4
        for y in range(y0, y0 + s):
            for x in range(x0, x0 + s):
                pix[y * w + x] = 250
        return pix, w, h
    if kind == "texture":
        # fine 2px checker + low-freq sinusoid: detail at Nyquist-ish scale
        for y in range(h):
            sy = math.sin(y * 2 * math.pi / max(8, h // 4))
            for x in range(w):
                checker = 255 if (x // 2 + y // 2) % 2 else 0
                v = 0.65 * checker + 0.35 * (127 + 90 *
                        math.sin(x * 2 * math.pi / max(8, w // 4)) * sy)
                pix[y * w + x] = max(0, min(255, int(round(v))))
        return pix, w, h
    if kind == "gradient":
        for y in range(h):
            for x in range(w):
                pix[y * w + x] = int(round(255 * x / max(1, w - 1)))
        return pix, w, h
    raise ValueError("unknown scene kind: %r" % kind)


# ------------------------------------------------------------ noise synth

def add_gaussian_noise(pix, sigma, seed=0):
    rng = random.Random(seed)
    return [max(0, min(255, int(round(v + rng.gauss(0.0, sigma)))))
            for v in pix]


def add_impulse_noise(pix, amount, seed=0):
    """Salt & pepper: fraction `amount` of pixels replaced by 0 or 255."""
    rng = random.Random(seed)
    out = list(pix)
    for i in range(len(out)):
        if rng.random() < amount:
            out[i] = 255 if rng.random() < 0.5 else 0
    return out


# ------------------------------------------------------- noise estimation

def estimate_sigma(pix, w, h):
    """Robust noise sigma from MAD of horizontal neighbour differences.

    In flat noisy areas d = p[x+1]-p[x] ~ N(0, 2*sigma^2), so
    sigma ~= median(|d|) / (0.6745 * sqrt(2)).  Edges contribute only a
    small fraction of differences and are rejected by the median.
    """
    diffs = []
    for y in range(h):
        row = y * w
        for x in range(w - 1):
            diffs.append(abs(pix[row + x + 1] - pix[row + x]))
    diffs.sort()
    mad = diffs[len(diffs) // 2]
    return mad / (0.6745 * math.sqrt(2.0))


# ---------------------------------------------------------------- filters

def box_filter(pix, w, h, radius):
    """Mean over a (2r+1)^2 window via an integral image: O(1)/pixel."""
    iw, ih = w + 1, h + 1
    integ = [0] * (iw * ih)
    for y in range(h):
        rowsum = 0
        base_i, base_o = y * w, (y + 1) * iw
        prev_o = y * iw
        for x in range(w):
            rowsum += pix[base_i + x]
            integ[base_o + x + 1] = integ[prev_o + x + 1] + rowsum
    out = [0] * (w * h)
    for y in range(h):
        y0 = max(0, y - radius)
        y1 = min(h - 1, y + radius)
        top, bot = y0 * iw, (y1 + 1) * iw
        base_o = y * w
        for x in range(w):
            x0 = max(0, x - radius)
            x1 = min(w - 1, x + radius)
            s = (integ[bot + x1 + 1] - integ[top + x1 + 1]
                 - integ[bot + x0] + integ[top + x0])
            out[base_o + x] = s // ((y1 - y0 + 1) * (x1 - x0 + 1))
    return out


def median_filter(pix, w, h, radius):
    """Median over a (2r+1)^2 window.  Strong against impulse noise."""
    out = [0] * (w * h)
    for y in range(h):
        y0, y1 = max(0, y - radius), min(h - 1, y + radius)
        base_o = y * w
        for x in range(w):
            x0, x1 = max(0, x - radius), min(w - 1, x + radius)
            win = []
            for yy in range(y0, y1 + 1):
                base = yy * w
                win.extend(pix[base + x0: base + x1 + 1])
            win.sort()
            out[base_o + x] = win[len(win) // 2]
    return out


def selective_mean(pix, w, h, radius, threshold):
    """Edge-aware mean: average only neighbours within `threshold` of centre.

    Rationale: in flat noisy regions neighbour-centre differences follow
    N(0, 2*sigma^2), so |d| > 3*sigma*sqrt(2) is unlikely (~0.3%); across
    a true edge a whole side of the window deviates consistently and is
    excluded, so the edge is not blurred.  threshold = c * sigma_est with
    c ~ 2.5-3.5 works well in practice.
    """
    out = [0] * (w * h)
    for y in range(h):
        y0, y1 = max(0, y - radius), min(h - 1, y + radius)
        base_o = y * w
        for x in range(w):
            x0, x1 = max(0, x - radius), min(w - 1, x + radius)
            c = pix[base_o + x]
            acc, n = 0, 0
            for yy in range(y0, y1 + 1):
                base = yy * w
                for xx in range(x0, x1 + 1):
                    v = pix[base + xx]
                    if abs(v - c) <= threshold:
                        acc += v
                        n += 1
            out[base_o + x] = acc // n
    return out


# ---------------------------------------------------------------- metrics

def mse(a, b, mask=None):
    """Mean squared error; optional boolean mask restricts the region."""
    if mask is None:
        s = 0
        for i in range(len(a)):
            d = a[i] - b[i]
            s += d * d
        return s / len(a)
    s, n = 0, 0
    for i in range(len(a)):
        if mask[i]:
            d = a[i] - b[i]
            s += d * d
            n += 1
    return s / max(1, n)


def psnr(a, b, mask=None):
    m = mse(a, b, mask)
    if m == 0:
        return float("inf")
    return 10.0 * math.log10(255.0 * 255.0 / m)


def gradient_mag(pix, w, h):
    """|Gx| + |Gy| with forward differences (interior pixels only)."""
    g = [0] * (w * h)
    for y in range(h - 1):
        base, nxt = y * w, (y + 1) * w
        for x in range(w - 1):
            g[base + x] = (abs(pix[base + x + 1] - pix[base + x])
                           + abs(pix[nxt + x] - pix[base + x]))
    return g


def edge_mask(clean, w, h, thresh=40):
    """True where the *clean* image has strong local structure (edges/texture)."""
    g = gradient_mag(clean, w, h)
    return [v >= thresh for v in g]


def gradient_energy_ratio(denoised, clean, w, h, mask=None):
    """Sum(|grad denoised|) / Sum(|grad clean|) over `mask` (detail retention).

    1.0 = perfect retention, <1 = detail lost to smoothing, >1 = leftover
    noise or overshoot.
    """
    gd, gc = gradient_mag(denoised, w, h), gradient_mag(clean, w, h)
    sd = sc = 0
    for i in range(len(gd)):
        if mask is None or mask[i]:
            sd += gd[i]
            sc += gc[i]
    return sd / sc if sc else float("nan")


def paired_metrics(noisy, denoised, clean, w, h, edge_thresh=40):
    """Noise side + detail side, always reported together.

    Returns dict with PSNR over flat regions (noise suppression) and over
    edge/texture regions plus gradient-energy ratio (detail preservation).
    """
    emask = edge_mask(clean, w, h, edge_thresh)
    fmask = [not m for m in emask]
    return {
        "flat_psnr_before": psnr(noisy, clean, fmask),
        "flat_psnr_after": psnr(denoised, clean, fmask),
        "edge_psnr_before": psnr(noisy, clean, emask),
        "edge_psnr_after": psnr(denoised, clean, emask),
        "grad_ratio": gradient_energy_ratio(denoised, clean, w, h, emask),
    }

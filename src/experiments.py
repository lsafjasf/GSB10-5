"""Quality experiments: paired noise/detail metrics across 4 scenarios.

Covers light noise, heavy noise, fine texture, strong edges (the edge
scenes) and impulse noise.  Writes ../results.md and sample .pgm images.

Selective-mean thresholds are swept as multiples of the *true* synthetic
noise sigma so the parameter study is clean; the robust sigma estimate is
printed alongside to show where the estimator is trustworthy.
"""

import os

import denoise as dn

SIZE = 128
SAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "samples")

SCENARIOS = [
    # name, scene, noise kind, noise level, true sigma (None for impulse)
    ("light_noise",   "edges",   "gaussian", 10.0, 10.0),
    ("heavy_noise",   "edges",   "gaussian", 45.0, 45.0),
    ("fine_texture",  "texture", "gaussian", 18.0, 18.0),
    ("impulse+edges", "edges",   "impulse",  0.12, None),
]


def make_noisy(clean, kind, level, seed):
    if kind == "gaussian":
        return dn.add_gaussian_noise(clean, level, seed)
    return dn.add_impulse_noise(clean, level, seed)


def dilate(mask, w, h):
    out = list(mask)
    for y in range(h):
        for x in range(w):
            if mask[y * w + x]:
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        xx, yy = x + dx, y + dy
                        if 0 <= xx < w and 0 <= yy < h:
                            out[yy * w + xx] = True
    return out


def variants(noisy, w, h, sigma_true):
    out = []
    for r in (1, 2, 3):
        out.append(("box r=%d (%dx%d)" % (r, 2 * r + 1, 2 * r + 1),
                    dn.box_filter(noisy, w, h, r)))
    for r in (1, 2):
        out.append(("median r=%d (%dx%d)" % (r, 2 * r + 1, 2 * r + 1),
                    dn.median_filter(noisy, w, h, r)))
    if sigma_true is not None:
        for c in (1.5, 2.5, 3.5):
            t = c * sigma_true
            out.append(("selective r=2, T=%.1f*sigma (T=%d)" % (c, t),
                        dn.selective_mean(noisy, w, h, 2, t)))
    else:
        out.append(("selective r=2, T=30 (fixed)",
                    dn.selective_mean(noisy, w, h, 2, 30)))
    return out


def fmt(x):
    return "inf" if x == float("inf") else "%.2f" % x


def main():
    os.makedirs(SAMPLES_DIR, exist_ok=True)
    lines = ["# Denoising experiment results (128x128, 0-255)", ""]
    lines.append(
        "Paired metrics per method: **noise side** = PSNR over flat "
        "(non-edge) pixels; **detail side** = PSNR over edge/texture "
        "pixels, PSNR over the dilated edge band (edge neighbourhood), "
        "and gradient-energy ratio over edge pixels (1.0 = full "
        "retention, <1 = detail lost).")
    lines.append("")
    for name, scene, nkind, level, sigma_true in SCENARIOS:
        clean, w, h = dn.make_image(SIZE, SIZE, scene)
        noisy = make_noisy(clean, nkind, level, seed=42)
        sigma_est = dn.estimate_sigma(noisy, w, h)
        dn.write_pgm(os.path.join(SAMPLES_DIR, "%s_clean.pgm" % name),
                     clean, w, h)
        dn.write_pgm(os.path.join(SAMPLES_DIR, "%s_noisy.pgm" % name),
                     noisy, w, h)
        emask = dn.edge_mask(clean, w, h)
        band = dilate(emask, w, h)
        fmask = [not m for m in emask]

        def row(label, den):
            return "| %s | %s -> %s | %s -> %s | %s -> %s | %.3f |" % (
                label,
                fmt(dn.psnr(noisy, clean, fmask)),
                fmt(dn.psnr(den, clean, fmask)),
                fmt(dn.psnr(noisy, clean, emask)),
                fmt(dn.psnr(den, clean, emask)),
                fmt(dn.psnr(noisy, clean, band)),
                fmt(dn.psnr(den, clean, band)),
                dn.gradient_energy_ratio(den, clean, w, h, emask))

        lines.append("## %s (scene=%s, %s level=%s)" % (name, scene,
                                                        nkind, level))
        lines.append("")
        if sigma_true is not None:
            lines.append("sigma_true=%.1f, sigma_est(MAD)=%.1f"
                         % (sigma_true, sigma_est))
        else:
            lines.append("impulse noise: sigma_est(MAD)=%.1f "
                         "(Gaussian model does not apply)" % sigma_est)
        lines.append("")
        lines.append("| method | flat PSNR (dB) | edge PSNR (dB) "
                     "| edge-band PSNR (dB) | grad ratio |")
        lines.append("|---|---|---|---|---|")
        lines.append(row("(no denoise)", noisy))
        for label, den in variants(noisy, w, h, sigma_true):
            lines.append(row(label, den))
            if label.startswith(("box r=2", "median r=1",
                                 "selective r=2, T=2.5")):
                tag = label.split(",")[0].replace(" ", "_")
                dn.write_pgm(
                    os.path.join(SAMPLES_DIR, "%s_%s.pgm" % (name, tag)),
                    den, w, h)
        lines.append("")
    out_path = os.path.join(os.path.dirname(__file__), "..", "results.md")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("\nwritten:", os.path.abspath(out_path))


if __name__ == "__main__":
    main()

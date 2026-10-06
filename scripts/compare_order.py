"""Compare sharpen-before-downscale vs sharpen-after-downscale.

For each representative image:
  reference : Lanczos-3 low-pass downscale, no sharpening (derived directly
              from the original; the ground-truth thumbnail)
  first     : unsharp-mask the ORIGINAL, then low-pass downscale
  last      : low-pass downscale, then unsharp-mask the THUMBNAIL
              (pre-sharpen amount is calibrated so first and last reach the
              same apparent sharpness: equal gradient energy on luma)
  nearest   : no low-pass, nearest sampling (aliasing baseline)

Metrics (SSIM / PSNR / RMSE / MAE / clipped fraction) are measured against
the reference, i.e. against the original reduced with a near-ideal low-pass.
PNG artifacts are written under out/order/ for eyeballing.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import thumblib as t

OUTDIR = os.path.join(os.path.dirname(__file__), "..", "out", "order")


def _paint(img, fn):
    for y in range(img.height):
        base = y * img.width
        for x in range(img.width):
            r, g, b = fn(x, y)
            img.channels[0][base + x] = r
            img.channels[1][base + x] = g
            img.channels[2][base + x] = b


def photo_like(w, h):
    import math

    def fn(x, y):
        dx, dy = x - w * 0.35, y - h * 0.4
        circle = 1.0 if dx * dx + dy * dy < (h * 0.32) ** 2 else 0.0
        r = 30 + 180 * x / w + 40 * circle
        g = 20 + 150 * y / h + 60 * math.sin(x / 7.0)
        b = 80 + 120 * (1 - x / w) + 50 * circle
        n = (x * 7349 + y * 12583) % 11
        return tuple(max(0, min(255, int(v) + n - 5)) for v in (r, g, b))

    img = t.Img.new(w, h, "RGB")
    _paint(img, fn)
    return img


def fine_texture(w, h):
    def fn(x, y):
        v = 255 if ((x // 1) + (y // 1)) % 2 else 0
        if (x // 8 + y // 8) % 2:
            v = (x + y) % 256
        return v, 255 - v, (v // 2 + x) % 256

    img = t.Img.new(w, h, "RGB")
    _paint(img, fn)
    return img


def text_like(w, h):
    def fn(x, y):
        v = 245
        for k in range(1, 6):
            yy = h * k // 6
            if yy <= y <= yy + max(2, h // 30):
                v = 30 if (x // 3) % 2 else 240
        for k in range(4):
            xx0, yy0 = w * (0.15 + 0.22 * k), h // 12
            if (xx0 <= x < xx0 + w // 10 and yy0 <= y < yy0 + h // 6):
                v = 20
        return v, v, v

    img = t.Img.new(w, h, "RGB")
    _paint(img, fn)
    return img


def banner(w, h):
    def fn(x, y):
        v = (x // 2) % 2 * 255 if y >= h // 2 else x * 255 // w
        return v, 255 - v, (x * 3 + y * 7) % 256

    img = t.Img.new(w, h, "RGB")
    _paint(img, fn)
    return img


CASES = [
    ("photo_like", photo_like(300, 220), (120, 120)),
    ("fine_texture", fine_texture(300, 220), (120, 120)),
    ("text_like", text_like(300, 220), (120, 120)),
    ("extreme_banner", banner(1600, 24), (120, 120)),
]


def calibrate_first_amount(img, box, sigma, target_energy, tol=0.01):
    lo, hi = 0.0, 16.0
    best = None
    for _ in range(22):
        mid = (lo + hi) / 2
        th, _ = t.make_thumbnail(img, box[0], box[1], order="sharpen_first",
                                 pre_sharpen_sigma=sigma,
                                 pre_sharpen_amount=mid)
        e = t.gradient_energy(th)
        best = (th, mid, e)
        if abs(e - target_energy) <= tol * target_energy:
            break
        if e < target_energy:
            lo = mid
        else:
            hi = mid
    return best


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    rows = []
    for name, img, box in CASES:
        ref = t.make_thumbnail(img, box[0], box[1], order="none")[0]
        last, _ = t.make_thumbnail(img, box[0], box[1], order="sharpen_last",
                                   sharpen_sigma=0.8, sharpen_amount=0.5)
        first, first_amount, first_e = calibrate_first_amount(
            img, box, sigma=1.0, target_energy=t.gradient_energy(last))
        near = t.resize(img, ref.width, ref.height, method="nearest")

        t.save_png(img, os.path.join(OUTDIR, name + "_original.png"))
        t.save_png(ref, os.path.join(OUTDIR, name + "_ref_none.png"))
        t.save_png(last, os.path.join(OUTDIR, name + "_sharpen_last.png"))
        t.save_png(first, os.path.join(OUTDIR, name + "_sharpen_first.png"))

        first_same, _ = t.make_thumbnail(img, box[0], box[1],
                                         order="sharpen_first",
                                         pre_sharpen_sigma=1.0,
                                         pre_sharpen_amount=0.5)
        for label, cand, amount in (
                ("none(ref)", ref, 0.0),
                ("sharpen_last", last, 0.5),
                ("first(same amt)", first_same, 0.5),
                ("first(matched)", first, first_amount),
                ("nearest", near, float("nan"))):
            r = t.score_against_reference(cand, ref)
            r["case"] = name
            r["variant"] = label
            r["amount"] = amount
            rows.append(r)

    hdr = ("| image | variant | amount | SSIM | PSNR(dB) | RMSE | MAE | "
           "clip% | grad-energy |")
    print(hdr)
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        print("| {case} | {variant} | {amount:.3g} | {ssim:.5f} | "
              "{psnr:.2f} | {rmse:.3f} | {mae:.3f} | {clip:.2f} | "
              "{ge:.2f} |".format(
                  case=r["case"], variant=r["variant"], amount=r["amount"],
                  ssim=r["ssim"], psnr=r["psnr"], rmse=r["rmse"],
                  mae=r["mae"], clip=100 * r["clip_frac"],
                  ge=r["grad_energy"]))
    print("\nartifacts: %s" % os.path.relpath(OUTDIR))


if __name__ == "__main__":
    main()

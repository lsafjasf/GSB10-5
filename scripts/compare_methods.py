"""Resampler comparison, scored directly against the ORIGINAL image:
each thumbnail is upscaled back to the original resolution (triangle) and
compared with the original via SSIM / PSNR / RMSE. This is the strict
"compare with the original" protocol; it measures end-to-end information
preservation of the whole downscale(+sharpen) chain.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import thumblib as t
from compare_order import CASES


def main():
    rows = []
    for name, img, box in CASES:
        ow, oh = t.fit_size(img.width, img.height, box[0], box[1])
        for method in ("lanczos", "area", "triangle", "nearest"):
            for order in ("none", "sharpen_last"):
                th, _ = t.make_thumbnail(img, box[0], box[1], order=order,
                                         resample=method)
                up = t.resize(th, img.width, img.height, method="triangle")
                rows.append({
                    "case": name, "method": method, "order": order,
                    "ssim": t.ssim(up, img), "psnr": t.psnr(up, img),
                    "rmse": t.rmse(up, img),
                })
    print("| image | resampler | sharpen | SSIM vs original | PSNR(dB) | RMSE |")
    print("|---|---|---|---:|---:|---:|")
    for r in rows:
        print("| {case} | {method} | {order} | {ssim:.5f} | {psnr:.2f} | "
              "{rmse:.3f} |".format(**r))


if __name__ == "__main__":
    main()

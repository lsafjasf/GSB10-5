#!/usr/bin/env python3
"""Order / kernel / sizing experiments; emits results/experiments.{md,json}.

Run: python3 experiments.py
"""

import json
import os

from thumb.pngcodec import encode_png
from thumb.pipeline import make_thumbnail, fit_size, Policy
from thumb.synth import render, SCENES
from thumb import metrics

SRC_W, SRC_H = 512, 384
BOX = (160, 120)
SS = 6  # supersampling factor for ground-truth integration
AMOUNT = 0.8


def order_experiment():
    rows = []
    for name in ("texture", "photo", "edges"):
        scene = SCENES[name]
        src = render(scene, SRC_W, SRC_H, ss=1)
        tw, th = fit_size(SRC_W, SRC_H, *BOX)
        truth = render(scene, tw, th, ss=SS)  # box-integrated ground truth
        truth_edge = metrics.edge_energy(truth)
        for order, label in (("before", "sharpen -> resize"),
                             ("after", "resize -> sharpen"),
                             ("none", "resize only (baseline)")):
            out = make_thumbnail(src, *BOX, kernel="triangle",
                                 sharpen=order, amount=AMOUNT, threshold=2)
            rows.append({
                "scene": name,
                "pipeline": label,
                "ssim": metrics.ssim(out, truth),
                "psnr_db": metrics.psnr(out, truth),
                "mae": metrics.mae(out, truth),
                "edge_energy": metrics.edge_energy(out),
                "truth_edge_energy": truth_edge,
                "png_bytes": len(encode_png(out, level=6)),
            })
    return rows


def kernel_experiment():
    rows = []
    for name in ("texture", "photo", "edges"):
        scene = SCENES[name]
        src = render(scene, SRC_W, SRC_H, ss=1)
        tw, th = fit_size(SRC_W, SRC_H, *BOX)
        truth = render(scene, tw, th, ss=SS)
        for kernel in ("nearest", "box", "triangle", "lanczos3"):
            out = make_thumbnail(src, *BOX, kernel=kernel, sharpen=None)
            rows.append({
                "scene": name,
                "kernel": kernel,
                "ssim": metrics.ssim(out, truth),
                "psnr_db": metrics.psnr(out, truth),
                "mae": metrics.mae(out, truth),
            })
    return rows


def sizing_examples():
    p = Policy(min_side=8)
    cases = [
        (512, 384, 160, 120, "normal 4:3 -> 160x120 box"),
        (2000, 100, 160, 160, "ultra-wide 20:1"),
        (120, 2400, 160, 160, "ultra-tall 1:20"),
        (4000, 8, 256, 256, "very long ribbon 500:1"),
        (8, 4000, 128, 128, "vertical ribbon 1:500"),
        (512, 384, 1, 1, "explicit 1x1 target"),
        (512, 384, 3, 2, "explicit 3x2 target"),
        (512, 384, 8, 8, "explicit 8x8 target"),
    ]
    rows = []
    for w, h, bw, bh, desc in cases:
        dw, dh = fit_size(w, h, bw, bh, p)
        src_ar = w / h
        out_ar = dw / dh
        rows.append({
            "case": desc,
            "src": "%dx%d" % (w, h),
            "box": "%dx%d" % (bw, bh),
            "out": "%dx%d" % (dw, dh),
            "aspect_delta_pct": 100.0 * abs(src_ar - out_ar) / src_ar,
        })
    return rows


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    rdir = os.path.join(here, "results")
    os.makedirs(rdir, exist_ok=True)
    order_rows = order_experiment()
    kernel_rows = kernel_experiment()
    size_rows = sizing_examples()

    lines = ["# Experiment results", "",
             "Source %dx%d, box %dx%d, unsharp amount %.1f, threshold 2; ground truth "
             "= same analytic scene rendered at output size with %dx%d box "
             "supersampling." % (SRC_W, SRC_H, BOX[0], BOX[1], AMOUNT, SS, SS),
             "", "## Sharpen order vs ground truth", "",
             "| scene | pipeline | SSIM | PSNR(dB) | MAE | edge energy | PNG bytes |",
             "|---|---|---|---|---|---|---|"]
    for r in order_rows:
        lines.append("| %s | %s | %.4f | %.2f | %.2f | %.2f (GT %.2f) | %d |"
                     % (r["scene"], r["pipeline"], r["ssim"], r["psnr_db"],
                        r["mae"], r["edge_energy"], r["truth_edge_energy"],
                        r["png_bytes"]))
    lines += ["", "## Resampling kernel vs ground truth (no sharpen)", "",
              "| scene | kernel | SSIM | PSNR(dB) | MAE |",
              "|---|---|---|---|---|"]
    for r in kernel_rows:
        lines.append("| %s | %s | %.4f | %.2f | %.2f |"
                     % (r["scene"], r["kernel"], r["ssim"], r["psnr_db"],
                        r["mae"]))
    lines += ["", "## Deterministic sizing (min_side=8, hard_max=8192)", "",
              "| case | src | box | output | aspect delta % |",
              "|---|---|---|---|---|"]
    for r in size_rows:
        lines.append("| %s | %s | %s | %s | %.3f |"
                     % (r["case"], r["src"], r["box"], r["out"],
                        r["aspect_delta_pct"]))
    report = "\n".join(lines) + "\n"
    with open(os.path.join(rdir, "experiments.md"), "w") as fh:
        fh.write(report)
    with open(os.path.join(rdir, "experiments.json"), "w") as fh:
        json.dump({"order": order_rows, "kernels": kernel_rows,
                   "sizing": size_rows}, fh, indent=2)
    print(report)


if __name__ == "__main__":
    main()

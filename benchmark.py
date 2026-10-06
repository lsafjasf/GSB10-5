#!/usr/bin/env python3
"""10k-image throughput + sampled quality benchmark.

Run: python3 benchmark.py [n_images]

Timing covers generation of the thumbnail from an in-memory source
(low-pass resize + unsharp mask), i.e. the steady-state hot path of a
thumbnail service; PNG encode is excluded and measured separately on a
sample. Quality is checked on every 50th image against a lanczos3
resample of the same source (the best reference this library can
produce from the original), using global SSIM and PSNR.
"""

import json
import os
import random
import sys
import time

from thumb.pipeline import make_thumbnail
from thumb.pngcodec import encode_png
from thumb.synth import tile_image
from thumb import metrics

BOXES = [(128, 128), (160, 90), (96, 96), (64, 64)]
KINDS = ["photo", "texture", "edges", "noise"]


def gen_case(rng, i):
    roll = rng.random()
    if roll < 0.55:      # normal aspect photos/textures
        w = rng.randint(96, 320)
        h = rng.randint(72, 240)
        kind = rng.choice(["photo", "photo", "texture", "edges"])
    elif roll < 0.8:     # fine texture / noise stress
        w = rng.randint(128, 320)
        h = rng.randint(96, 240)
        kind = rng.choice(["texture", "noise"])
    else:                # extreme aspect ratios
        long_ = rng.randint(240, 960)
        short = rng.randint(8, 48)
        if rng.random() < 0.5:
            w, h = long_, short
        else:
            w, h = short, long_
        kind = rng.choice(["photo", "texture"])
    img = tile_image(kind, w, h, seed=1000 + i)
    box = rng.choice(BOXES)
    return img, box


def percentile(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10000
    rng = random.Random(370)
    times = []
    quality = []
    encode_times = []
    t_start = time.perf_counter()
    for i in range(n):
        img, box = gen_case(rng, i)
        t0 = time.perf_counter()
        out = make_thumbnail(img, *box, kernel="triangle",
                             sharpen="after", amount=0.6)
        times.append(time.perf_counter() - t0)
        if i % 50 == 0:
            ref = make_thumbnail(img, *box, kernel="lanczos3",
                                 sharpen=None)
            quality.append({
                "ssim_global": metrics.ssim_global(out, ref),
                "psnr_db": metrics.psnr(out, ref),
            })
        if i % 200 == 0:
            t0 = time.perf_counter()
            encode_png(out)
            encode_times.append(time.perf_counter() - t0)
    total = time.perf_counter() - t_start
    times_ms = sorted(t * 1000.0 for t in times)
    mean_ms = sum(times_ms) / len(times_ms)
    var = sum((t - mean_ms) ** 2 for t in times_ms) / len(times_ms)
    ssim_vals = sorted(q["ssim_global"] for q in quality)
    psnr_vals = sorted(q["psnr_db"] for q in quality)

    summary = {
        "images": n,
        "total_wall_s": round(total, 3),
        "per_image_ms": {
            "mean": round(mean_ms, 3),
            "std": round(var ** 0.5, 3),
            "min": round(times_ms[0], 3),
            "p50": round(percentile(times_ms, 0.50), 3),
            "p90": round(percentile(times_ms, 0.90), 3),
            "p95": round(percentile(times_ms, 0.95), 3),
            "p99": round(percentile(times_ms, 0.99), 3),
            "max": round(times_ms[-1], 3),
        },
        "quality_sample_n": len(quality),
        "quality_vs_lanczos3_ref": {
            "ssim_global_mean": round(sum(ssim_vals) / len(ssim_vals), 4),
            "ssim_global_p05": round(percentile(ssim_vals, 0.05), 4),
            "psnr_db_mean": round(sum(psnr_vals) / len(psnr_vals), 2),
            "psnr_db_p05": round(percentile(psnr_vals, 0.05), 2),
        },
        "png_encode_ms_mean": round(
            1000.0 * sum(encode_times) / len(encode_times), 3),
    }
    here = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(os.path.join(here, "results"), exist_ok=True)
    with open(os.path.join(here, "results", "benchmark.json"), "w") as fh:
        json.dump(summary, fh, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

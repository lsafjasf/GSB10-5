"""Benchmark: 10,000 images, wall time + peak memory (stdlib only).

Usage: python3 benchmarks/bench_10k.py [--n 10000] [--report reports/benchmark_10k.json]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import resource
import statistics
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from smartcrop.analysis import analyze
from smartcrop.cropper import auto_crop
from smartcrop.image import Image

RATIOS = (1.0, 4 / 3, 16 / 9, 9 / 16)


def _template(rng, width, height, kind):
    if kind == "flat":
        return Image(width, height, bytes([rng.randint(0, 255)]) * (width * height))
    if kind == "noise":
        return Image(width, height,
                     bytes(rng.randint(0, 255) for _ in range(width * height)))
    # subject image: flat bg + textured rect at a random place (may touch edges)
    bg = rng.randint(90, 160)
    buf = bytearray(bytes([bg]) * (width * height))
    sw = rng.randint(width // 6, width // 2)
    sh = rng.randint(height // 6, height // 2)
    x0 = rng.randint(0, width - sw)
    y0 = rng.randint(0, height - sh)
    val = rng.randint(0, 255)
    for y in range(y0, y0 + sh):
        base = y * width + x0
        for x in range(sw):
            buf[base + x] = (val + rng.randint(0, 60)) & 0xFF
    # bright frame for strong edges
    for t in range(2):
        for x in range(x0, x0 + sw):
            buf[(y0 + t) * width + x] = 255
            buf[(y0 + sh - 1 - t) * width + x] = 255
        for y in range(y0, y0 + sh):
            buf[y * width + x0 + t] = 255
            buf[y * width + x0 + sw - 1 - t] = 255
    return Image(width, height, bytes(buf))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10000)
    ap.add_argument("--width", type=int, default=256)
    ap.add_argument("--height", type=int, default=192)
    ap.add_argument("--report", default=os.path.join(
        os.path.dirname(__file__), "..", "reports", "benchmark_10k.json"))
    args = ap.parse_args()

    rng = random.Random(20261005)
    kinds = ["subject", "subject", "subject", "noise", "flat"]
    templates = [_template(rng, args.width, args.height, rng.choice(kinds))
                 for _ in range(64)]
    found_count = 0
    fallback_count = 0
    bounds_violations = 0
    per_image_ms = []

    t0 = time.perf_counter()
    for i in range(args.n):
        img = templates[i % len(templates)]
        ratio = RATIOS[i % len(RATIOS)]
        ta = time.perf_counter()
        res = auto_crop(img, ratio)
        per_image_ms.append((time.perf_counter() - ta) * 1000.0)
        if res.subject_found:
            found_count += 1
        if res.mode == "center-fallback":
            fallback_count += 1
        if res.mode != "reject" and not res.bounds_check["all_inside"]:
            bounds_violations += 1
    wall = time.perf_counter() - t0
    peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

    # python-level allocation peak on a 1000-image sub-run (tracemalloc is slow)
    tracemalloc.start()
    for i in range(min(1000, args.n)):
        auto_crop(templates[i % len(templates)], RATIOS[i % len(RATIOS)])
    _, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    per_image_ms.sort()
    report = {
        "n_images": args.n,
        "image_size": [args.width, args.height],
        "ratios": ["1:1", "4:3", "16:9", "9:16"],
        "wall_time_s": round(wall, 3),
        "per_image_ms": {
            "mean": round(statistics.fmean(per_image_ms), 3),
            "p50": round(per_image_ms[len(per_image_ms) // 2], 3),
            "p95": round(per_image_ms[int(len(per_image_ms) * 0.95)], 3),
            "p99": round(per_image_ms[int(len(per_image_ms) * 0.99)], 3),
            "max": round(per_image_ms[-1], 3),
        },
        "throughput_img_per_s": round(args.n / wall, 1),
        "memory": {
            "peak_rss_mb": round(peak_rss_mb, 1),
            "python_peak_alloc_mb": round(py_peak / 1024 / 1024, 1),
        },
        "subject_found": found_count,
        "center_fallback": fallback_count,
        "bounds_violations": bounds_violations,
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }
    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    with open(args.report, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if bounds_violations == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

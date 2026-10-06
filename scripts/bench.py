"""Benchmark: 10,000 synthetic images through the production pipeline
(fit_size -> Lanczos-3 downscale -> unsharp mask -> PNG encode).

Per-image wall time of the pipeline (generation excluded) is recorded;
images are generated deterministically from their index so any slow case
can be reproduced. Work is fanned out with multiprocessing (stdlib).

Usage: python3 scripts/bench.py [count] [workers]
"""

import math
import os
import random
import statistics
import sys
import time
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import thumblib as t

BOX = (160, 160)


def make_spec(i):
    rng = random.Random(i * 2654435761 % (2 ** 32))
    long_edge = int(64 * (10.0 ** (rng.random() * 1.0)))
    aspect = 1.0 / (10.0 ** (rng.random() * 1.6021))
    if rng.random() < 0.5:
        w, h = long_edge, max(1, round(long_edge * aspect))
    else:
        w, h = max(1, round(long_edge * aspect)), long_edge
    content = ("gradient", "noise", "checker", "blobs")[i % 4]
    mode = "L" if i % 5 == 4 else "RGB"
    return w, h, content, mode


def gen_image(w, h, content, mode, seed):
    rng = random.Random(seed)
    n = w * h
    if content == "noise":
        planes = [bytearray(rng.randbytes(n)) for _ in range(3)]
    elif content == "gradient":
        row = bytes((x * 255 // max(1, w - 1)) for x in range(w))
        planes = [bytearray(row * h),
                  bytearray().join(bytearray([y * 255 // max(1, h - 1)]) * w
                                   for y in range(h)),
                  bytearray(row[::-1] * h)]
    elif content == "checker":
        p = 1 + seed % 3
        cell0 = bytes([0]) * p
        cell1 = bytes([255]) * p
        row_a = ((cell0 + cell1) * (w // (2 * p) + 1))[:w]
        row_b = ((cell1 + cell0) * (w // (2 * p) + 1))[:w]
        plane = bytearray().join(
            row_a if (y // p) % 2 == 0 else row_b for y in range(h))
        planes = [bytearray(plane), bytearray(plane[::-1]), bytearray(plane)]
    else:
        row = bytes(((x * x // 7 + x) % 256) for x in range(w))
        plane = bytearray().join(
            row[(y * 13) % w:] + row[:(y * 13) % w] for y in range(h))
        planes = [bytearray(plane),
                  bytearray(plane[::-1]),
                  bytearray((v // 2 + 64) % 256 for v in plane)]
    if mode == "L":
        planes = planes[:1]
    return t.Img(w, h, mode, planes)


def run_one(i):
    w, h, content, mode = make_spec(i)
    img = gen_image(w, h, content, mode, i)
    t0 = time.perf_counter()
    thumb, info = t.make_thumbnail(img, BOX[0], BOX[1], order="sharpen_last")
    payload = t.encode_png(thumb, level=6)
    ms = (time.perf_counter() - t0) * 1000.0
    assert min(thumb.width, thumb.height) >= min(4, min(w, h)), (w, h)
    return {"ms": ms, "in": (w, h), "out": (thumb.width, thumb.height),
            "content": content, "bytes": len(payload)}


def pct(sorted_vals, q):
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * q
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 10000
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else max(1, os.cpu_count() - 2)
    print("images=%d workers=%d box=%dx%d pipeline=lanczos+unsharp(0.8,0.5)+png"
          % (count, workers, BOX[0], BOX[1]))
    wall0 = time.perf_counter()
    results = []
    with Pool(workers) as pool:
        for rec in pool.imap_unordered(run_one, range(count), chunksize=16):
            results.append(rec)
    wall = time.perf_counter() - wall0

    times = sorted(r["ms"] for r in results)
    cpu_total = sum(times) / 1000.0
    print("\n== timing (per-image pipeline, ms) ==")
    print("total wall: %.1f s   total CPU: %.1f s   throughput: %.0f img/s (wall)"
          % (wall, cpu_total, count / wall))
    print("mean %.2f | median %.2f | p90 %.2f | p95 %.2f | p99 %.2f | "
          "min %.2f | max %.2f"
          % (statistics.fmean(times), pct(times, 0.5), pct(times, 0.9),
             pct(times, 0.95), pct(times, 0.99), times[0], times[-1]))

    buckets = [0, 1, 2, 5, 10, 20, 50, 100, 200, 500, float("inf")]
    labels = ["<1", "1-2", "2-5", "5-10", "10-20", "20-50", "50-100",
              "100-200", "200-500", ">=500"]
    print("\n== histogram (ms) ==")
    for lo, hi, label in zip(buckets, buckets[1:], labels):
        n = sum(1 for v in times if lo <= v < hi)
        bar = "#" * int(60 * n / count)
        print("%8s ms : %6d (%5.1f%%) %s" % (label, n, 100 * n / count, bar))

    print("\n== by content ==")
    for content in ("gradient", "noise", "checker", "blobs"):
        sub = sorted(r["ms"] for r in results if r["content"] == content)
        if sub:
            print("%-9s n=%4d mean %.2f ms  p95 %.2f ms"
                  % (content, len(sub), statistics.fmean(sub),
                     pct(sub, 0.95)))

    print("\n== slowest 5 ==")
    for r in sorted(results, key=lambda r: -r["ms"])[:5]:
        print("%8.2f ms  in=%dx%d out=%dx%d %s"
              % (r["ms"], r["in"][0], r["in"][1], r["out"][0], r["out"][1],
                 r["content"]))
    sizes = sorted(r["bytes"] for r in results)
    print("\noutput PNG bytes: median %d  p95 %d  max %d"
          % (pct(sizes, 0.5), pct(sizes, 0.95), sizes[-1]))


if __name__ == "__main__":
    main()

"""Benchmark 1000 stitch runs.

Run:  python3 benchmark.py [N]        (default N=1000)

All 1000 pairs are generated deterministically (fixed seed), varying offset,
exposure gain and subject position.  Only stitch() itself is timed.
"""

import json
import os
import random
import statistics
import sys
import time

from seamstitch.stitch import stitch
from seamstitch import synth

N = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
H, VIEW_W, SCENE_W = 64, 160, 320


def gen_pairs(n):
    rng = random.Random(20261006)
    pairs = []
    for _ in range(n):
        scene = synth.gradient_scene(SCENE_W, H)
        synth.add_noise(scene, 3, seed=rng.randint(0, 1 << 30))
        if rng.random() < 0.8:
            cx = rng.randint(40, SCENE_W - 40)
            r = rng.randint(6, 14)
            color = rng.choice([(10, 10, 10), (245, 245, 245), (200, 30, 30)])
            synth.add_disc(scene, cx, rng.randint(r, H - r), r, color)
        offset = rng.randint(40, VIEW_W - 12)          # overlap 12..120
        gain = rng.choice([1.0, 0.8, 1.25, 1.5, 0.7]) if rng.random() < 0.5 else 1.0
        left, right = synth.make_pair(scene, VIEW_W, offset, gain_right=gain)
        pairs.append((left, right, offset))
    return pairs


def percentile(sorted_vals, q):
    idx = min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


def main():
    pairs = gen_pairs(N)
    status_count = {}
    crossings_total = 0
    times_ms = []
    t0 = time.perf_counter()
    for left, right, offset in pairs:
        s = time.perf_counter()
        res = stitch(left, right, offset)
        times_ms.append((time.perf_counter() - s) * 1000.0)
        status_count[res.status] = status_count.get(res.status, 0) + 1
        crossings_total += res.crossings_pixels
    total_s = time.perf_counter() - t0

    ts = sorted(times_ms)
    hist_edges = [0.0, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0, 1e9]
    hist = [0] * (len(hist_edges) - 1)
    for v in ts:
        for i in range(len(hist_edges) - 1):
            if hist_edges[i] <= v < hist_edges[i + 1]:
                hist[i] += 1
                break
    report = {
        "runs": N,
        "image_size": f"{VIEW_W}x{H}",
        "overlap_range_px": [12, 120],
        "total_seconds": round(total_s, 4),
        "per_run_ms": {
            "mean": round(statistics.fmean(ts), 4),
            "stdev": round(statistics.pstdev(ts), 4),
            "min": round(ts[0], 4),
            "p50": round(percentile(ts, 0.50), 4),
            "p90": round(percentile(ts, 0.90), 4),
            "p95": round(percentile(ts, 0.95), 4),
            "p99": round(percentile(ts, 0.99), 4),
            "max": round(ts[-1], 4),
        },
        "histogram_ms": {
            "[0,1)": hist[0], "[1,2)": hist[1], "[2,3)": hist[2],
            "[3,5)": hist[3], "[5,8)": hist[4], "[8,12)": hist[5],
            "[12,20)": hist[6], "[20,inf)": hist[7],
        },
        "status_counts": status_count,
        "total_subject_crossing_pixels": crossings_total,
    }
    os.makedirs("out", exist_ok=True)
    with open("out/benchmark.json", "w") as fh:
        json.dump(report, fh, indent=1)
    print(json.dumps(report, indent=1))
    print(f"\nwrote out/benchmark.json")


if __name__ == "__main__":
    main()

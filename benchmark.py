"""基准：1000 组随机拼接，统计总耗时与单组耗时分布。

运行：python3 benchmark.py
输出：out/benchmark_report.json + 控制台分布表。
"""

import json
import math
import os
import random
import time

from seamstitch import Image, stitch, apply_gain

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT, exist_ok=True)

N = 1000
rnd = random.Random(20261005)


def random_scene(w, h, rng):
    img = Image(w, h)
    fx, fy = rng.uniform(15, 40), rng.uniform(15, 40)
    ph = rng.uniform(0, 6.28)
    for y in range(h):
        row = img.pixels[y]
        for x in range(w):
            v = 100 + 50 * math.sin(x / fx + ph) + 30 * math.cos(y / fy)
            row[x] = max(0, min(255, int(v + rng.uniform(-4, 4))))
    # 随机高对比主体
    for _ in range(rng.randint(0, 3)):
        cx, cy = rng.randint(10, w - 10), rng.randint(10, h - 10)
        rw, rh = rng.randint(3, 10), rng.randint(10, h // 2)
        for y in range(max(0, cy - rh), min(h, cy + rh)):
            for x in range(max(0, cx - rw), min(w, cx + rw)):
                img.pixels[y][x] = 230 if (x + y) % 5 < 2 else 25
    return img


def crop(img, x0, w):
    return Image(w, img.height, [row[x0:x0 + w] for row in img.pixels])


times = []
status_counts = {}
t_all = time.perf_counter()
for i in range(N):
    h = rnd.choice([60, 80, 100])
    w = rnd.choice([120, 160, 200])
    scene = random_scene(w, h, rnd)
    aw = rnd.randint(w // 2, w - 10)
    dx = rnd.randint(max(1, aw - 90), aw - 1)   # 重叠 1..90，覆盖大小重叠
    bw = w - dx
    a = crop(scene, 0, aw)
    b = crop(scene, dx, bw)
    if rnd.random() < 0.3:                       # 30% 制造曝光差异
        b = apply_gain(b, rnd.uniform(0.55, 0.9))
    t0 = time.perf_counter()
    _, rep = stitch(a, b, dx)
    times.append((time.perf_counter() - t0) * 1000.0)
    status_counts[rep["status"]] = status_counts.get(rep["status"], 0) + 1
total_ms = (time.perf_counter() - t_all) * 1000.0

times.sort()
def pct(p):
    return times[min(N - 1, int(p * N))]

report = {
    "pairs": N,
    "total_ms": round(total_ms, 1),
    "per_pair_ms": {
        "mean": round(sum(times) / N, 3),
        "min": round(times[0], 3),
        "p50": round(pct(0.50), 3),
        "p90": round(pct(0.90), 3),
        "p95": round(pct(0.95), 3),
        "p99": round(pct(0.99), 3),
        "max": round(times[-1], 3),
    },
    "status_counts": status_counts,
}
with open(os.path.join(OUT, "benchmark_report.json"), "w") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

print(json.dumps(report, ensure_ascii=False, indent=2))

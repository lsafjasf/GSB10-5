#!/usr/bin/env python3
"""压测：10,000 张合成图的主体定位 + 裁剪耗时与内存峰值。

运行: python3 benchmark.py [n]
产物: bench_output/benchmark.json

测量方法:
- 逐张生成（不计入处理耗时），逐张处理并计时；
- 全量内存用 resource.ru_maxrss（无测量开销）；
- Python 堆峰值用 tracemalloc 抽样 200 张（全量开启会有 ~30x 计时失真）。
"""

import json
import os
import random
import resource
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from autocrop_lib.ppm import new, fill_rect
from autocrop_lib import AutocropConfig, FitPolicy, autocrop

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_output")
BW, BH = 256, 192
RATIOS = [1.0, 16 / 9, 9 / 16, 4 / 3]
TRACE_SAMPLE = 200


def gen_image(kind, rnd):
    img = new(BW, BH, 1, (128,))
    if kind == "center":
        s = rnd.randint(24, 60)
        cx, cy = BW // 2, BH // 2
        fill_rect(img, cx - s // 2, cy - s // 2, cx + s // 2, cy + s // 2,
                  (rnd.choice([20, 235]),))
    elif kind == "edge":
        side = rnd.choice(["l", "r", "t", "b"])
        t = rnd.randint(12, 40)
        if side == "l":
            fill_rect(img, 0, rnd.randint(0, BH // 2), t, rnd.randint(BH // 2, BH), (230,))
        elif side == "r":
            fill_rect(img, BW - t, rnd.randint(0, BH // 2), BW, rnd.randint(BH // 2, BH), (230,))
        elif side == "t":
            fill_rect(img, rnd.randint(0, BW // 2), 0, rnd.randint(BW // 2, BW), t, (15,))
        else:
            fill_rect(img, rnd.randint(0, BW // 2), BH - t, rnd.randint(BW // 2, BW), BH, (15,))
    elif kind == "noise":
        img.data = bytearray(rnd.randbytes(BW * BH))
    # "flat": 保持纯色
    return img


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10000
    os.makedirs(OUT, exist_ok=True)
    rnd = random.Random(20261006)
    kinds = (["center"] * 40 + ["edge"] * 25 + ["noise"] * 20 + ["flat"] * 15)
    cfgs = [AutocropConfig(target_ratio=r, fit=FitPolicy(mode="contain"))
            for r in RATIOS]

    lat = []
    decisions = {}
    oob_fail = 0
    subject_detected = 0
    gen_s = 0.0
    proc_s = 0.0
    trace_peak = 0

    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    for i in range(n):
        t0 = time.perf_counter()
        img = gen_image(rnd.choice(kinds), rnd)
        gen_s += time.perf_counter() - t0

        if i < TRACE_SAMPLE:
            tracemalloc.start()
        t0 = time.perf_counter()
        res = autocrop(img, cfgs[i % len(cfgs)])
        dt = time.perf_counter() - t0
        proc_s += dt
        if i < TRACE_SAMPLE:
            _, pk = tracemalloc.get_traced_memory()
            trace_peak = max(trace_peak, pk)
            tracemalloc.stop()

        if i >= TRACE_SAMPLE:  # 被 tracemalloc 追踪的样本不计入延迟统计
            lat.append(dt)
        decisions[res["decision"]] = decisions.get(res["decision"], 0) + 1
        if res["analysis"]["has_subject"]:
            subject_detected += 1
        v = res["validation"]
        if v is not None and not v["all_ok"]:
            oob_fail += 1
        del img, res
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    report = {
        "n_images": n,
        "image_size": [BW, BH],
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "generation_seconds_total": round(gen_s, 3),
        "processing_seconds_total": round(proc_s, 3),
        "per_image_ms_mean": round(proc_s / n * 1000, 3),
        "per_image_ms_p50": round(sorted(lat)[len(lat) // 2] * 1000, 3),
        "per_image_ms_p95": round(sorted(lat)[int(len(lat) * 0.95)] * 1000, 3),
        "per_image_ms_p99": round(sorted(lat)[int(len(lat) * 0.99)] * 1000, 3),
        "per_image_ms_max": round(max(lat) * 1000, 3),
        "throughput_img_per_s": round(n / proc_s, 1),
        "memory_peak_tracemalloc_mb_sampled": round(trace_peak / 1048576, 2),
        "memory_ru_maxrss_mb": round(rss_after / 1024, 1),
        "memory_ru_maxrss_delta_mb": round((rss_after - rss_before) / 1024, 1),
        "decisions": decisions,
        "subject_detected": subject_detected,
        "oob_or_ratio_failures": oob_fail,
    }
    with open(os.path.join(OUT, "benchmark.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if oob_fail:
        sys.exit(1)


if __name__ == "__main__":
    main()

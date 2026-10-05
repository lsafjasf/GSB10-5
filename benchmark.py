"""1 小时视频基准：处理耗时、内存峰值、抽帧密度。

运行：python3 benchmark.py [--frames N]

视频为流式生成的合成 1 小时节目（25fps x 3600s = 90000 帧，
48x27 灰度），编排见 keyframe.synthetic.benchmark_schedule：
静态长镜头为主，穿插快速摇镜、溶解、淡入淡出。
为隔离“检测算法”本身的耗时，静态/运动段的加噪帧按小组循环
复用（检测器每帧的计算量与是否复用无关）。
"""

import resource
import sys
import time

from keyframe import KeyframeDetector
from keyframe.synthetic import (
    WIDTH, HEIGHT, FPS, _scene_a, _scene_b, _noise, _translate,
    _blend, _level, benchmark_schedule,
)
import random


def fast_frames(plan, w, h):
    """流式产出基准帧：静态/摇镜段循环复用少量预生成帧。"""
    rng = random.Random(7)
    a, b = _scene_a(w, h), _scene_b(w, h)
    black = bytes(w * h)
    cache = {}

    def variants(base, tag, k=8):
        if tag not in cache:
            cache[tag] = [_noise(base, rng) for _ in range(k)]
        return cache[tag]

    def pan_frames(base, tag, vx=4):
        if tag not in cache:
            period = w // vx
            cache[tag] = [
                _noise(_translate(base, w, h, vx * (k % period)), rng)
                for k in range(period * 2)
            ]
        return cache[tag]

    for kind, n in plan:
        if kind == "staticA":
            vs = variants(a, "sA")
            for i in range(n):
                yield vs[i % len(vs)]
        elif kind == "staticB":
            vs = variants(b, "sB")
            for i in range(n):
                yield vs[i % len(vs)]
        elif kind == "black":
            vs = variants(black, "bk")
            for i in range(n):
                yield vs[i % len(vs)]
        elif kind == "panA":
            vs = pan_frames(a, "pA")
            for i in range(n):
                yield vs[i % len(vs)]
        elif kind == "dissolveBA":
            for k in range(n):
                yield _noise(_blend(b, a, (k + 1) / (n + 1)), rng)
        elif kind == "fadeoutA":
            for k in range(n):
                yield _noise(_level(a, 1.0 - (k + 1) / (n + 1)), rng)
        elif kind == "fadeinB":
            for k in range(n):
                yield _noise(_level(b, (k + 1) / (n + 1)), rng)


def peak_rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def main():
    frames_arg = None
    if "--frames" in sys.argv:
        frames_arg = int(sys.argv[sys.argv.index("--frames") + 1])

    plan = benchmark_schedule()
    total = sum(n for _, n in plan)
    if frames_arg:
        total = min(total, frames_arg)

    det = KeyframeDetector(WIDTH, HEIGHT)
    t0 = time.perf_counter()
    count = 0
    for gray in fast_frames(plan, WIDTH, HEIGHT):
        det.feed(gray)
        count += 1
        if count >= total:
            break
    events = det.finish()
    dt = time.perf_counter() - t0

    keys = [e for e in events if e.kind in ("cut", "gradual")]
    cuts = [e for e in events if e.kind == "cut"]
    grads = [e for e in events if e.kind == "gradual"]
    hours = count / FPS / 3600
    minutes = count / FPS / 60

    print(f"帧数            : {count}（{count/FPS/60:.1f} 分钟 @ {FPS}fps, "
          f"{WIDTH}x{HEIGHT} 灰度）")
    print(f"处理耗时        : {dt:.1f}s（{count/dt:.0f} 帧/s，"
          f"实时倍率 {count/FPS/dt:.2f}x）")
    if abs(hours - 1.0) > 0.01:
        print(f"折算 1 小时耗时 : {dt/hours:.1f}s")
    print(f"内存峰值 (RSS)  : {peak_rss_mb():.1f} MB")
    print(f"硬切换事件      : {len(cuts)}")
    print(f"渐变转场事件    : {len(grads)}")
    print(f"关键帧总数      : {len(keys)}")
    print(f"抽帧密度        : {len(keys)/minutes:.2f} 个/分钟"
          f"（{len(keys)/max(hours,1e-9):.0f} 个/小时）")


if __name__ == "__main__":
    main()

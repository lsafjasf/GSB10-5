#!/usr/bin/env python3
"""Benchmark: process a synthetic 1-hour video (24 fps, 86400 frames).

Frames are streamed (never all in memory); we measure wall-clock time and
peak RSS of the detector.  Each 187 s cycle is explicitly scripted with its
ground truth:
  static 60s | CUT | motion 20s | CUT | shot 30s | DISSOLVE 1s |
  shot 30s | CUT | shot 15s | FADE 1s | shot 15s | pan 15s | CUT (next cycle)
"""

import os
import random
import resource
import sys
import time
from typing import Iterator, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from keyframe.detector import DetectorConfig, ShotDetector
from keyframe.metrics import keyframe_density, match_events
from keyframe import synth as S
from keyframe.synth import GTEvent

W, H, FPS = 128, 72, 24.0
TOTAL = int(3600 * FPS)          # 1 hour


def build_pools(seed: int = 11):
    rng = random.Random(seed)
    scenes = [S.texture_frame(rng, W, H) for _ in range(6)]
    static_v = S._noisy_variants(scenes[0], rng, 8)
    ball_r = max(3, H // 12)
    motion = []
    for k in range(120):
        f = bytearray(scenes[1])
        x = (k * 8) % (W - 2 * ball_r) + ball_r
        y = abs((k * 5) % (2 * (H - 2 * ball_r)) - (H - 2 * ball_r)) + ball_r
        S.draw_rect(f, W, H, x - ball_r, y - ball_r, x + ball_r, y + ball_r, 255)
        S.add_noise(f, rng, 2)
        motion.append(bytes(f))
    shot30 = S._noisy_variants(scenes[2], rng, 8)
    shot30b = S._noisy_variants(scenes[3], rng, 8)
    shot15c = S._noisy_variants(scenes[4], rng, 8)
    shot15d = S._noisy_variants(scenes[5], rng, 8)
    dis_len = int(FPS)
    dissolve = [S.blend(scenes[2], scenes[3], (k + 1) / (dis_len + 1))
                for k in range(dis_len)]
    fl = int(FPS / 2)
    fade = ([S.fade_level(scenes[4], 0, (k + 1) / (fl + 1)) for k in range(fl)]
            + [S.fade_level(scenes[5], 0, 1.0 - (k + 1) / (fl + 1)) for k in range(fl)])
    pan = [S.translate(scenes[5], W, H,
                       (k if k < 24 else 48 - k) * 3, 0, fill=32)
           for k in range(48)]
    return static_v, motion, shot30, dissolve, shot30b, shot15c, fade, shot15d, pan


def stream_frames(total: int, gt: List[GTEvent]) -> Iterator[bytes]:
    (static_v, motion, shot30, dissolve, shot30b,
     shot15c, fade, shot15d, pan) = build_pools()
    produced = 0

    def emit(pool, n):
        nonlocal produced
        for k in range(n):
            if produced >= total:
                return
            yield pool[k % len(pool)]
            produced += 1

    def cut():
        if produced < total:
            gt.append(GTEvent("cut", produced - 1, produced, "cut"))

    while produced < total:
        yield from emit(static_v, int(60 * FPS))
        cut()
        yield from emit(motion, int(20 * FPS))
        cut()
        yield from emit(shot30, int(30 * FPS))
        start = produced
        yield from emit(dissolve, len(dissolve))
        if produced > start:
            gt.append(GTEvent("gradual", start, produced, "dissolve"))
        yield from emit(shot30b, int(30 * FPS))
        cut()
        yield from emit(shot15c, int(15 * FPS))
        start = produced
        yield from emit(fade, len(fade))
        if produced > start:
            gt.append(GTEvent("gradual", start, produced, "fade"))
        yield from emit(shot15d, int(15 * FPS))
        yield from emit(pan, int(15 * FPS))
        cut()   # pan (scene5) -> next cycle static (scene0)


def main() -> None:
    cfg = DetectorConfig(width=W, height=H, fps=FPS)
    det = ShotDetector(cfg)
    gt: List[GTEvent] = []

    t0 = time.perf_counter()
    n = 0
    for frame in stream_frames(TOTAL, gt):
        det.add_frame(frame)
        n += 1
    events, shots = det.finish()
    dt = time.perf_counter() - t0

    peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    n_kf = sum(len(s.keyframes) for s in shots)
    cut_rep, grad_rep = match_events(events, gt)
    density = keyframe_density(n_kf, n, FPS)

    print(f"video length      : {n} frames = {n / FPS / 3600:.2f} h @ {FPS:.0f} fps")
    print(f"analysis size     : {W}x{H} gray")
    print(f"wall time         : {dt:.1f} s  ({n / dt:.0f} fps, "
          f"{dt / n * 1000:.2f} ms/frame, {3600 / dt:.1f}x realtime)")
    print(f"peak RSS          : {peak_kb / 1024:.1f} MB")
    print(f"shots             : {len(shots)}")
    print(f"cuts    TP/FP/FN  : {cut_rep.tp}/{cut_rep.fp}/{cut_rep.fn} "
          f"(annotated {len([g for g in gt if g.kind == 'cut'])})")
    print(f"gradual TP/FP/FN  : {grad_rep.tp}/{grad_rep.fp}/{grad_rep.fn} "
          f"(annotated {len([g for g in gt if g.kind == 'gradual'])})")
    print(f"keyframes         : {n_kf}  ({density:.2f} per minute)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Self-tests: run the detector over synthetic scenarios and score it.

Scenarios cover the four required cases:
  * static_long            -- static long shot (density control)
  * fast_motion            -- fast object motion + camera pan (no false cuts)
  * hard_cuts              -- abrupt transitions
  * gradual_transitions    -- cross-dissolves and fades
  * mixed                  -- everything combined

Ground truth is scripted (exact annotation); we report missed (漏检) and
false (误检) detections plus keyframe density for every scenario.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from keyframe.detector import DetectorConfig, detect
from keyframe.metrics import keyframe_density, match_events
from keyframe import synth

W, H, FPS = 128, 72, 24.0


def run_scenario(video, cfg):
    t0 = time.perf_counter()
    events, shots = detect(video.frames, cfg)
    dt = time.perf_counter() - t0
    reports = match_events(events, video.ground_truth)
    n_kf = sum(len(s.keyframes) for s in shots)
    density = keyframe_density(n_kf, video.total_frames, video.fps)
    return events, shots, reports, n_kf, density, dt


def main() -> int:
    cfg = DetectorConfig(width=W, height=H, fps=FPS)
    scenarios = [
        synth.static_long(W, H, FPS, seconds=120.0),
        synth.fast_motion(W, H, FPS, seconds=30.0),
        synth.hard_cuts(W, H, FPS, n_shots=8),
        synth.gradual_transitions(W, H, FPS),
        synth.mixed(W, H, FPS),
    ]

    failures = []
    print(f"{'scenario':<22}{'frames':>7}{'time':>7}{'shots':>6}{'kf':>4}"
          f"{'kf/min':>8}  cuts(tp/fp/fn)  gradual(tp/fp/fn)")
    print("-" * 96)
    for video in scenarios:
        events, shots, reports, n_kf, density, dt = run_scenario(video, cfg)
        cut, grad = reports
        print(f"{video.name:<22}{video.total_frames:>7}{dt:>6.2f}s"
              f"{len(shots):>6}{n_kf:>4}{density:>8.2f}"
              f"  {cut.tp}/{cut.fp}/{cut.fn}           "
              f"{grad.tp}/{grad.fp}/{grad.fn}")

        # ---- assertions (the "test" part) -------------------------------- #
        if video.name == "static_long":
            # static long shot: no boundaries, ~1 keyframe, low density
            if len(events) != 0 or n_kf > 2:
                failures.append(f"{video.name}: events={len(events)} kf={n_kf}")
        elif video.name == "fast_motion":
            # fast motion must not be detected as cuts
            if cut.fp != 0 or grad.fp != 0:
                failures.append(f"{video.name}: false alarms "
                                f"cut={cut.fp} gradual={grad.fp}")
        else:
            for rep in reports:
                if rep.fn or rep.fp:
                    failures.append(
                        f"{video.name}/{rep.kind}: fn={rep.fn} fp={rep.fp}"
                        f" missed={[(g.start, g.end) for g in rep.missed]}"
                        f" false={[(e.start, e.end) for e in rep.false_alarms]}")

        # detail lines for gradual scenario (样例检测结果)
        if video.name == "gradual_transitions":
            print("  gradual events detected:")
            for e in events:
                print(f"    {e.subtype:<9} frames [{e.start:>4},{e.end:>4})"
                      f"  score={e.score:.2f} brightness_drift={e.brightness_drift:.1f}")
            print("  ground truth:")
            for g in video.ground_truth:
                print(f"    {g.subtype:<9} frames [{g.start:>4},{g.end:>4})")

    print("-" * 96)
    if failures:
        print("FAILURES:")
        for f in failures:
            print("  *", f)
        return 1
    print("ALL SCENARIOS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

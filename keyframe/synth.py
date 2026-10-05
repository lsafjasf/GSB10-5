"""Synthetic video generator used by the self-tests and the benchmark.

Everything is produced with the standard library only.  A "video" is an
iterator of grayscale frames (``bytes`` of ``width*height``) plus a ground
truth list of transition events, which plays the role of human annotation
(the script *is* the annotation, so it is exact).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

Frame = bytes


@dataclass
class GTEvent:
    kind: str          # "cut" | "gradual"
    start: int         # first frame of the transition
    end: int           # exclusive
    subtype: str = ""  # "dissolve" | "fade"

    @property
    def position(self) -> int:
        return self.start if self.kind == "gradual" else self.end - 1


@dataclass
class SyntheticVideo:
    width: int
    height: int
    fps: float
    frames: Iterator[Frame]
    ground_truth: List[GTEvent] = field(default_factory=list)
    total_frames: int = 0
    name: str = ""


# --------------------------------------------------------------------------- #
# Frame primitives
# --------------------------------------------------------------------------- #

def texture_frame(rng: random.Random, width: int, height: int) -> Frame:
    """A scene: horizontal brightness bands + random blocks + pixel noise."""
    base = bytearray(width * height)
    bands = rng.randint(3, 6)
    band_vals = [rng.randint(20, 235) for _ in range(bands)]
    for y in range(height):
        v = band_vals[y * bands // height]
        row = y * width
        for x in range(width):
            base[row + x] = v
    for _ in range(rng.randint(4, 10)):
        x0 = rng.randint(0, width - 9)
        y0 = rng.randint(0, height - 9)
        w = rng.randint(4, max(5, width // 4))
        h = rng.randint(4, max(5, height // 4))
        v = rng.randint(0, 255)
        draw_rect(base, width, height, x0, y0,
                  min(x0 + w, width), min(y0 + h, height), v)
    add_noise(base, rng, 6)
    return bytes(base)


def add_noise(frame: bytearray, rng: random.Random, amp: int) -> None:
    for i in range(len(frame)):
        frame[i] = min(255, max(0, frame[i] + rng.randint(-amp, amp)))


def draw_rect(frame: bytearray, width: int, height: int,
              x0: int, y0: int, x1: int, y1: int, value: int) -> None:
    for y in range(max(0, y0), min(height, y1)):
        row = y * width
        for x in range(max(0, x0), min(width, x1)):
            frame[row + x] = value


def translate(frame: Frame, width: int, height: int,
              dx: int, dy: int, fill: int = 0) -> Frame:
    out = bytearray([fill]) * (width * height)
    for y in range(height):
        sy = y - dy
        if 0 <= sy < height:
            srow = sy * width
            orow = y * width
            sx0 = max(0, -dx)
            sx1 = min(width, width - dx)
            if sx1 > sx0:
                out[orow + sx0 + dx:orow + sx1 + dx] = frame[srow + sx0:srow + sx1]
    return bytes(out)


def blend(a: Frame, b: Frame, t: float) -> Frame:
    """Cross-dissolve: t=0 -> a, t=1 -> b."""
    return bytes(int(av * (1.0 - t) + bv * t) for av, bv in zip(a, b))


def fade_level(a: Frame, level: int, t: float) -> Frame:
    """Fade frame ``a`` toward a flat ``level`` frame (0=black, 255=white)."""
    return bytes(int(av * (1.0 - t) + level * t) for av in a)


# --------------------------------------------------------------------------- #
# Scenario builders
# --------------------------------------------------------------------------- #

def _noisy_variants(scene: Frame, rng: random.Random, n: int, amp: int = 2) -> List[Frame]:
    out = []
    for _ in range(n):
        b = bytearray(scene)
        add_noise(b, rng, amp)
        out.append(bytes(b))
    return out


def static_long(width: int, height: int, fps: float, seconds: float,
                seed: int = 1, noise_amp: int = 2) -> SyntheticVideo:
    """One static shot with sensor noise only.  Ground truth: no transitions."""
    rng = random.Random(seed)
    scene = texture_frame(rng, width, height)
    total = int(seconds * fps)

    def gen() -> Iterator[Frame]:
        # sensor noise is temporally correlated: model it as a slow random
        # walk over a small subset of pixels per frame
        cur = bytearray(scene)
        for _ in range(total):
            for _ in range(len(cur) // 30):
                j = rng.randrange(len(cur))
                cur[j] = max(0, min(255, cur[j] + rng.choice((-1, 1))))
            yield bytes(cur)

    return SyntheticVideo(width, height, fps, gen(), [], total, "static_long")


def fast_motion(width: int, height: int, fps: float, seconds: float,
                seed: int = 2) -> SyntheticVideo:
    """Fast object motion + a camera pan segment.  Ground truth: no transitions."""
    rng = random.Random(seed)
    bg = texture_frame(rng, width, height)
    total = int(seconds * fps)
    ball_r = max(3, height // 12)
    pan_start = total // 2
    pan_len = min(total - pan_start, int(2.0 * fps))
    pan_speed = 3  # px/frame at analysis resolution

    def pan_shift(k: int) -> int:
        # sweep out and back so the sequence stays continuous
        half = pan_len // 2
        if k < half:
            return k * pan_speed
        return (pan_len - k) * pan_speed

    def gen() -> Iterator[Frame]:
        for i in range(total):
            if pan_start <= i < pan_start + pan_len:
                # global camera pan: whole frame shifts
                yield translate(bg, width, height,
                                pan_shift(i - pan_start), 0, fill=32)
                continue
            frame = bytearray(bg)
            # fast ball: ~8 px/frame horizontal, bouncing vertically
            x = (i * 8) % (width - 2 * ball_r) + ball_r
            y = abs((i * 5) % (2 * (height - 2 * ball_r)) - (height - 2 * ball_r)) + ball_r
            draw_rect(frame, width, height,
                      x - ball_r, y - ball_r, x + ball_r, y + ball_r, 255)
            add_noise(frame, rng, 2)
            yield bytes(frame)

    return SyntheticVideo(width, height, fps, gen(), [], total, "fast_motion")


def hard_cuts(width: int, height: int, fps: float, n_shots: int = 8,
              seed: int = 3) -> SyntheticVideo:
    """Distinct scenes joined by abrupt cuts."""
    rng = random.Random(seed)
    scenes = [texture_frame(rng, width, height) for _ in range(n_shots)]
    variants = [_noisy_variants(s, rng, 4) for s in scenes]
    shot_len = int(3.0 * fps)
    gt: List[GTEvent] = []
    total = n_shots * shot_len

    def gen() -> Iterator[Frame]:
        idx = 0
        for s in range(n_shots):
            for k in range(shot_len):
                yield variants[s][k % 4]
                idx += 1
            if s < n_shots - 1:
                gt.append(GTEvent("cut", idx - 1, idx, "cut"))

    return SyntheticVideo(width, height, fps, gen(), gt, total, "hard_cuts")


def gradual_transitions(width: int, height: int, fps: float,
                        seed: int = 4) -> SyntheticVideo:
    """Scenes joined by cross-dissolves and fades to black."""
    rng = random.Random(seed)
    n_scenes = 4
    scenes = [texture_frame(rng, width, height) for _ in range(n_scenes)]
    variants = [_noisy_variants(s, rng, 4) for s in scenes]
    shot_len = int(2.5 * fps)
    dissolve_len = int(1.0 * fps)   # 24 frames @24fps
    fade_len = int(0.5 * fps)       # 12 frames out + 12 in
    gt: List[GTEvent] = []

    def gen() -> Iterator[Frame]:
        idx = 0
        for s in range(n_scenes):
            for k in range(shot_len):
                yield variants[s][k % 4]
                idx += 1
            if s == n_scenes - 1:
                break
            if s % 2 == 0:
                # cross-dissolve into next scene
                start = idx
                for k in range(dissolve_len):
                    t = (k + 1) / (dissolve_len + 1)
                    yield blend(scenes[s], scenes[s + 1], t)
                    idx += 1
                gt.append(GTEvent("gradual", start, idx, "dissolve"))
            else:
                # fade out to black, then fade in
                start = idx
                for k in range(fade_len):
                    t = (k + 1) / (fade_len + 1)
                    yield fade_level(scenes[s], 0, t)
                    idx += 1
                for k in range(fade_len):
                    t = (k + 1) / (fade_len + 1)
                    yield fade_level(scenes[s + 1], 0, 1.0 - t)
                    idx += 1
                gt.append(GTEvent("gradual", start, idx, "fade"))

    total = n_scenes * shot_len + 2 * dissolve_len + 2 * fade_len
    return SyntheticVideo(width, height, fps, gen(), gt, total,
                          "gradual_transitions")


def mixed(width: int, height: int, fps: float, seed: int = 5) -> SyntheticVideo:
    """A bit of everything: static, motion, cuts, dissolves, fades."""
    rng = random.Random(seed)
    scenes = [texture_frame(rng, width, height) for _ in range(6)]
    variants = [_noisy_variants(s, rng, 4) for s in scenes]
    shot_len = int(2.0 * fps)
    dissolve_len = int(0.8 * fps)
    gt: List[GTEvent] = []

    def gen() -> Iterator[Frame]:
        idx = 0

        def shot(s: int, n: int) -> None:
            nonlocal idx
            for k in range(n):
                yield variants[s][k % 4]
                idx += 1

        def motion(s: int, n: int) -> None:
            nonlocal idx
            for k in range(n):
                frame = bytearray(scenes[s])
                x = (k * 7) % (width - 12) + 6
                draw_rect(frame, width, height, x - 5, height // 2 - 5,
                          x + 5, height // 2 + 5, 250)
                add_noise(frame, rng, 2)
                yield bytes(frame)
                idx += 1

        def cut_after(s: int) -> None:
            gt.append(GTEvent("cut", idx - 1, idx, "cut"))

        def dissolve(s: int, t_s: int) -> None:
            nonlocal idx
            start = idx
            for k in range(dissolve_len):
                yield blend(scenes[s], scenes[t_s], (k + 1) / (dissolve_len + 1))
                idx += 1
            gt.append(GTEvent("gradual", start, idx, "dissolve"))

        def fade(s: int, t_s: int) -> None:
            nonlocal idx
            fl = int(0.5 * fps)
            start = idx
            for k in range(fl):
                yield fade_level(scenes[s], 0, (k + 1) / (fl + 1))
                idx += 1
            for k in range(fl):
                yield fade_level(scenes[t_s], 0, 1.0 - (k + 1) / (fl + 1))
                idx += 1
            gt.append(GTEvent("gradual", start, idx, "fade"))

        yield from shot(0, shot_len * 2)          # static
        cut_after(1)
        yield from motion(1, shot_len)            # fast object motion
        cut_after(2)
        yield from shot(2, shot_len)
        yield from dissolve(2, 3)
        yield from shot(3, shot_len)
        yield from fade(3, 4)
        yield from shot(4, shot_len)
        cut_after(5)
        yield from shot(5, shot_len * 2)          # static tail

    # segments: 2L + L + L + D + L + 2F + L + 2L = 9L + D + 2F
    total = 9 * shot_len + dissolve_len + 2 * int(0.5 * fps)
    return SyntheticVideo(width, height, fps, gen(), gt, total, "mixed")

"""Shot-boundary detection and keyframe extraction (Python standard library only).

The detector consumes downscaled grayscale frames (``bytes`` of ``width*height``,
0-255) one at a time and emits:

* shot-boundary events (hard cuts, gradual dissolves/fades);
* keyframe indices (one representative frame per shot plus content frames).

All measures are defined in README ("判定依据"); thresholds live in
:class:`DetectorConfig` and can be tuned without touching the algorithm.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator, List, Optional, Sequence, Tuple


# --------------------------------------------------------------------------- #
# Data containers
# --------------------------------------------------------------------------- #

@dataclass
class DetectorConfig:
    width: int = 128
    height: int = 72
    fps: float = 24.0
    # -- normalized histogram distance d_h in [0, 1] -------------------------
    # Calibrated from measured synthetic cases (see README):
    #   static/correlated noise d_h<=0.02, motion d_h~0.05,
    #   dissolve steps d_h~0.2, fade steps up to ~0.7, cuts d_h>=0.7.
    static_th: float = 0.02         # below this the scene is considered static
    grad_low: float = 0.10          # a frame belongs to a gradual transition
    cut_h_th: float = 0.30          # hard cut: histogram distance
    cut_mae_th: float = 15.0        # hard cut: raw pixel MAE (0-255)
    cut_block_th: float = 0.75      # hard cut: fraction of changed spatial blocks
    block_diff_th: float = 12.0     # per-block MAE threshold (0-255)
    block_cols: int = 16
    block_rows: int = 9
    # -- gradual transitions --------------------------------------------------
    grad_sum_th: float = 0.80       # accumulated d_h needed for a gradual event
    grad_min_frames: int = 4        # minimum span
    grad_gap: int = 3               # settle frames that end a transition
    fade_black: float = 30.0        # mean brightness reached -> fade to black
    fade_white: float = 225.0       # mean brightness reached -> fade to white
    # -- motion discrimination ------------------------------------------------
    shift_search: int = 4           # +/- pixels global-shift search
    shift_steps: Tuple[int, ...] = (0, 2, 4)
    motion_residual_ratio: float = 0.55  # compensated/uncompensated MAE
    # -- keyframe selection ---------------------------------------------------
    intra_anchor_th: float = 0.25   # d_h vs last keyframe -> new keyframe
    intra_min_frames: int = 12      # min spacing between in-shot keyframes


@dataclass
class Event:
    kind: str                # "cut" | "gradual"
    start: int               # first frame of the transition
    end: int                 # exclusive end (for cuts end == start + 1)
    score: float
    brightness_drift: float = 0.0
    subtype: str = ""        # "dissolve" | "fade" for gradual events

    @property
    def position(self) -> int:
        """Frame index where the new content becomes dominant."""
        return self.start if self.kind == "gradual" else self.end - 1


@dataclass
class Shot:
    start: int
    end: int                 # exclusive
    keyframes: List[int] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Pure per-frame measures
# --------------------------------------------------------------------------- #

def histogram(frame: bytes) -> List[int]:
    """256-bin grayscale histogram using C-level ``bytes.count``."""
    return [frame.count(v) for v in range(256)]


def hist_distance(h1: Sequence[int], h2: Sequence[int]) -> float:
    """Normalized L1 distance in [0, 1].

    Motion mostly permutes pixels between bins, so this stays low for panning
    but jumps when the content distribution genuinely changes.
    """
    total = sum(h1)
    if total == 0:
        return 0.0
    return sum(abs(a - b) for a, b in zip(h1, h2)) / (2.0 * total)


def mae(f1: bytes, f2: bytes) -> float:
    return sum(abs(a - b) for a, b in zip(f1, f2)) / len(f1)


def diff_image(f1: bytes, f2: bytes) -> bytes:
    return bytes(abs(a - b) for a, b in zip(f1, f2))


def block_fraction(diff: bytes, width: int, height: int,
                   cols: int, rows: int, block_th: float) -> float:
    """Fraction of spatial blocks whose mean absolute error exceeds threshold."""
    bw = width / cols
    bh = height / rows
    changed = 0
    for by in range(rows):
        y0 = int(by * bh)
        y1 = int((by + 1) * bh)
        for bx in range(cols):
            x0 = int(bx * bw)
            x1 = int((bx + 1) * bw)
            acc = 0
            n = 0
            for y in range(y0, y1):
                row = y * width
                acc += sum(diff[row + x0:row + x1])
                n += x1 - x0
            if n and acc / n >= block_th:
                changed += 1
    return changed / (cols * rows)


def compensated_mae(f1: bytes, f2: bytes, width: int, height: int,
                    search: int, steps: Sequence[int]) -> Tuple[float, int, int]:
    """Best global-translation compensated MAE.

    Camera pans/tilts (global motion) align most pixels after a small shift,
    whereas a cut cannot be explained by any shift.
    """
    deltas: List[int] = [0]
    for s in steps:
        if 0 < s <= search:
            deltas.extend((-s, s))

    best = float("inf")
    best_dx = best_dy = 0
    for dy in deltas:
        y0a, y0b = (dy, 0) if dy >= 0 else (0, -dy)
        hh = height - abs(dy)
        if hh <= 0:
            continue
        for dx in deltas:
            x0a, x0b = (dx, 0) if dx >= 0 else (0, -dx)
            ww = width - abs(dx)
            if ww <= 0:
                continue
            acc = 0
            n = 0
            for k in range(hh):
                ra = (y0a + k) * width
                rb = (y0b + k) * width
                acc += sum(abs(f1[ra + x0a + x] - f2[rb + x0b + x])
                           for x in range(ww))
                n += ww
            value = acc / n
            if value < best:
                best = value
                best_dx, best_dy = dx, dy
    return best, best_dx, best_dy


# --------------------------------------------------------------------------- #
# Detector state machine
# --------------------------------------------------------------------------- #

class ShotDetector:
    """Streaming shot-boundary detector.

    Call :meth:`add_frame` for every frame in order, then :meth:`finish`.
    Results are accumulated on ``events`` and ``shots``.

    A gradual transition is reported *when it completes* (a run of
    high-change frames followed by ``grad_gap`` settled frames), so the
    reported range covers the whole dissolve/fade.
    """

    def __init__(self, config: Optional[DetectorConfig] = None):
        self.cfg = config or DetectorConfig()
        self.events: List[Event] = []
        self.shots: List[Shot] = []

        self._idx = 0
        self._prev: Optional[bytes] = None
        self._prev_hist: Optional[List[int]] = None

        # current shot state
        self._shot_start = 0
        self._kf: List[int] = [0]
        self._last_keyframe = 0
        self._anchor_hist: Optional[List[int]] = None  # histogram at last keyframe
        self._anchor_bright: Optional[float] = None

        # gradual accumulation state
        self._acc = 0.0
        self._acc_start: Optional[int] = None
        self._acc_end: Optional[int] = None
        self._acc_bright: List[float] = []
        self._settle = 0

    # -- public API --------------------------------------------------------- #

    def add_frame(self, frame: bytes) -> None:
        if len(frame) != self.cfg.width * self.cfg.height:
            raise ValueError(
                f"frame has {len(frame)} bytes, expected "
                f"{self.cfg.width * self.cfg.height}"
            )
        idx = self._idx
        self._idx += 1
        if self._prev is None:
            self._prev = frame
            self._prev_hist = histogram(frame)
            self._anchor_hist = self._prev_hist
            self._anchor_bright = sum(frame) / len(frame)
            return

        cfg = self.cfg
        h = histogram(frame)
        dh = hist_distance(self._prev_hist, h)
        brightness = sum(frame) / len(frame)

        is_cut = False
        if dh >= cfg.cut_h_th:
            mae_value = mae(self._prev, frame)
            if mae_value >= cfg.cut_mae_th:
                diff = diff_image(self._prev, frame)
                bf = block_fraction(diff, cfg.width, cfg.height,
                                    cfg.block_cols, cfg.block_rows,
                                    cfg.block_diff_th)
                if bf >= cfg.cut_block_th:
                    comp, _, _ = compensated_mae(
                        self._prev, frame, cfg.width, cfg.height,
                        cfg.shift_search, cfg.shift_steps)
                    if comp / max(mae_value, 1e-9) >= cfg.motion_residual_ratio:
                        is_cut = True

        # gradual state: "active" while changing, "boundary" when a run ends
        grad_state = self._update_gradual(idx, dh, brightness,
                                          force_reset=is_cut)

        if is_cut:
            self.events.append(Event(
                kind="cut", start=idx - 1, end=idx,
                score=dh, subtype="cut",
            ))
            self._close_shot(idx)
            self._set_anchor(h, brightness)
        elif grad_state == "boundary":
            # new shot starts at the first settled frame (idx)
            self._set_anchor(h, brightness)
        elif grad_state == "0":
            # in-shot keyframe: content drifted far from the last anchor
            d_anchor = hist_distance(self._anchor_hist, h)
            if (d_anchor >= cfg.intra_anchor_th
                    and idx - self._last_keyframe >= cfg.intra_min_frames):
                self._kf.append(idx)
                self._last_keyframe = idx
                self._anchor_hist = h
                self._anchor_bright = brightness

        self._prev = frame
        self._prev_hist = h

    def finish(self) -> Tuple[List[Event], List[Shot]]:
        # a transition reaching the end of the video is reported only if it
        # already met the evidence thresholds
        if self._acc_start is not None and self._settle < self.cfg.grad_gap:
            self._finalize_gradual()
        if self._shot_start < self._idx:
            self.shots.append(Shot(
                start=self._shot_start, end=self._idx,
                keyframes=list(self._kf),
            ))
        return self.events, self.shots

    # -- internals ---------------------------------------------------------- #

    def _close_shot(self, boundary_idx: int) -> None:
        self.shots.append(Shot(
            start=self._shot_start, end=boundary_idx,
            keyframes=list(self._kf),
        ))
        self._shot_start = boundary_idx
        self._kf = [boundary_idx]
        self._last_keyframe = boundary_idx

    def _set_anchor(self, h: List[int], brightness: float) -> None:
        self._anchor_hist = h
        self._anchor_bright = brightness

    def _update_gradual(self, idx: int, dh: float, brightness: float,
                        force_reset: bool) -> str:
        """Return "active" while a run accumulates, "boundary" when one ends,
        or "0" on an ordinary frame."""
        cfg = self.cfg
        if force_reset:
            self._reset_gradual()
            return "0"

        if dh >= cfg.grad_low:
            if self._acc_start is None:
                self._acc_start = idx - 1
                self._acc_bright = [self._anchor_bright if self._anchor_bright
                                    is not None else brightness]
            self._acc += dh
            self._acc_bright.append(brightness)
            self._acc_end = idx
            self._settle = 0
            return "active"

        # settled frame: either end a completed run or discard a weak one
        if self._acc_start is not None:
            self._settle += 1
            if self._settle >= cfg.grad_gap:
                self._finalize_gradual()
                return "boundary"
        return "0"

    def _finalize_gradual(self) -> None:
        """Report (or discard) the accumulated gradual run and close the shot."""
        if self._acc_start is not None:
            span = (self._acc_end or self._acc_start) - self._acc_start
            if (self._acc >= self.cfg.grad_sum_th
                    and span + 1 >= self.cfg.grad_min_frames):
                start, end = self._acc_start, self._acc_end + 1
                drift = (abs(self._acc_bright[-1] - self._acc_bright[0])
                         if len(self._acc_bright) >= 2 else 0.0)
                extreme = min(self._acc_bright) if self._acc_bright else 128.0
                is_fade = (extreme <= self.cfg.fade_black
                           or extreme >= self.cfg.fade_white)
                self.events.append(Event(
                    kind="gradual", start=start, end=end,
                    score=self._acc, brightness_drift=drift,
                    subtype="fade" if is_fade else "dissolve",
                ))
                self._close_shot(end)
        self._reset_gradual()

    def _reset_gradual(self) -> None:
        self._acc = 0.0
        self._acc_start = None
        self._acc_end = None
        self._acc_bright = []
        self._settle = 0


def detect(frames: Iterable[bytes],
           config: Optional[DetectorConfig] = None
           ) -> Tuple[List[Event], List[Shot]]:
    """Convenience: run the detector over an iterable of frames."""
    det = ShotDetector(config)
    for frame in frames:
        det.add_frame(frame)
    return det.finish()


def iter_keyframes(frames: Iterable[bytes],
                   config: Optional[DetectorConfig] = None
                   ) -> Iterator[int]:
    """Yield keyframe indices for an iterable of frames."""
    _, shots = detect(frames, config)
    for shot in shots:
        yield from shot.keyframes

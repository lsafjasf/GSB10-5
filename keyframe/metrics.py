"""Evaluation helpers: match detected events against ground truth."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence

from .detector import Event
from .synth import GTEvent


@dataclass
class MatchReport:
    kind: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    missed: List[GTEvent] = field(default_factory=list)   # 漏检
    false_alarms: List[Event] = field(default_factory=list)  # 误检

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 1.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 1.0


def _overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def match_events(detected: Sequence[Event], truth: Sequence[GTEvent],
                 cut_tolerance: int = 2) -> List[MatchReport]:
    """Greedy one-to-one matching, reported separately for cuts and graduals.

    * cut:      matched when |det.position - gt.position| <= cut_tolerance
    * gradual:  matched when the detected and annotated ranges overlap
    """
    reports = {"cut": MatchReport("cut"), "gradual": MatchReport("gradual")}
    for kind in ("cut", "gradual"):
        det = [e for e in detected if e.kind == kind]
        gt = [g for g in truth if g.kind == kind]
        used = [False] * len(det)
        rep = reports[kind]
        for g in gt:
            best_i = -1
            best_d = None
            for i, e in enumerate(det):
                if used[i]:
                    continue
                if kind == "cut":
                    d = abs(e.position - g.position)
                    ok = d <= cut_tolerance
                else:
                    d = -_overlap(e.start, e.end, g.start, g.end)
                    ok = d < 0
                if ok and (best_d is None or d < best_d):
                    best_d = d
                    best_i = i
            if best_i >= 0:
                used[best_i] = True
                rep.tp += 1
            else:
                rep.fn += 1
                rep.missed.append(g)
        for i, e in enumerate(det):
            if not used[i]:
                rep.fp += 1
                rep.false_alarms.append(e)
    return [reports["cut"], reports["gradual"]]


def keyframe_density(n_keyframes: int, total_frames: int, fps: float) -> float:
    """Keyframes per minute."""
    minutes = total_frames / fps / 60.0
    return n_keyframes / minutes if minutes > 0 else 0.0

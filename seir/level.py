"""Severity levels, thresholds and response-time (SLA) policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Level(IntEnum):
    P1 = 1  # most severe
    P2 = 2
    P3 = 3
    P4 = 4
    P5 = 5  # informational / log only


# (lower bound of score, level). Deterministic, non-overlapping bands.
THRESHOLDS = ((85.0, Level.P1), (65.0, Level.P2), (45.0, Level.P3),
              (25.0, Level.P4), (0.0, Level.P5))

LEVEL_ORDER = [Level.P1, Level.P2, Level.P3, Level.P4, Level.P5]


def level_for_score(score: float) -> Level:
    for bound, level in THRESHOLDS:
        if score >= bound:
            return level
    return Level.P5


def lower_bound(level: Level) -> float:
    for bound, lv in THRESHOLDS:
        if lv == level:
            return bound
    raise ValueError(level)


@dataclass(frozen=True)
class SlaPolicy:
    """Response deadline per level, in seconds."""

    deadlines: dict

    def deadline(self, level: Level) -> float:
        return float(self.deadlines[level])


DEFAULT_SLA = SlaPolicy(
    deadlines={
        Level.P1: 15 * 60,
        Level.P2: 60 * 60,
        Level.P3: 4 * 3600,
        Level.P4: 24 * 3600,
        Level.P5: 72 * 3600,
    }
)

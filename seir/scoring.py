"""Signal fusion and weighted scoring (pure functions, no clock)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class Signal:
    """A single detection signal.

    strength:   intrinsic severity in [0, 100].
    confidence: detection confidence in [0, 1].
    """

    id: str
    category: str
    strength: float
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.strength <= 100.0:
            raise ValueError(f"strength out of range: {self.strength}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")

    @property
    def effective(self) -> float:
        """Confidence-adjusted strength, still in [0, 100]."""
        return self.strength * self.confidence


@dataclass(frozen=True)
class Event:
    id: str
    asset_tier: int          # 1 (lowest) .. 5 (mission critical)
    affected: int            # number of impacted hosts/users, >= 1
    signals: List[Signal] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not 1 <= self.asset_tier <= 5:
            raise ValueError(f"asset_tier out of range: {self.asset_tier}")
        if self.affected < 1:
            raise ValueError(f"affected must be >= 1: {self.affected}")


@dataclass(frozen=True)
class Contribution:
    """One signal's auditable contribution to the fused score."""

    signal_id: str
    category: str
    raw_strength: float
    confidence: float
    effective_strength: float
    marginal_points: float   # incremental points added to the fused value
    share: float             # marginal_points / fused, [0, 1]


@dataclass(frozen=True)
class ScoreBreakdown:
    event_id: str
    fused: float                      # signal fusion result, [0, 100]
    asset_factor: float
    scope_factor: float
    score: float                      # final score, [0, 100]
    contributions: List[Contribution]


_ASSET_FACTORS = {1: 0.8, 2: 1.0, 3: 1.2, 4: 1.4, 5: 1.6}


def asset_factor(asset_tier: int) -> float:
    """Linear, capped weight for asset importance.

    Linear so a tier is always meaningful, capped at x1.6 so that the
    asset alone can never turn noise into a P1 incident.
    """
    return _ASSET_FACTORS[asset_tier]


def scope_factor(affected: int) -> float:
    """Sub-linear (log10) weight for blast radius, capped at x1.6.

    Logarithmic scaling rewards breadth (10 -> 1.15, 100 -> 1.30)
    without letting a noisy mass event swamp signal quality.
    """
    return min(1.6, 1.0 + 0.15 * math.log10(affected))


def fuse_signals(signals: List[Signal]):
    """Fuse signals with the probabilistic sum.

        fused = 100 * (1 - prod(1 - s_i / 100))

    Rationale:
    * Several independent weak signals genuinely raise risk, so they
      must stack: two 30-point signals fuse to 51, not 30.
    * But alert *count* must not dominate: each signal has a bounded,
      diminishing marginal effect, so 100 zero-strength alerts stay 0
      and ten 5-point signals only reach 40.
    Returns (fused_value, contributions). Contributions are the
    deterministic marginal increments when signals are inserted in
    descending effective-strength order; they sum to fused and are
    therefore fully auditable.
    """
    ordered = sorted(signals, key=lambda s: (-s.effective, s.id))
    contributions = []
    unresolved = 1.0
    fused = 0.0
    for sig in ordered:
        p = sig.effective / 100.0
        marginal = unresolved * p * 100.0
        fused += marginal
        unresolved *= 1.0 - p
        contributions.append(
            Contribution(
                signal_id=sig.id,
                category=sig.category,
                raw_strength=sig.strength,
                confidence=sig.confidence,
                effective_strength=sig.effective,
                marginal_points=round(marginal, 4),
                share=round(marginal / fused, 6) if fused > 0 else 0.0,
            )
        )
    return round(min(100.0, fused), 4), contributions


def score_event(event: Event) -> ScoreBreakdown:
    """score = fused * asset_factor * scope_factor, capped at 100."""
    fused, contributions = fuse_signals(event.signals)
    af = asset_factor(event.asset_tier)
    sf = scope_factor(event.affected)
    score = min(100.0, fused * af * sf)
    return ScoreBreakdown(
        event_id=event.id,
        fused=fused,
        asset_factor=af,
        scope_factor=round(sf, 4),
        score=round(score, 4),
        contributions=contributions,
    )

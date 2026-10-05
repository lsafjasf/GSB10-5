"""Security event triage library (standard library only).

Scoring model (see README.md):
  score = fuse(signals) * asset_factor * scope_factor   (capped at 100)
  fuse uses the probabilistic sum so weak signals stack
  without raw alert count dominating the result.
"""

from .scoring import (
    Signal,
    Event,
    Contribution,
    ScoreBreakdown,
    asset_factor,
    scope_factor,
    fuse_signals,
    score_event,
)
from .level import Level, LEVEL_ORDER, SlaPolicy, DEFAULT_SLA
from .rules import Rule, RuleEngine
from .incident import Incident, Transition, SlaStats

__all__ = [
    "Signal",
    "Event",
    "Contribution",
    "ScoreBreakdown",
    "asset_factor",
    "scope_factor",
    "fuse_signals",
    "score_event",
    "Level",
    "LEVEL_ORDER",
    "SlaPolicy",
    "DEFAULT_SLA",
    "Rule",
    "RuleEngine",
    "Incident",
    "Transition",
    "SlaStats",
]

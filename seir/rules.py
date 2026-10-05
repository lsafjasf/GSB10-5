"""Deterministic triage rules with explicit conflict resolution.

Conflict rules (deterministic, no evaluation-order dependence):
  1. Matching rules are sorted by (-priority, name).
  2. "suppress" and "force_level" are terminal: the first (highest
     priority, then lexicographically smallest name) terminal rule
     wins and stops evaluation.
  3. "boost" rules apply only if they sort before the winning
     terminal rule; boosts after it are discarded.
  4. With no terminal rule, all matching boosts apply in sorted order.
So a suppress/force conflict is always settled by priority, then name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

from .level import Level
from .scoring import Event

SUPPRESS = "suppress"
BOOST = "boost"
FORCE_LEVEL = "force_level"


@dataclass(frozen=True)
class Rule:
    name: str
    priority: int                       # higher wins
    condition: Callable[[Event], bool]
    action: str                         # suppress | boost | force_level
    param: float = 0.0                  # boost: extra points; force_level: Level

    def __post_init__(self) -> None:
        if self.action not in (SUPPRESS, BOOST, FORCE_LEVEL):
            raise ValueError(f"unknown action: {self.action}")


@dataclass(frozen=True)
class RuleOutcome:
    applied: List[str]      # rule names that took effect, in order
    suppressed: bool
    forced_level: Optional[Level]
    boost_points: float


class RuleEngine:
    def __init__(self, rules: List[Rule]):
        self._rules = sorted(rules, key=lambda r: (-r.priority, r.name))

    def evaluate(self, event: Event) -> RuleOutcome:
        applied: List[str] = []
        suppressed = False
        forced: Optional[Level] = None
        boost = 0.0
        for rule in self._rules:
            if not rule.condition(event):
                continue
            if rule.action == SUPPRESS:
                suppressed = True
                applied.append(rule.name)
                break
            if rule.action == FORCE_LEVEL:
                forced = Level(int(rule.param))
                applied.append(rule.name)
                break
            boost += rule.param
            applied.append(rule.name)
        return RuleOutcome(
            applied=applied,
            suppressed=suppressed,
            forced_level=forced,
            boost_points=boost,
        )

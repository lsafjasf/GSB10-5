"""Incident lifecycle: deterministic escalation/degradation, SLA tracking.

All time comes from an injected clock (callable returning seconds),
so tests are fully deterministic.

Transition rules (deterministic):
  * Escalate immediately whenever the recomputed level is more severe.
  * Degrade only when the score falls at least HYSTERESIS points below
    the lower bound of the current level (prevents flapping), and only
    one band at a time per update.
  * A forced level (rule) acts as a severity floor: the incident is at
    least that severe, but a more severe computed level still wins;
    suppression pins the incident to P5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .level import DEFAULT_SLA, Level, SlaPolicy, level_for_score, lower_bound
from .rules import RuleEngine
from .scoring import Event, ScoreBreakdown, score_event

HYSTERESIS = 5.0


@dataclass(frozen=True)
class Transition:
    at: float
    from_level: Optional[Level]
    to_level: Level
    score: float
    reason: str


@dataclass(frozen=True)
class LevelAssessment:
    """Result of one update(): the auditable basis list for the level."""

    breakdown: ScoreBreakdown
    adjusted_score: float
    computed_level: Level
    applied_rules: List[str]
    suppressed: bool
    forced_level: Optional[Level]


class Incident:
    def __init__(
        self,
        incident_id: str,
        clock: Callable[[], float],
        sla: SlaPolicy = DEFAULT_SLA,
        rules: Optional[RuleEngine] = None,
    ):
        self.id = incident_id
        self._clock = clock
        self._sla = sla
        self._rules = rules or RuleEngine([])
        self.level: Optional[Level] = None
        self.trajectory: List[Transition] = []
        self.assessments: List[LevelAssessment] = []
        self._level_entered_at: Optional[float] = None
        self._responded_level: Optional[Level] = None
        self.response_time: Optional[float] = None
        self.sla_breached: Optional[bool] = None

    def assess(self, event: Event) -> LevelAssessment:
        """Score the event and apply rules; keep the auditable basis."""
        breakdown = score_event(event)
        outcome = self._rules.evaluate(event)
        score = breakdown.score
        forced = outcome.forced_level
        if outcome.suppressed:
            computed = Level.P5
            score = 0.0
        else:
            score = min(100.0, score + outcome.boost_points)
            computed = level_for_score(score)
            if forced is not None and forced < computed:
                computed = forced
        assessment = LevelAssessment(
            breakdown=breakdown,
            adjusted_score=round(score, 4),
            computed_level=computed,
            applied_rules=list(outcome.applied),
            suppressed=outcome.suppressed,
            forced_level=forced,
        )
        self.assessments.append(assessment)
        return assessment

    def update(self, event: Event) -> Transition:
        """Re-assess the incident with new evidence; apply transitions."""
        now = self._clock()
        assessment = self.assess(event)
        target = assessment.computed_level
        score = assessment.adjusted_score

        if self.level is None:
            return self._transition(now, None, target, score, "initial")

        if assessment.suppressed or assessment.forced_level is not None:
            if target != self.level:
                reason = "rule:suppress" if assessment.suppressed else "rule:force"
                return self._transition(now, self.level, target, score, reason)
            return self._transition(now, self.level, self.level, score, "unchanged")

        if target < self.level:  # IntEnum: smaller == more severe
            return self._transition(now, self.level, target, score, "escalate")

        if target > self.level:
            bound = lower_bound(self.level)
            if score <= bound - HYSTERESIS:
                degraded = Level(self.level + 1)
                return self._transition(now, self.level, degraded, score, "degrade")

        return self._transition(now, self.level, self.level, score, "unchanged")

    def _transition(
        self, at: float, old: Optional[Level], new: Level, score: float, reason: str
    ) -> Transition:
        t = Transition(at=at, from_level=old, to_level=new, score=score, reason=reason)
        self.trajectory.append(t)
        if old != new:
            self.level = new
            self._level_entered_at = at
        return t

    def respond(self) -> float:
        """Record the response at the injected current time."""
        if self.level is None or self._level_entered_at is None:
            raise RuntimeError("incident has no active level")
        now = self._clock()
        self.response_time = now - self._level_entered_at
        self._responded_level = self.level
        self.sla_breached = self.response_time > self._sla.deadline(self.level)
        return self.response_time

    def sla_deadline(self) -> float:
        if self.level is None or self._level_entered_at is None:
            raise RuntimeError("incident has no active level")
        return self._level_entered_at + self._sla.deadline(self.level)


@dataclass
class SlaStats:
    """Aggregated response-time statistics per level."""

    total: int = 0
    responded: int = 0
    breached: int = 0
    response_times: List[float] = field(default_factory=list)

    @property
    def breach_rate(self) -> float:
        return self.breached / self.total if self.total else 0.0

    @property
    def avg_response(self) -> float:
        return sum(self.response_times) / len(self.response_times) if self.response_times else 0.0


def sla_report(incidents: List[Incident], sla: SlaPolicy = DEFAULT_SLA):
    """Per-level response-time and breach statistics.

    Unresponded incidents count as breached if the injected clock has
    already passed their deadline.
    """
    stats = {level: SlaStats() for level in Level}
    for inc in incidents:
        if inc.level is None:
            continue
        bucket = stats[inc.level]
        bucket.total += 1
        if inc.response_time is not None:
            bucket.responded += 1
            bucket.response_times.append(inc.response_time)
            if inc.sla_breached:
                bucket.breached += 1
        else:
            now = inc._clock()
            if now > inc.sla_deadline():
                bucket.breached += 1
    return stats

"""Result/status value objects shared by runner, reports and tests."""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class Mode(str, Enum):
    DRILL = "DRILL"
    REAL = "REAL"


class Status(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class Phase(str, Enum):
    EXEC = "EXEC"
    COMPENSATE = "COMPENSATE"


class FailPolicy(str, Enum):
    """Deterministic handling when a step fails.

    ABORT   - stop the pipeline; later steps are SKIPPED; compensations run.
    ROLLBACK - abort and run this step's own compensation as part of unwind.
    MARK    - stop the pipeline and require a human decision (MANUAL_MARK).
    """

    ABORT = "ABORT"
    ROLLBACK = "ROLLBACK"
    MARK = "MARK"


@dataclass
class Check:
    """One judgement attached to a step: name, measured data, pass/fail."""

    name: str
    expected: str
    actual: str
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StepResult:
    index: int
    step_id: str
    name: str
    status: Status
    started_at: float
    duration_ms: float
    checks: list[Check] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    compensated: bool = False
    compensation_detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status is Status.SUCCESS and all(c.passed for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class RunResult:
    mode: Mode
    run_id: str
    plan_id: str
    plan_signature: str
    overall: Status
    started_at: float
    ended_at: float
    total_ms: float
    steps: list[StepResult] = field(default_factory=list)
    marked_for_manual: bool = False
    mark_reason: Optional[str] = None
    timeout: bool = False
    timeout_after_ms: Optional[float] = None
    resources: dict[str, float] = field(default_factory=dict)
    audit_path: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "run_id": self.run_id,
            "plan_id": self.plan_id,
            "plan_signature": self.plan_signature,
            "overall": self.overall.value,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "total_ms": self.total_ms,
            "steps": [s.to_dict() for s in self.steps],
            "marked_for_manual": self.marked_for_manual,
            "mark_reason": self.mark_reason,
            "timeout": self.timeout,
            "timeout_after_ms": self.timeout_after_ms,
            "resources": self.resources,
            "audit_path": self.audit_path,
        }

    def find(self, step_id: str) -> Optional[StepResult]:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None

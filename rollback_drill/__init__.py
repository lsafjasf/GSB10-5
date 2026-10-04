"""Periodic rollback-drill validation library.

The same :class:`~rollback_drill.runner.DrillRunner` executes both a real
production rollback and a rollback drill; only the :class:`~rollback_drill.target.Plan`
binding (target, mode label, snapshot) differs.  See docs/SCOPE.md for the
scope rationale and docs/PATH_PARITY.md for the step-by-step parity table.
"""

from .clock import Clock, FakeClock
from .model import (
    Mode,
    Status,
    FailPolicy,
    Check,
    StepResult,
    RunResult,
)
from .target import FileDataTarget, Plan, build_steps, FileEnv
from .runner import DrillRunner, FaultPlan, DeadlineExceeded, make_real_plan, make_drill_plan
from .scheduler import DrillScheduler, ScheduleResult
from .audit import JsonlAuditSink, AuditEvent

__all__ = [
    "Clock",
    "FakeClock",
    "Mode",
    "Status",
    "FailPolicy",
    "Check",
    "StepResult",
    "RunResult",
    "FileDataTarget",
    "Plan",
    "build_steps",
    "FileEnv",
    "DrillRunner",
    "FaultPlan",
    "DeadlineExceeded",
    "make_real_plan",
    "make_drill_plan",
    "DrillScheduler",
    "ScheduleResult",
    "JsonlAuditSink",
    "AuditEvent",
]

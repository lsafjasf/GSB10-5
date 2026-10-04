"""The rollback pipeline.

Drills and real rollbacks run through the *same* :meth:`DrillRunner.run`
loop over the *same* frozen step list (:func:`rollback_drill.target.build_steps`).
The only difference is the :class:`Plan`: mode label, root directory
(drill clone vs production live), and audit sink.
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .audit import AuditEvent, JsonlAuditSink
from .clock import Clock, SystemClock
from .model import FailPolicy, Mode, RunResult, Status, StepResult
from .resource import ResourceMeter
from .target import (
    STEP_IMPLS,
    FileDataTarget,
    FileEnv,
    Plan,
    Step,
    build_steps,
    tree_digest,
)


class DeadlineExceeded(Exception):
    def __init__(self, step_id: str, limit_s: float):
        super().__init__(f"deadline exceeded at {step_id} after {limit_s}s")
        self.step_id = step_id
        self.limit_s = limit_s


@dataclass
class FaultPlan:
    """Deterministic fault injection, controlled by tests.

    fail_step       - this step raises before executing (single-step failure).
    slow_step       - this step burns ``slow_seconds`` of injected time first.
    bypass_after    - after these steps, a stray writer commits a write that
                      bypasses the fence (simulates mid-run writes).
    jump_after      - after this step, the injected clock jumps N seconds.
    """

    fail_step: Optional[str] = None
    slow_step: Optional[str] = None
    slow_seconds: float = 0.0
    bypass_after: tuple[str, ...] = ()
    jump_after: Optional[str] = None
    jump_seconds: float = 0.0


@dataclass
class _Ctx:
    run_id: str
    clock: Clock
    meter: ResourceMeter
    fence_watermark: int = -1
    safety_dir: Optional[Path] = None


def make_real_plan(env: FileEnv, run_id_seed: str = "prod",
                   step_timeout_s: float = 30.0,
                   total_timeout_s: float = 300.0) -> Plan:
    target = FileDataTarget(env.live, env.snap, env.meta)
    return Plan(
        plan_id=f"plan-real-{run_id_seed}",
        mode=Mode.REAL,
        target=target,
        snapshot_dir=env.snap,
        audit_path=str(env.audit),
        step_timeout_s=step_timeout_s,
        total_timeout_s=total_timeout_s,
        label="production",
    )


def make_drill_plan(clone: FileEnv, run_id_seed: str = "drill",
                    step_timeout_s: float = 30.0,
                    total_timeout_s: float = 300.0) -> Plan:
    target = FileDataTarget(clone.live, clone.snap, clone.meta)
    return Plan(
        plan_id=f"plan-drill-{run_id_seed}",
        mode=Mode.DRILL,
        target=target,
        snapshot_dir=clone.snap,
        audit_path=str(clone.audit),
        step_timeout_s=step_timeout_s,
        total_timeout_s=total_timeout_s,
        label="drill-clone",
    )


class DrillRunner:
    def __init__(self, clock: Optional[Clock] = None,
                 audit_sink: Optional[JsonlAuditSink] = None):
        self.clock = clock or SystemClock()
        self.audit = audit_sink or JsonlAuditSink(None)
        self.steps: list[Step] = build_steps()

    # ------------------------------------------------------------------
    def run(self, plan: Plan, fault: Optional[FaultPlan] = None,
            run_id: Optional[str] = None) -> RunResult:
        fault = fault or FaultPlan()
        run_id = run_id or f"{plan.mode.value.lower()}-{uuid.uuid4().hex[:8]}"
        meter = ResourceMeter()
        ctx = _Ctx(run_id=run_id, clock=self.clock, meter=meter)
        started = self.clock.now()
        results: list[StepResult] = []
        overall = Status.SUCCESS
        marked = False
        mark_reason: Optional[str] = None
        timed_out = False
        timed_out_step: Optional[str] = None

        self._audit(plan, run_id, "-", "RUN_START", {"steps": len(self.steps)})

        abort = False
        for step in self.steps:
            if abort:
                results.append(self._skipped(step, self.clock.now()))
                continue

            t0 = self.clock.now()
            try:
                if fault.slow_step == step.step_id:
                    self.clock.sleep(fault.slow_seconds)
                    meter.add_bytes(0)
                self._check_deadlines(step, t0, plan, started, self.clock.now())
                if fault.fail_step == step.step_id:
                    raise RuntimeError(f"injected failure at {step.step_id}")
                out = STEP_IMPLS[step.step_id](plan.target, plan, ctx)
                self._check_deadlines(step, t0, plan, started, self.clock.now())
            except DeadlineExceeded:
                timed_out = True
                timed_out_step = step.step_id
                sr = self._failed(step, t0,
                                  f"timeout(>{plan.step_timeout_s}s)")
                sr.duration_ms = (self.clock.now() - t0) * 1000.0
                results.append(sr)
                self._audit(plan, run_id, step.step_id, "STEP_TIMEOUT",
                            {"limit_s": plan.step_timeout_s})
                overall, marked, mark_reason = self._handle_policy(
                    plan, run_id, step, sr, results, timed_out_step)
                abort = True
                continue
            except Exception as exc:  # deterministic failure of this step
                sr = self._failed(step, t0, str(exc))
                sr.duration_ms = (self.clock.now() - t0) * 1000.0
                results.append(sr)
                self._audit(plan, run_id, step.step_id, "STEP_FAILED", {"error": str(exc)})
                overall, marked, mark_reason = self._handle_policy(
                    plan, run_id, step, sr, results, None)
                abort = True
                continue

            sr = StepResult(
                index=step.index, step_id=step.step_id, name=step.name,
                status=Status.SUCCESS, started_at=t0,
                duration_ms=(self.clock.now() - t0) * 1000.0,
                checks=out["checks"], detail=out["detail"],
            )
            if not sr.ok:
                sr.status = Status.FAILED
                sr.error = "check failed: " + ",".join(
                    c.name for c in sr.checks if not c.passed)
                results.append(sr)
                self._audit(plan, run_id, step.step_id, "STEP_FAILED",
                            {"error": sr.error})
                overall, marked, mark_reason = self._handle_policy(
                    plan, run_id, step, sr, results, None)
                abort = True
                continue
            if step.step_id == "s03_fence_writes":
                ctx.fence_watermark = plan.target.watermark()
                sr.detail["fence_watermark"] = ctx.fence_watermark
            results.append(sr)
            self._audit(plan, run_id, step.step_id, "STEP_OK",
                        {"duration_ms": round(sr.duration_ms, 3)})
            copied = int(out["detail"].pop("_bytes_copied", 0) or 0)
            files = int(out["detail"].pop("_files_copied", 0) or 0)
            if copied:
                meter.add_bytes(copied)
                meter.add_files(files)

            if step.step_id in fault.bypass_after:
                plan.target.probe_write(self.clock)
                self._audit(plan, run_id, step.step_id, "BYPASS_WRITE_DETECTED",
                            {"watermark": plan.target.watermark()})
            if fault.jump_after == step.step_id:
                self.clock.sleep(fault.jump_seconds)

        ended = self.clock.now()
        if not abort:
            overall = Status.SUCCESS
            self._audit(plan, run_id, "-", "RUN_COMMIT", {})

        # best-effort safety cleanup (data is already restored or rolled back)
        if ctx.safety_dir and ctx.safety_dir.exists():
            shutil.rmtree(ctx.safety_dir, ignore_errors=True)

        res = RunResult(
            mode=plan.mode, run_id=run_id, plan_id=plan.plan_id,
            plan_signature=plan.signature(), overall=overall,
            started_at=started, ended_at=ended,
            total_ms=(ended - started) * 1000.0, steps=results,
            marked_for_manual=marked, mark_reason=mark_reason,
            timeout=timed_out,
            timeout_after_ms=(plan.step_timeout_s * 1000.0 if timed_out else None),
            resources=meter.snapshot(),
            audit_path=plan.audit_path,
        )
        self._audit(plan, run_id, "-", "RUN_END",
                    {"overall": res.overall.value, "total_ms": round(res.total_ms, 3)})
        return res

    # ------------------------------------------------------------------
    def _handle_policy(self, plan, run_id, step: Step, sr: StepResult,
                       results: list[StepResult], timed_out_step):
        overall = Status.FAILED
        marked = False
        mark_reason = None
        if step.policy is FailPolicy.MARK:
            marked = True
            mark_reason = step.mark_status or "MANUAL"
            sr.detail["manual_mark"] = mark_reason
            self._audit(plan, run_id, step.step_id, "MANUAL_MARK",
                        {"mark": mark_reason})
        elif step.policy is FailPolicy.ROLLBACK:
            self._compensate(plan, run_id, step, sr, results)
        else:  # ABORT
            self._compensate(plan, run_id, step, sr, results)
        return overall, marked, mark_reason

    def _compensate(self, plan, run_id, step: Step, sr: StepResult,
                    results: list[StepResult]) -> None:
        method = step.compensation
        if not method:
            sr.compensated = False
            return
        try:
            if method == "restore_safety_copy":
                safety = self._find_safety(results)
                if safety is None:
                    raise RuntimeError("no safety copy available for rollback")
                target = plan.target
                shutil.rmtree(target.live)
                shutil.copytree(safety, target.live)
                fp = tree_digest(target.live)
                sr.compensation_detail = {"restored_from": str(safety), "fp": fp}
            else:
                getattr(plan.target, method)(self.clock)
                sr.compensation_detail = {"method": method}
            sr.compensated = True
            self._audit(plan, run_id, step.step_id, "COMPENSATED",
                        sr.compensation_detail)
        except Exception as exc:
            sr.compensated = False
            sr.compensation_detail = {"error": str(exc)}
            self._audit(plan, run_id, step.step_id, "COMPENSATE_FAILED",
                        {"error": str(exc)})

    @staticmethod
    def _find_safety(results: list[StepResult]) -> Optional[Path]:
        s04 = next((r for r in results if r.step_id == "s04_capture_safety"), None)
        if s04 and s04.ok:
            p = Path(s04.detail["safety_dir"])
            return p if p.exists() else None
        return None

    def _check_deadlines(self, step, t0: float, plan: Plan, run_start: float,
                         now: float) -> None:
        if now - t0 > plan.step_timeout_s:
            raise DeadlineExceeded(step.step_id, plan.step_timeout_s)
        if now - run_start > plan.total_timeout_s:
            raise DeadlineExceeded(step.step_id, plan.total_timeout_s)

    @staticmethod
    def _failed(step: Step, t0: float, error: str) -> StepResult:
        from .clock import SystemClock  # noqa: F401
        return StepResult(
            index=step.index, step_id=step.step_id, name=step.name,
            status=Status.FAILED, started_at=t0, duration_ms=0.0, error=error,
        )

    @staticmethod
    def _skipped(step: Step, now: float) -> StepResult:
        return StepResult(
            index=step.index, step_id=step.step_id, name=step.name,
            status=Status.SKIPPED, started_at=now, duration_ms=0.0,
            detail={"reason": "pipeline already aborted"},
        )

    def _audit(self, plan, run_id, step_id, event, detail) -> None:
        self.audit.emit(AuditEvent(
            ts=self.clock.now(), run_id=run_id, mode=plan.mode.value,
            step_id=step_id, event=event, detail=detail,
        ))

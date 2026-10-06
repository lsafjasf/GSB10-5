"""执行器：编排状态机 + 检查点续跑 + 人工接管 + 中断安全退出。

不变式：
1. CONFIRM/APPROVAL/MANUAL 步骤只有拿到 Operator 的明确决策才会前进；
   Operator 返回 None（人不在）时，执行器落检查点并停在 WAITING_HUMAN。
2. 每个步骤的输入/输出都写入轨迹（step_input / step_output）。
3. 任何非终态都可以 takeover() 交出控制权，或 abort() 安全退出。
4. 中断（interrupt() 或 SIGINT/SIGTERM）在步骤边界生效，现场落盘后可续跑。
"""
from __future__ import annotations

import json
import os
import signal
import uuid
from typing import Any, Dict, List, Optional

from .models import Context, RunState, Runbook, Step, StepKind, StepRecord
from .operator import Decision, Operator
from .trace import TraceRecorder, utc_now


def new_run_id() -> str:
    return "run-" + uuid.uuid4().hex[:12]


class Executor:
    def __init__(
        self,
        runbook: Runbook,
        operator: Operator,
        trace: TraceRecorder,
        checkpoint_dir: Optional[str] = None,
        run_id: Optional[str] = None,
        install_signal_handlers: bool = False,
        step_observer: Optional[Any] = None,
    ):
        self.runbook = runbook
        self.operator = operator
        self.trace = trace
        self.run_id = run_id or new_run_id()
        self.state = RunState.PENDING
        self.records: Dict[str, StepRecord] = {}
        self.data: Dict[str, Any] = {}          # 跨步骤共享数据
        self.next_index = 0                      # 下一个待执行步骤下标
        self._interrupt_requested = False
        self._abort_requested = False
        self._step_observer = step_observer
        self.checkpoint_dir = checkpoint_dir
        self.checkpoint_path = (
            os.path.join(checkpoint_dir, f"{self.run_id}.checkpoint.json")
            if checkpoint_dir else None
        )
        if install_signal_handlers:
            self._install_signals()

    # ------------------------------------------------------------------ 信号
    def _install_signals(self) -> None:
        def _handler(signum: int, _frame: Any) -> None:
            self.interrupt(reason=f"signal:{signum}")

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)

    # -------------------------------------------------------------- 外部控制
    def interrupt(self, reason: str = "external") -> None:
        """请求中断：在当前步骤结束后落检查点并停住（可续跑）。"""
        self._interrupt_requested = True
        self._interrupt_reason = reason

    def abort(self, reason: str = "operator") -> None:
        """请求安全退出：不再执行任何后续步骤，状态收敛为 ABORTED。"""
        self._abort_requested = True
        self._abort_reason = reason

    # ---------------------------------------------------------------- 检查点
    def _save_checkpoint(self) -> None:
        if not self.checkpoint_path:
            return
        os.makedirs(os.path.dirname(self.checkpoint_path), exist_ok=True)
        payload = {
            "run_id": self.run_id,
            "book_id": self.runbook.book_id,
            "book_version": self.runbook.version,
            "state": self.state.value,
            "next_index": self.next_index,
            "records": {k: v.to_dict() for k, v in self.records.items()},
            "data": self.data,
        }
        tmp = self.checkpoint_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.checkpoint_path)  # 原子替换，防半写
        self.trace.emit("checkpoint", path=self.checkpoint_path,
                        state=self.state.value, next_index=self.next_index)

    def _restore(self, payload: Dict[str, Any]) -> None:
        if payload["book_id"] != self.runbook.book_id:
            raise ValueError(
                f"检查点属于手册 {payload['book_id']}，当前为 {self.runbook.book_id}"
            )
        self.run_id = payload["run_id"]
        self.state = RunState(payload["state"])
        self.next_index = payload["next_index"]
        self.records = {
            k: StepRecord.from_dict(v) for k, v in payload["records"].items()
        }
        self.data = payload.get("data", {})

    @classmethod
    def resume(
        cls,
        checkpoint_path: str,
        runbook: Runbook,
        operator: Operator,
        trace: TraceRecorder,
    ) -> "Executor":
        """从检查点续跑：已完成的步骤跳过，从断点继续。"""
        payload = load_checkpoint(checkpoint_path)
        ex = cls(runbook, operator, trace,
                 checkpoint_dir=os.path.dirname(checkpoint_path),
                 run_id=payload["run_id"])
        ex.checkpoint_path = checkpoint_path
        ex._restore(payload)
        ex.trace.run_id = ex.run_id
        ex.trace.emit("resume", state=ex.state.value, next_index=ex.next_index,
                      completed_steps=list(ex.records))
        return ex

    # ---------------------------------------------------------------- 主循环
    def run(self) -> RunState:
        """执行到终态 / 等待态 / 中断态为止，返回最终状态。"""
        if self.state.is_terminal:
            return self.state
        if self.state is RunState.PENDING:
            self.trace.emit("run_start", book=self.runbook.name,
                            steps=[s.step_id for s in self.runbook.steps])
        self.state = RunState.RUNNING
        steps = self.runbook.steps

        while self.next_index < len(steps):
            if self._abort_requested:
                self.state = RunState.ABORTED
                self.trace.emit("abort", reason=self._abort_reason,
                                next_index=self.next_index)
                self._save_checkpoint()
                return self.state
            if self._interrupt_requested:
                self.state = RunState.INTERRUPTED
                self.trace.emit("interrupt", reason=self._interrupt_reason,
                                next_index=self.next_index)
                self._save_checkpoint()
                return self.state

            step = steps[self.next_index]
            outcome = self._run_step(step)
            if outcome == "wait":
                self.state = RunState.WAITING_HUMAN
                self._save_checkpoint()
                return self.state
            if outcome == "fail":
                self.state = RunState.FAILED
                self.trace.emit("run_end", state=self.state.value)
                self._save_checkpoint()
                return self.state
            self.next_index += 1
            if self._step_observer is not None:
                self._step_observer(self, step)

        self.state = RunState.COMPLETED
        self.trace.emit("run_end", state=self.state.value)
        self._save_checkpoint()
        return self.state

    # ---------------------------------------------------------------- 单步
    def _context(self) -> Context:
        ctx = Context()
        ctx["run_id"] = self.run_id
        ctx["book_id"] = self.runbook.book_id
        ctx["data"] = self.data
        ctx["records"] = {k: v.to_dict() for k, v in self.records.items()}
        return ctx

    def _run_step(self, step: Step) -> str:
        """返回 ok / wait / fail。"""
        rec = StepRecord(step_id=step.step_id, status="running",
                         started_at=utc_now())
        # 输入核对：参数 + 可见上下文快照
        self.trace.emit("step_input", step.step_id,
                        title=step.title, kind=step.kind.value,
                        params=step.params, shared_data=dict(self.data))

        if step.kind is StepKind.AUTO:
            return self._run_auto(step, rec)
        return self._run_gated(step, rec)

    def _run_auto(self, step: Step, rec: StepRecord) -> str:
        try:
            output = step.handler(step, self._context()) if step.handler else {}
        except Exception as exc:  # 处理器异常 = 步骤失败
            rec.status = "failed"
            rec.error = f"{type(exc).__name__}: {exc}"
            rec.finished_at = utc_now()
            self.records[step.step_id] = rec
            self.trace.emit("step_output", step.step_id, status="failed",
                            error=rec.error)
            return "ok" if step.continue_on_failure else "fail"
        rec.status = "ok"
        rec.output = output
        rec.finished_at = utc_now()
        self.records[step.step_id] = rec
        self.data[step.step_id] = output
        self.trace.emit("step_output", step.step_id, status="ok", output=output)
        return "ok"

    def _run_gated(self, step: Step, rec: StepRecord) -> str:
        """人工闸门：拿不到明确决策就停，绝不自动越过。"""
        self.trace.emit("wait_human", step.step_id, kind=step.kind.value,
                        prompt=step.prompt)
        if step.kind is StepKind.CONFIRM:
            decision = self.operator.confirm(step, self._context())
        elif step.kind is StepKind.APPROVAL:
            decision = self.operator.approve(step, self._context())
        else:  # MANUAL
            decision = self.operator.manual(step, self._context())

        if decision is None:
            # 人不在：停住等待，轨迹里已有 wait_human 事件
            return "wait"

        self.trace.emit("human_decision", step.step_id, **decision.to_dict())
        rec.decision_by = decision.by
        rec.finished_at = utc_now()

        if decision.action == "pause":
            return "wait"
        if decision.action == "reject":
            rec.status = "rejected"
            rec.output = {"note": decision.note}
            self.records[step.step_id] = rec
            self.trace.emit("step_output", step.step_id, status="rejected",
                            by=decision.by, note=decision.note)
            return "ok" if step.continue_on_failure else "fail"
        # approve / done
        rec.status = "ok" if decision.action == "approve" else "manual_done"
        rec.output = {"note": decision.note}
        self.records[step.step_id] = rec
        self.data[step.step_id] = rec.output
        self.trace.emit("step_output", step.step_id, status=rec.status,
                        by=decision.by, note=decision.note)
        return "ok"

    # ---------------------------------------------------------------- 接管
    def takeover(self, operator_name: str) -> Dict[str, Any]:
        """人工接管：任何非终态都可调用。

        返回接管点数据；状态转为 HUMAN_CONTROLLED，现场落检查点。
        接管后：人可手工处理剩余步骤，再用 resume() 交还执行器，
        或 abort() 安全退出。
        """
        if self.state.is_terminal:
            raise RuntimeError(f"终态 {self.state.value} 不可接管")
        steps = self.runbook.steps
        point = {
            "run_id": self.run_id,
            "book_id": self.runbook.book_id,
            "book_name": self.runbook.name,
            "state_before": self.state.value,
            "state_after": RunState.HUMAN_CONTROLLED.value,
            "taken_over_by": operator_name,
            "taken_over_at": utc_now(),
            "next_index": self.next_index,
            "current_step": (
                steps[self.next_index].step_id
                if self.next_index < len(steps) else None
            ),
            "completed_steps": [
                {"step_id": r.step_id, "status": r.status,
                 "output": r.output, "decision_by": r.decision_by}
                for r in self.records.values()
            ],
            "pending_steps": [
                {"step_id": s.step_id, "title": s.title, "kind": s.kind.value}
                for s in steps[self.next_index:]
            ],
            "shared_data": dict(self.data),
            "checkpoint_path": self.checkpoint_path,
        }
        self.state = RunState.HUMAN_CONTROLLED
        self.trace.emit("takeover", **point)
        self._save_checkpoint()
        return point


def load_checkpoint(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)

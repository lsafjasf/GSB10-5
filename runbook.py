"""值班手册自动化执行库（仅标准库）。

核心能力：
- 步骤编排：auto / confirm / approval 三类步骤，confirm 与 approval 必须等人决策，绝不自动冲过。
- 执行轨迹：JSONL 追加写，哈希链防篡改，每步输入输出可核对。
- 人工接管：暂停点随时可 takeover，返回接管点数据，状态置为 manual。
- 中断恢复：每步完成后落检查点（原子写），中断后可 resume 续跑或 abort 安全退出。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional


class StepKind(str, Enum):
    AUTO = "auto"          # 全自动步骤：执行器直接跑
    CONFIRM = "confirm"    # 人工确认步骤：必须等值班人确认
    APPROVAL = "approval"  # 审批步骤：必须等审批人决策


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"            # 停在人工步骤，等决策
    INTERRUPTED = "interrupted"  # 被打断（如 Ctrl-C），检查点已落盘
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"        # 审批被拒
    ABORTED = "aborted"          # 安全退出
    MANUAL = "manual"            # 已被人工接管，执行器不再驱动


TERMINAL = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.REJECTED,
            RunStatus.ABORTED, RunStatus.MANUAL}


@dataclass
class Step:
    id: str
    name: str
    kind: StepKind
    action: str = ""            # AUTO 步骤的动作名（注册表查）
    params: dict = field(default_factory=dict)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "kind": self.kind.value,
                "action": self.action, "params": self.params}

    @staticmethod
    def from_dict(d):
        return Step(d["id"], d["name"], StepKind(d["kind"]),
                    d.get("action", ""), d.get("params", {}))


@dataclass
class Runbook:
    title: str
    steps: list
    context: dict = field(default_factory=dict)
    id: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = "rb-" + uuid.uuid4().hex[:8]

    def to_dict(self):
        return {"id": self.id, "title": self.title,
                "steps": [s.to_dict() for s in self.steps],
                "context": self.context}

    @staticmethod
    def from_dict(d):
        return Runbook(d["title"], [Step.from_dict(s) for s in d["steps"]],
                       d.get("context", {}), d["id"])


@dataclass
class Decision:
    approved: bool
    decided_by: str
    note: str = ""

    def to_dict(self):
        return {"approved": self.approved, "decided_by": self.decided_by,
                "note": self.note}


# 决策提供者：收到 (step, context)，返回 Decision 表示人已拍板；返回 None 表示
# 还没人决策 -> 执行器必须暂停等待，绝不自行继续。
DecisionProvider = Callable[[Step, dict], Optional[Decision]]


# ---------------------------------------------------------------- 动作注册表
ACTIONS: dict = {}

def action(name: str):
    def deco(fn):
        ACTIONS[name] = fn
        return fn
    return deco


# ---------------------------------------------------------------- 轨迹记录
class TraceRecorder:
    """JSONL 追加写轨迹，哈希链（prev_hash -> hash）保证可核对、防篡改。"""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self._prev = "0" * 64
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self._seq = rec["seq"]
                    self._prev = rec["hash"]

    @staticmethod
    def _hash(rec: dict) -> str:
        blob = json.dumps(rec, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(blob).hexdigest()

    def record(self, run_id: str, event: str, step_id: str = "", payload=None):
        rec = {"seq": self._seq + 1, "ts": round(time.time(), 6),
               "run_id": run_id, "event": event, "step_id": step_id,
               "payload": payload or {}, "prev_hash": self._prev}
        rec["hash"] = self._hash(rec)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._seq, self._prev = rec["seq"], rec["hash"]
        return rec


def verify_trace(path) -> tuple:
    """核对轨迹：序号连续 + 哈希链完整。返回 (ok, problems)。"""
    problems, prev, seq = [], "0" * 64, 0
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        rec = json.loads(line)
        seq += 1
        if rec["seq"] != seq:
            problems.append(f"line {n}: seq {rec['seq']} != {seq}")
        if rec["prev_hash"] != prev:
            problems.append(f"line {n}: prev_hash mismatch")
        body = {k: v for k, v in rec.items() if k != "hash"}
        if TraceRecorder._hash(body) != rec["hash"]:
            problems.append(f"line {n}: hash mismatch")
        prev = rec["hash"]
    return (not problems, problems)


# ---------------------------------------------------------------- 检查点
class CheckpointStore:
    """每步完成后原子落盘（tmp + rename），崩溃也不会拿到半个文件。"""

    def __init__(self, directory):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id):
        return self.dir / f"{run_id}.checkpoint.json"

    def save(self, state: dict):
        tmp = self.dir / f".{state['run_id']}.tmp"
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, self._path(state["run_id"]))

    def load(self, run_id) -> dict:
        return json.loads(self._path(run_id).read_text(encoding="utf-8"))

    def exists(self, run_id) -> bool:
        return self._path(run_id).exists()


# ---------------------------------------------------------------- 执行器
class Executor:
    def __init__(self, store_dir, trace_path=None):
        self.store = CheckpointStore(store_dir)
        self.trace = TraceRecorder(trace_path or Path(store_dir) / "trace.jsonl")

    # ---- 状态序列化
    @staticmethod
    def _state(runbook: Runbook) -> dict:
        return {"run_id": "run-" + uuid.uuid4().hex[:8],
                "runbook": runbook.to_dict(),
                "step_index": 0,
                "step_status": ["pending"] * len(runbook.steps),
                "context": dict(runbook.context),
                "status": RunStatus.PENDING.value,
                "decisions": [],
                "created_ts": time.time(), "updated_ts": time.time()}

    def _checkpoint(self, state: dict):
        state["updated_ts"] = time.time()
        self.store.save(state)

    # ---- 对外入口
    def start(self, runbook: Runbook, provider: DecisionProvider = None) -> dict:
        state = self._state(runbook)
        self.trace.record(state["run_id"], "run_started",
                          payload={"runbook": runbook.title,
                                   "steps": len(runbook.steps),
                                   "context": state["context"]})
        self._checkpoint(state)
        return self._execute(state, provider)

    def resume(self, run_id: str, provider: DecisionProvider = None) -> dict:
        state = self.store.load(run_id)
        if state["status"] == RunStatus.MANUAL.value:
            raise RuntimeError(f"{run_id} 已被人工接管，执行器不得继续驱动")
        if state["status"] in {s.value for s in TERMINAL} - {RunStatus.MANUAL.value}:
            raise RuntimeError(f"{run_id} 已终态({state['status']})，无法续跑")
        self.trace.record(run_id, "run_resumed",
                          payload={"from_step_index": state["step_index"],
                                   "from_status": state["status"]})
        return self._execute(state, provider)

    def abort(self, run_id: str, reason: str, operator: str = "system") -> dict:
        """安全退出：不再执行任何后续步骤，状态落盘为 aborted。"""
        state = self.store.load(run_id)
        state["status"] = RunStatus.ABORTED.value
        self.trace.record(run_id, "run_aborted",
                          payload={"reason": reason, "operator": operator,
                                   "at_step_index": state["step_index"]})
        self._checkpoint(state)
        return state

    def takeover(self, run_id: str, operator: str) -> dict:
        """人工接管：返回接管点数据，状态置 manual，执行器停手。"""
        state = self.store.load(run_id)
        if state["status"] in {RunStatus.COMPLETED.value, RunStatus.FAILED.value,
                               RunStatus.REJECTED.value, RunStatus.ABORTED.value}:
            raise RuntimeError(f"{run_id} 已终态，无需接管")
        idx = state["step_index"]
        steps = state["runbook"]["steps"]
        pending = steps[idx] if idx < len(steps) else None
        point = {"run_id": run_id, "operator": operator,
                 "status_before": state["status"],
                 "status_after": RunStatus.MANUAL.value,
                 "paused_at_step_index": idx,
                 "pending_step": pending,
                 "remaining_steps": steps[idx:],
                 "step_status": list(state["step_status"]),
                 "context": dict(state["context"]),
                 "decisions": list(state["decisions"]),
                 "checkpoint_path": str(self.store._path(run_id)),
                 "trace_seq": self.trace._seq, "ts": time.time()}
        state["status"] = RunStatus.MANUAL.value
        self.trace.record(run_id, "takeover",
                          payload={"operator": operator, "at_step_index": idx,
                                   "pending_step": (pending or {}).get("id")})
        self._checkpoint(state)
        return point

    # ---- 主循环
    def _execute(self, state: dict, provider: DecisionProvider) -> dict:
        runbook = Runbook.from_dict(state["runbook"])
        state["status"] = RunStatus.RUNNING.value
        self._checkpoint(state)
        try:
            while state["step_index"] < len(runbook.steps):
                i = state["step_index"]
                step = runbook.steps[i]
                state["step_status"][i] = "running"
                self.trace.record(state["run_id"], "step_started", step.id,
                                  {"name": step.name, "kind": step.kind.value,
                                   "inputs": step.params})
                proceed = True
                if step.kind is StepKind.AUTO:
                    self._run_auto(state, step, i)
                else:
                    proceed = self._run_manual(state, step, i, provider)
                if not proceed:
                    return state  # 无人拍板 -> 暂停等待
                state["step_index"] = i + 1
                self._checkpoint(state)
                if state["status"] != RunStatus.RUNNING.value:
                    break  # 拒绝/失败等终态：绝不往后冲
        except _RunFailed:
            return state
        except KeyboardInterrupt:
            state["status"] = RunStatus.INTERRUPTED.value
            self.trace.record(state["run_id"], "run_interrupted",
                              payload={"at_step_index": state["step_index"]})
            self._checkpoint(state)
            return state
        if state["status"] == RunStatus.RUNNING.value:
            state["status"] = RunStatus.COMPLETED.value
            self.trace.record(state["run_id"], "run_completed",
                              payload={"steps": len(runbook.steps)})
            self._checkpoint(state)
        return state

    def _run_auto(self, state: dict, step: Step, i: int):
        fn = ACTIONS.get(step.action)
        if fn is None:
            self._fail(state, step, i, f"动作未注册: {step.action}")
            raise _RunFailed
        try:
            output = fn(step.params, state["context"])
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - 记录后终止本次运行
            self._fail(state, step, i, f"{type(exc).__name__}: {exc}")
            raise _RunFailed
        state["step_status"][i] = "succeeded"
        self.trace.record(state["run_id"], "step_finished", step.id,
                          {"outputs": output})

    def _fail(self, state, step, i, error):
        state["step_status"][i] = "failed"
        state["status"] = RunStatus.FAILED.value
        self.trace.record(state["run_id"], "step_failed", step.id,
                          {"error": error})
        self.trace.record(state["run_id"], "run_failed",
                          payload={"at_step": step.id, "error": error})
        self._checkpoint(state)

    def _run_manual(self, state, step, i, provider) -> bool:
        """人工步骤：等决策。返回 True 继续，False 暂停。"""
        self.trace.record(state["run_id"], "decision_requested", step.id,
                          {"kind": step.kind.value, "name": step.name})
        decision = provider(step, state["context"]) if provider else None
        if decision is None:
            state["step_status"][i] = "waiting"
            state["status"] = RunStatus.PAUSED.value
            self.trace.record(state["run_id"], "run_paused", step.id,
                              {"reason": "等待人工决策",
                               "kind": step.kind.value})
            self._checkpoint(state)
            return False
        self.trace.record(state["run_id"], "decision_received", step.id,
                          decision.to_dict())
        state["decisions"].append({"step_id": step.id, **decision.to_dict()})
        if decision.approved:
            state["step_status"][i] = "succeeded"
            return True
        state["step_status"][i] = "rejected"
        state["status"] = (RunStatus.REJECTED if step.kind is StepKind.APPROVAL
                           else RunStatus.ABORTED).value
        event = ("run_rejected" if step.kind is StepKind.APPROVAL
                 else "run_aborted")
        self.trace.record(state["run_id"], event, step.id,
                          {"by": decision.decided_by, "note": decision.note})
        self._checkpoint(state)
        return True  # 终态，主循环检查 status 后退出


class _RunFailed(Exception):
    pass


"""手册、步骤、执行状态的领域模型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class StepKind(str, Enum):
    """步骤编排分类。

    AUTO     : 自动步骤，执行器直接调用处理器，无需人参与。
    CONFIRM  : 人工确认步骤，执行器必须停下等人确认/否决，禁止自动越过。
    APPROVAL : 审批步骤，必须停下等待具备身份的审批人批准（留痕审批人）。
    MANUAL   : 人工执行步骤，执行器在接管点交出控制权，由人手工完成后回报。
    """

    AUTO = "auto"
    CONFIRM = "confirm"
    APPROVAL = "approval"
    MANUAL = "manual"


# 处理器签名：(步骤, 上下文) -> 输出字典
Handler = Callable[["Step", "Context"], Dict[str, Any]]


@dataclass
class Step:
    step_id: str
    title: str
    kind: StepKind
    handler: Optional[Handler] = None
    params: Dict[str, Any] = field(default_factory=dict)
    prompt: str = ""
    # 失败时是否允许整个手册继续
    continue_on_failure: bool = False

    def is_automatic(self) -> bool:
        return self.kind is StepKind.AUTO


@dataclass
class Runbook:
    book_id: str
    name: str
    steps: List[Step]
    version: str = "1"

    def __post_init__(self) -> None:
        ids = [s.step_id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError(f"手册 {self.book_id} 存在重复步骤 id: {ids}")


class RunState(str, Enum):
    PENDING = "pending"            # 尚未开始
    RUNNING = "running"            # 自动执行中
    WAITING_HUMAN = "waiting_human"  # 停在人工确认/审批点
    HUMAN_CONTROLLED = "human_controlled"  # 人工接管中
    INTERRUPTED = "interrupted"    # 被打断，已落检查点，可续跑
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"            # 人工要求安全退出

    @property
    def is_terminal(self) -> bool:
        return self in (RunState.COMPLETED, RunState.FAILED, RunState.ABORTED)


@dataclass
class StepRecord:
    """单步执行结果（同时写入轨迹与检查点）。"""

    step_id: str
    status: str                    # ok / rejected / failed / skipped / manual_done
    output: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    decision_by: str = ""          # 人工步骤的决策人
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "status": self.status,
            "output": self.output,
            "error": self.error,
            "decision_by": self.decision_by,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StepRecord":
        return cls(
            step_id=d["step_id"],
            status=d["status"],
            output=d.get("output", {}),
            error=d.get("error", ""),
            decision_by=d.get("decision_by", ""),
            started_at=d.get("started_at", ""),
            finished_at=d.get("finished_at", ""),
        )


class Context(dict):
    """执行上下文：跨步骤共享，同时是轨迹核对的数据面。"""

    def output_of(self, step_id: str) -> Dict[str, Any]:
        rec = self["records"].get(step_id)
        return rec["output"] if rec else {}

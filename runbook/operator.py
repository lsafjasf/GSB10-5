"""操作员接口：人工确认 / 审批 / 手工执行 的唯一入口。

执行器遇到 CONFIRM / APPROVAL / MANUAL 步骤时，只能调用 Operator，
绝不自行跳过。控制台实现会阻塞等待真人输入；脚本化实现用于自测与演示。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import Context, Step


class Decision:
    """人工决策。"""

    def __init__(self, action: str, by: str = "", note: str = ""):
        assert action in ("approve", "reject", "pause", "done")
        self.action = action
        self.by = by
        self.note = note

    def to_dict(self) -> Dict[str, Any]:
        return {"action": self.action, "by": self.by, "note": self.note}


class Operator:
    """人工操作员抽象。返回 None 表示"现在没人"，执行器必须停下等待。"""

    def confirm(self, step: Step, ctx: Context) -> Optional[Decision]:
        raise NotImplementedError

    def approve(self, step: Step, ctx: Context) -> Optional[Decision]:
        raise NotImplementedError

    def manual(self, step: Step, ctx: Context) -> Optional[Decision]:
        raise NotImplementedError


class ConsoleOperator(Operator):
    """真人控制台：阻塞式等待输入，未给结论前执行器原地等待。"""

    def _ask(self, step: Step, verbs: str) -> Optional[Decision]:
        print(f"\n[人工步骤] {step.step_id} - {step.title}")
        if step.prompt:
            print(f"  说明: {step.prompt}")
        raw = input(f"  请选择 ({verbs}, 直接回车=继续等待): ").strip()
        if not raw:
            return None
        parts = raw.split("|", 2)
        action = parts[0].strip()
        by = parts[1].strip() if len(parts) > 1 else "console"
        note = parts[2].strip() if len(parts) > 2 else ""
        return Decision(action, by=by, note=note)

    def confirm(self, step: Step, ctx: Context) -> Optional[Decision]:
        return self._ask(step, "approve|reject|pause [|姓名|备注]")

    def approve(self, step: Step, ctx: Context) -> Optional[Decision]:
        return self._ask(step, "approve|reject|pause |审批人|备注")

    def manual(self, step: Step, ctx: Context) -> Optional[Decision]:
        return self._ask(step, "done|pause [|姓名|备注]")


class ScriptedOperator(Operator):
    """脚本化操作员：按队列给出决策，用于自测与演示。

    决策为 None 时模拟"人不在"，执行器必须停在等待态。
    """

    def __init__(self, decisions: List[Optional[Decision]]):
        self._queue = list(decisions)
        self.calls: List[str] = []

    def _next(self, step: Step) -> Optional[Decision]:
        self.calls.append(step.step_id)
        if not self._queue:
            return None
        return self._queue.pop(0)

    def confirm(self, step: Step, ctx: Context) -> Optional[Decision]:
        return self._next(step)

    def approve(self, step: Step, ctx: Context) -> Optional[Decision]:
        return self._next(step)

    def manual(self, step: Step, ctx: Context) -> Optional[Decision]:
        return self._next(step)


class AutoOperator(Operator):
    """全自动手册使用：手册里不允许出现人工步骤，否则直接报错。

    这保证"全自动手册"的定义被强制执行，而不是靠自觉。
    """

    def _forbid(self, step: Step) -> Optional[Decision]:
        raise RuntimeError(
            f"手册声明为全自动，但步骤 {step.step_id}({step.kind}) 需要人工"
        )

    def confirm(self, step: Step, ctx: Context) -> Optional[Decision]:
        return self._forbid(step)

    def approve(self, step: Step, ctx: Context) -> Optional[Decision]:
        return self._forbid(step)

    def manual(self, step: Step, ctx: Context) -> Optional[Decision]:
        return self._forbid(step)

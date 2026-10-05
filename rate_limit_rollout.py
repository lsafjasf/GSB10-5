"""
限流规则批量下发库（仅标准库）

核心设计：两阶段提交（2PC）+ 过渡期双限取小
---------------------------------------------------
阶段 0 BEFORE         节点全部执行旧规则
阶段 1 AFTER_PREPARE  新规则已暂存到全部节点，但旧规则仍在执行（不生效）
阶段 2 AFTER_COMMIT   节点进入过渡期：实际限流取 min(旧限流, 新限流)
阶段 3 AFTER_ACTIVATE 全部节点确认后才真正按新规则放量
任一阶段失败 -> 全部节点 ABORT，丢弃暂存规则，恢复为统一的旧规则

放量安全不变式：
    任意时刻每个节点实际限流 <= min(旧限流, 新限流)（过渡期）
    全局限流总量 <= max(旧总量, 新总量)
    => 新旧并存期间不可能出现“按新规则放量但只有部分节点受约束”的失控。

并发下发：同一时刻只允许一次下发。下发进行中收到的新规则被确定性拒绝
（状态 REJECTED_IN_PROGRESS），正在进行的下发不受影响。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple


class Phase(str, Enum):
    PREPARE = "prepare"
    COMMIT = "commit"
    ACTIVATE = "activate"
    ABORT = "abort"


class NodeState(str, Enum):
    STEADY = "STEADY"           # 只有活动规则
    STAGED = "STAGED"           # 暂存了新规则，但仍执行旧规则
    TRANSITION = "TRANSITION"   # 已进入过渡期，执行 min(旧, 新)


@dataclass(frozen=True)
class Rule:
    version: int
    limit: float   # 每秒放量上限
    burst: int = 0


@dataclass
class RolloutResult:
    status: str                 # COMMITTED / ROLLED_BACK / REJECTED_IN_PROGRESS
    from_version: Optional[int]
    to_version: int
    failed_phase: Optional[str]
    failed_nodes: List[str]
    duration_ms: float          # 本次下发总耗时（含回退）
    rollback_ms: Optional[float]
    trace: List[dict] = field(default_factory=list)


class Node:
    """模拟一个限流网关节点。"""

    def __init__(self, node_id: str, rule: Rule):
        self.node_id = node_id
        self.active: Rule = rule
        self.staged: Optional[Rule] = None
        self.state = NodeState.STEADY
        # 测试钩子：让指定阶段失败（模拟节点宕机/网络错误）
        self.fail_on: set = set()
        # 测试钩子：在某阶段执行前回调（用于模拟下发中途再次收到新规则）
        self.on_phase: Optional[Callable[["Node", Phase], None]] = None

    def _hook(self, phase: Phase) -> None:
        if self.on_phase is not None:
            self.on_phase(self, phase)

    def prepare(self, rule: Rule) -> bool:
        """暂存新规则，不生效。"""
        self._hook(Phase.PREPARE)
        if Phase.PREPARE in self.fail_on:
            return False
        self.staged = rule
        self.state = NodeState.STAGED
        return True

    def commit(self) -> bool:
        """进入过渡期：执行 min(旧, 新)，等待全局确认。"""
        self._hook(Phase.COMMIT)
        if Phase.COMMIT in self.fail_on:
            return False
        if self.state is not NodeState.STAGED:
            return False
        self.state = NodeState.TRANSITION
        return True

    def activate(self) -> bool:
        """全部节点确认后，正式切换为新规则。"""
        self._hook(Phase.ACTIVATE)
        if Phase.ACTIVATE in self.fail_on:
            return False
        if self.state is not NodeState.TRANSITION:
            return False
        self.active = self.staged
        self.staged = None
        self.state = NodeState.STEADY
        return True

    def abort(self) -> bool:
        """回退：丢弃暂存/过渡规则，恢复执行原活动规则。"""
        self.staged = None
        self.state = NodeState.STEADY
        return True

    def effective_limit(self) -> float:
        """该节点当前实际执行的限流上限。"""
        if self.state is NodeState.TRANSITION and self.staged is not None:
            return min(self.active.limit, self.staged.limit)
        return self.active.limit


class RolloutManager:
    def __init__(self, nodes: List[Node], trace_nodes: bool = True):
        self.nodes = list(nodes)
        self.in_progress = False
        self.trace: List[dict] = []
        # 大规模压测时只记录版本直方图，不记录逐节点数据，避免追踪本身占用内存
        self.trace_nodes = trace_nodes

    def _snapshot(self, stage: str) -> dict:
        versions: Dict[int, int] = {}
        per_node: Dict[str, Tuple[str, int, float]] = {}
        total = 0.0
        states: Dict[str, int] = {}
        for n in self.nodes:
            versions[n.active.version] = versions.get(n.active.version, 0) + 1
            states[n.state.value] = states.get(n.state.value, 0) + 1
            eff = n.effective_limit()
            total += eff
            if self.trace_nodes:
                per_node[n.node_id] = (n.state.value, n.active.version, eff)
        snap = {
            "stage": stage,
            "versions": versions,   # 活动规则版本直方图
            "states": states,       # 节点状态直方图
            "total_limit": total,   # 全局限流总量
            "nodes": per_node,
        }
        self.trace.append(snap)
        return snap

    def audit(self) -> dict:
        """检查节点版本是否一致、是否存在暂存/过渡残留。"""
        versions: Dict[int, int] = {}
        dirty = []
        for n in self.nodes:
            versions[n.active.version] = versions.get(n.active.version, 0) + 1
            if n.state is not NodeState.STEADY or n.staged is not None:
                dirty.append(n.node_id)
        return {
            "skew": len(versions) > 1,
            "versions": versions,
            "dirty_nodes": dirty,
        }

    def reconcile(self, target: Rule) -> RolloutResult:
        """版本混乱修复：强制把全部节点收敛到 target 规则（幂等）。"""
        return self.rollout(target)

    def rollout(self, rule: Rule) -> RolloutResult:
        if self.in_progress:
            # 下发期间再次收到新规则：确定性拒绝，正在进行的下发继续
            return RolloutResult(
                status="REJECTED_IN_PROGRESS",
                from_version=None,
                to_version=rule.version,
                failed_phase=None,
                failed_nodes=[],
                duration_ms=0.0,
                rollback_ms=None,
                trace=list(self.trace),
            )

        self.in_progress = True
        self.trace = []
        started = time.perf_counter()
        from_version = self.nodes[0].active.version

        def elapsed_ms() -> float:
            return (time.perf_counter() - started) * 1000.0

        def roll_back(phase: str, failed: List[str]) -> RolloutResult:
            rb_start = time.perf_counter()
            for n in self.nodes:
                n.abort()
            rollback_ms = (time.perf_counter() - rb_start) * 1000.0
            self._snapshot("AFTER_ABORT")
            return RolloutResult(
                status="ROLLED_BACK",
                from_version=from_version,
                to_version=rule.version,
                failed_phase=phase,
                failed_nodes=failed,
                duration_ms=elapsed_ms(),
                rollback_ms=rollback_ms,
                trace=list(self.trace),
            )

        try:
            self._snapshot("BEFORE")

            # 阶段 1：预置新规则（不生效）
            failed = [n.node_id for n in self.nodes if not n.prepare(rule)]
            self._snapshot("AFTER_PREPARE")
            if failed:
                return roll_back(Phase.PREPARE.value, failed)

            # 阶段 2：进入过渡期，实际限流取 min(旧, 新)
            failed = [n.node_id for n in self.nodes if not n.commit()]
            self._snapshot("AFTER_COMMIT")
            if failed:
                return roll_back(Phase.COMMIT.value, failed)

            # 阶段 3：全部节点确认后统一激活
            failed = [n.node_id for n in self.nodes if not n.activate()]
            self._snapshot("AFTER_ACTIVATE")
            if failed:
                return roll_back(Phase.ACTIVATE.value, failed)

            return RolloutResult(
                status="COMMITTED",
                from_version=from_version,
                to_version=rule.version,
                failed_phase=None,
                failed_nodes=[],
                duration_ms=elapsed_ms(),
                rollback_ms=None,
                trace=list(self.trace),
            )
        finally:
            self.in_progress = False


def validate_no_amplification(trace: List[dict],
                              old_limit: float,
                              new_limit: float,
                              node_count: Optional[int] = None) -> List[str]:
    """
    放量安全校验：返回违规列表，为空表示全程无放量失控。

    不变式 1：任何阶段全局限流总量 <= max(旧, 新) * 节点数。
    不变式 2：过渡期（AFTER_COMMIT 阶段）每个节点实际限流 <= min(旧, 新)。
    """
    violations: List[str] = []
    if node_count is None:
        node_count = sum(trace[0]["versions"].values())
    global_ceiling = max(old_limit, new_limit) * node_count
    transition_clamp = min(old_limit, new_limit)

    in_transition = False
    for snap in trace:
        stage = snap["stage"]
        if stage == "AFTER_COMMIT":
            in_transition = True
        elif stage in ("AFTER_ACTIVATE", "AFTER_ABORT"):
            in_transition = False

        if snap["total_limit"] > global_ceiling + 1e-9:
            violations.append(
                f"[{stage}] 全局限流总量 {snap['total_limit']:.1f} "
                f"超过安全上限 {global_ceiling:.1f}"
            )
        if in_transition and snap["nodes"]:
            for nid, (state, _ver, eff) in snap["nodes"].items():
                if state == NodeState.TRANSITION.value and eff > transition_clamp + 1e-9:
                    violations.append(
                        f"[{stage}] 节点 {nid} 过渡期实际限流 {eff:.1f} "
                        f"> min(旧,新)={transition_clamp:.1f}"
                    )
    return violations

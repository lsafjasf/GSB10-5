# -*- coding: utf-8 -*-
"""限流规则批量下发库（Python 3，仅标准库）。

下发流程（两阶段 + 提交窗口护栏）：

    PREPARE  把新规则 stage 到每个节点，此时旧规则仍然生效，节点同时持有两份规则
    BARRIER  确认全部节点暂存成功；只要有一个失败，丢弃已暂存的新规则，整体中止
    COMMIT   逐节点 activate 新版本。已激活节点进入“护栏窗口”：
             实际执行阈值 = min(旧阈值, 新阈值)，因此集群总放量
             始终 <= 旧规则总放量，新旧并存也不会超发
    SEAL     全部激活成功后统一解除护栏、清理旧规则；此后按新规则全量放量
    ROLLBACK COMMIT 中途失败：已激活节点 revert 回旧版本，所有节点丢弃新规则，
             集群回到“同一旧版本”的统一状态

串行化：协调器同一时刻只允许一个下发任务，下发期间到达的新规则被确定性拒绝
（RolloutInProgress），不会出现三代规则交叠。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


class NodeError(Exception):
    """节点侧错误（模拟网络故障 / 节点拒绝 / 写规则失败）。"""


class RolloutInProgress(Exception):
    """已有下发任务在进行中，新规则被拒绝。"""


class ClusterVersionDrift(Exception):
    """集群节点版本不一致（节点版本混乱），必须先 repair。"""


@dataclass(frozen=True)
class Rule:
    version: int   # 规则版本号，单调递增
    limit: int     # 每节点每窗口允许的请求数（限流阈值）


class Node:
    """模拟一个限流执行节点：本地可持有多版本规则，但同一时刻只有一个生效。"""

    def __init__(self, node_id: str, rule: Rule):
        self.node_id = node_id
        self.staged: Dict[int, Rule] = {rule.version: rule}
        self.active_version: int = rule.version
        # 护栏窗口：切到新版本后仍记录旧版本，effective_limit 取两者较小值
        self.guard_from: Optional[int] = None
        # 故障注入：命中的版本在对应阶段抛错
        self.fail_stage_on: set = set()
        self.fail_activate_on: set = set()

    # ---------- 下发原语（对应真实系统中的节点 RPC） ----------
    def stage(self, rule: Rule) -> None:
        if rule.version in self.fail_stage_on:
            raise NodeError("%s: stage v%d failed" % (self.node_id, rule.version))
        self.staged[rule.version] = rule

    def activate(self, version: int) -> None:
        if version in self.fail_activate_on:
            raise NodeError("%s: activate v%d failed" % (self.node_id, version))
        if version not in self.staged:
            raise NodeError("%s: v%d not staged" % (self.node_id, version))
        self.guard_from = self.active_version
        self.active_version = version

    def revert(self, version: int) -> None:
        if version not in self.staged:
            raise NodeError("%s: revert target v%d missing" % (self.node_id, version))
        self.active_version = version
        self.guard_from = None

    def discard(self, version: int) -> None:
        if version == self.active_version:
            raise NodeError("%s: cannot discard active rule v%d" % (self.node_id, version))
        self.staged.pop(version, None)

    def seal(self) -> None:
        """提交窗口结束：解除护栏并清理旧规则。"""
        if self.guard_from is not None:
            old = self.guard_from
            self.guard_from = None
            if old != self.active_version:
                self.staged.pop(old, None)

    # ---------- 放量 ----------
    def effective_limit(self) -> int:
        """节点当前真实执行的限流阈值。

        护栏窗口内取新旧较小值：新规则放量时不会超过旧规则，
        新规则收紧时已切换节点立刻变严，两种方向都不超发。
        """
        cur = self.staged[self.active_version].limit
        if self.guard_from is None:
            return cur
        old = self.staged[self.guard_from].limit
        return min(cur, old)

    def snapshot(self) -> dict:
        return {
            "node": self.node_id,
            "active": self.active_version,
            "staged": sorted(self.staged),
            "guard_from": self.guard_from,
            "effective_limit": self.effective_limit(),
        }


@dataclass
class RolloutReport:
    version: int
    success: bool
    rolled_back: bool
    reason: Optional[str]
    duration_ms: float          # 整个下发（含回退）耗时
    rollback_ms: float          # 回退本身耗时
    events: List[dict] = field(default_factory=list)


ProgressHook = Callable[[int, int], None]


class Coordinator:
    """批量下发协调器：维护单飞锁、版本审计、两阶段提交与回退。"""

    def __init__(self, nodes: List[Node]):
        self.nodes = list(nodes)
        self._inflight: Optional[int] = None
        self.reports: List[RolloutReport] = []

    # ---------- 集群视图 ----------
    def audit(self) -> Dict[int, List[str]]:
        """返回 {生效版本: [节点 id]}，用于发现节点版本混乱。"""
        dist: Dict[int, List[str]] = {}
        for n in self.nodes:
            dist.setdefault(n.active_version, []).append(n.node_id)
        return dist

    def cluster_capacity(self) -> int:
        """集群当前每窗口真实可放量总数（含护栏效应）。"""
        return sum(n.effective_limit() for n in self.nodes)

    def snapshots(self) -> List[dict]:
        return [n.snapshot() for n in self.nodes]

    def repair(self, version: int) -> None:
        """把版本漂移的节点强制对齐到指定版本（该版本至少在一个节点上存在）。"""
        donor = next((n for n in self.nodes if version in n.staged), None)
        if donor is None:
            raise NodeError("no node carries v%d, cannot repair" % version)
        rule = donor.staged[version]
        for n in self.nodes:
            if n.active_version != version:
                n.stage(rule)
                n.activate(version)
                n.seal()

    # ---------- 下发 ----------
    def rollout(self, rule: Rule, on_commit_progress: Optional[ProgressHook] = None) -> RolloutReport:
        """批量下发一条新规则。返回 RolloutReport。

        on_commit_progress(done, total)：每激活一个节点回调一次，
        供审计/测试采集提交窗口中途的版本与放量数据。
        """
        if self._inflight is not None:
            raise RolloutInProgress("rollout of v%d in progress" % self._inflight)

        dist = self.audit()
        if len(dist) != 1:
            raise ClusterVersionDrift(dist)
        old_version = next(iter(dist))

        start = time.perf_counter()
        self._inflight = rule.version
        events: List[dict] = []
        try:
            # ---- 阶段 1：PREPARE（只暂存，不生效） ----
            staged: List[Node] = []
            try:
                for n in self.nodes:
                    n.stage(rule)
                    staged.append(n)
            except NodeError as exc:
                rollback_ms = self._abort_prepare(rule.version, staged)
                return self._record(RolloutReport(
                    rule.version, False, True, "prepare failed: %s" % exc,
                    (time.perf_counter() - start) * 1000.0, rollback_ms,
                    events + [{"phase": "PREPARE", "ok": False},
                              {"phase": "ROLLBACK", "scope": "prepare", "ok": True}],
                ))
            events.append({"phase": "PREPARE", "ok": True, "staged_nodes": len(staged)})

            # ---- BARRIER：全部节点已暂存，旧规则仍全量生效 ----
            events.append({"phase": "BARRIER", "ok": True, "staged_nodes": len(staged)})

            # ---- 阶段 2：COMMIT（逐节点激活，护栏窗口开启） ----
            activated: List[Node] = []
            try:
                for idx, n in enumerate(self.nodes, 1):
                    n.activate(rule.version)
                    activated.append(n)
                    if on_commit_progress is not None:
                        on_commit_progress(idx, len(self.nodes))
            except NodeError as exc:
                rollback_ms = self._abort_commit(rule.version, old_version, activated)
                return self._record(RolloutReport(
                    rule.version, False, True, "commit failed: %s" % exc,
                    (time.perf_counter() - start) * 1000.0, rollback_ms,
                    events + [{"phase": "COMMIT", "ok": False,
                               "activated_before_fail": len(activated)},
                              {"phase": "ROLLBACK", "scope": "commit", "ok": True,
                               "reverted_nodes": len(activated)}],
                ))
            events.append({"phase": "COMMIT", "ok": True, "activated_nodes": len(activated)})

            # ---- 阶段 3：SEAL（全部激活成功，解除护栏，清理旧规则） ----
            for n in self.nodes:
                n.seal()
            events.append({"phase": "SEAL", "ok": True})
            return self._record(RolloutReport(
                rule.version, True, False, None,
                (time.perf_counter() - start) * 1000.0, 0.0, events,
            ))
        finally:
            self._inflight = None

    # ---------- 回退 ----------
    def _abort_prepare(self, version: int, staged: List[Node]) -> float:
        start = time.perf_counter()
        for n in staged:
            n.discard(version)
        return (time.perf_counter() - start) * 1000.0

    def _abort_commit(self, new_version: int, old_version: int, activated: List[Node]) -> float:
        start = time.perf_counter()
        # 已切到新版本的节点先回旧版本，再让所有节点丢弃新规则
        for n in activated:
            n.revert(old_version)
        for n in self.nodes:
            if new_version in n.staged:
                n.discard(new_version)
        return (time.perf_counter() - start) * 1000.0

    def _record(self, report: RolloutReport) -> RolloutReport:
        self.reports.append(report)
        return report

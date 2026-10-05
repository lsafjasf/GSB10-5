"""数据保留期自动清理核心库（仅标准库）。

判定所需的当前时间一律由调用方通过 ``now`` 参数注入，
库内部绝不调用 time.time()，以便测试与离线重放。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence

__all__ = [
    "Record",
    "Decision",
    "DeletionEntry",
    "SparedEntry",
    "DanglingReference",
    "CleanupReport",
    "Policy",
    "evaluate",
]


class Decision(str, Enum):
    """单条记录的判定结论。"""

    DELETE_EXPIRED = "delete_expired"            # 已到期且无任何保护
    KEEP_HOLD = "keep_hold"                      # 存在暂停保留标记
    KEEP_REFERENCED = "keep_referenced"          # 仍被其他现存记录引用
    KEEP_NOT_EXPIRED = "keep_not_expired"        # 尚未超过保留期
    KEEP_CLOCK_ANOMALY = "keep_clock_anomaly"    # 时间戳异常（时钟不准等）


@dataclass(frozen=True, slots=True)
class Record:
    """被清理的数据记录。

    id:               记录唯一标识。
    created_at:       创建时间（epoch 秒）。
    last_accessed_at: 最后访问时间（epoch 秒），没有访问过时与创建时间相同。
    references:       本记录引用的其他记录 id；引用完整性校验基于该字段。
    retention_hold:   暂停保留标记；置 True 时该记录永远不参与到期删除。
    """

    id: object
    created_at: float
    last_accessed_at: float
    references: tuple = ()
    retention_hold: bool = False


@dataclass(frozen=True, slots=True)
class Policy:
    """保留策略。

    retention_seconds:         保留期；超过该时长未活动即视为到期。
    clock_skew_tolerance_seconds:
                               时钟不准容忍带。时间戳落在
                               (now, now+tolerance] 内视为时钟轻微偏移，
                               钳制为 now 处理；超过容忍带则判为时间异常。
    """

    retention_seconds: float
    clock_skew_tolerance_seconds: float = 300.0


@dataclass(frozen=True, slots=True)
class DeletionEntry:
    """删除清单中的一条记录（可追溯）。"""

    id: object
    reason: str
    created_at: float
    last_accessed_at: float
    idle_seconds: float
    evaluated_at: float


@dataclass(frozen=True, slots=True)
class SparedEntry:
    """幸免清单中的一条记录。"""

    id: object
    decision: Decision
    reason: str
    referrers: tuple = ()


@dataclass(frozen=True, slots=True)
class DanglingReference:
    """悬空引用：引用目标在数据集中不存在。"""

    source_id: object
    target_id: object


@dataclass(slots=True)
class CleanupReport:
    """一次清理评估的完整结果。

    本对象只做“判定”，不真正删除任何数据；调用方依据 deletion_manifest
    执行删除，可实现先评估、后审计、再删除的两阶段流程。
    """

    now: float
    policy: Policy
    deletion_manifest: list = field(default_factory=list)   # list[DeletionEntry]
    spared: list = field(default_factory=list)              # list[SparedEntry]
    spared_referenced: list = field(default_factory=list)   # list[SparedEntry]
    anomalies: list = field(default_factory=list)           # list[SparedEntry]
    dangling_references: list = field(default_factory=list)  # list[DanglingReference]
    total_records: int = 0

    @property
    def delete_count(self) -> int:
        return len(self.deletion_manifest)

    def summary(self) -> dict:
        counts = {d.value: 0 for d in Decision}
        for entry in self.spared:
            counts[entry.decision.value] += 1
        counts[Decision.DELETE_EXPIRED.value] = self.delete_count
        return {
            "now": self.now,
            "total_records": self.total_records,
            "delete": self.delete_count,
            "kept": len(self.spared),
            "spared_by_hold": counts[Decision.KEEP_HOLD.value],
            "spared_by_reference": counts[Decision.KEEP_REFERENCED.value],
            "not_expired": counts[Decision.KEEP_NOT_EXPIRED.value],
            "clock_anomalies": counts[Decision.KEEP_CLOCK_ANOMALY.value],
            "dangling_references": len(self.dangling_references),
        }


def evaluate(records: Iterable[Record], now: float, policy: Policy) -> CleanupReport:
    """按保留策略对全部记录做一次清理判定。

    判定优先级（先到先得，任何一级命中即不再下判）：
      1. 时间异常：时间戳位于容忍带之外的未来，或 last_accessed_at 早于
         created_at。时钟可能不准时采用保守策略：宁可保留，绝不误删。
      2. 暂停保留：retention_hold 为 True，无论多旧都保留。
      3. 被引用：仍被任意一条现存记录引用即保留（引用完整性校验先于删除）。
      4. 到期判定：以 max(created_at, last_accessed_at) 为最后活动时间；
         now - 最后活动时间 >= retention_seconds 则删除，否则保留。

    注：只要引用方记录当前存在（即使它自己本次也会被删），被引记录本轮
    仍按“被引用”幸免，避免级联删除造成误删；后续轮次会自然收敛。
    """
    records = list(records)
    ids = set()
    incoming: dict = {}
    dangling: list = []

    for record in records:
        ids.add(record.id)

    for record in records:
        for target in record.references:
            if target not in ids:
                dangling.append(DanglingReference(record.id, target))
            else:
                incoming.setdefault(target, []).append(record.id)

    report = CleanupReport(
        now=now,
        policy=policy,
        dangling_references=dangling,
        total_records=len(records),
    )
    tolerance = policy.clock_skew_tolerance_seconds

    for record in records:
        decision, reason = _classify(record, now, policy, tolerance, record.id in incoming)

        if decision is Decision.DELETE_EXPIRED:
            last_activity = max(record.created_at, record.last_accessed_at)
            report.deletion_manifest.append(
                DeletionEntry(
                    id=record.id,
                    reason=reason,
                    created_at=record.created_at,
                    last_accessed_at=record.last_accessed_at,
                    idle_seconds=now - last_activity,
                    evaluated_at=now,
                )
            )
            continue

        entry = SparedEntry(
            id=record.id,
            decision=decision,
            reason=reason,
            referrers=tuple(incoming.get(record.id, ())),
        )
        report.spared.append(entry)
        if decision is Decision.KEEP_REFERENCED:
            report.spared_referenced.append(entry)
        elif decision is Decision.KEEP_CLOCK_ANOMALY:
            report.anomalies.append(entry)

    return report


def _classify(record: Record, now: float, policy: Policy, tolerance: float,
              is_referenced: bool) -> tuple:
    upper = now + tolerance

    if record.created_at > upper or record.last_accessed_at > upper:
        return (
            Decision.KEEP_CLOCK_ANOMALY,
            "timestamp lies in the future beyond the clock-skew tolerance "
            f"(now={now}, tolerance={tolerance}s)",
        )

    if record.last_accessed_at < record.created_at:
        return (
            Decision.KEEP_CLOCK_ANOMALY,
            "last_accessed_at is earlier than created_at; timestamps inconsistent",
        )

    if record.retention_hold:
        return Decision.KEEP_HOLD, "retention hold (pause) flag is set"

    if is_referenced:
        return Decision.KEEP_REFERENCED, "still referenced by at least one existing record"

    last_activity = max(record.created_at, record.last_accessed_at)
    # 容忍带内的轻微时钟前拨（未来时间戳）钳制为 now，等价于“刚刚活动过”。
    if last_activity > now:
        last_activity = now

    if now - last_activity >= policy.retention_seconds:
        return (
            Decision.DELETE_EXPIRED,
            f"idle for {now - last_activity:.0f}s >= retention "
            f"{policy.retention_seconds:.0f}s and unprotected",
        )
    return (
        Decision.KEEP_NOT_EXPIRED,
        f"idle for {now - last_activity:.0f}s < retention "
        f"{policy.retention_seconds:.0f}s",
    )

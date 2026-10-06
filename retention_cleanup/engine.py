"""数据保留期自动清理引擎（纯标准库，时间由调用方注入）。

判定规则（详见 README.md「判定说明」）：

    对一条记录 R，在清理时刻 now（epoch 秒，由测试/调用方注入）：

      1. 时间戳异常（不可解析 / NaN / 负数 / 超过时钟偏差容忍的未来时间 /
         最后访问早于创建）→ ANOMALY，永不删除，转人工复核。
      2. 存在来自其他现存记录的引用（引用完整性校验通过后）→ REFERENCED，
         永不删除（保守策略：不级联删除）。
      3. 存在暂停保留标记 hold → HELD，保留期暂停计时，永不删除。
      4. 空闲时长 = now - max(created_at, last_accessed_at)
         若 idle < retention + grace（宽限，用于对抗时钟偏慢）→ ACTIVE，保留。
      5. 其余 → DELETE，理由为“空闲超期且无引用/无暂停/无异常”。

时钟不准的处理：
  * 宽限期 grace_seconds 给“时钟偏慢导致少算年龄”的系统留安全垫；
  * 清理只依赖注入的单调 now，引擎本身绝不读取系统时钟；
  * 明显来自未来（now + skew_tolerance 之后）的时间戳判为异常而非提前删除。
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum


class Verdict(str, Enum):
    DELETE = "delete"
    REFERENCED = "referenced"
    HELD = "held"
    ACTIVE = "active"
    ANOMALY = "anomaly"


# 删除 / 幸免理由码（全部 intern，单例存储，百万级清单也只持有常量引用）
class Reason:
    DELETED_IDLE_EXPIRED = "deleted:idle_expired"
    NOT_EXPIRED_ACTIVE = "spared:not_expired_active"
    REFERENCED_BY = "spared:referenced_by"
    HELD_ACTIVE = "spared:hold_pause"
    ANOMALY = "spared:timestamp_anomaly"


class Anomaly:
    NON_FINITE_TIMESTAMP = "non_finite_or_nonnumeric_timestamp"
    NEGATIVE_TIMESTAMP = "negative_timestamp"
    FUTURE_TIMESTAMP = "future_timestamp_beyond_skew"
    ACCESS_BEFORE_CREATED = "last_access_before_created"
    MISSING_CREATED_AT = "missing_created_at"


class Violation:
    DUPLICATE_ID = "duplicate_record_id"
    DANGLING_REFERENCE = "dangling_reference"
    REFERENCED_BY_DELETED = "post_check:still_referenced_by_deleted"


@dataclass(frozen=True)
class RetentionPolicy:
    """保留策略，时间单位均为秒。

    retention_seconds : 空闲保留期，超过后若无引用且未暂停则可清理。
    grace_seconds     : 额外宽限，对抗时钟偏慢/抖动（年龄多算一点才删）。
    skew_tolerance_seconds: 判定“未来时间戳”异常的容忍上界。
    """

    retention_seconds: float
    grace_seconds: float = 0.0
    skew_tolerance_seconds: float = 86400.0


@dataclass(frozen=True)
class Record:
    record_id: str
    created_at: float
    last_accessed_at: float | None = None
    referenced_by: tuple = ()
    hold: bool = False


class Decision:
    __slots__ = (
        "record_id", "verdict", "reasons", "referrers", "anomalies",
        "created_at", "last_accessed_at", "age_seconds", "idle_seconds", "hold",
    )

    def __init__(self, record_id, verdict, reasons, referrers, anomalies,
                 created_at, last_accessed_at, age_seconds, idle_seconds, hold):
        self.record_id = record_id
        self.verdict = verdict
        self.reasons = reasons
        self.referrers = referrers
        self.anomalies = anomalies
        self.created_at = created_at
        self.last_accessed_at = last_accessed_at
        self.age_seconds = age_seconds
        self.idle_seconds = idle_seconds
        self.hold = hold


class IntegrityViolation:
    __slots__ = ("kind", "record_id", "detail")

    def __init__(self, kind, record_id, detail):
        self.kind = kind
        self.record_id = record_id
        self.detail = detail


class CleanupReport:
    """一次清理的完整可追溯结果。"""

    def __init__(self, now, policy, applied):
        self.now = now
        self.policy = policy
        self.applied = applied
        self.total_records = 0
        self.decisions: list[Decision] = []
        self.violations: list[IntegrityViolation] = []
        self.integrity_ok = True
        self.cleanup_aborted = False
        self.elapsed_seconds = None

    def _by(self, verdict):
        return [d for d in self.decisions if d.verdict == verdict]

    @property
    def deleted(self):
        return self._by(Verdict.DELETE)

    @property
    def spared_referenced(self):
        return self._by(Verdict.REFERENCED)

    @property
    def spared_hold(self):
        return self._by(Verdict.HELD)

    @property
    def spared_active(self):
        return self._by(Verdict.ACTIVE)

    @property
    def spared_anomaly(self):
        return self._by(Verdict.ANOMALY)

    @property
    def would_delete(self):
        return [d.record_id for d in self.deleted]

    def manifest(self):
        """返回可 JSON 序列化的清理清单（含删除理由与幸免理由）。"""
        rows = []
        for d in self.decisions:
            rows.append({
                "record_id": d.record_id,
                "verdict": d.verdict.value,
                "reasons": list(d.reasons),
                "referrers": list(d.referrers),
                "anomalies": list(d.anomalies),
                "created_at": d.created_at,
                "last_accessed_at": d.last_accessed_at,
                "age_seconds": d.age_seconds,
                "idle_seconds": d.idle_seconds,
                "hold": d.hold,
            })
        return {
            "now": self.now,
            "applied": self.applied,
            "integrity_ok": self.integrity_ok,
            "cleanup_aborted": self.cleanup_aborted,
            "total_records": self.total_records,
            "elapsed_seconds": self.elapsed_seconds,
            "policy": {
                "retention_seconds": self.policy.retention_seconds,
                "grace_seconds": self.policy.grace_seconds,
                "skew_tolerance_seconds": self.policy.skew_tolerance_seconds,
            },
            "counts": {
                "delete": len(self.deleted),
                "referenced": len(self.spared_referenced),
                "held": len(self.spared_hold),
                "active": len(self.spared_active),
                "anomaly": len(self.spared_anomaly),
                "integrity_violations": len(self.violations),
            },
            "entries": rows,
        }


def _finite_number(value):
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


class RetentionCleaner:
    """保留期清理器；无状态、可重入，当前时间一律通过参数注入。"""

    def __init__(self, policy: RetentionPolicy):
        self.policy = policy

    def evaluate(self, records, now, apply=False, abort_on_violation=True):
        report = CleanupReport(now, self.policy, applied=False)
        report.total_records = len(records)

        seen: dict[str, Record] = {}
        indegree: dict[str, set] = defaultdict(set)
        duplicates = set()
        for rec in records:
            if rec.record_id in seen and rec.record_id not in duplicates:
                duplicates.add(rec.record_id)
                report.violations.append(IntegrityViolation(
                    Violation.DUPLICATE_ID, rec.record_id,
                    "record id appears more than once"))
            seen.setdefault(rec.record_id, rec)
            for referrer in dict.fromkeys(rec.referenced_by):
                indegree[rec.record_id].add(referrer)

        known = set(seen)
        for target, referrers in indegree.items():
            for referrer in referrers:
                if referrer not in known:
                    report.violations.append(IntegrityViolation(
                        Violation.DANGLING_REFERENCE, target,
                        f"referenced by unknown record {referrer!r}"))

        if report.violations:
            report.integrity_ok = False
            report.cleanup_aborted = abort_on_violation

        if report.cleanup_aborted:
            return report

        decisions = [self._classify(rec, now, indegree) for rec in records]

        if apply:
            # 后置不变量：被删除的只能是无入引用的记录，因此不可能有
            # 幸存者仍引用被删项；做一次 O(E) 防御性校验。
            deleted_ids = {
                d.record_id for d in decisions if d.verdict == Verdict.DELETE
            }
            for rec in records:
                if rec.record_id in deleted_ids:
                    continue
                if indegree.get(rec.record_id, set()) & deleted_ids:
                    report.violations.append(IntegrityViolation(
                        Violation.REFERENCED_BY_DELETED, rec.record_id,
                        "surviving record referenced by a deleted record"))
            if report.violations:
                report.integrity_ok = False
                report.cleanup_aborted = True
                return report
            report.applied = True

        report.decisions = decisions
        return report

    def _classify(self, rec, now, indegree):
        anomalies = []
        created = rec.created_at
        accessed = rec.last_accessed_at if rec.last_accessed_at is not None else created

        if not _finite_number(created):
            anomalies.append(Anomaly.NON_FINITE_TIMESTAMP)
        elif created < 0:
            anomalies.append(Anomaly.NEGATIVE_TIMESTAMP)
        elif created > now + self.policy.skew_tolerance_seconds:
            anomalies.append(Anomaly.FUTURE_TIMESTAMP)

        if rec.last_accessed_at is not None:
            if not _finite_number(accessed):
                anomalies.append(Anomaly.NON_FINITE_TIMESTAMP)
            elif _finite_number(created) and accessed < created:
                anomalies.append(Anomaly.ACCESS_BEFORE_CREATED)
            elif accessed > now + self.policy.skew_tolerance_seconds:
                anomalies.append(Anomaly.FUTURE_TIMESTAMP)

        age = now - created if _finite_number(created) else None
        last_activity = (
            max(created, accessed)
            if _finite_number(created) and _finite_number(accessed) else None
        )
        idle = now - last_activity if last_activity is not None else None

        referrers = tuple(sorted(indegree.get(rec.record_id, ())))

        if anomalies:
            return Decision(
                rec.record_id, Verdict.ANOMALY, (Reason.ANOMALY,), (),
                tuple(anomalies), created,
                rec.last_accessed_at, age, idle, rec.hold)

        if referrers:
            return Decision(
                rec.record_id, Verdict.REFERENCED, (Reason.REFERENCED_BY,),
                referrers, (), created, rec.last_accessed_at, age, idle, rec.hold)

        if rec.hold:
            return Decision(
                rec.record_id, Verdict.HELD, (Reason.HELD_ACTIVE,), (), (),
                created, rec.last_accessed_at, age, idle, rec.hold)

        if idle < self.policy.retention_seconds + self.policy.grace_seconds:
            return Decision(
                rec.record_id, Verdict.ACTIVE, (Reason.NOT_EXPIRED_ACTIVE,), (),
                (), created, rec.last_accessed_at, age, idle, rec.hold)

        return Decision(
            rec.record_id, Verdict.DELETE, (Reason.DELETED_IDLE_EXPIRED,), (),
            (), created, rec.last_accessed_at, age, idle, rec.hold)


def apply_cleanup(records, report):
    """按报告物理删除，返回幸存记录列表；报告必须来自 apply=True 的评估。"""
    if not report.applied or report.cleanup_aborted:
        raise RuntimeError("refuse to delete: cleanup was not safely applied")
    deleted = set(report.would_delete)
    return [rec for rec in records if rec.record_id not in deleted]

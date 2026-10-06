"""回滚演练定期验证核心库（仅标准库）。

设计要点：
- 真实回滚与演练共用同一条流水线（STEP_SPECS），保证路径一致；
  两者差别只在运行参数（目标库、是否真冻结写入），由 RunContext.mode 决定。
- 时间全部通过注入的 clock/advance 获得，测试可精确控制超时与耗时。
- 失败处理策略是确定性的，见 POLICY。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


# ---------------------------------------------------------------- 时间注入

class ManualClock:
    """可手动推进的时钟。业务代码只允许通过它读时间/推进时间。"""

    def __init__(self, start: float = 1000.0):
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        if dt < 0:
            raise ValueError("clock cannot go backwards")
        self.now += dt


# ---------------------------------------------------------------- 模拟存储

class WriteFrozenError(RuntimeError):
    pass


class DataStore:
    """模拟数据库：记录集 + 写序列号（write_seq 用于检测演练期间的新写入）。"""

    def __init__(self, name: str, records: Optional[Dict[str, str]] = None):
        self.name = name
        self.records: Dict[str, str] = dict(records or {})
        self.write_seq = 0
        self.frozen = False

    def write(self, key: str, value: str) -> None:
        if self.frozen:
            raise WriteFrozenError(f"writes frozen on {self.name}")
        self.records[key] = value
        self.write_seq += 1

    def clear(self) -> None:
        self.records.clear()

    def checksum(self) -> str:
        h = hashlib.sha256()
        for k in sorted(self.records):
            h.update(k.encode())
            h.update(b"=")
            h.update(str(self.records[k]).encode())
            h.update(b"\n")
        return h.hexdigest()

    def clone(self, name: str) -> "DataStore":
        c = DataStore(name, self.records)
        c.write_seq = self.write_seq
        return c


class BackupStore:
    """模拟备份仓：保存记录快照及其校验和。"""

    def __init__(self):
        self._backups: Dict[str, Dict[str, str]] = {}

    def save(self, backup_id: str, records: Dict[str, str]) -> None:
        self._backups[backup_id] = dict(records)

    def load(self, backup_id: str) -> Dict[str, str]:
        if backup_id not in self._backups:
            raise KeyError(f"backup {backup_id} not found")
        return dict(self._backups[backup_id])

    @staticmethod
    def checksum_of(records: Dict[str, str]) -> str:
        return DataStore("tmp", records).checksum()


# ---------------------------------------------------------------- 故障注入

@dataclass
class Faults:
    """测试用故障注入。全部默认关闭。"""
    fail_step: Optional[str] = None          # 指定步骤执行时抛错
    fail_message: str = "injected failure"
    write_during_step: Optional[str] = None  # 在该步骤期间模拟外部新写入
    extra_delay: Dict[str, float] = field(default_factory=dict)  # 步骤额外耗时（触发超时）


# ---------------------------------------------------------------- 上下文与报告

@dataclass
class RunContext:
    mode: str                     # "real" 真实回滚 | "drill" 演练
    source: DataStore             # 线上库（演练时也只读它，恢复目标是副本）
    target: DataStore             # real: 线上库本身；drill: 线上数据的副本（沙箱）
    backups: BackupStore
    backup_id: str
    clock: Callable[[], float]
    advance: Callable[[float], None]
    faults: Faults = field(default_factory=Faults)
    deadline: Optional[float] = None          # 绝对截止时间（注入时钟）
    step_durations: Dict[str, float] = field(default_factory=dict)
    watermark: Optional[int] = None           # freeze 时记录的写序列号


@dataclass
class StepRecord:
    order: int
    name: str
    status: str                  # ok | failed | skipped
    started_at: float
    ended_at: float
    duration: float
    data: Dict = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict:
        return dict(order=self.order, name=self.name, status=self.status,
                    started_at=self.started_at, ended_at=self.ended_at,
                    duration=round(self.duration, 6), data=self.data,
                    error=self.error)


@dataclass
class Report:
    mode: str
    outcome: str = "running"     # success | aborted | invalidated | timeout
    abort_reason: Optional[str] = None
    tainted: bool = False        # 演练期间检测到新写入，结果被标记不可信
    started_at: float = 0.0
    ended_at: float = 0.0
    steps: List[StepRecord] = field(default_factory=list)
    compensations: List[StepRecord] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.ended_at - self.started_at

    def trace(self) -> List[str]:
        return [s.name for s in self.steps if s.status != "skipped"]

    def to_dict(self) -> Dict:
        return dict(
            mode=self.mode, outcome=self.outcome, abort_reason=self.abort_reason,
            tainted=self.tainted, started_at=self.started_at, ended_at=self.ended_at,
            duration=round(self.duration, 6),
            steps=[s.to_dict() for s in self.steps],
            compensations=[s.to_dict() for s in self.compensations],
        )


# ---------------------------------------------------------------- 步骤定义

class StepError(RuntimeError):
    pass


def _s_snapshot_source(ctx: RunContext) -> Dict:
    snap = ctx.source.clone(f"{ctx.source.name}-snapshot")
    ctx.snapshot = snap  # type: ignore[attr-defined]
    return {"records": len(snap.records), "checksum": snap.checksum(),
            "write_seq": ctx.source.write_seq}


def _s_verify_backup(ctx: RunContext) -> Dict:
    backup_records = ctx.backups.load(ctx.backup_id)
    backup_sum = BackupStore.checksum_of(backup_records)
    source_sum = ctx.snapshot.checksum()  # type: ignore[attr-defined]
    if backup_sum != source_sum:
        raise StepError(f"backup checksum mismatch: {backup_sum} != {source_sum}")
    return {"backup_id": ctx.backup_id, "checksum": backup_sum, "match": True}


def _s_freeze_writes(ctx: RunContext) -> Dict:
    ctx.watermark = ctx.source.write_seq
    if ctx.mode == "real":
        ctx.source.frozen = True
    return {"watermark": ctx.watermark,
            "hard_freeze": ctx.mode == "real",
            "note": "drill 只记录水位线，不阻塞线上写入" if ctx.mode == "drill" else "线上写入已冻结"}


def _s_restore_backup(ctx: RunContext) -> Dict:
    records = ctx.backups.load(ctx.backup_id)
    ctx.target.clear()
    for k, v in records.items():
        ctx.target.records[k] = v
    return {"target": ctx.target.name, "restored_records": len(records)}


def _s_verify_restore(ctx: RunContext) -> Dict:
    expected = BackupStore.checksum_of(ctx.backups.load(ctx.backup_id))
    actual = ctx.target.checksum()
    if actual != expected:
        raise StepError(f"restore checksum mismatch: {actual} != {expected}")
    return {"expected": expected, "actual": actual, "match": True}


def _s_detect_new_writes(ctx: RunContext) -> Dict:
    new_writes = ctx.source.write_seq - (ctx.watermark or 0)
    return {"watermark": ctx.watermark, "current_seq": ctx.source.write_seq,
            "new_writes": new_writes}


def _s_unfreeze_writes(ctx: RunContext) -> Dict:
    was_frozen = ctx.source.frozen
    ctx.source.frozen = False
    return {"released": was_frozen}


def _s_cleanup(ctx: RunContext) -> Dict:
    if ctx.mode == "drill":
        discarded = len(ctx.target.records)
        ctx.target.clear()
        return {"sandbox_discarded": True, "discarded_records": discarded}
    return {"sandbox_discarded": False}


def _c_unfreeze(ctx: RunContext) -> Dict:
    ctx.source.frozen = False
    return {"compensated": "freeze_writes"}


def _c_restore(ctx: RunContext) -> Dict:
    if ctx.mode == "drill":
        ctx.target.clear()
        return {"compensated": "restore_backup", "sandbox_cleared": True}
    return {"compensated": "restore_backup", "note": "real 模式保留现场待人工处理"}


@dataclass
class StepSpec:
    name: str
    action: Callable[[RunContext], Dict]
    base_duration: float = 0.0
    compensate: Optional[Callable[[RunContext], Dict]] = None


# 真实回滚与演练共用的唯一步骤序列 —— 路径一致性的根源
STEP_SPECS: List[StepSpec] = [
    StepSpec("snapshot_source",   _s_snapshot_source,   0.5),
    StepSpec("verify_backup",     _s_verify_backup,     0.5),
    StepSpec("freeze_writes",     _s_freeze_writes,     0.2, compensate=_c_unfreeze),
    StepSpec("restore_backup",    _s_restore_backup,    1.0, compensate=_c_restore),
    StepSpec("verify_restore",    _s_verify_restore,    0.5),
    StepSpec("detect_new_writes", _s_detect_new_writes, 0.1),
    StepSpec("unfreeze_writes",   _s_unfreeze_writes,   0.2),
    StepSpec("cleanup",           _s_cleanup,           0.1),
]

# 确定性失败处理策略
POLICY = {
    "step_failure": "abort_and_compensate",   # 任一步失败：中止 + 逆序补偿已完成步骤
    "new_writes":   "mark_and_abort",         # 检测到新写入：标记 tainted + 中止 + 补偿
    "timeout":      "abort_and_compensate",   # 超时：中止 + 补偿
}


# ---------------------------------------------------------------- 流水线

def run_pipeline(ctx: RunContext) -> Report:
    """真实回滚与演练都调用这一个函数。"""
    report = Report(mode=ctx.mode, started_at=ctx.clock())
    completed: List[StepSpec] = []

    def compensate() -> None:
        for spec in reversed(completed):
            if spec.compensate is None:
                continue
            start = ctx.clock()
            ctx.advance(0.05)
            data = spec.compensate(ctx)
            report.compensations.append(StepRecord(
                order=len(report.compensations) + 1, name=f"compensate:{spec.name}",
                status="ok", started_at=start, ended_at=ctx.clock(),
                duration=ctx.clock() - start, data=data))

    for order, spec in enumerate(STEP_SPECS, 1):
        # 超时检查（在步骤开始前，用注入时钟判定）
        if ctx.deadline is not None and ctx.clock() > ctx.deadline:
            report.outcome = "timeout"
            report.abort_reason = f"deadline {ctx.deadline} exceeded before step '{spec.name}'"
            for rest in STEP_SPECS[order - 1:]:
                report.steps.append(StepRecord(order=STEP_SPECS.index(rest) + 1,
                                               name=rest.name, status="skipped",
                                               started_at=ctx.clock(), ended_at=ctx.clock(),
                                               duration=0.0))
            compensate()
            break

        # 故障注入：步骤期间的外部新写入
        if ctx.faults.write_during_step == spec.name:
            ctx.source.write(f"late-{ctx.source.write_seq}", "concurrent-write")

        start = ctx.clock()
        duration = spec.base_duration + ctx.step_durations.get(spec.name, 0.0) \
            + ctx.faults.extra_delay.get(spec.name, 0.0)
        ctx.advance(duration)
        try:
            if ctx.faults.fail_step == spec.name:
                raise StepError(ctx.faults.fail_message)
            data = spec.action(ctx)
            rec = StepRecord(order=order, name=spec.name, status="ok",
                             started_at=start, ended_at=ctx.clock(),
                             duration=ctx.clock() - start, data=data)
            report.steps.append(rec)
            completed.append(spec)
        except StepError as exc:
            report.steps.append(StepRecord(order=order, name=spec.name, status="failed",
                                           started_at=start, ended_at=ctx.clock(),
                                           duration=ctx.clock() - start, error=str(exc)))
            for rest in STEP_SPECS[order:]:
                report.steps.append(StepRecord(order=STEP_SPECS.index(rest) + 1,
                                               name=rest.name, status="skipped",
                                               started_at=ctx.clock(), ended_at=ctx.clock(),
                                               duration=0.0))
            report.outcome = "aborted"
            report.abort_reason = f"step '{spec.name}' failed: {exc}"
            compensate()
            break

        # 确定性策略：检测到新写入 -> 标记 + 中止 + 补偿
        if spec.name == "detect_new_writes" and data.get("new_writes", 0) > 0:
            report.tainted = True
            report.outcome = "invalidated"
            report.abort_reason = (f"{data['new_writes']} new write(s) during "
                                   f"{ctx.mode}; result marked tainted")
            for rest in STEP_SPECS[order:]:
                report.steps.append(StepRecord(order=STEP_SPECS.index(rest) + 1,
                                               name=rest.name, status="skipped",
                                               started_at=ctx.clock(), ended_at=ctx.clock(),
                                               duration=0.0))
            compensate()
            break
    else:
        report.outcome = "success"

    report.ended_at = ctx.clock()
    return report


# ---------------------------------------------------------------- 环境构造

def build_env(mode: str, records: Dict[str, str], clock: ManualClock,
              faults: Optional[Faults] = None,
              timeout_after: Optional[float] = None) -> RunContext:
    """构造运行上下文。real 与 drill 的差别只在这里的参数，不在步骤代码。"""
    source = DataStore("production", records)
    backups = BackupStore()
    backups.save("bkp-latest", records)
    if mode == "real":
        target = source            # 真实回滚：直接恢复线上库
    else:
        target = source.clone("drill-sandbox")  # 演练：恢复到线上数据的副本
    deadline = None
    if timeout_after is not None:
        deadline = clock() + timeout_after
    return RunContext(mode=mode, source=source, target=target, backups=backups,
                      backup_id="bkp-latest", clock=clock, advance=clock.advance,
                      faults=faults or Faults(), deadline=deadline)


def compare_paths(sample_records: Dict[str, str]) -> Dict:
    """分别走一遍 real 与 drill，输出逐步对照数据，证明路径一致。"""
    real_clock, drill_clock = ManualClock(), ManualClock()
    real = run_pipeline(build_env("real", sample_records, real_clock))
    drill = run_pipeline(build_env("drill", sample_records, drill_clock))

    real_trace = [s.name for s in real.steps]
    drill_trace = [s.name for s in drill.steps]
    rows = []
    for i, name in enumerate(real_trace):
        same = i < len(drill_trace) and drill_trace[i] == name
        rows.append({
            "order": i + 1,
            "step": name,
            "real_status": real.steps[i].status,
            "drill_status": drill.steps[i].status if i < len(drill.steps) else "missing",
            "same_order": same,
            "same_code_path": True,   # 两者都执行 STEP_SPECS 中同一个函数
            "param_differences": _param_differences(name),
        })
    return {
        "path_identical": real_trace == drill_trace,
        "real_trace": real_trace,
        "drill_trace": drill_trace,
        "real_outcome": real.outcome,
        "drill_outcome": drill.outcome,
        "steps": rows,
    }


def _param_differences(step: str) -> List[str]:
    table = {
        "freeze_writes": ["real: 硬冻结线上写入", "drill: 只记录写水位线"],
        "restore_backup": ["real: 恢复到线上库", "drill: 恢复到线上数据副本(沙箱)"],
        "cleanup": ["real: 无沙箱清理", "drill: 丢弃沙箱副本"],
    }
    return table.get(step, [])

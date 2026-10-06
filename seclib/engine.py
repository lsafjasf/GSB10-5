"""事件分级响应引擎：分级、SLA 计时、升级/降级规则、状态轨迹。

时钟由外部注入：engine = SecurityEngine(clock=lambda: fixed_time)，
保证测试可复现、无真实时间依赖。

确定性规则（同一评估轮内按优先级执行，详见 README.md）：
- R1 新证据 / R3 范围扩大：重算分跨阈值 -> 立即升级；
- R2 SLA 预警：未响应且耗时 >= 当前级别 SLA 的 50% -> 升一级（10 分钟冷却）；
- R4 证据失效：重算分低于“当前级别阈值 - 迟滞量” -> 降一级（每轮最多一级）；
- 冲突裁决：同一轮内升级规则（R1/R2/R3）优先于降级规则（R4）。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional

from .grading import HYSTERESIS, GradeResult, grade, lower_threshold
from .models import Event, Level, Signal

# SLA 响应时限（秒），与级别绑定
SLA_SECONDS: Dict[Level, int] = {
    Level.P1: 15 * 60,
    Level.P2: 60 * 60,
    Level.P3: 4 * 60 * 60,
    Level.P4: 24 * 60 * 60,
}

SLA_WARN_RATIO = 0.50    # 超过 SLA 的 50% 未响应 -> 升级
ESCALATE_COOLDOWN = 600  # 两次自动升级之间的最小间隔（秒），防抖动


@dataclass(frozen=True)
class Transition:
    ts: float
    old_level: Level
    new_level: Level
    rule: str
    reason: str


@dataclass
class EventState:
    event: Event
    initial: GradeResult
    current: GradeResult
    created_ts: float
    acked_ts: Optional[float] = None
    resolved_ts: Optional[float] = None
    last_escalation_ts: Optional[float] = None
    sla_level_at_ack: Optional[Level] = None
    trajectory: List[Transition] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.resolved_ts is None


class SecurityEngine:
    def __init__(self, clock: Callable[[], float]):
        self._clock = clock
        self._states: Dict[str, EventState] = {}

    # ---------- 生命周期 ----------

    def ingest(self, event: Event) -> GradeResult:
        if event.event_id in self._states:
            raise KeyError(f"duplicate event: {event.event_id}")
        result = grade(event)
        state = EventState(
            event=event, initial=result, current=result,
            created_ts=self._clock(),
        )
        state.trajectory.append(
            Transition(state.created_ts, result.level, result.level,
                       "R0_INIT", f"初评 {result.level.name} 总分 {result.score}")
        )
        self._states[event.event_id] = state
        return result

    def state_of(self, event_id: str) -> EventState:
        return self._states[event_id]

    # ---------- 规则触发 ----------

    def add_signal(self, event_id: str, signal: Signal) -> GradeResult:
        """R1 新证据：加入信号后重算，若跨过更高阈值则升级。"""
        state = self._states[event_id]
        state.event.signals.append(signal)
        return self._regrade(state, "R1_NEW_SIGNAL", f"新增信号 {signal.name}(强度{signal.strength})")

    def expand_scope(self, event_id: str, new_scope_level: int) -> GradeResult:
        """R3 范围扩大：scope 上调后重算。"""
        state = self._states[event_id]
        old = state.event.scope_level
        if new_scope_level <= old:
            raise ValueError("scope expansion requires a strictly larger level")
        state.event.scope_level = new_scope_level
        return self._regrade(state, "R3_SCOPE_EXPAND", f"范围 {old}->{new_scope_level}")

    def expire_signals(self, event_id: str, names: List[str]) -> GradeResult:
        """R4 证据失效：移除信号后重算，按迟滞规则可能降级。"""
        state = self._states[event_id]
        remaining = [s for s in state.event.signals if s.name not in names]
        if len(remaining) == len(state.event.signals):
            raise ValueError("no matching signal removed")
        if not remaining:
            raise ValueError("event must keep at least one signal")
        state.event.signals = remaining
        return self._regrade(state, "R4_SIGNAL_EXPIRE", f"信号失效: {','.join(names)}")

    def tick(self) -> List[Transition]:
        """R2 SLA 预警：扫描所有未响应事件，超时风险者升一级。"""
        now = self._clock()
        fired: List[Transition] = []
        for state in self._states.values():
            if self._sla_warn_due(state, now):
                old = state.current.level
                new = old.higher()
                self._append(state, old, new, "R2_SLA_WARN",
                             f"未响应 {now - state.created_ts:.0f}s >= "
                             f"{SLA_SECONDS[old] * SLA_WARN_RATIO:.0f}s(SLA50%)")
                state.current = replace(state.current, level=new)
                state.last_escalation_ts = now
                fired.append(state.trajectory[-1])
        return fired

    # ---------- 响应与统计 ----------

    def acknowledge(self, event_id: str) -> float:
        """记录响应时刻，返回实际响应耗时（相对创建时刻，秒）。"""
        state = self._states[event_id]
        if state.acked_ts is not None:
            raise RuntimeError(f"event already acked: {event_id}")
        state.acked_ts = self._clock()
        state.sla_level_at_ack = state.current.level
        return state.acked_ts - state.created_ts

    def resolve(self, event_id: str) -> None:
        self._states[event_id].resolved_ts = self._clock()

    def response_seconds(self, event_id: str) -> Optional[float]:
        state = self._states[event_id]
        if state.acked_ts is None:
            return None
        return state.acked_ts - state.created_ts

    def sla_for(self, event_id: str) -> int:
        """响应时限按“响应发生时的级别”核对（未响应按当前级别）。"""
        state = self._states[event_id]
        ref = state.sla_level_at_ack or state.current.level
        return SLA_SECONDS[ref]

    def is_overdue(self, event_id: str) -> bool:
        used = self.response_seconds(event_id)
        if used is None:
            return self._clock() - self._states[event_id].created_ts > self.sla_for(event_id)
        return used > self.sla_for(event_id)

    def stats(self) -> dict:
        """汇总：初评级分布、超时数、平均/最大响应耗时。"""
        level_counts = {lv: 0 for lv in Level}
        acked = overdue = 0
        total_used = 0.0
        max_used = 0.0
        for state in self._states.values():
            level_counts[state.initial.level] += 1
            used = self.response_seconds(state.event.event_id)
            if used is not None:
                acked += 1
                total_used += used
                max_used = max(max_used, used)
                if used > SLA_SECONDS[state.sla_level_at_ack]:
                    overdue += 1
        return {
            "total": len(self._states),
            "acked": acked,
            "level_counts": {lv.name: n for lv, n in level_counts.items()},
            "overdue": overdue,
            "overdue_rate": round(overdue / acked, 4) if acked else 0.0,
            "avg_response_seconds": round(total_used / acked, 2) if acked else 0.0,
            "max_response_seconds": round(max_used, 2),
        }

    # ---------- 内部 ----------

    def _sla_warn_due(self, state: EventState, now: float) -> bool:
        if not state.is_open or state.acked_ts is not None:
            return False
        level = state.current.level
        if level == Level.P1:
            return False
        if now - state.created_ts < SLA_SECONDS[level] * SLA_WARN_RATIO:
            return False
        if (state.last_escalation_ts is not None
                and now - state.last_escalation_ts < ESCALATE_COOLDOWN):
            return False
        return True

    def _regrade(self, state: EventState, rule: str, reason: str) -> GradeResult:
        now = self._clock()
        old_level = state.current.level
        result = grade(state.event)

        effective = old_level
        if result.level < old_level:
            # 升级：重算分跨过更高阈值，立即生效（可跨多级）
            effective = result.level
            self._append(state, old_level, effective, rule,
                         f"{reason}；总分 {state.initial.score}->{result.score}")
            state.last_escalation_ts = now
        elif result.level > old_level:
            if self._sla_warn_due(state, now):
                # 冲突裁决：R2 升级优先于 R4 降级
                effective = old_level.higher()
                self._append(state, old_level, effective, "R2_SLA_WARN",
                             f"{reason} 后本应降级，但 SLA 预警未解除，升级优先")
                state.last_escalation_ts = now
            elif result.score < lower_threshold(old_level) - HYSTERESIS:
                # 降级：迟滞闸门通过，每轮最多降一级
                effective = old_level.lower()
                self._append(state, old_level, effective, rule,
                             f"{reason}；总分 {result.score} < "
                             f"{lower_threshold(old_level):.2f}-{HYSTERESIS:.2f} 迟滞闸门")
        state.current = replace(result, level=effective)
        return state.current

    def _append(self, state: EventState, old: Level, new: Level, rule: str, reason: str) -> None:
        state.trajectory.append(Transition(self._clock(), old, new, rule, reason))

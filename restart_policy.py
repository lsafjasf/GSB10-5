"""服务自愈与重启策略库（仅标准库）。

核心思想：
  * “进程起来了”不等于“服务活了”。必须同时满足
      1. 持续存活达到 min_alive_duration（最小存活时长）；
      2. 在该窗口内健康检查通过；
    才判定为一次健康存活（health_mark），随后才重置退避计数。
  * 崩溃后按指数退避重启；退避序列完全由注入的时钟决定，便于测试。
  * 若连续多次重启都“起来就崩”（存活 < min_alive_duration），判定为
    重启无效，进入终态 STOPPED_INEFFECTIVE，不再掩盖问题。
  * 依赖长期不可用时不重启服务本体，改为对依赖做低频探活；依赖恢复后
    再重启服务，避免无意义的重启循环。

时间一律由外部注入：所有方法的 now 参数、clock() 调用都可替换，
库本身不 sleep、不读墙钟（clock 默认为 time.monotonic，仅生产用）。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional


class State(Enum):
    STARTING = "starting"                 # 进程已启动，尚未证明存活
    HEALTHY = "healthy"                   # 达到最小存活时长且健康检查通过
    WAITING_RESTART = "waiting_restart"   # 退避等待中（本方可恢复的故障）
    DEP_WAIT = "dep_wait"                 # 依赖不可用，等待依赖探活
    STOPPED_INEFFECTIVE = "stopped_ineffective"  # 重启无效，终态
    STOPPED_MAX_RESTARTS = "stopped_max_restarts"  # 达到重启总数上限，终态


class Action(Enum):
    RESTART = "restart"           # 在 decision.until 时刻重启
    PROBE_DEPENDENCY = "probe"    # 在 decision.until 时刻探测依赖
    NONE = "none"                 # 当前无需动作（仍在等待窗口内）
    STOP = "stop"                 # 进入终态，停止自愈


_TERMINAL = (State.STOPPED_INEFFECTIVE, State.STOPPED_MAX_RESTARTS)


@dataclass
class Config:
    # --- 退避 ---
    initial_backoff: float = 1.0
    backoff_multiplier: float = 2.0
    max_backoff: float = 60.0
    max_total_restarts: Optional[int] = None  # 可选的重启总数硬上限

    # --- 存活判定 ---
    min_alive_duration: float = 30.0

    # --- 无效重启判定：连续 max_fast_failures 次“起来就崩”即终止 ---
    max_fast_failures: int = 5

    # --- 依赖长期不可用 ---
    dependency_available: bool = True
    dep_probe_initial: float = 5.0
    dep_probe_multiplier: float = 2.0
    dep_probe_max: float = 300.0

    # --- 抖动（默认关闭，保证时刻序列确定；测试可注入 rng） ---
    jitter_ratio: float = 0.0
    rng: Optional[random.Random] = None

    # --- 诊断：是否保留全部事件日志（压测时可关闭以体现状态有界） ---
    keep_history: bool = True


@dataclass
class Decision:
    """调度器对本次事件给出的结论。"""
    action: Action
    state: State
    until: Optional[float] = None          # 下次动作的计划时刻
    reason: str = ""

    def __iter__(self):
        # 方便旧写法 action, until = decide(...)
        yield self.action
        yield self.until


@dataclass
class _Event:
    t: float
    kind: str
    detail: str = ""


class RestartSupervisor:
    """事件驱动、无阻塞的重启调度器。

    使用方式（伪代码）：
        sup = RestartSupervisor(Config(...), clock=time.monotonic)
        d = sup.on_started(now)            # 进程拉起
        # 定时到 sup.next_health_check_at() 时调用 on_health_check(...)
        d = sup.on_exit(now, code=1)       # 进程退出
        if d.action is Action.RESTART:
            schedule_restart_at(d.until)
    """

    def __init__(self, config: Optional[Config] = None,
                 clock: Optional[Callable[[], float]] = None):
        self.cfg = config or Config()
        self._clock = clock
        self.state = State.STARTING
        self.started_at: Optional[float] = None
        self.restart_count = 0
        self.fast_failures = 0          # 连续“起来就崩”次数
        self.health_marks = 0          # 健康存活周期数
        self.dep_probe_count = 0       # 本轮依赖不可用期间的探活次数
        self.restart_times: List[float] = []
        self.events: List[_Event] = []
        self.last_reason = ""

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _now(self, now: Optional[float]) -> float:
        if now is not None:
            return now
        if self._clock is not None:
            return self._clock()
        raise ValueError("必须注入时间：传入 now 参数或在构造时提供 clock")

    def _record(self, t: float, kind: str, detail: str = "") -> None:
        if self.cfg.keep_history:
            self.events.append(_Event(t, kind, detail))

    def _jitter(self, delay: float) -> float:
        ratio = self.cfg.jitter_ratio
        if ratio <= 0.0:
            return delay
        rng = self.cfg.rng or random
        return delay * (1.0 + rng.uniform(-ratio, ratio))

    def _backoff(self, n: int) -> float:
        """第 n 次重试（n 从 1 起）的退避时长。"""
        c = self.cfg
        raw = c.initial_backoff * (c.backoff_multiplier ** (n - 1))
        return self._jitter(min(raw, c.max_backoff))

    def _dep_probe_delay(self, probes: int) -> float:
        c = self.cfg
        raw = c.dep_probe_initial * (c.dep_probe_multiplier ** max(probes - 1, 0))
        return min(raw, c.dep_probe_max)

    # ------------------------------------------------------------------ #
    # 事件入口
    # ------------------------------------------------------------------ #
    def on_started(self, now: Optional[float] = None) -> Decision:
        """服务进程被拉起（首次启动或重启后）。"""
        t = self._now(now)
        self.started_at = t
        self.state = State.STARTING
        self._record(t, "started")
        return Decision(Action.NONE, self.state, None, "等待存活证明")

    def on_health_check(self, ok: bool, now: Optional[float] = None) -> Decision:
        """上报一次健康检查结果。

        - STARTING 中：存活达到 min_alive_duration 且检查通过 => 判定存活；
          未达到时长即使通过也仍需继续观察（不提前认定存活）。
        - HEALTHY 中：检查失败 => 视为服务失活，按崩溃处理。
        """
        t = self._now(now)
        c = self.cfg
        uptime = (t - self.started_at) if self.started_at is not None else 0.0

        if self.state == State.STARTING:
            if ok and uptime >= c.min_alive_duration:
                self.state = State.HEALTHY
                self.fast_failures = 0
                self.health_marks += 1
                self._record(t, "healthy", f"uptime={uptime:g}")
                return Decision(Action.NONE, self.state, None, "存活成立")
            if not ok:
                self._record(t, "health_fail", f"uptime={uptime:g}")
                return self._handle_crash(t, "健康检查失败")
            return Decision(Action.NONE, self.state, None,
                            f"存活观察中 uptime={uptime:g}<{c.min_alive_duration:g}")

        if self.state == State.HEALTHY:
            if not ok:
                self._record(t, "health_fail", "运行中健康检查失败")
                return self._handle_crash(t, "运行中健康检查失败")
            return Decision(Action.NONE, self.state, None, "健康")

        return Decision(Action.NONE, self.state, None, "非运行态，忽略健康检查")

    def on_exit(self, now: Optional[float] = None, code: int = 0) -> Decision:
        """进程退出。非零退出码，或存活未达标就退出，都按崩溃计。"""
        t = self._now(now)
        if self.state in _TERMINAL:
            return Decision(Action.NONE, self.state, None, "终态，忽略事件")
        uptime = (t - self.started_at) if self.started_at is not None else 0.0
        if code != 0 or uptime < self.cfg.min_alive_duration:
            reason = f"退出码={code}" if code != 0 else "存活时长不足即退出"
            self._record(t, "crash", f"uptime={uptime:g}, {reason}")
            return self._handle_crash(t, reason)
        # 已健康存活后正常退出：不重启
        self._record(t, "clean_exit", f"uptime={uptime:g}")
        return Decision(Action.STOP, State.HEALTHY, None, "健康后正常退出，不重启")

    def on_dependency_report(self, available: bool,
                             now: Optional[float] = None) -> Decision:
        """外部上报依赖可用性（健康检查发现依赖缺失时用）。"""
        t = self._now(now)
        self.cfg.dependency_available = available
        self._record(t, "dependency", f"available={available}")
        if not available and self.state != State.DEP_WAIT:
            self.state = State.DEP_WAIT
            return Decision(Action.PROBE_DEPENDENCY, self.state,
                            t + self._dep_probe_delay(1), "依赖不可用，开始探活")
        if available and self.state == State.DEP_WAIT:
            # 依赖恢复：立即安排一次服务重启
            self.dep_probe_count = 0
            return self._schedule_restart(t, 0.0, "依赖恢复，立即重启")
        return Decision(Action.NONE, self.state, None, "依赖状态无变化")

    def reset(self, now: Optional[float] = None) -> None:
        """人工介入后清掉终态，重新开始。"""
        t = self._now(now)
        self.state = State.STARTING
        self.fast_failures = 0
        self.restart_count = 0
        self.dep_probe_count = 0
        self.started_at = None
        self._record(t, "manual_reset")

    # ------------------------------------------------------------------ #
    # 核心决策
    # ------------------------------------------------------------------ #
    def _handle_crash(self, t: float, reason: str) -> Decision:
        c = self.cfg

        # 1) 依赖不可用：不重启服务本体，转低频探活
        if not c.dependency_available:
            self.state = State.DEP_WAIT
            until = t + self._dep_probe_delay(self.dep_probe_count + 1)
            self._record(t, "dep_blocked", reason)
            return Decision(Action.PROBE_DEPENDENCY, self.state, until,
                            "依赖不可用，挂起重启并探活依赖")

        # 2) 存活判定：本次是否“起来就崩”
        uptime = (t - self.started_at) if self.started_at is not None else 0.0
        if uptime < c.min_alive_duration:
            self.fast_failures += 1
        else:
            # 已证明能健康存活的服务再次崩溃：退避从头算，不计无效重启
            self.fast_failures = 0

        # 3) 重启无效判定：连续多次起来就崩 => 终止
        if self.fast_failures >= c.max_fast_failures:
            self.state = State.STOPPED_INEFFECTIVE
            self.last_reason = reason
            self._record(t, "give_up",
                         f"连续{self.fast_failures}次无效重启：{reason}")
            return Decision(Action.STOP, self.state, None,
                            f"连续{self.fast_failures}次起来就崩，判定重启无效，"
                            "终止自愈并上报告警")

        # 4) 重启总数硬上限（可选）
        if (c.max_total_restarts is not None
                and self.restart_count >= c.max_total_restarts):
            self.state = State.STOPPED_MAX_RESTARTS
            self._record(t, "give_up", "达到重启总数上限")
            return Decision(Action.STOP, self.state, None,
                            "达到重启总数上限，终止自愈")

        # 5) 指数退避重启。健康存活后崩溃视为新一轮故障，退避从头算起。
        delay = self._backoff(self.fast_failures if self.fast_failures > 0 else 1)
        return self._schedule_restart(t, delay, reason)

    def _schedule_restart(self, t: float, delay: float, reason: str) -> Decision:
        self.restart_count += 1
        until = t + delay
        if self.cfg.keep_history:
            self.restart_times.append(until)
        self.state = State.WAITING_RESTART
        self._record(t, "schedule_restart", f"at={until:g} ({reason})")
        return Decision(Action.RESTART, self.state, until, reason)

    def on_dependency_probe(self, available: bool,
                            now: Optional[float] = None) -> Decision:
        """一次依赖探活的结果（由调度器在 PROBE_DEPENDENCY 时刻回调）。"""
        t = self._now(now)
        self.dep_probe_count += 1
        self._record(t, "dep_probe", f"available={available}")
        self.cfg.dependency_available = available
        if available:
            self.dep_probe_count = 0
            return self._schedule_restart(t, 0.0, "依赖探活成功，立即重启")
        until = t + self._dep_probe_delay(self.dep_probe_count + 1)
        self.state = State.DEP_WAIT
        return Decision(Action.PROBE_DEPENDENCY, self.state, until,
                        "依赖仍不可用，退避后再次探活")

    # ------------------------------------------------------------------ #
    # 观测
    # ------------------------------------------------------------------ #
    def next_health_check_at(self, step: float = 1.0) -> Optional[float]:
        """建议的下一次健康检查时刻（注入式调度器可忽略，自行轮询）。"""
        if self.state in (State.STARTING, State.HEALTHY) and self.started_at is not None:
            return self.started_at + self.cfg.min_alive_duration
        return None

    def history(self):
        return [(e.t, e.kind, e.detail) for e in self.events]

"""级别下发与传播。

传播模型（模拟控制面推送）：
  1. set_level / set_guard_level 只修改“期望状态”，并生成一条投递事件，
     投递时刻 = 当前虚拟时间 + delay_fn(source)（每节点传播延迟）。
  2. advance_to(t) 把所有到期的投递事件应用到节点上，节点此刻才真正生效。
  3. 每个节点维护单调递增的 change_seq；过期事件（change_seq 小于已应用
     序号）直接丢弃，因此乱序/重复投递下仍是 last-write-wins，不会回退。

一致性校验 consistency_check()：逐节点比较“期望状态”（requested 与
guard 覆盖的较大者）与“已生效状态”，并核对 change_seq 是否全部应用，
任何未生效节点都会出现在 mismatches 里。
"""

import heapq
import itertools

from .audit import AuditLog
from .levels import INFO, coerce, level_name


class SourceState:
    __slots__ = (
        "requested_level",
        "guard_level",
        "effective_level",
        "version",
        "applied_version",
        "change_seq",
        "applied_seq",
        "applied_at",
    )

    def __init__(self, level):
        self.requested_level = level
        self.guard_level = None
        self.effective_level = level
        self.version = 0
        self.applied_version = 0
        self.change_seq = 0
        self.applied_seq = 0
        self.applied_at = 0.0


class ConsistencyReport:
    def __init__(self, total, mismatches):
        self.total = total
        self.mismatches = mismatches

    @property
    def ok(self):
        return not self.mismatches

    def summary(self):
        if self.ok:
            return "total=%d ok=True mismatches=0" % self.total
        return "total=%d ok=False mismatches=%d" % (self.total, len(self.mismatches))


class LogLevelManager:
    def __init__(self, delay_fn=None, audit=None, on_apply=None):
        self._delay_fn = delay_fn or (lambda source: 0.0)
        self.audit = audit or AuditLog()
        self._on_apply = on_apply
        self._sources = {}
        self._events = []
        self._event_ids = itertools.count(1)
        self._version = 0
        self._now = 0.0

    @property
    def now(self):
        return self._now

    def add_source(self, name, level=INFO):
        state = SourceState(coerce(level))
        self._sources[name] = state
        return state

    def state(self, name):
        return self._sources[name]

    def sources(self):
        return sorted(self._sources)

    def desired_level(self, name):
        state = self._sources[name]
        return max(state.requested_level, state.guard_level or 0)

    def effective_level(self, name):
        return self._sources[name].effective_level

    def set_level(self, name, level, operator, reason=""):
        """操作者调整期望级别；返回本次变更的全局版本号。"""
        level = coerce(level)
        state = self._sources[name]
        old = state.requested_level
        self._version += 1
        state.version = self._version
        state.requested_level = level
        self.audit.append(
            operator, name, level_name(old), level_name(level), state.version, reason
        )
        self._schedule(name, state)
        return state.version

    def set_guard_level(self, name, level, reason=""):
        """限流守卫的自动覆盖（operator 记为 auto-guard）；None 表示解除。"""
        state = self._sources[name]
        old = state.guard_level
        state.guard_level = None if level is None else coerce(level)
        self.audit.append(
            "auto-guard",
            name,
            level_name(old),
            level_name(state.guard_level),
            state.version,
            reason,
        )
        self._schedule(name, state)

    def _schedule(self, name, state):
        state.change_seq += 1
        deliver_at = self._now + self._delay_fn(name)
        event = (
            deliver_at,
            next(self._event_ids),
            name,
            state.change_seq,
            state.version,
            state.requested_level,
            state.guard_level,
        )
        heapq.heappush(self._events, event)

    def advance_to(self, t):
        """推进虚拟时钟到 t，应用所有到期的投递事件。"""
        while self._events and self._events[0][0] <= t:
            deliver_at, _, name, change_seq, version, requested, guard = heapq.heappop(
                self._events
            )
            state = self._sources[name]
            if change_seq > state.applied_seq:
                state.effective_level = max(requested, guard or 0)
                state.applied_seq = change_seq
                state.applied_version = version
                state.applied_at = deliver_at
                if self._on_apply:
                    self._on_apply(name, version, requested, guard, deliver_at)
        if t > self._now:
            self._now = t

    def advance(self, dt):
        self.advance_to(self._now + dt)

    def consistency_check(self):
        """校验所有节点：已生效级别 == 期望级别，且无未应用的变更。"""
        mismatches = []
        for name in sorted(self._sources):
            state = self._sources[name]
            expected = max(state.requested_level, state.guard_level or 0)
            if state.applied_seq != state.change_seq or state.effective_level != expected:
                mismatches.append(
                    {
                        "source": name,
                        "expected": level_name(expected),
                        "effective": level_name(state.effective_level),
                        "change_seq": state.change_seq,
                        "applied_seq": state.applied_seq,
                    }
                )
        return ConsistencyReport(len(self._sources), mismatches)

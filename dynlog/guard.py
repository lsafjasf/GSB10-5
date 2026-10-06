"""日志量上限守卫：超限时自动收敛（调高有效级别），回落后自动释放。

每个观测周期调用 observe(volumes)：
  - 总量 > cap：按日志量从大到小，对最吵的源逐级调高 guard 覆盖级别，
    直到按 reduction_factor 估算的削减量覆盖超出的部分。
  - 总量 <= cap * release_ratio 且持续 cooldown 个周期：把 guard 覆盖
    逐级往回放（向操作者期望级别靠拢）。
  - guard 覆盖级别 <= 操作者期望级别时覆盖已冗余，立即解除。

所有自动调整都通过 manager.set_guard_level 下发，走同样的传播通道，
并写入审计（operator=auto-guard）。
"""

from .levels import next_higher, next_lower


class VolumeGuard:
    def __init__(self, manager, cap, reduction_factor=3.0, cooldown=3, release_ratio=0.9):
        self.manager = manager
        self.cap = float(cap)
        self.reduction_factor = float(reduction_factor)
        self.cooldown = cooldown
        self.release_ratio = release_ratio
        self.escalations = 0
        self.relaxations = 0
        self._cool = 0

    def observe(self, volumes):
        total = float(sum(volumes.values()))
        if total > self.cap:
            self._cool = 0
            self._escalate(volumes, total)
        else:
            self._maybe_relax(total)
        return total

    def _escalate(self, volumes, total):
        excess = total - self.cap
        noisy_first = sorted(volumes, key=volumes.get, reverse=True)
        for name in noisy_first:
            if excess <= 0:
                break
            state = self.manager.state(name)
            current = max(state.requested_level, state.guard_level or 0)
            higher = next_higher(current)
            if higher is None:
                continue
            self.manager.set_guard_level(
                name, higher, reason="volume %.0f > cap %.0f" % (total, self.cap)
            )
            self.escalations += 1
            excess -= volumes[name] * (1.0 - 1.0 / self.reduction_factor)

    def _maybe_relax(self, total):
        manager = self.manager
        for name in manager.sources():
            state = manager.state(name)
            if state.guard_level is not None and state.guard_level <= state.requested_level:
                manager.set_guard_level(name, None, reason="override redundant, release")
                self.relaxations += 1
        if total <= self.cap * self.release_ratio:
            self._cool += 1
        else:
            self._cool = 0
        if self._cool < self.cooldown:
            return
        self._cool = 0
        for name in manager.sources():
            state = manager.state(name)
            if state.guard_level is None or state.guard_level <= state.requested_level:
                continue
            lower = next_lower(state.guard_level)
            if lower is None or lower <= state.requested_level:
                manager.set_guard_level(name, None, reason="volume back under cap")
            else:
                manager.set_guard_level(name, lower, reason="volume back under cap")
            self.relaxations += 1

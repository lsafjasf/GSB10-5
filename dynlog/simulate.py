"""日志量仿真：每个源按当前“已生效级别”产生日志。

倍率表刻画级别对日志量的影响：DEBUG 是 WARN 的 10 倍，与线上
“级别一调到底、日志量涨几百倍”的场景同构（高频源基础速率更高，
整体放大倍数随之叠加）。
"""

from .levels import DEBUG, INFO, WARN, ERROR, OFF

MULTIPLIER = {
    DEBUG: 10.0,
    INFO: 3.0,
    WARN: 1.0,
    ERROR: 0.3,
    OFF: 0.0,
}


class Workload:
    def __init__(self, manager, base_rates):
        self.manager = manager
        self.base_rates = dict(base_rates)

    def tick(self):
        """返回本周期 {source: emitted_count}。"""
        manager = self.manager
        return {
            name: self.base_rates[name] * MULTIPLIER[manager.effective_level(name)]
            for name in self.base_rates
        }

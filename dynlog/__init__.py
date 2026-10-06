"""动态日志级别调整库（仅标准库）。

模块:
  levels   日志级别定义与升降级工具
  audit    不可变追加式审计记录
  manager  级别下发、传播与一致性校验
  guard    日志量上限守卫，超限时自动收敛
  simulate 日志量仿真（按生效级别产生日志）
"""

from .levels import DEBUG, INFO, WARN, ERROR, OFF, coerce, level_name
from .audit import AuditLog, AuditRecord
from .manager import LogLevelManager, SourceState, ConsistencyReport
from .guard import VolumeGuard
from .simulate import Workload

__all__ = [
    "DEBUG",
    "INFO",
    "WARN",
    "ERROR",
    "OFF",
    "coerce",
    "level_name",
    "AuditLog",
    "AuditRecord",
    "LogLevelManager",
    "SourceState",
    "ConsistencyReport",
    "VolumeGuard",
    "Workload",
]

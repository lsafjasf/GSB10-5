"""数据保留期自动清理库（仅标准库，时间由调用方注入）。"""

from .core import (
    CleanupReport,
    Decision,
    DeletionEntry,
    DanglingReference,
    Policy,
    Record,
    SparedEntry,
    evaluate,
)

__version__ = "0.1.0"

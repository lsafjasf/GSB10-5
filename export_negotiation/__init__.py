"""数据导出格式协商库（仅标准库）。"""

from .negotiator import (
    DOWNGRADED,
    EXACT,
    PARTIAL,
    UNSUPPORTED,
    negotiate,
)
from .registry import ALIASES, FALLBACK_FAMILY, FORMATS

__all__ = [
    "EXACT",
    "DOWNGRADED",
    "PARTIAL",
    "UNSUPPORTED",
    "negotiate",
    "FORMATS",
    "ALIASES",
    "FALLBACK_FAMILY",
]

__version__ = "1.0.0"

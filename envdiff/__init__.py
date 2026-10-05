"""envdiff：环境参数差异对比库（仅标准库）。"""
from .attribution import ExpectedSpec, WhitelistRule
from .engine import (
    CATEGORY_LABELS,
    INTENTIONAL,
    SUSPECTED_OMISSION,
    TO_CONFIRM,
    Report,
    compare,
)
from .sensitive import is_sensitive
from .loader import load_env_file

__all__ = [
    "ExpectedSpec",
    "WhitelistRule",
    "compare",
    "is_sensitive",
    "load_env_file",
    "Report",
    "INTENTIONAL",
    "TO_CONFIRM",
    "SUSPECTED_OMISSION",
    "CATEGORY_LABELS",
]

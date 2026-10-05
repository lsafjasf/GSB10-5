"""rollback_check：回滚方案可行性验证库（Python 3 标准库）。"""

from . import gen, perf, schema, verify
from .oldreader import OldReader
from .verify import (
    ROLLBACK_OK, NEEDS_CONVERSION, NOT_ROLLBACKABLE,
    Verifier, build_report,
)

__all__ = [
    "gen", "perf", "schema", "verify", "OldReader", "Verifier",
    "build_report", "ROLLBACK_OK", "NEEDS_CONVERSION", "NOT_ROLLBACKABLE",
]

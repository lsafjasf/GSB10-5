"""资源清单差异比对库（仅使用 Python 标准库）。

公开接口：
    compare_manifests(baseline, candidate) -> dict
    load_manifest(path) -> dict
"""
from .core import (
    VERDICT_COMPLIANT,
    VERDICT_VIOLATION,
    VERDICT_NEEDS_REVIEW,
    KEY_FIELDS,
    ALLOWED_FIELDS,
    compare_manifests,
    load_manifest,
)

__all__ = [
    "VERDICT_COMPLIANT",
    "VERDICT_VIOLATION",
    "VERDICT_NEEDS_REVIEW",
    "KEY_FIELDS",
    "ALLOWED_FIELDS",
    "compare_manifests",
    "load_manifest",
]

"""标注数据一致性核对库（仅依赖 Python 标准库）。

公开接口见 ``annotation_consistency.core`` 与 ``annotation_consistency.metrics``。
"""

from .core import (
    Annotation,
    Conflict,
    ConflictType,
    overlap_length,
    spans_overlap,
    classify,
    check_consistency,
)
from .metrics import PairwiseMetrics, pairwise_metrics, cohens_kappa_chars
from .report import conflicts_to_rows, write_conflicts_csv

__all__ = [
    "Annotation",
    "Conflict",
    "ConflictType",
    "overlap_length",
    "spans_overlap",
    "classify",
    "check_consistency",
    "PairwiseMetrics",
    "pairwise_metrics",
    "cohens_kappa_chars",
    "conflicts_to_rows",
    "write_conflicts_csv",
]

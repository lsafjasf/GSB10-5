"""anoncheck: re-identification risk checking for anonymized datasets.

Standard library only. See docs/IDENTIFICATION.md for methodology.
"""

from .core import (
    RiskReport,
    build_report,
    count_classes,
    find_risky_records,
    iter_records,
    risky_class_map,
    screen_combinations,
    suppress_records,
)
from .hierarchies import get_transform, parse_generalize_arg

__version__ = "1.0.0"

__all__ = [
    "RiskReport",
    "build_report",
    "count_classes",
    "find_risky_records",
    "get_transform",
    "iter_records",
    "parse_generalize_arg",
    "risky_class_map",
    "screen_combinations",
    "suppress_records",
]

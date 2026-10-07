"""cardinality_guard -- metric label cardinality control (stdlib only).

Pipeline: observe (Detector) -> identify high-cardinality labels
-> plan merge rules (auto_plan) -> guard (validate_policy) -> merge.
"""

from .sketch import HyperLogLog, SpaceSaving, LabelSketch
from .detector import Thresholds, CardinalityDetector, DetectionReport, LabelReport
from .normalize import template_of, normalize_path, extract_channel, template_distribution
from .policy import (
    PolicyError, Rule, Policy, PlanEntry,
    merge_labels, auto_plan, check_must_distinguish, validate_policy, OTHER,
)
from .measure import measure_merged, raw_series_hll

__version__ = "0.1.0"

__all__ = [
    "HyperLogLog", "SpaceSaving", "LabelSketch",
    "Thresholds", "CardinalityDetector", "DetectionReport", "LabelReport",
    "template_of", "normalize_path", "extract_channel", "template_distribution",
    "PolicyError", "Rule", "Policy", "PlanEntry",
    "merge_labels", "auto_plan", "check_must_distinguish", "validate_policy",
    "OTHER", "measure_merged", "raw_series_hll",
]

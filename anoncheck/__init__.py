"""anoncheck: re-identification risk checker (Python stdlib only)."""

from .quasi import identify_quasi_identifiers, enumerate_combinations
from .risk import evaluate_combination, find_risky_records
from .transform import apply_generalization, suppress_risky_records

__all__ = [
    "identify_quasi_identifiers",
    "enumerate_combinations",
    "evaluate_combination",
    "find_risky_records",
    "apply_generalization",
    "suppress_risky_records",
]

__version__ = "0.1.0"

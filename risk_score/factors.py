"""Scoring factors: definitions, literature rationale, and normalization.

Factor *selection* is grounded in published empirical software-engineering
research (see FACTOR_SOURCES below).  Factor *weights* are NOT hard-coded:
they are fitted from labelled historical outcomes by ``risk_score.model``.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import exp, log1p
from typing import Callable, Dict, List, Sequence, Tuple


@dataclass(frozen=True)
class FileInfo:
    """A file touched by a change."""
    path: str
    churn_90d: int          # commits touching this file in the last 90 days
    test_ratio: float       # test code / (test + prod code) for this file, 0..1
    ownership_frac: float   # top author's share of commits on this file, 0..1


@dataclass(frozen=True)
class ChangeMetrics:
    """Raw, measurable attributes of one change."""
    lines_added: int
    lines_deleted: int
    files: Tuple[FileInfo, ...]
    author_prior_changes: int        # author's lifetime changes in this repo
    author_prior_rollbacks: int      # how many of those were rolled back
    reviewer_count: int
    review_comment_count: int
    is_emergency: bool               # emergency / expedited change
    is_off_hours: bool               # outside business hours / weekend
    is_release_window: bool          # merged near a release cut / freeze


# --- Factor definitions -----------------------------------------------------
# Each factor maps raw metrics to a raw risk signal in [0, 1] where 1 is the
# riskiest end.  The mapping shape follows the cited literature; the exact
# numeric weight of each factor is learned, not assumed.

def _size(m: ChangeMetrics) -> float:
    churn = m.lines_added + m.lines_deleted
    return min(1.0, log1p(churn) / log1p(10_000))  # 10k-line change -> 1.0


def _hotness(m: ChangeMetrics) -> float:
    if not m.files:
        return 0.0
    return min(1.0, max(f.churn_90d for f in m.files) / 20.0)


def _test_gap(m: ChangeMetrics) -> float:
    if not m.files:
        return 1.0
    avg_ratio = sum(f.test_ratio for f in m.files) / len(m.files)
    return 1.0 - min(1.0, max(0.0, avg_ratio))


def _author_inexperience(m: ChangeMetrics) -> float:
    # 1/(1+x): an author with 0 prior changes scores 1.0; ~9 changes -> 0.1
    return 1.0 / (1.0 + max(0, m.author_prior_changes))


def _author_rollback_rate(m: ChangeMetrics) -> float:
    if m.author_prior_changes <= 0:
        return 0.5  # no history: neutral
    return m.author_prior_rollbacks / m.author_prior_changes


def _review_gap(m: ChangeMetrics) -> float:
    if m.reviewer_count <= 0:
        return 1.0
    depth = 1.0 - exp(-m.review_comment_count / 5.0)
    breadth = min(1.0, m.reviewer_count / 3.0)
    return 1.0 - depth * breadth


def _schedule_pressure(m: ChangeMetrics) -> float:
    return min(1.0, 0.6 * m.is_emergency + 0.3 * m.is_off_hours
               + 0.3 * m.is_release_window)


@dataclass(frozen=True)
class Factor:
    name: str
    description: str
    source: str          # literature rationale for including this factor
    compute: Callable[[ChangeMetrics], float]


FACTORS: Tuple[Factor, ...] = (
    Factor(
        "size",
        "Change size (log-scaled added+deleted lines)",
        "Nagappan & Ball, MSR 2005: relative code churn predicts defect "
        "density; Forsgren et al., 'Accelerate' (2018): small batches are a "
        "key predictor of delivery performance.",
        _size,
    ),
    Factor(
        "hotness",
        "Hotspot files (max 90-day churn of touched files)",
        "Tornhill, 'Your Code as a Crime Scene' (2015): high-churn hotspots "
        "concentrate defects.",
        _hotness,
    ),
    Factor(
        "test_gap",
        "Missing automated tests around touched files",
        "Mockus et al., IEEE TSE 2003: test coverage is a significant "
        "predictor of field failures.",
        _test_gap,
    ),
    Factor(
        "author_inexperience",
        "Author's unfamiliarity with the codebase",
        "Mockus & Weiss, IEEE TSE 2000: developer experience correlates with "
        "change quality.",
        _author_inexperience,
    ),
    Factor(
        "author_rollback_rate",
        "Author's historical rollback rate",
        "Herzig & Zeller, MSR 2013 / Shihab et al.: past outcome history is "
        "predictive of future change risk.",
        _author_rollback_rate,
    ),
    Factor(
        "review_gap",
        "Weak code review (few reviewers / shallow discussion)",
        "McIntosh et al., IEEE TSE 2016: review participation and discussion "
        "coverage correlate with post-release defects.",
        _review_gap,
    ),
    Factor(
        "schedule_pressure",
        "Emergency / off-hours / release-window timing",
        "Google SRE Book (2016), ch. 14 'Managing Incidents': emergency "
        "changes carry elevated risk; 'Accelerate' (2018): change-failure "
        "rate is a core delivery metric.",
        _schedule_pressure,
    ),
)

FACTOR_NAMES: Tuple[str, ...] = tuple(f.name for f in FACTORS)


def raw_factor_vector(m: ChangeMetrics) -> List[float]:
    """Raw per-factor risk signals, each in [0, 1]."""
    return [f.compute(m) for f in FACTORS]


# --- Empirical normalization ------------------------------------------------
# Raw signals are mapped to their empirical percentile within a reference
# (historical) population.  This introduces no extra tunable constants: the
# mapping is fully determined by the reference data.

def fit_ecdf(reference_vectors: Sequence[Sequence[float]]) -> List[List[float]]:
    """Return per-factor sorted reference values used for percentile lookup."""
    n_factors = len(FACTORS)
    cols: List[List[float]] = [[] for _ in range(n_factors)]
    for vec in reference_vectors:
        for j, v in enumerate(vec):
            cols[j].append(v)
    for col in cols:
        col.sort()
    return cols


def _percentile(sorted_vals: Sequence[float], x: float) -> float:
    """Empirical CDF with average ranks for ties, in [0, 1]."""
    n = len(sorted_vals)
    if n == 0:
        return 0.5
    lo, hi = 0, n
    while lo < hi:  # first index >= x
        mid = (lo + hi) // 2
        if sorted_vals[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    first = lo
    lo, hi = first, n
    while lo < hi:  # first index > x
        mid = (lo + hi) // 2
        if sorted_vals[mid] <= x:
            lo = mid + 1
        else:
            hi = mid
    last_exclusive = lo
    avg_rank = (first + last_exclusive - 1) / 2.0
    return avg_rank / (n - 1) if n > 1 else 0.5


def normalize_vector(raw: Sequence[float], ecdf: List[List[float]]) -> List[float]:
    return [_percentile(ecdf[j], raw[j]) for j in range(len(FACTORS))]

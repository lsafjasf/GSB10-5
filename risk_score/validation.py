"""Outcome validation: rank correlation, discrimination, calibration."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence, Tuple


def _average_ranks(values: Sequence[float]) -> List[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based average rank
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(scores: Sequence[float], labels: Sequence[int]) -> float:
    """Spearman rank correlation between scores and binary outcomes.

    Uses average ranks and the exact Pearson-of-ranks formula (handles ties).
    """
    n = len(scores)
    rs, rl = _average_ranks(scores), _average_ranks(labels)
    ms, ml = sum(rs) / n, sum(rl) / n
    cov = sum((a - ms) * (b - ml) for a, b in zip(rs, rl))
    vs = sum((a - ms) ** 2 for a in rs)
    vl = sum((b - ml) ** 2 for b in rl)
    if vs <= 0 or vl <= 0:
        return 0.0
    return cov / math.sqrt(vs * vl)


def point_biserial(scores: Sequence[float], labels: Sequence[int]) -> float:
    """Pearson correlation between continuous score and binary label."""
    n = len(scores)
    mean = sum(scores) / n
    cov = sum((s - mean) * l for s, l in zip(scores, labels))
    vs = sum((s - mean) ** 2 for s in scores)
    yl = sum(labels)
    vl_factor = yl * (n - yl)
    if vs <= 0 or vl_factor <= 0:
        return 0.0
    return cov / math.sqrt(vs * vl_factor / n)


def auc(scores: Sequence[float], labels: Sequence[int]) -> float:
    """ROC AUC via Mann-Whitney U with tie correction."""
    pos = [s for s, l in zip(scores, labels) if l == 1]
    neg = [s for s, l in zip(scores, labels) if l == 0]
    if not pos or not neg:
        return float("nan")
    wins = 0.0
    for ps in pos:
        for ns in neg:
            if ps > ns:
                wins += 1.0
            elif ps == ns:
                wins += 0.5
    return wins / (len(pos) * len(neg))


def auc_fast(scores: Sequence[float], labels: Sequence[int]) -> float:
    """O(n log n) AUC using average ranks (same value as Mann-Whitney)."""
    n = len(scores)
    ranks = _average_ranks(scores)
    n_pos = sum(labels)
    n_neg = n - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    sum_ranks_pos = sum(r for r, l in zip(ranks, labels) if l == 1)
    return (sum_ranks_pos - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


@dataclass(frozen=True)
class Calibration:
    mean_predicted: float
    observed_rate: float
    brier: float
    decile_table: Tuple[Tuple[float, float, float, int], ...]  # mean_pred, obs, n


def calibration(probs: Sequence[float], labels: Sequence[int],
                bins: int = 10) -> Calibration:
    order = sorted(range(len(probs)), key=lambda i: probs[i])
    table = []
    for b in range(bins):
        idxs = order[b * len(order) // bins:(b + 1) * len(order) // bins]
        if not idxs:
            continue
        mp = sum(probs[i] for i in idxs) / len(idxs)
        obs = sum(labels[i] for i in idxs) / len(idxs)
        table.append((mp, obs, mp - obs, len(idxs)))
    brier = sum((p - l) ** 2 for p, l in zip(probs, labels)) / len(probs)
    return Calibration(sum(probs) / len(probs), sum(labels) / len(labels),
                       brier, tuple(table))


def choose_threshold(scores: Sequence[float], labels: Sequence[int],
                     base_rate: float, delta: float = 0.05) -> float:
    """Pick a score threshold for 'high risk' buckets.

    Smallest decile boundary whose observed rollback rate is at least
    base_rate + delta, i.e. where risk becomes meaningfully elevated.
    """
    order = sorted(scores)
    n = len(order)
    thresholds = [order[int(n * q)] for q in (0.5, 0.6, 0.7, 0.8, 0.9)]
    best = order[int(n * 0.8)]
    for t in thresholds:
        bucket = [l for s, l in zip(scores, labels) if s >= t]
        if bucket and sum(bucket) / len(bucket) >= base_rate + delta:
            return t
    return best


@dataclass(frozen=True)
class BucketStats:
    score_band: str
    n: int
    rollback_rate: float
    share_of_rollbacks: float


def risk_bands(scores: Sequence[float], labels: Sequence[int],
               edges: Sequence[float]) -> Tuple[BucketStats, ...]:
    total_rb = sum(labels) or 1
    out = []
    cuts = list(edges) + [math.inf]
    names = []
    for i in range(len(cuts)):
        lo = 0.0 if i == 0 else cuts[i - 1]
        names.append(f"[{lo:.0f},{cuts[i]:.0f})" if i < len(cuts) - 1
                     else f"{lo:.0f}+")
    for i, hi in enumerate(cuts):
        lo = 0.0 if i == 0 else cuts[i - 1]
        members = [(s, l) for s, l in zip(scores, labels) if lo <= s < hi]
        n = len(members)
        rb = sum(l for _, l in members)
        out.append(BucketStats(names[i], n, rb / n if n else 0.0, rb / total_rb))
    return tuple(out)

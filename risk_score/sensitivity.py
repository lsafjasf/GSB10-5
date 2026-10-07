"""Weight sensitivity: how much do conclusions move if weights are perturbed?

Two views:
1. One-factor-at-a-time (OAT): shift each weight by +/-10% (redistributing
   the difference evenly across the other factors) and measure the change in
   score ranking and high-risk classifications.
2. Monte Carlo: draw weight vectors from a Dirichlet centred on the fitted
   weights (concentration reflects fitted coefficient standard errors) and
   measure the distribution of outcomes.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Callable, List, Sequence, Tuple

from .factors import FACTOR_NAMES
from .model import risk_score


def _normalized(v: Sequence[float]) -> List[float]:
    s = sum(v)
    return [x / s for x in v]


@dataclass(frozen=True)
class OATResult:
    factor: str
    delta_frac: float
    mean_abs_score_change: float
    spearman: float
    fraction_reclassified: float


def oat_sensitivity(X: Sequence[Sequence[float]], weights: Sequence[float],
                    threshold: float, delta_frac: float = 0.10
                    ) -> Tuple[OATResult, ...]:
    base = [risk_score(x, weights) for x in X]
    k = len(weights)
    out: List[OATResult] = []
    for j in range(k):
        for sign in (-1.0, 1.0):
            w = list(weights)
            d = w[j] * delta_frac * sign
            w[j] += d
            for i2 in range(k):
                if i2 != j:
                    w[i2] -= d / (k - 1)
            perturbed = [risk_score(x, w) for x in X]
            reclass = sum(1 for a, b in zip(base, perturbed)
                          if (a >= threshold) != (b >= threshold)) / len(X)
            out.append(OATResult(
                factor=FACTOR_NAMES[j],
                delta_frac=sign * delta_frac,
                mean_abs_score_change=sum(abs(a - b) for a, b in zip(base, perturbed)) / len(X),
                spearman=_rank_corr(base, perturbed),
                fraction_reclassified=reclass,
            ))
    return tuple(out)


def _rank_corr(a: Sequence[float], b: Sequence[float]) -> float:
    from .validation import _average_ranks
    n = len(a)
    ra, rb = _average_ranks(a), _average_ranks(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra)
    vb = sum((y - mb) ** 2 for y in rb)
    return cov / math.sqrt(va * vb) if va > 0 and vb > 0 else 1.0


@dataclass(frozen=True)
class MCResult:
    draws: int
    score_mean: float
    score_std: float
    score_p05: float
    score_p95: float
    rank_corr_min: float
    highrisk_prob_mean: float
    highrisk_flip_rate: float


def monte_carlo_sensitivity(X: Sequence[Sequence[float]], weights: Sequence[float],
                            std_errors: Sequence[float], threshold: float,
                            draws: int = 400, seed: int = 7,
                            beta: Sequence[float] | None = None
                            ) -> Tuple[MCResult, Tuple[Tuple[float, ...], ...]]:
    """Return aggregate result plus per-change score distribution summaries.

    Each draw perturbs the fitted coefficients by sampling
    beta_j ~ Gamma(shape=(beta_j/se_j)^2), i.e. a distribution with the
    fitted mean and the fitted standard error, then renormalizing to weights.
    """
    rng = random.Random(seed)
    k = len(weights)
    if beta is None:
        beta = list(weights)
    shapes = []
    for b, se in zip(beta, std_errors):
        if se and se == se and se > 0 and b > 0:
            shapes.append((b / se) ** 2)
        else:
            shapes.append(100.0)

    base_flags = [risk_score(x, weights) >= threshold for x in X]
    per_change_scores: List[List[float]] = [[] for _ in X]
    flips = 0
    min_rank = 1.0
    highrisk_counts = [0] * len(X)
    for d in range(draws):
        w = _normalized([max(1e-4, rng.gammavariate(shapes[i], 1.0))
                         for i in range(k)])
        scores = [risk_score(x, w) for x in X]
        if d < draws:
            rc = _rank_corr([risk_score(x, weights) for x in X], scores)
            min_rank = min(min_rank, rc)
        for i, s in enumerate(scores):
            per_change_scores[i].append(s)
            if s >= threshold:
                highrisk_counts[i] += 1
            if (s >= threshold) != base_flags[i]:
                flips += 1
    flat = [s for vals in per_change_scores for s in vals]
    flat.sort()

    def pct(p):
        return flat[min(len(flat) - 1, int(p * len(flat)))]

    mean = sum(flat) / len(flat)
    var = sum((s - mean) ** 2 for s in flat) / len(flat)
    return MCResult(
        draws=draws,
        score_mean=mean, score_std=math.sqrt(var),
        score_p05=pct(0.05), score_p95=pct(0.95),
        rank_corr_min=min_rank,
        highrisk_prob_mean=sum(c / draws for c in highrisk_counts) / len(X),
        highrisk_flip_rate=flips / (draws * len(X)),
    ), tuple(tuple(vals) for vals in per_change_scores)

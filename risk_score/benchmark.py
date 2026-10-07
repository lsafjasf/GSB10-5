"""Performance benchmark: score N changes, report wall time and peak RSS."""
from __future__ import annotations

import resource
import time
from typing import List, Sequence, Tuple

from .factors import ChangeMetrics, normalize_vector, raw_factor_vector
from .model import risk_score


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def run_benchmark(metrics: Sequence[ChangeMetrics], ecdf, weights,
                  keep_results: bool = True) -> dict:
    t0 = time.perf_counter()
    scores: List[float] = []
    for m in metrics:
        x = normalize_vector(raw_factor_vector(m), ecdf)
        s = risk_score(x, weights)
        if keep_results:
            scores.append(s)
    dt = time.perf_counter() - t0
    return {
        "n": len(metrics),
        "seconds": dt,
        "per_change_us": dt / len(metrics) * 1e6,
        "peak_rss_mb": peak_rss_mb(),
        "results_kept": keep_results,
        "mean_score": sum(scores) / len(scores) if scores else None,
    }

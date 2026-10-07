"""Weight fitting and scoring.

Weights come from a logistic regression of normalized factor vectors against
historical rollback labels (did the change get rolled back? 1/0).  This is
the standard "risk factors -> binary outcome" model used throughout the
empirical software-engineering literature (e.g. Mockus et al., IEEE TSE
2003; Shihab et al., MSR 2012).  Coefficients are constrained to be
non-negative (a documented risk factor must not flip sign) by parameterizing
beta_j = softplus(t_j), then normalized to sum to 1 so they read directly as
percentage weights.

Fitting is IRLS (Newton's method / iteratively reweighted least squares) with
a tiny ridge and backtracking, all in the pure standard library.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

from .factors import FACTOR_NAMES


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def _softplus(t: float) -> float:
    return math.log1p(math.exp(-abs(t))) + max(t, 0.0)


@dataclass(frozen=True)
class FittedModel:
    beta: Tuple[float, ...]        # raw logistic coefficients (positive)
    intercept: float
    weights: Tuple[float, ...]     # beta normalized to sum to 1
    std_errors: Tuple[float, ...]  # SE of each beta (delta method)
    log_likelihood: float
    n_samples: int
    converged: bool
    iterations: int


def _log_likelihood(X, y, t, b0):
    ll = 0.0
    for xi, yi in zip(X, y):
        z = b0 + sum(_softplus(tj) * xj for tj, xj in zip(t, xi))
        p = _sigmoid(z)
        ll += yi * math.log(max(p, 1e-15)) + (1 - yi) * math.log(max(1 - p, 1e-15))
    return ll


def _solve_posdef(A: List[List[float]], b: List[float],
                  ridge: float = 1e-8) -> List[float]:
    n = len(A)
    M = [A[i][:] + [b[i]] for i in range(n)]
    for i in range(n):
        M[i][i] += ridge
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-14:
            M[piv][col] = 1e-14
        M[col], M[piv] = M[piv], M[col]
        d = M[col][col]
        M[col] = [v / d for v in M[col]]
        for r in range(n):
            if r != col and M[r][col] != 0.0:
                f = M[r][col]
                M[r] = [a - f * c for a, c in zip(M[r], M[col])]
    return [row[n] for row in M]


def fit_logistic(X: Sequence[Sequence[float]], y: Sequence[int],
                 max_iter: int = 50, tol: float = 1e-9,
                 ridge: float = 1e-6) -> FittedModel:
    n = len(y)
    k = len(X[0])
    t = [0.0] * k          # softplus pre-images of beta
    b0 = 0.0
    ll = _log_likelihood(X, y, t, b0)
    converged = False
    it = 0
    for it in range(1, max_iter + 1):
        # Gradient and expected (Fisher) information in theta=(b0, t) space.
        dim = k + 1
        g = [0.0] * dim
        H = [[0.0] * dim for _ in range(dim)]
        for xi, yi in zip(X, y):
            s_t = [_sigmoid(tj) for tj in t]              # d beta_j / d t_j
            z = b0 + sum(_softplus(t[j]) * xi[j] for j in range(k))
            p = _sigmoid(z)
            err = p - yi
            w = p * (1.0 - p)
            deriv = [1.0] + [s_t[j] * xi[j] for j in range(k)]
            for a in range(dim):
                g[a] += err * deriv[a]
                ra = w * deriv[a]
                for c in range(a, dim):
                    H[a][c] += ra * deriv[c]
        for a in range(dim):
            for c in range(a):
                H[a][c] = H[c][a]
        step = _solve_posdef(H, g)
        # backtracking line search
        alpha = 1.0
        for _ in range(40):
            new_t = [t[j] - alpha * step[j + 1] for j in range(k)]
            new_b0 = b0 - alpha * step[0]
            new_ll = _log_likelihood(X, y, new_t, new_b0)
            if new_ll >= ll - 1e-12:
                break
            alpha *= 0.5
        t = new_t
        b0 = new_b0
        if abs(new_ll - ll) < tol * max(1.0, abs(new_ll)):
            ll = new_ll
            converged = True
            break
        ll = new_ll

    beta = [_softplus(tj) for tj in t]
    total = sum(beta)
    weights = tuple(bj / total for bj in beta)

    # Standard errors via observed/expected Fisher information + delta rule:
    # Var(beta_j) ~= (sigmoid(t_j))^2 * Var(t_j).
    se: List[float] = []
    if all(b > 0 for b in beta):
        cov = _invert(H)
        for j in range(k):
            se.append(_sigmoid(t[j]) * math.sqrt(max(cov[j + 1][j + 1], 0.0)))
    else:
        se = [float("nan")] * k

    return FittedModel(
        beta=tuple(beta), intercept=b0, weights=weights,
        std_errors=tuple(se), log_likelihood=ll, n_samples=n,
        converged=converged, iterations=it,
    )


def _invert(A: List[List[float]]) -> List[List[float]]:
    n = len(A)
    M = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            M[piv][col] = 1e-12
        M[col], M[piv] = M[piv], M[col]
        d = M[col][col]
        M[col] = [v / d for v in M[col]]
        for r in range(n):
            if r != col and M[r][col] != 0.0:
                f = M[r][col]
                M[r] = [a - f * b for a, b in zip(M[r], M[col])]
    return [row[n:] for row in M]


def score_contributions(x: Sequence[float],
                        weights: Sequence[float]) -> List[Tuple[str, float, float, float]]:
    """Decompose a score into per-factor contributions.

    Returns (factor_name, normalized_value, weight, share_of_score) per
    factor; share_of_score sums to 1 when the score is non-zero.
    """
    contribs = [w * v for w, v in zip(weights, x)]
    total = sum(contribs)
    out = []
    for name, v, w, c in zip(FACTOR_NAMES, x, weights, contribs):
        share = c / total if total > 0 else 0.0
        out.append((name, v, w, share))
    return out


def risk_score(x: Sequence[float], weights: Sequence[float]) -> float:
    """Weighted score in [0, 100]."""
    return 100.0 * sum(w * v for w, v in zip(weights, x))


def rollback_probability(x: Sequence[float], model: FittedModel) -> float:
    z = model.intercept + sum(b * v for b, v in zip(model.beta, x))
    return _sigmoid(z)

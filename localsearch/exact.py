"""Exact optima and bounds used to quantify the local-search gap."""

from __future__ import annotations

from fractions import Fraction
from typing import List, Tuple

from .knapsack import KnapsackProblem


def exhaustive_optimum(problem: KnapsackProblem) -> Tuple[int, List[int]]:
    """Brute-force optimum over all 2^n subsets (small n only).

    Ties are broken by the lexicographically smallest bit string so the
    returned certificate is deterministic.
    """
    n = problem.n
    best_value = 0
    best_x = [0] * n

    def visit(i: int, weight: int, value: int, x: List[int]) -> None:
        nonlocal best_value, best_x
        if weight > problem.capacity:
            return
        if i == n:
            if value > best_value or (value == best_value and x < best_x):
                best_value = value
                best_x = list(x)
            return
        x[i] = 0
        visit(i + 1, weight, value, x)
        x[i] = 1
        visit(i + 1, weight + problem.weights[i], value + problem.values[i], x)
        x[i] = 0

    visit(0, 0, 0, [0] * n)
    return best_value, best_x


def dp_optimum(problem: KnapsackProblem) -> int:
    """Exact optimum by capacity-indexed DP; O(n * capacity) time/space."""
    dp = [0] * (problem.capacity + 1)
    for value, weight in zip(problem.values, problem.weights):
        for c in range(problem.capacity, weight - 1, -1):
            candidate = dp[c - weight] + value
            if candidate > dp[c]:
                dp[c] = candidate
    return max(dp)


def fractional_upper_bound(problem: KnapsackProblem) -> Fraction:
    """LP relaxation bound: fill capacity by value/weight density."""
    order = sorted(
        range(problem.n),
        key=lambda i: (-Fraction(problem.values[i], problem.weights[i]), i),
    )
    remaining = problem.capacity
    bound = Fraction(0)
    for i in order:
        take = min(remaining, problem.weights[i])
        bound += Fraction(problem.values[i] * take, problem.weights[i])
        remaining -= take
        if remaining == 0:
            break
    return bound


def gap_percent(value: int, bound) -> float:
    """Optimality gap of a feasible value against an upper bound, in %."""
    bound_value = float(bound)
    if bound_value <= 0:
        return 0.0
    return max(0.0, (bound_value - value) / bound_value * 100.0)

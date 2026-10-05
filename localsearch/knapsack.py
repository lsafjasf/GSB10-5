"""0/1 knapsack model, initial-solution builders, and two neighborhoods."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .core import Move


@dataclass(frozen=True)
class KnapsackProblem:
    values: Tuple[int, ...]
    weights: Tuple[int, ...]
    capacity: int

    def __post_init__(self) -> None:
        if len(self.values) != len(self.weights):
            raise ValueError("values and weights must have equal length")
        if self.capacity < 0:
            raise ValueError("capacity must be non-negative")
        for value, weight in zip(self.values, self.weights):
            if value <= 0 or weight <= 0:
                raise ValueError("values and weights must be positive")

    @property
    def n(self) -> int:
        return len(self.values)


class KnapsackState:
    """Mutable binary solution with incrementally maintained totals."""

    __slots__ = ("problem", "x", "weight", "value")

    def __init__(self, problem: KnapsackProblem, x: Sequence[int]):
        if len(x) != problem.n:
            raise ValueError("solution length mismatch")
        self.problem = problem
        self.x: List[int] = [1 if bit else 0 for bit in x]
        self.weight = sum(w for w, bit in zip(problem.weights, self.x) if bit)
        self.value = sum(v for v, bit in zip(problem.values, self.x) if bit)
        if self.weight > problem.capacity:
            raise ValueError("initial solution is infeasible")

    def copy(self) -> "KnapsackState":
        return KnapsackState(self.problem, self.x)


class FlipNeighborhood:
    """1-flip neighborhood: toggle a single item.

    Nominal size is exactly n moves; feasible moves are removals of selected
    items plus additions of unselected items that still fit.
    """

    name = "flip"

    def propose(self, state: KnapsackState) -> Optional[Move]:
        problem = state.problem
        for i in range(problem.n):
            if state.x[i]:
                delta = -problem.values[i]
                new_weight = state.weight - problem.weights[i]
            else:
                delta = problem.values[i]
                new_weight = state.weight + problem.weights[i]
            if delta > 0 and new_weight <= problem.capacity:
                return (i, -1)
        return None

    def apply(self, state: KnapsackState, move: Move) -> None:
        i, _ = move
        if state.x[i]:
            state.x[i] = 0
            state.weight -= state.problem.weights[i]
            state.value -= state.problem.values[i]
        else:
            state.x[i] = 1
            state.weight += state.problem.weights[i]
            state.value += state.problem.values[i]

    def size(self, state: KnapsackState) -> int:
        return state.problem.n


class ExchangeNeighborhood:
    """Union of 1-flips and 1-out/1-in exchanges.

    Nominal size is n + k*(n-k) where k = sum(x). Same-direction 2-flips
    (add two / remove two) are omitted deliberately: if adding two items is
    feasible and improving, adding the first one alone is already an
    improving flip, so those moves can never be the first improving move
    and never change the reachable local optima.
    """

    name = "exchange"

    def propose(self, state: KnapsackState) -> Optional[Move]:
        problem = state.problem
        values = problem.values
        weights = problem.weights
        capacity = problem.capacity
        x = state.x
        for i in range(problem.n):
            if x[i]:
                delta = -values[i]
                new_weight = state.weight - weights[i]
            else:
                delta = values[i]
                new_weight = state.weight + weights[i]
            if delta > 0 and new_weight <= capacity:
                return (i, -1)
        for i in range(problem.n):
            if not x[i]:
                continue
            base_weight = state.weight - weights[i]
            base_value = -values[i]
            for j in range(problem.n):
                if x[j]:
                    continue
                delta = base_value + values[j]
                if delta > 0 and base_weight + weights[j] <= capacity:
                    return (i, j)
        return None

    def apply(self, state: KnapsackState, move: Move) -> None:
        i, j = move
        problem = state.problem
        if j < 0:
            if state.x[i]:
                state.x[i] = 0
                state.weight -= problem.weights[i]
                state.value -= problem.values[i]
            else:
                state.x[i] = 1
                state.weight += problem.weights[i]
                state.value += problem.values[i]
            return
        state.x[i] = 0
        state.weight -= problem.weights[i]
        state.value -= problem.values[i]
        state.x[j] = 1
        state.weight += problem.weights[j]
        state.value += problem.values[j]

    def size(self, state: KnapsackState) -> int:
        n = state.problem.n
        k = sum(state.x)
        return n + k * (n - k)


def greedy_solution(problem: KnapsackProblem) -> KnapsackState:
    """Density-ordered greedy fill; deterministic tie-break by index."""
    order = sorted(
        range(problem.n),
        key=lambda i: (-problem.values[i] / problem.weights[i], i),
    )
    x = [0] * problem.n
    weight = 0
    for i in order:
        if weight + problem.weights[i] <= problem.capacity:
            x[i] = 1
            weight += problem.weights[i]
    return KnapsackState(problem, x)


def random_feasible_solution(
    problem: KnapsackProblem, rng: random.Random
) -> KnapsackState:
    """Insert items in a uniform random order while they fit."""
    order = list(range(problem.n))
    rng.shuffle(order)
    x = [0] * problem.n
    weight = 0
    for i in order:
        if weight + problem.weights[i] <= problem.capacity:
            x[i] = 1
            weight += problem.weights[i]
    return KnapsackState(problem, x)

"""Generic steepest-descent local search with deterministic tie-breaking."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, Sequence, Tuple, TypeVar

State = TypeVar("State")
Move = Tuple[int, int]


class Neighborhood(Protocol[State]):
    name: str

    def propose(self, state: State) -> Optional[Move]:
        ...

    def apply(self, state: State, move: Move) -> None:
        ...

    def size(self, state: State) -> int:
        ...


@dataclass
class SearchResult:
    state: object
    value: int
    moves: int
    scans: int
    timed_out: bool
    elapsed: float


def local_search(
    state: State,
    neighborhood: Neighborhood[State],
    value_of: Callable[[State], int],
    deadline: Optional[float] = None,
) -> SearchResult:
    """Run first-improvement descent until a local optimum or the deadline.

    The neighborhood defines the complete, deterministic move order. Equal
    improving moves are never compared: the first one in that order is taken.
    """
    start = time.perf_counter()
    moves = 0
    scans = 0
    timed_out = False
    while True:
        if deadline is not None and time.perf_counter() >= deadline:
            timed_out = True
            break
        move = neighborhood.propose(state)
        scans += 1
        if move is None:
            break
        neighborhood.apply(state, move)
        moves += 1
    return SearchResult(state, value_of(state), moves, scans, timed_out, time.perf_counter() - start)


def random_restarts(
    initial_states: Sequence[State],
    neighborhood_factory: Callable[[], Neighborhood[State]],
    value_of: Callable[[State], int],
    deadline: Optional[float] = None,
) -> SearchResult:
    """Run one descent per initial state; ties keep the earlier run."""
    best: Optional[SearchResult] = None
    for state in initial_states:
        result = local_search(state, neighborhood_factory(), value_of, deadline)
        if best is None or result.value > best.value:
            best = result
    assert best is not None
    return best

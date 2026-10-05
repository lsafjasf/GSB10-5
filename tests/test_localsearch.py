"""Self-tests for the local-search library (standard library only)."""

import random
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from localsearch.core import local_search
from localsearch.exact import (
    dp_optimum,
    exhaustive_optimum,
    fractional_upper_bound,
)
from localsearch.knapsack import (
    ExchangeNeighborhood,
    FlipNeighborhood,
    KnapsackProblem,
    KnapsackState,
    greedy_solution,
    random_feasible_solution,
)


def random_problem(n, seed, ratio=0.5):
    rng = random.Random(seed)
    weights = tuple(rng.randint(1, 50) for _ in range(n))
    values = tuple(rng.randint(1, 50) for _ in range(n))
    capacity = max(1, int(ratio * sum(weights)))
    return KnapsackProblem(values, weights, capacity)


class ExactSolverTest(unittest.TestCase):
    def test_exhaustive_matches_dp(self):
        for seed in range(5):
            problem = random_problem(14, seed)
            exhaustive, certificate = exhaustive_optimum(problem)
            self.assertEqual(exhaustive, dp_optimum(problem))
            state = KnapsackState(problem, certificate)
            self.assertEqual(state.value, exhaustive)

    def test_fractional_bound_dominates_optimum(self):
        problem = random_problem(30, 7)
        self.assertGreaterEqual(
            float(fractional_upper_bound(problem)), dp_optimum(problem)
        )


class NeighborhoodTest(unittest.TestCase):
    def test_exchange_escapes_flip_local_optimum(self):
        # capacity fits one item only; item 1 is better but item 0 is taken
        problem = KnapsackProblem((10, 11), (6, 6), 10)
        start = KnapsackState(problem, [1, 0])
        flip = local_search(start.copy(), FlipNeighborhood(), lambda s: s.value)
        self.assertEqual(flip.value, 10)  # stuck
        exchange = local_search(start.copy(), ExchangeNeighborhood(), lambda s: s.value)
        self.assertEqual(exchange.value, 11)  # escaped to the optimum
        self.assertEqual(exchange.value, dp_optimum(problem))

    def test_neighborhood_sizes(self):
        problem = KnapsackProblem((5, 4, 3, 2), (2, 2, 2, 2), 4)
        state = KnapsackState(problem, [1, 1, 0, 0])
        self.assertEqual(FlipNeighborhood().size(state), 4)
        self.assertEqual(ExchangeNeighborhood().size(state), 4 + 2 * 2)

    def test_tie_break_is_deterministic(self):
        # two equally improving flips: the smaller index must win
        problem = KnapsackProblem((5, 5, 1), (1, 1, 1), 2)
        state = KnapsackState(problem, [0, 0, 1])
        self.assertEqual(FlipNeighborhood().propose(state), (0, -1))
        first = local_search(state.copy(), ExchangeNeighborhood(), lambda s: s.value)
        second = local_search(state.copy(), ExchangeNeighborhood(), lambda s: s.value)
        self.assertEqual(first.state.x, second.state.x)
        self.assertEqual(first.value, second.value)

    def test_local_optimum_certificate(self):
        problem = random_problem(25, 11)
        for hood in (FlipNeighborhood(), ExchangeNeighborhood()):
            state = greedy_solution(problem)
            result = local_search(state, hood, lambda s: s.value)
            self.assertIsNone(hood.propose(result.state))
            self.assertLessEqual(result.value, dp_optimum(problem))

    def test_feasibility_and_incremental_totals(self):
        problem = random_problem(40, 13)
        rng = random.Random(99)
        state = random_feasible_solution(problem, rng)
        result = local_search(state, ExchangeNeighborhood(), lambda s: s.value)
        final = result.state
        self.assertLessEqual(final.weight, problem.capacity)
        self.assertEqual(
            final.value,
            sum(v for v, bit in zip(problem.values, final.x) if bit),
        )
        self.assertEqual(
            final.weight,
            sum(w for w, bit in zip(problem.weights, final.x) if bit),
        )

    def test_deadline_stops_promptly(self):
        problem = random_problem(300, 5)
        state = greedy_solution(problem)
        result = local_search(
            state, ExchangeNeighborhood(), lambda s: s.value,
            deadline=time.perf_counter(),
        )
        self.assertTrue(result.timed_out)
        self.assertEqual(result.moves, 0)

    def test_reproducibility_same_seed(self):
        problem = random_problem(50, 21)
        values = []
        for _ in range(2):
            rng = random.Random(1234)
            starts = [random_feasible_solution(problem, rng) for _ in range(5)]
            run_values = [
                local_search(s, ExchangeNeighborhood(), lambda st: st.value).value
                for s in starts
            ]
            values.append(run_values)
        self.assertEqual(values[0], values[1])


if __name__ == "__main__":
    unittest.main()

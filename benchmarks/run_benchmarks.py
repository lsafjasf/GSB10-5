"""Benchmark local-search neighborhoods on knapsack instances.

Scenarios cover: small exhaustive-checkable, medium exact-DP, strongly
constrained, tight time budget, and n=1000 large scale. Results are
written to JSON and printed as a Markdown table.

Usage: python3 benchmarks/run_benchmarks.py [--runs 20] [--large-runs 10]
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from localsearch.core import local_search
from localsearch.exact import (
    dp_optimum,
    exhaustive_optimum,
    fractional_upper_bound,
    gap_percent,
)
from localsearch.knapsack import (
    ExchangeNeighborhood,
    FlipNeighborhood,
    KnapsackProblem,
    greedy_solution,
    random_feasible_solution,
)

NEIGHBORHOODS = {
    "flip": FlipNeighborhood,
    "exchange": ExchangeNeighborhood,
}


def make_instance(kind: str, n: int, ratio: float, seed: int) -> KnapsackProblem:
    rng = random.Random(seed)
    weights = [rng.randint(1, 100) for _ in range(n)]
    if kind == "uncorrelated":
        values = [rng.randint(1, 100) for _ in range(n)]
    elif kind == "weakly":
        values = [max(1, w + rng.randint(-10, 10)) for w in weights]
    elif kind == "strongly":
        values = [w + 10 for w in weights]
    else:
        raise ValueError(kind)
    capacity = max(1, int(ratio * sum(weights)))
    return KnapsackProblem(tuple(values), tuple(weights), capacity)


def run_scenario(name, problem, runs, seed, budget=None, exact=None, bound=None):
    """Run both neighborhoods on identical random starts; collect stats."""
    # Every neighborhood gets identical starts: one deterministic greedy
    # restart (first, ties keep it) plus the requested random restarts.
    initials = [
        greedy_solution(problem)
    ] + [
        random_feasible_solution(problem, random.Random(seed * 1_000_003 + r))
        for r in range(runs)
    ]
    greedy = initials[0]
    scenario = {
        "name": name,
        "n": problem.n,
        "capacity": problem.capacity,
        "runs": len(initials),
        "budget_per_run_s": budget,
        "exact_optimum": exact,
        "lp_upper_bound": float(bound) if bound is not None else None,
        "greedy_value": greedy.value,
        "neighborhood_sizes": {
            "flip": FlipNeighborhood().size(greedy),
            "exchange": ExchangeNeighborhood().size(greedy),
        },
        "results": {},
    }
    reference = exact if exact is not None else bound
    for hood_name, hood_cls in NEIGHBORHOODS.items():
        values, elapsed, moves, timeouts = [], [], [], 0
        for state in initials:
            deadline = None
            if budget is not None:
                deadline = time.perf_counter() + budget
            result = local_search(
                state.copy(), hood_cls(), lambda s: s.value, deadline
            )
            values.append(result.value)
            elapsed.append(result.elapsed)
            moves.append(result.moves)
            timeouts += int(result.timed_out)
        scenario["results"][hood_name] = {
            "values": values,
            "best": max(values),
            "worst": min(values),
            "median": statistics.median(values),
            "mean": round(statistics.fmean(values), 2),
            "gap_best_pct": round(gap_percent(max(values), reference), 4),
            "gap_median_pct": round(gap_percent(statistics.median(values), reference), 4),
            "total_time_s": round(sum(elapsed), 4),
            "median_time_s": round(statistics.median(elapsed), 6),
            "median_moves": statistics.median(moves),
            "timeouts": timeouts,
        }
    return scenario


def print_table(scenarios):
    header = (
        "| 场景 | n | 邻域 | 最好 | 最差 | 中位数 | 差距best% | 差距median% "
        "| 总耗时s | 单次中位s | 超时 |"
    )
    sep = "|" + "---|" * 11
    print(header)
    print(sep)
    for sc in scenarios:
        for hood, res in sc["results"].items():
            print(
                f"| {sc['name']} | {sc['n']} | {hood} | {res['best']} | "
                f"{res['worst']} | {res['median']} | {res['gap_best_pct']} | "
                f"{res['gap_median_pct']} | {res['total_time_s']} | "
                f"{res['median_time_s']} | {res['timeouts']} |"
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--large-runs", type=int, default=10)
    parser.add_argument("--budget", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=20261005)
    parser.add_argument("--out", default="benchmarks/results.json")
    args = parser.parse_args()

    s = args.seed
    scenarios = []

    small = make_instance("uncorrelated", 18, 0.5, s + 1)
    exact_small, _ = exhaustive_optimum(small)
    scenarios.append(
        run_scenario("small_exhaustive", small, args.runs, s + 1, exact=exact_small)
    )

    medium = make_instance("weakly", 60, 0.5, s + 2)
    exact_medium = dp_optimum(medium)
    scenarios.append(
        run_scenario("medium_exact_dp", medium, args.runs, s + 2, exact=exact_medium)
    )

    strong = make_instance("strongly", 100, 0.05, s + 3)
    exact_strong = dp_optimum(strong)
    scenarios.append(
        run_scenario("strong_constraint", strong, args.runs, s + 3, exact=exact_strong)
    )

    tight = make_instance("weakly", 500, 0.5, s + 4)
    exact_tight = dp_optimum(tight)
    scenarios.append(
        run_scenario(
            "tight_budget_50ms", tight, args.runs, s + 4,
            budget=args.budget, exact=exact_tight,
        )
    )

    large = make_instance("uncorrelated", 1000, 0.5, s + 5)
    bound_large = fractional_upper_bound(large)
    scenarios.append(
        run_scenario(
            "large_n1000", large, args.large_runs, s + 5, bound=bound_large
        )
    )

    payload = {"seed": s, "scenarios": scenarios}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print_table(scenarios)
    print(f"\nJSON written to {out_path}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Self-tests: point-by-point cross-check (dui-pai) of ALT vs naive Dijkstra.

Covers: random directed graphs (all-pairs check), disconnected graphs,
zero-weight edges, self queries, chain graphs, and the memory-guard
policies (reduce / fallback / raise).
"""
import random
import sys

from spp import (ALTIndex, Graph, MemoryBudgetExceeded, dijkstra,
                 estimate_total_bytes)

FAILURES = 0


def check(cond, msg):
    global FAILURES
    if not cond:
        FAILURES += 1
        print("FAIL:", msg)


def random_graph(rng, n, m, max_w=100, allow_zero=True):
    g = Graph(n)
    # random spanning arborescence so most pairs are reachable
    for v in range(1, n):
        u = rng.randrange(v)
        w = rng.randint(0 if allow_zero else 1, max_w)
        g.add_edge(u, v, w)
    for _ in range(m):
        g.add_edge(rng.randrange(n), rng.randrange(n),
                   rng.randint(0 if allow_zero else 1, max_w))
    return g.finalize()


def crosscheck_all_pairs(g, index, tag):
    n = g.n
    for s in range(n):
        truth = dijkstra(g, s)
        for t in range(n):
            got = index.query(s, t)
            check(got == truth[t],
                  "%s: dist(%d,%d) alt=%r naive=%r" % (tag, s, t, got, truth[t]))


def test_random_graphs():
    rng = random.Random(20261005)
    pairs_checked = 0
    for trial in range(40):
        n = rng.randint(2, 40)
        m = rng.randint(0, 4 * n)
        g = random_graph(rng, n, m)
        idx = ALTIndex(g, num_landmarks=rng.randint(1, 8), seed=trial)
        crosscheck_all_pairs(g, idx, "random#%d" % trial)
        pairs_checked += n * n
    print("random graphs: 40 graphs, %d pairs checked, all exact" % pairs_checked)


def test_disconnected():
    g = Graph(6)
    g.add_edge(0, 1, 5)
    g.add_edge(2, 3, 7)
    g.finalize()
    idx = ALTIndex(g, num_landmarks=4, seed=1)
    inf = float("inf")
    check(idx.query(0, 1) == 5.0, "disconnected: reachable pair")
    check(idx.query(1, 0) == inf, "disconnected: no back edge")
    check(idx.query(0, 2) == inf, "disconnected: across components")
    check(idx.query(3, 3) == 0.0, "disconnected: self query")
    print("disconnected graph: ok")


def test_zero_weight_and_selfloop():
    g = Graph(4)
    g.add_edge(0, 1, 0)
    g.add_edge(1, 2, 0)
    g.add_edge(0, 2, 5)
    g.add_edge(2, 2, 3)  # self loop must be ignored by relaxation
    g.add_edge(2, 3, 1)
    g.finalize()
    idx = ALTIndex(g, num_landmarks=3, seed=2)
    check(idx.query(0, 3) == 1.0, "zero-weight chain then 1")
    check(idx.query(0, 2) == 0.0, "zero-weight path beats weight-5 edge")
    print("zero-weight edges / self loop: ok")


def test_chain_graph():
    n = 2000
    g = Graph(n)
    for i in range(n - 1):
        g.add_edge(i, i + 1, 1)
        g.add_edge(i + 1, i, 2)
    g.finalize()
    idx = ALTIndex(g, num_landmarks=8, seed=3)
    rng = random.Random(7)
    for _ in range(200):
        s, t = rng.randrange(n), rng.randrange(n)
        truth = dijkstra(g, s, target=t)
        check(idx.query(s, t) == truth, "chain dist(%d,%d)" % (s, t))
    print("chain graph (n=2000): 200 random pairs exact")


def test_memory_guard():
    rng = random.Random(99)
    g = random_graph(rng, 300, 900)
    full = estimate_total_bytes(g, 8)

    # raise policy must refuse loudly instead of being OOM-killed
    try:
        ALTIndex(g, num_landmarks=8, mem_budget=full - 1, on_exceed="raise")
        check(False, "raise policy should have raised")
    except MemoryBudgetExceeded:
        pass

    # reduce policy shrinks L and stays exact
    idx = ALTIndex(g, num_landmarks=8, mem_budget=estimate_total_bytes(g, 3),
                   on_exceed="reduce")
    check(idx.num_landmarks <= 3, "reduce policy caps landmarks")
    check(idx.num_landmarks > 0, "reduce keeps at least one landmark here")
    crosscheck_all_pairs(g, idx, "reduce")

    # tiny budget -> degrade to naive, still exact, process alive
    idx = ALTIndex(g, num_landmarks=8, mem_budget=1, on_exceed="reduce")
    check(idx.num_landmarks == 0 and idx.degraded, "tiny budget degrades")
    crosscheck_all_pairs(g, idx, "degraded")

    idx = ALTIndex(g, num_landmarks=8, mem_budget=1, on_exceed="fallback")
    check(idx.num_landmarks == 0 and idx.degraded, "fallback degrades")
    crosscheck_all_pairs(g, idx, "fallback")
    print("memory guard (raise/reduce/fallback): ok, all exact")


def main():
    test_random_graphs()
    test_disconnected()
    test_zero_weight_and_selfloop()
    test_chain_graph()
    test_memory_guard()
    if FAILURES:
        print("\n%d FAILURE(S)" % FAILURES)
        return 1
    print("\nALL SELF-TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

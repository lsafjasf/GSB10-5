"""sppre 自测：与朴素 Dijkstra 逐点对拍 + 边界情形 + 成本模型。

运行：python3 test_sppre.py -v
"""
import math
import random
import unittest

from sppre import (
    ALTPreprocessor,
    Graph,
    NaivePreprocessor,
    break_even_queries,
    build_preprocessor,
    dijkstra_pair,
    total_cost,
    INF,
)


def random_graph(n, m, seed, max_w=100):
    g = Graph(n)
    rng = random.Random(seed)
    # 先铺一条随机哈密顿路径保证整体弱连通，便于对拍
    order = list(range(n))
    rng.shuffle(order)
    for i in range(n - 1):
        g.add_edge(order[i], order[i + 1], rng.randint(1, max_w))
    for _ in range(m - (n - 1)):
        g.add_edge(rng.randrange(n), rng.randrange(n), rng.randint(1, max_w))
    return g


def chain_graph(n, seed, extra=0):
    """接近链状：双向链 + 少量随机弦。"""
    g = Graph(n)
    rng = random.Random(seed)
    for i in range(n - 1):
        w = rng.randint(1, 10)
        g.add_edge(i, i + 1, w)
        g.add_edge(i + 1, i, w)
    for _ in range(extra):
        g.add_edge(rng.randrange(n), rng.randrange(n), rng.randint(1, 50))
    return g


class CrossCheckTest(unittest.TestCase):
    """预处理结果必须与朴素 Dijkstra 完全一致（整数权，浮点精确相等）。"""

    def test_alt_matches_naive_on_random_graphs(self):
        total_pairs = 0
        for trial in range(30):
            n = 40 + trial * 3
            g = random_graph(n, n * 4, seed=trial)
            alt = ALTPreprocessor(g, k=8, seed=trial)
            rng = random.Random(1000 + trial)
            for _ in range(25):
                s, t = rng.randrange(n), rng.randrange(n)
                expected = dijkstra_pair(g, s, t)
                got = alt.query(s, t)
                total_pairs += 1
                self.assertEqual(expected, got,
                                 "mismatch at (%d,%d) trial %d" % (s, t, trial))
        print("\n[对拍] ALT vs 朴素: %d 对查询全部一致" % total_pairs)

    def test_apsp_matches_naive_all_pairs(self):
        g = random_graph(60, 240, seed=7)
        apsp = build_preprocessor(g, mode="apsp")
        mismatches = 0
        for s in range(g.n):
            for t in range(g.n):
                if apsp.query(s, t) != dijkstra_pair(g, s, t):
                    mismatches += 1
        self.assertEqual(0, mismatches)
        print("\n[对拍] APSP vs 朴素: %d 对全部一致" % (g.n * g.n))

    def test_chain_like_graph(self):
        g = chain_graph(2000, seed=3, extra=40)
        alt = ALTPreprocessor(g, k=8, seed=3)
        rng = random.Random(99)
        for _ in range(60):
            s, t = rng.randrange(g.n), rng.randrange(g.n)
            self.assertEqual(dijkstra_pair(g, s, t), alt.query(s, t))
        print("\n[对拍] 链状图 ALT vs 朴素: 60 对全部一致")

    def test_unreachable_and_self(self):
        g = Graph(4)
        g.add_edge(0, 1, 5)
        alt = ALTPreprocessor(g, k=2, seed=1)
        self.assertEqual(INF, alt.query(1, 0))
        self.assertEqual(INF, dijkstra_pair(g, 1, 0))
        self.assertEqual(0.0, alt.query(2, 2))

    def test_negative_weight_rejected(self):
        g = Graph(2)
        with self.assertRaises(ValueError):
            g.add_edge(0, 1, -1)


class MemoryLimitTest(unittest.TestCase):
    """内存超限必须明确处理：降级或拒绝，进程不能被杀。"""

    def test_alt_degrades_k_when_budget_small(self):
        g = random_graph(500, 2000, seed=11)
        # 500 点：每个路标 2*500*8 = 8000 字节；预算 20000 -> k=2
        alt = build_preprocessor(g, mode="alt", k=16,
                                 memory_limit_bytes=20000)
        self.assertEqual(2, alt.k)
        rng = random.Random(5)
        for _ in range(20):
            s, t = rng.randrange(g.n), rng.randrange(g.n)
            self.assertEqual(dijkstra_pair(g, s, t), alt.query(s, t))

    def test_degrades_to_naive_when_budget_tiny(self):
        g = random_graph(500, 2000, seed=12)
        pre = build_preprocessor(g, mode="alt", k=16, memory_limit_bytes=100)
        self.assertIsInstance(pre, NaivePreprocessor)
        rng = random.Random(6)
        for _ in range(20):
            s, t = rng.randrange(g.n), rng.randrange(g.n)
            self.assertEqual(dijkstra_pair(g, s, t), pre.query(s, t))
        print("\n[内存] 预算 100B -> 降级为朴素，查询仍正确")

    def test_apsp_degrades_to_alt_when_too_big(self):
        g = random_graph(300, 1200, seed=13)
        # APSP 需要 300*300*8 = 720KB；预算 100KB -> 降级 ALT
        pre = build_preprocessor(g, mode="apsp", k=4,
                                 memory_limit_bytes=100000)
        self.assertEqual("alt", pre.mode)
        rng = random.Random(8)
        for _ in range(20):
            s, t = rng.randrange(g.n), rng.randrange(g.n)
            self.assertEqual(dijkstra_pair(g, s, t), pre.query(s, t))

    def test_raise_mode_refuses_cleanly(self):
        g = random_graph(300, 1200, seed=14)
        with self.assertRaises(MemoryError):
            build_preprocessor(g, mode="apsp", memory_limit_bytes=1000,
                               on_overflow="raise")
        with self.assertRaises(MemoryError):
            build_preprocessor(g, mode="alt", k=4, memory_limit_bytes=10,
                               on_overflow="raise")
        print("\n[内存] on_overflow=raise -> 干净地抛 MemoryError，进程存活")


class CostModelTest(unittest.TestCase):
    """查询极少/极多两种情形下，总成本比较必须符合临界点公式。"""

    def test_break_even_threshold(self):
        P, D, A = 100.0, 2.0, 0.5  # 预处理 100，朴素单次 2，加速单次 0.5
        q_star = break_even_queries(P, D, A)
        self.assertAlmostEqual(100.0 / 1.5, q_star)
        # 临界点以下：朴素总成本更低（不划算）
        q_low = int(q_star) - 1
        self.assertLess(total_cost(q_low, 0.0, D), total_cost(q_low, P, A))
        # 临界点以上：预处理总成本更低（划算）
        q_high = int(q_star) + 1
        self.assertLess(total_cost(q_high, P, A), total_cost(q_high, 0.0, D))

    def test_no_speedup_means_never_worth_it(self):
        self.assertEqual(math.inf, break_even_queries(10.0, 1.0, 1.0))
        self.assertEqual(math.inf, break_even_queries(10.0, 1.0, 2.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)

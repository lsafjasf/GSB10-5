"""自测：策略正确性 + 边界用例。运行：python3 -m unittest cachebench.test_cache -v"""

import unittest

from .policies import LRU, LFU, FIFO, RandomPolicy
from .workloads import gen_sequential, gen_zipf, gen_uniform

ALL = [LRU, LFU, FIFO, RandomPolicy]


class CommonBehavior(unittest.TestCase):
    def test_capacity_zero_never_caches(self):
        for cls in ALL:
            with self.subTest(policy=cls.name):
                p = cls(0)
                for k in range(1000):
                    self.assertFalse(p.access(k, 1.0))
                self.assertEqual(len(p), 0)
                self.assertEqual(p.hits, 0)
                self.assertEqual(p.misses, 1000)

    def test_capacity_one(self):
        for cls in ALL:
            with self.subTest(policy=cls.name):
                p = cls(1)
                self.assertFalse(p.access("a", 1.0))
                self.assertTrue(p.access("a", 1.0))
                self.assertFalse(p.access("b", 1.0))   # 淘汰 a
                self.assertFalse(p.access("a", 1.0))   # a 已不在
                self.assertEqual(len(p), 1)

    def test_negative_capacity_rejected(self):
        for cls in ALL:
            with self.assertRaises(ValueError):
                cls(-1)

    def test_size_never_exceeds_capacity(self):
        for cls in ALL:
            p = cls(7)
            for i, k in enumerate(gen_zipf(10_000, 500)):
                p.access(k, 1.0)
                self.assertLessEqual(len(p), 7, cls.name)

    def test_miss_cost_accumulates_only_on_miss(self):
        for cls in ALL:
            with self.subTest(policy=cls.name):
                p = cls(2)
                p.access("x", 10.0)   # miss +10
                p.access("x", 10.0)   # hit
                p.access("y", 3.0)    # miss +3
                p.access("z", 7.0)    # miss +7（触发淘汰）
                p.access("y", 3.0)    # hit（FIFO 可能被淘汰，见下不假设）
                self.assertGreaterEqual(p.miss_cost, 20.0)


class LRUCorrectness(unittest.TestCase):
    def test_lru_eviction_order(self):
        p = LRU(2)
        p.access("a", 1); p.access("b", 1); p.access("a", 1)
        p.access("c", 1)  # 淘汰 b
        self.assertTrue(p.access("a", 1))
        self.assertFalse(p.access("b", 1))

    def test_lru_sequential_scan_thrash(self):
        cap = 100
        p = LRU(cap)
        for k in gen_sequential(2 * cap + 1, cap + 1):
            p.access(k, 1.0)
        # 扫描长度 > 容量时，整个缓存每轮都被污染，命中率应为 0
        self.assertEqual(p.hits, 0)


class FIFOCorrectness(unittest.TestCase):
    def test_fifo_eviction_order(self):
        p = FIFO(2)
        p.access("a", 1); p.access("b", 1); p.access("a", 1)  # a 是热键但入队最早
        p.access("c", 1)  # 仍淘汰 a
        self.assertTrue(p.access("b", 1))   # b 仍在
        self.assertFalse(p.access("a", 1))  # a 已被淘汰

    def test_fifo_scan_thrash(self):
        cap = 50
        p = FIFO(cap)
        for k in gen_sequential(2 * cap + 1, cap + 1):
            p.access(k, 1.0)
        self.assertEqual(p.hits, 0)


class LFUCorrectness(unittest.TestCase):
    def test_lfu_evicts_lowest_frequency(self):
        p = LFU(2)
        for _ in range(3):
            p.access("a", 1.0)
        for _ in range(2):
            p.access("b", 1.0)
        p.access("c", 1.0)   # freq(c)=1，淘汰 c 自身？不会——容量满才淘汰
        # 上面插入 c 时已满，淘汰的是同频桶中... b=2,a=3，故淘汰 b
        self.assertTrue(p.access("a", 1))
        self.assertFalse(p.access("b", 1))

    def test_lfu_same_frequency_tie_is_lru(self):
        p = LFU(2)
        p.access("a", 1); p.access("b", 1)  # 同频，a 更早
        p.access("c", 1)                    # 淘汰 a
        self.assertTrue(p.access("b", 1))   # b 仍在
        self.assertFalse(p.access("a", 1))  # a 已被淘汰

    def test_lfu_minfreq_resets_after_bucket_empties(self):
        p = LFU(2)
        p.access("a", 1); p.access("a", 1); p.access("a", 1)  # freq 3
        p.access("b", 1); p.access("b", 1)                    # freq 2
        p.access("c", 1)   # 淘汰 b（freq 2）
        p.access("d", 1)   # 新键 freq 1 应能正常插入（min_freq 重置正确）
        self.assertTrue(p.access("a", 1))
        self.assertTrue(p.access("d", 1))


class RandomCorrectness(unittest.TestCase):
    def test_swap_remove_keeps_all_keys_findable(self):
        p = RandomPolicy(50, seed=7)
        keys = set()
        for i, k in enumerate(gen_uniform(5000, 200, seed=9)):
            p.access(k, 1.0)
            keys = set(p._keys)
            self.assertEqual(len(keys), len(p._index))
            for held in p._keys:
                self.assertIn(held, p._index)
                self.assertEqual(p._keys[p._index[held]], held)

    def test_random_uniform_hits_capacity_fraction(self):
        # 均匀随机访问下，随机淘汰稳态命中率 ≈ capacity / working_set
        cap, ws = 100, 1000
        p = RandomPolicy(cap, seed=1)
        for k in gen_uniform(200_000, ws, seed=1):
            p.access(k, 1.0)
        rate = p.hits / (p.hits + p.misses)
        self.assertGreater(rate, 0.07)
        self.assertLess(rate, 0.13)


class CrossPolicyBoundaries(unittest.TestCase):
    def test_capacity_covers_working_set_only_cold_misses(self):
        n, ws = 20_000, 500
        for cls in ALL:
            with self.subTest(policy=cls.name):
                p = cls(ws + 10)
                for k in gen_zipf(n, ws, seed=5):
                    p.access(k, 1.0)
                expected = 1.0 - ws / n      # 只有冷启动的工作集个缺失
                self.assertAlmostEqual(p.hits / n, expected, places=2)

    def test_all_policies_consume_identical_sequence(self):
        """同种子生成两次序列必须一致（公平性保证）。"""
        a = list(gen_zipf(100, 50, seed=11))
        b = list(gen_zipf(100, 50, seed=11))
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()

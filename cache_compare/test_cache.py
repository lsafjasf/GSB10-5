"""边界用例与策略行为自测（标准库 unittest）。

运行：python3 -m unittest test_cache -v
"""

import unittest

from cache_policies import ALL_POLICIES, FIFOCache, LFUCache, LRUCache, RandomCache


class TestEdgeCases(unittest.TestCase):
    """对所有策略统一成立的边界行为。"""

    def test_zero_capacity_never_hits(self):
        for cls in ALL_POLICIES:
            c = cls(0)
            self.assertFalse(c.access("a"))
            self.assertFalse(c.access("a"))  # 容量 0：重复访问也不缓存

    def test_capacity_one(self):
        for cls in ALL_POLICIES:
            c = cls(1)
            self.assertFalse(c.access("a"))
            self.assertTrue(c.access("a"))   # 同键第二次命中
            self.assertFalse(c.access("b"))  # 挤掉 a
            self.assertFalse(c.access("a"))  # a 已被淘汰

    def test_capacity_never_exceeded(self):
        for cls in ALL_POLICIES:
            c = cls(3)
            for i in range(100):
                c.access(i)
            self.assertLessEqual(len(c), 3)

    def test_negative_capacity_treated_as_zero(self):
        for cls in ALL_POLICIES:
            c = cls(-5)
            self.assertFalse(c.access("x"))

    def test_repeated_same_key_hits(self):
        for cls in ALL_POLICIES:
            c = cls(2)
            c.access("k")
            for _ in range(10):
                self.assertTrue(c.access("k"))

    def test_bookkeeping_bytes_nonnegative_and_scales(self):
        for cls in ALL_POLICIES:
            small, large = cls(10), cls(1000)
            for i in range(1000):
                small.access(i)
                large.access(i)
            self.assertGreaterEqual(small.bookkeeping_bytes(), 0)
            self.assertGreaterEqual(large.bookkeeping_bytes(),
                                    small.bookkeeping_bytes())


class TestPolicySemantics(unittest.TestCase):
    """各策略特有的淘汰语义。"""

    def test_lru_evicts_least_recently_used(self):
        c = LRUCache(2)
        c.access(1); c.access(2)
        c.access(1)          # 1 变最新
        c.access(3)          # 应淘汰 2
        self.assertTrue(c.access(1))
        self.assertFalse(c.access(2))

    def test_fifo_ignores_hits(self):
        c = FIFOCache(2)
        c.access(1); c.access(2)
        self.assertTrue(c.access(1))  # 命中不改变顺序
        c.access(3)                   # 仍淘汰最早的 1
        self.assertTrue(c.access(2))  # 2 仍在
        self.assertFalse(c.access(1))

    def test_lfu_evicts_least_frequent(self):
        c = LFUCache(2)
        c.access(1); c.access(1)      # 1 的频次=2
        c.access(2)                   # 2 的频次=1
        c.access(3)                   # 应淘汰频次最低的 2
        self.assertTrue(c.access(1))
        self.assertFalse(c.access(2))

    def test_lfu_scan_resistance(self):
        # 热点 + 一轮扫描：LFU 应保住热点，LRU 会被污染
        hot = list(range(10))
        lfu, lru = LFUCache(15), LRUCache(15)
        for _ in range(5):
            for k in hot:
                lfu.access(k); lru.access(k)
        for k in range(10, 60):       # 扫描 50 个冷键
            lfu.access(k); lru.access(k)
        lfu_hits = sum(lfu.access(k) for k in hot)
        lru_hits = sum(lru.access(k) for k in hot)
        self.assertEqual(lfu_hits, 10)
        self.assertLess(lru_hits, lfu_hits)

    def test_random_respects_capacity_and_caches(self):
        c = RandomCache(5, seed=1)
        for i in range(50):
            c.access(i)
        self.assertLessEqual(len(c._set), 5)
        self.assertEqual(len(c._set), len(c._keys))  # 两结构一致



if __name__ == "__main__":
    unittest.main()

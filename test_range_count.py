"""range_count 自测：边界规则、重复点、空/退化矩形、极端分布、随机对拍。

运行：python3 test_range_count.py
"""

import random
import unittest

from range_count import RangeCounter, count_brute


class TestBoundaryRule(unittest.TestCase):
    """规则：四边闭区间，恰好落在边上/角上的点全部计入。"""

    def setUp(self):
        # 矩形 [0,10]x[0,10] 的四边、四角、内部、外部点
        self.pts = [
            (0, 5), (10, 5), (5, 0), (5, 10),      # 四边中点（在边界上）
            (0, 0), (0, 10), (10, 0), (10, 10),    # 四个角（在边界上）
            (5, 5),                                 # 内部
            (-1, 5), (11, 5), (5, -1), (5, 11),    # 外部（贴边外侧）
        ]
        self.rc = RangeCounter(self.pts)

    def test_edges_and_corners_counted(self):
        self.assertEqual(self.rc.count(0, 0, 10, 10), 9)
        self.assertEqual(count_brute(self.pts, 0, 0, 10, 10), 9)

    def test_each_edge(self):
        self.assertEqual(self.rc.count(0, 0, 0, 10), 3)    # 左边: (0,0)(0,5)(0,10)
        self.assertEqual(self.rc.count(10, 0, 10, 10), 3)  # 右边
        self.assertEqual(self.rc.count(0, 0, 10, 0), 3)    # 下边
        self.assertEqual(self.rc.count(0, 10, 10, 10), 3)  # 上边

    def test_outside_points_excluded(self):
        self.assertEqual(self.rc.count(0, 0, 9, 9), 4)   # (0,0)(0,5)(5,0)(5,5)
        self.assertEqual(self.rc.count(-5, -5, -1, 8), 1)  # 仅 (-1,5)


class TestDuplicates(unittest.TestCase):
    """重复点：每个副本都计入，不去重。"""

    def test_duplicates_all_counted(self):
        pts = [(3, 3)] * 5 + [(1, 1), (1, 1), (4, 4)]
        rc = RangeCounter(pts)
        self.assertEqual(rc.count(0, 0, 10, 10), 8)
        self.assertEqual(rc.count(3, 3, 3, 3), 5)   # 退化到单点，5 个副本
        self.assertEqual(rc.count(1, 1, 1, 1), 2)
        self.assertEqual(rc.count(2, 2, 3, 3), 5)
        for q in [(0, 0, 10, 10), (3, 3, 3, 3), (1, 1, 1, 1), (2, 2, 3, 3)]:
            self.assertEqual(rc.count(*q), count_brute(pts, *q))


class TestEmptyAndDegenerate(unittest.TestCase):
    """空矩形恒为 0；退化矩形（宽/高为 0）按闭区间规则计数。"""

    def setUp(self):
        self.pts = [(1, 1), (2, 2), (3, 3), (2, 1), (1, 2)]
        self.rc = RangeCounter(self.pts)

    def test_empty_rectangle_is_zero(self):
        self.assertEqual(self.rc.count(5, 0, 1, 10), 0)   # x1 > x2
        self.assertEqual(self.rc.count(0, 5, 10, 1), 0)   # y1 > y2
        self.assertEqual(self.rc.count(5, 5, 1, 1), 0)    # 两者都反
        self.assertEqual(self.rc.count(100, 100, 200, 200), 0)  # 无点区域

    def test_degenerate_line_and_point(self):
        self.assertEqual(self.rc.count(1, 1, 3, 1), 2)    # 水平线段 y=1: (1,1)(2,1)
        self.assertEqual(self.rc.count(1, 1, 1, 3), 2)    # 垂直线段 x=1: (1,1)(1,2)
        self.assertEqual(self.rc.count(2, 2, 2, 2), 1)    # 单点矩形: (2,2)
        self.assertEqual(self.rc.count(7, 7, 7, 7), 0)    # 单点矩形无点


class TestExtremeDistributions(unittest.TestCase):
    """全在界内、全在界外、大量边界点。"""

    def test_all_inside(self):
        pts = [(i, i) for i in range(1000)]
        rc = RangeCounter(pts)
        self.assertEqual(rc.count(-10, -10, 2000, 2000), 1000)

    def test_all_outside(self):
        pts = [(i, i) for i in range(1000)]
        rc = RangeCounter(pts)
        self.assertEqual(rc.count(2000, 2000, 3000, 3000), 0)
        self.assertEqual(rc.count(-1000, -1000, -1, -1), 0)

    def test_many_boundary_points(self):
        # 20000 个点全部落在矩形 [0,100]x[0,100] 的四条边上
        pts = []
        for i in range(5000):
            pts += [(i % 101, 0), (i % 101, 100), (0, i % 101), (100, i % 101)]
        rc = RangeCounter(pts)
        self.assertEqual(rc.count(0, 0, 100, 100), 20000)          # 全部计入
        self.assertEqual(rc.count(1, 1, 99, 99), 0)                # 开区间内部无点
        # 下边一行：横边 5000 + 两条竖边在 y=0 处各 50 个副本（角点不去重）
        self.assertEqual(rc.count(0, 0, 100, 0), 5100)
        # 与暴力一致
        for q in [(0, 0, 100, 100), (1, 1, 99, 99), (0, 0, 100, 0), (50, 0, 50, 100)]:
            self.assertEqual(rc.count(*q), count_brute(pts, *q))


class TestRandomCrosscheck(unittest.TestCase):
    """随机点集 + 随机查询（含空/退化/边界贴边查询）与暴力扫描对拍。"""

    def test_small_random(self):
        rng = random.Random(20261005)
        total_q = 0
        for _ in range(300):
            n = rng.randint(0, 100)
            pts = [(rng.randint(-8, 8), rng.randint(-8, 8)) for _ in range(n)]
            rc = RangeCounter(pts)
            for _ in range(60):
                x1, x2 = sorted((rng.randint(-10, 10), rng.randint(-10, 10)))
                y1, y2 = sorted((rng.randint(-10, 10), rng.randint(-10, 10)))
                r = rng.random()
                if r < 0.08:
                    x1, x2 = x2 + rng.randint(1, 3), x2      # 空矩形
                elif r < 0.16:
                    x2 = x1                                   # 退化（宽 0）
                elif r < 0.24:
                    y2 = y1                                   # 退化（高 0）
                self.assertEqual(rc.count(x1, y1, x2, y2),
                                 count_brute(pts, x1, y1, x2, y2))
                total_q += 1
        print(f"\nsmall random crosscheck: {total_q} queries all match")

    def test_medium_random(self):
        rng = random.Random(777)
        pts = [(rng.randint(-10**6, 10**6), rng.randint(-10**6, 10**6))
               for _ in range(20000)]
        rc = RangeCounter(pts)
        for i in range(2000):
            x1, x2 = sorted((rng.randint(-10**6, 10**6), rng.randint(-10**6, 10**6)))
            y1, y2 = sorted((rng.randint(-10**6, 10**6), rng.randint(-10**6, 10**6)))
            if i % 10 == 0:
                x2 = x1  # 周期性插入退化查询
            self.assertEqual(rc.count(x1, y1, x2, y2),
                             count_brute(pts, x1, y1, x2, y2))
        print("medium random crosscheck: 20000 pts x 2000 queries all match")


if __name__ == "__main__":
    unittest.main(verbosity=2)

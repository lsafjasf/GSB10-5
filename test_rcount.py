# -*- coding: utf-8 -*-
"""RangeCounter 单元测试：边界规则、重复点、退化/空矩形、随机对拍。"""

import random
import unittest

from rcount import RangeCounter, brute_force_count

EPS = 1e-9


class BoundaryRuleTest(unittest.TestCase):
    """闭矩形规则：四条边、四个角上的点全部计入。"""

    def setUp(self):
        # 单位正方形 [0,1]x[0,1]，四条边与四个角上各放点，界外近邻点做对照。
        self.points = [
            (0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0),   # 四个角
            (0.5, 0.0), (0.5, 1.0), (0.0, 0.5), (1.0, 0.5),   # 四条边中点
            (0.5, 0.5),                                        # 内部
            (-EPS, 0.5), (1.0 + EPS, 0.5),                     # 界外（左右）
            (0.5, -EPS), (0.5, 1.0 + EPS),                     # 界外（上下）
        ]
        self.rc = RangeCounter(self.points)

    def test_edges_and_corners_included(self):
        # 9 个界内/界上点全部计入，4 个界外点不计入。
        self.assertEqual(self.rc.count(0.0, 0.0, 1.0, 1.0), 9)

    def test_each_edge_line(self):
        # 沿每条边的退化矩形（线），验证该边上的点被计入。
        self.assertEqual(self.rc.count(0.0, 0.0, 1.0, 0.0), 3)   # 下边
        self.assertEqual(self.rc.count(0.0, 1.0, 1.0, 1.0), 3)   # 上边
        self.assertEqual(self.rc.count(0.0, 0.0, 0.0, 1.0), 3)   # 左边
        self.assertEqual(self.rc.count(1.0, 0.0, 1.0, 1.0), 3)   # 右边

    def test_corner_point_rect(self):
        # 点矩形恰好压在角上：计入该角。
        self.assertEqual(self.rc.count(1.0, 1.0, 1.0, 1.0), 1)
        # 点矩形紧贴界外：不计入。
        self.assertEqual(self.rc.count(1.0 + EPS, 1.0 + EPS,
                                       1.0 + EPS, 1.0 + EPS), 0)


class DuplicateTest(unittest.TestCase):
    """重复点按出现次数全部计入，不去重。"""

    def test_duplicates_all_counted(self):
        points = [(1.0, 1.0)] * 5 + [(2.0, 2.0)] * 3 + [(0.0, 0.0)]
        rc = RangeCounter(points)
        self.assertEqual(rc.count(1.0, 1.0, 1.0, 1.0), 5)   # 点矩形数重复点
        self.assertEqual(rc.count(1.5, 1.5, 2.5, 2.5), 3)
        self.assertEqual(rc.count(0.0, 0.0, 2.0, 2.0), 9)   # 全部
        self.assertEqual(rc.count(3.0, 3.0, 4.0, 4.0), 0)


class DegenerateAndEmptyTest(unittest.TestCase):
    """退化矩形（宽或高为零）与空矩形（x1>x2 或 y1>y2）的确定结果。"""

    def setUp(self):
        self.points = [(1.0, 1.0), (1.0, 2.0), (1.0, 3.0),
                       (2.0, 2.0), (3.0, 2.0), (5.0, 5.0)]
        self.rc = RangeCounter(self.points)

    def test_zero_width_vertical_segment(self):
        # x = 1, 1 <= y <= 3：3 个点。
        self.assertEqual(self.rc.count(1.0, 1.0, 1.0, 3.0), 3)
        self.assertEqual(self.rc.count(1.0, 1.5, 1.0, 2.5), 1)

    def test_zero_height_horizontal_segment(self):
        # y = 2, 1 <= x <= 3：3 个点。
        self.assertEqual(self.rc.count(1.0, 2.0, 3.0, 2.0), 3)

    def test_point_rect(self):
        self.assertEqual(self.rc.count(5.0, 5.0, 5.0, 5.0), 1)
        self.assertEqual(self.rc.count(4.0, 4.0, 4.0, 4.0), 0)

    def test_empty_rect_is_zero(self):
        self.assertEqual(self.rc.count(3.0, 0.0, 1.0, 10.0), 0)   # x1 > x2
        self.assertEqual(self.rc.count(0.0, 5.0, 10.0, 1.0), 0)   # y1 > y2
        self.assertEqual(self.rc.count(3.0, 5.0, 1.0, 1.0), 0)    # 两者同时
        self.assertEqual(brute_force_count(self.points, 3.0, 0.0, 1.0, 10.0), 0)


class AllInAllOutTest(unittest.TestCase):
    """点全在界内 / 点全在界外。"""

    def test_all_inside(self):
        points = [(float(i), float(i % 7)) for i in range(1000)]
        rc = RangeCounter(points)
        self.assertEqual(rc.count(-1.0, -1.0, 1000.0, 7.0), 1000)

    def test_all_outside(self):
        points = [(float(i), float(i)) for i in range(1000)]
        rc = RangeCounter(points)
        self.assertEqual(rc.count(2000.0, 2000.0, 3000.0, 3000.0), 0)
        self.assertEqual(rc.count(-100.0, -100.0, -1.0, -1.0), 0)


class RandomCrossCheckTest(unittest.TestCase):
    """随机对拍：RangeCounter 必须与逐点暴力扫描完全一致。"""

    def _random_rect(self, rng, lo, hi, kind):
        x1 = rng.uniform(lo, hi)
        x2 = rng.uniform(lo, hi)
        y1 = rng.uniform(lo, hi)
        y2 = rng.uniform(lo, hi)
        if kind == 0:      # 普通矩形
            pass
        elif kind == 1:    # 零宽
            x2 = x1
        elif kind == 2:    # 零高
            y2 = y1
        elif kind == 3:    # 点矩形
            x2, y2 = x1, y1
        elif kind == 4:    # 空矩形
            x1, x2 = max(x1, x2), min(x1, x2)
        else:              # 全平面
            x1, y1, x2, y2 = lo, lo, hi, hi
        return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)) \
            if kind != 4 else (x1, min(y1, y2), x2, max(y1, y2))

    def test_random_uniform_crosscheck(self):
        rng = random.Random(20261005)
        n, q = 20000, 1500
        points = [(rng.uniform(-500, 500), rng.uniform(-500, 500))
                  for _ in range(n)]
        rc = RangeCounter(points)
        for i in range(q):
            rect = self._random_rect(rng, -600, 600, i % 6)
            got = rc.count(*rect)
            want = brute_force_count(points, *rect)
            self.assertEqual(got, want, f"rect={rect}")

    def test_boundary_heavy_crosscheck(self):
        # 点全落在整数网格上、查询边界也对齐网格：大量点恰好落在矩形边上。
        rng = random.Random(777)
        n, q = 20000, 1500
        points = [(float(rng.randint(0, 99)), float(rng.randint(0, 99)))
                  for _ in range(n)]
        rc = RangeCounter(points)
        for _ in range(q):
            x1, x2 = sorted((rng.randint(0, 99), rng.randint(0, 99)))
            y1, y2 = sorted((rng.randint(0, 99), rng.randint(0, 99)))
            got = rc.count(x1, y1, x2, y2)
            want = brute_force_count(points, x1, y1, x2, y2)
            self.assertEqual(got, want)

    def test_duplicate_heavy_crosscheck(self):
        rng = random.Random(31337)
        n, q = 10000, 800
        hot = [(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]
        points = [rng.choice(hot) for _ in range(n)]
        rc = RangeCounter(points)
        for _ in range(q):
            x1 = rng.uniform(0, 4)
            x2 = rng.uniform(0, 4)
            y1 = rng.uniform(0, 4)
            y2 = rng.uniform(0, 4)
            rect = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
            self.assertEqual(rc.count(*rect), brute_force_count(points, *rect))

    def test_extreme_values(self):
        points = [(-1e300, -1e300), (1e300, 1e300), (0.0, 0.0),
                  (-0.0, 0.0), (1e-300, -1e-300)]
        rc = RangeCounter(points)
        self.assertEqual(rc.count(-1e300, -1e300, 1e300, 1e300), 5)
        self.assertEqual(rc.count(0.0, 0.0, 0.0, 0.0), 2)   # 0.0 与 -0.0 相等
        self.assertEqual(rc.count(1e-300, -1e-300, 1e-300, -1e-300), 1)

    def test_empty_point_set(self):
        rc = RangeCounter([])
        self.assertEqual(rc.count(0.0, 0.0, 1.0, 1.0), 0)
        self.assertEqual(rc.count(1.0, 1.0, 0.0, 0.0), 0)

    def test_int_points(self):
        points = [(i, i * i) for i in range(100)]
        rc = RangeCounter(points)
        self.assertEqual(rc.count(10, 0, 20, 400), 11)


if __name__ == "__main__":
    unittest.main(verbosity=2)

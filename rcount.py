# -*- coding: utf-8 -*-
"""平面点的正交矩形范围统计（标准库实现）。

计数规则
========
计数区域为闭矩形 R = [x1, x2] x [y1, y2]：

    点 (px, py) 被计入  <=>  x1 <= px <= x2  且  y1 <= py <= y2

* 四条边、四个角上的点全部计入（上下界均含，闭区间）。
* 重复点按出现次数全部计入，不去重（多重集合计数）。
* 空矩形：若 x1 > x2 或 y1 > y2，结果恒为 0（不报错、不自动交换端点）。
* 退化矩形按同样的闭区间规则确定计数：
    - x1 == x2 且 y1 < y2：退化为竖直线段 x = x1, y1 <= y <= y2；
    - y1 == y2 且 x1 < x2：退化为水平线段 y = y1, x1 <= x <= x2；
    - x1 == x2 且 y1 == y2：退化为单点，计数等于该坐标的重复次数。

数据结构
========
点按 x 升序排列（全局二分确定 x 满足条件的下标区间），再按 x 顺序切成
大小约为 sqrt(n) 的块，每块保存按 y 升序的副本。查询时：

* 中间完整落在 x 区间内的块：块内对 y 做两次二分（O(log B)）；
* 首尾两个不完整块：逐点检查 y。

查询复杂度 O(sqrt(n) log n)，构建复杂度 O(n log n)，空间 O(n)。
结果与逐点暴力扫描 brute_force_count 完全一致（见 test_rcount.py / crosscheck.py）。
"""

from array import array
from bisect import bisect_left, bisect_right
from math import isqrt

__all__ = ["RangeCounter", "brute_force_count"]


class RangeCounter:
    """闭矩形范围计数器（点的多重集合）。

    Parameters
    ----------
    points : 可迭代的 (x, y) 二元组，x/y 为 int/float。
    block_size : 分块大小，默认 max(1, floor(sqrt(n)))。
    """

    def __init__(self, points, block_size=None):
        pts = sorted((float(px), float(py)) for px, py in points)
        n = len(pts)
        self.n = n
        if block_size is None:
            block_size = max(1, isqrt(n))
        if block_size < 1:
            raise ValueError("block_size 必须 >= 1")
        self.block_size = block_size

        # 按 x 升序排列的全局坐标（x 相同再按 y 升序）。
        self._xs = array("d", (p[0] for p in pts))
        self._ys = array("d", (p[1] for p in pts))

        # 每块一份按 y 升序的副本，用于块内二分计数。
        self._block_ys = []
        for start in range(0, n, block_size):
            self._block_ys.append(sorted(self._ys[start:start + block_size]))

    def count(self, x1, y1, x2, y2):
        """返回闭矩形 [x1,x2] x [y1,y2] 内的点数（重复点全部计入）。"""
        if x1 > x2 or y1 > y2:
            return 0

        xs = self._xs
        ys = self._ys

        # 全局按 x 有序：[lo, hi) 即满足 x1 <= x <= x2 的全部点。
        lo = bisect_left(xs, x1)
        hi = bisect_right(xs, x2)
        if lo >= hi:
            return 0

        block = self.block_size
        block_ys = self._block_ys
        first = lo // block          # 头块编号
        last = (hi - 1) // block     # 尾块编号
        total = 0

        if first == last:
            # 下标区间完全落在一个块内：逐点检查 y。
            for i in range(lo, hi):
                py = ys[i]
                if y1 <= py <= y2:
                    total += 1
            return total

        # 头块不完整部分：[lo, (first+1)*block)
        head_end = (first + 1) * block
        for i in range(lo, head_end):
            py = ys[i]
            if y1 <= py <= y2:
                total += 1

        # 尾块不完整部分：[last*block, hi)
        tail_start = last * block
        for i in range(tail_start, hi):
            py = ys[i]
            if y1 <= py <= y2:
                total += 1

        # 中间完整块：块内点必满足 x 条件，只数 y1 <= y <= y2。
        for b in range(first + 1, last):
            sy = block_ys[b]
            total += bisect_right(sy, y2) - bisect_left(sy, y1)

        return total


def brute_force_count(points, x1, y1, x2, y2):
    """逐点暴力扫描参考实现，计数规则与 RangeCounter.count 相同。"""
    if x1 > x2 or y1 > y2:
        return 0
    total = 0
    for px, py in points:
        if x1 <= px <= x2 and y1 <= py <= y2:
            total += 1
    return total

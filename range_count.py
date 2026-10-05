"""平面点矩形范围计数库（仅标准库）。

计数规则（全库统一，详见 README.md）：
  * 矩形 [x1, x2] x [y1, y2] 四边全部闭区间：落在边界上的点计入。
  * 空矩形（x1 > x2 或 y1 > y2）：结果恒为 0。
  * 退化矩形（宽或高为 0，即线段/单点矩形）：按闭区间规则正常计数。
  * 重复点：每个副本都计入，不去重。

实现：x 排序 + y 坐标压缩 + 小波矩阵（wavelet matrix）。
  建索引 O(n log n)，单次查询 O(log n)，索引本体约 2.5 字节/点/层。
"""

from array import array
from bisect import bisect_left, bisect_right

__all__ = ["RangeCounter", "count_brute", "RULES"]

RULES = (
    "closed rectangle [x1,x2]x[y1,y2]; boundary points counted; "
    "empty rectangle (x1>x2 or y1>y2) -> 0; degenerate (zero width/height) "
    "counted by the same closed rule; duplicates all counted"
)


def count_brute(points, x1, y1, x2, y2):
    """逐点暴力扫描的参考实现，用于对拍。规则与 RangeCounter 完全一致。"""
    if x1 > x2 or y1 > y2:
        return 0
    cnt = 0
    for px, py in points:
        if x1 <= px <= x2 and y1 <= py <= y2:
            cnt += 1
    return cnt


class _WaveletMatrix:
    """对非负整数序列建小波矩阵，支持区间 [l, r) 内值域 [lo, hi] 计数。"""

    __slots__ = ("bits", "zeros", "pref")

    def __init__(self, values, nbits):
        n = len(values)
        words = (n + 7) // 8
        self.bits = []   # 每层 bitset（bytearray，小端位序）
        self.zeros = []  # 每层 0 的个数
        self.pref = []   # 每层逐字节 popcount 前缀和（array('I')）
        cur = list(values)
        for level in range(nbits - 1, -1, -1):
            mask = 1 << level
            bits = bytearray(words)
            for i, v in enumerate(cur):
                if v & mask:
                    bits[i >> 3] |= 1 << (i & 7)
            # 逐字节 popcount 前缀和：rank1(i) = pref[i>>3] + 末字节掩码 popcount
            pref = array("I", bytes(4 * (words + 1)))
            acc = 0
            for j in range(words):
                pref[j] = acc
                acc += bits[j].bit_count()
            pref[words] = acc
            zeros = []
            ones = []
            zappend = zeros.append
            oappend = ones.append
            for v in cur:
                if v & mask:
                    oappend(v)
                else:
                    zappend(v)
            self.bits.append(bits)
            self.zeros.append(len(zeros))
            self.pref.append(pref)
            cur = zeros + ones  # 按当前位稳定划分

    def _rank1(self, lv, i):
        """第 lv 层前 i 个位置中 1 的个数，i ∈ [0, n]。"""
        if i <= 0:
            return 0
        bits = self.bits[lv]
        byte, rem = i >> 3, i & 7
        res = self.pref[lv][byte]
        if rem:
            res += (bits[byte] & ((1 << rem) - 1)).bit_count()
        return res

    def count_leq(self, l, r, k):
        """区间 [l, r) 内值 <= k 的元素个数，要求 0 <= k < 2**nbits。"""
        cnt = 0
        nbits = len(self.bits)
        rank1 = self._rank1
        for lv in range(nbits):
            z = self.zeros[lv]
            l1 = rank1(lv, l)
            r1 = rank1(lv, r)
            if k & (1 << (nbits - 1 - lv)):
                # 当前位为 1：本位取 0 的元素必然 < k，全部计入
                cnt += (r - r1) - (l - l1)
                l, r = z + l1, z + r1
            else:
                l, r = l - l1, r - r1
            if l >= r:
                return cnt
        return cnt + (r - l)  # 余下的恰好等于 k

    def count_between(self, l, r, lo, hi):
        """区间 [l, r) 内值满足 lo <= v <= hi 的元素个数。"""
        if l >= r or lo > hi:
            return 0
        cnt = self.count_leq(l, r, hi)
        if lo > 0:
            cnt -= self.count_leq(l, r, lo - 1)
        return cnt


class RangeCounter:
    """静态点集的矩形范围计数器。"""

    __slots__ = ("n", "xs", "ys_sorted", "_ymap", "_wm", "_nbits")

    def __init__(self, points):
        pts = sorted(points)  # 按 (x, y) 排序；重复点全部保留
        self.n = len(pts)
        self.xs = [p[0] for p in pts]
        ys = sorted({p[1] for p in pts})
        self.ys_sorted = ys
        self._ymap = {y: i for i, y in enumerate(ys)}
        ranks = [self._ymap[p[1]] for p in pts]
        nbits = max(1, (len(ys) - 1).bit_length())
        self._nbits = nbits
        self._wm = _WaveletMatrix(ranks, nbits)

    def count(self, x1, y1, x2, y2):
        """返回 [x1,x2]x[y1,y2] 闭矩形内的点数（边界计入，重复点各算一次）。"""
        if x1 > x2 or y1 > y2 or self.n == 0:
            return 0
        l = bisect_left(self.xs, x1)
        r = bisect_right(self.xs, x2)
        if l >= r:
            return 0
        ys = self.ys_sorted
        lo = bisect_left(ys, y1)
        if lo == len(ys):
            return 0
        hi = bisect_right(ys, y2) - 1
        if hi < lo:
            return 0
        return self._wm.count_between(l, r, lo, hi)

    def index_bytes(self):
        """索引本体（小波矩阵）占用的字节数，不含点坐标与压缩表。"""
        total = 0
        for bits, pref in zip(self._wm.bits, self._wm.pref):
            total += len(bits) + pref.itemsize * len(pref)
        return total

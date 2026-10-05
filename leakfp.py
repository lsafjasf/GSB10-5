"""leakfp -- 文本泄露检测指纹匹配库（仅依赖 Python 3 标准库）。

指纹生成方式
------------
1. 归一化：NFKC 兼容分解 + 小写化，只保留字母与数字（去掉空白、标点、
   全角/半角差异），降低排版差异对匹配的影响。
2. 分片：在归一化后的字符串上取长度为 k 的字符 n-gram（相邻分片重叠
   k-1 个字符）。中文按字切分，k=10 即 10 个汉字。
3. 哈希：64 位 Rabin 多项式滚动哈希（模 2**64，底数取 FNV 素数
   0x100000001b3），滑窗 O(n) 计算；取哈希值高 44 位作为指纹。
4. 选指纹（winnowing）：在哈希序列上以大小为 w 的滑动窗口选取
   "最右最小"的哈希。该算法保证：任意长度 >= k+w-1 个字符的公共子串，
   两侧至少产生一个相同指纹。

索引结构
--------
指纹 44 位 = 高 16 位桶号 + 低 28 位桶内键；桶内键与 20 位文档号打包成
一个 64 位整数，存入每桶一个有序 array('Q')。查询时先定位桶，再二分
查找键区间，取出文档号计数。单库最多 2**20 = 1,048,576 篇文档。
"""

from __future__ import annotations

import bisect
import unicodedata
from array import array
from collections import deque
from hashlib import blake2b

__all__ = ["LeakDetector", "normalize"]

_MASK64 = (1 << 64) - 1
_BASE = 0x100000001B3          # 滚动哈希底数（FNV 素数）
_DOCID_BITS = 20               # 文档号位数
_MAX_DOCS = 1 << _DOCID_BITS
_DOCID_MASK = _MAX_DOCS - 1
_HASH_BITS = 44                # 指纹位数
_BUCKET_BITS = 16              # 桶号位数
_LOW_BITS = _HASH_BITS - _BUCKET_BITS   # 桶内键位数 = 28
_LOW_MASK = (1 << _LOW_BITS) - 1


def normalize(text: str) -> str:
    """归一化：NFKC + 小写，仅保留字母和数字。"""
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(ch for ch in text if ch.isalnum())


def _shingle_hashes(s: str, k: int, base: int, base_k1: int) -> list[int]:
    """对归一化字符串计算所有 k-gram 的 64 位滚动哈希。"""
    n = len(s)
    if n == 0:
        return []
    if n < k:
        # 短于 k 的文本退化为整串哈希（只能匹配完全相同的短文本）
        return [int.from_bytes(blake2b(s.encode("utf-8"), digest_size=8).digest(), "big")]
    h = 0
    for ch in s[:k]:
        h = (h * base + ord(ch)) & _MASK64
    out = [h]
    mask = _MASK64
    for i in range(k, n):
        h = ((h - ord(s[i - k]) * base_k1) * base + ord(s[i])) & mask
        out.append(h)
    return out


def _winnow(hashes: list[int], w: int) -> list[int]:
    """winnowing：每个大小为 w 的窗口选取最右最小哈希（单调队列 O(n)）。"""
    if not hashes:
        return []
    if w < 1:
        w = 1
    selected = []
    window = deque()          # 存下标，对应哈希值严格递增
    last = -1
    for i, h in enumerate(hashes):
        while window and window[0] <= i - w:
            window.popleft()
        while window and hashes[window[-1]] >= h:
            window.pop()
        window.append(i)
        if window[0] != last:
            last = window[0]
            selected.append(hashes[window[0]])
    return selected


class LeakDetector:
    """指纹索引 + 查询。文档号按添加顺序从 0 自动分配。"""

    def __init__(self, k: int = 10, w: int = 8):
        if k < 3:
            raise ValueError("k 至少为 3")
        if w < 1:
            raise ValueError("w 至少为 1")
        self.k = k
        self.w = w
        self._base_k1 = pow(_BASE, k - 1, 1 << 64)
        self._buckets: dict[int, array] = {}
        self._fp_counts = array("I")     # 每篇文档的指纹数
        self._n = 0
        self._finalized = False

    # ---- 指纹 ----

    def fingerprints(self, text: str) -> set[int]:
        """返回文本的指纹集合（44 位整数，去重）。"""
        s = normalize(text)
        hashes = _shingle_hashes(s, self.k, _BASE, self._base_k1)
        shift = 64 - _HASH_BITS
        return {h >> shift for h in _winnow(hashes, self.w)}

    # ---- 建库 ----

    def add(self, text: str) -> int:
        """添加一篇受保护文档，返回分配的文档号。"""
        if self._finalized:
            raise RuntimeError("finalize() 之后不能再添加文档")
        if self._n >= _MAX_DOCS:
            raise OverflowError("单库最多 2**20 篇文档")
        doc_id = self._n
        fps = self.fingerprints(text)
        for fp in fps:
            bucket = fp >> _LOW_BITS
            packed = ((fp & _LOW_MASK) << _DOCID_BITS) | doc_id
            arr = self._buckets.get(bucket)
            if arr is None:
                arr = self._buckets[bucket] = array("Q")
            arr.append(packed)
        self._fp_counts.append(len(fps))
        self._n += 1
        return doc_id

    def add_many(self, texts) -> None:
        for t in texts:
            self.add(t)

    def finalize(self) -> None:
        """排序各桶，建库结束后调用（查询前会自动调用一次）。"""
        for bucket, arr in self._buckets.items():
            self._buckets[bucket] = array("Q", sorted(arr))
        self._finalized = True

    # ---- 查询 ----

    def query(self, text: str, threshold: float = 0.10, min_matches: int = 4,
              max_df: int | None = 32, top: int = 5) -> list[tuple[int, float, int]]:
        """查询待检文本，返回 [(doc_id, 重合度, 命中指纹数), ...]，按重合度降序。

        重合度 = 命中指纹数 / min(查询指纹数, 库中文档指纹数)。
        threshold   : 重合度阈值。
        min_matches : 命中指纹的绝对条数下限（抑制短文本/巧合误报）。
        max_df      : 忽略出现在超过 max_df 篇文档中的指纹（抑制模板/
                      套话造成的误报）；None 表示不过滤。
        """
        if not self._finalized:
            self.finalize()
        fps = self.fingerprints(text)
        if not fps:
            return []
        counts: dict[int, int] = {}
        for fp in fps:
            arr = self._buckets.get(fp >> _LOW_BITS)
            if arr is None:
                continue
            lo_key = (fp & _LOW_MASK) << _DOCID_BITS
            lo = bisect.bisect_left(arr, lo_key)
            hi = bisect.bisect_right(arr, lo_key | _DOCID_MASK)
            df = hi - lo
            if df == 0 or (max_df is not None and df > max_df):
                continue
            for j in range(lo, hi):
                d = arr[j] & _DOCID_MASK
                counts[d] = counts.get(d, 0) + 1
        nq = len(fps)
        results = []
        for d, m in counts.items():
            if m < min_matches:
                continue
            denom = min(nq, self._fp_counts[d])
            if denom == 0:
                continue
            score = m / denom
            if score >= threshold:
                results.append((d, score, m))
        results.sort(key=lambda t: (-t[1], -t[2]))
        return results[:top]

    # ---- 统计 ----

    def stats(self) -> dict:
        return {
            "docs": self._n,
            "fingerprints": int(sum(self._fp_counts)),
            "buckets": len(self._buckets),
            "index_bytes": sum(a.buffer_info()[1] * a.itemsize
                               for a in self._buckets.values()),
        }

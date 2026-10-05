"""leakfp - 泄漏检测指纹匹配库（仅标准库）

指纹方案
========
1. 归一化：转小写，抽取 token。英文/数字整体算一个词，中文按单字切分；
   标点、空白、符号全部丢弃，因此改标点、改空格不影响指纹。
2. 分片：滑动 k-gram（默认 k=8 个 token），每个分片用 BLAKE2b 取 64 位哈希。
3. 选指纹：Winnowing（窗口默认 w=6）。对所有 k-gram 哈希做滑动窗口，
   每个窗口取最小值作为文档指纹。数学保证：任意两段长度 >= k+w-1 个 token
   的连续相同片段，至少有 1 个共同指纹，因此可以把指纹密度降到约 1/w。
4. 建库：倒排表 {指纹哈希: [文档id]}，另存每文档的指纹集合大小。
   公共套话控制：文档频率（DF）超过 max_df 的指纹在查询时被忽略——
   真正被抄的内容 DF 通常只有 1~2，而套话/模板会在大量文档里出现。
5. 查询：统计与库内各文档命中的不同指纹数 matched；
   query_containment = matched / 查询文档指纹数（被怀疑文档有多少比例来自该源文档）；
   source_coverage   = matched / 源文档指纹数（源文档被抄走多少比例）。

能容忍的改写
------------
- 改字/改标点/改空白/换大小写：局部 k-gram 失效，其余 k-gram 不受影响。
- 少量增删改 token（默认参数下，分散的、比例约 10% 以内的改动）。
- 段落换序：段落内部的 k-gram 原样保留，只有段落接缝处失效。
- 拆散后拼到别处：每段只要连续命中长度 >= k+w-1 token 就贡献共同指纹，
  多处碎片命中数累加（默认要求 >= 6 个不同指纹，约等于 40+ token 的实抄量）。

不能容忍
--------
- 大规模同义词替换/翻译级改写（这不是精确指纹能解决的，需要语义指纹）。
- 极短文档：不足 k+w 个 token 的文本直接拒判，避免短文本误报。
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict, deque
from typing import Dict, Iterable, List, Optional

__all__ = ["tokenize", "fingerprints", "LeakIndex", "Match"]

# 英文/数字词，或单个 CJK 汉字
_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]", re.UNICODE)


def tokenize(text: str) -> List[str]:
    """归一化并切 token：小写、去标点空白，英文按词、中文按字。"""
    return _TOKEN_RE.findall(text.lower())


def _shingle_hash(shingle: str) -> int:
    return int.from_bytes(
        hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest(),
        "little",
    )


def _winnow(hashes: List[int], w: int) -> List[int]:
    """对 k-gram 哈希序列做 Winnowing，单调队列 O(n)。

    每个长度为 w 的窗口选最小哈希（平局取最左），新选出的位置与上一次
    不同才输出。保证任意长度 >= w 的 k-gram 连续区间至少被选中一个哈希。
    """
    n = len(hashes)
    if n == 0:
        return []
    if n <= w:
        return [min(hashes)]

    out: List[int] = []
    dq: deque[int] = deque()
    prev_idx = -1

    for i, h in enumerate(hashes):
        while dq and dq[0] <= i - w:
            dq.popleft()
        while dq and hashes[dq[-1]] >= h:  # >= 保留更靠左的平局
            dq.pop()
        dq.append(i)

        if i >= w - 1:
            idx = dq[0]
            if idx != prev_idx:
                prev_idx = idx
                out.append(hashes[idx])
    return out


def fingerprints(text: str, k: int = 8, w: int = 6) -> List[int]:
    """返回文本的指纹哈希列表（已按 winnowing 选取，可能含重复值）。"""
    tokens = tokenize(text)
    if len(tokens) < k:
        return []
    shingle_hashes = [
        _shingle_hash(" ".join(tokens[i : i + k]))
        for i in range(len(tokens) - k + 1)
    ]
    return _winnow(shingle_hashes, w)


class Match(dict):
    """单条命中结果：doc_id / matched / query_containment / source_coverage。"""


class LeakIndex:
    def __init__(
        self,
        k: int = 8,
        w: int = 6,
        min_hits: int = 6,
        min_tokens: Optional[int] = None,
        max_df: Optional[int] = 8,
    ) -> None:
        if k < 2 or w < 1:
            raise ValueError("k 必须 >= 2，w 必须 >= 1")
        self.k = k
        self.w = w
        self.min_hits = min_hits
        # 不足该 token 数的文本指纹太少，直接拒判
        self.min_tokens = k + w if min_tokens is None else min_tokens
        # 出现在超过 max_df 篇文档里的指纹视为公共套话，查询时忽略；
        # 设为 None 可关闭该过滤。
        self.max_df = max_df
        self._index: Dict[int, List[object]] = defaultdict(list)
        self._sizes: Dict[object, int] = {}

    def add(self, doc_id: object, text: str) -> int:
        """入库一篇文档，返回其去重后的指纹数。"""
        fps = set(fingerprints(text, self.k, self.w))
        if doc_id in self._sizes:
            # 重新入库：先移除旧引用
            self._remove(doc_id)
        self._sizes[doc_id] = len(fps)
        for fp in fps:
            self._index[fp].append(doc_id)
        return len(fps)

    def _remove(self, doc_id: object) -> None:
        # 小规模/测试场景用；大批量建库不会调用
        stale = [fp for fp, ids in self._index.items() if doc_id in ids]
        for fp in stale:
            self._index[fp].remove(doc_id)
            if not self._index[fp]:
                del self._index[fp]
        self._sizes.pop(doc_id, None)

    def __len__(self) -> int:
        return len(self._sizes)

    def stats(self) -> dict:
        return {
            "documents": len(self._sizes),
            "distinct_fingerprints": len(self._index),
        }

    def query(
        self,
        text: str,
        min_hits: Optional[int] = None,
        max_results: Optional[int] = None,
    ) -> List[Match]:
        """查询，返回命中的源文档列表（按共同指纹数降序）。

        判定阈值：不同指纹命中数 >= min_hits（默认 6）。
        文本短于 min_tokens 个 token 时返回空列表（拒判）。
        """
        tokens = tokenize(text)
        if len(tokens) < self.min_tokens:
            return []

        q_fps = set(fingerprints(text, self.k, self.w))
        if not q_fps:
            return []

        threshold = self.min_hits if min_hits is None else min_hits
        counts: Dict[object, int] = defaultdict(int)
        for fp in q_fps:
            postings = self._index.get(fp)
            if not postings:
                continue
            if self.max_df is not None and len(postings) > self.max_df:
                continue  # 公共套话指纹，忽略
            for doc_id in postings:
                counts[doc_id] += 1

        results: List[Match] = []
        for doc_id, matched in counts.items():
            if matched < threshold:
                continue
            d_size = self._sizes.get(doc_id, 1) or 1
            results.append(
                Match(
                    doc_id=doc_id,
                    matched=matched,
                    query_containment=matched / len(q_fps),
                    source_coverage=matched / d_size,
                )
            )
        results.sort(key=lambda m: (-m["matched"], str(m["doc_id"])))
        return results if max_results is None else results[:max_results]

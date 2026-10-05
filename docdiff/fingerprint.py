"""正文指纹、shingle 与相似度（标准库实现）。"""
from __future__ import annotations

import difflib
import zlib
from typing import FrozenSet, List

from .model import Tree

SHINGLE_K = 2


def body_fingerprint(lines: List[str]) -> int:
    """逐行 crc32 链式指纹：正文逐字节相同即相等。"""
    fp = 0
    for ln in lines:
        fp = zlib.crc32(ln.encode("utf-8"), fp)
    return fp & 0xFFFFFFFF


def shingles(lines: List[str], k: int = SHINGLE_K) -> FrozenSet[int]:
    """正文行的 k-shingle 哈希集合（单段不足 k 行时退化为行集合）。"""
    if not lines:
        return frozenset()
    if len(lines) < k:
        grams = lines
    else:
        grams = ["\x00".join(lines[i:i + k]) for i in range(len(lines) - k + 1)]
    return frozenset(zlib.crc32(g.encode("utf-8")) for g in grams)


def jaccard(a: FrozenSet[int], b: FrozenSet[int]) -> float:
    """集合 Jaccard 相似度。两个空集视为 1.0（都没有正文信号）。"""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / (len(a) + len(b) - len(a & b))


def title_sim(a: str, b: str) -> float:
    """标题相似度：difflib 序列比（基于最长连续匹配块）。"""
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def fingerprint_tree(tree: Tree) -> None:
    for sec in tree.sections.values():
        sec.body_fp = body_fingerprint(sec.body_lines)
        sec.body_shingles = shingles(sec.body_lines)

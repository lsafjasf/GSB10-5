"""数据模型与文本规范化。"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_WS_RE = re.compile(r"\s+")


def norm_title(text: str) -> str:
    """标题规范化：NFKC 兼容分解 + 空白折叠 + 小写。"""
    text = unicodedata.normalize("NFKC", text)
    return _WS_RE.sub(" ", text).strip().lower()


@dataclass
class Section:
    sid: int
    level: int                      # 标题层级 1..6
    title: str                      # 原始标题
    ntitle: str                     # 规范化标题
    parent: Optional[int]           # 父节 sid，根为 None
    children: List[int] = field(default_factory=list)
    start_line: int = 0             # 标题行号（1 起）
    end_line: int = 0               # 末行行号（含）
    body_lines: List[str] = field(default_factory=list)  # 规范化正文行（去空行、去首尾空白）
    body_fp: int = 0                # 正文指纹 crc32
    body_shingles: frozenset = frozenset()  # 正文 2-shingle 哈希集合

    @property
    def n_lines(self) -> int:
        return self.end_line - self.start_line + 1


@dataclass
class Tree:
    sections: Dict[int, Section]
    roots: List[int]
    preamble_lines: int = 0

    def preorder(self) -> List[int]:
        out: List[int] = []
        stack = list(reversed(self.roots))
        while stack:
            sid = stack.pop()
            out.append(sid)
            stack.extend(reversed(self.sections[sid].children))
        return out

    def subtree_size(self, sid: int) -> int:
        sec = self.sections[sid]
        return 1 + sum(self.subtree_size(c) for c in sec.children)

    def subtree_lines(self, sid: int) -> int:
        sec = self.sections[sid]
        return sec.n_lines + sum(self.subtree_lines(c) for c in sec.children)

    def path(self, sid: int) -> List[str]:
        titles: List[str] = []
        cur: Optional[int] = sid
        while cur is not None:
            sec = self.sections[cur]
            titles.append(sec.title)
            cur = sec.parent
        return list(reversed(titles))

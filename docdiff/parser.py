"""Markdown 章节解析：ATX（#）与 Setext（=== / ---）标题。

解析结果是一棵章节树：标题之间的内容归属于所属章节正文。
第一个标题之前的内容记为 preamble（不参与差异分析）。
"""
from __future__ import annotations

import re
from typing import List, Tuple

from .model import Section, Tree, norm_title

_ATX_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_SETEXT_RE = re.compile(r"^(=+|-+)[ \t]*$")


def parse(text: str) -> Tree:
    lines = text.split("\n")
    n = len(lines)

    # 1) 找标题：(level, title, title_lineno, head_end_lineno)
    heads: List[Tuple[int, str, int, int]] = []
    i = 0
    while i < n:
        m = _ATX_RE.match(lines[i])
        if m:
            heads.append((len(m.group(1)), m.group(2), i + 1, i + 1))
            i += 1
            continue
        if i + 1 < n and lines[i].strip():
            m2 = _SETEXT_RE.match(lines[i + 1])
            if m2:
                level = 1 if m2.group(1).startswith("=") else 2
                heads.append((level, lines[i].strip(), i + 1, i + 2))
                i += 2
                continue
        i += 1

    # 2) 建树：按标题层级维护父栈
    sections = {}
    roots: List[int] = []
    stack: List[int] = []
    preamble_lines = heads[0][2] - 1 if heads else n

    for idx, (level, title, lineno, head_end) in enumerate(heads):
        next_start = heads[idx + 1][2] if idx + 1 < len(heads) else n + 1
        end_line = next_start - 1
        body = [ln.strip() for ln in lines[head_end:end_line] if ln.strip()]
        sec = Section(
            sid=idx, level=level, title=title, ntitle=norm_title(title),
            parent=None, start_line=lineno, end_line=end_line, body_lines=body,
        )
        while stack and sections[stack[-1]].level >= level:
            stack.pop()
        if stack:
            sec.parent = stack[-1]
            sections[stack[-1]].children.append(idx)
        else:
            roots.append(idx)
        sections[idx] = sec
        stack.append(idx)

    return Tree(sections=sections, roots=roots, preamble_lines=preamble_lines)

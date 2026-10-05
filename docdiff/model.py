"""文档模型与 Markdown 解析。

解析规则:
- 仅识别 ATX 标题（行首 1-6 个 '#'，后跟空白），代码围栏(``` 或 ~~~)内的 '#' 不算标题。
- 标题行尾部可选 `{#anchor-id}`，作为跨版本的稳定锚点（最强匹配信号）。
- 层级由标题级别决定；标题级别跳跃（如 # 直接到 ###）按“深一级即子级”处理。
- 首个标题之前的内容挂在合成根节点 root 下；root 永远与 root 匹配。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*$")
ANCHOR_RE = re.compile(r"\{#([A-Za-z0-9_:\-\.]+)\}\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
ROOT_TITLE = "<root>"


@dataclass
class Section:
    id: int
    level: int                      # root 为 0，'#' 为 1，依此类推
    title: str
    anchor: Optional[str] = None
    body_lines: List[str] = field(default_factory=list)
    children: List["Section"] = field(default_factory=list)
    parent: Optional["Section"] = field(default=None, repr=False)
    ordinal: int = 0                # 在兄弟节点中的序号（0 起）
    start_line: int = 0             # 标题所在行号（1 起），root 为 0
    own_shingles: frozenset = frozenset()  # 自身正文（不含子章节）的 shingle 集合

    def path(self) -> str:
        parts = []
        node = self
        while node is not None and node.parent is not None:
            parts.append(node.title)
            node = node.parent
        return " > ".join(reversed(parts))

    def is_leaf(self) -> bool:
        return not self.children


@dataclass
class SectionIndex:
    root: Section
    sections: List[Section]         # 按文档顺序（先序），含 root（id=0）
    total_lines: int

    def by_id(self, sid: int) -> Section:
        return self.sections[sid]


def parse_document(text: str) -> SectionIndex:
    lines = text.splitlines()
    root = Section(id=0, level=0, title=ROOT_TITLE, start_line=0)
    sections: List[Section] = [root]
    stack: List[Section] = [root]
    in_fence = False

    for lineno, line in enumerate(lines, start=1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            stack[-1].body_lines.append(line)
            continue
        m = None if in_fence else HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            anchor = None
            am = ANCHOR_RE.search(title)
            if am:
                anchor = am.group(1)
                title = title[: am.start()].rstrip()
            node = Section(id=len(sections), level=level, title=title,
                           anchor=anchor, start_line=lineno)
            while len(stack) > 1 and stack[-1].level >= level:
                stack.pop()
            parent = stack[-1]
            node.parent = parent
            node.ordinal = len(parent.children)
            parent.children.append(node)
            sections.append(node)
            stack.append(node)
        else:
            stack[-1].body_lines.append(line)

    return SectionIndex(root=root, sections=sections, total_lines=len(lines))

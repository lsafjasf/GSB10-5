# -*- coding: utf-8 -*-
"""tocgen —— 目录生成与页码对齐库（仅使用 Python 标准库）。

核心概念
--------
物理页 (physical page)
    文档按阅读顺序逐张数下去的页，从 1 开始连续编号，包含前置内容与正文。

逻辑页 (logical page / 印出页码)
    实际印在页脚上的“页码”：
      * 前置内容（扉页、序言等）使用小写罗马数字 i, ii, iii ...；
      * 正文从阿拉伯数字 1 重新起算。
    物理页与逻辑页的换算见 :func:`logical_label`。

正文固定从“新的一页”开始，且正文逻辑页恒为 1 —— 因此目录本身放在前置
内容里、页数增减时，不会影响任何目录项的正文页码。这是出版排版（Chicago
Manual of Style 第 1.4–1.6 节等）通行的前置/正文分编号惯例，也是本库页码
口径的依据。

标题换页规则（确定性）
----------------------
* 一级标题（章）默认“另起一页”：即使当前页还有空位，也推到下一物理页，
  页码取新页；
* 任何标题遵循“与后文保持同页 (keep-with-next)”：若标题落在本页后，
  剩余空间放不下 标题行数 + keep_with_next 行正文，则整标题推到下一页；
* 标题允许跨页续排时（极端超长标题），页码取标题**首行**所在页；
* 当配置 chapter_new_page=False（章不另起页）时，可能出现“上一章末尾与
  下一章章首同页”，该章页码同样取标题首行所在的那个物理页——即同页。
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Union

# ---------------------------------------------------------------------------
# 文档模型
# ---------------------------------------------------------------------------


@dataclass
class Heading:
    """标题。level: 1=章, 2=节, 3=小节。"""

    level: int
    text: str


@dataclass
class Paragraph:
    """普通段落，按页面宽度自动折行。"""

    text: str


Block = Union[Heading, Paragraph]


@dataclass
class Document:
    """前置内容 (front) 与正文 (body) 两部分。front 可为空。"""

    body: list = field(default_factory=list)
    front: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# 版面与分页
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Layout:
    lines_per_page: int = 40
    page_width: int = 60
    chapter_new_page: bool = True   # 一级标题（章）是否另起一页
    keep_with_next: int = 2         # 标题后至少保留的正文行数
    body_starts_new_page: bool = True  # 正文是否必须从新页开始


@dataclass
class Page:
    number: int          # 物理页号，从 1 开始
    lines: list          # 该页逐行内容（未写满时末尾留空）


@dataclass
class Pagination:
    pages: list                          # list[Page]
    anchors: dict                        # id(Heading) -> 物理页号
    front_pages: int                     # 前置内容占用的物理页数
    body_start_page: int                 # 正文起始物理页（== front_pages + 1）


def display_width(s: str) -> int:
    """字符串在等宽终端上的显示列宽：全角/宽字符（含汉字）计 2，其余计 1。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in s)


_dw = display_width


def _take(word: str, width: int) -> str:
    """按显示列宽从 word 头部切下不超过 width 的一段。"""
    used, cut = 0, ""
    for ch in word:
        w = _dw(ch)
        if used + w > width:
            break
        cut += ch
        used += w
    return cut


def _wrap(text: str, width: int) -> list:
    """按显示列宽贪心折行；无空格的超长串（如连续汉字）按字符硬切。"""
    lines: list = []
    line, used = "", 0
    for word in text.split():
        if used + (1 if line else 0) + _dw(word) <= width:
            line += (" " if line else "") + word
            used += (1 if line else 0) + _dw(word)
        else:
            if line:
                lines.append(line)
                line, used = "", 0
            while _dw(word) > width:
                cut = _take(word, width)
                lines.append(cut)
                word = word[len(cut):]
            line, used = word, _dw(word)
    if line:
        lines.append(line)
    return lines or [""]


def paginate(doc: Document, layout: Layout = Layout()) -> Pagination:
    """把文档排版为物理页，并记录每个标题的锚点（首行物理页号）。"""
    pages: list = []
    cur: list = []
    anchors: dict = {}

    def flush() -> None:
        nonlocal cur
        pages.append(cur)
        cur = []

    def place(blocks: list) -> None:
        for blk in blocks:
            if isinstance(blk, Heading):
                hlines = _wrap(blk.text, layout.page_width)
                need = len(hlines) + layout.keep_with_next
                if blk.level == 1 and layout.chapter_new_page and cur:
                    flush()                                  # 章另起一页
                elif cur and len(cur) + need > layout.lines_per_page:
                    flush()                                  # keep-with-next
                anchors[id(blk)] = len(pages) + 1           # 首行所在页
                for ln in hlines:
                    if len(cur) >= layout.lines_per_page:
                        flush()
                    cur.append(ln)
            else:
                for ln in _wrap(blk.text, layout.page_width):
                    if len(cur) >= layout.lines_per_page:
                        flush()
                    cur.append(ln)

    place(doc.front)
    if cur:
        flush()
    front_pages = len(pages)
    place(doc.body)
    if cur or not pages:
        flush()

    page_objs = [Page(number=i + 1, lines=ln) for i, ln in enumerate(pages)]
    return Pagination(
        pages=page_objs,
        anchors=anchors,
        front_pages=front_pages,
        body_start_page=front_pages + 1,
    )


# ---------------------------------------------------------------------------
# 页码编号：物理页 <-> 逻辑页
# ---------------------------------------------------------------------------

_ROMAN = (
    (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"),
    (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
    (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
)


def to_roman(n: int) -> str:
    """1 -> 'i'，4 -> 'iv'，9 -> 'ix'（小写罗马数字）。"""
    if n <= 0:
        raise ValueError("罗马数字编号从 1 开始")
    out = ""
    for value, sym in _ROMAN:
        while n >= value:
            out += sym
            n -= value
    return out


def logical_label(physical: int, front_pages: int) -> str:
    """物理页号 -> 印出的逻辑页码。

    1..front_pages 用小写罗马数字；其后的正文页用阿拉伯数字 1,2,3...
    例：front_pages=2 时，物理页 1->'i', 2->'ii', 3->'1', 4->'2'。
    """
    if physical <= front_pages:
        return to_roman(physical)
    return str(physical - front_pages)


# ---------------------------------------------------------------------------
# 目录生成与对齐渲染
# ---------------------------------------------------------------------------


@dataclass
class TocEntry:
    level: int
    text: str
    physical: int    # 标题首行所在物理页
    logical: str     # 该物理页印出的逻辑页码（目录里显示的就是它）


def build_toc(doc: Document, p: Pagination, include_front: bool = True) -> list:
    """收集全部标题为目录项，页码与分页锚点严格对应。"""
    entries: list = []
    blocks = (doc.front if include_front else []) + doc.body
    for blk in blocks:
        if isinstance(blk, Heading):
            physical = p.anchors[id(blk)]
            entries.append(
                TocEntry(
                    level=blk.level,
                    text=blk.text,
                    physical=physical,
                    logical=logical_label(physical, p.front_pages),
                )
            )
    return entries


def render_toc(entries: list, width: int = 60, indent_step: int = 2,
               min_dots: int = 2) -> str:
    """渲染目录。

    对齐规则（不随标题长度变化）：
      * 第 level 级缩进 (level-1)*indent_step 个空格；
      * 页码右对齐到固定的 width 列，数字位宽取所有项的最大值；
      * 标题与页码之间至少有 min_dots 个点导引线；
      * 标题过长时按可用宽度折行，续行与标题起始列对齐（悬挂缩进），
        页码只出现在最后一行——因此页码列永远不会错位。
    """
    if not entries:
        return ""
    num_w = max(len(e.logical) for e in entries)
    out = []
    for e in entries:
        indent = " " * ((e.level - 1) * indent_step)
        avail = width - len(indent) - num_w - 1 - min_dots
        if avail < 8:
            raise ValueError("页面宽度不足以容纳缩进与页码")
        segs = _wrap(e.text, avail)
        for seg in segs[:-1]:
            out.append(indent + seg)
        last = indent + segs[-1]
        dots = "." * (width - _dw(last) - num_w - 1)
        out.append(f"{last} {dots}{e.logical:>{num_w}}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 逐项校验
# ---------------------------------------------------------------------------


@dataclass
class ValidationRow:
    level: int
    text: str
    physical: int
    logical: str
    label_ok: bool       # 逻辑页码换算是否正确
    found_on_page: bool  # 标题首行是否真的出现在该物理页
    matched_line: str    # 在该物理页找到的标题行（证据）


def validate(doc: Document, layout: Layout, p: Pagination,
             entries: list) -> list:
    """逐项核对：目录项声称的物理页上是否真有该标题，逻辑页码是否正确。"""
    page_map = {pg.number: pg.lines for pg in p.pages}
    rows = []
    for e in entries:
        first_seg = _wrap(e.text, layout.page_width)[0]
        lines = page_map.get(e.physical, [])
        matched = next((ln for ln in lines if ln.strip() == first_seg.strip()), "")
        rows.append(
            ValidationRow(
                level=e.level,
                text=e.text,
                physical=e.physical,
                logical=e.logical,
                label_ok=(e.logical == logical_label(e.physical, p.front_pages)),
                found_on_page=bool(matched),
                matched_line=matched,
            )
        )
    # 完整性：文档中的每个标题都必须有目录项
    n_headings = sum(isinstance(b, Heading) for b in doc.front + doc.body)
    if n_headings != len(entries):
        raise AssertionError(
            f"目录项数 {len(entries)} 与标题数 {n_headings} 不一致")
    return rows


def all_rows_ok(rows: list) -> bool:
    return all(r.label_ok and r.found_on_page for r in rows)


def format_validation(rows: list) -> str:
    """把校验结果渲染成逐行表格。"""
    head = f"{'#':>4} {'LV':>2} {'物理页':>4} {'逻辑页':>5}  校验  标题/匹配行"
    bar = "-" * 72
    lines = [head, bar]
    for i, r in enumerate(rows, 1):
        ok = "OK " if (r.label_ok and r.found_on_page) else "FAIL"
        title = r.text if len(r.text) <= 28 else r.text[:27] + "…"
        lines.append(
            f"{i:>4} {r.level:>2} {r.physical:>5} {r.logical:>5}  {ok}  {title}")
        if r.found_on_page:
            ev = r.matched_line if len(r.matched_line) <= 28 else r.matched_line[:27] + "…"
            lines.append(f"{'':>4} {'':>2} {'':>5} {'':>5}       └ 页内实际行: {ev}")
    lines.append(bar)
    lines.append(
        f"合计 {len(rows)} 项，全部通过" if all_rows_ok(rows)
        else f"合计 {len(rows)} 项，存在失败")
    return "\n".join(lines)

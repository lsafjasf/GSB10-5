"""toc_lib.py —— 目录生成与页码对齐库（仅依赖 Python 3 标准库）。

页码口径（见 README.md「页码口径」一节）：

* 物理页：全书从 1 连续编号的排版页，是引擎内部唯一坐标，含前置内容。
* 逻辑页码（印在页眉/页脚、目录中使用）：分节编号——
    - 前置节（front）：小写罗马数字 i, ii, iii, iv …，跨前置节连续；
    - 正文节（body）：阿拉伯数字，从 1 重新起算，跨正文节连续。
* 目录自身属于前置节，目录占页只影响前置节内部编号，不影响正文页码。
* 目录收录「排在目录之后的前置节」时，其页码依赖目录自身长度，
  采用不动点迭代重排，直到页码/行数稳定。
"""
from __future__ import annotations

import functools
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------- 显示宽度

@functools.lru_cache(maxsize=8192)
def char_width(ch: str) -> int:
    """单字符显示宽度：全角（W/F）计 2，组合记号计 0，其余计 1。"""
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(text: str) -> int:
    """字符串显示宽度（CJK 全角字符占 2 列）。"""
    return sum(char_width(ch) for ch in text)


def wrap_display(text: str, width: int) -> List[str]:
    """按显示宽度贪心折行；超宽词按字符硬切；保证至少返回一行。

    直接扫描文本，不物化逐字 token 列表，避免大段 CJK 文本产生海量临时对象。
    """
    if width < 1:
        raise ValueError("width 必须 >= 1")
    lines: List[str] = []
    cur, cur_w = "", 0
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == " ":
            if cur and cur_w + 1 <= width:
                cur += " "
                cur_w += 1
            i += 1
            continue
        cw = char_width(ch)
        if cw == 2:  # CJK 单字即可断点
            token, tw, i = ch, 2, i + 1
        else:        # 拉丁连续串作为一个词
            j = i + 1
            while j < n and text[j] != " " and char_width(text[j]) != 2:
                j += 1
            token, i = text[i:j], j
            tw = display_width(token)
        if cur_w + tw <= width:
            cur += token
            cur_w += tw
        elif tw <= width:
            lines.append(cur.rstrip(" "))
            cur, cur_w = token, tw
        else:        # 超宽词按字符硬切
            for c in token:
                c2 = char_width(c)
                if cur_w + c2 > width:
                    lines.append(cur.rstrip(" "))
                    cur, cur_w = "", 0
                cur += c
                cur_w += c2
    lines.append(cur.rstrip(" "))
    return lines


# ---------------------------------------------------------------- 罗马数字

_ROMAN_TABLE = [
    (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"),
    (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
    (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
]


def to_roman(n: int) -> str:
    """1..4999 -> 小写罗马数字。"""
    if not 1 <= n <= 4999:
        raise ValueError("罗马数字仅支持 1..4999")
    out = []
    for value, symbol in _ROMAN_TABLE:
        while n >= value:
            out.append(symbol)
            n -= value
    return "".join(out)


# ---------------------------------------------------------------- 文档模型

@dataclass
class Heading:
    """章节标题。level 越小级别越高（1 = 章/级 1）。"""
    level: int
    text: str


@dataclass
class Paragraph:
    """正文段落，渲染时按栏宽折行。"""
    text: str


@dataclass
class RawLine:
    """不再折行的成品行（目录页使用），宽度必须 <= 栏宽。"""
    text: str


@dataclass
class LayoutConfig:
    page_height: int = 40          # 每页可排文本行数
    page_width: int = 72           # 每页栏宽（显示列）
    chapter_break_level: int = 1   # level <= 此值的标题强制另起新页；0 表示不强制
    keep_with_next: int = 1        # 标题后至少与标题同页的行数
    toc_level_indent: int = 2      # 目录每级缩进列数
    toc_min_dots: int = 2          # 标题与页码间点线最小长度


@dataclass
class Section:
    """节。kind: 'front'=前置节（罗马页码）, 'body'=正文节（阿拉伯页码）, 'toc'=目录占位节。"""
    kind: str
    blocks: List[object] = field(default_factory=list)
    include_in_toc: Optional[bool] = None  # None: 仅 body 收录
    name: str = ""


@dataclass
class PlacedLine:
    text: str
    block: object


def paginate_blocks(blocks: List[object], cfg: LayoutConfig,
                    wrap_cache: Optional[Dict[int, List[str]]] = None,
                    ) -> List[List[PlacedLine]]:
    """把一个节的块流排成分好的物理页（节内坐标，页从 0 起）。

    规则（确定性）：
    * Heading 且 level <= chapter_break_level：所在页若已有内容则另起新页，
      故章首恒为新页第一行，页码取该新页；
    * Heading 受 keep_with_next 保护：标题 + 后续 N 行放不下当前页时，
      标题整体推到下一页，页码取下一页；
    * 任何块折行后超出页高则自然换页。
    """
    pages: List[List[PlacedLine]] = [[]]

    def new_page_if_nonempty() -> None:
        if pages[-1]:
            pages.append([])

    for block in blocks:
        if isinstance(block, Heading) and cfg.chapter_break_level > 0 \
                and block.level <= cfg.chapter_break_level:
            new_page_if_nonempty()

        if isinstance(block, RawLine):
            line_texts = [block.text]
        elif wrap_cache is not None and id(block) in wrap_cache:
            line_texts = wrap_cache[id(block)]
        else:
            line_texts = wrap_display(block.text, cfg.page_width)
            if wrap_cache is not None:
                wrap_cache[id(block)] = line_texts

        if isinstance(block, Heading) and cfg.keep_with_next > 0 and pages[-1]:
            remaining = cfg.page_height - len(pages[-1])
            if len(line_texts) + cfg.keep_with_next > remaining:
                pages.append([])  # 与后续内容分离，推到新页

        for text in line_texts:
            if len(pages[-1]) >= cfg.page_height:
                pages.append([])
            pages[-1].append(PlacedLine(text, block))

    return pages


# ---------------------------------------------------------------- 目录渲染

@dataclass
class TocEntry:
    level: int
    title: str
    label: str          # 目录中打印的逻辑页码
    physical: int       # 该标题实际所在物理页
    heading: Heading


@dataclass
class VerifyRow:
    title: str
    level: int
    physical: int
    label_in_page: str  # 标题实际落页的逻辑页码
    label_in_toc: str   # 目录中打印的逻辑页码
    ok: bool


@dataclass
class TocLayoutCheck:
    max_line_width: int          # 目录所有行的最大显示宽度
    width_limit: int             # 栏宽
    all_within_width: bool
    label_lines_right_aligned: bool  # 含点线的页码行右边界是否统一为栏宽列
    label_line_columns: List[int]    # 各页码行右边界列（应全部 == 栏宽）


@dataclass
class BuildResult:
    cfg: LayoutConfig
    sections: List[Tuple[Section, List[List[PlacedLine]]]]
    page_labels: Dict[Tuple[int, int], str]  # (节序, 节内页序) -> 逻辑页码
    heading_pages: Dict[int, Tuple[int, str]]  # id(Heading) -> (物理页, 逻辑页码)
    entries: List[TocEntry]
    toc_lines: List[str]
    toc_page_count: int
    total_pages: int
    iterations: int


def render_toc(entries: List[TocEntry], cfg: LayoutConfig) -> List[str]:
    """把目录项渲染成文本行。

    对齐规则（确定性，与标题长度无关）：
    * 每级缩进 toc_level_indent 列；续行悬挂缩进（再多 2 列）；
    * 超长标题按「栏宽 - 缩进 - 页码宽 - 最少点线」折行，可占多行；
    * 页码只出现在最后一行，前面用 '.' 填充到「栏宽 - 页码宽」列；
    * 因此每个页码行的右边界恒等于栏宽列，页码右对齐于同一列。
    """
    if not entries:
        return []
    width = cfg.page_width
    num_w = max(display_width(e.label) for e in entries)
    lines: List[str] = []
    for e in entries:
        indent = " " * ((e.level - 1) * cfg.toc_level_indent)
        cont_indent = indent + "  "
        avail = width - display_width(cont_indent) - num_w - cfg.toc_min_dots
        if avail < 4:
            raise ValueError("栏宽不足以容纳缩进与页码，请增大 page_width")
        parts = wrap_display(e.title, avail)
        for i, part in enumerate(parts):
            prefix = indent if i == 0 else cont_indent
            if i < len(parts) - 1:
                lines.append(prefix + part)
                continue
            cur = prefix + part
            dots = width - display_width(cur) - num_w
            lines.append(cur + "." * dots + e.label.rjust(num_w))
    return lines


def build(sections: List[Section], cfg: LayoutConfig,
          max_iterations: int = 20) -> BuildResult:
    """分节排版并生成目录；目录占位节（kind='toc'）会被成品目录行替换。

    当目录页码或行数发生变化时重排，直到不动点（处理目录收录后续前置节的情形）。
    """
    toc_lines: List[str] = []
    last_result: Optional[BuildResult] = None
    wrap_cache: Dict[int, List[str]] = {}

    for iteration in range(1, max_iterations + 1):
        sec_pages: List[Tuple[Section, List[List[PlacedLine]]]] = []
        page_labels: Dict[Tuple[int, int], str] = {}
        heading_pages: Dict[int, Tuple[int, str]] = {}
        heading_loc: Dict[int, Tuple[int, int]] = {}
        physical_base = 0
        front_done = 0
        body_done = 0

        for si, sec in enumerate(sections):
            if sec.kind == "toc":
                blocks: List[object] = [RawLine(line) for line in toc_lines]
            else:
                blocks = sec.blocks
            pages = paginate_blocks(blocks, cfg, wrap_cache)
            sec_pages.append((sec, pages))

            for li in range(len(pages)):
                if sec.kind == "body":
                    page_labels[(si, li)] = str(body_done + li + 1)
                else:  # front 与 toc 均按前置内容编罗马页码
                    page_labels[(si, li)] = to_roman(front_done + li + 1)

            for li, page in enumerate(pages):
                for placed in page:
                    if isinstance(placed.block, Heading):
                        hid = id(placed.block)
                        if hid not in heading_loc:
                            heading_loc[hid] = (si, li)

            count = len(pages)
            if sec.kind == "body":
                body_done += count
            else:
                front_done += count
            physical_base += count

        section_offsets: List[int] = []
        offset = 0
        for _, pages in sec_pages:
            section_offsets.append(offset)
            offset += len(pages)

        for hid, (si, li) in heading_loc.items():
            heading_pages[hid] = (
                section_offsets[si] + li + 1,
                page_labels[(si, li)],
            )

        entries: List[TocEntry] = []
        for si, sec in enumerate(sections):
            include = sec.include_in_toc
            if include is None:
                include = sec.kind == "body"
            if not include or sec.kind == "toc":
                continue
            for block in sec.blocks:
                if isinstance(block, Heading):
                    physical, label = heading_pages[id(block)]
                    entries.append(TocEntry(block.level, block.text,
                                            label, physical, block))

        new_toc_lines = render_toc(entries, cfg)
        toc_sec_index = next((i for i, s in enumerate(sections)
                              if s.kind == "toc"), None)
        toc_page_count = (len(sec_pages[toc_sec_index][1])
                          if toc_sec_index is not None else 0)

        last_result = BuildResult(
            cfg=cfg,
            sections=sec_pages,
            page_labels=page_labels,
            heading_pages=heading_pages,
            entries=entries,
            toc_lines=new_toc_lines,
            toc_page_count=toc_page_count,
            total_pages=sum(len(p) for _, p in sec_pages),
            iterations=iteration,
        )
        if new_toc_lines == toc_lines:
            return last_result
        toc_lines = new_toc_lines

    raise RuntimeError(f"目录页码在 {max_iterations} 轮内未收敛")


# ---------------------------------------------------------------- 校验

def check_toc_layout(result: BuildResult) -> TocLayoutCheck:
    """校验目录成品行：不超栏宽；所有含点线的页码行右边界统一在栏宽列。"""
    width = result.cfg.page_width
    columns: List[int] = []
    max_w = 0
    for line in result.toc_lines:
        w = display_width(line)
        max_w = max(max_w, w)
        if ".." in line:  # 页码行（点线 >= toc_min_dots）
            columns.append(w)
    right_ok = all(col == width for col in columns)
    return TocLayoutCheck(
        max_line_width=max_w,
        width_limit=width,
        all_within_width=max_w <= width,
        label_lines_right_aligned=right_ok,
        label_line_columns=columns,
    )


def verify(result: BuildResult) -> List[VerifyRow]:
    """逐项核对：目录打印页码 == 标题实际落页的逻辑页码（且物理页一致）。"""
    rows: List[VerifyRow] = []
    for e in result.entries:
        physical, label = result.heading_pages[id(e.heading)]
        rows.append(VerifyRow(
            title=e.title,
            level=e.level,
            physical=physical,
            label_in_page=label,
            label_in_toc=e.label,
            ok=(label == e.label and physical == e.physical),
        ))
    return rows

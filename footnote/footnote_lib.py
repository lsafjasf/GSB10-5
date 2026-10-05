"""footnote_lib: 脚注编号与跨页合并库（仅标准库，Python 3）。

模型
----
- 正文由若干“行”(Line)组成，每行携带若干引用点 (Ref)。
- 每个引用点引用一个脚注定义 (FootnoteDef, 由 key 标识)。
- paginate() 把正文行装进页面，并在每页页脚区排版被引用脚注的正文。

编号与排版规则（完整版见 docs/RULES.md）
----------------------------------------
1. 编号 = 该脚注 key 的“首次引用点”在全文中的顺序，从 1 开始连续分配。
2. 同一 key 的重复引用共享同一个编号（编号只占一个），每个引用点都显示该编号。
3. 删除引用点后重新编号，编号保持 1..N 连续，不留空号。
4. 脚注正文排在其“首个引用点所在页”页脚；放不下的部分作为续块顺延下一页，
   下一页先排续块，再排本页新条目。
5. 每个脚注的正文全文只排一次（首个引用点所在页）；之后页的重复引用只在
   正文里显示同一个上标编号，不再重印条目。条目尾部回链列出该脚注在全文中的
   全部引用点位置（同一行多个引用合并为 ×n）。引用页页脚已满时，条目整体顺延
   到下一页（标“(顺延)”），回链仍指向真正的引用页。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "Ref", "Line", "FootnoteDef", "RefPoint", "Chunk", "Page",
    "assign_numbers", "paginate", "check_consistency",
]

CONT_MARK = "(续)"       # 续块编号前缀
CARRIED_MARK = "(顺延)"  # 引用页放不下、整体顺延到下一页的新条目前缀
SUPERS = "⁰¹²³⁴⁵⁶⁷⁸⁹"


@dataclass(frozen=True)
class Ref:
    """正文行内的一个引用点。tag 区分同一行内引用同一 key 的多个点。"""
    key: str
    tag: str = ""


@dataclass
class Line:
    text: str
    refs: List[Ref] = field(default_factory=list)


@dataclass
class FootnoteDef:
    key: str
    body: List[str]


@dataclass
class RefPoint:
    """预扫描得到的引用点：seq 为全文顺序号（删除重排时用于对照）。"""
    seq: int
    line_index: int
    key: str
    tag: str


@dataclass
class Chunk:
    """页脚区中某脚注的一个排版块（同页同 key 只有一个块）。"""
    key: str
    number: int
    page: int
    lines: List[str]                    # 本块容纳的脚注正文行
    start: int                          # 首行在 FootnoteDef.body 中的下标
    continuation: bool                  # 是否为上一页块的续块
    carried: bool                       # 是否为引用页放不下、顺延到本页的新条目
    refs: List[Tuple[int, int, int]]    # 引用位置 (引用所在页, 正文行下标, 引用点seq)
    has_more: bool


@dataclass
class Page:
    number: int
    body: List[str]
    foot: List[str]
    chunks: List[Chunk] = field(default_factory=list)


@dataclass
class _Pending:
    key: str
    start: int          # 下一条待排正文行下标；0 表示新条目，>0 表示续块
    carried: bool       # 仅 start==0 时有意义：新条目是否从引用页顺延而来
    refs: List[Tuple[int, int, int]] = field(default_factory=list)


def assign_numbers(lines: Sequence[Line]) -> Tuple[List[RefPoint], Dict[str, int]]:
    """预扫描：收集引用点并按首次出现顺序为每个 key 分配编号。"""
    points: List[RefPoint] = []
    numbers: Dict[str, int] = {}
    seq = 0
    for line_index, line in enumerate(lines):
        for ref in line.refs:
            seq += 1
            points.append(RefPoint(seq, line_index, ref.key, ref.tag))
            if ref.key not in numbers:
                numbers[ref.key] = len(numbers) + 1
    return points, numbers


def superscript(number: int) -> str:
    return "".join(SUPERS[int(d)] for d in str(number))


def paginate(
    lines: Sequence[Line],
    defs: Dict[str, FootnoteDef],
    body_cap: int,
    foot_cap: int,
) -> Tuple[List[Page], Dict[str, int], List[RefPoint]]:
    """分页并排版脚注。

    返回 (页面列表, key->编号, 引用点列表)。
    body_cap / foot_cap 分别为每页正文区、页脚区可容纳的行数。
    脚注正文一行是不可再分的最小排版单位（超过页脚容量的行由调用方自行折行）。
    """
    points, numbers = assign_numbers(lines)
    points_by_line: Dict[int, List[RefPoint]] = {}
    for p in points:
        points_by_line.setdefault(p.line_index, []).append(p)

    line_page = _line_pages(lines, body_cap)
    all_refs: Dict[str, List[Tuple[int, int, int]]] = {}
    for p in points:
        all_refs.setdefault(p.key, []).append(
            (line_page[p.line_index], p.line_index, p.seq)
        )

    pages: List[Page] = []
    pending: List[_Pending] = []
    done = set()  # 正文已完整排出的 key（含正在顺延中的，避免重复排队）
    li = 0
    n = len(lines)

    while li < n or pending:
        page_no = len(pages) + 1

        body: List[str] = []
        page_points: List[RefPoint] = []
        while len(body) < body_cap and li < n:
            line = lines[li]
            body.append(
                line.text + "".join(superscript(numbers[r.key]) for r in line.refs)
            )
            page_points.extend(points_by_line.get(li, []))
            li += 1

        cur_keys = []
        seen_cur = set()
        for p in page_points:
            if p.key not in seen_cur:
                seen_cur.add(p.key)
                cur_keys.append(p.key)

        queue: List[_Pending] = []
        seen = set()
        # 1) 上一页顺延：续块或被顺延的新条目
        for item in pending:
            queue.append(item)
            seen.add(item.key)
            done.add(item.key)
        # 2) 本页首次引用、且尚未排过的新条目（重复引用不重印）
        for key in cur_keys:
            if key not in seen and key not in done:
                seen.add(key)
                refs = [(pg, ln, sq) for (pg, ln, sq) in all_refs[key]]
                queue.append(_Pending(key=key, start=0, carried=False, refs=refs))

        foot: List[str] = []
        chunks: List[Chunk] = []
        next_pending: List[_Pending] = []
        qi = 0
        while qi < len(queue):
            item = queue[qi]
            room = foot_cap - len(foot)
            if room <= 0:
                # 页脚已满：本条目及后续条目整体顺延（复用对象，避免 O(积压×页数)）
                for rest in queue[qi:]:
                    if rest.start == 0:
                        rest.carried = True
                    done.add(rest.key)
                    next_pending.append(rest)
                break
            qi += 1

            all_body = defs[item.key].body
            take = min(room, len(all_body) - item.start)
            part = all_body[item.start:item.start + take]
            has_more = item.start + take < len(all_body)

            mark = CONT_MARK if item.start > 0 else (CARRIED_MARK if item.carried else "")
            tag = f"[{mark}{numbers[item.key]}]"
            indent = " " * len(tag)
            back = _backlink(item.refs)
            for i, text in enumerate(part):
                if i == 0:
                    foot.append(f"{tag} {text}{back}")
                else:
                    foot.append(f"{indent} {text}")

            chunks.append(Chunk(
                key=item.key,
                number=numbers[item.key],
                page=page_no,
                lines=part,
                start=item.start,
                continuation=item.start > 0,
                carried=item.carried and item.start == 0,
                refs=item.refs,
                has_more=has_more,
            ))
            done.add(item.key)
            if has_more:
                next_pending.append(_Pending(
                    key=item.key, start=item.start + take,
                    carried=False, refs=[],
                ))

        pages.append(Page(page_no, body, foot, chunks))
        pending = next_pending

    return pages, numbers, points


def _line_pages(lines: Sequence[Line], body_cap: int) -> List[int]:
    """每行正文所在的物理页号（1 起）。"""
    return [i // body_cap + 1 for i in range(len(lines))]


def _backlink(refs: List[Tuple[int, int, int]]) -> str:
    if not refs:
        return ""
    # 同一正文行上的多个引用点合并显示（×n）
    merged: Dict[Tuple[int, int], int] = {}
    for page, line_index, _seq in refs:
        key = (page, line_index)
        merged[key] = merged.get(key, 0) + 1
    parts = [
        f"p{page}.L{line_index + 1}" + (f"×{count}" if count > 1 else "")
        for (page, line_index), count in sorted(merged.items())
    ]
    return " ← " + ", ".join(parts)


def check_consistency(
    pages: Sequence[Page],
    defs: Dict[str, FootnoteDef],
    points: Sequence[RefPoint],
    numbers: Dict[str, int],
    foot_cap: Optional[int] = None,
) -> List[str]:
    """引用一致性校验，返回问题列表（空列表表示全部通过）。"""
    problems: List[str] = []

    # 正文行下标 -> 物理页号
    line_page: Dict[int, int] = {}
    for page in pages:
        for _ in page.body:
            pass
    line_index = 0
    for page in pages:
        for _ in page.body:
            line_page[line_index] = page.number
            line_index += 1

    chunks_by_key: Dict[str, List[Chunk]] = {}
    for page in pages:
        if foot_cap is not None and len(page.foot) > foot_cap:
            problems.append(f"第{page.number}页页脚行数 {len(page.foot)} 超过容量 {foot_cap}")
        keys = [ch.key for ch in page.chunks]
        if len(keys) != len(set(keys)):
            problems.append(f"第{page.number}页存在同 key 的多个块")
        for ch in page.chunks:
            chunks_by_key.setdefault(ch.key, []).append(ch)

    # 1) 每个引用点都有同编号条目，且出现在回链中
    for p in points:
        candidates = [
            ch for ch in chunks_by_key.get(p.key, [])
            if any(seq == p.seq for _, _, seq in ch.refs)
        ]
        if not candidates:
            problems.append(f"引用点#{p.seq}(key={p.key}) 没有对应的页脚条目")
            continue
        ch = candidates[0]
        if ch.number != numbers[p.key]:
            problems.append(
                f"引用点#{p.seq} 编号不一致: 正文[{numbers[p.key]}] vs 页脚[{ch.number}]"
            )

    # 2) 每个有引用的 key 在其首个引用页（或顺延页）有 start==0 的首块
    first_ref_page: Dict[str, int] = {}
    for p in points:
        first_ref_page.setdefault(p.key, line_page.get(p.line_index, 0))
    for key, pg in first_ref_page.items():
        chs = sorted(chunks_by_key.get(key, []), key=lambda c: (c.page, c.start))
        if not chs or chs[0].start != 0:
            problems.append(f"脚注[{key}] 缺少首块(start=0)")
            continue
        first_block = chs[0]
        if first_block.page != pg and not first_block.carried:
            problems.append(f"脚注[{key}] 首块在第{first_block.page}页但引用在第{pg}页且未标记顺延")
        heads = [c for c in chs if c.start == 0]
        if len(heads) != 1:
            problems.append(f"脚注[{key}] 首块数量异常: {len(heads)}")

    # 3) 跨页块首尾相接、续块标记正确，合并后等于定义正文
    for key, chs in chunks_by_key.items():
        chs.sort(key=lambda c: (c.page, c.start))
        for prev, nxt in zip(chs, chs[1:]):
            if nxt.start != prev.start + len(prev.lines):
                problems.append(
                    f"脚注[{key}] 块不连续: {prev.start}+{len(prev.lines)} → {nxt.start}"
                )
            if not nxt.continuation:
                problems.append(f"脚注[{key}] 第{nxt.page}页块未标记为续块")
        merged = [text for ch in chs for text in ch.lines]
        if key in defs and merged != defs[key].body:
            problems.append(f"脚注[{key}] 跨页块合并结果与定义正文不一致")

    # 4) 编号连续无空号
    used = sorted(set(numbers.values()))
    if used != list(range(1, len(used) + 1)):
        problems.append(f"编号不连续: {used}")

    return problems

"""footnote.py — 脚注编号与跨页合并库（仅使用 Python 标准库）。

编号规则
--------
1. 每个引用点（reference）通过 key 指向一条脚注正文（body）。
2. 编号按"引用点在文档中首次出现的顺序"从 1 开始连续分配；
   同一条脚注（同一 key）被多处引用时，所有引用点共享同一个编号。
3. 删除引用点后调用 renumber()：剩余引用点仍按首次出现顺序重新编号，
   编号保持 1..N 连续、不留空号；若某条脚注的所有引用点都被删除，
   该脚注的编号被回收（其正文仍保留在库中，可再次被引用）。
4. 脚注正文跨页时按行拆分，每页碎片带有"承上页 / 转下页"标记；
   merge_parts() 可把同一编号的碎片无损合并回完整正文。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 引用点与编号
# ---------------------------------------------------------------------------

class FootnoteRegistry:
    """管理脚注正文、引用点与编号。"""

    def __init__(self) -> None:
        self._bodies: Dict[str, str] = {}          # key -> 脚注正文
        self._refs: List[List] = []                # [ref_id, key, alive]，按文档顺序
        self._by_id: Dict[str, int] = {}           # ref_id -> 在 _refs 中的下标
        self._numbers: Dict[str, int] = {}         # key -> 编号（renumber 后有效）
        self._dirty: bool = True
        self._seq: int = 0

    # ---- 脚注正文 -------------------------------------------------------

    def define(self, key: str, text: str) -> None:
        """登记一条脚注正文。重复 define 同一 key 会覆盖正文。"""
        self._bodies[key] = text

    def body_of(self, key: str) -> str:
        return self._bodies[key]

    # ---- 引用点 ---------------------------------------------------------

    def add_reference(self, key: str, ref_id: Optional[str] = None) -> str:
        """在文档末尾追加一个指向 key 的引用点，返回 ref_id。"""
        if key not in self._bodies:
            raise KeyError(f"未定义的脚注: {key!r}")
        if ref_id is None:
            self._seq += 1
            ref_id = f"r{self._seq}"
        if ref_id in self._by_id:
            raise ValueError(f"引用点 id 重复: {ref_id!r}")
        self._by_id[ref_id] = len(self._refs)
        self._refs.append([ref_id, key, True])
        self._dirty = True
        return ref_id

    def remove_reference(self, ref_id: str) -> None:
        """删除一个引用点（墓碑标记，renumber 时回收编号）。"""
        idx = self._by_id.pop(ref_id)
        self._refs[idx][2] = False
        self._dirty = True

    # ---- 编号 -----------------------------------------------------------

    def renumber(self) -> Dict[str, int]:
        """按首次出现顺序重排编号，返回 {key: 编号}。O(引用点数)。"""
        numbers: Dict[str, int] = {}
        n = 0
        for _rid, key, alive in self._refs:
            if alive and key not in numbers:
                n += 1
                numbers[key] = n
        self._numbers = numbers
        self._dirty = False
        return dict(numbers)

    def _ensure_numbers(self) -> None:
        if self._dirty:
            self.renumber()

    def number_of(self, key: str) -> Optional[int]:
        """某条脚注当前的编号；无存活引用点时返回 None。"""
        self._ensure_numbers()
        return self._numbers.get(key)

    def number_at(self, ref_id: str) -> int:
        """某个引用点应显示的编号。"""
        self._ensure_numbers()
        key = self._refs[self._by_id[ref_id]][1]
        return self._numbers[key]

    def mapping_table(self) -> List[Tuple[int, str, str]]:
        """对照表：[(编号, key, 正文)]，按编号升序。"""
        self._ensure_numbers()
        return sorted(
            (num, key, self._bodies[key]) for key, num in self._numbers.items()
        )

    def reference_table(self) -> List[Tuple[str, str, int]]:
        """引用点表：[(ref_id, key, 编号)]，按文档顺序。"""
        self._ensure_numbers()
        return [
            (rid, key, self._numbers[key])
            for rid, key, alive in self._refs
            if alive
        ]

    # ---- 一致性校验 -----------------------------------------------------

    def validate(self) -> List[str]:
        """校验引用一致性，返回问题列表（空列表表示全部通过）。

        检查项：
        a) 同一 key 的所有存活引用点解析到同一个编号；
        b) 编号集合恰为 1..N，连续且不留空号；
        c) 每个持有编号的 key 至少有一个存活引用点；
        d) 每个存活引用点指向的 key 都有编号与正文。
        """
        self._ensure_numbers()
        problems: List[str] = []

        seen: Dict[str, int] = {}
        live_keys = set()
        for rid, key, alive in self._refs:
            if not alive:
                continue
            live_keys.add(key)
            num = self._numbers.get(key)
            if num is None:
                problems.append(f"引用点 {rid} 指向的脚注 {key!r} 没有编号")
                continue
            if key not in self._bodies:
                problems.append(f"引用点 {rid} 指向的脚注 {key!r} 缺少正文")
            prev = seen.setdefault(key, num)
            if prev != num:
                problems.append(
                    f"脚注 {key!r} 的引用点编号不一致: {prev} != {num}"
                )

        nums = sorted(self._numbers.values())
        if nums != list(range(1, len(nums) + 1)):
            problems.append(f"编号不连续或留有空号: {nums}")

        for key in sorted(self._numbers):
            if key not in live_keys:
                problems.append(f"脚注 {key!r} 持有编号但没有任何存活引用点")

        return problems


# ---------------------------------------------------------------------------
# 跨页拆分与合并
# ---------------------------------------------------------------------------

@dataclass
class FootnotePart:
    """一页上的一段脚注碎片。"""

    number: int
    lines: List[str]
    continued_from_previous_page: bool = False   # 承上页
    continues_on_next_page: bool = False         # 转下页


def paginate_footnotes(
    ordered: List[Tuple[int, str]], lines_per_page: int
) -> List[List[FootnotePart]]:
    """把按编号排序的 [(编号, 正文)] 切分到各页脚注区。

    每页脚注区最多容纳 lines_per_page 行；一条脚注放不下时按行拆分，
    碎片自动带上承上 / 转下标记。返回 [页][碎片]。
    """
    if lines_per_page < 1:
        raise ValueError("lines_per_page 必须 >= 1")
    pages: List[List[FootnotePart]] = []
    current: List[FootnotePart] = []
    used = 0
    for number, text in ordered:
        remaining = text.split("\n")
        first_chunk = True
        while remaining:
            if used == lines_per_page:
                pages.append(current)
                current, used = [], 0
            space = lines_per_page - used
            chunk, remaining = remaining[:space], remaining[space:]
            current.append(
                FootnotePart(
                    number=number,
                    lines=chunk,
                    continued_from_previous_page=not first_chunk,
                    continues_on_next_page=bool(remaining),
                )
            )
            used += len(chunk)
            first_chunk = False
    if current:
        pages.append(current)
    return pages


def merge_parts(parts: List[FootnotePart]) -> List[Tuple[int, str]]:
    """把跨页碎片按编号合并回 [(编号, 完整正文)]，保持编号顺序。"""
    merged: Dict[int, List[str]] = {}
    order: List[int] = []
    for part in parts:
        if part.number not in merged:
            merged[part.number] = []
            order.append(part.number)
        merged[part.number].extend(part.lines)
    return [(n, "\n".join(merged[n])) for n in order]


def render_page(page_no: int, parts: List[FootnotePart]) -> str:
    """把一页的脚注碎片渲染成带标记的文本（用于演示与调试）。"""
    out = [f"--- 第 {page_no} 页脚注区 ---"]
    for part in parts:
        head = f"[{part.number}]"
        if part.continued_from_previous_page:
            head += "（接上页）"
        out.append(f"{head} {part.lines[0]}")
        out.extend(f"     {line}" for line in part.lines[1:])
        if part.continues_on_next_page:
            out.append("     （下转下页）")
    return "\n".join(out)

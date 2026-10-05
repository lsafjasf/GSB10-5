"""一致性核对核心逻辑。

区间模型
========
所有字符区间均为**左闭右开** ``[start, end)``，字符位置从 0 开始：

* ``start`` 指向区间首个字符，``end`` 指向区间之后的位置；区间长度为 ``end - start``。
* 两个区间"重叠"定义为存在**正长度**交集，即
  ``max(a.start, b.start) < min(a.end, b.end)``。
* **相邻区间不算重叠**：``a.end == b.start``（或反过来）时交集长度为 0，不报冲突。
* **零长度区间** ``[p, p)`` 是合法的"点标注"：
  - 两个点标注仅在同一点位（``start``、``end`` 完全相同）时才相互匹配；
  - 点标注与任何非零长度区间都不构成重叠（即使点位落在其内部），
    因为左闭右开区间 ``[p, p)`` 不覆盖任何字符。
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class ConflictType(Enum):
    """两名标注者在同一文档上的成对判定结果。"""

    EXACT_MATCH = "exact_match"
    """区间完全相同且标签相同（一致，不计入冲突清单）。"""

    LABEL_CONFLICT = "label_conflict"
    """区间完全相同（含零长度点标注），但标签不同（纯标签冲突）。"""

    SPAN_OVERLAP = "span_overlap"
    """区间存在正长度重叠且标签相同（仅边界分歧）。"""

    SPAN_LABEL_CONFLICT = "span_label_conflict"
    """区间存在正长度重叠且标签不同（边界 + 标签双重冲突）。"""

    @property
    def is_conflict(self) -> bool:
        return self is not ConflictType.EXACT_MATCH

    @property
    def severity(self) -> str:
        if self is ConflictType.LABEL_CONFLICT:
            return "high"
        if self is ConflictType.SPAN_LABEL_CONFLICT:
            return "high"
        if self is ConflictType.SPAN_OVERLAP:
            return "medium"
        return "none"


@dataclass(frozen=True)
class Annotation:
    """一条标注：某标注者对某文档 ``[start, end)`` 区间打的标签。"""

    doc_id: str
    start: int
    end: int
    label: str
    annotator: str

    def __post_init__(self) -> None:
        if not isinstance(self.start, int) or not isinstance(self.end, int):
            raise TypeError(f"区间端点必须为整数: {self!r}")
        if self.start < 0 or self.end < 0:
            raise ValueError(f"区间端点不能为负: {self!r}")
        if self.start > self.end:
            raise ValueError(f"start 不能大于 end: {self!r}")
        if not str(self.label):
            raise ValueError(f"标签不能为空: {self!r}")
        if not str(self.annotator):
            raise ValueError(f"标注者标识不能为空: {self!r}")

    @property
    def is_point(self) -> bool:
        """零长度（点）标注。"""
        return self.start == self.end

    def as_tuple(self) -> tuple[str, int, int, str]:
        return (self.doc_id, self.start, self.end, self.label)


@dataclass(frozen=True)
class Conflict:
    """一处可返工的冲突/一致记录，精确定位到条目与字符区间。"""

    doc_id: str
    conflict_type: ConflictType
    annotator_a: str
    start_a: int
    end_a: int
    label_a: str
    annotator_b: str
    start_b: int
    end_b: int
    label_b: str

    @property
    def overlap_start(self) -> int:
        return max(self.start_a, self.start_b)

    @property
    def overlap_end(self) -> int:
        return min(self.end_a, self.end_b)

    @property
    def union_start(self) -> int:
        """返工定位区间起点（取双方并集，保证覆盖整段分歧）。"""
        return min(self.start_a, self.start_b)

    @property
    def union_end(self) -> int:
        """返工定位区间终点。"""
        return max(self.end_a, self.end_b)

    @property
    def char_range(self) -> str:
        """形如 ``doc-001:[12,30)`` 的字符区间定位串。"""
        return f"{self.doc_id}:[{self.union_start},{self.union_end})"


def overlap_length(start_a: int, end_a: int, start_b: int, end_b: int) -> int:
    """正长度交集的字符数；相邻或不相交均返回 0。"""
    return max(0, min(end_a, end_b) - max(start_a, start_b))


def spans_overlap(a: Annotation, b: Annotation) -> bool:
    """是否存在正长度重叠。

    相邻（``a.end == b.start``）返回 ``False``；
    任意一方为零长度点标注时返回 ``False``。
    """
    return min(a.end, b.end) > max(a.start, b.start)


def classify(a: Annotation, b: Annotation) -> ConflictType | None:
    """比较同一文档上两名不同标注者的两条标注。

    返回 :class:`ConflictType`，若两者既不相同也不重叠则返回 ``None``。
    调用方需保证 ``a.doc_id == b.doc_id`` 且 ``a.annotator != b.annotator``。
    """
    if a.doc_id != b.doc_id:
        raise ValueError("classify 只能比较同一文档内的标注")
    if a.annotator == b.annotator:
        raise ValueError("classify 只能比较不同标注者的标注")

    same_span = a.start == b.start and a.end == b.end
    if same_span:
        # 含零长度点标注：同一点位才匹配，不同点位互不相关。
        return ConflictType.EXACT_MATCH if a.label == b.label else ConflictType.LABEL_CONFLICT
    if spans_overlap(a, b):
        # 点标注不可能走到这里（其正长度交集恒为 0）。
        return (
            ConflictType.SPAN_OVERLAP
            if a.label == b.label
            else ConflictType.SPAN_LABEL_CONFLICT
        )
    return None


@dataclass
class CheckResult:
    """核对结果：冲突清单（不含 exact_match）与全部成对记录。"""

    conflicts: list[Conflict]
    pair_records: list[Conflict]
    docs: list[str]
    annotators: list[str]
    annotations_by_doc_annotator: dict[str, dict[str, list[Annotation]]]

    def conflicts_of_type(self, conflict_type: ConflictType) -> list[Conflict]:
        return [c for c in self.conflicts if c.conflict_type is conflict_type]


def _unique_sorted(spans: Iterable[Annotation]) -> list[Annotation]:
    """同一标注者在同文档上的完全重复标注只保留一条，按 (start, end) 排序。"""
    uniq = { (s.start, s.end, s.label): s for s in spans }
    return sorted(uniq.values(), key=lambda s: (s.start, s.end, s.label))


def check_consistency(annotations: Iterable[Annotation]) -> CheckResult:
    """核对整批评注，返回可返工的冲突清单。

    算法：按文档分组 -> 标注者两两配对 ->
    先用哈希精确匹配相同区间，再用按起点排序的扫描线找出所有正长度重叠对，
    整体复杂度近似 ``O(n log n + k)``（``k`` 为报告的成对数）。
    """
    grouped: dict[str, dict[str, list[Annotation]]] = defaultdict(lambda: defaultdict(list))
    annotator_set: set[str] = set()
    for ann in annotations:
        grouped[ann.doc_id][ann.annotator].append(ann)
        annotator_set.add(ann.annotator)

    records: list[Conflict] = []
    for doc_id in sorted(grouped):
        per_annotator = {
            name: _unique_sorted(spans)
            for name, spans in grouped[doc_id].items()
        }
        names = sorted(per_annotator)
        for i, name_a in enumerate(names):
            spans_a = per_annotator[name_a]
            for name_b in names[i + 1:]:
                spans_b = per_annotator[name_b]
                records.extend(
                    _compare_pair(doc_id, name_a, spans_a, name_b, spans_b)
                )

    records.sort(
        key=lambda c: (
            c.doc_id, c.union_start, c.union_end,
            c.annotator_a, c.annotator_b, c.conflict_type.value,
        )
    )
    conflicts = [c for c in records if c.conflict_type.is_conflict]
    return CheckResult(
        conflicts=conflicts,
        pair_records=records,
        docs=sorted(grouped),
        annotators=sorted(annotator_set),
        annotations_by_doc_annotator=grouped,
    )


def _make_record(
    doc_id: str, kind: ConflictType,
    name_a: str, a: Annotation, name_b: str, b: Annotation,
) -> Conflict:
    return Conflict(
        doc_id=doc_id,
        conflict_type=kind,
        annotator_a=name_a,
        start_a=a.start, end_a=a.end, label_a=a.label,
        annotator_b=name_b,
        start_b=b.start, end_b=b.end, label_b=b.label,
    )


def _compare_pair(
    doc_id: str,
    name_a: str, spans_a: list[Annotation],
    name_b: str, spans_b: list[Annotation],
) -> list[Conflict]:
    """两名标注者的跨度列表已按 (start, end) 排序且无重复。"""
    out: list[Conflict] = []

    # 1) 精确区间匹配（含零长度点标注），按 (start,end) 建索引。
    by_span: dict[tuple[int, int], list[Annotation]] = defaultdict(list)
    for b in spans_b:
        by_span[(b.start, b.end)].append(b)
    for a in spans_a:
        for b in by_span.get((a.start, a.end), ()):
            kind = classify(a, b)
            if kind is not None:
                out.append(_make_record(doc_id, kind, name_a, a, name_b, b))

    # 2) 扫描线：找出所有正长度重叠对（精确匹配对跳过，避免重复报告）。
    # spans_b 已按起点排序；active 保留 end > 当前 a.start 的候选 b。
    active: deque[Annotation] = deque()
    cursor = 0
    for a in spans_a:
        while active and active[0].end <= a.start:
            active.popleft()
        while cursor < len(spans_b) and spans_b[cursor].start < a.end:
            candidate = spans_b[cursor]
            if candidate.end > a.start:
                active.append(candidate)
            cursor += 1
        for b in active:
            if (b.start, b.end) == (a.start, a.end):
                continue  # 精确对已在上一步处理
            kind = classify(a, b)
            if kind is not None:
                out.append(_make_record(doc_id, kind, name_a, a, name_b, b))

    return out

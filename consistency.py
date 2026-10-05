"""标注数据一致性核对库（仅标准库）。

数据模型：一条标注 = (item_id, annotator, start, end, label)，
字符区间为半开区间 [start, end)，单位是字符偏移。

判定规则（详细说明见 RULES.md）：
- 重叠：s1 < e2 且 s2 < e1。相邻（e1 == s2）不算重叠。
- 零长度区间（start == end）：不参与重叠判定；仅与同一位置的
  零长度区间做精确比较（标签同则一致，不同则标签冲突）。
- 冲突分类：
    LABEL_CONFLICT      区间完全相同，标签不同
    OVERLAP_SAME_LABEL  区间部分重叠（含包含），标签相同
    OVERLAP_DIFF_LABEL  区间部分重叠（含包含），标签不同
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field, asdict

# 冲突类型常量
LABEL_CONFLICT = "LABEL_CONFLICT"
OVERLAP_SAME_LABEL = "OVERLAP_SAME_LABEL"
OVERLAP_DIFF_LABEL = "OVERLAP_DIFF_LABEL"
ZERO_LENGTH = "ZERO_LENGTH"  # 异常提示，不算冲突


@dataclass(frozen=True)
class Annotation:
    item_id: str
    annotator: str
    start: int
    end: int
    label: str

    def __post_init__(self):
        if self.start < 0 or self.end < 0:
            raise ValueError(f"负偏移非法: {self}")
        if self.start > self.end:
            raise ValueError(f"start > end 非法: {self}")

    @property
    def is_zero_length(self) -> bool:
        return self.start == self.end


@dataclass
class Conflict:
    item_id: str
    kind: str                      # LABEL_CONFLICT / OVERLAP_*
    char_start: int                # 冲突定位区间（重叠部分为交集，标签冲突为区间本身）
    char_end: int
    annotators: list               # [{annotator, start, end, label}, ...] 恰好两条
    snippet: str = ""              # 原文上下文（若提供了文本）


@dataclass
class PairAgreement:
    annotator_a: str
    annotator_b: str
    n_a: int                       # A 的标注数（不含零长度异常？含，见 RULES）
    n_b: int
    exact_matches: int
    relaxed_matches: int           # exact + 重叠且同标签的配对
    strict: float                  # 严格一致率 = 2*exact/(n_a+n_b)
    relaxed: float                 # 宽松一致率 = 2*relaxed/(n_a+n_b)


@dataclass
class Report:
    total_annotations: int
    total_items: int
    annotators: list
    conflicts: list = field(default_factory=list)        # list[Conflict]
    zero_length: list = field(default_factory=list)      # list[Annotation] 异常提示
    pairwise: list = field(default_factory=list)         # list[PairAgreement]
    strict_overall: float = 0.0
    relaxed_overall: float = 0.0

    def to_dict(self):
        return {
            "total_annotations": self.total_annotations,
            "total_items": self.total_items,
            "annotators": self.annotators,
            "conflicts": [asdict(c) for c in self.conflicts],
            "zero_length": [asdict(a) for a in self.zero_length],
            "pairwise": [asdict(p) for p in self.pairwise],
            "strict_overall": self.strict_overall,
            "relaxed_overall": self.relaxed_overall,
        }


def load_annotations(path):
    """从 JSON 读取。格式：{"items": {id: text}, "annotations": [...]}，
    items 可省略。返回 (annotations, items_text)。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    anns = [Annotation(str(a["item_id"]), str(a["annotator"]),
                       int(a["start"]), int(a["end"]), str(a["label"]))
            for a in data["annotations"]]
    items = {str(k): v for k, v in data.get("items", {}).items()}
    return anns, items


def _snippet(text, start, end, ctx=10):
    lo = max(0, start - ctx)
    hi = min(len(text), end + ctx)
    return text[lo:hi]


def _pair_conflicts(item_id, a_list, b_list, ann_a, ann_b, items_text, out):
    """对同一 item 下两个标注者的标注做两指针扫描，产出冲突。
    a_list / b_list 已按 (start, end) 排序，且均不含零长度区间。"""
    # 1) 同区间不同标签 -> LABEL_CONFLICT
    by_span_b = defaultdict(list)
    for b in b_list:
        by_span_b[(b.start, b.end)].append(b)
    matched_span_b = set()  # 已按“同区间”处理过的 b 索引
    exact_same_label = 0
    for a in a_list:
        for b in by_span_b.get((a.start, a.end), ()):
            if b.label != a.label:
                out.append(Conflict(
                    item_id, LABEL_CONFLICT, a.start, a.end,
                    [asdict(a), asdict(b)],
                    _snippet(items_text[item_id], a.start, a.end)
                    if item_id in items_text else ""))
            else:
                exact_same_label += 1
            matched_span_b.add(id(b))
    # 2) 部分重叠（排除区间完全相同，上面已处理）
    j = 0
    n_b = len(b_list)
    for a in a_list:
        while j < n_b and b_list[j].end <= a.start:
            j += 1
        k = j
        while k < n_b and b_list[k].start < a.end:
            b = b_list[k]
            k += 1
            if (b.start, b.end) == (a.start, a.end):
                continue  # 同区间已处理
            kind = OVERLAP_SAME_LABEL if a.label == b.label else OVERLAP_DIFF_LABEL
            out.append(Conflict(
                item_id, kind, max(a.start, b.start), min(a.end, b.end),
                [asdict(a), asdict(b)],
                _snippet(items_text[item_id], max(a.start, b.start), min(a.end, b.end))
                if item_id in items_text else ""))
    return exact_same_label


def _count_agreements(a_list, b_list):
    """返回 (exact_matches, relaxed_matches)。
    exact：区间+标签完全一致（含零长度区间同位置同标签）。
    relaxed：exact + 部分重叠且同标签的贪心配对（每条标注至多配对一次）。"""
    cnt_a = defaultdict(int)
    cnt_b = defaultdict(int)
    for a in a_list:
        cnt_a[(a.start, a.end, a.label)] += 1
    for b in b_list:
        cnt_b[(b.start, b.end, b.label)] += 1
    exact = sum(min(cnt_a[k], cnt_b[k]) for k in cnt_a.keys() & cnt_b.keys())

    # relaxed：非零长度、区间不同、重叠且同标签的贪心配对
    a_norm = sorted((a for a in a_list if not a.is_zero_length),
                    key=lambda x: (x.start, x.end))
    b_norm = sorted((b for b in b_list if not b.is_zero_length),
                    key=lambda x: (x.start, x.end))
    used_b = [False] * len(b_norm)
    relaxed_extra = 0
    j = 0
    for a in a_norm:
        while j < len(b_norm) and b_norm[j].end <= a.start:
            j += 1
        k = j
        best = -1
        while k < len(b_norm) and b_norm[k].start < a.end:
            b = b_norm[k]
            if not used_b[k] and b.label == a.label and \
               (b.start, b.end) != (a.start, a.end):
                best = k
                break
            k += 1
        if best >= 0:
            used_b[best] = True
            relaxed_extra += 1
    return exact, exact + relaxed_extra


def check_consistency(annotations, items_text=None):
    """主入口。annotations: list[Annotation]；items_text: {item_id: text} 可选。"""
    items_text = items_text or {}
    by_item = defaultdict(lambda: defaultdict(list))
    annotator_set = set()
    zero_length = []
    for a in annotations:
        by_item[a.item_id][a.annotator].append(a)
        annotator_set.add(a.annotator)
        if a.is_zero_length:
            zero_length.append(a)

    conflicts = []
    # 每个 (item, 标注者对) 的配对统计：pair -> [n_a, n_b, exact, relaxed]
    pair_stats = defaultdict(lambda: [0, 0, 0, 0])

    for item_id, per_annotator in by_item.items():
        names = sorted(per_annotator)
        # 零长度区间之间的精确比较（同位置不同标签 -> LABEL_CONFLICT）
        zero_by_pos = defaultdict(list)
        for name in names:
            for a in per_annotator[name]:
                if a.is_zero_length:
                    zero_by_pos[a.start].append(a)
        for pos, group in zero_by_pos.items():
            for i in range(len(group)):
                for k in range(i + 1, len(group)):
                    x, y = group[i], group[k]
                    if x.annotator != y.annotator and x.label != y.label:
                        conflicts.append(Conflict(
                            item_id, LABEL_CONFLICT, pos, pos,
                            [asdict(x), asdict(y)],
                            _snippet(items_text[item_id], pos, pos)
                            if item_id in items_text else ""))

        for i in range(len(names)):
            for k in range(i + 1, len(names)):
                na, nb = names[i], names[k]
                la = sorted(per_annotator[na], key=lambda x: (x.start, x.end))
                lb = sorted(per_annotator[nb], key=lambda x: (x.start, x.end))
                la_norm = [a for a in la if not a.is_zero_length]
                lb_norm = [b for b in lb if not b.is_zero_length]
                _pair_conflicts(item_id, la_norm, lb_norm, na, nb,
                                items_text, conflicts)
                exact, relaxed = _count_agreements(la, lb)
                st = pair_stats[(na, nb)]
                st[0] += len(la)
                st[1] += len(lb)
                st[2] += exact
                st[3] += relaxed

    pairwise = []
    for (na, nb), (ca, cb, exact, relaxed) in sorted(pair_stats.items()):
        denom = ca + cb
        pairwise.append(PairAgreement(
            na, nb, ca, cb, exact, relaxed,
            strict=2 * exact / denom if denom else 1.0,
            relaxed=2 * relaxed / denom if denom else 1.0))

    strict_overall = (sum(p.strict for p in pairwise) / len(pairwise)
                      if pairwise else 1.0)
    relaxed_overall = (sum(p.relaxed for p in pairwise) / len(pairwise)
                       if pairwise else 1.0)

    conflicts.sort(key=lambda c: (c.item_id, c.char_start, c.char_end, c.kind))
    return Report(
        total_annotations=len(annotations),
        total_items=len(by_item),
        annotators=sorted(annotator_set),
        conflicts=conflicts,
        zero_length=zero_length,
        pairwise=pairwise,
        strict_overall=strict_overall,
        relaxed_overall=relaxed_overall,
    )


def format_report(report):
    lines = []
    lines.append(f"标注总数: {report.total_annotations}  条目数: {report.total_items}  "
                 f"标注者: {', '.join(report.annotators)}")
    lines.append(f"冲突总数: {len(report.conflicts)}  "
                 f"零长度异常: {len(report.zero_length)}")
    by_kind = defaultdict(int)
    for c in report.conflicts:
        by_kind[c.kind] += 1
    for kind in sorted(by_kind):
        lines.append(f"  {kind}: {by_kind[kind]}")
    lines.append("一致率（严格=区间+标签完全一致；宽松=允许边界偏移的重叠同标签）:")
    for p in report.pairwise:
        lines.append(f"  {p.annotator_a} vs {p.annotator_b}: "
                     f"strict={p.strict:.4f} relaxed={p.relaxed:.4f} "
                     f"(n={p.n_a}/{p.n_b}, exact={p.exact_matches}, "
                     f"relaxed={p.relaxed_matches})")
    lines.append(f"整体: strict={report.strict_overall:.4f} "
                 f"relaxed={report.relaxed_overall:.4f}")
    return "\n".join(lines)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="标注一致性核对")
    ap.add_argument("data", help="输入 JSON：{items?, annotations[]}")
    ap.add_argument("--conflicts", help="冲突清单输出 JSON 路径")
    args = ap.parse_args(argv)

    anns, items = load_annotations(args.data)
    report = check_consistency(anns, items)
    print(format_report(report))
    if args.conflicts:
        with open(args.conflicts, "w", encoding="utf-8") as f:
            json.dump([asdict(c) for c in report.conflicts], f,
                      ensure_ascii=False, indent=2)
        print(f"冲突清单已写入 {args.conflicts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

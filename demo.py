#!/usr/bin/env python3
"""演示：构造覆盖全部情形的标注数据，输出冲突清单与一致率。

运行：python3 demo.py
产物：outputs/conflicts.csv、outputs/summary.json
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from annotation_consistency import (
    Annotation,
    check_consistency,
)
from annotation_consistency.metrics import all_pairwise_metrics
from annotation_consistency.report import (
    format_conflicts_table,
    format_metrics,
    write_conflicts_csv,
)

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")


def build_sample_data():
    """三名标注者 alice/bob/carol 的演示数据。

    每篇文档长度见 doc_lengths（供字符级 Kappa 使用）。
    """
    rows = []
    add = lambda d, s, e, label, who: rows.append(
        Annotation(d, s, e, label, who)
    )

    # doc-001：完全一致
    add("doc-001", 0, 5, "PER", "alice")
    add("doc-001", 0, 5, "PER", "bob")
    add("doc-001", 0, 5, "PER", "carol")
    add("doc-001", 10, 16, "ORG", "alice")
    add("doc-001", 10, 16, "ORG", "bob")
    add("doc-001", 10, 16, "ORG", "carol")

    # doc-002：区间相同、标签冲突
    add("doc-002", 2, 9, "PER", "alice")
    add("doc-002", 2, 9, "ORG", "bob")
    add("doc-002", 2, 9, "PER", "carol")

    # doc-003：区间部分重叠、标签相同（边界分歧）
    add("doc-003", 0, 12, "LOC", "alice")
    add("doc-003", 8, 18, "LOC", "bob")
    add("doc-003", 0, 12, "LOC", "carol")

    # doc-004：区间部分重叠 + 标签不同（双重冲突）
    add("doc-004", 30, 42, "PER", "alice")
    add("doc-004", 40, 50, "ORG", "bob")

    # doc-005：相邻区间不算重叠；且不相邻的不相关区间不配对
    add("doc-005", 0, 10, "PER", "alice")
    add("doc-005", 10, 20, "PER", "bob")
    add("doc-005", 40, 50, "ORG", "alice")
    add("doc-005", 60, 70, "ORG", "bob")

    # doc-006：零长度点标注（同点位同标签=一致；同点位不同标签=冲突；
    #          点位落在对方区间内=不冲突；不同点位=不冲突）
    add("doc-006", 7, 7, "MARK", "alice")
    add("doc-006", 7, 7, "MARK", "bob")
    add("doc-006", 20, 20, "S", "alice")
    add("doc-006", 20, 20, "E", "bob")
    add("doc-006", 33, 33, "MARK", "alice")
    add("doc-006", 30, 40, "PER", "bob")
    add("doc-006", 50, 50, "MARK", "alice")
    add("doc-006", 51, 51, "MARK", "bob")

    # doc-007：包含关系（大区间 vs 内部小区间，标签相同 -> 边界分歧）
    add("doc-007", 0, 100, "EVT", "alice")
    add("doc-007", 20, 30, "EVT", "bob")

    doc_lengths = {
        "doc-001": 60, "doc-002": 60, "doc-003": 60, "doc-004": 80,
        "doc-005": 100, "doc-006": 80, "doc-007": 120,
    }
    return rows, doc_lengths


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    data, doc_lengths = build_sample_data()
    result = check_consistency(data)

    print(f"标注总数: {len(data)}  文档数: {len(result.docs)}  标注者: {result.annotators}")
    print()

    type_counts = {}
    for c in result.conflicts:
        type_counts[c.conflict_type.value] = type_counts.get(c.conflict_type.value, 0) + 1
    print("冲突分类计数:", json.dumps(type_counts, ensure_ascii=False, sort_keys=True))
    print(f"冲突总数: {len(result.conflicts)}")
    print()
    print("可返工冲突清单（精确定位到条目与字符区间）")
    print(format_conflicts_table(result.conflicts))
    print()

    metrics = all_pairwise_metrics(data, doc_lengths)
    print("标注者间一致率")
    print(format_metrics(metrics))

    csv_path = os.path.join(OUT_DIR, "conflicts.csv")
    write_conflicts_csv(result.conflicts, csv_path)

    summary = {
        "annotation_count": len(data),
        "doc_count": len(result.docs),
        "annotators": result.annotators,
        "conflict_count": len(result.conflicts),
        "conflict_counts_by_type": type_counts,
        "pairwise_metrics": [
            {
                "pair": [m.annotator_a, m.annotator_b],
                "exact_span_label_f1": round(m.exact_span_label.f1, 4),
                "exact_span_label_precision": round(m.exact_span_label.precision, 4),
                "exact_span_label_recall": round(m.exact_span_label.recall, 4),
                "exact_span_only_f1": round(m.exact_span_only.f1, 4),
                "lenient_overlap_f1": round(m.lenient_span_label.f1, 4),
                "label_accuracy_on_exact_spans": (
                    None if m.label_accuracy_on_exact_spans is None
                    else round(m.label_accuracy_on_exact_spans, 4)
                ),
                "cohens_kappa_chars": (
                    None if m.cohens_kappa_chars is None
                    else round(m.cohens_kappa_chars, 4)
                ),
                "char_positions": m.char_positions,
            }
            for m in metrics
        ],
    }
    with open(os.path.join(OUT_DIR, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(f"\n已写出: {csv_path}")
    print(f"已写出: {os.path.join(OUT_DIR, 'summary.json')}")


if __name__ == "__main__":
    main()

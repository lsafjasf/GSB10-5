"""冲突清单与一致率报告的格式化输出。"""

from __future__ import annotations

import csv
import io
from typing import Iterable

from .core import Conflict
from .metrics import PairwiseMetrics

CSV_HEADER = [
    "doc_id",
    "conflict_type",
    "severity",
    "char_range",
    "annotator_a",
    "span_a",
    "label_a",
    "annotator_b",
    "span_b",
    "label_b",
    "overlap_range",
]


def conflicts_to_rows(conflicts: Iterable[Conflict]) -> list[list[str]]:
    """把冲突清单转成表格行，每行精确定位到条目与字符区间。"""
    rows = []
    for c in conflicts:
        rows.append([
            c.doc_id,
            c.conflict_type.value,
            c.conflict_type.severity,
            f"[{c.union_start},{c.union_end})",
            c.annotator_a,
            f"[{c.start_a},{c.end_a})",
            c.label_a,
            c.annotator_b,
            f"[{c.start_b},{c.end_b})",
            c.label_b,
            f"[{c.overlap_start},{c.overlap_end})",
        ])
    return rows


def conflicts_to_csv_text(conflicts: Iterable[Conflict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_HEADER)
    writer.writerows(conflicts_to_rows(conflicts))
    return buf.getvalue()


def write_conflicts_csv(conflicts: Iterable[Conflict], path: str) -> int:
    """写出冲突清单 CSV，返回冲突条数。"""
    rows = conflicts_to_rows(conflicts)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)
    return len(rows)


def format_conflicts_table(conflicts: list[Conflict], limit: int | None = None) -> str:
    """生成便于人工核对的等宽文本表。"""
    header = ["doc_id", "type", "range", "A", "span_A", "label_A",
              "B", "span_B", "label_B"]
    rows = []
    for c in conflicts[: limit if limit is not None else len(conflicts)]:
        rows.append([
            c.doc_id, c.conflict_type.value, c.char_range.split(":", 1)[1],
            c.annotator_a, f"[{c.start_a},{c.end_a})", c.label_a,
            c.annotator_b, f"[{c.start_b},{c.end_b})", c.label_b,
        ])
    widths = [len(h) for h in header]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(header))
    sep = "  ".join("-" * w for w in widths)
    body = ["  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))
            for row in rows]
    if limit is not None and len(conflicts) > limit:
        body.append(f"... 其余 {len(conflicts) - limit} 条见 CSV 清单")
    return "\n".join([line, sep, *body])


def format_metrics(metrics: list[PairwiseMetrics]) -> str:
    """格式化两两标注者一致率。"""
    out = []
    for m in metrics:
        def pct(x: float | None) -> str:
            return "  n/a " if x is None else f"{x * 100:6.2f}%"

        out.append(
            f"{m.annotator_a} vs {m.annotator_b}:\n"
            f"  严格口径 区间+标签精确匹配 F1 : {pct(m.exact_span_label.f1)}"
            f"  (P={pct(m.exact_span_label.precision)} R={pct(m.exact_span_label.recall)})\n"
            f"  严格口径 仅区间精确匹配   F1 : {pct(m.exact_span_only.f1)}\n"
            f"  宽松口径 重叠即匹配(同标签) F1 : {pct(m.lenient_span_label.f1)}\n"
            f"  共同精确区间上的标签一致率   : {pct(m.label_accuracy_on_exact_spans)}\n"
            f"  字符级 Cohen's Kappa        : {pct(m.cohens_kappa_chars)}"
            f"  (字符位置数={m.char_positions})"
        )
    return "\n".join(out)

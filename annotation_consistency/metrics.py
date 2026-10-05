"""标注者间一致率指标。

提供两种口径，结果不可直接互换，报告时必须注明口径：

1. **区间级精确匹配 F1（严格口径，主指标）**
   把每个 ``(doc_id, start, end[, label])`` 跨度视为一个集合元素，
   对两名标注者计算 Precision / Recall / F1（Dice）。
   边界差一个字符就算不一致，最严格，最适合驱动返工。

2. **字符级 Cohen's Kappa（宽松口径，参考指标）**
   把文档每个字符位置当作一个分类样本（未覆盖位置记为 ``<NONE>``），
   计算 Cohen's Kappa。部分重叠会被部分计入，且当标注稀疏时
   大量 ``<NONE>`` 位置会**抬高**一致率，需与口径 1 对照阅读。

此外给出"宽松区间匹配 F1"：只要区间存在正长度重叠即算匹配
（贪心一对一），用于衡量"找到同一批实体"的能力，不看边界是否精确。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .core import Annotation, spans_overlap

NONE_LABEL = "<NONE>"


@dataclass(frozen=True)
class PRF:
    precision: float
    recall: float
    f1: float
    support: int  # 双方标注总数（|A| + |B|）


@dataclass(frozen=True)
class PairwiseMetrics:
    annotator_a: str
    annotator_b: str
    exact_span_label: PRF
    """严格口径：区间 + 标签完全一致才算命中。"""
    exact_span_only: PRF
    """区间完全一致即命中，不看标签。"""
    lenient_span_label: PRF
    """宽松口径：正长度重叠且标签相同，贪心一对一匹配。"""
    label_accuracy_on_exact_spans: float | None
    """在区间完全一致的标注中，标签也一致的比例；无共同区间时为 None。"""
    cohens_kappa_chars: float | None
    """字符级 Cohen's Kappa；某一方所有字符标签恒定时无法定义，为 None。"""
    char_positions: int
    """参与 Kappa 计算的字符位置总数。"""


def _prf(hits: int, total_a: int, total_b: int) -> PRF:
    precision = hits / total_a if total_a else 0.0
    recall = hits / total_b if total_b else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return PRF(precision=precision, recall=recall, f1=f1, support=total_a + total_b)


def _multiset_exact_hits(
    spans_a: list[Annotation], spans_b: list[Annotation], with_label: bool
) -> int:
    def key(ann: Annotation):
        base = (ann.doc_id, ann.start, ann.end)
        return base + (ann.label,) if with_label else base

    counter = Counter(key(a) for a in spans_a)
    hits = 0
    for b in spans_b:
        k = key(b)
        if counter.get(k, 0) > 0:
            counter[k] -= 1
            hits += 1
    return hits


def _lenient_hits(
    spans_a: list[Annotation], spans_b: list[Annotation]
) -> int:
    """正长度重叠且标签相同的贪心一对一匹配数（按文档分组后扫描）。"""
    by_doc_a: dict[str, list[Annotation]] = defaultdict(list)
    by_doc_b: dict[str, list[Annotation]] = defaultdict(list)
    for a in spans_a:
        by_doc_a[a.doc_id].append(a)
    for b in spans_b:
        by_doc_b[b.doc_id].append(b)

    hits = 0
    for doc_id, list_a in by_doc_a.items():
        candidates = sorted(
            (b for b in by_doc_b.get(doc_id, ())),
            key=lambda b: (b.start, b.end),
        )
        used = [False] * len(candidates)
        for a in sorted(list_a, key=lambda a: (a.start, a.end)):
            for idx, b in enumerate(candidates):
                if used[idx] or b.label != a.label:
                    continue
                if spans_overlap(a, b):
                    used[idx] = True
                    hits += 1
                    break
    return hits


def _char_labels(
    doc_spans: list[Annotation], doc_len: int
) -> list[str]:
    """把跨度展开为逐字符标签数组；重叠时后写的覆盖先写的（仅用于 Kappa）。"""
    labels = [NONE_LABEL] * doc_len
    for span in doc_spans:
        for pos in range(span.start, min(span.end, doc_len)):
            labels[pos] = span.label
    return labels


def cohens_kappa_chars(
    spans_a: list[Annotation],
    spans_b: list[Annotation],
    doc_lengths: dict[str, int] | None = None,
) -> tuple[float | None, int]:
    """字符级 Cohen's Kappa。

    ``doc_lengths`` 给出每篇文档的字符数；缺省时用双方标注的最大 ``end``
    作为文档长度（会**低估**未标注区域，从而进一步抬高 Kappa，报告中需注明）。
    返回 ``(kappa, 字符位置总数)``；当期望一致率 pe == 1（无法定义）时 kappa 为 None。
    """
    doc_ids = {a.doc_id for a in spans_a} | {b.doc_id for b in spans_b}
    by_doc_a: dict[str, list[Annotation]] = defaultdict(list)
    by_doc_b: dict[str, list[Annotation]] = defaultdict(list)
    for a in spans_a:
        by_doc_a[a.doc_id].append(a)
    for b in spans_b:
        by_doc_b[b.doc_id].append(b)

    agree = 0
    total = 0
    count_a: Counter[str] = Counter()
    count_b: Counter[str] = Counter()
    for doc_id in doc_ids:
        if doc_lengths and doc_id in doc_lengths:
            doc_len = doc_lengths[doc_id]
        else:
            doc_len = max(
                [0] + [s.end for s in by_doc_a.get(doc_id, ())]
                + [s.end for s in by_doc_b.get(doc_id, ())]
            )
        labels_a = _char_labels(by_doc_a.get(doc_id, []), doc_len)
        labels_b = _char_labels(by_doc_b.get(doc_id, []), doc_len)
        total += doc_len
        count_a.update(labels_a)
        count_b.update(labels_b)
        agree += sum(1 for x, y in zip(labels_a, labels_b) if x == y)

    if total == 0:
        return None, 0
    po = agree / total
    pe = sum((count_a[k] / total) * (count_b[k] / total) for k in count_a.keys() | count_b.keys())
    if pe >= 1.0:
        return None, total
    return (po - pe) / (1 - pe), total


def pairwise_metrics(
    spans_a: list[Annotation],
    spans_b: list[Annotation],
    name_a: str = "A",
    name_b: str = "B",
    doc_lengths: dict[str, int] | None = None,
) -> PairwiseMetrics:
    """计算两名标注者之间的全部一致率指标。"""
    exact_label_hits = _multiset_exact_hits(spans_a, spans_b, with_label=True)
    exact_span_hits = _multiset_exact_hits(spans_a, spans_b, with_label=False)
    lenient_hits = _lenient_hits(spans_a, spans_b)
    kappa, char_positions = cohens_kappa_chars(spans_a, spans_b, doc_lengths)

    label_accuracy = (
        exact_label_hits / exact_span_hits if exact_span_hits else None
    )
    return PairwiseMetrics(
        annotator_a=name_a,
        annotator_b=name_b,
        exact_span_label=_prf(exact_label_hits, len(spans_a), len(spans_b)),
        exact_span_only=_prf(exact_span_hits, len(spans_a), len(spans_b)),
        lenient_span_label=_prf(lenient_hits, len(spans_a), len(spans_b)),
        label_accuracy_on_exact_spans=label_accuracy,
        cohens_kappa_chars=kappa,
        char_positions=char_positions,
    )


def all_pairwise_metrics(
    annotations: list[Annotation],
    doc_lengths: dict[str, int] | None = None,
) -> list[PairwiseMetrics]:
    """对数据中出现的所有标注者两两计算一致率。"""
    by_annotator: dict[str, list[Annotation]] = defaultdict(list)
    for ann in annotations:
        by_annotator[ann.annotator].append(ann)
    names = sorted(by_annotator)
    results = []
    for i, name_a in enumerate(names):
        for name_b in names[i + 1:]:
            results.append(
                pairwise_metrics(
                    by_annotator[name_a], by_annotator[name_b],
                    name_a, name_b, doc_lengths,
                )
            )
    return results

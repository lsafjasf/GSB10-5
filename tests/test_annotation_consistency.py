"""annotation_consistency 自测（标准库 unittest）。

运行：python3 -m unittest discover -s tests -v
"""

import unittest

from annotation_consistency import (
    Annotation,
    ConflictType,
    check_consistency,
    classify,
    overlap_length,
    pairwise_metrics,
    spans_overlap,
)


def ann(doc, start, end, label, annotator):
    return Annotation(doc_id=doc, start=start, end=end, label=label, annotator=annotator)


class TestIntervalRules(unittest.TestCase):
    """区间模型与边界情况的确定性结论。"""

    def test_overlap_length(self):
        self.assertEqual(overlap_length(0, 10, 5, 15), 5)
        self.assertEqual(overlap_length(0, 10, 10, 20), 0)   # 相邻
        self.assertEqual(overlap_length(0, 10, 20, 30), 0)   # 不相交
        self.assertEqual(overlap_length(2, 8, 0, 20), 6)     # 包含

    def test_adjacent_spans_do_not_overlap(self):
        a = ann("d", 0, 5, "X", "u1")
        b = ann("d", 5, 9, "X", "u2")
        self.assertFalse(spans_overlap(a, b))
        self.assertFalse(spans_overlap(b, a))
        self.assertIsNone(classify(a, b))

    def test_zero_length_never_overlaps_span(self):
        point = ann("d", 3, 3, "X", "u1")
        covering = ann("d", 0, 10, "X", "u2")
        self.assertFalse(spans_overlap(point, covering))
        self.assertIsNone(classify(point, covering))

    def test_zero_length_points_at_different_positions(self):
        p1 = ann("d", 3, 3, "X", "u1")
        p2 = ann("d", 4, 4, "X", "u2")
        self.assertIsNone(classify(p1, p2))

    def test_invalid_annotation_rejected(self):
        with self.assertRaises(ValueError):
            ann("d", 5, 3, "X", "u1")   # start > end
        with self.assertRaises(ValueError):
            ann("d", -1, 3, "X", "u1")  # 负坐标
        with self.assertRaises(ValueError):
            ann("d", 0, 3, "", "u1")    # 空标签


class TestClassification(unittest.TestCase):
    """完全一致 / 区间重叠 / 标签冲突 / 零长度 四类情形。"""

    def test_exact_match(self):
        a = ann("d", 2, 7, "PER", "u1")
        b = ann("d", 2, 7, "PER", "u2")
        self.assertIs(classify(a, b), ConflictType.EXACT_MATCH)

    def test_label_conflict_same_span(self):
        a = ann("d", 2, 7, "PER", "u1")
        b = ann("d", 2, 7, "ORG", "u2")
        self.assertIs(classify(a, b), ConflictType.LABEL_CONFLICT)

    def test_span_overlap_same_label(self):
        a = ann("d", 2, 7, "PER", "u1")
        b = ann("d", 5, 9, "PER", "u2")
        self.assertIs(classify(a, b), ConflictType.SPAN_OVERLAP)

    def test_span_overlap_diff_label(self):
        a = ann("d", 2, 7, "PER", "u1")
        b = ann("d", 5, 9, "ORG", "u2")
        self.assertIs(classify(a, b), ConflictType.SPAN_LABEL_CONFLICT)

    def test_containment_is_overlap(self):
        a = ann("d", 0, 20, "PER", "u1")
        b = ann("d", 3, 8, "PER", "u2")
        self.assertIs(classify(a, b), ConflictType.SPAN_OVERLAP)

    def test_zero_length_point_exact_match(self):
        p1 = ann("d", 4, 4, "CURSOR", "u1")
        p2 = ann("d", 4, 4, "CURSOR", "u2")
        self.assertIs(classify(p1, p2), ConflictType.EXACT_MATCH)

    def test_zero_length_point_label_conflict(self):
        p1 = ann("d", 4, 4, "START", "u1")
        p2 = ann("d", 4, 4, "END", "u2")
        self.assertIs(classify(p1, p2), ConflictType.LABEL_CONFLICT)


class TestCheckConsistency(unittest.TestCase):
    def setUp(self):
        self.data = [
            # doc1：完全一致（不报冲突）
            ann("doc1", 0, 5, "PER", "alice"),
            ann("doc1", 0, 5, "PER", "bob"),
            # doc1：同区间不同标签
            ann("doc1", 10, 14, "PER", "alice"),
            ann("doc1", 10, 14, "ORG", "bob"),
            # doc2：部分重叠同标签
            ann("doc2", 0, 10, "LOC", "alice"),
            ann("doc2", 6, 15, "LOC", "bob"),
            # doc2：部分重叠不同标签
            ann("doc2", 20, 30, "PER", "alice"),
            ann("doc2", 25, 35, "ORG", "bob"),
            # doc3：相邻区间（不算冲突）
            ann("doc3", 0, 5, "PER", "alice"),
            ann("doc3", 5, 9, "PER", "bob"),
            # doc3：零长度点标注，同点位同标签（一致）
            ann("doc3", 12, 12, "MARK", "alice"),
            ann("doc3", 12, 12, "MARK", "bob"),
            # doc3：零长度点标注，同点位不同标签
            ann("doc3", 20, 20, "S", "alice"),
            ann("doc3", 20, 20, "E", "bob"),
            # doc3：零长度点落在对方区间内（不算冲突）
            ann("doc3", 30, 30, "MARK", "alice"),
            ann("doc3", 25, 40, "PER", "bob"),
        ]

    def test_conflict_counts_by_type(self):
        result = check_consistency(self.data)
        counts = {}
        for c in result.conflicts:
            counts[c.conflict_type] = counts.get(c.conflict_type, 0) + 1
        self.assertEqual(counts.get(ConflictType.LABEL_CONFLICT), 2)       # doc1 区间 + doc3 点
        self.assertEqual(counts.get(ConflictType.SPAN_OVERLAP), 1)
        self.assertEqual(counts.get(ConflictType.SPAN_LABEL_CONFLICT), 1)
        self.assertEqual(len(result.conflicts), 4)

    def test_conflicts_pinpoint_doc_and_range(self):
        result = check_consistency(self.data)
        key = {(c.doc_id, c.union_start, c.union_end, c.conflict_type) for c in result.conflicts}
        self.assertIn(("doc1", 10, 14, ConflictType.LABEL_CONFLICT), key)
        self.assertIn(("doc2", 0, 15, ConflictType.SPAN_OVERLAP), key)
        self.assertIn(("doc2", 20, 35, ConflictType.SPAN_LABEL_CONFLICT), key)
        self.assertIn(("doc3", 20, 20, ConflictType.LABEL_CONFLICT), key)

    def test_no_conflict_cases_stay_silent(self):
        result = check_consistency(self.data)
        # doc3 的相邻区间 [0,5)/[5,9) 与 点[30,30)/区间[25,40) 不应出现
        for c in result.conflicts:
            self.assertFalse(c.doc_id == "doc3" and c.union_start in (0, 25, 30))

    def test_exact_matches_not_in_conflicts(self):
        result = check_consistency(self.data)
        self.assertTrue(all(c.conflict_type.is_conflict for c in result.conflicts))
        exact = [r for r in result.pair_records
                 if r.conflict_type is ConflictType.EXACT_MATCH]
        self.assertEqual(len(exact), 2)  # doc1 [0,5) + doc3 点 [12,12)

    def test_three_annotators_pairwise(self):
        data = [
            ann("d", 0, 5, "X", "a"),
            ann("d", 0, 5, "Y", "b"),
            ann("d", 0, 5, "Z", "c"),
        ]
        result = check_consistency(data)
        # a-b、a-c、b-c 三对，每对一个标签冲突
        self.assertEqual(len(result.conflicts), 3)
        pairs = {(c.annotator_a, c.annotator_b) for c in result.conflicts}
        self.assertEqual(pairs, {("a", "b"), ("a", "c"), ("b", "c")})

    def test_duplicate_annotation_from_same_annotator_ignored(self):
        data = [
            ann("d", 0, 5, "X", "a"),
            ann("d", 0, 5, "X", "a"),  # 同人重复提交
            ann("d", 0, 5, "X", "b"),
        ]
        result = check_consistency(data)
        self.assertEqual(len(result.conflicts), 0)


class TestMetrics(unittest.TestCase):
    def test_perfect_agreement(self):
        a = [ann("d", 0, 5, "X", "u1"), ann("d", 10, 12, "Y", "u1")]
        b = [ann("d", 0, 5, "X", "u2"), ann("d", 10, 12, "Y", "u2")]
        m = pairwise_metrics(a, b, "u1", "u2", doc_lengths={"d": 20})
        self.assertEqual(m.exact_span_label.f1, 1.0)
        self.assertEqual(m.cohens_kappa_chars, 1.0)

    def test_total_disagreement(self):
        a = [ann("d", 0, 5, "X", "u1")]
        b = [ann("d", 10, 15, "Y", "u2")]
        m = pairwise_metrics(a, b, "u1", "u2", doc_lengths={"d": 20})
        self.assertEqual(m.exact_span_label.f1, 0.0)
        self.assertLess(m.cohens_kappa_chars, 0.2)

    def test_partial_overlap_lenient_vs_strict(self):
        a = [ann("d", 0, 10, "X", "u1")]
        b = [ann("d", 5, 15, "X", "u2")]
        m = pairwise_metrics(a, b, "u1", "u2", doc_lengths={"d": 40})
        self.assertEqual(m.exact_span_label.f1, 0.0)   # 严格口径不认
        self.assertEqual(m.lenient_span_label.f1, 1.0)  # 宽松口径认
        self.assertGreater(m.cohens_kappa_chars, 0.0)   # 字符级部分计入

    def test_label_accuracy_on_exact_spans(self):
        a = [ann("d", 0, 5, "X", "u1"), ann("d", 8, 9, "Y", "u1")]
        b = [ann("d", 0, 5, "X", "u2"), ann("d", 8, 9, "Z", "u2")]
        m = pairwise_metrics(a, b)
        self.assertEqual(m.label_accuracy_on_exact_spans, 0.5)


if __name__ == "__main__":
    unittest.main()

"""consistency 库的自测（标准库 unittest）。"""
import unittest

from consistency import (
    Annotation, check_consistency,
    LABEL_CONFLICT, OVERLAP_SAME_LABEL, OVERLAP_DIFF_LABEL,
)


def A(item, who, s, e, label):
    return Annotation(item, who, s, e, label)


def kinds(report):
    return sorted(c.kind for c in report.conflicts)


class TestExactMatch(unittest.TestCase):
    def test_fully_identical_no_conflict(self):
        anns = [A("d1", "x", 0, 5, "PER"), A("d1", "y", 0, 5, "PER")]
        r = check_consistency(anns)
        self.assertEqual(r.conflicts, [])
        self.assertEqual(len(r.pairwise), 1)
        self.assertAlmostEqual(r.pairwise[0].strict, 1.0)
        self.assertAlmostEqual(r.pairwise[0].relaxed, 1.0)

    def test_disjoint_no_conflict(self):
        anns = [A("d1", "x", 0, 5, "PER"), A("d1", "y", 10, 15, "LOC")]
        r = check_consistency(anns)
        self.assertEqual(r.conflicts, [])
        self.assertAlmostEqual(r.pairwise[0].strict, 0.0)


class TestOverlap(unittest.TestCase):
    def test_partial_overlap_same_label(self):
        anns = [A("d1", "x", 0, 6, "PER"), A("d1", "y", 3, 9, "PER")]
        r = check_consistency(anns)
        self.assertEqual(kinds(r), [OVERLAP_SAME_LABEL])
        c = r.conflicts[0]
        self.assertEqual((c.item_id, c.char_start, c.char_end), ("d1", 3, 6))
        self.assertEqual({a["annotator"] for a in c.annotators}, {"x", "y"})

    def test_partial_overlap_diff_label(self):
        anns = [A("d1", "x", 0, 6, "PER"), A("d1", "y", 3, 9, "LOC")]
        r = check_consistency(anns)
        self.assertEqual(kinds(r), [OVERLAP_DIFF_LABEL])
        self.assertEqual((r.conflicts[0].char_start, r.conflicts[0].char_end), (3, 6))

    def test_containment_is_overlap(self):
        anns = [A("d1", "x", 0, 10, "PER"), A("d1", "y", 2, 4, "PER")]
        r = check_consistency(anns)
        self.assertEqual(kinds(r), [OVERLAP_SAME_LABEL])
        self.assertEqual((r.conflicts[0].char_start, r.conflicts[0].char_end), (2, 4))

    def test_adjacent_not_overlap(self):
        # 边界结论：半开区间，e1 == s2 不算重叠
        anns = [A("d1", "x", 0, 5, "PER"), A("d1", "y", 5, 9, "PER")]
        r = check_consistency(anns)
        self.assertEqual(r.conflicts, [])

    def test_relaxed_counts_overlap_same_label(self):
        anns = [A("d1", "x", 0, 6, "PER"), A("d1", "y", 3, 9, "PER")]
        r = check_consistency(anns)
        p = r.pairwise[0]
        self.assertEqual(p.exact_matches, 0)
        self.assertEqual(p.relaxed_matches, 1)
        self.assertAlmostEqual(p.strict, 0.0)
        self.assertAlmostEqual(p.relaxed, 1.0)


class TestLabelConflict(unittest.TestCase):
    def test_same_span_diff_label(self):
        anns = [A("d1", "x", 2, 7, "PER"), A("d1", "y", 2, 7, "ORG")]
        r = check_consistency(anns)
        self.assertEqual(kinds(r), [LABEL_CONFLICT])
        c = r.conflicts[0]
        self.assertEqual((c.char_start, c.char_end), (2, 7))
        self.assertAlmostEqual(r.pairwise[0].strict, 0.0)


class TestZeroLength(unittest.TestCase):
    def test_zero_length_reported_not_conflict(self):
        anns = [A("d1", "x", 3, 3, "PER"), A("d1", "y", 0, 5, "PER")]
        r = check_consistency(anns)
        # 零长度区间落在他人区间内部也不算重叠
        self.assertEqual(r.conflicts, [])
        self.assertEqual(len(r.zero_length), 1)

    def test_zero_length_same_pos_diff_label_is_label_conflict(self):
        anns = [A("d1", "x", 3, 3, "PER"), A("d1", "y", 3, 3, "ORG")]
        r = check_consistency(anns)
        self.assertEqual(kinds(r), [LABEL_CONFLICT])
        self.assertEqual((r.conflicts[0].char_start, r.conflicts[0].char_end), (3, 3))

    def test_zero_length_same_pos_same_label_agrees(self):
        anns = [A("d1", "x", 3, 3, "PER"), A("d1", "y", 3, 3, "PER")]
        r = check_consistency(anns)
        self.assertEqual(r.conflicts, [])
        self.assertAlmostEqual(r.pairwise[0].strict, 1.0)


class TestInvalid(unittest.TestCase):
    def test_start_gt_end_rejected(self):
        with self.assertRaises(ValueError):
            Annotation("d1", "x", 5, 3, "PER")
        with self.assertRaises(ValueError):
            Annotation("d1", "x", -1, 3, "PER")


class TestMultiAnnotator(unittest.TestCase):
    def test_three_annotators_pairwise(self):
        anns = [
            A("d1", "a", 0, 5, "PER"), A("d1", "b", 0, 5, "PER"),
            A("d1", "c", 0, 5, "ORG"),
            A("d2", "a", 1, 4, "LOC"), A("d2", "b", 1, 4, "LOC"),
            A("d2", "c", 1, 4, "LOC"),
        ]
        r = check_consistency(anns)
        self.assertEqual(len(r.pairwise), 3)
        pairs = {(p.annotator_a, p.annotator_b): p for p in r.pairwise}
        self.assertAlmostEqual(pairs[("a", "b")].strict, 1.0)
        self.assertAlmostEqual(pairs[("a", "c")].strict, 0.5)
        # a-c 在 d1 上有一个 LABEL_CONFLICT
        lc = [c for c in r.conflicts if c.kind == LABEL_CONFLICT]
        self.assertEqual(len(lc), 2)  # a-c 与 b-c 各一处
        self.assertTrue(all(c.item_id == "d1" for c in lc))

    def test_snippet_from_items_text(self):
        anns = [A("d1", "x", 2, 4, "PER"), A("d1", "y", 2, 4, "ORG")]
        r = check_consistency(anns, {"d1": "abcdefgh"})
        self.assertEqual(r.conflicts[0].snippet, "abcdefgh")


if __name__ == "__main__":
    unittest.main()

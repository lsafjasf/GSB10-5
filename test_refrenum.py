#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""refrenum 自测（仅标准库 unittest）。

运行: python3 test_refrenum.py -v
"""

import unittest

from refrenum import RefrenumError, parse_document, process, renumber

HEADING = "## 参考文献"


def make_doc(body_lines, entries):
    """entries: [(编号, 文本)]，按给定顺序写入文末列表。"""
    parts = list(body_lines) + ["", HEADING, ""]
    parts += ["[%d] %s" % (n, t) for n, t in entries]
    return "\n".join(parts).encode("utf-8")


class TestSingleCitation(unittest.TestCase):
    """单条引用：一个引用点、一个条目。"""

    def test_single(self):
        doc = make_doc(["结论见 [7]。"], [(7, "条目七.")])
        new_doc, report = process(doc)
        self.assertIn("[1]", new_doc.decode())
        self.assertIn("[1] 条目七.", new_doc.decode())
        self.assertEqual(report["mapping_old_to_new"], {"7": 1})
        self.assertTrue(report["verification"]["ok"])
        self.assertTrue(report["byte_compare"]["body_identical_outside_markers"])


class TestRepeatedCitation(unittest.TestCase):
    """多次重复引用：同一编号出现多次，只占一个新编号。"""

    def test_repeat_same_number(self):
        doc = make_doc(
            ["甲 [5] 乙 [5] 丙 [5]。"],
            [(5, "条目五.")],
        )
        new_doc, report = process(doc)
        text = new_doc.decode()
        self.assertEqual(text.count("[1]"), 4)  # 正文 3 处 + 文末 1 处
        self.assertEqual(report["mapping_old_to_new"], {"5": 1})
        self.assertEqual(report["counts"]["entries_cited"], 1)
        self.assertTrue(report["verification"]["ok"])

    def test_repeat_inside_group(self):
        doc = make_doc(["见 [2,2,2]。"], [(2, "条目二.")])
        new_doc, report = process(doc)
        self.assertIn("[1,1,1]", new_doc.decode())
        self.assertEqual(report["mapping_old_to_new"], {"2": 1})

    def test_first_appearance_order(self):
        # 首次出现顺序 9 -> 4 -> 9 -> 1，新编号应为 9:1, 4:2, 1:3
        doc = make_doc(
            ["a [9] b [4] c [9] d [1]"],
            [(1, "一."), (4, "四."), (9, "九.")],
        )
        new_doc, report = process(doc)
        self.assertEqual(report["mapping_old_to_new"],
                         {"9": 1, "4": 2, "1": 3})
        text = new_doc.decode()
        self.assertIn("a [1] b [2] c [1] d [3]", text)
        self.assertLess(text.index("[1] 九."), text.index("[2] 四."))
        self.assertLess(text.index("[2] 四."), text.index("[3] 一."))


class TestOrphanEntry(unittest.TestCase):
    """孤立条目：条目从未被引用，分类报告且不出现在新列表。"""

    def test_orphan(self):
        doc = make_doc(
            ["只引用 [1]。"],
            [(1, "被引用."), (2, "孤立甲."), (3, "孤立乙.")],
        )
        new_doc, report = process(doc)
        self.assertEqual(report["issues"]["orphan_entries"], [2, 3])
        text = new_doc.decode()
        self.assertIn("[1] 被引用.", text)
        self.assertNotIn("孤立甲.", text)
        self.assertNotIn("孤立乙.", text)
        self.assertTrue(report["verification"]["ok"])


class TestNumberingGap(unittest.TestCase):
    """编号跳号：旧列表中缺失的整数被分类报告。"""

    def test_gaps(self):
        doc = make_doc(
            ["见 [1] 与 [5]。"],
            [(1, "一."), (5, "五.")],
        )
        new_doc, report = process(doc)
        self.assertEqual(report["issues"]["numbering_gaps_in_old_list"], [2, 3, 4])
        self.assertEqual(report["mapping_old_to_new"], {"1": 1, "5": 2})
        # 新列表必须连续无跳号
        self.assertTrue(report["verification"]["ok"])


class TestDanglingCitation(unittest.TestCase):
    """悬空引用：引用了不存在的条目，原样保留并报告。"""

    def test_dangling(self):
        doc = make_doc(
            ["正常 [1]，悬空 [99]。"],
            [(1, "一.")],
        )
        new_doc, report = process(doc)
        self.assertEqual(report["issues"]["dangling_citations"], [99])
        text = new_doc.decode()
        self.assertIn("悬空 [99]", text)  # 原样保留
        self.assertIn("正常 [1]", text)
        self.assertTrue(report["verification"]["ok"])


class TestGroupsAndRanges(unittest.TestCase):
    """组合标记与区间标记。"""

    def test_range_contiguous_stays_range(self):
        doc = make_doc(
            ["见 [3-5]。"],
            [(3, "三."), (4, "四."), (5, "五.")],
        )
        new_doc, report = process(doc)
        self.assertIn("[1-3]", new_doc.decode())
        self.assertEqual(report["mapping_old_to_new"],
                         {"3": 1, "4": 2, "5": 3})

    def test_range_noncontiguous_degrades_to_list(self):
        # 先 [1,3]：1->1, 3->2；随后 [1-3] 展开 1,2,3 -> 新编号 1,3,2，不连续
        doc = make_doc(
            ["先 [1,3] 后 [1-3]。"],
            [(1, "一."), (2, "二."), (3, "三.")],
        )
        new_doc, report = process(doc)
        self.assertIn("后 [1,3,2]", new_doc.decode())
        self.assertTrue(report["verification"]["ok"])

    def test_separator_preserved(self):
        doc = make_doc(
            ["见 [2, 4 ,6]。"],
            [(2, "二."), (4, "四."), (6, "六.")],
        )
        new_doc, report = process(doc)
        # 分隔符（逗号与空格）原样保留
        self.assertIn("[1, 2 ,3]", new_doc.decode())

    def test_duplicate_number_in_group_with_range(self):
        # 回归：组内重复数字不得让区间游标错位
        doc = make_doc(
            ["见 [3,3-4]。"],
            [(3, "三."), (4, "四.")],
        )
        new_doc, report = process(doc)
        self.assertIn("[1,1-2]", new_doc.decode())
        self.assertTrue(report["verification"]["ok"])


class TestByteIdenticalBody(unittest.TestCase):
    """重排不得改变正文内容：标记窗口之外逐字节相同。"""

    def test_body_identical_outside_markers(self):
        doc = make_doc(
            ["段一 [10, 2] 含标点，换行", "段二 [2] 与 [10-10]。"],
            [(2, "二."), (10, "十.")],
        )
        new_doc, report = process(doc)
        bc = report["byte_compare"]
        self.assertTrue(bc["body_identical_outside_markers"])
        # 每个 diff 都必须是 marker_renumbered 类型
        for d in bc["diffs"]:
            self.assertEqual(d["type"], "marker_renumbered")
        self.assertEqual(bc["marker_segments_changed"], 2)

    def test_no_citations_body_untouched(self):
        doc = make_doc(["没有任何引用。"], [(1, "孤立.")])
        new_doc, report = process(doc)
        self.assertTrue(report["byte_compare"]["body_identical_outside_markers"])
        self.assertEqual(report["byte_compare"]["marker_segments_changed"], 0)
        self.assertEqual(report["issues"]["orphan_entries"], [1])


class TestParseErrors(unittest.TestCase):
    def test_missing_heading(self):
        with self.assertRaises(RefrenumError):
            parse_document("正文 [1]。".encode())

    def test_duplicate_entry_number(self):
        doc = make_doc(["[1]"], [(1, "甲."), (1, "乙.")])
        with self.assertRaises(RefrenumError):
            parse_document(doc)


class TestEndToEndConsistency(unittest.TestCase):
    """端到端：重排输出再跑一次应为幂等（编号已按序）。"""

    def test_idempotent(self):
        doc = make_doc(
            ["x [8] y [2,8] z [8]"],
            [(2, "二."), (8, "八.")],
        )
        new_doc, report1 = process(doc)
        new_doc2, report2 = process(new_doc)
        self.assertEqual(new_doc, new_doc2)
        self.assertEqual(report1["mapping_old_to_new"], {"8": 1, "2": 2})
        self.assertEqual(report2["mapping_old_to_new"], {"1": 1, "2": 2})
        self.assertTrue(report2["verification"]["ok"])


if __name__ == "__main__":
    unittest.main()

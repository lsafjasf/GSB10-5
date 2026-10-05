"""selftest.py —— toc_lib 自测（python3 -m unittest，仅标准库）。

覆盖：无前置内容、有前置内容（罗马页码 + 正文重起阿拉伯页码）、
超长标题折行对齐、多级标题混排、章末/章首同页取值、标题页底保护。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from toc_lib import (  # noqa: E402
    BuildResult, Heading, LayoutConfig, Paragraph, Section,
    build, check_toc_layout, display_width, to_roman, verify, wrap_display,
)


def cjk_para(lines: int, width: int) -> Paragraph:
    """生成恰好占 lines 行的纯中文段落（每字宽 2）。"""
    return Paragraph("文" * (width // 2 * lines))


class TestPrimitives(unittest.TestCase):
    def test_roman(self):
        self.assertEqual([to_roman(n) for n in (1, 2, 4, 9, 14, 49, 99, 2024)],
                         ["i", "ii", "iv", "ix", "xiv", "xlix", "xcix", "mmxxiv"])

    def test_display_width(self):
        self.assertEqual(display_width("abc"), 3)
        self.assertEqual(display_width("中文"), 4)
        self.assertEqual(display_width("a中b"), 4)

    def test_wrap_display(self):
        self.assertEqual(wrap_display("文文文文", 4), ["文文", "文文"])
        lines = wrap_display("hello world", 5)
        self.assertEqual(lines, ["hello", "world"])
        # 混排：英文词 + 中文，按显示列折行
        self.assertEqual(wrap_display("ab文文", 4), ["ab文", "文"])
        # 超宽词硬切
        self.assertEqual(wrap_display("xxxxxx", 4), ["xxxx", "xx"])


class TestNoFrontMatter(unittest.TestCase):
    """情形一：无前置内容，物理页 == 逻辑页（阿拉伯，从 1 起）。"""

    def setUp(self):
        self.cfg = LayoutConfig(page_height=10, page_width=40)
        self.doc = [
            Section("body", [Heading(1, "第一章 概述"), cjk_para(3, 40),
                             Heading(2, "1.1 背景"), cjk_para(2, 40)]),
            Section("body", [Heading(1, "第二章 方法"), cjk_para(4, 40),
                             Heading(2, "2.1 模型"), cjk_para(2, 40)]),
        ]
        self.res = build(self.doc, self.cfg)

    def test_page_numbers(self):
        r = self.res
        self.assertEqual(r.heading_pages[id(self.doc[0].blocks[0])], (1, "1"))
        # 第一章：标题1行+3行+1.1标题1行+2行 = 7 行，同页
        self.assertEqual(r.heading_pages[id(self.doc[0].blocks[2])], (1, "1"))
        # 第二章强制另起新页
        self.assertEqual(r.heading_pages[id(self.doc[1].blocks[0])], (2, "2"))

    def test_verify_all_match(self):
        self.assertTrue(all(row.ok for row in verify(self.res)))
        check = check_toc_layout(self.res)
        self.assertTrue(check.all_within_width)
        self.assertTrue(check.label_lines_right_aligned)


class TestWithFrontMatter(unittest.TestCase):
    """情形二：有前置内容（扉页 i、目录 ii、前言 iii、正文重起 1）。"""

    def setUp(self):
        self.cfg = LayoutConfig(page_height=12, page_width=40)
        self.title = Section("front", [Paragraph("扉页"), cjk_para(2, 40)],
                             name="扉页")
        self.toc = Section("toc", [], name="目录")
        self.preface = Section("front",
                               [Heading(1, "前言"), cjk_para(3, 40)],
                               include_in_toc=True, name="前言")
        self.body1 = Section("body", [Heading(1, "第一章 概述"),
                                      cjk_para(3, 40),
                                      Heading(2, "1.1 背景"),
                                      cjk_para(2, 40)])
        self.body2 = Section("body", [Heading(1, "第二章 方法"),
                                      cjk_para(2, 40)])
        self.doc = [self.title, self.toc, self.preface,
                    self.body1, self.body2]
        self.res = build(self.doc, self.cfg)

    def test_front_roman_body_restart(self):
        r = self.res
        # 物理页 1=扉页(i)，2=目录(ii)，3=前言(iii)，4=正文第一章(逻辑 1)
        self.assertEqual(r.page_labels[(0, 0)], "i")
        self.assertEqual(r.page_labels[(1, 0)], "ii")
        self.assertEqual(r.page_labels[(2, 0)], "iii")
        self.assertEqual(r.page_labels[(3, 0)], "1")
        self.assertEqual(r.heading_pages[id(self.preface.blocks[0])], (3, "iii"))
        self.assertEqual(r.heading_pages[id(self.body1.blocks[0])], (4, "1"))
        self.assertEqual(r.heading_pages[id(self.body2.blocks[0])], (5, "2"))

    def test_fixpoint_iterated(self):
        # 目录收录了排在其后的「前言」，至少经历 空目录->成稿->稳定 两轮
        self.assertGreaterEqual(self.res.iterations, 2)

    def test_toc_entries_and_verify(self):
        labels = [(e.title, e.label) for e in self.res.entries]
        self.assertIn(("前言", "iii"), labels)
        self.assertIn(("第一章 概述", "1"), labels)
        self.assertIn(("第二章 方法", "2"), labels)
        self.assertTrue(all(row.ok for row in verify(self.res)))
        check = check_toc_layout(self.res)
        self.assertTrue(check.all_within_width)
        self.assertTrue(check.label_lines_right_aligned)


class TestChapterEndStartPage(unittest.TestCase):
    """情形三：章末与章首同页 / 换页时页码取值必须确定。"""

    def _doc(self, one_section=False):
        if one_section:  # 同一正文节内的两章（节间恒分页，同页只发生在节内）
            return [Section("body", [Heading(1, "第一章"), cjk_para(3, 40),
                                     Heading(1, "第二章"), cjk_para(3, 40)])]
        return [
            Section("body", [Heading(1, "第一章"), cjk_para(3, 40)]),
            Section("body", [Heading(1, "第二章"), cjk_para(3, 40)]),
        ]

    def test_default_chapter_starts_new_page(self):
        cfg = LayoutConfig(page_height=20, page_width=40)  # 章末只占 4 行
        res = build(self._doc(), cfg)
        # 第一章与第二章页码
        p1 = res.heading_pages[id(res.sections[0][0].blocks[0])]
        p2 = res.heading_pages[id(res.sections[1][0].blocks[0])]
        self.assertEqual(p1, (1, "1"))
        self.assertEqual(p2, (2, "2"))  # 即使章末只到第 4 行，仍取新页
        # 第一章页只排了 4 行（章末与章首不允许同页）
        self.assertEqual(len(res.sections[0][1][0]), 4)

    def test_no_break_chapter_shares_page(self):
        # 显式关闭强制换页：章首接续在章末之后，同页时页码取该页（确定值 1）
        cfg = LayoutConfig(page_height=20, page_width=40,
                           chapter_break_level=0)
        res = build(self._doc(one_section=True), cfg)
        p1 = res.heading_pages[id(res.sections[0][0].blocks[0])]
        p2 = res.heading_pages[id(res.sections[0][0].blocks[2])]
        self.assertEqual(p1, (1, "1"))
        self.assertEqual(p2, (1, "1"))  # 同页：页码取共同所在页
        self.assertEqual(len(res.sections[0][1]), 1)

    def test_heading_pushed_from_page_bottom(self):
        # 页高 6：先排 4 行，剩 2 行；标题1行+keep1行恰好放下
        cfg = LayoutConfig(page_height=6, page_width=40, keep_with_next=1)
        fits = [cjk_para(4, 40), Heading(2, "刚好放下的标题"),
                cjk_para(2, 40)]
        res = build([Section("body", fits)], cfg)
        self.assertEqual(res.heading_pages[id(fits[1])], (1, "1"))

        # 先排 5 行，剩 1 行 < 标题1行+keep1行：标题整体推到第 2 页
        pushed = [cjk_para(5, 40), Heading(2, "被推走的标题"),
                  cjk_para(2, 40)]
        res2 = build([Section("body", pushed)], cfg)
        self.assertEqual(res2.heading_pages[id(pushed[1])], (2, "2"))
        # 第一页末不留孤标题：只有 5 行正文
        self.assertEqual(len(res2.sections[0][1][0]), 5)


class TestLongTitleAndMixedLevels(unittest.TestCase):
    """情形四：超长标题不错位；多级标题缩进正确。"""

    def setUp(self):
        self.cfg = LayoutConfig(page_height=60, page_width=40)
        long_title = ("目录页码对齐超长标题测试：当一个章节标题的长度超过栏宽"
                      "时必须按显示宽度折行，续行使用悬挂缩进，页码仍然与"
                      "其他条目对齐于同一右边界 internationalization")
        self.long_heading = Heading(1, long_title)
        doc = [
            Section("body", [
                Heading(1, "第一章"),
                Heading(2, "1.1 二级标题"),
                Heading(3, "1.1.1 三级标题"),
                self.long_heading,
                cjk_para(2, 40),
            ]),
        ]
        self.res = build(doc, self.cfg)

    def test_long_title_wraps_and_aligns(self):
        r = self.res
        lines = r.toc_lines
        label_lines = [ln for ln in lines if ".." in ln]
        # 超长标题至少折成 3 行
        self.assertGreaterEqual(len(lines), 5)
        # 每个页码行右边界恰好是栏宽 40，不因超长而错位
        for ln in label_lines:
            self.assertEqual(display_width(ln), 40, ln)
        check = check_toc_layout(r)
        self.assertTrue(check.all_within_width)
        self.assertTrue(check.label_lines_right_aligned)
        self.assertTrue(all(col == 40 for col in check.label_line_columns))

    def test_continuation_hanging_indent(self):
        lines = self.res.toc_lines
        long_idx = next(i for i, ln in enumerate(lines) if "超长" in ln)
        # 级1首行无缩进；续行悬挂缩进 2 列；末行含点线与页码
        self.assertTrue(lines[long_idx].startswith("目录页码对齐"))
        self.assertTrue(lines[long_idx + 1].startswith("  "))
        self.assertNotIn("..", lines[long_idx + 1])
        self.assertTrue(lines[-1].rstrip().endswith("2"), lines[-1])

    def test_mixed_level_indents(self):
        lines = self.res.toc_lines
        self.assertTrue(lines[0].startswith("第一章"))          # 级1：0 列
        self.assertTrue(lines[1].startswith("  1.1"))           # 级2：2 列
        self.assertTrue(lines[2].startswith("    1.1.1"))       # 级3：4 列
        # 各条目页码仍统一在第 40 列
        for ln in lines[:3]:
            self.assertEqual(display_width(ln), 40)

    def test_verify_all_match(self):
        self.assertTrue(all(row.ok for row in verify(self.res)))


class TestEmptyToc(unittest.TestCase):
    def test_no_entries(self):
        cfg = LayoutConfig(page_height=10, page_width=40)
        res = build([Section("front", [Paragraph("扉页")]),
                     Section("toc", []),
                     Section("body", [Paragraph("无标题正文")])], cfg)
        self.assertEqual(res.toc_lines, [])
        self.assertEqual(res.entries, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

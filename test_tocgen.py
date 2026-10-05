# -*- coding: utf-8 -*-
"""tocgen 自测：无前置 / 有前置 / 超长标题 / 多级混排 / 换页规则 / 页码校验。

运行：python3 -m unittest -v test_tocgen
"""
import unittest

from tocgen import (
    Document, Heading, Paragraph, Layout,
    paginate, build_toc, render_toc, validate, all_rows_ok,
    logical_label, to_roman, display_width,
)

L = Layout(lines_per_page=10, page_width=40, keep_with_next=2)
L_NOBREAK = Layout(lines_per_page=10, page_width=40, keep_with_next=2,
                   chapter_new_page=False)


class TestNumbering(unittest.TestCase):
    def test_roman(self):
        self.assertEqual([to_roman(n) for n in (1, 2, 3, 4, 9, 14, 40)],
                         ["i", "ii", "iii", "iv", "ix", "xiv", "xl"])

    def test_logical_label_with_and_without_front(self):
        # 无前置：物理页即逻辑页
        self.assertEqual(logical_label(1, 0), "1")
        self.assertEqual(logical_label(7, 0), "7")
        # 前置 2 页：i ii | 1 2 3
        self.assertEqual([logical_label(p, 2) for p in range(1, 5)],
                         ["i", "ii", "1", "2"])


class TestPagination(unittest.TestCase):
    def test_no_front_matter(self):
        doc = Document(body=[
            Heading(1, "第一章 起步"),
            Paragraph("alpha " * 20),
            Heading(1, "第二章 继续"),
            Paragraph("beta " * 5),
        ])
        p = paginate(doc, L)
        self.assertEqual(p.front_pages, 0)
        self.assertEqual(p.body_start_page, 1)
        entries = build_toc(doc, p, include_front=False)
        rows = validate(doc, L, p, entries)
        self.assertTrue(all_rows_ok(rows))
        # 章另起页：两章分别从物理页 1 和后续新页开始
        ch1, ch2 = entries
        self.assertEqual(ch1.physical, 1)
        self.assertEqual(ch1.logical, "1")
        self.assertGreater(ch2.physical, ch1.physical)
        # 章首行必须出现在该页第一行
        self.assertEqual(p.pages[ch1.physical - 1].lines[0], "第一章 起步")
        self.assertEqual(p.pages[ch2.physical - 1].lines[0], "第二章 继续")

    def test_with_front_matter(self):
        doc = Document(
            front=[Heading(1, "序言"), Paragraph("前言内容 " * 15)],
            body=[
                Heading(1, "第一章"),
                Paragraph("正文 " * 30),
                Heading(2, "第一节"),
                Paragraph("小节正文 " * 5),
            ],
        )
        p = paginate(doc, L)
        self.assertGreaterEqual(p.front_pages, 1)
        # 正文从新页开始，且逻辑页重新计为 1
        body_first = p.body_start_page
        self.assertEqual(logical_label(body_first, p.front_pages), "1")
        entries = build_toc(doc, p)
        rows = validate(doc, L, p, entries)
        self.assertTrue(all_rows_ok(rows))
        front_entry = entries[0]
        self.assertEqual(front_entry.physical, 1)
        self.assertEqual(front_entry.logical, "i")
        ch1 = next(e for e in entries if e.text == "第一章")
        self.assertEqual(ch1.physical, body_first)
        self.assertEqual(ch1.logical, "1")
        # 物理页连续不断
        self.assertEqual([pg.number for pg in p.pages],
                         list(range(1, len(p.pages) + 1)))

    def test_chapter_page_break_is_deterministic(self):
        # 当前页只剩很少空间：一级标题仍然被推到新页
        body = [Heading(1, "上一章"), Paragraph("x " * 9)]
        # 10 行/页：章标题占 1 + keep 2，段落 9 行，标题后只能放下 9 行段落，
        # 下一个章标题必然另起页
        body += [Heading(1, "下一章"), Paragraph("y " * 3)]
        p = paginate(Document(body=body), L)
        anchor_next = p.anchors[id(body[2])]
        self.assertEqual(p.pages[anchor_next - 1].lines[0], "下一章")
        # 页码等于标题首行所在页
        self.assertEqual(anchor_next, 2)

    def test_chapter_end_and_start_same_page(self):
        # chapter_new_page=False：上一章末尾与下一章章首可落在同页
        body = [
            Heading(1, "甲章"),
            Paragraph("甲 " * 3),
            Heading(1, "乙章"),       # 与甲章末尾同页
            Paragraph("乙 " * 20),
            Heading(2, "乙的节"),
        ]
        p = paginate(Document(body=body), L_NOBREAK)
        a = p.anchors[id(body[0])]
        b = p.anchors[id(body[2])]
        # 确定结果：两章章首同页，乙章页码取其首行所在页 == 甲章页
        self.assertEqual(a, b)
        page_lines = p.pages[a - 1].lines
        self.assertIn("甲章", page_lines)
        self.assertIn("乙章", page_lines)
        entries = build_toc(Document(body=body), p, include_front=False)
        self.assertEqual(entries[1].physical, a)  # 乙章
        self.assertEqual(entries[1].logical, str(a))
        rows = validate(Document(body=body), L_NOBREAK, p, entries)
        self.assertTrue(all_rows_ok(rows))

    def test_keep_with_next_pushes_heading(self):
        # 标题落在页底且其后放不下 keep_with_next 行 -> 整标题推到下一页
        # 10 行/页、宽 40："padding " 每行 5 个词，45 个词恰好占满 9 行
        body = [Paragraph("padding " * 45), Heading(2, "挤不下的节"),
                Paragraph("z " * 5)]
        p = paginate(Document(body=body), L)
        anchor = p.anchors[id(body[1])]
        self.assertEqual(anchor, 2)
        self.assertEqual(p.pages[1].lines[0], "挤不下的节")

    def test_multi_level_mix(self):
        doc = Document(
            front=[Heading(1, "出版说明"), Paragraph("p " * 6)],
            body=[
                Heading(1, "第一章"),
                Heading(2, "第一节"),
                Paragraph("a " * 8),
                Heading(3, "一小节"),
                Paragraph("b " * 8),
                Heading(2, "第二节"),
                Paragraph("c " * 25),
                Heading(1, "第二章"),
                Heading(2, "第一节"),
                Paragraph("d " * 5),
            ],
        )
        p = paginate(doc, L)
        entries = build_toc(doc, p)
        rows = validate(doc, L, p, entries)
        self.assertTrue(all_rows_ok(rows))
        levels = [e.level for e in entries]
        self.assertEqual(levels, [1, 1, 2, 3, 2, 1, 2])
        # 页码单调不减
        phys = [e.physical for e in entries]
        self.assertEqual(phys, sorted(phys))


class TestLongTitle(unittest.TestCase):
    def setUp(self):
        self.long = "这是一个非常非常非常非常非常非常非常非常非常长的章节标题" \
                    "它远超页面宽度需要折成好几行才能完整显示在目录里"
        doc = Document(body=[
            Heading(1, self.long),
            Paragraph("正文 " * 10),
            Heading(2, "短标题"),
            Paragraph("x " * 5),
        ])
        self.doc = doc
        self.p = paginate(doc, L)
        self.entries = build_toc(doc, self.p, include_front=False)

    def test_long_title_page_is_first_line_page(self):
        # 正文里超长标题折行，页码仍锚定首行所在页
        e0 = self.entries[0]
        self.assertEqual(e0.physical, 1)
        rows = validate(self.doc, L, self.p, self.entries)
        self.assertTrue(all_rows_ok(rows))

    def test_toc_columns_never_shift(self):
        width = 40
        text = render_toc(self.entries, width=width)
        lines = text.splitlines()
        # 每个条目的最后一行都以该项逻辑页码结尾
        last_of_entry = [ln for ln in lines if "." in ln]
        self.assertEqual(len(last_of_entry), len(self.entries))
        for ln, e in zip(last_of_entry, self.entries):
            self.assertTrue(ln.endswith(e.logical), msg=ln)
            # 条目末行（点导引行）一律铺满到第 width 列——页码列固定
            self.assertEqual(display_width(ln), width, msg=ln)
        # 续行不出现点导引与页码，且与标题起始列对齐
        cont = [ln for ln in lines if "." not in ln]
        self.assertTrue(cont)  # 长标题确实产生了续行
        for ln in cont:
            self.assertFalse(ln.endswith(tuple("0123456789")))
            self.assertEqual(display_width(ln) <= width, True)

    def test_indent_per_level(self):
        text = render_toc(self.entries, width=40)
        e0, e1 = self.entries
        long_last = next(ln for ln in text.splitlines()
                         if "." in ln and ln.endswith(e0.logical))
        short_line = next(ln for ln in text.splitlines()
                          if ln.startswith("  短标题"))
        self.assertFalse(long_last.startswith(" "))   # 1 级无缩进
        self.assertTrue(short_line.endswith(e1.logical))


class TestValidation(unittest.TestCase):
    def test_tampered_entry_is_rejected(self):
        doc = Document(body=[Heading(1, "章一"), Paragraph("a " * 30),
                             Heading(1, "章二")])
        p = paginate(doc, L)
        entries = build_toc(doc, p, include_front=False)
        self.assertTrue(all_rows_ok(validate(doc, L, p, entries)))
        entries[1].physical = 99  # 篡改页码
        rows = validate(doc, L, p, entries)
        self.assertFalse(all_rows_ok(rows))
        self.assertFalse(rows[1].found_on_page)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""test_footnote.py — footnote 库自测（标准库 unittest）。

覆盖情形：单次引用、多处重复引用、引用被删除后重排、脚注跨页拆分与合并、
引用一致性校验、异常输入。
"""

import unittest

from footnote import (
    FootnoteRegistry,
    merge_parts,
    paginate_footnotes,
)


def make_registry():
    """构造文档：6 个引用点、4 条脚注（author 正文 7 行，用于跨页）。"""
    reg = FootnoteRegistry()
    reg.define("author", "\n".join(f"作者简介第{i}行" for i in range(1, 8)))
    reg.define("ref-a", "文献 A，某出版社，2020。")
    reg.define("data", "数据来源：国家统计局年鉴。")
    reg.define("ref-b", "文献 B，某期刊，2021。")
    reg.add_reference("ref-a", "r1")   # 首次出现 -> 1
    reg.add_reference("author", "r2")  # 首次出现 -> 2
    reg.add_reference("ref-a", "r3")   # 重复引用 -> 仍为 1
    reg.add_reference("data", "r4")    # 首次出现 -> 3
    reg.add_reference("author", "r5")  # 重复引用 -> 仍为 2
    reg.add_reference("ref-b", "r6")   # 首次出现 -> 4
    return reg


class TestSingleReference(unittest.TestCase):
    def test_single_reference_gets_number_1(self):
        reg = FootnoteRegistry()
        reg.define("only", "唯一脚注。")
        reg.add_reference("only", "x1")
        self.assertEqual(reg.number_at("x1"), 1)
        self.assertEqual(reg.number_of("only"), 1)
        self.assertEqual(reg.validate(), [])


class TestRepeatedReference(unittest.TestCase):
    def setUp(self):
        self.reg = make_registry()

    def test_number_by_first_appearance(self):
        self.assertEqual(
            self.reg.renumber(), {"ref-a": 1, "author": 2, "data": 3, "ref-b": 4}
        )

    def test_all_reference_points_share_number(self):
        self.assertEqual(self.reg.number_at("r1"), 1)
        self.assertEqual(self.reg.number_at("r3"), 1)   # 重复引用同号
        self.assertEqual(self.reg.number_at("r2"), 2)
        self.assertEqual(self.reg.number_at("r5"), 2)   # 重复引用同号
        self.assertEqual(self.reg.number_at("r6"), 4)

    def test_validate_passes(self):
        self.assertEqual(self.reg.validate(), [])


class TestDeleteAndRenumber(unittest.TestCase):
    def setUp(self):
        self.reg = make_registry()

    def test_delete_only_reference_recycles_number(self):
        # 删除 data 唯一的引用点 r4：data(3) 被回收，ref-b 4 -> 3，不留空号
        self.reg.remove_reference("r4")
        self.assertEqual(
            self.reg.renumber(), {"ref-a": 1, "author": 2, "ref-b": 3}
        )
        self.assertIsNone(self.reg.number_of("data"))
        self.assertEqual(self.reg.number_at("r6"), 3)
        self.assertEqual(self.reg.validate(), [])

    def test_delete_one_of_many_references_keeps_number(self):
        # 删除 r3 后 ref-a 仍有 r1，编号不变
        self.reg.remove_reference("r3")
        self.assertEqual(self.reg.number_of("ref-a"), 1)
        self.assertEqual(self.reg.number_at("r1"), 1)
        self.assertEqual(self.reg.validate(), [])

    def test_delete_all_references_of_a_key(self):
        # 删除 ref-a 的全部引用点 r1、r3：后续编号整体上移
        self.reg.remove_reference("r1")
        self.reg.remove_reference("r3")
        self.assertEqual(
            self.reg.renumber(), {"author": 1, "data": 2, "ref-b": 3}
        )
        self.assertEqual(self.reg.validate(), [])

    def test_remove_unknown_reference_raises(self):
        with self.assertRaises(KeyError):
            self.reg.remove_reference("nope")


class TestCrossPage(unittest.TestCase):
    def setUp(self):
        self.reg = make_registry()
        self.ordered = [
            (num, self.reg.body_of(key))
            for num, key, _ in self.reg.mapping_table()
        ]

    def test_split_across_pages(self):
        # author 正文 7 行，每页脚注区 4 行 -> 必然跨页
        pages = paginate_footnotes(self.ordered, lines_per_page=4)
        # 找到 author(编号2) 的碎片
        parts = [p for page in pages for p in page if p.number == 2]
        self.assertGreater(len(parts), 1)
        self.assertFalse(parts[0].continued_from_previous_page)
        self.assertTrue(parts[0].continues_on_next_page)
        self.assertTrue(parts[-1].continued_from_previous_page)
        self.assertFalse(parts[-1].continues_on_next_page)
        # 中间碎片两头都带标记
        for mid in parts[1:-1]:
            self.assertTrue(mid.continued_from_previous_page)
            self.assertTrue(mid.continues_on_next_page)

    def test_merge_restores_original_text(self):
        pages = paginate_footnotes(self.ordered, lines_per_page=4)
        all_parts = [p for page in pages for p in page]
        merged = merge_parts(all_parts)
        self.assertEqual(merged, self.ordered)  # 无损还原

    def test_no_split_when_fits(self):
        pages = paginate_footnotes(self.ordered, lines_per_page=100)
        self.assertEqual(len(pages), 1)
        for part in pages[0]:
            self.assertFalse(part.continued_from_previous_page)
            self.assertFalse(part.continues_on_next_page)

    def test_invalid_page_size(self):
        with self.assertRaises(ValueError):
            paginate_footnotes(self.ordered, lines_per_page=0)


class TestValidationAndErrors(unittest.TestCase):
    def test_reference_to_undefined_key_raises(self):
        reg = FootnoteRegistry()
        with self.assertRaises(KeyError):
            reg.add_reference("ghost")

    def test_duplicate_ref_id_raises(self):
        reg = FootnoteRegistry()
        reg.define("a", "正文")
        reg.add_reference("a", "x")
        with self.assertRaises(ValueError):
            reg.add_reference("a", "x")

    def test_mapping_table_sorted_by_number(self):
        reg = make_registry()
        nums = [num for num, _key, _text in reg.mapping_table()]
        self.assertEqual(nums, [1, 2, 3, 4])


if __name__ == "__main__":
    unittest.main(verbosity=2)

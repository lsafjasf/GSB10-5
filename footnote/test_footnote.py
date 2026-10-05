"""footnote_lib 自测：覆盖单次引用、多处重复引用、引用删除重排、脚注跨页。"""

import unittest

from footnote_lib import (
    CARRIED_MARK, CONT_MARK, FootnoteDef, Line, Ref,
    assign_numbers, check_consistency, paginate,
)


def make_defs():
    return {
        "alpha": FootnoteDef("alpha", ["甲注：第一行。", "甲注：第二行。"]),
        "beta": FootnoteDef("beta", ["乙注：唯一一行。"]),
        "gamma": FootnoteDef("gamma", ["丙注：第一行。", "丙注：第二行。", "丙注：第三行。"]),
        "delta": FootnoteDef("delta", ["丁注：单行。"]),
    }


class NumberingTest(unittest.TestCase):
    def test_single_reference(self):
        lines = [Line("正文", [Ref("beta")])]
        points, numbers = assign_numbers(lines)
        self.assertEqual(numbers, {"beta": 1})
        self.assertEqual(len(points), 1)

    def test_duplicate_references_share_one_number(self):
        lines = [
            Line("一", [Ref("alpha")]),
            Line("二", [Ref("beta")]),
            Line("三", [Ref("alpha"), Ref("alpha", tag="again")]),
        ]
        points, numbers = assign_numbers(lines)
        # alpha 首次出现在 beta 之前 -> 1；重复引用不另占编号
        self.assertEqual(numbers, {"alpha": 1, "beta": 2})
        self.assertEqual(len(points), 4)
        self.assertEqual([p.seq for p in points], [1, 2, 3, 4])

    def test_renumber_after_delete_no_gap(self):
        before = [
            Line("一", [Ref("alpha")]),
            Line("二", [Ref("beta")]),
            Line("三", [Ref("gamma")]),
        ]
        _, numbers_before = assign_numbers(before)
        self.assertEqual(numbers_before, {"alpha": 1, "beta": 2, "gamma": 3})

        after = [line for line in before if not any(r.key == "beta" for r in line.refs)]
        _, numbers_after = assign_numbers(after)
        self.assertEqual(numbers_after, {"alpha": 1, "gamma": 2})  # 无空号

    def test_delete_one_of_duplicates_keeps_number(self):
        lines = [
            Line("一", [Ref("alpha")]),
            Line("二", [Ref("beta")]),
            Line("三", [Ref("alpha")]),
        ]
        _, before = assign_numbers(lines)
        after = lines[:2]  # 删除第二处 alpha 引用
        _, after_numbers = assign_numbers(after)
        self.assertEqual(before, after_numbers)  # 还有剩余引用，编号不变


class LayoutTest(unittest.TestCase):
    def test_same_page_duplicates_merge_into_one_entry(self):
        defs = make_defs()
        lines = [
            Line("一", [Ref("alpha")]),
            Line("二", [Ref("alpha"), Ref("alpha", tag="x")]),
        ]
        pages, numbers, points = paginate(lines, defs, body_cap=10, foot_cap=10)
        self.assertEqual(len(pages), 1)
        alpha_chunks = [c for c in pages[0].chunks if c.key == "alpha"]
        self.assertEqual(len(alpha_chunks), 1)          # 同页合并为一个条目
        self.assertEqual(len(alpha_chunks[0].refs), 3)  # 三个引用点都回链
        self.assertEqual(check_consistency(pages, defs, points, numbers, foot_cap=10), [])

    def test_footnote_splits_across_pages_and_merges_back(self):
        defs = make_defs()
        lines = [Line(f"正文{i}", [Ref("gamma")] if i == 0 else []) for i in range(3)]
        # 页脚容量 2：gamma 三行 -> 首页 2 行 + 次页续 1 行
        pages, numbers, points = paginate(lines, defs, body_cap=3, foot_cap=2)
        self.assertEqual(len(pages), 2)
        first, second = pages[0].chunks[0], pages[1].chunks[0]
        self.assertEqual((first.start, len(first.lines)), (0, 2))
        self.assertFalse(first.continuation)
        self.assertTrue(first.has_more)
        self.assertEqual((second.start, len(second.lines)), (2, 1))
        self.assertTrue(second.continuation)
        self.assertFalse(second.has_more)
        self.assertIn(CONT_MARK, pages[1].foot[0])
        merged = first.lines + second.lines
        self.assertEqual(merged, defs["gamma"].body)     # 跨页合并还原
        self.assertEqual(check_consistency(pages, defs, points, numbers, foot_cap=2), [])

    def test_continuation_takes_priority_over_new_entries(self):
        defs = make_defs()
        lines = [Line("一", [Ref("gamma")])] + [Line("二", [Ref("delta")])]
        # 第1页：正文1行，页脚2行 -> gamma 排 2 行，余 1 行顺延
        # 第2页：先排 gamma 续块(1行)，再排 delta(1行)
        pages, numbers, points = paginate(lines, defs, body_cap=1, foot_cap=2)
        self.assertEqual(len(pages), 2)
        p2_keys = [c.key for c in pages[1].chunks]
        self.assertEqual(p2_keys, ["gamma", "delta"])
        self.assertTrue(pages[1].chunks[0].continuation)
        self.assertEqual(check_consistency(pages, defs, points, numbers, foot_cap=2), [])

    def test_entry_carried_when_reference_page_foot_full(self):
        defs = make_defs()
        lines = [
            Line("一", [Ref("gamma")]),   # gamma 3 行，占满第1页页脚(容量3)
            Line("二", [Ref("delta")]),   # delta 在第1页被引用但放不下 -> 顺延
        ]
        pages, numbers, points = paginate(lines, defs, body_cap=2, foot_cap=3)
        self.assertEqual(len(pages), 2)
        carried = pages[1].chunks[0]
        self.assertEqual(carried.key, "delta")
        self.assertTrue(carried.carried)
        self.assertIn(CARRIED_MARK, pages[1].foot[0])
        # 回链仍指向真正的引用页（第1页）
        self.assertEqual(carried.refs[0][0], 1)
        self.assertEqual(check_consistency(pages, defs, points, numbers, foot_cap=3), [])

    def test_every_reference_points_back(self):
        defs = make_defs()
        lines = [
            Line("一", [Ref("alpha"), Ref("beta")]),
            Line("二", [Ref("alpha")]),
            Line("三", [Ref("gamma"), Ref("alpha", tag="z")]),
            Line("四", [Ref("beta")]),
        ]
        pages, numbers, points = paginate(lines, defs, body_cap=2, foot_cap=4)
        self.assertEqual(check_consistency(pages, defs, points, numbers, foot_cap=4), [])

    def test_delete_then_repaginate_consistent(self):
        defs = make_defs()
        lines = [
            Line("一", [Ref("alpha")]),
            Line("二", [Ref("beta")]),
            Line("三", [Ref("gamma")]),
            Line("四", [Ref("alpha")]),
        ]
        pages1, num1, pts1 = paginate(lines, defs, body_cap=2, foot_cap=6)
        self.assertEqual(check_consistency(pages1, defs, pts1, num1, foot_cap=6), [])

        del lines[1]  # 删除唯一引用 beta 的行
        pages2, num2, pts2 = paginate(lines, defs, body_cap=2, foot_cap=6)
        self.assertEqual(num2, {"alpha": 1, "gamma": 2})  # 重排无空号
        self.assertNotIn("beta", num2)
        self.assertEqual(check_consistency(pages2, defs, pts2, num2, foot_cap=6), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

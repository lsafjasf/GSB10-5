#!/usr/bin/env python3
"""docdiff 自测：python3 selftest.py  （或 python3 -m unittest selftest -v）

覆盖：完全相同、仅改名、整章移动、层级调整、增删、组合场景、
摘要<->明细互还原、配对阈值边界行为。
"""
import unittest

from docdiff import diff_text
from docdiff.fingerprint import jaccard, shingles
from docdiff.scenarios import SCENARIOS
from docdiff.summary import verify_roundtrip


def scenario(name):
    return next(s for s in SCENARIOS if s["name"] == name)


def kinds(res):
    return [r["kind"] for r in res["records"]]


class TestScenarios(unittest.TestCase):
    def assert_roundtrip(self, res):
        v = verify_roundtrip(res["records"])
        self.assertTrue(v["summary_to_detail_ok"], "摘要->明细 还原失败")
        self.assertTrue(v["detail_to_summary_ok"], "明细->摘要 还原失败")
        self.assertTrue(v["stub_projection_ok"], "摘要->存根投影 还原失败")

    def test_identical(self):
        sc = scenario("identical")
        res = diff_text(sc["old"], sc["new"])
        self.assertEqual(res["records"], [], "完全相同不应产生任何记录")
        self.assert_roundtrip(res)

    def test_rename_only(self):
        sc = scenario("rename_only")
        res = diff_text(sc["old"], sc["new"])
        self.assertEqual(kinds(res), ["renamed"])
        rec = res["records"][0]
        self.assertEqual(rec["flags"], ["renamed"])
        self.assertNotIn("added", kinds(res))
        self.assertNotIn("deleted", kinds(res))
        self.assertEqual(rec["old_path"][-1], "安装步骤")
        self.assertEqual(rec["new_path"][-1], "安装流程")
        self.assertTrue(rec["evidence"]["body_fp_equal"], "改名判定依据：正文指纹一致")
        self.assertLess(rec["evidence"]["title_sim"], 1.0)
        self.assert_roundtrip(res)

    def test_chapter_move(self):
        sc = scenario("chapter_move")
        res = diff_text(sc["old"], sc["new"])
        self.assertEqual(sorted(kinds(res)), ["carried", "carried", "moved"])
        moved = next(r for r in res["records"] if r["kind"] == "moved")
        self.assertEqual(moved["old_path"][-1], "第二章 安装")
        self.assertTrue(moved["struct_evidence"]["reordered"])
        self.assertFalse(moved["struct_evidence"]["parent_changed"])
        carried = [r for r in res["records"] if r["kind"] == "carried"]
        self.assertEqual(len(carried), 2, "整章搬迁时子节应折叠为 carried")
        for r in carried:
            self.assertEqual(r["carried_by"], "第二章 安装")
        self.assertNotIn("added", kinds(res))
        self.assertNotIn("deleted", kinds(res))
        self.assert_roundtrip(res)

    def test_relevel(self):
        sc = scenario("relevel")
        res = diff_text(sc["old"], sc["new"])
        self.assertEqual(kinds(res), ["releveled", "releveled"])
        by_title = {r["old_path"][-1]: r for r in res["records"]}
        yi = by_title["小节乙"]
        self.assertEqual(yi["delta_level"], 1)
        self.assertTrue(yi["struct_evidence"]["parent_changed"],
                        "并入他节：父节点变化")
        self.assertIn("moved", yi["flags"])
        bing = by_title["小节丙"]
        self.assertEqual(bing["delta_level"], 1)
        self.assertFalse(bing["struct_evidence"]["parent_changed"],
                         "同父降级：父节点不变")
        self.assert_roundtrip(res)

    def test_add_delete(self):
        sc = scenario("add_delete")
        res = diff_text(sc["old"], sc["new"])
        self.assertEqual(sorted(kinds(res)), ["added", "deleted"])
        deleted = next(r for r in res["records"] if r["kind"] == "deleted")
        self.assertEqual(deleted["old_path"][-1], "第二章 安装")
        self.assertEqual(deleted["subtree"]["sections"], 3, "子树折叠：只记根")
        added = next(r for r in res["records"] if r["kind"] == "added")
        self.assertEqual(added["new_path"][-1], "第四章 维护")
        self.assertEqual(added["subtree"]["sections"], 3)
        self.assertNotIn("moved", kinds(res), "中间章删除不应误判其余章为移动")
        self.assert_roundtrip(res)

    def test_combined(self):
        sc = scenario("combined")
        res = diff_text(sc["old"], sc["new"])
        self.assertEqual(sorted(kinds(res)), ["carried", "modified", "moved"])
        moved = next(r for r in res["records"] if r["kind"] == "moved")
        self.assertIn("renamed", moved["flags"], "移动+改名应同时标记")
        self.assertEqual(moved["new_path"][-1], "第二章 安装手册")
        modified = next(r for r in res["records"] if r["kind"] == "modified")
        self.assertEqual(modified["old_path"][-1], "第三章 使用")
        self.assertGreaterEqual(modified["evidence"]["body_jaccard"], 0.6)
        self.assert_roundtrip(res)

    def test_roundtrip_all_scenarios(self):
        for sc in SCENARIOS:
            with self.subTest(scenario=sc["name"]):
                res = diff_text(sc["old"], sc["new"])
                self.assert_roundtrip(res)

    def test_threshold_boundary(self):
        """正文 20 行连续改写 6 行：实测 Jaccard 与阈值比较决定配对与否。"""
        old_body = [f"阈值自测第{i}行内容" for i in range(20)]
        new_body = list(old_body)
        for i in range(10, 16):
            new_body[i] = f"阈值自测第{i}行已改写"
        old = "# 章节\n" + "\n".join(old_body)
        new = "# 章节\n" + "\n".join(new_body)
        j = jaccard(shingles(old_body), shingles(new_body))
        res = diff_text(old, new)
        if j >= 0.60:
            self.assertEqual(kinds(res), ["modified"])
        else:
            self.assertEqual(sorted(kinds(res)), ["added", "deleted"])
        self.assertAlmostEqual(j, 12 / 26, places=4,
                               msg="2-shingle Jaccard 应为 12/26≈0.4615")

    def test_rename_vs_move_distinct(self):
        """改名与移动必须区分：纯改名不带 moved，纯移动不带 renamed。"""
        rn = diff_text(scenario("rename_only")["old"], scenario("rename_only")["new"])
        self.assertNotIn("moved", rn["records"][0]["flags"])
        mv = diff_text(scenario("chapter_move")["old"], scenario("chapter_move")["new"])
        moved = next(r for r in mv["records"] if r["kind"] == "moved")
        self.assertNotIn("renamed", moved["flags"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

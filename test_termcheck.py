"""termcheck 自测：覆盖完全一致、多种译名、大小写差异、一词多义等情形。

运行：python3 -m unittest test_termcheck -v
"""

import unittest

from termcheck import (Checker, Entry, Termbase, SEV_ERROR, SEV_REVIEW,
                       normalize_term, inflect_term)


def make_checker(entries):
    return Checker(Termbase(entries))


class TestExactMatch(unittest.TestCase):
    """完全一致：规范译名不报，变体译名必报且位置准确。"""

    def setUp(self):
        self.checker = make_checker([
            Entry("T001", "机器学习", variants=["机械学习"]),
        ])

    def test_approved_not_reported(self):
        self.assertEqual(self.checker.check("机器学习是人工智能的分支。"), [])

    def test_variant_reported_with_position(self):
        text = "前言。\n机械学习是人工智能的分支，机械学习需要数据。"
        findings = self.checker.check(text)
        self.assertEqual(len(findings), 2)
        f = findings[0]
        self.assertEqual(f.entry_id, "T001")
        self.assertEqual(f.approved, "机器学习")
        self.assertEqual(f.found, "机械学习")
        self.assertEqual(f.severity, SEV_ERROR)
        self.assertEqual(f.line, 2)
        self.assertEqual(f.col, 1)
        self.assertEqual(text[f.offset:f.offset + len(f.found)], "机械学习")
        g = findings[1]
        self.assertEqual(text[g.offset:g.offset + len(g.found)], "机械学习")
        self.assertGreater(g.offset, f.offset)

    def test_unrelated_synonym_not_reported(self):
        # “同义词”未登记为变体，不能误报
        self.assertEqual(self.checker.check("机器学习，也叫自动归纳方法。"), [])


class TestMultipleVariants(unittest.TestCase):
    """一个概念登记多种译名：所有变体都要被发现。"""

    def test_all_variants_found(self):
        checker = make_checker([
            Entry("T002", "深度神经网络",
                  variants=["深层神经网络", "深度神经网路", "深度神经网"]),
        ])
        text = "深层神经网络与深度神经网路都属于深度神经网络，简称深度神经网。"
        findings = checker.check(text)
        errors = [f for f in findings if f.severity == SEV_ERROR]
        self.assertEqual([f.found for f in errors],
                         ["深层神经网络", "深度神经网路", "深度神经网"])
        self.assertTrue(all(f.approved == "深度神经网络" for f in errors))


class TestCaseAndWidth(unittest.TestCase):
    """大小写与全半角：规范化后能匹配，但与规范译名不一致要报告。"""

    def setUp(self):
        self.checker = make_checker([
            Entry("T003", "GitHub", variants=["码云"]),
            Entry("T004", "API", variants=["接口程序"]),
        ])

    def test_exact_case_ok(self):
        self.assertEqual(self.checker.check("使用 GitHub 与 API 文档。"), [])

    def test_wrong_case_reported(self):
        findings = self.checker.check("请访问 Github 或 GITHUB 主页。")
        self.assertEqual(len(findings), 2)
        self.assertTrue(all(f.entry_id == "T003" for f in findings))
        self.assertTrue(all("大小写" in f.reason for f in findings))

    def test_fullwidth_reported(self):
        findings = self.checker.check("全角写法 ＧｉｔＨｕｂ 不规范。")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].found, "ＧｉｔＨｕｂ")

    def test_word_boundary(self):
        # GitHubbed 是词形变化（双写+ed），不报错；GitHubX 不是词边界命中
        self.assertEqual(self.checker.check("He GitHubbed it."), [])
        self.assertEqual(self.checker.check("GitHubX 不是命中。"), [])

    def test_latin_variant_case_insensitive(self):
        findings = self.checker.check("也有人用 码云 或 APIs 接口程序。")
        # “接口程序”是 T004 的变体；“APIs”是规范译名 API 的复数变形，规范用法
        self.assertEqual(len(findings), 2)
        self.assertEqual(findings[0].found, "码云")
        self.assertEqual(findings[1].found, "接口程序")


class TestInflection(unittest.TestCase):
    def test_inflect(self):
        self.assertIn("categories", inflect_term("category"))
        self.assertIn("boxes", inflect_term("box"))
        self.assertIn("running", inflect_term("run"))
        self.assertEqual(inflect_term("机器学习"), {"机器学习"})


class TestPolysemy(unittest.TestCase):
    """一词多义：命中上下文关键词 -> 冲突；未命中 -> 人工确认项。"""

    def setUp(self):
        self.checker = make_checker([
            Entry("T005", "苹果公司", variants=["苹果"],
                  context_keywords=["手机", "股价", "发布会"]),
        ])

    def test_context_hit_is_conflict(self):
        findings = self.checker.check("苹果发布了新手机，股价上涨。")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, SEV_ERROR)

    def test_context_miss_is_review(self):
        findings = self.checker.check("他每天吃一个苹果，有益健康。")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, SEV_REVIEW)
        self.assertIn("人工确认", findings[0].reason)

    def test_longest_match_suppresses_variant(self):
        # 规范译名“苹果公司”命中时，其中的“苹果”不再单报
        self.assertEqual(self.checker.check("苹果公司召开发布会。"), [])


class TestAmbiguity(unittest.TestCase):
    """某条目的变体恰是另一条目的规范译名 -> 人工确认。"""

    def test_cross_entry_ambiguity(self):
        checker = make_checker([
            Entry("T006", "存储器", variants=[]),
            Entry("T007", "内存", variants=["存储器"]),
        ])
        findings = checker.check("这台机器的存储器容量很大。")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, SEV_REVIEW)
        self.assertIn("T006", findings[0].reason)


class TestNormalize(unittest.TestCase):
    def test_fullwidth_and_case(self):
        self.assertEqual(normalize_term("ＡＰＩ"), "api")
        self.assertEqual(normalize_term("Api"), "api")
        self.assertEqual(normalize_term("机器学习"), "机器学习")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""termcheck 自测：覆盖完全一致、多种译名、大小写差异、一词多义等情形。"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from termcheck.ahocorasick import AhoCorasick
from termcheck.checker import Checker
from termcheck.lemma import is_acronym, surface_forms
from termcheck.normalize import normalize, line_col
from termcheck.termbase import load, load_file

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "fixtures")


def run_check(tb_data, segments):
    return Checker(load(tb_data)).check(segments)


class TestNormalize(unittest.TestCase):
    def test_fullwidth(self):
        norm, _ = normalize("Ｓａｖｅ　ｔｈｅ　ｆｉｌｅ．")
        self.assertEqual(norm, "Save the file.")

    def test_whitespace_collapse(self):
        norm, _ = normalize("a \t b\n\nc")
        self.assertEqual(norm, "a b c")

    def test_mapping_points_to_original(self):
        text = "ＡＢc"
        norm, mapping = normalize(text)
        self.assertEqual(norm, "ABc")
        self.assertEqual(text[mapping[0]], "Ａ")
        self.assertEqual(text[mapping[2]], "c")

    def test_line_col(self):
        self.assertEqual(line_col("ab\ncd\nef", 5), (2, 3))


class TestLemma(unittest.TestCase):
    def test_regular_inflections(self):
        self.assertIn("cats", surface_forms("cat"))
        self.assertIn("boxes", surface_forms("box"))
        self.assertIn("bodies", surface_forms("body"))
        self.assertIn("played", surface_forms("play"))
        self.assertIn("stopped", surface_forms("stop"))
        self.assertIn("running", surface_forms("run"))
        self.assertIn("liked", surface_forms("like"))

    def test_irregular(self):
        self.assertIn("ran", surface_forms("run"))
        self.assertIn("indices", surface_forms("index"))
        self.assertIn("mice", surface_forms("mouse"))
        self.assertIn("went", surface_forms("go"))

    def test_no_overreach(self):
        # 派生词不应被还原：short 不应截成 shor，bus 不应截成 bu
        self.assertNotIn("shor", surface_forms("short"))
        self.assertNotIn("bu", surface_forms("bus"))

    def test_acronym(self):
        self.assertTrue(is_acronym("API"))
        self.assertFalse(is_acronym("Api"))
        self.assertFalse(is_acronym("I"))
        self.assertIn("apis", surface_forms("API"))


class TestAhoCorasick(unittest.TestCase):
    def test_multi_pattern(self):
        ac = AhoCorasick()
        ac.add("he", 1)
        ac.add("she", 2)
        ac.add("hers", 3)
        ac.freeze()
        hits = sorted(ac.search("ushers"))
        # she@1..3, he@2..3, hers@2..5
        self.assertIn((3, 2), hits)
        self.assertIn((3, 1), hits)
        self.assertIn((5, 3), hits)


TB_MINI = [
    {"id": "E1", "source": "file", "canonical": "文件",
     "synonyms": ["档案"], "forbidden": ["档"], "allow_multiple": True,
     "safe_embeddings": ["归档"]},
    {"id": "E2", "source": "API", "canonical": "应用程序接口",
     "synonyms": ["API接口"]},
    {"id": "E3", "source": "mouse", "senses": [
        {"id": "m.c", "canonical": "鼠标",
         "cues": {"positive": ["click", "点击"]}},
        {"id": "m.a", "canonical": "老鼠",
         "cues": {"positive": ["cat", "猫"]}},
    ]},
    {"id": "E4", "source": "machine learning", "canonical": "机器学习"},
    {"id": "E5", "source": "run", "canonical": "运行"},
]


class TestChecker(unittest.TestCase):
    def test_exact_match_no_conflict(self):
        """完全一致：规范译名 -> 零报告。"""
        rep = run_check(TB_MINI, [
            {"id": "s1", "source": "Save the file.", "target": "保存文件。"}])
        self.assertEqual(rep.conflicts, [])
        self.assertEqual(rep.review, [])

    def test_inflection_and_case(self):
        """词形变化 + 句首大小写差异都能匹配，且不误报。"""
        rep = run_check(TB_MINI, [
            {"id": "s1", "source": "He Ran the Files.", "target": "他运行了文件。"}])
        self.assertEqual(rep.conflicts, [])
        self.assertEqual(rep.review, [])

    def test_fullwidth_source(self):
        """全角英文经 NFKC 归一化后照常匹配。"""
        rep = run_check(TB_MINI, [
            {"id": "s1", "source": "ｒｕｎ　ｉｔ", "target": "运行它。"}])
        self.assertEqual(rep.conflicts, [])
        self.assertEqual(rep.review, [])

    def test_acronym_case_sensitive(self):
        """缩写条目大小写敏感：api 不匹配 API，API/APIs 匹配。"""
        rep = run_check(TB_MINI, [
            {"id": "s1", "source": "The api is nice.",
             "target": "应用程序接口不错。"}])
        # api 小写 -> 条目未被源文佐证 -> 不报 unlisted_translation
        self.assertEqual(rep.review, [])
        rep2 = run_check(TB_MINI, [
            {"id": "s1", "source": "The APIs are nice.",
             "target": "应用程序接口不错。"}])
        self.assertEqual(rep2.conflicts, [])
        self.assertEqual(rep2.review, [])

    def test_forbidden_translation(self):
        rep = run_check(TB_MINI, [
            {"id": "s1", "source": "Save the file.", "target": "把档存好。"}])
        kinds = [(c["type"], c["entry_id"]) for c in rep.conflicts]
        self.assertIn(("forbidden_translation", "E1"), kinds)
        c = next(c for c in rep.conflicts
                 if c["type"] == "forbidden_translation")
        self.assertEqual((c["segment"], c["line"], c["col"]), ("s1", 1, 2))
        self.assertEqual(c["render"], "档")

    def test_safe_embedding_not_flagged(self):
        """「归档」含禁用语素「档」，但登记为安全组合词，不报。"""
        rep = run_check(TB_MINI, [
            {"id": "s1", "source": "Archive the file.", "target": "把文件归档。"}])
        self.assertEqual(rep.conflicts, [])
        self.assertEqual(rep.review, [])

    def test_allowed_synonyms_not_flagged(self):
        """allow_multiple 的条目混用规范译名与同义词，不算冲突。"""
        rep = run_check(TB_MINI, [
            {"id": "s1", "source": "Save the file.", "target": "保存文件。"},
            {"id": "s2", "source": "Archive the file.", "target": "把档案归档。"}])
        self.assertEqual(rep.conflicts, [])

    def test_multiple_renderings_flagged(self):
        """未声明 allow_multiple 的条目出现两种译名 -> 冲突，位置可复核。"""
        rep = run_check(TB_MINI, [
            {"id": "s1", "source": "The API is up.", "target": "应用程序接口正常。"},
            {"id": "s2", "source": "The API changed.", "target": "API接口变了。"}])
        multi = [c for c in rep.conflicts if c["type"] == "multiple_renderings"]
        self.assertEqual(len(multi), 1)
        self.assertEqual(multi[0]["entry_id"], "E2")
        renders = {o["render"] for o in multi[0]["renderings"]}
        self.assertEqual(renders, {"应用程序接口", "API接口"})
        segs = {o["segment"] for o in multi[0]["renderings"]}
        self.assertEqual(segs, {"s1", "s2"})

    def test_polysemy_resolved_by_context(self):
        """一词多义：cue 词命中 -> 正确义项，不误报。"""
        rep = run_check(TB_MINI, [
            {"id": "s1", "source": "Click the mouse.", "target": "点击鼠标。"},
            {"id": "s2", "source": "The cat ate the mouse.",
             "target": "猫吃了老鼠。"}])
        self.assertEqual(rep.conflicts, [])
        self.assertEqual(rep.review, [])

    def test_polysemy_wrong_sense(self):
        """上下文指向计算机义项，译文却用动物义项 -> wrong_sense。"""
        rep = run_check(TB_MINI, [
            {"id": "s1", "source": "Click the mouse.", "target": "点击老鼠。"}])
        kinds = [(c["type"], c.get("expected_sense"), c.get("actual_sense"))
                 for c in rep.conflicts]
        self.assertIn(("wrong_sense", "m.c", "m.a"), kinds)

    def test_polysemy_ambiguous_goes_to_review(self):
        """无 cue 词 -> 进入人工确认列表，而不是直接报错。"""
        rep = run_check(TB_MINI, [
            {"id": "s1", "source": "The mouse is here.", "target": "它在这。"}])
        self.assertEqual(rep.conflicts, [])
        types = [r["type"] for r in rep.review]
        self.assertIn("ambiguous_sense", types)

    def test_unlisted_translation_review(self):
        """源文出现条目、译文无任何登记译名 -> 人工确认（可能是新译名）。"""
        rep = run_check(TB_MINI, [
            {"id": "s1", "source": "Run it now.", "target": "现在就执行。"}])
        types = [r["type"] for r in rep.review]
        self.assertIn("unlisted_translation", types)
        self.assertEqual(rep.conflicts, [])

    def test_multiword_term(self):
        rep = run_check(TB_MINI, [
            {"id": "s1", "source": "Machine learning is fun.",
             "target": "机器学习很有趣。"}])
        self.assertEqual(rep.conflicts, [])
        self.assertEqual(rep.review, [])

    def test_word_boundary(self):
        """词边界：catalog 不应匹配 cat，filed 属于 file 的变形可匹配。"""
        tb = TB_MINI + [{"id": "E9", "source": "cat", "canonical": "猫"}]
        rep = run_check(tb, [
            {"id": "s1", "source": "The catalog is here.", "target": "目录在这。"}])
        self.assertFalse(any(r.get("entry_id") == "E9" for r in rep.review))
        self.assertFalse(any(c.get("entry_id") == "E9" for c in rep.conflicts))

    def test_termbase_issue_detection(self):
        """两个条目词形/大小写撞车 -> 术语库问题提示。"""
        tb = load([
            {"id": "A", "source": "File", "canonical": "文件"},
            {"id": "B", "source": "file", "canonical": "档案"},
        ])
        types = [i["type"] for i in tb.issues]
        self.assertIn("overlapping_source", types)

    def test_position_offset_accuracy(self):
        """冲突定位：offset 指向原文中的确切位置。"""
        target = "前言。把档存好。"
        rep = run_check(TB_MINI, [
            {"id": "s1", "source": "Save the file.", "target": target}])
        c = next(c for c in rep.conflicts
                 if c["type"] == "forbidden_translation")
        self.assertEqual(target[c["offset"]:c["offset"] + 1], "档")
        self.assertEqual((c["line"], c["col"]), (1, 5))


class TestFixtures(unittest.TestCase):
    """对随附语料的端到端测试。"""

    @classmethod
    def setUpClass(cls):
        cls.tb = load_file(os.path.join(FIXTURES, "termbase.json"))

    def _load_corpus(self, name):
        segs = []
        with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    segs.append(json.loads(line))
        return segs

    def test_clean_corpus_zero_reports(self):
        """人工确认无冲突的语料：0 冲突、0 待确认（误报量化）。"""
        rep = Checker(self.tb).check(self._load_corpus("corpus_clean.jsonl"))
        self.assertEqual(rep.conflicts, [],
                         msg=json.dumps(rep.conflicts, ensure_ascii=False))
        self.assertEqual(rep.review, [],
                         msg=json.dumps(rep.review, ensure_ascii=False))

    def test_conflict_corpus_findings(self):
        rep = Checker(self.tb).check(self._load_corpus("corpus_conflicts.jsonl"))
        by_type = {}
        for c in rep.conflicts:
            by_type.setdefault(c["type"], []).append(c)
        self.assertEqual(len(by_type.get("forbidden_translation", [])), 1)
        self.assertEqual(len(by_type.get("wrong_sense", [])), 1)
        multi_entries = {c["entry_id"]
                         for c in by_type.get("multiple_renderings", [])}
        self.assertEqual(multi_entries, {"E005", "E008"})
        review_types = [r["type"] for r in rep.review]
        self.assertIn("ambiguous_sense", review_types)
        self.assertIn("unlisted_translation", review_types)
        self.assertIn("forbidden_unverified", review_types)


if __name__ == "__main__":
    unittest.main(verbosity=2)

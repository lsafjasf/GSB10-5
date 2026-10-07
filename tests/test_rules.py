"""规则自测：语料驱动 + 单元用例。

语料构造说明：
- tests/corpus/real_issues.py        每条规则一个真实问题（TP）
- tests/corpus/decoys.py             字符串/注释/文档字符串里的假问题（应为 0 报警）
- tests/corpus/string_usage_decoy.py 导入名只出现在注释与字符串中（仍应报未使用导入）
- tests/corpus/clean.py              全部正确写法（应为 0 报警）
- tests/corpus/pkg_a, pkg_b          跨文件上下文（CR006）
- tests/corpus/expected.json         人工标注的期望报警（规则、文件、行号、结构）
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from codereview import check_paths, check_source

CORPUS = os.path.join(os.path.dirname(__file__), "corpus")


def load_expected():
    with open(os.path.join(CORPUS, "expected.json"), encoding="utf-8") as fh:
        return json.load(fh)


def actual_set(findings):
    return {
        (f.rule_id, os.path.relpath(f.path, CORPUS), f.line, f.structure)
        for f in findings
    }


def expected_set():
    return {(e["rule_id"], e["file"], e["line"], e["structure"]) for e in load_expected()}


class CorpusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.findings = check_paths([CORPUS])

    def test_findings_match_manifest_exactly(self):
        self.assertEqual(actual_set(self.findings), expected_set())

    def test_precision_and_recall_on_corpus(self):
        actual = actual_set(self.findings)
        expected = expected_set()
        tp = len(actual & expected)
        fp = len(actual - expected)
        fn = len(expected - actual)
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + fn) if tp + fn else 1.0
        print(f"\n语料指标: TP={tp} FP={fp} FN={fn} "
              f"precision={precision:.3f} recall={recall:.3f}")
        self.assertEqual((fp, fn), (0, 0))

    def test_decoys_produce_zero_findings(self):
        path = os.path.join(CORPUS, "decoys.py")
        hits = [f for f in self.findings if f.path == path]
        self.assertEqual(hits, [])

    def test_clean_file_produces_zero_findings(self):
        path = os.path.join(CORPUS, "clean.py")
        hits = [f for f in self.findings if f.path == path]
        self.assertEqual(hits, [])


class InlineCaseTest(unittest.TestCase):
    def check(self, source):
        return check_source(source, path="<test>")

    def test_mutable_default_variants(self):
        src = "def f(a, x=[], y={}, z=set(), w=dict()):\n    pass\n"
        hits = [f for f in self.check(src) if f.rule_id == "CR001"]
        self.assertEqual(len(hits), 4)
        self.assertTrue(all(f.line == 1 and f.structure == "f" for f in hits))

    def test_immutable_defaults_are_fine(self):
        src = "def f(a=None, b=(), c='', d=0, e=frozenset()):\n    pass\n"
        self.assertEqual([f for f in self.check(src) if f.rule_id == "CR001"], [])

    def test_nested_structure_name(self):
        src = (
            "class Svc:\n"
            "    def run(self, expr):\n"
            "        try:\n"
            "            return eval(expr)\n"
            "        except:\n"
            "            return None\n"
        )
        hits = self.check(src)
        eval_hit = next(f for f in hits if f.rule_id == "CR003")
        except_hit = next(f for f in hits if f.rule_id == "CR002")
        self.assertEqual((eval_hit.line, eval_hit.structure), (4, "Svc.run"))
        self.assertEqual((except_hit.line, except_hit.structure), (5, "Svc.run"))

    def test_eval_in_string_and_comment_ignored(self):
        src = 's = "eval(1+1)"  # eval(2+2)\nprint(s)\n'
        self.assertEqual(self.check(src), [])

    def test_except_exception_is_fine(self):
        src = "try:\n    pass\nexcept Exception:\n    pass\n"
        self.assertEqual([f for f in self.check(src) if f.rule_id == "CR002"], [])

    def test_import_used_only_in_comment_still_reported(self):
        src = "import os  # os\nprint('os 路径')\n"
        hits = [f for f in self.check(src) if f.rule_id == "CR004"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].line, 1)

    def test_dunder_all_counts_as_usage(self):
        src = "from pkg import helper\n__all__ = ['helper']\n"
        self.assertEqual([f for f in self.check(src) if f.rule_id == "CR004"], [])

    def test_sql_percent_and_format_variants(self):
        src = (
            "def a(cur, x):\n"
            "    cur.execute('SELECT %s' % x)\n"
            "def b(cur, x):\n"
            "    cur.execute('SELECT {}'.format(x))\n"
            "def c(cur, x):\n"
            "    cur.execute('SELECT %s', (x,))\n"
        )
        hits = [f for f in self.check(src) if f.rule_id == "CR005"]
        self.assertEqual([f.line for f in hits], [2, 4])

    def test_cross_file_missing_symbol(self):
        index = {"pkg.mod": {"foo"}}
        src = "from pkg.mod import foo, bar\nprint(foo, bar)\n"
        hits = self.check(src) if False else check_source(
            src, path="<test>", module_index=index
        )
        cr006 = [f for f in hits if f.rule_id == "CR006"]
        self.assertEqual(len(cr006), 1)
        self.assertEqual(cr006[0].line, 1)
        self.assertIn("bar", cr006[0].message)

    def test_syntax_error_reported_not_crash(self):
        hits = self.check("def broken(:\n")
        self.assertEqual(hits[0].rule_id, "CR000")
        self.assertEqual(hits[0].line, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

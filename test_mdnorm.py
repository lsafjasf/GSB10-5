"""mdnorm 自测：纯正文 / 代码块 / 引用块 / 硬换行。运行: python3 test_mdnorm.py"""

import unittest

import mdnorm


SAMPLE = """# 标题\t 后有Tab和多余空格

这是第一行   软换行
这是第二行\t结尾硬换行  \
这是第三行用反斜杠硬换行\\
这是第四行普通换行
再一行

> 引用第一行  结尾空白
> 引用第二行

普通段  落，后面紧跟围栏：

```python
def f( ):
    x = 1\t  # 缩进与Tab必须保留
    if x:
        return    "spaced"
```

缩进代码块：

    line one\twith tab
       line two  spaces

收尾段落   多空格
"""


class TestNormalize(unittest.TestCase):

    def test_plain_paragraph(self):
        text = "第一行   软换行\n第二行\t继续\n\n另起一段   \n"
        result, diffs = mdnorm.normalize(text)
        self.assertEqual(result,
                         "第一行 软换行 第二行 继续\n\n另起一段\n")
        kinds = {d.kind for d in diffs}
        self.assertIn("SOFT_BREAK_JOIN", kinds)
        self.assertIn("WHITESPACE", kinds)

    def test_hard_vs_soft_break(self):
        text = "软一\n软二\n硬两空格  \n硬反斜杠\\\n末行\n"
        result, _ = mdnorm.normalize(text)
        self.assertEqual(
            result,
            "软一 软二 硬两空格  \n硬反斜杠\\\n末行\n")
        lines = result.split("\n")
        self.assertEqual(lines[0], "软一 软二 硬两空格  ")
        self.assertTrue(lines[0].endswith("  "))   # 硬换行：换行保留
        self.assertEqual(lines[1], "硬反斜杠\\")
        self.assertIn("软一 软二", result)          # 软换行：变成空格

    def test_fenced_code_byte_identical(self):
        text = "前文\n\n```\n  a = 1\t  \n    nested\n```\n\n后文  空格\n"
        result, _ = mdnorm.normalize(text)
        records = mdnorm.code_region_hashes(text, result)
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["identical"])
        self.assertIn("  a = 1\t  \n    nested\n", result)
        self.assertIn("后文 空格\n", result)

    def test_tilde_fence_and_indented_code(self):
        text = "前文\n\n~~~\n` 不是围栏  \n~~~\n\n    indented\tcode  \n\n后文\n"
        result, _ = mdnorm.normalize(text)
        records = mdnorm.code_region_hashes(text, result)
        self.assertEqual(len(records), 2)
        self.assertTrue(all(r["identical"] for r in records))

    def test_quote_preserved(self):
        text = "> 引用行  保留空白\n> 第二行\n\n正文  空格\n"
        result, _ = mdnorm.normalize(text)
        self.assertIn("> 引用行  保留空白\n> 第二行\n", result)
        self.assertIn("正文 空格\n", result)

    def test_blank_collapse_and_final_newline(self):
        text = "段落\n\n\n\n\n无尾换行"
        result, diffs = mdnorm.normalize(text)
        self.assertEqual(result, "段落\n\n无尾换行\n")
        self.assertTrue(any(d.kind == "BLANK_COLLAPSE" for d in diffs))
        self.assertTrue(any(d.kind == "FINAL_NEWLINE" for d in diffs))

    def test_regions_classified(self):
        regions = mdnorm.parse_regions(SAMPLE)
        kinds = [r.kind for r in regions]
        self.assertIn("FENCE_CODE", kinds)
        self.assertIn("INDENT_CODE", kinds)
        self.assertIn("QUOTE", kinds)
        self.assertIn("PARAGRAPH", kinds)

    def test_sample_code_blocks_unchanged(self):
        result, _ = mdnorm.normalize(SAMPLE)
        records = mdnorm.code_region_hashes(SAMPLE, result)
        self.assertGreaterEqual(len(records), 2)
        for rec in records:
            self.assertTrue(rec["identical"], rec)

    def test_idempotent(self):
        once, _ = mdnorm.normalize(SAMPLE)
        twice, diffs = mdnorm.normalize(once)
        self.assertEqual(once, twice)
        self.assertEqual(diffs, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

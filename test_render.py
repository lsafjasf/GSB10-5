"""render.py 自测：宽度断言 + 结构对拍（parse(render(x)) == x）。"""

import json
import unittest

from render import CONT_MARK, INDENT_UNIT, display_width, parse, render


def assert_widths(testcase, text, width):
    """断言每个物理行的显示宽度（含缩进）都不超过上限。"""
    for lineno, line in enumerate(text.splitlines(), 1):
        w = display_width(line)
        testcase.assertLessEqual(
            w, width, f"第 {lineno} 行显示宽度 {w} 超过上限 {width}: {line!r}"
        )


def deep_node(depth):
    node = "leaf"
    for i in range(depth):
        node = {f"level-{depth - i}": [node, {"side": i}]}
    return node


class RenderTest(unittest.TestCase):
    def check(self, obj, width):
        text = render(obj, width=width)
        assert_widths(self, text, width)          # 宽度断言（缩进计入）
        self.assertEqual(parse(text), obj)        # 结构对拍
        return text

    def test_flat(self):
        """单层结构。"""
        obj = {"name": "alice", "age": 30, "ok": True, "nil": None, "pi": 3.5}
        self.check(obj, 40)

    def test_deep_nesting(self):
        """深嵌套（20 层交替 dict/list => 40 缩进级，宽度必须算进缩进）。"""
        obj = deep_node(20)
        text = self.check(obj, 100)
        # 最深一行有 80 列缩进：缩进本身已占宽度的 80%
        self.assertTrue(any(line.startswith(" " * 80) for line in text.splitlines()))

    def test_depth_exceeds_width(self):
        """缩进自身超过宽度时按规则报错，而不是产生超宽行。"""
        with self.assertRaises(ValueError):
            render(deep_node(20), width=50)

    def test_wide_chars(self):
        """超宽字符：CJK、全角、emoji、组合字符。"""
        obj = {
            "中文键": "中文值",
            "全角": "ＡＢＣ１２３",
            "emoji": "🎉🎊✨",
            "combining": "éx",  # e + 组合重音符
            "混排": "abc中文🎉def",
        }
        text = self.check(obj, 20)
        # 宽字符不可分割：任何行内不出现半个宽字符（行宽必为整数且 <= 20）
        self.assertTrue(all(display_width(l) <= 20 for l in text.splitlines()))

    def test_long_unbreakable_word(self):
        """超长不可断开单词：按显示宽度边界强制断开，续行以 \\ 标记。"""
        word = "x" * 53
        obj = {"outer": {"token": word}}  # 续行带 2 列缩进
        text = self.check(obj, 24)
        lines = text.splitlines()
        # 首行：2 缩进 + 10 前缀 + 12 个 x = 24
        self.assertEqual(lines[1], '  "token": "' + "x" * 12)
        # 续行：2 缩进 + "\" + 21 个 x = 24（强制断开点确定）
        self.assertEqual(lines[2], "  " + CONT_MARK + "x" * 21)
        self.assertEqual(lines[3], "  " + CONT_MARK + "x" * 20 + '"')
        # 拼回去等于原单词
        self.assertEqual(parse(text), obj)

    def test_long_word_deterministic(self):
        """超长单词的断开结果逐字节确定。"""
        text = render({"k": "abcdefghij" * 3}, width=12)
        self.assertEqual(
            text,
            '"k": "abcdef\n'
            '\\ghijabcdefg\n'
            '\\hijabcdefgh\n'
            '\\ij"\n',
        )

    def test_mixed(self):
        """混合：空容器、列表套字典、特殊字符、unicode 键。"""
        obj = {
            "empty_dict": {},
            "empty_list": [],
            "items": [
                {"id": 1, "tags": ["a", "b"]},
                {"id": 2, "tags": []},
                "纯字符串",
                42,
            ],
            "特殊\n字符": "tab\t引号\"反斜杠\\",
            "键名含:冒号": "值含 - 前缀",
            "键名像json": '{"a": 1}',
        }
        self.check(obj, 30)

    def test_scalars_at_top(self):
        for v in ["hello", "中文", 123, 4.5, True, None, "x" * 100]:
            self.check(v, 16)

    def test_width_too_small(self):
        with self.assertRaises(ValueError):
            render({"k": "v"}, width=3)

    def test_random_roundtrip(self):
        """随机结构对拍（固定种子，可复现）。"""
        import random

        rng = random.Random(20261005)

        def gen(depth):
            if depth == 0 or rng.random() < 0.4:
                return rng.choice([
                    rng.randint(-1000, 1000),
                    rng.random(),
                    "".join(rng.choice("ab中文🎉 \\\":\n") for _ in range(rng.randint(0, 30))),
                    True, None,
                ])
            if rng.random() < 0.5:
                return [gen(depth - 1) for _ in range(rng.randint(0, 5))]
            return {
                "".join(rng.choice("key键🎉") for _ in range(rng.randint(1, 8))): gen(depth - 1)
                for _ in range(rng.randint(0, 5))
            }

        for trial in range(200):
            obj = gen(4)
            width = rng.choice([12, 20, 33, 80])
            text = render(obj, width=width)
            assert_widths(self, text, width)
            self.assertEqual(parse(text), obj, f"trial={trial} width={width}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

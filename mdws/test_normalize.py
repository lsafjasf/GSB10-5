"""mdws.normalize 自测：python3 -m unittest mdws.test_normalize -v"""

import hashlib
import unittest

from mdws import normalize as nz


def rule_ids(changes):
    return [(c.line, tuple(c.rules)) for c in changes]


class PureTextTests(unittest.TestCase):
    def test_collapse_trim_blank_lead(self):
        src = "   Hello    world\t! \n\t \nsecond\tline \n"
        res = nz.normalize(src)
        self.assertEqual(res.text, "Hello world !\n\nsecond line\n")
        ids = dict(rule_ids(res.changes))
        self.assertEqual(
            ids,
            {
                1: (nz.R_TRIM, nz.R_LEAD, nz.R_COLLAPSE),
                2: (nz.R_BLANK,),
                3: (nz.R_TRIM, nz.R_COLLAPSE),
            },
        )

    def test_crlf_outside_code(self):
        res = nz.normalize("a   b\r\n\r\nc\r")
        self.assertEqual(res.text, "a b\n\nc")
        self.assertTrue(any(nz.R_EOL in r for _, r in rule_ids(res.changes)))
        # 快路径行（首字符为普通文字）的 CRLF 也要记录 R1-EOL。
        plain = nz.normalize("plain line\r\n")
        self.assertEqual(plain.text, "plain line\n")
        self.assertEqual(rule_ids(plain.changes), [(1, (nz.R_EOL,))])

    def test_line_count_unchanged(self):
        src = "a\n\n   \nb    \n"
        self.assertEqual(src.count("\n"), nz.normalize(src).text.count("\n"))


class FencedCodeTests(unittest.TestCase):
    SRC = (
        "before\t\ttext  \n"
        "```\n"
        "    def  f( ):   \n"
        "\t  raw\tline \n"
        "\n"
        "```\n"
        "- a \n"
    )

    def test_code_bytes_identical(self):
        res = nz.normalize(self.SRC)
        before = [l.encode() for l in nz.protected_lines(self.SRC)]
        after = [l.encode() for l in nz.protected_lines(res.text)]
        self.assertEqual(before, after)
        digest = hashlib.sha256(b"".join(before)).hexdigest()
        for chunk, d in zip(before, after):
            self.assertEqual(hashl := hashlib.sha256(chunk).hexdigest(),
                             hashlib.sha256(d).hexdigest())
        self.assertIn("    def  f( ):   ", res.text)
        self.assertIn("\t  raw\tline ", res.text)
        self.assertEqual(digest, hashlib.sha256(b"".join(after)).hexdigest())

    def test_fence_line_still_normalized(self):
        # 围栏开/闭行不在保护区：其行尾空白仍会被规范。
        src = "```   \ncode\n```  \n"
        res = nz.normalize(src)
        self.assertEqual(res.text, "```\ncode\n```\n")

    def test_tilde_fence_and_crlf_inside(self):
        src = "p  \r\n~~~\r\n  x\t \r\n~~~\r\n"
        res = nz.normalize(src)
        self.assertIn(b"  x\t \r", res.text.encode())


class IndentedCodeTests(unittest.TestCase):
    def test_indented_block_protected(self):
        src = "para\n\n    def f():  \n        return 1 \n\nnext para \n"
        res = nz.normalize(src)
        self.assertEqual(
            res.text,
            "para\n\n    def f():  \n        return 1 \n\nnext para\n",
        )
        protected = nz.protected_lines(src)
        self.assertEqual(
            protected, ["    def f():  ", "        return 1 ", ""]
        )

    def test_lazy_continuation_not_code(self):
        # 段落延续行的 4 空格缩进不是代码块：会被规范化。
        src = "first line\n    continued \n"
        res = nz.normalize(src)
        self.assertEqual(res.text, "first line\ncontinued\n")


class QuoteTests(unittest.TestCase):
    def test_quote_structure_preserved(self):
        src = ">   Hello    world  \n>second\tline \n>   \n"
        res = nz.normalize(src)
        self.assertEqual(res.text, "> Hello world  \n> second line\n>\n")
        ids = dict(rule_ids(res.changes))
        self.assertIn(nz.R_COLLAPSE, ids[1])
        self.assertIn(nz.R_HARD, ids[1])
        self.assertIn(nz.R_TRIM, ids[3])
        self.assertEqual(res.text.count(">"), src.count(">"))
        self.assertEqual(res.text.count("\n"), src.count("\n"))

    def test_quote_with_code_fence(self):
        src = "> ```\n>   not code   \n> ```\n"
        res = nz.normalize(src)
        # 未使用 CommonMark 围栏中断规则时，引用内的 ``` 只当普通文本；
        # 这里明确声明：本库不做嵌套解析，'>' 之后的文本仍规范化。
        self.assertNotIn("not code   ", res.text)
        self.assertIn("not code", res.text)


class HardBreakTests(unittest.TestCase):
    def test_hard_vs_soft_break(self):
        src = "hard line  \nnext\nsoft line \nnext\nthree   \nx\n"
        res = nz.normalize(src)
        self.assertEqual(res.text,
                         "hard line  \nnext\nsoft line\nnext\nthree  \nx\n")
        ids = dict(rule_ids(res.changes))
        self.assertNotIn(1, ids)            # 恰好 2 空格：保持不变
        self.assertNotIn(2, ids)
        self.assertEqual(ids[3], (nz.R_TRIM,))
        self.assertEqual(ids[5], (nz.R_HARD,))

    def test_backslash_hard_break_untouched(self):
        # '\' 结尾的硬换行不涉及空白，规范化不应改动。
        src = "line\\   \n"
        res = nz.normalize(src)
        self.assertEqual(res.text, "line\\  \n")


class IdempotencyTests(unittest.TestCase):
    def test_twice_equal_once(self):
        docs = [
            FencedCodeTests.SRC,
            ">   a    b  \n> c \n\n    keep\t  \n",
            "x   y \n\n```\n\traw  \n```\n- z \n",
        ]
        for d in docs:
            once = nz.normalize(d).text
            twice = nz.normalize(once).text
            self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main(verbosity=2)

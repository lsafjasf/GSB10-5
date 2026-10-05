"""password_policy 的自测（标准库 unittest）。

覆盖：全部通过、只违反一条、违反多条（全部列出且有序）、
长度边界（刚好等于最小/最大、刚好小于最小、刚好超出最大）、
多字节字符与组合序列的长度口径。
"""

import unittest

from password_policy import DEFAULT_COMMON_PASSWORDS, PasswordPolicy


def make_policy(**overrides):
    base = dict(
        min_length=8,
        max_length=16,
        require_lower=True,
        require_upper=True,
        require_digit=True,
        require_symbol=True,
        no_whitespace=True,
        max_repeat=3,
        common_passwords=DEFAULT_COMMON_PASSWORDS,
        username="alice",
    )
    base.update(overrides)
    return PasswordPolicy(**base)


class TestAllPass(unittest.TestCase):
    def test_all_pass(self):
        res = make_policy().validate("Str0ng!pw")
        self.assertTrue(res.ok)
        self.assertEqual(res.violations, ())
        self.assertTrue(all(r.passed for r in res.results))
        self.assertEqual(res.char_length, 9)
        self.assertEqual(res.byte_length, 9)


class TestSingleViolation(unittest.TestCase):
    def test_only_missing_digit(self):
        res = make_policy().validate("Strong!pw")  # 9 字符，只差数字
        self.assertFalse(res.ok)
        self.assertEqual(len(res.violations), 1)
        self.assertEqual(res.violations[0].rule_id, "require_digit")
        self.assertIn("缺少数字", res.violations[0].message)


class TestMultipleViolations(unittest.TestCase):
    def test_all_violations_listed_in_order(self):
        # 太短、缺大写、缺符号、含空白、含用户名 -> 5 条违规
        res = make_policy().validate("alice 1")
        self.assertFalse(res.ok)
        got = [r.rule_id for r in res.violations]
        self.assertEqual(
            got,
            [
                "min_length",
                "require_upper",
                "require_symbol",
                "no_whitespace",
                "not_contains_username",
            ],
        )
        # 顺序必须是策略表定义的可读顺序，而非只报第一条
        all_ids = [r.rule_id for r in res.results]
        self.assertEqual(got, [i for i in all_ids if i in got])

    def test_common_password_and_others(self):
        res = make_policy().validate("password")
        ids = [r.rule_id for r in res.violations]
        self.assertIn("not_common", ids)
        self.assertIn("require_upper", ids)
        self.assertIn("require_digit", ids)
        self.assertIn("require_symbol", ids)


class TestLengthBoundaries(unittest.TestCase):
    def setUp(self):
        self.policy = make_policy()

    def test_exactly_min_length_passes(self):
        pw = "Aa1!bcde"  # 刚好 8 字符
        self.assertEqual(len(pw), 8)
        res = self.policy.validate(pw)
        self.assertTrue(res.ok, res.explain())

    def test_one_below_min_length_fails(self):
        pw = "Aa1!bcd"  # 7 字符
        res = self.policy.validate(pw)
        self.assertFalse(res.ok)
        self.assertEqual([r.rule_id for r in res.violations], ["min_length"])

    def test_exactly_max_length_passes(self):
        pw = "Aa1!" + "bcdefghijklm"  # 刚好 16 字符
        self.assertEqual(len(pw), 16)
        res = self.policy.validate(pw)
        self.assertTrue(res.ok, res.explain())

    def test_one_above_max_length_fails(self):
        pw = "Aa1!" + "bcdefghijklmn"  # 17 字符，刚好超出最大长度
        self.assertEqual(len(pw), 17)
        res = self.policy.validate(pw)
        self.assertFalse(res.ok)
        self.assertEqual([r.rule_id for r in res.violations], ["max_length"])


class TestUnicodeLengthSemantics(unittest.TestCase):
    def test_multibyte_counts_as_characters_not_bytes(self):
        # "口令口令Aa1!" = 8 个字符，但 UTF-8 是 16 字节
        pw = "口令口令Aa1!"
        res = make_policy().validate(pw)
        self.assertEqual(res.char_length, 8)
        self.assertEqual(res.byte_length, 16)
        self.assertTrue(res.ok, res.explain())  # 按字符口径应通过最小长度 8

    def test_precomposed_vs_combining_sequence(self):
        # "é" 单码点（NFC）计 1；"e"+"◌́"（NFD 组合序列）计 2
        nfc = "é"
        nfd = "e\u0301"
        self.assertEqual(len(nfc), 1)
        self.assertEqual(len(nfd), 2)
        res_nfc = make_policy(min_length=1, max_length=1).validate(nfc)
        res_nfd = make_policy(min_length=1, max_length=1).validate(nfd)
        self.assertEqual(res_nfc.char_length, 1)
        self.assertEqual(res_nfd.char_length, 2)
        # 视觉上同是"一个字符"，组合序列在 max_length=1 下超界
        max_nfc = [r for r in res_nfc.results if r.rule_id == "max_length"][0]
        max_nfd = [r for r in res_nfd.results if r.rule_id == "max_length"][0]
        self.assertTrue(max_nfc.passed)
        self.assertFalse(max_nfd.passed)

    def test_emoji_counts_as_one_character(self):
        res = make_policy().validate("Aa1!😀😀😀x")
        self.assertEqual(res.char_length, 8)
        self.assertEqual(res.byte_length, 17)


class TestExplainOutput(unittest.TestCase):
    def test_explain_contains_every_rule_and_summary(self):
        res = make_policy().validate("short")
        text = res.explain()
        for r in res.results:
            self.assertIn(r.rule_id, text)
        self.assertIn("未通过", text)
        self.assertIn("字符", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

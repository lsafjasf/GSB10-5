"""password_policy 自测（标准库 unittest）。

运行: python3 -m unittest -v test_password_policy
"""
import unittest

from password_policy import PasswordPolicy


class PasswordPolicyTest(unittest.TestCase):
    def setUp(self):
        # min=8, max=16，便于构造边界用例
        self.policy = PasswordPolicy(min_length=8, max_length=16)

    def rule(self, report, rule_id):
        return next(r for r in report.results if r.rule_id == rule_id)

    # ---- 全部通过 ----
    def test_all_pass(self):
        report = self.policy.validate("Str0ng!Pass")
        self.assertTrue(report.ok)
        self.assertEqual(report.violations, ())
        self.assertTrue(all(r.passed for r in report.results))

    # ---- 只违反一条 ----
    def test_single_violation_only_short(self):
        report = self.policy.validate("Aa1!xyz")          # 7 字符，其余条目均满足
        self.assertFalse(report.ok)
        self.assertEqual([r.rule_id for r in report.violations], ["min_length"])

    def test_single_violation_only_missing_digit(self):
        report = self.policy.validate("Abcdefg!x")        # 缺数字，其余满足
        self.assertEqual([r.rule_id for r in report.violations], ["require_digit"])

    # ---- 违反多条：全部列出且顺序固定 ----
    def test_multiple_violations_all_listed_in_order(self):
        report = self.policy.validate("abc")              # 短 + 缺大写 + 缺数字 + 缺符号
        ids = [r.rule_id for r in report.violations]
        self.assertEqual(ids, ["min_length", "require_upper", "require_digit", "require_symbol"])
        # 通过项与未通过项都在明细里
        self.assertTrue(self.rule(report, "require_lower").passed)
        self.assertEqual(len(report.results), 9)

    def test_multiple_violations_independent(self):
        # 长度已失败时，其余条目仍然照常判定（独立判定）
        report = self.policy.validate("a" * 20 + "AAA1!")  # 超长 + 连续重复 20 次
        ids = [r.rule_id for r in report.violations]
        self.assertIn("max_length", ids)
        self.assertIn("max_repeat", ids)

    # ---- 边界长度 ----
    def test_boundary_exactly_min_length_passes(self):
        report = self.policy.validate("Aa1!wxyz")          # 恰好 8 字符
        self.assertTrue(self.rule(report, "min_length").passed)
        self.assertTrue(report.ok)

    def test_boundary_min_minus_one_fails(self):
        report = self.policy.validate("Aa1!wxy")           # 7 字符
        r = self.rule(report, "min_length")
        self.assertFalse(r.passed)
        self.assertIn("还差 1 字符", r.message)

    def test_boundary_exactly_max_length_passes(self):
        report = self.policy.validate("Aa1!" + "wxyz" * 3)  # 恰好 16 字符
        self.assertEqual(report.length_chars, 16)
        self.assertTrue(self.rule(report, "max_length").passed)
        self.assertTrue(report.ok)

    def test_boundary_max_plus_one_fails(self):
        report = self.policy.validate("Aa1!" + "wxyz" * 3 + "q")  # 17 字符
        r = self.rule(report, "max_length")
        self.assertFalse(r.passed)
        self.assertIn("超出 1 字符", r.message)

    # ---- 长度口径：字符数（码点数），不是字节数 ----
    def test_multibyte_counts_as_characters_not_bytes(self):
        # “密码” 2 个汉字 = 2 字符 / 6 字节 UTF-8
        report = self.policy.validate("密码Ab1!xy")        # 8 字符，按字符口径应通过
        self.assertEqual(report.length_chars, 8)
        self.assertEqual(report.length_bytes_utf8, 12)
        self.assertTrue(self.rule(report, "min_length").passed)
        self.assertTrue(report.ok)

    def test_combining_sequence_counts_each_codepoint(self):
        # "e\u0301"（e + 组合重音符）= 2 个码点；预组合 "é" = 1 个码点
        decomposed = self.policy.validate("e\u0301" + "Aa1!wxy")   # 9 字符
        precomposed = self.policy.validate("\u00e9" + "Aa1!wxy")   # 8 字符
        self.assertEqual(decomposed.length_chars, 9)
        self.assertEqual(precomposed.length_chars, 8)
        self.assertTrue(decomposed.ok)
        self.assertTrue(precomposed.ok)

    def test_combining_sequence_with_normalize(self):
        # normalize=True 时先做 NFC，组合序列被合并为 1 个字符
        policy = PasswordPolicy(min_length=9, max_length=16, normalize=True)
        report = policy.validate("e\u0301" + "Aa1!wxyz")   # NFC 后 9 字符
        self.assertEqual(report.length_chars, 9)
        self.assertTrue(self.rule(report, "min_length").passed)

    # ---- 其他条目 ----
    def test_common_password_rejected(self):
        report = self.policy.validate("P@ssw0rd")
        self.assertIn("not_common", [r.rule_id for r in report.violations])

    def test_whitespace_rejected(self):
        report = self.policy.validate("Aa1! wx yz")
        self.assertIn("no_whitespace", [r.rule_id for r in report.violations])

    def test_consecutive_repeat_rejected(self):
        report = self.policy.validate("Abc1!wxyaaaa")      # a 连续 4 次 > 3
        self.assertIn("max_repeat", [r.rule_id for r in report.violations])

    def test_username_contained_rejected(self):
        policy = PasswordPolicy(min_length=8, max_length=16, username="alice")
        report = policy.validate("Alice1!xyz")
        self.assertIn("no_username", [r.rule_id for r in report.violations])

    def test_explain_lists_every_rule(self):
        text = self.policy.validate("abc").explain()
        self.assertIn("最小长度", text)
        self.assertIn("缺少大写字母", text)
        self.assertIn("缺少数字", text)
        self.assertIn("缺少符号", text)
        self.assertIn("4 条策略未通过", text)

    def test_type_error_on_non_str(self):
        with self.assertRaises(TypeError):
            self.policy.validate(12345678)


if __name__ == "__main__":
    unittest.main()

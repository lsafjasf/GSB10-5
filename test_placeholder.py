"""placeholder_lib 自测：顺序调整 / 缺失 / 多余 / 重复 / 格式说明 / 边界。"""

import unittest

from placeholder_lib import (
    PlaceholderMismatchError,
    parse_placeholders,
    restore,
    signature_counter,
    validate,
)


class ParseTests(unittest.TestCase):
    def test_basic_and_order_preserved(self):
        toks = parse_placeholders("%s 有 %d 条消息")
        self.assertEqual([t.signature for t in toks], ["%s", "%d"])

    def test_format_specs(self):
        sigs = [t.signature for t in parse_placeholders("%+08.3f|%-10s|%05d|%.2f")]
        self.assertEqual(sigs, ["%+08.3f", "%-10s", "%05d", "%.2f"])

    def test_named_params(self):
        toks = parse_placeholders("%(user_name)s: %(count)05d")
        self.assertEqual([t.signature for t in toks],
                         ["%(user_name)s", "%(count)05d"])

    def test_literal_percent_not_placeholder(self):
        self.assertEqual(parse_placeholders("100%% 完成"), [])
        toks = parse_placeholders("%d%% of %s")
        self.assertEqual([t.signature for t in toks], ["%d", "%s"])

    def test_dangling_percent_is_literal_text(self):
        # 末尾单独的 % 不构成合法占位符，按普通文本处理
        self.assertEqual([t.signature for t in parse_placeholders("折扣 80%")], [])
        self.assertEqual([t.signature for t in parse_placeholders("结尾%")], [])

    def test_different_specs_are_different_signatures(self):
        c = signature_counter("%s %5s")
        self.assertEqual(c["%s"], 1)
        self.assertEqual(c["%5s"], 1)


class ValidateTests(unittest.TestCase):
    def test_order_changed_is_ok(self):
        r = validate("Hello %s, you have %d messages", "%d 条消息，%s 你好")
        self.assertTrue(r.ok)

    def test_duplicate_placeholders_ok_when_count_equal(self):
        r = validate("%s and %s", "%s 与 %s")
        self.assertTrue(r.ok)

    def test_missing(self):
        r = validate("%s 有 %d 条消息", "%s 有几条消息")
        self.assertFalse(r.ok)
        self.assertEqual([(m.signature, m.expected_count) for m in r.missing],
                         [("%d", 1)])
        self.assertEqual(r.extra, [])
        self.assertEqual(r.count_mismatches, [])

    def test_extra(self):
        r = validate("你好 %s", "你好 %s 余额 %.2f")
        self.assertFalse(r.ok)
        self.assertEqual([(e.signature, e.actual_count) for e in r.extra],
                         [("%.2f", 1)])
        self.assertEqual(r.missing, [])

    def test_count_mismatch_fewer(self):
        r = validate("%s %s %d", "%s %d")
        self.assertEqual([(c.signature, c.expected_count, c.actual_count)
                          for c in r.count_mismatches],
                         [("%s", 2, 1)])
        self.assertEqual(r.missing, [])
        self.assertEqual(r.extra, [])

    def test_count_mismatch_more(self):
        r = validate("%s", "%s %s")
        self.assertEqual([(c.signature, c.expected_count, c.actual_count)
                          for c in r.count_mismatches],
                         [("%s", 1, 2)])

    def test_missing_and_extra_coexist(self):
        r = validate("%s %d", "%s %f")
        self.assertEqual([m.signature for m in r.missing], ["%d"])
        self.assertEqual([e.signature for e in r.extra], ["%f"])

    def test_format_spec_mismatch_is_reported(self):
        # %s 与 %5s 是不同签名，互相记为缺失和多余
        r = validate("值 %s", "值 %5s")
        self.assertEqual([m.signature for m in r.missing], ["%s"])
        self.assertEqual([e.signature for e in r.extra], ["%5s"])

    def test_named_missing(self):
        r = validate("%(user)s/%(id)d", "%(user)s")
        self.assertEqual([m.signature for m in r.missing], ["%(id)d"])

    def test_empty_and_no_placeholder(self):
        self.assertTrue(validate("", "").ok)
        self.assertTrue(validate("纯文本无占位符", "plain text").ok)


class RestoreTests(unittest.TestCase):
    def test_restore_reordered_positional(self):
        out = restore("Hello %s, you have %d messages",
                      "%d 条消息，%s 你好", ("Alice", 5))
        self.assertEqual(out, "5 条消息，Alice 你好")

    def test_restore_named(self):
        out = restore("%(user)s 有 %(count)d 条",
                      "%(count)d messages for %(user)s",
                      named_values={"user": "Bob", "count": 3})
        self.assertEqual(out, "3 messages for Bob")

    def test_restore_applies_width_precision_flags(self):
        out = restore("比例 %08.3f 占比 %-5s|",
                      "ratio=%08.3f name=[%-5s]", (3.14159, "ab"))
        self.assertEqual(out, "ratio=0003.142 name=[ab   ]")

    def test_restore_duplicate_same_signature_fifo(self):
        # 同签名重复出现：按原文顺序绑定、按译文顺序消费（FIFO）
        out = restore("%s 和 %s", "%s & %s", ("甲", "乙"))
        self.assertEqual(out, "甲 & 乙")

    def test_restore_literal_percent(self):
        out = restore("进度 %d%%", "done %d%%", (90,))
        self.assertEqual(out, "done 90%")

    def test_restore_extra_in_target_raises(self):
        with self.assertRaises(PlaceholderMismatchError):
            restore("%s", "%s %d", ("x",))

    def test_restore_missing_in_target_raises(self):
        with self.assertRaises(ValueError):
            restore("%s %d", "%s", ("x", 2))

    def test_restore_not_enough_values_raises(self):
        with self.assertRaises(PlaceholderMismatchError):
            restore("%s %d", "%d %s", ("only-one",))

    def test_restore_too_many_values_raises(self):
        with self.assertRaises(PlaceholderMismatchError):
            restore("%s", "%s", ("a", "b"))

    def test_restore_star_width_raises_value_error(self):
        with self.assertRaises(ValueError):
            restore("%*d", "%*d", (3, 5))

    def test_restore_positional_and_named_mixed(self):
        out = restore("%(name)s 得分 %d，比率 %.2f",
                      "ratio=%.2f score=%d player=%(name)s",
                      (88, 0.75), named_values={"name": "张三"})
        self.assertEqual(out, "ratio=0.75 score=88 player=张三")

    def test_edge_adjacent_and_boundary_placeholders(self):
        out = restore("%s%s", "%s-%s", ("a", "b"))
        self.assertEqual(out, "a-b")
        self.assertEqual(restore("%s!", "%s?", ("hi",)), "hi?")
        self.assertEqual(restore("!%s", "?%s", ("hi",)), "?hi")

    def test_empty_text(self):
        self.assertEqual(restore("", "", ()), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)

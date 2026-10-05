import unittest

from phcheck import scan


def idents(text):
    return [t.ident for t in scan(text).tokens]


class TestParser(unittest.TestCase):
    def test_named_with_width_precision(self):
        result = scan("%(user)s 剩余 %(amount)08.2f 元")
        self.assertEqual(result.invalid, ())
        self.assertEqual(idents("%(user)s 剩余 %(amount)08.2f 元"), ["%(user)s", "%(amount)f"])
        tok = result.tokens[1]
        self.assertEqual(tok.flags, "0")
        self.assertEqual(tok.width, "8")
        self.assertEqual(tok.precision, "2")
        self.assertEqual(tok.style, "named")

    def test_positional(self):
        result = scan("%2$.3f")
        self.assertEqual(result.invalid, ())
        tok = result.tokens[0]
        self.assertEqual(tok.index, 2)
        self.assertEqual(tok.precision, "3")
        self.assertEqual(tok.ident, "%2$f")
        self.assertEqual(tok.style, "positional")

    def test_bare_ordinals(self):
        result = scan("%s %s %d")
        self.assertEqual([t.ordinal for t in result.tokens], [1, 2, 3])
        self.assertEqual(idents("%s %s %d"), ["%s", "%s", "%d"])

    def test_literal_percent(self):
        result = scan("100%%")
        self.assertEqual(result.tokens, ())
        self.assertEqual(result.invalid, ())
        self.assertEqual("".join(c.text for c in result.chunks), "100%")

    def test_literal_percent_inside_text(self):
        result = scan("a%%b")
        self.assertEqual("".join(c.text for c in result.chunks), "a%b")

    def test_flags_and_length(self):
        result = scan("%-+5d")
        self.assertEqual(result.invalid, ())
        self.assertEqual(result.tokens[0].flags, "-+")
        named = scan("%(n)hd")
        self.assertEqual(named.invalid, ())
        self.assertEqual(named.tokens[0].length, "h")
        self.assertEqual(named.tokens[0].ident, "%(n)d")
        length_ll = scan("%lld")
        self.assertEqual(length_ll.invalid, ())
        self.assertEqual(length_ll.tokens[0].length, "ll")
        self.assertEqual(length_ll.tokens[0].ident, "%d")

    def test_bare_dot_precision(self):
        result = scan("%(v).f")
        self.assertEqual(result.invalid, ())
        self.assertEqual(result.tokens[0].precision, "")

    def test_invalid_conversion(self):
        result = scan("%z")
        self.assertEqual(len(result.invalid), 1)
        self.assertIn("转换字符", result.invalid[0].reason)

    def test_dangling_percent(self):
        result = scan("abc%")
        self.assertEqual(len(result.invalid), 1)
        self.assertIn("悬空", result.invalid[0].reason)

    def test_dynamic_width_star(self):
        self.assertIn("动态", scan("%*d").invalid[0].reason)
        self.assertIn("动态", scan("%.*f").invalid[0].reason)

    def test_empty_key(self):
        self.assertIn("空命名键", scan("%()s").invalid[0].reason)

    def test_zero_index(self):
        self.assertIn("从 1 开始", scan("%0$s").invalid[0].reason)

    def test_mixing_styles(self):
        self.assertIn("混用", scan("%(a)s %1$s").invalid[0].reason)
        self.assertIn("混用", scan("%s %1$d").invalid[0].reason)
        self.assertIn("混用", scan("%1$s %s").invalid[0].reason)

    def test_modified_percent_literal(self):
        self.assertIn("字面量", scan("%5%").invalid[0].reason)
        self.assertIn("字面量", scan("%(a)%").invalid[0].reason)

    def test_named_and_positional_together(self):
        self.assertIn("不能同时出现", scan("%(a)1$s").invalid[0].reason)

    def test_multiple_invalid_accumulate(self):
        result = scan("%z 然后 %q")
        self.assertEqual(len(result.invalid), 2)

    def test_spec_key_distinguishes_format(self):
        a = scan("%(count)05d").tokens[0]
        b = scan("%(count)5d").tokens[0]
        self.assertEqual(a.ident, b.ident)
        self.assertNotEqual(a.spec_key, b.spec_key)


if __name__ == "__main__":
    unittest.main()

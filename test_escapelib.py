"""Injection-attempt tests for escapelib. Run: python3 test_escapelib.py -v"""
import json
import re
import unittest

from escapelib import (
    SafeString,
    UnsafeUrlError,
    escape_attr,
    escape_css,
    escape_css_in_attr,
    escape_css_url,
    escape_js_string,
    escape_json_in_script,
    escape_text,
    escape_url,
    escape_url_in_attr,
)



def _all_quotes_escaped(s):
    """True if every quote char is preceded by an odd-length backslash run."""
    backslashes = 0
    for ch in s:
        if ch == "\\":
            backslashes += 1
        elif ch in "\"'":
            if backslashes % 2 == 0:
                return False
            backslashes = 0
        else:
            backslashes = 0
    return True

def assert_no_raw(testcase, escaped, chars, ctx):
    for ch in chars:
        testcase.assertNotIn(
            ch, escaped, f"{ctx}: raw {ch!r} survived in {escaped!r}"
        )


class TextContextTest(unittest.TestCase):
    PAYLOADS = [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "<svg/onload=alert(1)>",
        "&lt;script&gt;",          # pre-escaped-looking input
        "a & b < c > d",
        "\"><iframe src=//evil>",
    ]

    def test_no_tag_survives(self):
        for p in self.PAYLOADS:
            out = escape_text(p)
            assert_no_raw(self, out, "<>", "text")
            self.assertNotIn("<script", out.lower())

    def test_ampersand_first(self):
        # & must become &amp; so &lt; in input cannot resurrect as <
        self.assertEqual(escape_text("&lt;"), "&amp;lt;")
        self.assertEqual(escape_text("a & b"), "a &amp; b")


class AttrContextTest(unittest.TestCase):
    PAYLOADS = [
        '" onmouseover="alert(1)',
        "' onclick='alert(1)",
        '"><script>alert(1)</script>',
        "x`onfocus=alert(1)`",
        '" autofocus onfocus="alert(1)',
    ]

    def test_cannot_break_quotes(self):
        for p in self.PAYLOADS:
            out = escape_attr(p)
            # No raw quote or angle bracket may survive: the value cannot
            # terminate its attribute nor open a new tag.
            assert_no_raw(self, out, ['"', "'", "<", ">"], "attr")

    def test_entities(self):
        self.assertEqual(escape_attr('a"b\'c'), "a&quot;b&#x27;c")


class JsStringContextTest(unittest.TestCase):
    PAYLOADS = [
        '";alert(1);//',
        "';alert(1);//",
        "</script><script>alert(1)</script>",
        "</ScRiPt><img src=x onerror=alert(1)>",
        "\\\nalert(1)",
        "line1\nline2\r\nline3",
        "  ",  # U+2028 / U+2029
        "\x00\x01\x1f\x7f",
    ]

    def test_cannot_escape_string_or_script(self):
        for p in self.PAYLOADS:
            out = escape_js_string(p)
            # No raw newline or angle bracket may survive.
            assert_no_raw(self, out, ["\n", "\r", "<", ">"], "js")
            # Every quote must be escaped (odd-length backslash run).
            self.assertTrue(_all_quotes_escaped(out), f"js: {out!r}")
            self.assertNotIn("</script", out.lower())
            self.assertNotIn(" ", out)
            self.assertNotIn(" ", out)

    def test_control_chars_hex_escaped(self):
        out = escape_js_string("\x00\x01")
        self.assertEqual(out, "\\0\\x01")


class CssContextTest(unittest.TestCase):
    PAYLOADS = [
        "red;}</style><script>alert(1)</script>",
        "expression(alert(1))",
        "url(javascript:alert(1))",
        "red;background:url(evil)",
        '";behavior:url(xss.htc);"',
    ]

    def test_no_css_metachar_survives(self):
        for p in self.PAYLOADS:
            out = escape_css(p)
            # Every structural char is backslash-hex escaped.
            assert_no_raw(self, out, list("{};<>()\"'"), "css")
            self.assertNotIn("</style", out.lower())

    def test_identifier_passthrough(self):
        self.assertEqual(escape_css("red"), "red")
        self.assertEqual(escape_css("a1Z"), "a1Z")
        self.assertEqual(escape_css("}"), "\\7D ")


class UrlContextTest(unittest.TestCase):
    EVIL = [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "java\tscript:alert(1)",
        "java\nscript:alert(1)",
        "  javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "\x01javascript:alert(1)",
    ]

    GOOD = [
        "https://example.com/a b?x=1&y=2",
        "http://example.com/%20already",
        "/relative/path?q=你好",
        "mailto:a@b.com",
        "#anchor",
    ]

    def test_evil_schemes_rejected(self):
        for p in self.EVIL:
            with self.assertRaises(UnsafeUrlError, msg=p):
                escape_url(p)

    def test_good_urls_percent_encoded(self):
        for p in self.GOOD:
            out = escape_url(p)
            self.assertNotIn(" ", out)
            self.assertNotIn("<", out)
        self.assertEqual(
            escape_url("https://example.com/a b"),
            "https://example.com/a%20b",
        )

    def test_unicode_encoded(self):
        out = escape_url("/q=你好")
        self.assertNotIn("你", out)


class NestedContextTest(unittest.TestCase):
    def test_url_in_attr(self):
        out = escape_url_in_attr('https://e.com/a?x=" onclick="evil')
        assert_no_raw(self, out, ['"', "'"], "url_in_attr")
        self.assertIsInstance(out, SafeString)

    def test_url_in_attr_evil_scheme(self):
        with self.assertRaises(UnsafeUrlError):
            escape_url_in_attr("javascript:alert(1)")

    def test_url_in_css(self):
        # Try to break out of url("...") with parens, quotes, backslash.
        payload = 'x");}body{background:url(javascript:alert(1))}//'
        out = escape_css_url(payload)
        assert_no_raw(self, out, ['(', ')', '"', "'", "\\", " ", "<"], "css_url")

    def test_url_in_css_evil_scheme(self):
        with self.assertRaises(UnsafeUrlError):
            escape_css_url("javascript:alert(1)")

    def test_css_in_attr(self):
        out = escape_css_in_attr('color:red" onmouseover="alert(1)')
        assert_no_raw(self, out, ['"', "'"], "css_in_attr")

    def test_json_in_script(self):
        payload = {
            "x": "</script><script>alert(1)</script>",
            "y": "  &<>",
        }
        out = escape_json_in_script(payload)
        self.assertNotIn("</script", out.lower())
        assert_no_raw(self, out, ["<", ">", "&"], "json_script")
        # Still valid JSON after the browser parses the script.
        self.assertEqual(json.loads(out), json.loads(json.dumps(payload)))


class IdempotencyTest(unittest.TestCase):
    """Already-escaped content must NOT be double-escaped into mojibake."""

    RAW = '<b>"Tom & Jerry"</b>\n'

    def test_double_escape_same_context_is_noop(self):
        for fn in (escape_text, escape_attr, escape_js_string, escape_css):
            once = fn(self.RAW)
            twice = fn(once)
            self.assertEqual(once, twice, fn.__name__)
            self.assertIs(once, twice)

    def test_no_amp_amp_mojibake(self):
        once = escape_text("a & b")
        twice = escape_text(once)
        self.assertNotIn("&amp;amp;", twice)
        self.assertEqual(twice, "a &amp; b")

    def test_url_double_escape(self):
        once = escape_url("https://e.com/a b")
        twice = escape_url(once)
        self.assertEqual(once, twice)
        self.assertNotIn("%2520", twice)  # %20 must not become %2520

    def test_different_context_still_escapes(self):
        # Composition is not double-escaping: attr rules apply on top of
        # url rules when the context actually changes.
        url = escape_url("https://e.com/?q=a'b")
        out = escape_attr(url)
        self.assertNotIn("'", out)

    def test_context_tag(self):
        self.assertEqual(escape_text("x").context, "text")
        self.assertEqual(escape_attr("x").context, "attr")


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""secescape 自测：注入防护、嵌套上下文、防二次转义、边界用例。

运行：python3 -m unittest -v test_secescape
"""

import json
import unittest
from html.parser import HTMLParser

import secescape as esc


class TagCollector(HTMLParser):
    """收集标签/属性/数据，用于结构级注入验证。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.attrs = {}
        self.data = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs[tag] = dict(attrs)

    def handle_data(self, data):
        self.data.append(data)


def parse(html):
    parser = TagCollector()
    parser.feed(html)
    parser.close()
    return parser


class TestTextContext(unittest.TestCase):
    """文本内容上下文：& < > 转义，引号保留。"""

    def test_basic_rules(self):
        self.assertEqual(esc.text("<b>&\"'</b>"), "&lt;b&gt;&amp;\"'&lt;/b&gt;")

    def test_script_tag_injection(self):
        payload = "<script>alert(1)</script>"
        html = "<div>%s</div>" % esc.text(payload)
        parser = parse(html)
        self.assertNotIn("script", parser.tags)
        self.assertIn("<script>alert(1)</script>", "".join(parser.data))

    def test_img_onerror_injection(self):
        payload = '<img src=x onerror=alert(1)>'
        html = "<p>%s</p>" % esc.text(payload)
        parser = parse(html)
        self.assertNotIn("img", parser.tags)

    def test_entity_smuggling(self):
        # 攻击者预置实体，期望浏览器解码出 <script>
        payload = "&lt;script&gt;alert(1)&lt;/script&gt;"
        out = esc.text(payload)
        parser = parse("<div>%s</div>" % out)
        self.assertNotIn("script", parser.tags)
        # 浏览器端看到的是字面文本，不会变成标签
        self.assertIn("&lt;script&gt;", "".join(parser.data))

    def test_idempotent(self):
        once = esc.text("<b>&")
        self.assertEqual(esc.text(once), once)
        self.assertEqual(esc.text(esc.text(once)), once)


class TestAttrContext(unittest.TestCase):
    """属性值上下文：在文本规则上追加 " 和 '。"""

    def test_basic_rules(self):
        self.assertEqual(
            esc.attr('<a href="x">\'&'),
            "&lt;a href=&quot;x&quot;&gt;&#x27;&amp;",
        )

    def test_quote_breakout_injection(self):
        payload = '" onmouseover="alert(1)'
        html = '<div title="%s">x</div>' % esc.attr(payload)
        parser = parse(html)
        div_attrs = parser.attrs["div"]
        self.assertEqual(set(div_attrs), {"title"})
        self.assertEqual(div_attrs["title"], '" onmouseover="alert(1)')

    def test_unquoted_style_breakout(self):
        payload = "x onclick=alert(1) y="
        html = '<div title="%s">x</div>' % esc.attr(payload)
        parser = parse(html)
        self.assertEqual(set(parser.attrs["div"]), {"title"})

    def test_tag_injection_via_attr(self):
        payload = '"><script>alert(1)</script>'
        html = '<div title="%s">x</div>' % esc.attr(payload)
        parser = parse(html)
        self.assertNotIn("script", parser.tags)

    def test_attr_pair_name_validation(self):
        self.assertEqual(esc.attr_pair("title", "a<b"), 'title="a&lt;b"')
        with self.assertRaises(ValueError):
            esc.attr_pair('x" onclick="', "v")

    def test_idempotent(self):
        once = esc.attr('a"b<c&')
        self.assertEqual(esc.attr(once), once)


class TestScriptContext(unittest.TestCase):
    """脚本内容上下文：JSON 序列化 + < 与 U+2028/2029 处理。"""

    def test_basic_rules(self):
        self.assertEqual(esc.js_string('he said "hi"'), '"he said \\"hi\\""')
        self.assertEqual(esc.js_value(42), "42")
        self.assertEqual(esc.js_value([1, "a"]), '[1, "a"]')

    def test_close_script_injection(self):
        payload = "</script><script>alert(1)</script>"
        out = esc.js_string(payload)
        self.assertNotIn("</script", out.lower())
        html = "<script>var x = %s;</script>" % out
        parser = parse(html)
        self.assertEqual(parser.tags.count("script"), 1)

    def test_html_comment_injection(self):
        payload = "<!--<script>alert(1)</script>-->"
        out = esc.js_string(payload)
        self.assertNotIn("<!--", out)
        self.assertNotIn("<script", out.lower())

    def test_quote_and_backslash(self):
        payload = '";alert(1);//'
        out = esc.js_string(payload)
        # 还原为 JS 字符串值后必须等于原始输入
        self.assertEqual(json.loads(out), payload)

    def test_line_separators(self):
        payload = "a\u2028b\u2029c"
        out = esc.js_string(payload)
        self.assertNotIn("\u2028", out)
        self.assertNotIn("\u2029", out)
        self.assertEqual(json.loads(out), payload)

    def test_roundtrip_all_types(self):
        for value in ["s", 1, 1.5, True, None, [1, "a"], {"k": "</script>"}]:
            self.assertEqual(json.loads(str(esc.js_value(value))), value)

    def test_js_string_rejects_non_str(self):
        with self.assertRaises(TypeError):
            esc.js_string(123)

    def test_idempotent(self):
        once = esc.js_string("</script>")
        self.assertEqual(esc.js_string(once), once)


class TestScriptInAttrNested(unittest.TestCase):
    """嵌套：事件属性里的 JS 字符串（属性内嵌脚本）。"""

    def test_breakout_attempt(self):
        payload = '");alert(1);//'
        html = '<button onclick="f(%s)">go</button>' % esc.js_attr(payload)
        parser = parse(html)
        btn = parser.attrs["button"]
        self.assertEqual(set(btn), {"onclick"})
        # 属性解码后是合法 JS 调用 f("...")，字符串内容与原始输入一致
        handler = btn["onclick"]
        self.assertTrue(handler.startswith('f("'))
        inner = handler[2:-1]
        self.assertEqual(json.loads(inner), payload)

    def test_quote_smuggling_via_entity(self):
        # 攻击者用 &#34; 期望属性解码后变成引号逃逸
        payload = "&#34;);alert(1);//"
        html = '<button onclick="f(%s)">go</button>' % esc.js_attr(payload)
        parser = parse(html)
        handler = parser.attrs["button"]["onclick"]
        inner = handler[2:-1]
        self.assertEqual(json.loads(inner), payload)

    def test_close_script_in_attr(self):
        payload = "</script><script>alert(1)</script>"
        html = '<button onclick="f(%s)">go</button>' % esc.js_attr(payload)
        parser = parse(html)
        self.assertNotIn("script", parser.tags)


class TestStyleContext(unittest.TestCase):
    """样式内容上下文：CSS 字符串白名单 + 十六进制转义。"""

    def test_basic_rules(self):
        self.assertEqual(esc.css_string("hello world-1"), "'hello world-1'")
        self.assertEqual(esc.css_string("a'b"), "'a\\000027 b'")

    def test_close_style_injection(self):
        payload = "</style><script>alert(1)</script>"
        out = esc.css_string(payload)
        self.assertNotIn("</style", out.lower())
        html = "<style>a::before{content:%s}</style>" % out
        parser = parse(html)
        self.assertNotIn("script", parser.tags)

    def test_quote_breakout_injection(self):
        payload = "';color:red;background:url(javascript:alert(1));x:'"
        out = esc.css_string(payload)
        # 单引号被转义，无法闭合字符串
        self.assertNotIn("'", out[1:-1])
        # 冒号白名单保留，但整段仍是字符串内容，不构成声明
        self.assertTrue(out.startswith("'") and out.endswith("'"))

    def test_newline_injection(self):
        payload = "red\n}body{display:none"
        out = esc.css_string(payload)
        self.assertNotIn("\n", out)
        self.assertNotIn("}", out)

    def test_backslash_injection(self):
        payload = "a\\';alert(1);//"
        out = esc.css_string(payload)
        self.assertNotIn("\\'", out)

    def test_idempotent(self):
        once = esc.css_string("a'b<c")
        self.assertEqual(esc.css_string(once), once)


class TestUrlContext(unittest.TestCase):
    """地址上下文：协议白名单 + 控制字符清洗 + 百分号编码。"""

    DANGEROUS = [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "java\tscript:alert(1)",
        "java\nscript:alert(1)",
        "java\x00script:alert(1)",
        " javascript:alert(1)",
        "javascript&#58;alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
        "\\evil.com/path",
    ]

    def test_dangerous_urls_rejected(self):
        for payload in self.DANGEROUS:
            with self.assertRaises(esc.UnsafeURLError, msg=payload):
                esc.url(payload)

    def test_allowed_urls(self):
        cases = {
            "https://example.com/a b?x=1&y=2": "https://example.com/a%20b?x=1&y=2",
            "http://example.com/%E4%B8%AD": "http://example.com/%E4%B8%AD",
            "mailto:a@b.com": "mailto:a@b.com",
            "tel:+8613800138000": "tel:+8613800138000",
            "/relative/path?q=1": "/relative/path?q=1",
            "#anchor": "#anchor",
            "//cdn.example.com/x.js": "//cdn.example.com/x.js",
        }
        for raw, expected in cases.items():
            self.assertEqual(esc.url(raw), expected)

    def test_non_ascii_encoded(self):
        out = esc.url("https://example.com/中文")
        self.assertEqual(out, "https://example.com/%E4%B8%AD%E6%96%87")

    def test_attr_url_entity_smuggling(self):
        # 经典绕过：&#106; 解实体后变 j，拼出 javascript: 协议 -> 直接拒绝
        with self.assertRaises(esc.UnsafeURLError):
            esc.attr_url("&#106;avascript:alert(1)")
        # 良性地址里恰好出现 &# 形态时，& 被属性转义成 &amp;，
        # 属性解析后还原为字面 &#，不会触发新属性/协议
        html = '<a href="%s">x</a>' % esc.attr_url("/search?q=a&#34;x=1")
        parser = parse(html)
        href = parser.attrs["a"]["href"]
        self.assertEqual(href, "/search?q=a&#34;x=1")
        self.assertNotIn("javascript", href)

    def test_attr_url_ampersand(self):
        html = '<a href="%s">x</a>' % esc.attr_url("https://e.com/?a=1&b=2")
        parser = parse(html)
        self.assertEqual(parser.attrs["a"]["href"], "https://e.com/?a=1&b=2")

    def test_attr_url_dangerous_rejected(self):
        with self.assertRaises(esc.UnsafeURLError):
            esc.attr_url("javascript:alert(1)")

    def test_query_param(self):
        self.assertEqual(esc.query_param("a b&c=d"), "a%20b%26c%3Dd")

    def test_idempotent(self):
        once = esc.url("https://example.com/a b")
        self.assertEqual(esc.url(once), once)


class TestStyleUrlNested(unittest.TestCase):
    """嵌套：样式里的地址（CSS url()）。"""

    def test_safe_url(self):
        out = esc.css_url("https://example.com/a b.png")
        self.assertEqual(out, "'https://example.com/a%20b.png'")

    def test_close_style_via_url(self):
        payload = "https://e.com/x');}</style><script>alert(1)</script>"
        out = esc.css_url(payload)
        self.assertNotIn("</style", out.lower())
        html = "<style>a{background:url(%s)}</style>" % out
        parser = parse(html)
        self.assertNotIn("script", parser.tags)

    def test_javascript_scheme_rejected(self):
        with self.assertRaises(esc.UnsafeURLError):
            esc.css_url("javascript:alert(1)")

    def test_expression_rejected(self):
        with self.assertRaises(esc.UnsafeURLError):
            esc.css_url("vbscript:x")


class TestDoubleEscape(unittest.TestCase):
    """防二次转义：Safe 标记幂等 + escape_once 兼容历史数据。"""

    def test_safe_marker_passthrough(self):
        for fn, raw in [
            (esc.text, "<b>&"),
            (esc.attr, '"<>&'),
            (esc.js_string, "</script>"),
            (esc.css_string, "a'b"),
            (esc.url, "https://e.com/a b"),
        ]:
            once = fn(raw)
            self.assertIsInstance(once, esc.Safe)
            self.assertEqual(fn(once), once, fn.__name__)

    def test_mark_safe(self):
        self.assertTrue(esc.is_safe(esc.mark_safe("<b>ok</b>")))
        self.assertEqual(esc.text(esc.mark_safe("<b>ok</b>")), "<b>ok</b>")

    def test_escape_once_preserves_entities(self):
        self.assertEqual(esc.escape_once("&lt;b&gt;&amp;"), "&lt;b&gt;&amp;")
        self.assertEqual(esc.escape_once("&#60;&#x3C;"), "&#60;&#x3C;")

    def test_escape_once_escapes_raw(self):
        self.assertEqual(esc.escape_once("<b>&\""), "&lt;b&gt;&amp;&quot;")

    def test_escape_once_mixed(self):
        self.assertEqual(
            esc.escape_once("a&lt;b&c&#65;"),
            "a&lt;b&amp;c&#65;",
        )

    def test_escape_once_idempotent(self):
        once = esc.escape_once("<a>&lt;b&gt;&")
        self.assertEqual(esc.escape_once(once), once)

    def test_render_autoescape(self):
        out = esc.render("<p>{v}</p>", v="<script>x</script>")
        self.assertEqual(out, "<p>&lt;script&gt;x&lt;/script&gt;</p>")
        # Safe 值不被二次转义
        out2 = esc.render("<p>{v}</p>", v=esc.mark_safe("<b>ok</b>"))
        self.assertEqual(out2, "<p><b>ok</b></p>")


class TestEdgeCases(unittest.TestCase):
    """边界用例。"""

    def test_empty_string(self):
        self.assertEqual(esc.text(""), "")
        self.assertEqual(esc.attr(""), "")
        self.assertEqual(esc.js_string(""), '""')
        self.assertEqual(esc.css_string(""), "''")
        self.assertEqual(esc.url(""), "")

    def test_nul_byte(self):
        self.assertEqual(esc.text("a\x00b"), "a\ufffdb")
        self.assertEqual(esc.attr("a\x00b"), "a\ufffdb")
        self.assertEqual(esc.css_string("a\x00b"), "'a\\00fffd b'")
        with self.assertRaises(esc.UnsafeURLError):
            esc.url("java\x00script:alert(1)")

    def test_non_bmp_emoji(self):
        payload = "emoji \U0001F600 end"
        self.assertEqual(esc.text(payload), payload)
        self.assertEqual(json.loads(str(esc.js_string(payload))), payload)
        css = esc.css_string(payload)
        self.assertIn("\\01f600", css)

    def test_long_string(self):
        payload = "<script>" * 10000
        out = esc.text(payload)
        self.assertEqual(out.count("&lt;script&gt;"), 10000)

    def test_all_control_chars_in_url(self):
        # 所有 C0 控制符 + DEL 在 URL 中被清洗
        payload = "https://e.com/" + "".join(chr(i) for i in range(0x20)) + "\x7f"
        out = esc.url(payload)
        self.assertEqual(out, "https://e.com/")

    def test_url_only_colon(self):
        # 冒号开头不是合法 scheme，按相对地址处理
        self.assertEqual(esc.url(":foo"), ":foo")

    def test_surrogate_safe(self):
        # 孤立代理项不应导致崩溃
        payload = "a\ud800b"
        out = esc.text(payload)
        self.assertIn("a", out)

    def test_js_value_structures(self):
        self.assertEqual(esc.js_value({"a": [1, None, True]}), '{"a": [1, null, true]}')


if __name__ == "__main__":
    unittest.main()

"""注入测试报告：对每类上下文投放真实攻击载荷，验证无法逃逸出当前上下文。

验证方式：把转义结果嵌入真实 HTML 模板，用标准库 html.parser 做结构级
解析，检查是否出现攻击者注入的标签 / 属性 / 协议；同时做字符串级检查。
运行：python3 injection_report.py
"""

from html.parser import HTMLParser

import secescape as esc


class Structure(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.attrs = {}
        self.data = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.setdefault(tag, []).append(dict(attrs))

    def handle_data(self, data):
        self.data.append(data)


def parse(html):
    parser = Structure()
    parser.feed(html)
    parser.close()
    return parser


def check(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    print("  [%s] %s%s" % (status, name, (" -- " + detail) if detail else ""))
    return ok


results = []


def case(context, payload, render, verify):
    html = render(payload)
    parsed = parse(html)
    notes = verify(html, parsed)
    print("上下文: %s" % context)
    print("  载荷: %r" % payload)
    print("  输出: %r" % (html if len(html) < 160 else html[:157] + "..."))
    for note, passed in notes:
        results.append(check(note, passed))
    print()


# ---------------------------------------------------------------- 文本
case(
    "文本内容 text()",
    "<script>alert(1)</script>",
    lambda p: "<div>%s</div>" % esc.text(p),
    lambda html, p: [
        ("未注入 <script> 标签", "script" not in p.tags),
        ("载荷以纯文本形式呈现", "<script>alert(1)</script>" in "".join(p.data)),
    ]
)
case(
    "文本内容 text()",
    '<img src=x onerror=alert(1)>',
    lambda p: "<div>%s</div>" % esc.text(p),
    lambda html, p: [("未注入 <img> 标签", "img" not in p.tags)],
)
case(
    "文本内容 text()",
    "&lt;script&gt;alert(1)&lt;/script&gt;",
    lambda p: "<div>%s</div>" % esc.text(p),
    lambda html, p: [
        ("预置实体被二次中和，未还原出标签", "script" not in p.tags),
    ]
)

# ---------------------------------------------------------------- 属性
case(
    "属性值 attr()",
    '" onmouseover="alert(1)',
    lambda p: '<div title="%s">x</div>' % esc.attr(p),
    lambda html, p: [
        ("未逃逸出 title 属性", set(p.attrs["div"][0]) == {"title"}),
        ("未注入 onmouseover 事件", "onmouseover" not in p.attrs["div"][0]),
    ]
)
case(
    "属性值 attr()",
    '"><script>alert(1)</script>',
    lambda p: '<div title="%s">x</div>' % esc.attr(p),
    lambda html, p: [("未闭合标签注入 <script>", "script" not in p.tags)],
)
case(
    "属性值 attr()",
    "' autofocus onfocus=alert(1) x='",
    lambda p: "<div title='%s'>x</div>" % esc.attr(p),
    lambda html, p: [
        ("单引号属性同样无法逃逸", set(p.attrs["div"][0]) == {"title"}),
    ]
)

# ---------------------------------------------------------------- 脚本
case(
    "脚本内容 js_string()",
    "</script><script>alert(1)</script>",
    lambda p: "<script>var x = %s;</script>" % esc.js_string(p),
    lambda html, p: [
        ("未提前闭合 <script>", p.tags.count("script") == 1),
        ("输出中不存在原始 </script", "</script" not in html.rsplit("</script>", 1)[0]),
    ]
)
case(
    "脚本内容 js_string()",
    '";alert(1);//',
    lambda p: "<script>var x = %s;</script>" % esc.js_string(p),
    lambda html, p: [
        ("引号被 JSON 转义，无法闭合字符串", '\\"' in html),
    ]
)
case(
    "脚本内容 js_string()",
    "<!--<script>alert(1)</script>-->",
    lambda p: "<script>var x = %s;</script>" % esc.js_string(p),
    lambda html, p: [
        ("<!-- 注释走私被 < 转义中和", "<!--" not in html),
        ("未注入 <script> 标签", p.tags.count("script") == 1),
    ]
)

# ---------------------------------------------------------------- 样式
case(
    "样式内容 css_string()",
    "';}</style><script>alert(1)</script>",
    lambda p: "<style>a::before{content:%s}</style>" % esc.css_string(p),
    lambda html, p: [
        ("未提前闭合 <style>", p.tags.count("style") == 1),
        ("未注入 <script> 标签", "script" not in p.tags),
    ]
)
case(
    "样式内容 css_string()",
    "red\n}body{display:none",
    lambda p: "<style>a::before{content:%s}</style>" % esc.css_string(p),
    lambda html, p: [
        ("换行被转义，无法截断 CSS 字符串", "\n" not in html),
        ("} 被转义，无法闭合规则块", "\\00007d" in html and "}body" not in html),
    ]
)

# ---------------------------------------------------------------- 地址
def url_case(payload):
    def render(p):
        return '<a href="%s">x</a>' % esc.attr_url(p)
    def verify(html, p):
        href = p.attrs["a"][0]["href"]
        return [
            ("属性解析后 href 不以 javascript: 开头", not href.lower().startswith("javascript:")),
            ("href 中不含未编码的 <script", "<script" not in href.lower()),
        ]
    try:
        case("地址 attr_url()", payload, render, verify)
    except esc.UnsafeURLError as exc:
        print("上下文: 地址 attr_url()")
        print("  载荷: %r" % payload)
        results.append(check("协议白名单直接拒绝: %s" % exc, True))
        print()


url_case("javascript:alert(1)")
url_case("JaVaScRiPt:alert(1)")
url_case("java\tscript:alert(1)")
url_case("&#106;avascript:alert(1)")
url_case("data:text/html,<script>alert(1)</script>")
url_case("https://example.com/a b?x=1&y=2")

# ---------------------------------------------------------------- 嵌套：脚本在属性里
case(
    "嵌套 js_attr()（事件属性里的 JS 字符串）",
    '");alert(1);//',
    lambda p: '<button onclick="f(%s)">go</button>' % esc.js_attr(p),
    lambda html, p: [
        ("未逃逸出 onclick 属性", set(p.attrs["button"][0]) == {"onclick"}),
        ("属性解码后是合法函数字符串调用", p.attrs["button"][0]["onclick"].startswith('f("')),
    ]
)
case(
    "嵌套 js_attr()（实体走私引号）",
    "&#34;);alert(1);//",
    lambda p: '<button onclick="f(%s)">go</button>' % esc.js_attr(p),
    lambda html, p: [
        ("& 被属性转义，实体无法还原成引号", "&amp;#34;" in html),
        ("未逃逸出 onclick 属性", set(p.attrs["button"][0]) == {"onclick"}),
    ]
)

# ---------------------------------------------------------------- 嵌套：样式里的地址
case(
    "嵌套 css_url()（样式里的地址）",
    "https://e.com/x');}</style><script>alert(1)</script>",
    lambda p: "<style>a{background:url(%s)}</style>" % esc.css_url(p),
    lambda html, p: [
        ("未提前闭合 <style>", p.tags.count("style") == 1),
        ("未注入 <script> 标签", "script" not in p.tags),
        ("未逃逸出 CSS 字符串", "\x27);}" not in html),
    ]
)
try:
    esc.css_url("javascript:alert(1)")
    results.append(check("css_url 拒绝 javascript: 协议", False))
except esc.UnsafeURLError:
    results.append(check("css_url 拒绝 javascript: 协议", True))

print("=" * 60)
print("注入检查项: %d 项, 通过 %d 项" % (len(results), sum(results)))
raise SystemExit(0 if all(results) else 1)

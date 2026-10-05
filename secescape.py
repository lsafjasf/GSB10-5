"""secescape -- 上下文感知的输出转义与注入防护库（仅标准库，Python 3.8+）。

核心原则：同一段用户内容出现在不同输出位置时，必须按该位置的
解析规则转义，不存在"一个函数套到底"的安全方案。本库为每一类
上下文提供独立函数，并用 Safe 标记防止二次转义。

上下文与对应函数
----------------
文本内容        text(value)            <div>{...}</div>
属性值          attr(value)            <div title="{...}">
属性名+值       attr_pair(name, value) 校验属性名后拼接 name="..."
脚本内容        js_value(value)        <script>var x = {...};</script>
              js_string(value)       仅接受 str 的 js_value
脚本嵌套在属性  js_attr(value)         <button onclick="f({...})">
样式内容        css_string(value)      <style>a::before{content:{...}}</style>
样式中的地址    css_url(value)         <style>a{background:url({...})}</style>
地址(原始)      url(value)             Location 头、JSON 字段等
地址在属性中    attr_url(value)        <a href="{...}">
查询参数        query_param(value)     拼接 URL 的 query 部分
防二次转义      Safe / mark_safe / is_safe / escape_once

所有函数对 Safe 输入幂等：已转义内容原样返回，不会变成乱码。
"""

import json
import re
from html import unescape as _html_unescape
from urllib.parse import quote

__all__ = [
    "Safe", "UnsafeURLError",
    "mark_safe", "is_safe",
    "text", "attr", "attr_pair",
    "js_value", "js_string", "js_attr",
    "css_string", "css_url",
    "url", "attr_url", "css_url", "query_param",
    "escape_once", "render",
]


class UnsafeURLError(ValueError):
    """URL 未通过协议白名单校验时抛出。"""


class Safe(str):
    """已按某个上下文转义完成的字符串标记。

    所有转义函数遇到 Safe 实例直接原样返回（幂等），
    因此同一段内容经过任意次转义都不会变成乱码。
    """

    __slots__ = ()

    def __add__(self, other):
        return Safe(str.__add__(self, other))

    def __radd__(self, other):
        return Safe(str.__add__(other, self))

    def join(self, iterable):
        return Safe(str.join(self, iterable))


def mark_safe(value):
    """把调用方确认安全的字符串标记为 Safe。"""
    if isinstance(value, Safe):
        return value
    return Safe(str(value))


def is_safe(value):
    return isinstance(value, Safe)


# ---------------------------------------------------------------------------
# 公共预处理
# ---------------------------------------------------------------------------

# NUL 在任何上下文中都是"替换字符"（HTML 规范）或直接丢弃（URL 规范），
# 保留它只会给解析器制造差异，统一在入口处处理。
_NUL = "\x00"


def _clean_nul(value):
    return value.replace(_NUL, "\ufffd")


# ---------------------------------------------------------------------------
# 1. 文本内容上下文
# ---------------------------------------------------------------------------

_TEXT_TABLE = str.maketrans({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
})


def _text_escape(value):
    return value.translate(_TEXT_TABLE)


def text(value):
    """转义后可直接放入 HTML 元素文本内容。

    规则：& -> &amp;  < -> &lt;  > -> &gt;
    引号在文本节点中没有结构意义，不转义（保持可读性）。
    """
    if isinstance(value, Safe):
        return value
    return Safe(_text_escape(_clean_nul(str(value))))


# ---------------------------------------------------------------------------
# 2. 属性值上下文（统一按带双引号的属性处理）
# ---------------------------------------------------------------------------

_ATTR_TABLE = str.maketrans({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#x27;",
})


def _attr_escape(value):
    return value.translate(_ATTR_TABLE)


def attr(value):
    """转义后可直接放入带双引号的 HTML 属性值。

    规则：在文本规则基础上额外转义 " 和 '。
    注意：本函数只返回值本身，调用方必须用双引号包裹；
    需要连引号一起生成时用 attr_pair。
    """
    if isinstance(value, Safe):
        return value
    return Safe(_attr_escape(_clean_nul(str(value))))


_ATTR_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]*\Z")


def attr_pair(name, value):
    """生成完整的 name="value" 片段，属性名走白名单校验。"""
    if not _ATTR_NAME_RE.match(name):
        raise ValueError("非法属性名: %r" % (name,))
    return Safe('%s="%s"' % (name, attr(value)))


# ---------------------------------------------------------------------------
# 3. 脚本内容上下文
# ---------------------------------------------------------------------------

# U+2028 / U+2029 在 JSON 里合法、在 JS 字符串字面量里是换行符，
# 必须额外转义，否则 json.dumps 的输出直接放进 <script> 会语法错误。
_JS_LINE_SEPARATORS = str.maketrans({
    "\u2028": "\\u2028",
    "\u2029": "\\u2029",
})


def _js_dumps(value):
    raw = json.dumps(value, ensure_ascii=False)
    # 转义 < 同时堵住 </script>、<!--、<script 三种逃逸路径；
    # < 在 JS 字符串里没有结构意义，转义后语义不变。
    raw = raw.replace("<", "\\u003c")
    return raw.translate(_JS_LINE_SEPARATORS)


def js_value(value):
    """把 JSON 可序列化的值转成可安全内联进 <script> 的 JS 字面量。

    规则：json.dumps 序列化（自动处理引号、反斜杠、控制字符），
    再把 < 转成 \\u003c，把 U+2028/U+2029 转成 \\u2028/\\u2029。
    输出是完整的 JS 字面量（含引号），直接放在 = 右侧即可。
    """
    if isinstance(value, Safe):
        return value
    if isinstance(value, str):
        value = _clean_nul(value)
    return Safe(_js_dumps(value))


def js_string(value):
    """js_value 的字符串专用版本，拒绝非 str 输入以免误用。"""
    if isinstance(value, Safe):
        return value
    if not isinstance(value, str):
        raise TypeError("js_string 只接受 str，收到 %s" % type(value).__name__)
    return js_value(value)


def js_attr(value):
    """脚本嵌套在事件属性里（onclick="..."）的专用函数。

    嵌套上下文要按"由内向外"的顺序处理：
    先做 JS 字符串转义，再对整体做属性转义。
    浏览器解析顺序相反：先解属性实体，再把结果交给 JS 引擎。
    注意：内层结果虽然是 Safe，但进入外层上下文时必须重新转义，
    因此这里调用的是不带 Safe 短路的内部 _attr_escape。
    """
    if isinstance(value, Safe):
        return value
    inner = str(js_value(value))
    return Safe(_attr_escape(inner))


# ---------------------------------------------------------------------------
# 4. 样式内容上下文
# ---------------------------------------------------------------------------

# CSS 字符串采用保守白名单：不在集合内的字符一律转成 \XXXXXX 形式
# 的 CSS 十六进制转义（CSS 规范允许的通用转义，解码后语义不变）。
_CSS_SAFE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    " -_.#%:,;/~@()[]"
)


def _css_escape(value):
    out = []
    for ch in value:
        if ch in _CSS_SAFE_CHARS:
            out.append(ch)
        else:
            # 定宽 6 位十六进制 + 尾随空格；空格是 CSS 转义的终止符，
            # 也顺带防止转义序列"吃掉"后面的十六进制字符。
            out.append("\\%06x " % ord(ch))
    # 用单引号包裹，保证整段是字符串字面量
    return "'%s'" % "".join(out)


def css_string(value):
    """转义后可直接放入 CSS 字符串（输出含包裹用的单引号）。

    规则：白名单字符原样保留，其余字符（含引号、反斜杠、换行、
    </style> 里的 < 等）一律转成 \\XXXXXX 形式的 CSS 转义。
    CSS 字符串不允许出现原始换行，本函数从根上杜绝。
    """
    if isinstance(value, Safe):
        return value
    value = _clean_nul(str(value))
    return Safe(_css_escape(value))


# ---------------------------------------------------------------------------
# 5. 地址上下文
# ---------------------------------------------------------------------------

ALLOWED_SCHEMES = frozenset({"http", "https", "mailto", "tel", "ftp"})

_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")

# URL 解析前浏览器会丢弃 C0 控制符(0x00-0x1F)和 DEL(0x7F)，先按同样
# 规则清洗再判断协议，否则 "java\tscript:" 这类载荷会绕过字符串匹配。
# 注意 0x20 是普通空格，不在控制符范围内（会被百分号编码成 %20）。
_URL_STRIP_RE = re.compile(r"[\x00-\x1f\x7f]")

# 地址里保留的安全字符：RFC 3986 unreserved + 常见保留字。
# % 保留：合法的 %XX 序列原样通过，孤立 % 也不会被当作协议解码，
# 不会构成协议走私（协议判断在编码之前已经完成）。
_URL_SAFE_CHARS = "/:@!$'()*+,;=%-._~?#[]&"


def _check_scheme(candidate, original):
    match = _SCHEME_RE.match(candidate)
    if match:
        scheme = match.group(1).lower()
        if scheme not in ALLOWED_SCHEMES:
            raise UnsafeURLError("协议 %r 不在白名单内: %r" % (scheme, original))


def _check_and_encode_url(value):
    """协议白名单校验 + 百分号编码，返回编码后的字符串。"""
    value = str(value)
    # 去控制符，再剥首尾空白（浏览器解析 URL 时会做同样处理）
    cleaned = _URL_STRIP_RE.sub("", value).strip()
    if cleaned.startswith("\\"):
        # 部分浏览器把 "\\path" 当作 "/path"，开头反斜杠一律拒绝
        raise UnsafeURLError("拒绝以反斜杠开头的地址: %r" % (value,))
    # 原始形态先查一次协议
    _check_scheme(cleaned, value)
    # 再按 HTML 实体解码后查一次，堵住 javascript&#58;alert(1)
    # 这种"等浏览器解实体才露出真协议"的走私路径
    decoded = _html_unescape(cleaned)
    if decoded != cleaned:
        _check_scheme(decoded, value)
    return quote(cleaned, safe=_URL_SAFE_CHARS)


def url(value):
    """校验并编码一个地址，返回可直接使用的百分号编码形式。

    适用：Location 跳转头、JSON 字段等非 HTML 位置。
    放进 HTML 属性请用 attr_url，放进 CSS url() 请用 css_url。
    """
    if isinstance(value, Safe):
        return value
    return Safe(_check_and_encode_url(value))


def attr_url(value):
    """地址嵌套在 HTML 属性里（href/src）的专用函数。

    处理顺序：协议白名单校验 -> 百分号编码 -> 属性转义。
    属性转义在最后，把 & 转成 &amp;，使 &#x6a; 之类的实体走私
    在属性解析阶段无法还原出可执行协议。
    """
    if isinstance(value, Safe):
        return value
    encoded = _check_and_encode_url(value)
    return Safe(_attr_escape(encoded))


def css_url(value):
    """地址嵌套在 CSS url() 里的专用函数。

    处理顺序：协议白名单校验 -> 百分号编码 -> CSS 字符串转义。
    输出形如 url 的安全字符串字面量（含单引号），
    直接写 background:url({结果}) 即可。
    """
    if isinstance(value, Safe):
        return value
    encoded = _check_and_encode_url(value)
    return Safe(_css_escape(encoded))


def query_param(value):
    """把单个值编码为 URL 查询参数形式（quote_plus 语义）。"""
    if isinstance(value, Safe):
        return value
    return Safe(quote(str(value), safe=""))


# ---------------------------------------------------------------------------
# 6. 防二次转义
# ---------------------------------------------------------------------------

# 合法的 HTML 实体（有名 / 十进制 / 十六进制），escape_once 予以保留
_ENTITY_RE = re.compile(
    r"&(?:[A-Za-z][A-Za-z0-9]+|#[0-9]+|#[Xx][0-9A-Fa-f]+);"
)

_ESCAPE_ONCE_MAP = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
}


def escape_once(value):
    """幂等的文本级转义：已存在的合法实体原样保留，其余字符正常转义。

    用于历史数据里"可能已经转义过、但来源没有 Safe 标记"的场景。
    新代码优先使用 Safe 标记 + text()，escape_once 是兼容手段。
    """
    if isinstance(value, Safe):
        return value
    value = _clean_nul(str(value))
    parts = []
    pos = 0
    for match in _ENTITY_RE.finditer(value):
        for ch in value[pos:match.start()]:
            parts.append(_ESCAPE_ONCE_MAP.get(ch, ch))
        parts.append(match.group(0))
        pos = match.end()
    for ch in value[pos:]:
        parts.append(_ESCAPE_ONCE_MAP.get(ch, ch))
    return Safe("".join(parts))


# ---------------------------------------------------------------------------
# 7. 演示：带自动转义的极简渲染器
# ---------------------------------------------------------------------------

def render(template, /, **context):
    """str.format 风格渲染：普通值按文本上下文转义，Safe 值原样通过。

    演示"框架层自动转义"如何与 Safe 标记配合，避免二次转义。
    """
    safe_context = {
        key: (value if isinstance(value, Safe) else text(value))
        for key, value in context.items()
    }
    return Safe(template.format(**safe_context))

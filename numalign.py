"""numalign -- 显示宽度感知的数字列对齐库（仅标准库）。

对外接口:
    display_width(s)           按显示宽度规则计算字符串宽度
    graphemes(s)               按扩展字形簇切分
    fit_width(s, max_width)    在字形簇边界截断/填充，绝不切断组合序列
    parse_number(text)         各种数字写法 -> Decimal
    extract_number(text)       带装饰字符的文本 -> (前缀, Decimal, 后缀)
    format_number(value, ...)  统一小数位/千分位/负号/指数
    align_column(texts, ...)   一列数字按小数点对齐
    align_table(rows, ...)     多列数字对齐，返回行与各列起始位置
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, ROUND_HALF_UP

# ---------------------------------------------------------------------------
# 显示宽度规则
# ---------------------------------------------------------------------------
#
# 1. 先把字符串切成“扩展字形簇”（grapheme cluster）：
#    基字符 + 后续的组合记号（category Mn/Me 或 unicodedata.combining≠0）
#    + 变体选择符 U+FE00..U+FE0F、肤色修饰 U+1F3FB..U+1F3FF、
#      围合键帽 U+20E3；U+200D(ZWJ) 及其后一个字符并入同一簇；
#      区域指示符（U+1F1E6..U+1F1FF）两两成簇（国旗）。
# 2. 单字符宽度:
#    - 控制符(Cc)、格式符(Cf)、零宽记号(Mn/Me) -> 0
#      （含 ZWSP U+200B、ZWNJ U+200C、ZWJ U+200D、BOM U+FEFF、
#       词连接符 U+2060、软连字符 U+00AD 等零宽/不可见字符）
#    - East Asian Width 为 W/F（全角、CJK、多数 emoji） -> 2
#    - 其余（半角字母数字、标点） -> 1
# 3. 字形簇宽度取簇内成员最大宽度；键帽序列、区域指示符对、
#    VS16 emoji 呈现序列固定按 2 计。即“再宽的可视字形，单簇最多 2 列”。
#
# 填充只在字形簇边界插入空格，因此零宽字符和组合序列不可能被切成半个。

VS_START, VS_END = 0xFE00, 0xFE0F          # 变体选择符
SKIN_START, SKIN_END = 0x1F3FB, 0x1F3FF    # emoji 肤色
RI_START, RI_END = 0x1F1E6, 0x1F1FF        # 区域指示符（国旗）
ZWJ = 0x200D                               # 零宽连接符
KEYCAP = 0x20E3                            # 组合围合键帽
INVISIBLE_CATEGORIES = {"Cc", "Cf", "Mn", "Me"}


def _char_width(ch: str) -> int:
    cat = unicodedata.category(ch)
    if cat in INVISIBLE_CATEGORIES or unicodedata.combining(ch):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def graphemes(s: str):
    """按简化版扩展字形簇规则切分字符串。"""
    if s.isascii():
        if s.isprintable():
            yield from s
            return
    cluster = ""
    after_zwj = False
    for ch in s:
        cp = ord(ch)
        if not cluster:
            cluster, after_zwj = ch, False
            continue
        is_mark = (
            unicodedata.category(ch) in ("Mn", "Me")
            or unicodedata.combining(ch) != 0
        )
        attaches = (
            after_zwj
            or is_mark
            or VS_START <= cp <= VS_END
            or SKIN_START <= cp <= SKIN_END
            or cp in (KEYCAP, ZWJ, 0x200C)
            or (
                RI_START <= cp <= RI_END
                and RI_START <= ord(cluster[0]) <= RI_END
            )
        )
        if attaches:
            cluster += ch
        else:
            yield cluster
            cluster = ch
        after_zwj = cp == ZWJ
    if cluster:
        yield cluster


def _cluster_width(cluster: str) -> int:
    cps = [ord(c) for c in cluster]
    if KEYCAP in cps:
        return 2
    if cps and all(RI_START <= cp <= RI_END for cp in cps):
        return 2
    if 0xFE0F in cps:
        return 2
    return max((_char_width(c) for c in cluster), default=0)


def display_width(s: str) -> int:
    """字符串在等宽终端中占据的显示列数。"""
    if s.isascii():
        if s.isprintable():
            return len(s)
        return sum(
            0 if unicodedata.category(ch) in INVISIBLE_CATEGORIES else 1
            for ch in s
        )
    return sum(_cluster_width(g) for g in graphemes(s))


def fit_width(s: str, max_width: int, fill: str = " ") -> str:
    """把字符串限制在 max_width 显示列内；只在字形簇边界切，再补空格。"""
    used = 0
    kept = []
    for g in graphemes(s):
        w = _cluster_width(g)
        if used + w > max_width:
            break
        kept.append(g)
        used += w
    return "".join(kept) + fill * (max_width - used)


def split_at_width(s: str, width: int):
    """在显示宽度 width 处按字形簇边界切成 (head, tail)。

    若 width 落在某个字形簇内部（不可能合法切断的位置），返回 None。
    """
    if width == 0:
        return "", s
    used, idx = 0, 0
    for g in graphemes(s):
        w = _cluster_width(g)
        if used + w > width:
            return None
        used += w
        idx += len(g)
        if used == width:
            return s[:idx], s[idx:]
    return None


# ---------------------------------------------------------------------------
# 数字写法统一
# ---------------------------------------------------------------------------

_MINUS_SIGNS = "\u2212\u2010\u2011\u2012\u2013\u2014\uFF0D"  # −‐‑‒–—－

_RAW_TRANS = {ord("０") + i: ord("0") + i for i in range(10)}
_RAW_TRANS[ord("．")] = ord(".")
_RAW_TRANS[ord("，")] = ord(",")
for _m in _MINUS_SIGNS:
    _RAW_TRANS[ord(_m)] = ord("-")


def _strip_invisible(s: str) -> str:
    """去掉零宽/不可见字符与组合记号（Cf/Cc/Mn/Me），不影响数值。"""
    if s.isascii() and s.isprintable():
        return s
    return "".join(
        ch for ch in s if unicodedata.category(ch) not in INVISIBLE_CATEGORIES
    )


def parse_number(text: str) -> Decimal:
    """把各种写法的数字解析为 Decimal。

    接受：全角数字/小数点/逗号、各种形态负号、千分位逗号、
    科学计数法(e/E)、括号负数、下划分数位分隔、夹杂零宽字符
    与组合记号（如数字上的重音符）及普通空白。
    """
    s = _strip_invisible(text).strip().translate(_RAW_TRANS)
    negative = False
    if len(s) >= 2 and s[0] == "(" and s[-1] == ")":
        negative = True
        s = s[1:-1]
    s = s.replace(",", "").replace("_", "").replace(" ", "")
    if not s:
        raise ValueError("空数字")
    if s[0] == "+":
        s = s[1:]
    elif s[0] == "-":
        negative = not negative
        s = s[1:]
    value = Decimal(s)
    return -value if negative else value


_MARKS = "\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe00-\ufe0f"
_INVIS_IN_NUM = "\u200b\u200c\u200d\ufeff\u2060\u00ad"
_NUM_JUNK = _MARKS + _INVIS_IN_NUM
_NUM_CORE = re.compile(
    r"[+\(−‐‑‒–—－-]?"
    r"[0-9０-９](?:[0-9０-９,_，\s]|[" + _NUM_JUNK + r"])*"
    r"(?:[.．](?:[0-9０-９]|[" + _NUM_JUNK + r"])+)?"
    r"(?:[eE][+\-−－]?[0-9０-９]+)?"
)


def extract_number(text: str):
    """从含装饰字符的文本中提取数字核心，返回 (前缀, Decimal, 后缀)。

    数字本体中的零宽字符/组合记号在解析时剥离；数字两侧的全角字符、
    emoji、ZWJ 序列等装饰内容原样保留在前缀/后缀里（不破坏任何序列）。
    """
    m = _NUM_CORE.search(text)
    if not m:
        raise ValueError(f"找不到数字: {text!r}")
    start, end = m.span()
    if (
        start > 0
        and end < len(text)
        and text[start - 1] == "("
        and text[end] == ")"
    ):
        start, end = start - 1, end + 1
    return text[:start], parse_number(text[start:end]), text[end:]


def _group_integer(intpart: str) -> str:
    sign = ""
    if intpart[:1] in "+-":
        sign, intpart = intpart[0], intpart[1:]
    groups = []
    while len(intpart) > 3:
        groups.append(intpart[-3:])
        intpart = intpart[:-3]
    groups.append(intpart)
    return sign + ",".join(reversed(groups))


def format_number(
    value: Decimal,
    decimals: int | None = 2,
    thousands: bool = True,
    exponent: bool = False,
    exp_width: int = 2,
    minus: str = "-",
) -> str:
    """统一数字本体格式。

    decimals: 固定小数位（None 时保留原始有效位，不做补零）
    thousands: 是否加千分位逗号
    exponent: True 时输出规范化科学计数法（E 大写、指数带符号、零填充）
    exp_width: 指数数字最小宽度，如 3 -> E+03
    minus: 统一后的负号字符
    """
    if exponent:
        if value == 0:
            mant_text = "0" + ("." + "0" * decimals if decimals else "")
            e_digits = "0" * exp_width
            return f"{mant_text}E+{e_digits}"
        sign, digits, exp = value.as_tuple()
        first = exp + len(digits) - 1
        if decimals is None:
            mantissa = Decimal((0, digits, exp - first))
        else:
            mantissa = Decimal((0, digits, exp - first)).quantize(
                Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP
            )
            if abs(mantissa) >= 10:
                mantissa = mantissa.scaleb(-1)
                first += 1
        text = f"{mantissa:.{decimals}f}" if decimals is not None else format(mantissa, "f")
        if thousands and "." in text:
            whole, frac = text.split(".")
            text = _group_integer(whole) + "." + frac
        e_sign = "+" if first >= 0 else "-"
        e_digits = str(abs(first)).zfill(exp_width)
        prefix = minus if sign else ""
        return f"{prefix}{text}E{e_sign}{e_digits}"

    if decimals is not None:
        quant = Decimal(1).scaleb(-decimals)
        value = value.quantize(quant, rounding=ROUND_HALF_UP)
        if value == 0:
            value = abs(value)
    text = format(value, "f")
    if "." in text:
        whole, frac = text.split(".")
    else:
        whole, frac = text, ""
    sign = ""
    if whole[:1] == "-":
        sign, whole = minus, whole[1:]
    if thousands:
        whole = _group_integer(whole)
    return sign + whole + ("." + frac if frac else "")


# ---------------------------------------------------------------------------
# 列对齐
# ---------------------------------------------------------------------------

def _split_parts(formatted: str):
    """拆成 (整数段含负号, 小数段含小数点, 指数段)。"""
    if "E" in formatted:
        mantissa, exp_part = formatted.split("E", 1)
        exp_part = "E" + exp_part
    else:
        mantissa, exp_part = formatted, ""
    if "." in mantissa:
        whole, frac = mantissa.split(".", 1)
        return whole, "." + frac, exp_part
    return mantissa, "", exp_part


def _align_column_impl(
    texts,
    decimals: int | None = 2,
    thousands: bool = True,
    exponent: bool = False,
    fill: str = " ",
):
    """把一列原始数字字符串按小数点纵向对齐。

    数字两侧的装饰字符（全角/emoji/ZWJ 序列等）保留在独立前缀、
    后缀区，各自按显示宽度对齐，不影响小数点位置。
    """
    extracted = [extract_number(t) for t in texts]
    formatted = [
        format_number(v, decimals=decimals, thousands=thousands, exponent=exponent)
        for _, v, _ in extracted
    ]
    parts = [_split_parts(f) for f in formatted]
    pre_ws = [display_width(pre) for pre, _, _ in extracted]
    suf_ws = [display_width(suf) for _, _, suf in extracted]
    whole_ws = [display_width(p[0]) for p in parts]
    frac_ws = [display_width(p[1]) for p in parts]
    exp_ws = [display_width(p[2]) for p in parts]
    w_prefix, w_suffix = max(pre_ws), max(suf_ws)
    w_whole, w_frac, w_exp = max(whole_ws), max(frac_ws), max(exp_ws)
    lines = []
    for (prefix, _, suffix), (whole, frac, exp_part), pw, sw, ww, fw, ew in zip(
        extracted, parts, pre_ws, suf_ws, whole_ws, frac_ws, exp_ws
    ):
        line = (
            prefix
            + fill * (w_prefix - pw)
            + fill * (w_whole - ww)
            + whole
            + frac
            + fill * (w_frac - fw)
            + exp_part
            + fill * (w_exp - ew)
            + suffix
            + fill * (w_suffix - sw)
        )
        lines.append(line)
    width = w_prefix + w_whole + w_frac + w_exp + w_suffix
    return lines, width


def align_column(texts, decimals: int | None = 2, thousands: bool = True,
                 exponent: bool = False, fill: str = " "):
    """把一列原始数字字符串按小数点纵向对齐（见 _align_column_impl 说明）。"""
    return _align_column_impl(texts, decimals, thousands, exponent, fill)[0]


def align_table(rows, gap: int = 2, **align_kwargs):
    """对齐多列数字。

    返回 (lines, starts):
      lines  -- 对齐后的每行字符串
      starts -- 每个单元格的起始显示列，形状为 len(rows) x len(cols)
    """
    cols = len(rows[0])
    aligned_cols = [
        _align_column_impl([row[c] for row in rows], **align_kwargs)
        for c in range(cols)
    ]
    col_widths = [w for _, w in aligned_cols]
    col_lines = [ls for ls, _ in aligned_cols]
    starts = []
    lines = []
    for r in range(len(rows)):
        pos = 0
        row_starts = []
        cells = []
        for c in range(cols):
            cell = col_lines[c][r]
            row_starts.append(pos)
            cells.append(cell)
            if c < cols - 1:
                pos += col_widths[c] + gap
        starts.append(row_starts)
        lines.append((" " * gap).join(cell for cell in cells))
    return lines, starts

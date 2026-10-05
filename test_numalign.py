"""numalign 自测：宽度规则、写法统一对照、列起始位置逐一断言、簇安全。"""

import unicodedata

from numalign import (
    align_column,
    align_table,
    display_width,
    fit_width,
    format_number,
    graphemes,
    parse_number,
    split_at_width,
)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f"  {detail}" if detail else ""))


def show(s):
    return s.replace(" ", "·")


print("=" * 72)
print("1. 显示宽度规则验证")
print("=" * 72)
cases = [
    ("半角数字 '123'", "123", 3),
    ("全角数字 '１２３'", "１２３", 6),
    ("CJK '漢字'", "漢字", 4),
    ("组合序列 'e\\u0301' (e+ acute)", "e\u0301", 1),
    ("组合序列 '3\\u0301' (数字+ acute)", "3\u0301", 1),
    ("键帽序列 '1\\u20e3'", "1\u20e3", 2),
    ("emoji '🎉'", "\U0001F389", 2),
    ("ZWJ 序列 '👨‍👩‍👧'", "\U0001F468\u200D\U0001F469\u200D\U0001F467", 2),
    ("国旗 '🇨🇳' (区域指示符对)", "\U0001F1E8\U0001F1F3", 2),
    ("零宽 ZWSP/ZWNJ/ZWJ/BOM", "\u200b\u200c\u200d\ufeff", 0),
    ("软连字符 U+00AD", "\u00ad", 0),
    ("变体选择符 '✈\\ufe0f'", "✈\ufe0f", 2),
    ("肤色修饰 '👍🏽'", "\U0001F44D\U0001F3FD", 2),
]
for name, s, want in cases:
    got = display_width(s)
    check(f"width({name}) == {want}", got == want, f"got {got}")

print()
print("=" * 72)
print("2. 数字写法统一：解析 -> 统一格式（前后对照）")
print("=" * 72)
raw_inputs = [
    "1234567.891",
    "１２３４５６７．８９１",
    "1,234,567.891",
    "−1234.5",
    "－１２３４．５",
    "(9876.5)",
    "1.5e3",
    "４２E－２",
    "1_000_000",
    "12\u200b34.5\ufeff6",
    "9.996",
    "0.00001234",
]
print(f"  {'原始输入':<22} -> {'Decimal':>16} -> {'统一格式(2位/千分位)':>20}")
normalized = []
for raw in raw_inputs:
    v = parse_number(raw)
    f = format_number(v, decimals=2, thousands=True)
    normalized.append(f)
    print(f"  {raw!r:<22} -> {str(v):>16} -> {f:>20}")
check("全角数字 == 半角数字", parse_number("１２３４５６７．８９１") == parse_number("1234567.891"))
check("U+2212 负号 == ASCII 负号", parse_number("−1234.5") == parse_number("-1234.5"))
check("括号负数", parse_number("(9876.5)") == parse_number("-9876.5"))
check("零宽字符不影响数值", parse_number("12\u200b34.5\ufeff6") == parse_number("1234.56"))
check("千分位逗号被剥离", parse_number("1,234,567.891") == parse_number("1234567.891"))

print()
print("  指数统一对照（exp_width=2, decimals=2）:")
for raw in ["1.5e3", "0.00001234", "-2.5E-7", "123456"]:
    v = parse_number(raw)
    print(f"  {raw!r:<14} -> {format_number(v, decimals=2, exponent=True)}")
check("指数进位 9.996 -> 1.00E+01",
      format_number(parse_number("9.996"), decimals=2, exponent=True) == "1.00E+01")
check("指数零填充", format_number(parse_number("1.5e3"), decimals=2, exponent=True) == "1.50E+03")

print()
print("=" * 72)
print("3. 四类情形对齐 + 每列起始位置逐一断言")
print("=" * 72)

groups = {
    "A. 纯半角数字": [
        ["1234.5", "0.25", "1000000"],
        ["-42", "3.14159", "-0.001"],
        ["1e3", "2,500", "7"],
    ],
    "B. 混全角数字": [
        ["１２３４．５", "０．２５", "１００００００"],
        ["−４２", "３．１４１５９", "−０．００１"],
        ["１２", "２，５００", "７"],
    ],
    "C. 含组合序列/零宽字符": [
        ["123\u03014.5", "0.2\u03015", "100\u200b0000"],
        ["-4\u03012", "3.14159", "-0.001"],
        ["1e3", "25\u030100", "7\u20e3"],
    ],
    "D. 含极宽字符（emoji/ZWJ/国旗）": [
        ["\U0001F3891234.5", "0.25", "1000000\U0001F1E8\U0001F1F3"],
        ["-42", "\U0001F468\u200D\U0001F469\u200D\U0001F4673.14159", "-0.001"],
        ["1e3", "2500", "7"],
    ],
}

for title, rows in groups.items():
    print(f"\n--- {title} ---")
    lines, starts = align_table(rows, gap=2, decimals=2, thousands=True)
    ncols = len(rows[0])
    for r, line in enumerate(lines):
        print(f"  |{line}|")
    expected = [starts[0][c] for c in range(ncols)]
    for c in range(ncols):
        col_starts = [starts[r][c] for r in range(len(rows))]
        ok = all(s == expected[c] for s in col_starts)
        check(f"列 {c} 起始位置全部 == {expected[c]}", ok, f"实测 {col_starts}")
        col_cells = align_column([row[c] for row in rows], decimals=2, thousands=True)
        dot_cols = [display_width(cell[: cell.index(".")]) for cell in col_cells]
        check(f"列 {c} 小数点显示列全部 == {dot_cols[0]}",
              len(set(dot_cols)) == 1, f"实测 {dot_cols}")
    for r, line in enumerate(lines):
        for c in range(ncols):
            cut = split_at_width(line, starts[r][c])
            ok = cut is not None and display_width(cut[0]) == starts[r][c]
            check(f"行{r} 列{c} 起始列 {starts[r][c]} 落在簇边界且前缀宽度吻合", ok)

print()
print("=" * 72)
print("4. 组合序列/不可见字符不被截断")
print("=" * 72)
s = "ab\u0301c\u200bd\U0001F468\u200D\U0001F469\u200D\U0001F467e"
for w in range(0, display_width(s) + 1):
    out = fit_width(s, w)
    for g in graphemes(out):
        base = g[0]
        if unicodedata.category(base) in ("Mn", "Me") or unicodedata.combining(base):
            check(f"fit_width({w}) 组合记号未脱离基字符", False, f"簇 {g!r}")
            break
    else:
        check(f"fit_width({w}) 簇完整", True, f"-> {out.rstrip()!r}")
joined = "".join(graphemes(s))
check("切分再拼接 == 原串（无字符丢失）", joined == s)
check("fit_width 宽度不超上限", all(display_width(fit_width(s, w)) == w for w in range(7)))

print()
print("=" * 72)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 72)
raise SystemExit(1 if FAIL else 0)

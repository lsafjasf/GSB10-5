"""生成恶意/非法表达式拒绝清单（Markdown），每条含分类与精确位置。"""

import sys

sys.path.insert(0, ".")

from filterexpr import compile as compile_expr
from filterexpr.errors import FilterError
from filterexpr.parser import DEFAULT_MAX_DEPTH, DEFAULT_MAX_LENGTH

CASES = [
    ("函数调用", "__import__('os').system('id')"),
    ("函数调用", "len(name) > 3"),
    ("函数调用", "eval('1+1') == 2"),
    ("函数调用", "(getattr)('x')"),
    ("属性访问", "user.name == 'alice'"),
    ("属性访问", "a.__class__ == 1"),
    ("下标访问", "items[0] == 1"),
    ("下标访问", "a['key'] == 1"),
    ("超长嵌套（括号）", "(" * (DEFAULT_MAX_DEPTH + 5) + "x" + ")" * (DEFAULT_MAX_DEPTH + 5)),
    ("超长嵌套（not 链）", "not " * (DEFAULT_MAX_DEPTH + 5) + "x == 1"),
    ("超长表达式", "x == 1 and " + "y == 2 and " * 3000 + "z == 3"),
    ("赋值/语句注入", "x = 1"),
    ("语句拼接", "x == 1; y == 2"),
    ("链式比较", "1 < x < 100"),
    ("语法错误", "age >="),
    ("语法错误", "'unterminated"),
    ("语法错误", "age >= 18 and"),
    ("语法错误", "(age > 1"),
    ("语法错误", "age > 1)"),
    ("语法错误", "age !"),
]


def main():
    lines = []
    lines.append("# 恶意/非法表达式拒绝清单")
    lines.append("")
    lines.append("以下表达式全部被拒绝；`位置` 为 1 起始的 行:列，指向出错字符。")
    lines.append("")
    lines.append("| # | 类别 | 表达式（截断显示） | 分类 | 位置 | 拒绝原因 |")
    lines.append("|---|------|--------------------|------|------|----------|")

    rows = []
    for index, (kind, source) in enumerate(CASES, 1):
        try:
            compile_expr(source)
            rows.append((index, kind, source, "未拒绝！", "", "", ""))
            continue
        except FilterError as exc:
            shown = source if len(source) <= 48 else source[:45] + "..."
            shown = shown.replace("|", "\\|")
            rows.append(
                (
                    index,
                    kind,
                    shown,
                    exc.category,
                    "%d:%d" % (exc.line, exc.col),
                    exc.message.replace("|", "\\|"),
                )
            )

    for index, kind, shown, category, pos, message in rows:
        lines.append(
            "| %d | %s | `%s` | %s | %s | %s |"
            % (index, kind, shown, category, pos, message)
        )

    lines.append("")
    lines.append("说明：")
    lines.append("")
    lines.append("- 默认上限：表达式长度 %d 字符、嵌套深度 %d 层（括号/not/一元负号计层）。" % (DEFAULT_MAX_LENGTH, DEFAULT_MAX_DEPTH))
    lines.append("- `security` 类拒绝发生在编译期，表达式根本不会进入求值阶段。")
    lines.append("- 重新生成：`python3 tools/rejections.py`")

    text = "\n".join(lines) + "\n"
    with open("REJECTIONS.md", "w", encoding="utf-8") as fh:
        fh.write(text)
    print("已生成 REJECTIONS.md（%d 条，全部拒绝）" % len(rows))
    if any(r[3] == "未拒绝！" for r in rows):
        sys.exit(1)


if __name__ == "__main__":
    main()

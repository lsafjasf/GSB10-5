"""Produce docs/REJECTIONS.md: every dangerous / invalid construct with the
exact error category, code and source position reported by safefilter."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from safefilter import (ExpressionTooDeep, ExpressionTooLong, FilterError,
                        ForbiddenConstruct, MalformedLiteral,
                        SyntaxFilterError, TypeMismatch, UnknownField,
                        compile_expr)

# Parse-time / compile-time rejection cases.
PARSE_CASES = [
    ("os.system('id')", "命令执行：属性访问 + 函数调用"),
    ("__import__('os').system('id')", "模块导入 + 命令执行"),
    ("globals()", "内建函数调用"),
    ("eval('1 + 1')", "动态求值调用"),
    ("getattr(user)", "反射型函数调用"),
    ("(lambda: 1)()", "立即执行函数"),
    ("(f)()", "括号后函数调用"),
    ("user.__class__", "dunder 属性访问（逃逸沙箱常见手法）"),
    ("user.name == 'alice'", "普通属性访问"),
    ("rows[0]", "下标访问"),
    ("user['name'] == 'alice'", "字符串下标访问"),
    ("age >= 18; rm -rf /", "语句分隔符 / 多条表达式"),
    ("age >= 18 # comment", "非法字符 #"),
    ("'user input", "未闭合字符串"),
    ("age == 1.", "残缺数字字面量"),
    ("age == 1e", "残缺指数数字"),
    ("1 < 2 < 3", "连续比较（未用 and 连接）"),
    ("(age > 1", "括号未闭合"),
    ("age >=", "表达式截断"),
    ("", "空表达式"),
    ("(" * 65 + "age == 1" + ")" * 65, "嵌套深度 65（上限 64）"),
    ("not " * 65 + "active", "not 链深度 65（上限 64）"),
    ("age == 1 or " + "active or " * 1700, "超长表达式（上限 10000 字符）"),
]

# Evaluation-time classification cases (parse succeeds, row evaluation fails
# or yields a result).
EVAL_CASES = [
    ("age > 'beijing'", {"age": 20}, "数字与字符串做大小比较"),
    ("age == vip", {"age": 20, "vip": False}, "数字与布尔做相等比较"),
    ("vip < true", {"vip": False}, "布尔值做大小比较"),
    ("age and active", {"age": 20, "active": True}, "逻辑运算符作用于数字"),
    ("not age", {"age": 20}, "not 作用于数字"),
    ("missing == 1", {"age": 20}, "字段不存在"),
    ("age < 18 and ghost == 1", {"age": 20}, "短路：右侧字段缺失不报错"),
]


def md_escape(text):
    return text.replace("|", "\\|").replace("\n", " ")


def main():
    lines = []
    lines.append("# 拒绝清单（Rejection List）")
    lines.append("")
    lines.append("由 `tools/gen_rejections.py` 生成；所有位置均为 1 起始的 `行:列`。")
    lines.append("")
    lines.append("## 解析期拒绝（恶意构造 / 语法错误 / 资源限制）")
    lines.append("")
    lines.append("| # | 表达式 | 意图/问题 | 错误分类 | 错误码 | 位置 | 信息 |")
    lines.append("|---|--------|-----------|----------|--------|------|------|")
    for i, (source, note) in enumerate(PARSE_CASES, 1):
        shown = source if len(source) <= 48 else source[:45] + "..."
        try:
            compile_expr(source)
            category, code, pos, msg = "-", "NOT REJECTED", "-", "-"
        except FilterError as err:
            category, code = err.category, err.code
            pos = "%d:%d" % (err.line, err.column) if err.line else "-"
            msg = err.message
        lines.append("| %d | `%s` | %s | %s | `%s` | %s | %s |" % (
            i, md_escape(shown), note, category, code, pos,
            md_escape(msg)))

    lines.append("")
    lines.append("## 求值期分类报告（不静默当成假）")
    lines.append("")
    lines.append("| # | 表达式 | 行数据 | 场景 | 错误码 | 位置 | 信息 |")
    lines.append("|---|--------|--------|------|--------|------|------|")
    for i, (source, row, note) in enumerate(EVAL_CASES, 1):
        try:
            result = compile_expr(source).evaluate(row)
            code, pos, msg = "-", "-", "正常求值 = %s" % result
        except FilterError as err:
            code = err.code
            pos = "%d:%d" % (err.line, err.column) if err.line else "-"
            msg = err.message
        lines.append("| %d | `%s` | `%s` | %s | `%s` | %s | %s |" % (
            i, md_escape(source), md_escape(str(row)), note, code, pos,
            md_escape(msg)))

    lines.append("")
    lines.append("错误码含义：")
    lines.append("")
    lines.append("- `E_FORBIDDEN`（security）：函数调用 / 属性访问 / 下标访问，一律拒绝。")
    lines.append("- `E_SYNTAX`（syntax）：词法或语法不合法。")
    lines.append("- `E_LITERAL`（syntax）：数字/字符串字面量残缺。")
    lines.append("- `E_DEPTH`（resource-limit）：括号或 `not` 嵌套超过 64 层。")
    lines.append("- `E_LENGTH`（resource-limit）：表达式超过 10000 字符。")
    lines.append("- `E_TYPE`（type-mismatch）：操作数类型不匹配，显式报错。")
    lines.append("- `E_FIELD`（unknown-field）：引用了行中不存在的字段。")
    lines.append("")

    out = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "docs", "REJECTIONS.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("wrote %s (%d parse cases, %d eval cases)"
          % (out, len(PARSE_CASES), len(EVAL_CASES)))


if __name__ == "__main__":
    main()

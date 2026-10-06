# -*- coding: utf-8 -*-
"""演示：结果表、嵌套引用逐步展开、循环引用报告。

运行：python3 demo.py
"""

from envexpand import (
    EnvExpander,
    CircularReferenceError,
    UndefinedVariableError,
    EmptyNameError,
    UnmatchedBraceError,
)

SEP = "=" * 72


def show_steps(title, template, env):
    """用 trace 回调逐步展示嵌套引用的展开过程。"""
    print("\n【%s】" % title)
    print("  模板: %r" % template)
    print("  环境: %r" % env)
    events = []
    ex = EnvExpander(env, trace=lambda d, ev, *a: events.append((d, ev, a)))
    try:
        result = ex.expand(template)
    except Exception as exc:  # noqa: BLE001 - 演示需要展示错误
        result = "!! %s: %s" % (type(exc).__name__, exc)
    for depth, ev, args in events:
        pad = "  " * (depth + 1)
        if ev == "ref":
            print("  %s[%d] 遇到引用 $%s" % (pad, depth, args[0]))
        elif ev == "value":
            print("  %s[%d] %s 展开完成 -> %r" % (pad, depth, args[0], args[1]))
        elif ev == "cached":
            print("  %s[%d] %s 命中缓存 -> %r" % (pad, depth, args[0], args[1]))
        elif ev == "empty":
            print("  %s[%d] %s 已定义但为空 -> ''" % (pad, depth, args[0]))
        elif ev == "undefined":
            print("  %s[%d] %s 未定义" % (pad, depth, args[0]))
        elif ev == "cycle":
            print("  %s[%d] 检测到环: %s" % (pad, depth, " -> ".join(args[0])))
    print("  结果: %r" % result)
    return result


def result_table():
    print(SEP)
    print("一、结果表（普通 / 嵌套 / 转义 / 未定义 / 空值 / 循环）")
    print(SEP)
    rows = [
        # (类别, 模板, 环境, 期望结果或异常类型, on_undefined)
        ("普通引用", "Hello $NAME!", {"NAME": "World"}, "Hello World!", "raise"),
        ("普通引用", "${A}-${B}", {"A": "x", "B": "y"}, "x-y", "raise"),
        ("普通引用", "cost is 5$", {}, "cost is 5$", "raise"),
        ("转义", "price: $$5", {}, "price: $5", "raise"),
        ("转义", "$${A}", {"A": "x"}, "${A}", "raise"),
        ("转义(值内)", "$X", {"X": "$${B}", "B": "leak"}, "${B}", "raise"),
        ("嵌套引用", "${${A}}", {"A": "K", "K": "deep"}, "deep", "raise"),
        ("嵌套引用", "${A_$I}", {"I": "2", "A_2": "hit"}, "hit", "raise"),
        ("嵌套引用", "${${${L1}}}", {"L1": "L2", "L2": "L3", "L3": "v"}, "v", "raise"),
        ("值为模板", "pre/$A/post", {"A": "$B", "B": "base"}, "pre/base/post", "raise"),
        ("空值", "[$E]", {"E": ""}, "[]", "raise"),
        ("未定义(raise)", "$NOPE", {}, UndefinedVariableError, "raise"),
        ("未定义(empty)", "[$NOPE]", {}, "[]", "empty"),
        ("未定义(keep)", "a${NOPE}b", {}, "a${NOPE}b", "keep"),
        ("循环引用", "$X", {"X": "$Y", "Y": "${X}"}, CircularReferenceError, "raise"),
        ("循环引用", "$A", {"A": "$B", "B": "$C", "C": "${A}"}, CircularReferenceError, "raise"),
    ]
    header = ("类别", "模板", "环境", "结果")
    widths = [12, 22, 44, 30]
    fmt = "  %-*s %-*s %-*s %-*s"
    print(fmt % (widths[0], header[0], widths[1], header[1], widths[2], header[2], widths[3], header[3]))
    print("  " + "-" * (sum(widths) + 3))
    ok = True
    for kind, template, env, expected, policy in rows:
        try:
            got = EnvExpander(env, on_undefined=policy).expand(template)
            shown = repr(got)
            passed = got == expected
        except Exception as exc:  # noqa: BLE001
            shown = "%s: %s" % (type(exc).__name__, exc)
            passed = isinstance(expected, type) and isinstance(exc, expected)
        ok = ok and passed
        mark = "" if passed else "   <-- 与期望不符!"
        print(fmt % (widths[0], kind, widths[1], template, widths[2], repr(env), widths[3], shown) + mark)
    print("\n结果表全部符合预期。" if ok else "\n存在不符合预期的行！")
    return ok


def nested_steps():
    print("\n" + SEP)
    print("二、嵌套引用逐步展开（每一步可核对）")
    print(SEP)
    show_steps("两层嵌套", "${${A}}", {"A": "K", "K": "deep"})
    show_steps("名字里带裸引用", "${A_$I}", {"I": "2", "A_2": "hit"})
    show_steps("三层嵌套", "${${${L1}}}", {"L1": "L2", "L2": "L3", "L3": "value"})
    show_steps("值本身是模板（链式）", "pre/$A/post", {"A": "$B", "B": "$C", "C": "leaf"})


def cycle_report():
    print("\n" + SEP)
    print("三、循环引用报告（识别环上的变量名，立即中止，绝不无限展开）")
    print(SEP)
    cases = [
        ("自引用", "$A", {"A": "$A"}),
        ("两节点互引", "${X}", {"X": "$Y", "Y": "${X}"}),
        ("三节点环", "$A", {"A": "$B", "B": "$C", "C": "${A}-end"}),
        ("带前导的环", "$G", {"G": "$H", "H": "$I", "I": "${H}"}),
    ]
    for title, template, env in cases:
        print("\n【%s】模板 %r，环境 %r" % (title, template, env))
        try:
            EnvExpander(env).expand(template)
            print("  !! 未检测到循环（不应发生）")
        except CircularReferenceError as exc:
            print("  环上的变量: %s" % " -> ".join(exc.cycle + [exc.cycle[0]]))
            print("  完整展开路径: %s" % " -> ".join(exc.path))
            print("  异常信息: %s" % exc)


def undefined_vs_empty():
    print("\n" + SEP)
    print("四、未定义引用 vs 空值（严格区分）")
    print(SEP)
    env = {"E": ""}
    print("  环境: %r（E 已定义但为空；NOPE 未定义）" % env)
    print("  [$E]                -> %r   （空值：合法，展开为空串）"
          % EnvExpander(env).expand("[$E]"))
    try:
        EnvExpander(env).expand("[$NOPE]")
    except UndefinedVariableError as exc:
        print("  [$NOPE]             -> 抛 UndefinedVariableError: %s（默认策略 raise）" % exc)
    print("  [$NOPE] (empty策略) -> %r" % EnvExpander(env, on_undefined="empty").expand("[$NOPE]"))
    print("  [$NOPE] (keep策略)  -> %r" % EnvExpander(env, on_undefined="keep").expand("[$NOPE]"))
    print("  [$NOPE] (自定义)    -> %r"
          % EnvExpander(env, on_undefined=lambda n: "<default:%s>" % n).expand("[$NOPE]"))


def main():
    ok = result_table()
    nested_steps()
    cycle_report()
    undefined_vs_empty()
    print("\n" + SEP)
    print("演示结束%s。" % ("" if ok else "（存在不一致，请检查）"))


if __name__ == "__main__":
    main()

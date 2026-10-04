"""演示：嵌套引用逐步展开、循环引用报告、未定义与空值的区分。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from env_expand import CycleError, UndefinedVariableError, expand


def show_trace(title, template, env):
    print(f"== {title} ==")
    print(f"模板: {template!r}")
    for k, v in env.items():
        print(f"  {k} = {v!r}")
    trace = []
    result = expand(template, env, _trace=trace)
    step = 0
    for kind, name, detail in trace:
        step += 1
        if kind == "enter":
            print(f"  步骤{step}: 解析 {name}，原始值 {detail!r}")
        elif kind == "leave":
            print(f"  步骤{step}: {name} 展开完成 -> {detail!r}")
        else:
            print(f"  步骤{step}: [{kind}] {name} -> {detail!r}")
    print(f"结果: {result!r}\n")


def main():
    show_trace("嵌套引用（值中套引用）", "$A", {"A": "$B/x", "B": "$C", "C": "leaf"})
    show_trace(
        "大括号嵌套（变量名由内部展开拼出）",
        "${PRE_${INNER}}",
        {"INNER": "NAME", "PRE_NAME": "v"},
    )
    show_trace("转义：\\$ 是字面 $，替换后不再解释", r"\$HOME + $HOME", {"HOME": "/root"})

    print("== 循环引用报告 ==")
    env = {"A": "$B", "B": "$C", "C": "$A"}
    for k, v in env.items():
        print(f"  {k} = {v!r}")
    try:
        expand("$A", env)
    except CycleError as e:
        print(f"CycleError: {e}")
        print(f"环上的变量: {e.cycle}")
        print(f"完整解析路径: {e.path}\n")

    print("== 未定义 vs 空值 ==")
    print(f"空值:     expand('[ $E ]', E='') -> {expand('[ $E ]', {'E': ''})!r}")
    try:
        expand("[ $U ]", {})
    except UndefinedVariableError as e:
        print(f"未定义:   expand('[ $U ]', {{}}) -> UndefinedVariableError: {e}")
    print(f"未定义+empty 策略: {expand('[ $U ]', {}, undefined='empty')!r}")
    print(f"未定义+keep 策略:  {expand('[ $U ]', {}, undefined='keep')!r}")


if __name__ == "__main__":
    main()

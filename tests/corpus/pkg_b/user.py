"""跨文件引用样例：CR006 需要两个文件的上下文。"""
from pkg_a.mod import foo  # 符号存在，不报警
from pkg_a.mod import missing_symbol  # 符号不存在：应为 CR006
from pkg_a import mod  # mod 是 pkg_a 的子模块，不报警
import json  # 未使用：应为 CR004


def use_it():
    return foo() + mod.ANSWER + missing_symbol

"""跨文件上下文样例：一处真实错误 + 多处正确用法。"""
import helper
from helper import compute_total
from helper import compute_totals


def good_call(items):
    return helper.compute_total(items)


def bad_call(items):
    return helper.compute_totals(items)


def another_good(first, last):
    return helper.format_name(first, last)


def via_from_import(items):
    return compute_total(items)

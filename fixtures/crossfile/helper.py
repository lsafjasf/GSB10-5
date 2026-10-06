"""被引用的项目内模块。"""


def compute_total(items):
    total = 0
    for item in items:
        total += item
    return total


def format_name(first, last):
    return f"{first} {last}"

"""AST 节点定义。

节点用元组表示：(tag, payload..., offset, line, col)，
tag 取值为下列常量之一。用元组而非类是为了求值速度。
"""

LIT = "lit"    # (LIT, value, offset, line, col)
FIELD = "fld"  # (FIELD, name, offset, line, col)
NOT = "not"    # (NOT, operand, offset, line, col)
NEG = "neg"    # (NEG, operand, offset, line, col)
AND = "and"    # (AND, left, right, offset, line, col)
OR = "or"      # (OR, left, right, offset, line, col)
CMP = "cmp"    # (CMP, op, left, right, offset, line, col)  op in == != < <= > >=

CMP_OPS = ("==", "!=", "<", "<=", ">", ">=")


def node(tag, *payload):
    """构造节点，位置信息固定为 (0, 1, 1)，由 parser 覆盖。"""
    return (tag,) + payload + (0, 1, 1)


def at(n, offset, line, col):
    """返回带位置信息的节点副本。"""
    return n[:-3] + (offset, line, col)

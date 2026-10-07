"""AST 求值器。

语义要点（完整规则见 GRAMMAR.md）：
- 字段取值时若行内不存在该 key，抛 FieldMissing（分类 field_missing），
  绝不静默当成 False。
- == / != 允许任意两侧类型，但 bool 不被视作 int（True != 1）；
  仅 null == null、null != x 成立。
- < <= > >= 只允许两侧同为数值（int/float，不含 bool），否则 TypeMismatch。
- not 仅作用于 bool；一元负号仅作用于 int/float（不含 bool）。
- and/or 两个操作数必须是 bool，短路求值，本身返回 bool。
"""

from __future__ import annotations

from . import nodes as N
from .errors import FieldMissing, TypeMismatch


def _type_name(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def _is_number(value):
    # 刻意排除 bool（Python 里 bool 是 int 的子类）
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def evaluate(node, row):
    """对一行求值，返回布尔结果；字段缺失/类型不匹配时抛对应异常。"""
    result = eval_raw(node, row)
    if not isinstance(result, bool):
        raise TypeMismatch(
            "过滤条件的整体结果必须是 bool，实际类型为 %s" % _type_name(result)
        )
    return result


def eval_raw(node, row):
    tag = node[0]

    if tag == N.LIT:
        return node[1]

    if tag == N.FIELD:
        name = node[1]
        try:
            return row[name]
        except (KeyError, TypeError) as exc:
            raise FieldMissing("字段 %r 不存在" % name) from exc

    if tag == N.NOT:
        value = eval_raw(node[1], row)
        if not isinstance(value, bool):
            raise TypeMismatch(
                "not 的操作数必须是 bool，实际类型为 %s" % _type_name(value)
            )
        return not value

    if tag == N.NEG:
        value = eval_raw(node[1], row)
        if not _is_number(value):
            raise TypeMismatch(
                "一元负号的操作数必须是 number，实际类型为 %s" % _type_name(value)
            )
        return -value

    if tag == N.AND:
        left = eval_raw(node[1], row)
        if not isinstance(left, bool):
            raise TypeMismatch(
                "and 的左操作数必须是 bool，实际类型为 %s" % _type_name(left)
            )
        if not left:
            return False
        right = eval_raw(node[2], row)
        if not isinstance(right, bool):
            raise TypeMismatch(
                "and 的右操作数必须是 bool，实际类型为 %s" % _type_name(right)
            )
        return right

    if tag == N.OR:
        left = eval_raw(node[1], row)
        if not isinstance(left, bool):
            raise TypeMismatch(
                "or 的左操作数必须是 bool，实际类型为 %s" % _type_name(left)
            )
        if left:
            return True
        right = eval_raw(node[2], row)
        if not isinstance(right, bool):
            raise TypeMismatch(
                "or 的右操作数必须是 bool，实际类型为 %s" % _type_name(right)
            )
        return right

    if tag == N.CMP:
        op = node[1]
        left = eval_raw(node[2], row)
        right = eval_raw(node[3], row)
        if op == "==":
            return _eq(left, right)
        if op == "!=":
            return not _eq(left, right)
        if not _is_number(left) or not _is_number(right):
            raise TypeMismatch(
                "运算符 %r 只允许用于两个 number（int/float），实际为 %s 与 %s"
                % (op, _type_name(left), _type_name(right))
            )
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        return left >= right

    raise TypeMismatch("未知 AST 节点 %r" % (tag,))


def _eq(left, right):
    # bool 不与数值相等；null 只与自身相等；其余按类型相同再比值
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if _type_name(left) != _type_name(right):
        return False
    return left == right

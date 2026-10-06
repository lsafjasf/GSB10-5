"""调用点守卫条件的静态求值。

只处理可安全求值的表达式：字面量常量、模块级常量名、布尔运算、
常量之间的比较。求值结果三态：True / False / None（无法判定）。
依据来源见 docs/CRITERIA.md。
"""

from __future__ import annotations

import ast
from typing import Optional


def _const_value(node: ast.AST, constants: dict):
    """把表达式归约为 Python 常量；失败返回 _UNKNOWN。"""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in constants:
        return constants[node.id]
    if isinstance(node, ast.Tuple):
        values = [_const_value(elt, constants) for elt in node.elts]
        if any(v is _UNKNOWN for v in values):
            return _UNKNOWN
        return tuple(values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        value = _const_value(node.operand, constants)
        if value is _UNKNOWN:
            return _UNKNOWN
        return not value
    if isinstance(node, ast.BoolOp):
        values = [_const_value(v, constants) for v in node.values]
        if any(v is _UNKNOWN for v in values):
            return _UNKNOWN
        if isinstance(node.op, ast.And):
            return all(values)
        return any(values)
    if isinstance(node, ast.Compare):
        return _eval_compare(node, constants)
    return _UNKNOWN


class _Unknown:
    def __repr__(self):  # pragma: no cover
        return "<unknown>"


_UNKNOWN = _Unknown()

_CMP_OPS = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}


def _eval_compare(node: ast.Compare, constants: dict):
    left = _const_value(node.left, constants)
    if left is _UNKNOWN:
        return _UNKNOWN
    for op, comparator in zip(node.ops, node.comparators):
        right = _const_value(comparator, constants)
        if right is _UNKNOWN:
            return _UNKNOWN
        func = _CMP_OPS.get(type(op))
        if func is None:
            return _UNKNOWN
        try:
            result = func(left, right)
        except TypeError:
            return _UNKNOWN
        if not result:
            return False
        left = right
    return True


def eval_guard(expr: ast.AST, constants: dict) -> Optional[bool]:
    """对 if 条件求值。返回 True/False，无法静态判定时返回 None。"""
    value = _const_value(expr, constants)
    if value is _UNKNOWN:
        return None
    return bool(value)


def collect_module_constants(tree: ast.Module) -> dict:
    """收集模块顶层形如 ``NAME = <字面量>`` 的常量绑定。"""
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                value = _const_value(node.value, constants)
                if value is not _UNKNOWN:
                    constants[target.id] = value
    return constants

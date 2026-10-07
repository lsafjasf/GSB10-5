"""随机表达式/数据行生成器（确定性种子），供对拍与基准测试共用。

生成器直接构造 AST，并保证类型正确（比较两侧类型一致、逻辑运算两侧为
bool），这样渲染出的表达式在我们的求值器与参考实现（CPython 语义）下
结果必然一致。
"""

import random

from filterexpr import nodes as N

SCHEMA = (
    ("age", "int"),
    ("score", "float"),
    ("name", "str"),
    ("city", "str"),
    ("active", "bool"),
    ("level", "int"),
)

NAMES = ["alice", "bob", "carol", "dave", "erin", "frank"]
CITIES = ["beijing", "shanghai", "shenzhen", "hangzhou"]

EQ_OPS = ("==", "!=")
ORD_OPS = ("<", "<=", ">", ">=")

# 优先级（数值越大绑得越紧），用于最小括号渲染
_PREC = {N.OR: 1, N.AND: 2, N.NOT: 3, N.CMP: 4, N.NEG: 5}
_ATOM = 6


def random_row(rng):
    return {
        "age": rng.randint(0, 99),
        "score": round(rng.uniform(0, 100), 2),
        "name": rng.choice(NAMES),
        "city": rng.choice(CITIES),
        "active": rng.random() < 0.5,
        "level": rng.randint(1, 10),
    }


def _literal_of_type(rng, type_):
    if type_ == "int":
        return N.node(N.LIT, rng.randint(0, 99))
    if type_ == "float":
        return N.node(N.LIT, round(rng.uniform(0, 100), 2))
    if type_ == "str":
        return N.node(N.LIT, rng.choice(NAMES + CITIES))
    if type_ == "bool":
        return N.node(N.LIT, rng.random() < 0.5)
    raise ValueError(type_)


def _typed_expr(rng, type_, depth):
    """生成指定结果类型的表达式 AST。"""
    if type_ == "bool":
        return _bool_expr(rng, depth)
    if depth <= 0 or rng.random() < 0.6:
        choices = [N.node(N.LIT, None)]
        fields = [f for f, t in SCHEMA if t == type_]
        if fields and rng.random() < 0.7:
            return N.node(N.FIELD, rng.choice(fields))
        return _literal_of_type(rng, type_)
    # 数值类型可用一元负号包一层
    if type_ in ("int", "float") and rng.random() < 0.3:
        return N.node(N.NEG, _typed_expr(rng, type_, depth - 1))
    return _literal_of_type(rng, type_)


def _comparison(rng, depth):
    type_ = rng.choice(("int", "float", "str", "bool"))
    left = _typed_expr(rng, type_, depth - 1)
    right = _typed_expr(rng, type_, depth - 1)
    if type_ in ("int", "float"):
        op = rng.choice(EQ_OPS + ORD_OPS)
    else:
        op = rng.choice(EQ_OPS)
    return N.node(N.CMP, op, left, right)


def _bool_expr(rng, depth):
    if depth <= 0:
        roll = rng.random()
        if roll < 0.55:
            return _comparison(rng, 0)
        if roll < 0.8:
            return N.node(N.FIELD, "active")
        return N.node(N.LIT, rng.random() < 0.5)
    roll = rng.random()
    if roll < 0.45:
        return _comparison(rng, depth)
    if roll < 0.65:
        return N.node(N.AND, _bool_expr(rng, depth - 1), _bool_expr(rng, depth - 1))
    if roll < 0.85:
        return N.node(N.OR, _bool_expr(rng, depth - 1), _bool_expr(rng, depth - 1))
    return N.node(N.NOT, _bool_expr(rng, depth - 1))


def random_expression(rng, depth=3):
    """生成一个类型正确的布尔表达式 AST。"""
    return _bool_expr(rng, depth)


# ---- 渲染 ----


def _fmt_filter(value):
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    return repr(value)


def _fmt_oracle(value):
    return repr(value)


def _render(node, fmt, parent_prec=0, is_right=False):
    tag = node[0]
    if tag == N.LIT:
        return fmt(node[1]), _ATOM
    if tag == N.FIELD:
        return node[1], _ATOM
    if tag == N.NEG:
        inner, _ = _render(node[1], fmt, _PREC[N.NEG])
        text = "-" + inner
        prec = _PREC[N.NEG]
    elif tag == N.NOT:
        inner, _ = _render(node[1], fmt, _PREC[N.NOT])
        text = "not " + inner
        prec = _PREC[N.NOT]
    elif tag in (N.AND, N.OR):
        prec = _PREC[tag]
        left, _ = _render(node[1], fmt, prec)
        right, _ = _render(node[2], fmt, prec, is_right=True)
        text = "%s %s %s" % (left, tag, right)
    elif tag == N.CMP:
        prec = _PREC[N.CMP]
        left, _ = _render(node[2], fmt, prec)
        right, _ = _render(node[3], fmt, prec, is_right=True)
        text = "%s %s %s" % (left, node[1], right)
    else:
        raise ValueError(tag)
    if (
        prec < parent_prec
        or (is_right and prec == parent_prec and tag in (N.AND, N.OR))
        or (prec == parent_prec and tag == N.CMP)
    ):
        return "(" + text + ")", _ATOM
    return text, prec


def render_filter(node):
    """渲染为本语言语法（true/false/null、and/or/not）。"""
    text, _ = _render(node, _fmt_filter)
    return text


def render_oracle(node):
    """渲染为参考实现（CPython）语法：True/False/None。"""
    text, _ = _render(node, _fmt_oracle)
    return text

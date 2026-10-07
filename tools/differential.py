"""对拍工具：自研解析/求值 vs 独立参考实现，逐条比对并落盘 JSONL。

参考实现 oracle_eval 是独立手写的 AST 遍历器，实现与 Python 表达式一致
的优先级、结合性与短路语义；全程不调用 eval/exec/compile 等任何语言
自带求值能力。

三类对拍：
1. fixed   固定优先级/结合性用例，期望值人工按参考语义算出；
2. roundtrip 随机 AST -> 最小括号渲染 -> 重新解析，AST 必须结构一致
   （验证解析器的优先级与结合性）；
3. value   同一 AST 在相同数据行上，自研求值器与参考实现结果必须一致。
"""

import argparse
import json
import random
import sys

sys.path.insert(0, ".")

from filterexpr import nodes as N
from filterexpr.evaluator import evaluate
from filterexpr.parser import parse
from tools import exprgen


# ---- 独立参考实现（手写遍历，不调用语言自带求值能力） ----


def oracle_eval(node, row):
    tag = node[0]
    if tag == N.LIT:
        return node[1]
    if tag == N.FIELD:
        return row[node[1]]
    if tag == N.NOT:
        return not oracle_eval(node[1], row)
    if tag == N.NEG:
        return -oracle_eval(node[1], row)
    if tag == N.AND:
        if not oracle_eval(node[1], row):
            return False
        return bool(oracle_eval(node[2], row))
    if tag == N.OR:
        if oracle_eval(node[1], row):
            return True
        return bool(oracle_eval(node[2], row))
    if tag == N.CMP:
        op = node[1]
        left = oracle_eval(node[2], row)
        right = oracle_eval(node[3], row)
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
    raise ValueError(tag)


# ---- 固定优先级/结合性用例（期望值按参考语义人工计算） ----

R1 = {"a": True, "b": False, "c": True, "d": True, "x": 1, "y": 2, "z": 3}
R2 = {"a": False, "b": True, "c": False, "d": True, "x": 5, "y": 0, "z": -1}
R3 = {"a": True, "b": False, "c": False, "d": False, "x": 0, "y": 0, "z": 0}

FIXED_CASES = [
    # or 优先级低于 and
    ("a or b and c", R3, True),
    ("(a or b) and c", R3, False),
    # not 优先级高于 and
    ("not a and b", R1, False),
    ("not (a and b)", R1, True),
    # 比较优先级高于 not：not x == y 等价 not (x == y)
    ("not x == y", R1, True),
    ("not (x == y)", R1, True),
    # 一元负号优先级高于比较
    ("-x < y", R2, True),
    ("not -x == y", R2, True),
    # and/or 混合
    ("a and b or c and d", R1, True),
    ("a or b or c and d", R2, True),
    ("not not a", R1, True),
    ("x < y and y < z or x == 1", R1, True),
    ("x < y and y < z or x == 1", R2, False),
    ("x == 1 or y == 2 and z == 3", R1, True),
    ("x == 1 or y == 2 and z == 3", R2, False),
    ("(x == 1 or y == 2) and z == 3", R1, True),
    ("(x == 1 or y == 2) and z == 3", R2, False),
    ("not a or not b", R1, True),
    ("not (a or b)", R1, False),
    ("true and not false", R1, True),
]


def strip_pos(node):
    """去掉位置信息，便于结构比较。"""
    tag = node[0]
    payload = node[1:-3]
    out = []
    for item in payload:
        if isinstance(item, tuple):
            out.append(strip_pos(item))
        else:
            out.append(item)
    return (tag,) + tuple(out)


def run_fixed(records):
    failures = 0
    for expr, row, expected in FIXED_CASES:
        actual = evaluate(parse(expr), row)
        ok = actual == expected
        failures += 0 if ok else 1
        records.append(
            {
                "kind": "fixed",
                "expr": expr,
                "row": row,
                "expected": expected,
                "actual": actual,
                "ok": ok,
            }
        )
    return failures


def run_random(records, count, seed, rows_per_case):
    rng = random.Random(seed)
    failures = 0
    for _ in range(count):
        ast = exprgen.random_expression(rng, depth=3)
        text = exprgen.render_filter(ast)

        # roundtrip：重新解析后 AST 结构必须一致（验证优先级/结合性）
        reparsed = parse(text)
        rt_ok = strip_pos(reparsed) == strip_pos(ast)

        for _ in range(rows_per_case):
            row = exprgen.random_row(rng)
            expected = oracle_eval(ast, row)
            actual = evaluate(ast, row)
            ok = rt_ok and (actual == expected)
            failures += 0 if ok else 1
            records.append(
                {
                    "kind": "random",
                    "expr": text,
                    "row": row,
                    "expected": expected,
                    "actual": actual,
                    "roundtrip_ok": rt_ok,
                    "ok": ok,
                }
            )
    return failures


def main():
    ap = argparse.ArgumentParser(description="过滤表达式对拍工具")
    ap.add_argument("--count", type=int, default=1000, help="随机表达式条数")
    ap.add_argument("--seed", type=int, default=20261007)
    ap.add_argument("--rows-per-case", type=int, default=3)
    ap.add_argument("--out", default="data/differential_cases.jsonl")
    args = ap.parse_args()

    records = []
    failures = run_fixed(records)
    failures += run_random(records, args.count, args.seed, args.rows_per_case)

    with open(args.out, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    total = len(records)
    print(
        "对拍完成：共 %d 条（固定 %d + 随机 %d x %d 行），失败 %d 条 -> %s"
        % (total, len(FIXED_CASES), args.count, args.rows_per_case, failures, args.out)
    )
    if failures:
        for rec in records:
            if not rec["ok"]:
                print("MISMATCH:", json.dumps(rec, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()

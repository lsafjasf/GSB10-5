"""基准测试：N 行数据 x M 条表达式的求值耗时与内存峰值。

- 数据行与表达式都由固定种子的生成器产生，结果可复现；
- 表达式只编译一次，计时循环是纯求值；
- 额外混入 2 条必然出错的表达式，验证大规模下的错误分类统计；
- 内存峰值取 resource.getrusage 的 ru_maxrss（含数据集本身）。
"""

import argparse
import json
import platform
import random
import resource
import sys
import time

sys.path.insert(0, ".")

from filterexpr import compile as compile_expr
from filterexpr.errors import FieldMissing, TypeMismatch
from tools import exprgen


def make_rows(count, seed):
    rng = random.Random(seed)
    rows = []
    append = rows.append
    for _ in range(count):
        append(exprgen.random_row(rng))
    return rows


def make_exprs(count, seed):
    rng = random.Random(seed + 1)
    sources = [
        exprgen.render_filter(exprgen.random_expression(rng, depth=2))
        for _ in range(count)
    ]
    # 两条探针：一条字段不存在、一条类型不匹配，验证错误分类统计
    sources.append("unknown_field > 1")
    sources.append("name > 1")
    return [compile_expr(s) for s in sources]


def main():
    ap = argparse.ArgumentParser(description="过滤表达式求值基准测试")
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--exprs", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20261007)
    ap.add_argument("--json", default=None, help="结果写入的 JSON 文件")
    args = ap.parse_args()

    t0 = time.perf_counter()
    exprs = make_exprs(args.exprs, args.seed)
    t_compile = time.perf_counter() - t0

    t0 = time.perf_counter()
    rows = make_rows(args.rows, args.seed)
    t_gen = time.perf_counter() - t0

    matches = [0] * len(exprs)
    errors = {"field_missing": 0, "type_mismatch": 0}
    bound = [e.evaluate for e in exprs]

    t0 = time.perf_counter()
    total = 0
    for row in rows:
        for i, fn in enumerate(bound):
            try:
                if fn(row):
                    matches[i] += 1
            except FieldMissing:
                errors["field_missing"] += 1
            except TypeMismatch:
                errors["type_mismatch"] += 1
        total += 1
    t_eval = time.perf_counter() - t0

    peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    evals = total * len(exprs)
    result = {
        "rows": total,
        "expressions": len(exprs),
        "evaluations": evals,
        "compile_seconds": round(t_compile, 4),
        "row_generation_seconds": round(t_gen, 4),
        "eval_seconds": round(t_eval, 4),
        "eval_per_ns": round(t_eval / evals * 1e9, 1),
        "matched_rows_per_expr_avg": round(sum(matches) / len(exprs), 1),
        "errors": errors,
        "peak_rss_mb": round(peak_kb / 1024, 1),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()

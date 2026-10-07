"""确定性地生成约 10 万行的基准工程，并记录注入问题的精确位置。

用法: python3 tools/gen_benchmark.py [输出目录] [目标行数]
生成 benchmark/manifest.json，记录每个注入问题的 (file, line, rule_id)，
供 run_benchmark.py 计算召回率、误报率与定位精度。
"""
import json
import os
import random
import sys

SEED = 20261007


def normal_func(lines, tag, rng):
    lines.append(f"def func_{tag}(alpha, beta={rng.randint(1, 99)}):")
    lines.append(f"    total = alpha * beta + {rng.randint(0, 999)}")
    lines.append("    if total > 1000:")
    lines.append(f"        return total - {rng.randint(1, 9)}")
    lines.append("    for item in range(3):")
    lines.append("        total += item")
    lines.append("    return total")
    lines.append("")
    lines.append("")


def issue_func(lines, tag, kind, manifest, relpath):
    if kind == "CR001":
        manifest.append({"file": relpath, "line": len(lines) + 1, "rule_id": "CR001"})
        lines.append(f"def bad_default_{tag}(alpha, data=[]):")
        lines.append("    data.append(alpha)")
        lines.append("    return data")
    elif kind == "CR002":
        lines.append(f"def bare_except_{tag}(path):")
        lines.append("    try:")
        lines.append("        return open(path).read()")
        manifest.append({"file": relpath, "line": len(lines) + 1, "rule_id": "CR002"})
        lines.append("    except:")
        lines.append('        return ""')
    elif kind == "CR003":
        lines.append(f"def use_eval_{tag}(expr):")
        manifest.append({"file": relpath, "line": len(lines) + 1, "rule_id": "CR003"})
        lines.append("    return eval(expr)")
    elif kind == "CR005":
        lines.append(f"def find_row_{tag}(cur, uid):")
        manifest.append({"file": relpath, "line": len(lines) + 1, "rule_id": "CR005"})
        lines.append('    cur.execute(f"SELECT * FROM t WHERE id = {uid}")')
    lines.append("")
    lines.append("")


def gen_file(file_idx, n_funcs, rng, manifest, relpath):
    lines = [f'"""基准生成模块 {file_idx}。"""', "", ""]
    if file_idx % 5 == 0:
        manifest.append({"file": relpath, "line": len(lines) + 1, "rule_id": "CR004"})
        lines.append("import json  # 基准注入：未使用导入")
        lines.append("")
        lines.append("")
    kinds = ["CR001", "CR002", "CR003", "CR005"]
    for func_idx in range(n_funcs):
        tag = f"{file_idx}_{func_idx}"
        if func_idx % 10 == 5:
            issue_func(lines, tag, kinds[(file_idx + func_idx) % 4], manifest, relpath)
        else:
            normal_func(lines, tag, rng)
    return "\n".join(lines) + "\n"


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "benchmark"
    target_lines = int(sys.argv[2]) if len(sys.argv) > 2 else 100_000
    rng = random.Random(SEED)
    os.makedirs(out_dir, exist_ok=True)
    manifest = []
    total = 0
    file_idx = 0
    while total < target_lines:
        relpath = f"mod_{file_idx:04d}.py"
        content = gen_file(file_idx, 56, rng, manifest, relpath)
        with open(os.path.join(out_dir, relpath), "w", encoding="utf-8") as fh:
            fh.write(content)
        total += content.count("\n")
        file_idx += 1
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    print(f"生成 {file_idx} 个文件，共 {total} 行，注入 {len(manifest)} 个问题 -> {out_dir}")


if __name__ == "__main__":
    main()

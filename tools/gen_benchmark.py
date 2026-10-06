"""生成 >=10 万行的合成 Python 项目 (确定性，固定种子)。

合成代码刻意混入少量真实问题 (planted issues)，用于在大规模代码上
复核漏报：生成器把 planted 数量写入 expected.txt，run_benchmark.py
会核对检出的对应规则报警数是否一致。
"""

import argparse
import json
import os
import random
import shutil

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(REPO_ROOT, "build", "bench")


def gen_module(idx: int, rng: random.Random, planted: dict) -> str:
    lines = [f'"""synthetic bench module {idx}"""', ""]
    # 每 10 个模块种一个可变默认参数
    if idx % 10 == 0:
        lines += [
            f"def bad_default_{idx}(items=[]):",
            "    items.append(1)",
            "    return items",
            "",
        ]
        planted["RV001"] = planted.get("RV001", 0) + 1
    # 每 25 个模块种一个裸 except
    if idx % 25 == 0:
        lines += [
            f"def risky_{idx}():",
            "    try:",
            "        return 1 / 1",
            "    except:",
            "        return 0",
            "",
        ]
        planted["RV002"] = planted.get("RV002", 0) + 1
    # 每 40 个模块种一处不可达代码
    if idx % 40 == 0:
        lines += [
            f"def dead_{idx}():",
            "    return 1",
            "    print('dead')",
            "",
        ]
        planted["RV006"] = planted.get("RV006", 0) + 1

    fn = 0
    while len(lines) < 500:
        fn += 1
        lines += [
            f"def func_{idx}_{fn}(value, count):",
            "    total = value",
            "    for step in range(count):",
            "        total += step * 2",
            "    if total is None:",
            "        return 0",
            '    text = f"result: {total}"',
            "    return len(text) + total",
            "",
        ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--min-lines", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=20261006)
    args = parser.parse_args()

    if os.path.exists(args.out):
        shutil.rmtree(args.out)
    os.makedirs(args.out)

    rng = random.Random(args.seed)
    planted: dict = {}
    total_lines = 0
    idx = 0
    while total_lines < args.min_lines:
        idx += 1
        source = gen_module(idx, rng, planted)
        path = os.path.join(args.out, f"mod_{idx:04d}.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source)
        total_lines += source.count("\n")

    meta = {
        "files": idx,
        "lines": total_lines,
        "seed": args.seed,
        "planted": planted,
    }
    with open(os.path.join(args.out, "expected.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    print(f"生成 {idx} 个文件, 共 {total_lines} 行 -> {args.out}")
    print(f"planted issues: {planted}")


if __name__ == "__main__":
    main()

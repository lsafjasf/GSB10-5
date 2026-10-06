"""命令行入口: python -m revcheck <文件或目录> [...]"""

from __future__ import annotations

import argparse
import sys

from .engine import check_project
from .rules import RULES


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="revcheck",
        description="基于 AST 语法结构的代码评审检查 (仅标准库)",
    )
    parser.add_argument("paths", nargs="*", help="要检查的文件或目录")
    parser.add_argument(
        "--rules", action="store_true", help="列出全部规则与依据后退出"
    )
    args = parser.parse_args(argv)

    if args.rules:
        for meta in RULES.values():
            print(f"{meta.rule_id} {meta.title}")
            print(f"    依据: {meta.rationale}")
            print(f"    语法基础: {meta.ast_basis}")
        return 0

    if not args.paths:
        parser.error("请提供至少一个文件或目录 (或用 --rules 查看规则)")

    findings = []
    for path in args.paths:
        findings.extend(check_project(path))
    for finding in findings:
        print(finding)
    print(f"\n共 {len(findings)} 条报警", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())

import argparse
import sys

from .checker import check_paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="codereview", description="基于 AST 的 Python 代码评审检查器（标准库实现）"
    )
    parser.add_argument("paths", nargs="+", help="要检查的文件或目录")
    args = parser.parse_args(argv)

    findings = check_paths(args.paths)
    for finding in findings:
        print(finding.format())
    print(f"共 {len(findings)} 条报警", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())

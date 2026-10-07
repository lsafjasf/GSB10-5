"""命令行入口。

用法：
  python3 -m termcheck check  --termbase tb.json --corpus corpus.jsonl \
      [--format json|md] [--output report.md]
  python3 -m termcheck benchmark [--entries 100000] [--chars 1000000]
"""

import argparse
import sys

from .checker import Checker, iter_segments
from .report import to_json, to_markdown
from .termbase import load_file


def main(argv=None):
    parser = argparse.ArgumentParser(prog="termcheck",
                                     description="译名一致性检查工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="对语料执行一致性检查")
    p_check.add_argument("--termbase", required=True, help="术语库 JSON")
    p_check.add_argument("--corpus", required=True, help="语料 JSONL")
    p_check.add_argument("--format", choices=["json", "md"], default="md")
    p_check.add_argument("--output", help="输出文件（缺省打印到 stdout）")
    p_check.add_argument("--cue-window", type=int, default=80,
                         help="义项 cue 词上下文窗口大小（字符）")

    p_bench = sub.add_parser("benchmark", help="性能基准（合成数据）")
    p_bench.add_argument("--entries", type=int, default=100000)
    p_bench.add_argument("--chars", type=int, default=1000000)
    p_bench.add_argument("--seed", type=int, default=42)

    args = parser.parse_args(argv)

    if args.cmd == "check":
        tb = load_file(args.termbase)
        checker = Checker(tb, cue_window=args.cue_window)
        report = checker.check(list(iter_segments(args.corpus)))
        text = to_json(report) if args.format == "json" else to_markdown(report)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
            print("报告已写入 %s（冲突 %d，待确认 %d）"
                  % (args.output, len(report.conflicts), len(report.review)))
        else:
            print(text)
        return 1 if report.conflicts else 0

    if args.cmd == "benchmark":
        from .benchmark import run_benchmark
        result = run_benchmark(entries=args.entries, chars=args.chars,
                               seed=args.seed)
        for k, v in result.items():
            print("%-28s %s" % (k, v))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

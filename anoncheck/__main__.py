"""命令行入口。

用法：
  python -m anoncheck check [--n 20000] [--csv data.csv] [--out out] [--k 5]
  python -m anoncheck bench [--n 1000000] [--out out]
"""

import argparse
import csv

from .dataset import generate
from .report import run_check
from .benchmark import run_benchmark


def load_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        headers = next(reader)
        rows = [tuple(row) for row in reader]
    return headers, rows


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="anoncheck",
        description="匿名化可逆性（重识别风险）检查工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="对数据执行完整风险检查并出报告")
    p_check.add_argument("--n", type=int, default=20000,
                         help="使用内置合成数据时的记录数（默认 20000）")
    p_check.add_argument("--csv", help="使用外部 CSV 文件（首行为表头）")
    p_check.add_argument("--out", default="out", help="输出目录（默认 out）")
    p_check.add_argument("--k", type=int, default=5,
                         help="稀有组合阈值（默认 5）")
    p_check.add_argument("--max-order", type=int, default=3,
                         help="QI 组合最大阶数（默认 3）")

    p_bench = sub.add_parser("bench", help="100 万条记录性能基准")
    p_bench.add_argument("--n", type=int, default=1_000_000)
    p_bench.add_argument("--out", default="out")

    args = parser.parse_args(argv)

    if args.command == "check":
        if args.csv:
            headers, rows = load_csv(args.csv)
            source = args.csv
        else:
            headers, rows = generate(args.n)
            source = f"内置合成数据 n={args.n}"
        report = run_check(headers, rows, args.out,
                           k_threshold=args.k, max_order=args.max_order)
        print(f"数据源：{source}")
        print(report)
    elif args.command == "bench":
        result = run_benchmark(n=args.n, out_dir=args.out)
        print(f"检查耗时：{result['check_seconds']:.2f} s；"
              f"tracemalloc 峰值：{result['tracemalloc_peak_mib']:.1f} MiB；"
              f"RSS 峰值：{result['rss_peak_mib']:.1f} MiB")
        print(f"详情见 {args.out}/benchmark.md")


if __name__ == "__main__":
    main()

"""命令行入口：python3 -m docdiff <command>"""
from __future__ import annotations

import argparse
import json


def main(argv=None) -> None:
    from . import TAU_BODY, TAU_TITLE, diff_staged

    parser = argparse.ArgumentParser(
        prog="docdiff", description="文档版本结构差异工具（Python 3，仅标准库）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("diff", help="比较两个 Markdown 文档")
    d.add_argument("old", help="旧版本文件")
    d.add_argument("new", help="新版本文件")
    d.add_argument("--json", action="store_true", help="输出 JSON 明细")
    d.add_argument("--tau-body", type=float, default=TAU_BODY,
                   help="正文配对阈值（默认 %(default)s）")
    d.add_argument("--tau-title", type=float, default=TAU_TITLE,
                   help="无正文节配对阈值（默认 %(default)s）")

    sub.add_parser("bench", help="生成 10 万行基准文档对到 bench/")
    pe = sub.add_parser("perf", help="10 万行性能测量")
    pe.add_argument("--trials", type=int, default=3)
    sub.add_parser("sweep", help="阈值影响扫描")
    sub.add_parser("report", help="生成 reports/ 下全部报告")

    args = parser.parse_args(argv)

    if args.cmd == "diff":
        with open(args.old, encoding="utf-8") as fh:
            old_text = fh.read()
        with open(args.new, encoding="utf-8") as fh:
            new_text = fh.read()
        res = diff_staged(old_text, new_text, args.tau_body, args.tau_title)
        if args.json:
            print(json.dumps({"summary": res["summary"],
                              "records": res["records"]},
                             ensure_ascii=False, indent=2))
        else:
            from .report import render_text
            print(render_text(res))
    elif args.cmd == "bench":
        from .benchgen import main as bench_main
        bench_main()
    elif args.cmd == "perf":
        from .report import format_perf, run_perf
        print(format_perf(run_perf(args.trials)))
    elif args.cmd == "sweep":
        from .report import format_sweep, run_sweep
        print(format_sweep(run_sweep()))
    elif args.cmd == "report":
        from .report import write_reports
        write_reports()


if __name__ == "__main__":
    main()

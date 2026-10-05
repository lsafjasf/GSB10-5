"""termcheck 命令行入口。

用法：
    python3 check.py 术语库.tsv 文档1 [文档2 ...] [--format md|json] [--out 文件]
"""

import argparse
import json
import sys

from termcheck import Checker, Termbase, summarize, SEV_ERROR, SEV_REVIEW


def render_md(rows):
    lines = ["| 级别 | 条目 | 规范译名 | 实际出现 | 位置 | 原因 | 上下文 |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        lines.append("| {severity} | {entry_id} | {approved} | {found} | {loc} | {reason} | …{context}… |".format(
            loc=f"{r['source']}:{r['line']}:{r['col']}", **r))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="译名一致性检查")
    ap.add_argument("termbase")
    ap.add_argument("docs", nargs="+")
    ap.add_argument("--format", choices=["md", "json"], default="md")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    checker = Checker(Termbase.load(args.termbase))
    rows = []
    for path in args.docs:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for f in checker.check(text, source=path):
            d = f.to_dict()
            d["source"] = path
            rows.append(d)

    errors = [r for r in rows if r["severity"] == SEV_ERROR]
    reviews = [r for r in rows if r["severity"] == SEV_REVIEW]

    if args.format == "json":
        out = json.dumps(rows, ensure_ascii=False, indent=2)
    else:
        out = render_md(rows)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out + "\n")
    else:
        print(out)

    print(f"检查完成：冲突 {len(errors)} 条，待人工确认 {len(reviews)} 条。", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

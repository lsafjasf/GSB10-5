#!/usr/bin/env python3
"""docdiff 命令行入口。

用法:
  python3 cli.py OLD.md NEW.md [--threshold 0.6] [--json OUT.json] [--md OUT.md]
  python3 cli.py OLD.md NEW.md --sweep 0.3,0.5,0.6,0.7,0.8,0.9,0.95 [--sweep-json OUT.json]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from docdiff import (diff_documents, render_markdown, sweep_thresholds,
                     verify_roundtrip)
from docdiff.diff import CATEGORY_LABELS, DEFAULT_THRESHOLD, to_json


def main(argv=None):
    ap = argparse.ArgumentParser(prog="docdiff", description="文档版本结构差异")
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--md", dest="md_out")
    ap.add_argument("--sweep", help="逗号分隔的阈值列表，输出各阈值归类对比")
    ap.add_argument("--sweep-json", dest="sweep_json")
    args = ap.parse_args(argv)

    with open(args.old, encoding="utf-8") as f:
        old_text = f.read()
    with open(args.new, encoding="utf-8") as f:
        new_text = f.read()

    if args.sweep:
        thresholds = [float(x) for x in args.sweep.split(",")]
        rows = sweep_thresholds(old_text, new_text, thresholds)
        print(f"{'阈值':>6} {'配对数':>6}  " +
              "  ".join(f"{k}" for k in
                        ["added", "deleted", "reparent", "level_change",
                         "rename", "modified", "unchanged"]))
        for r in rows:
            s = r["stats"]
            print(f"{r['threshold']:>6.2f} {r['matched_pairs']:>6}  " +
                  "  ".join(str(s.get(k, 0)) for k in
                           ["added", "deleted", "reparent", "level_change",
                            "rename", "modified", "unchanged"]))
        if args.sweep_json:
            with open(args.sweep_json, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, indent=2)
            print(f"阈值扫描明细已写入 {args.sweep_json}")
        return 0

    diff = diff_documents(old_text, new_text, threshold=args.threshold)

    print(f"阈值 {diff.threshold} | 旧版 {diff.old_total_lines} 行 "
          f"{diff.old_section_count} 章节 | 新版 {diff.new_total_lines} 行 "
          f"{diff.new_section_count} 章节 | 配对 {diff.matched_pairs} 对")
    for cat, count in sorted(diff.stats().items(), key=lambda kv: -kv[1]):
        print(f"  {CATEGORY_LABELS.get(cat, cat)}({cat}): {count}")
    rt = verify_roundtrip(diff)
    print(f"摘要<->明细还原验证: {'通过' if rt['ok'] else '失败'} "
          f"(changes={rt['change_count']}, manifest={rt['manifest_count']}, "
          f"expanded={rt['expanded_count']})")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            f.write(to_json(diff))
        print(f"JSON 明细已写入 {args.json_out}")
    if args.md_out:
        with open(args.md_out, "w", encoding="utf-8") as f:
            f.write(render_markdown(diff))
        print(f"Markdown 报告已写入 {args.md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

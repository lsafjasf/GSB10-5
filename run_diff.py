#!/usr/bin/env python3
"""资源清单差异比对 CLI。

用法:
    python3 run_diff.py BASELINE.json CANDIDATE.json [-o report.json]
"""
import argparse
import json
import sys

from manifestdiff import compare_manifests, load_manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="资源清单差异比对（含合规判定与归因）")
    parser.add_argument("baseline", help="基线清单 JSON 文件")
    parser.add_argument("candidate", help="候选清单 JSON 文件")
    parser.add_argument("-o", "--output", help="报告输出路径（默认打印到 stdout）")
    args = parser.parse_args(argv)

    report = compare_manifests(load_manifest(args.baseline),
                               load_manifest(args.candidate))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print(f"报告已写入 {args.output}")
    else:
        print(text)

    verdict = report["summary"]["overall_verdict"]
    print(f"总体判定: {verdict}", file=sys.stderr)
    return 1 if verdict == "violation" else 0


if __name__ == "__main__":
    sys.exit(main())

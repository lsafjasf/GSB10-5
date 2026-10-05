#!/usr/bin/env python3
"""环境参数差异对比 CLI（仅依赖标准库）。

退出码：0 = 无"待确认/疑似遗漏"问题项；1 = 存在问题项；2 = 加载错误。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from envdiff import compare  # noqa: E402
from envdiff.attribution import load_json_file  # noqa: E402
from envdiff.loader import load_env_file  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="环境参数差异对比")
    parser.add_argument("--a", required=True, help="环境 A 参数文件（JSON 或 KEY=VALUE）")
    parser.add_argument("--b", required=True, help="环境 B 参数文件（JSON 或 KEY=VALUE）")
    parser.add_argument("--label-a", default="env_a")
    parser.add_argument("--label-b", default="env_b")
    parser.add_argument("--expected", help="声明的期望值 JSON")
    parser.add_argument("--whitelist", help="允许差异白名单 JSON（数组）")
    parser.add_argument("--baseline", help="历史基线参数文件")
    parser.add_argument("--json", action="store_true", help="输出 JSON 报告")
    args = parser.parse_args(argv)

    try:
        env_a = load_env_file(args.a)
        env_b = load_env_file(args.b)
        expected = load_json_file(args.expected) if args.expected else None
        whitelist = load_json_file(args.whitelist) if args.whitelist else None
        baseline = load_env_file(args.baseline) if args.baseline else None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"加载失败：{exc}", file=sys.stderr)
        return 2

    report = compare(env_a, env_b, expected=expected, whitelist=whitelist,
                     baseline=baseline, label_a=args.label_a, label_b=args.label_b)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
          if args.json else report.to_text())
    return 1 if report.problem_count else 0


if __name__ == "__main__":
    raise SystemExit(main())

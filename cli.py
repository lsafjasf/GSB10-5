"""envdiff 命令行入口：python3 cli.py --a ... --b ... [--expected --whitelist --baseline]"""

import argparse
import json
import sys

import envdiff


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="两套环境参数差异对比")
    p.add_argument("--a", required=True, help="环境 A 参数文件 (key=value/json)")
    p.add_argument("--b", required=True, help="环境 B 参数文件 (key=value/json)")
    p.add_argument("--expected", help="声明的期望值 JSON")
    p.add_argument("--whitelist", help="允许差异白名单 JSON")
    p.add_argument("--baseline", help="历史基线 JSON")
    p.add_argument("--case-sensitive-keys", default="",
                   help="大小写敏感的 key glob，逗号分隔")
    p.add_argument("--sensitive-patterns", default="",
                   help="额外敏感参数 key glob，逗号分隔")
    p.add_argument("--report", help="输出 Markdown 报告路径")
    p.add_argument("--json-out", help="输出 JSON 结果路径")
    args = p.parse_args(argv)

    env_a = envdiff.load_env_file(args.a)
    env_b = envdiff.load_env_file(args.b)
    expected = envdiff.load_json_file(args.expected)
    wl_data = envdiff.load_json_file(args.whitelist) or []
    whitelist = envdiff.parse_whitelist(wl_data)
    baseline = envdiff.load_json_file(args.baseline)
    cs_keys = [s for s in args.case_sensitive_keys.split(",") if s]
    extra_sens = [s for s in args.sensitive_patterns.split(",") if s]

    report = envdiff.compare_envs(
        env_a, env_b, expected, whitelist, baseline,
        case_sensitive_keys=cs_keys, extra_sensitive_patterns=extra_sens)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(envdiff.render_markdown(report, args.a, args.b))

    s = report.summary()
    print("对比汇总:", {envdiff.CATEGORY_LABELS[k]: v for k, v in s.items()})
    for cat in (envdiff.SUSPECTED_MISS, envdiff.NEEDS_CONFIRM):
        for it in report.by_category(cat):
            print(f"  [{envdiff.CATEGORY_LABELS[cat]}] {it.key}: {it.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

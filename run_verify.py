#!/usr/bin/env python3
"""回滚可行性验证入口。

用法：
  python3 run_verify.py                                # 全部场景，每个 10 万条
  python3 run_verify.py --scenario new_enum --count 50000
  python3 run_verify.py --perf --perf-count 1000000    # 100 万条性能测量
"""

import argparse
import json
import os
import sys
import tempfile

from rollback_check import gen, perf
from rollback_check.gen import SCENARIOS
from rollback_check.verify import Verifier, build_report


def run_scenarios(names, count, outdir):
    datadir = os.path.join(outdir, "data")
    os.makedirs(datadir, exist_ok=True)
    verifier = Verifier()
    reports = []
    for scenario in names:
        data_path, index_path = gen.write_scenario(datadir, scenario, count)
        stats = verifier.verify_files(data_path, index_path)
        report = build_report(scenario, stats)
        report_path = os.path.join(outdir, "report_%s.json" % scenario)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        reports.append(report)
        print("[%s] 总数=%d 可读=%d 不可读=%d 耗时=%.2fs => %s"
              % (scenario, report["total"], report["read_ok"],
                 report["read_failed"], report["verify_elapsed_sec"],
                 report["conclusion"]))
    return reports


def render_summary(reports, perf_result, count):
    lines = []
    lines.append("# 回滚可行性验证结果\n")
    lines.append("旧版本读取器：v1（Python 标准库 JSON Lines 读取，严格枚举/类型校验，"
                 "未知字段 ignore）。\n")
    lines.append("## 场景结论（每场景 %s 条新版本写入的数据）\n" % format(count, ","))
    lines.append("| 场景 | 总数 | 旧版本可读 | 不可读 | 验证耗时(s) | 结论 |")
    lines.append("|---|---|---|---|---|---|")
    for r in reports:
        lines.append("| %s | %d | %d | %d | %.3f | **%s** |"
                     % (r["scenario"], r["total"], r["read_ok"], r["read_failed"],
                        r["verify_elapsed_sec"], r["conclusion"]))
    lines.append("")
    lines.append("## 不兼容清单与处理步骤\n")
    for r in reports:
        lines.append("### %s — %s" % (r["scenario"], r["conclusion"]))
        if r["incompatibilities"]:
            for item in r["incompatibilities"]:
                lines.append("- 字段/位置：`%s`；类型：`%s`；影响：%d 条；首现行：%d；"
                             "样本：`%s`"
                             % (item["path"], item["kind"], item["count"],
                                item["first_line"], item["sample"]))
                lines.append("  - 说明：%s" % item["detail"])
                lines.append("  - 处理：%s" % item["suggestion"])
        else:
            lines.append("- 无 error 级不兼容项。")
        if r["informational"]:
            lines.append("- info（不阻断回滚）：")
            for item in r["informational"]:
                lines.append("  - `%s`（%s）×%d：%s"
                             % (item["path"], item["kind"], item["count"],
                                item["suggestion"]))
        lines.append("")
        lines.append("处理步骤：")
        for i, step in enumerate(r["handling_steps"], 1):
            lines.append("%d. %s" % (i, step))
        lines.append("")

    if perf_result:
        p = perf_result
        lines.append("## 性能数据（%s 条）\n" % format(p["count"], ","))
        lines.append("- 场景：`%s`，数据文件大小 %s MB（流式逐行读取，%s）"
                     % (p["scenario"], p["data_file_mb"], "全量不驻留内存"))
        lines.append("- 生成耗时：%.3f s；验证耗时：%.3f s；吞吐：%s 条/s"
                     % (p["generate_elapsed_sec"], p["verify_elapsed_sec"],
                        format(p["records_per_sec"], ",")))
        lines.append("- 进程内存峰值 ru_maxrss：%s MB；验证阶段当前 RSS：%s -> %s MB"
                     "（增量 %s MB，流式读取不随数据量增长）"
                     % (p["peak_rss_mb"], p["verify_rss_before_mb"],
                        p["verify_rss_after_mb"], p["verify_rss_delta_mb"]))
        lines.append("- 验证结论：%s（可读 %d / 不可读 %d）\n"
                     % (p["conclusion"], p["read_ok"], p["read_failed"]))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="回滚可行性验证")
    parser.add_argument("--scenario", default="all",
                        help="场景名或 all：%s" % ", ".join(SCENARIOS))
    parser.add_argument("--count", type=int, default=100_000,
                        help="每个场景生成的记录数（默认 100000）")
    parser.add_argument("--outdir", default="report", help="报告输出目录")
    parser.add_argument("--perf", action="store_true", help="执行大数据量性能测量")
    parser.add_argument("--perf-count", type=int, default=1_000_000)
    parser.add_argument("--perf-scenario", default="additive_fields")
    args = parser.parse_args(argv)

    os.makedirs(args.outdir, exist_ok=True)
    names = SCENARIOS if args.scenario == "all" else (args.scenario,)
    for name in names:
        if name not in SCENARIOS:
            parser.error("未知场景 %r" % name)

    reports = run_scenarios(names, args.count, args.outdir)

    perf_result = None
    if args.perf:
        print("运行 %s 条性能测量（场景 %s）..." % (format(args.perf_count, ","),
                                              args.perf_scenario))
        perf_result = perf.run_perf(
            count=args.perf_count, scenario=args.perf_scenario,
            directory=tempfile.mkdtemp(prefix="rollback_perf_"))
        with open(os.path.join(args.outdir, "perf.json"), "w", encoding="utf-8") as f:
            json.dump(perf_result, f, ensure_ascii=False, indent=2)
        print("[perf] 验证耗时=%.2fs 吞吐=%s条/s 内存峰值=%sMB => %s"
              % (perf_result["verify_elapsed_sec"],
                 format(perf_result["records_per_sec"], ","),
                 perf_result["peak_rss_mb"], perf_result["conclusion"]))

    summary = render_summary(reports, perf_result, args.count)
    with open(os.path.join(args.outdir, "summary.md"), "w", encoding="utf-8") as f:
        f.write(summary)
    print("报告已写入 %s/（report_*.json、perf.json、summary.md）" % args.outdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())

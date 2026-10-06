#!/usr/bin/env python3
"""运行演示：冲突检测 + 翻译覆盖率/回退 + 发布稳定性校验，输出报告。"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from errcodes import (
    FALLBACK_CODE,
    build_from_paths,
    check_stability,
    load_locale,
    load_snapshot,
    Translator,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
MODULES = [
    os.path.join(ROOT, "demo/modules/auth.json"),
    os.path.join(ROOT, "demo/modules/payment.json"),
]
LOCALE = os.path.join(ROOT, "demo/locales/zh-CN.json")
SNAPSHOT = os.path.join(ROOT, "demo/releases/v2026.09.json")
REPORT_DIR = os.path.join(ROOT, "reports")


def main():
    os.makedirs(REPORT_DIR, exist_ok=True)
    registry = build_from_paths(MODULES)
    locale, messages = load_locale(LOCALE)
    translator = Translator(registry, locale, messages)
    snapshot = load_snapshot(SNAPSHOT)
    stability = check_stability(registry, snapshot)

    conflict_report = registry.as_dict()
    coverage_report = translator.coverage()

    # 翻译回退示例：有翻译 / 缺翻译回退默认文案 / 缺翻译回退原码 / 未注册码
    samples = ["E0101001", "E0205001", "E0104999", "ERR0001A", "E9999999"]
    fallback_default = [
        {"code": code, "text": text, "source": source}
        for code, (text, source) in zip(samples, [translator.translate(c) for c in samples])
    ]
    translator_code = Translator(registry, locale, messages, fallback=FALLBACK_CODE)
    fallback_code_sample = {
        "code": "E0205001",
        "text": translator_code.translate("E0205001")[0],
        "source": translator_code.translate("E0205001")[1],
    }

    with open(os.path.join(REPORT_DIR, "conflicts.json"), "w", encoding="utf-8") as fh:
        json.dump(conflict_report, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(REPORT_DIR, "coverage.json"), "w", encoding="utf-8") as fh:
        json.dump(coverage_report, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(REPORT_DIR, "stability.json"), "w", encoding="utf-8") as fh:
        json.dump(stability, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(REPORT_DIR, "fallback.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {"fallback_default": fallback_default,
             "fallback_code": fallback_code_sample},
            fh, ensure_ascii=False, indent=2)

    print("=" * 70)
    print("冲突检测（共 %d 个唯一码，%d 个问题）"
          % (conflict_report["total_codes"], len(conflict_report["issues"])))
    print("=" * 70)
    for issue in conflict_report["issues"]:
        print("[%s] %-16s %s" %
              (issue["severity"], issue["kind"], issue["message"]))

    print()
    print("=" * 70)
    print("翻译覆盖率（%s）" % locale)
    print("=" * 70)
    print("总计 %d，已翻译 %d，缺失 %d，覆盖率 %.2f%%"
          % (coverage_report["total"], coverage_report["translated"],
             coverage_report["missing_count"], coverage_report["coverage_percent"]))
    for module, stats in coverage_report["by_module"].items():
        print("  %-10s %d/%d  %.2f%%"
              % (module, stats["translated"], stats["total"],
                 stats["coverage_percent"]))
    print("缺失翻译：")
    for item in coverage_report["missing"]:
        print("  %s  %-20s -> 回退默认文案：%s"
              % (item["code"], item["name"], item["default_message"]))

    print()
    print("回退示例：")
    for item in fallback_default:
        print("  %-10s [%-10s] %s" % (item["code"], item["source"], item["text"]))
    print("  %-10s [%-10s] %s （fallback=code 时）"
          % (fallback_code_sample["code"], fallback_code_sample["source"],
             fallback_code_sample["text"]))

    print()
    print("=" * 70)
    print("发布稳定性校验（基线 %s）" % stability["baseline_version"])
    print("=" * 70)
    print("已发布 %d 码，当前 %d 码；新增 %d，删除 %d，改义 %d，文案漂移 %d"
          % (stability["released_count"], stability["current_count"],
             stability["added_count"], stability["removed_count"],
             stability["repurposed_count"], stability["message_drift_count"]))
    print("破坏性变更：%s" % ("是" if stability["has_breaking_changes"] else "否"))
    for item in stability["repurposed"]:
        print("  [改义] %s: %s -> %s（%s）"
              % (item["code"], item["old_name"], item["new_name"], item["module"]))
    for item in stability["message_drift"]:
        print("  [文案漂移] %s: '%s' -> '%s'"
              % (item["code"], item["old_message"], item["new_message"]))
    for item in stability["removed"]:
        print("  [删除] %s %s" % (item["code"], item["name"]))
    for item in stability["added"]:
        print("  [新增] %s %s" % (item["code"], item["name"]))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

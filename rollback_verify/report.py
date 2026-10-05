"""Aggregates verification results into JSON + human-readable reports."""
import json
from collections import Counter, defaultdict

from . import reader as R
from .checker import VERDICT_LABELS


def aggregate_issues(issues, max_samples_per_field=3):
    by_field = defaultdict(list)
    for i in issues:
        if i.kind == R.KIND_UNKNOWN_FIELD:
            continue
        by_field[(i.surface, i.kind, i.field)].append(i)
    summary = []
    for (surface, kind, field), items in sorted(by_field.items()):
        summary.append({
            "surface": surface,
            "kind": kind,
            "field": field,
            "count": len(items),
            "convertible": all(i.convertible for i in items),
            "expected": items[0].expected,
            "actual_examples": sorted({json.dumps(i.actual, ensure_ascii=False) if isinstance(i.actual, (list, dict)) else str(i.actual) for i in items})[:5],
            "sample_record_ids": [i.record_id for i in items[:max_samples_per_field]],
            "conversion": items[0].conversion,
        })
    return summary


def build_report(scenario_name, old_version, new_version, total_records,
                 ok_records, issues, verdict, steps, sample_ok, sample_bad,
                 elapsed_sec=None, peak_mem_bytes=None):
    return {
        "scenario": scenario_name,
        "old_version": old_version,
        "new_version": new_version,
        "records_checked": total_records,
        "records_readable_by_old": ok_records,
        "records_unreadable_by_old": total_records - ok_records,
        "verdict": verdict,
        "verdict_label": VERDICT_LABELS[verdict],
        "steps": steps,
        "incompatibility_list": aggregate_issues(issues),
        "sample_records_old_can_read": sample_ok,
        "sample_records_old_cannot_read": sample_bad,
        "benchmark": ({"elapsed_sec": round(elapsed_sec, 3),
                       "peak_memory_bytes": peak_mem_bytes}
                      if elapsed_sec is not None else None),
    }


def write_json(report, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)


def render_text(report):
    lines = []
    lines.append("=" * 72)
    lines.append("场景: %s   (旧版本 %s <- 新版本 %s)" % (
        report["scenario"], report["old_version"], report["new_version"]))
    lines.append("校验记录数: %d   旧版本可读: %d   不可读: %d" % (
        report["records_checked"], report["records_readable_by_old"],
        report["records_unreadable_by_old"]))
    lines.append("结论: %s (%s)" % (report["verdict"], report["verdict_label"]))
    lines.append("处理步骤:")
    for s in report["steps"]:
        lines.append("  " + s)
    if report["incompatibility_list"]:
        lines.append("不兼容清单:")
        for item in report["incompatibility_list"]:
            lines.append("  - [%s] 字段 %s | %s | 出现 %d 次 | 可转换: %s" % (
                item["surface"], item["field"], item["kind"],
                item["count"], "是" if item["convertible"] else "否"))
            lines.append("      期望: %s  实际样例: %s  记录样例: %s" % (
                item["expected"], item["actual_examples"],
                item["sample_record_ids"]))
            if item["conversion"]:
                lines.append("      处理: " + item["conversion"])
    else:
        lines.append("不兼容清单: 无")
    if report["sample_records_old_can_read"]:
        lines.append("旧版本可正确读取的数据样例:")
        for r in report["sample_records_old_can_read"]:
            lines.append("  " + json.dumps(r, ensure_ascii=False, sort_keys=True))
    if report["sample_records_old_cannot_read"]:
        lines.append("旧版本无法读取的数据样例:")
        for r in report["sample_records_old_cannot_read"]:
            lines.append("  " + json.dumps(r, ensure_ascii=False, sort_keys=True))
    if report.get("benchmark"):
        b = report["benchmark"]
        lines.append("性能: 耗时 %.3fs, 内存峰值 %.1f MB" % (
            b["elapsed_sec"], b["peak_memory_bytes"] / 1048576.0))
    return "\n".join(lines)

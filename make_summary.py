#!/usr/bin/env python3
"""由 output/report.json 与 output/benchmark.json 生成 output/SUMMARY.md。"""
import json

KIND_LABELS = {
    "key_field_mismatch": "关键字段不一致",
    "key_field_unparsable": "关键字段无法解析",
    "resource_missing": "资源缺失",
    "resource_added": "资源新增",
    "allowed_field_mismatch": "允许不同字段变化",
    "unknown_field": "口径外未知字段",
    "missing_key_field": "关键字段缺失",
    "duplicate_id": "id 重复",
    "equivalent_notation": "等价写法",
    "resource_order": "资源顺序",
}
VERDICT_LABELS = {"violation": "违规", "compliant": "合规", "needs_review": "待确认"}


def main():
    with open("output/report.json", encoding="utf-8") as handle:
        report = json.load(handle)
    with open("output/benchmark.json", encoding="utf-8") as handle:
        bench = json.load(handle)

    summary = report["summary"]
    counts = summary["diff_counts"]
    lines = [
        "# 资源清单比对结果摘要（样本数据）",
        "",
        f"- 总体判定：**{VERDICT_LABELS[summary['overall_verdict']]}**",
        f"- 基线资源数：{summary['resources_baseline']}；"
        f"候选资源数：{summary['resources_candidate']}；"
        f"匹配：{summary['matched_resources']}",
        f"- 差异数：违规 {counts['violation']}、合规 {counts['compliant']}、"
        f"待确认 {counts['needs_review']}",
        f"- 关键字段缺失：{summary['key_field_missing_count']}（单独报出）",
        f"- 等价写法/顺序信息项：{summary['equivalent_notation_count']}（不计为差异）",
        "",
        "## 差异归因明细",
        "",
        "| 资源 | 归因 | 字段 | 判定 | 基线 | 候选 |",
        "|---|---|---|---|---|---|",
    ]
    for diff in report["diffs"]:
        lines.append(
            "| {rid} | {kind} | {field} | {verdict} | {base} | {cand} |".format(
                rid=diff.get("resource_id", "-"),
                kind=KIND_LABELS.get(diff["kind"], diff["kind"]),
                field=diff.get("field", "-"),
                verdict=VERDICT_LABELS[diff["verdict"]],
                base=json.dumps(diff["baseline"], ensure_ascii=False),
                cand=json.dumps(diff["candidate"], ensure_ascii=False),
            )
        )
    lines += ["", "## 关键字段缺失（单独报告）", ""]
    for item in report["key_field_missing"]:
        lines.append(
            f"- side={item.get('side')} resource={item.get('resource_id')} "
            f"field={item.get('field')}：{item['reason']}"
        )
    lines += ["", "## 等价写法 / 顺序（合规，不算差异）", ""]
    for item in report["equivalent_notations"]:
        field = item.get("field", "-")
        lines.append(
            "- {kind} {field}: {base} -> {cand}".format(
                kind=item["kind"], field=field,
                base=json.dumps(item["baseline"], ensure_ascii=False),
                cand=json.dumps(item["candidate"], ensure_ascii=False),
            )
        )
    lines += [
        "",
        "# 1 万份清单基准结果",
        "",
        f"- 清单对数：{bench['manifest_pairs']}；"
        f"每份 {bench['resources_per_manifest']} 个资源；"
        f"共比对资源 {bench['total_resources_compared']} 个",
        f"- 总耗时：{bench['elapsed_seconds']} s"
        f"（单份均值 {bench['avg_seconds_per_pair'] * 1000} ms，"
        f"{bench['throughput_pairs_per_second']} 对/秒）",
        f"- 开启 tracemalloc 时耗时：{bench['elapsed_seconds_with_tracemalloc']} s"
        "（仅用于测内存，有插桩开销）",
        f"- Python 对象内存峰值（tracemalloc）：{bench['peak_python_mib_tracemalloc']} MiB"
        "（流式逐对比对）",
        f"- 进程 RSS 峰值（getrusage）：{bench['rss_peak_mib_getrusage']} MiB",
        f"- 聚合差异：{json.dumps(bench['aggregated_diffs'])}；"
        f"关键字段缺失 {bench['key_field_missing_total']}；"
        f"等价写法 {bench['equivalent_notation_total']}",
        "",
    ]
    with open("output/SUMMARY.md", "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    print("已生成 output/SUMMARY.md")


if __name__ == "__main__":
    main()

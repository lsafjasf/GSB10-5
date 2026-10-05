"""把分析结果渲染为 Markdown / JSON 报告。"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .analysis import ScenarioReport
from .bench import BenchmarkResult


def scenario_to_dict(report: ScenarioReport) -> Dict[str, Any]:
    return {
        "name": report.name,
        "base": report.base_name,
        "candidate": report.cand_name,
        "total_layers": report.total_layers,
        "reused_layers": report.reused_layers,
        "rebuilt_layers": report.rebuilt_layers,
        "layer_reuse_rate": round(report.layer_reuse_rate, 6),
        "total_bytes": report.total_bytes,
        "reused_bytes": report.reused_bytes,
        "byte_reuse_rate": round(report.byte_reuse_rate, 6),
        "first_divergence_index": report.first_divergence_index,
        "instruction_overlap": round(report.instruction_overlap, 6),
        "verified": report.verified,
        "verification": {
            "final_image_match": report.verification.final_image_match,
            "layers_checked": len(report.verification.checks),
            "all_layers_match": all(c.ok for c in report.verification.checks),
        } if report.verification else None,
        "verdicts": [
            {"index": v.index, "instruction": v.instruction,
             "verdict": v.verdict, "reason": v.reason,
             "cache_key": v.cache_key}
            for v in report.verdicts
        ],
    }


def render_markdown(scenarios: List[ScenarioReport],
                    bench: BenchmarkResult,
                    extra_notes: List[str]) -> str:
    lines: List[str] = []
    lines.append("# 分层缓存分析报告")
    lines.append("")
    lines.append("## 复用率数据")
    lines.append("")
    lines.append("| 场景 | 总层数 | 复用 | 重建 | 层复用率 | 字节复用率 | 首个分叉层 | 指令重合度 | 校验 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for s in scenarios:
        lines.append(
            f"| {s.name} | {s.total_layers} | {s.reused_layers} | {s.rebuilt_layers} "
            f"| {s.layer_reuse_rate:.1%} | {s.byte_reuse_rate:.1%} "
            f"| {s.first_divergence_index if s.first_divergence_index is not None else '-'} "
            f"| {s.instruction_overlap:.1%} | {'通过' if s.verified else '失败'} |")
    lines.append("")
    lines.append("## 逐层判定")
    for s in scenarios:
        lines.append("")
        lines.append(f"### {s.name}（{s.base_name} → {s.cand_name}）")
        lines.append("")
        lines.append("| 层 | 指令 | 判定 | 原因 |")
        lines.append("| --- | --- | --- | --- |")
        for v in s.verdicts:
            lines.append(f"| {v.index} | `{v.instruction}` | {v.verdict} | {v.reason} |")
    lines.append("")
    lines.append("## 复用校验数据")
    lines.append("")
    lines.append("校验方法：候选脚本分别在有缓存与无缓存（逐层重建）下构建，"
                 "逐层比对层内容摘要与镜像摘要。")
    lines.append("")
    lines.append("| 场景 | 校验层数 | 复用层数 | 层内容一致 | 最终镜像一致 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for s in scenarios:
        v = s.verification
        if v is None:
            continue
        lines.append(
            f"| {s.name} | {len(v.checks)} | {v.reused_layers} "
            f"| {'全部一致' if all(c.ok for c in v.checks) else '存在不一致'} "
            f"| {'是' if v.final_image_match else '否'} |")
    lines.append("")
    lines.append("## 耗时与内存（1000 组构建脚本）")
    lines.append("")
    lines.append(f"- 脚本数：{bench.script_count}（共 {bench.layer_count} 层）")
    lines.append(f"- 总耗时：{bench.wall_seconds:.3f} s（平均每组 {bench.per_script_ms:.3f} ms）")
    lines.append(f"- Python 分配峰值（tracemalloc）：{bench.peak_traced_bytes / 1024 / 1024:.2f} MiB")
    lines.append(f"- 进程峰值 RSS（ru_maxrss）：{bench.peak_rss_kb / 1024:.1f} MiB")
    lines.append(f"- 缓存条目数（去重后）：{bench.cache_entries}")
    lines.append(f"- 层缓存命中率：{bench.hit_rate:.1%}（{bench.hits} 命中 / {bench.misses} 未命中）")
    lines.append("")
    if extra_notes:
        lines.append("## 备注")
        lines.append("")
        for note in extra_notes:
            lines.append(f"- {note}")
        lines.append("")
    return "\n".join(lines)


def render_json(scenarios: List[ScenarioReport],
                bench: BenchmarkResult) -> str:
    return json.dumps({
        "scenarios": [scenario_to_dict(s) for s in scenarios],
        "benchmark": bench.as_dict(),
    }, ensure_ascii=False, indent=2)

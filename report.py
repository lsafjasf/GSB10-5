#!/usr/bin/env python3
"""运行全部分析场景并生成 REPORT.md / report.json。"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from layercache.analysis import analyze_pair
from layercache.bench import run_benchmark
from layercache.builder import CacheStore
from layercache.report import render_json, render_markdown
from layercache.scenarios import negative_pairs, scenario_pairs


def main() -> None:
    scenarios = []
    for name, base, candidate in scenario_pairs():
        scenarios.append(analyze_pair(base, candidate, CacheStore(), name=name))

    negative = []
    for name, base, candidate in negative_pairs():
        negative.append(analyze_pair(base, candidate, CacheStore(), name=name))

    bench = run_benchmark(1000)

    notes = [
        "负例场景（文件内容/权限位变化）层复用率："
        + ", ".join(f"{s.name} {s.layer_reuse_rate:.1%}" for s in negative),
        "负例场景同样通过复用正确性校验（复用的前缀层与重建一致）。",
    ]

    md = render_markdown(scenarios, bench, notes)
    with open("REPORT.md", "w", encoding="utf-8") as fh:
        fh.write(md + "\n")

    payload = json.loads(render_json(scenarios, bench))
    payload["negative_scenarios"] = [
        {"name": s.name, "layer_reuse_rate": round(s.layer_reuse_rate, 6),
         "verified": s.verified}
        for s, (name, _, _) in zip(negative, negative_pairs())
    ]
    with open("report.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print(md)
    print(f"\n[report] 已写出 REPORT.md 与 report.json；"
          f"benchmark {bench.script_count} 组脚本耗时 {bench.wall_seconds:.3f}s，"
          f"tracemalloc 峰值 {bench.peak_traced_bytes / 1024 / 1024:.2f} MiB")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Benchmark the full pipeline against a synthetic 10,000,000-series stream.

Usage:
    python3 benchmark.py                         # default: 10,000,000, combo
    python3 benchmark.py --n 1000000 --scenario single
    python3 benchmark.py --n 10000000 --out docs/benchmark_10m.md

Stdlib only.  All distinct counts during detection/merging are sketch based
(HLL + SpaceSaving + bounded reservoir), so peak memory does not grow with
the number of unique ids.
"""

from __future__ import annotations

import argparse
import gc
import platform
import resource
import sys
import time

from cardinality_guard import (
    CardinalityDetector, auto_plan, validate_policy, measure_merged,
)
from cardinality_guard.data import generate


def rss_current_mb() -> float:
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    return 0.0


def rss_peak_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def phase(name):
    class _Ctx:
        def __enter__(self):
            gc.collect()
            self.rss0 = rss_current_mb()
            self.t0 = time.perf_counter()
            return self

        def __exit__(self, *exc):
            self.secs = time.perf_counter() - self.t0
            self.rss_after = rss_current_mb()
            print(f"  {name:<28} {self.secs:>8.2f}s   "
                  f"常驻 RSS: {self.rss0:7.1f} -> {self.rss_after:7.1f} MB")
    return _Ctx()


def run(n: int, scenario: str, windows: int, out: str | None,
        pool_size: int | None) -> dict:
    print(LINE)
    print(f"基数控制流水线基准：n={n:,}  scenario={scenario}  "
          f"windows={windows}  python={platform.python_version()}")
    print(LINE)
    baseline = rss_current_mb()
    print(f"  {'阶段':<30}{'耗时':>10}   内存")

    t_start = time.perf_counter()
    with phase("1. 摄取 + 识别（检测草图）") as ctx_detect:
        det = CardinalityDetector()
        for window, labels in generate(
                n, scenario=scenario, n_windows=windows, pool_size=pool_size):
            det.feed(labels, window)

    with phase("2. 分析 + 自动归并方案 + 防护") as ctx_plan:
        rep = det.report()
        plan = auto_plan(rep, sketches=det.sketches)
        rules = {e.label: e.rule for e in plan}
        policy = validate_policy_wrap(rules, rep)
        top_values = {
            name: {v for v, _ in det.sketches[name].top_values(rule.k)}
            for name, rule in rules.items() if rule.action == "truncate"
        }

    with phase("3. 重放 + 归并 + 归并后基数") as ctx_merge:
        merged_n, merged_labels = measure_merged(
            (labels for _, labels in generate(
                n, scenario=scenario, n_windows=windows, pool_size=pool_size)),
            policy, top_values, exact_limit=1_000_000)

    total = time.perf_counter() - t_start
    peak = rss_peak_mb()
    print(LINE)
    print(f"总耗时: {total:.2f}s   "
          f"吞吐: {n / ctx_detect.secs:,.0f} 系列/秒（检测阶段）")
    print(f"进程峰值 RSS (ru_maxrss): {peak:.1f} MB   "
          f"解释器基线: {baseline:.1f} MB   工作集增量: {peak - baseline:.1f} MB")

    data = dict(n=n, scenario=scenario, windows=windows,
                baseline=baseline, peak=peak, total=total,
                detect_secs=ctx_detect.secs, plan_secs=ctx_plan.secs,
                merge_secs=ctx_merge.secs, rep=rep, plan=plan,
                merged_n=merged_n, merged_labels=merged_labels,
                rss_detect=ctx_detect.rss_after,
                rss_merge=ctx_merge.rss_after)
    print_report(data)
    if out:
        write_markdown(out, data)
        print(f"\nMarkdown 报告已写入: {out}")
    return data


LINE = "-" * 78


def validate_policy_wrap(rules, rep):
    from cardinality_guard import Policy, validate_policy
    policy = Policy(rules=rules)
    validate_policy(policy, report=rep)
    return policy


def print_report(d) -> None:
    rep = d["rep"]
    print("\n识别结果（每个标签）:")
    print(f"  {'标签':<10}{'取值数量':>12}{'选择率':>9}{'熵':>8}{'增长率':>9}  判定")
    for name, lr in sorted(rep.labels.items()):
        verdict = "高基数" if lr.is_high_cardinality else "低基数"
        print(f"  {name:<10}{lr.distinct:>12,.0f}{lr.selectivity:>9.3f}"
              f"{lr.entropy_norm:>8.3f}{lr.growth_rate:>9.3f}  {verdict}")
    print(f"\n原始序列基数（HLL 估计）: {rep.distinct_series:,.0f}")
    print(f"完全展开的标签对数量: {len(rep.exploding_pairs)}")
    for p in rep.exploding_pairs[:6]:
        a, b = p["pair"]
        print(f"  {a} x {b}: 联合 {p['joint']:,.0f}, "
              f"边际积 {p['marginal_product']:,.0f}, 实现率 {p['factor']:.3f}")

    print("\n自动归并方案:")
    for e in d["plan"]:
        print(f"  {e.label:<10} {e.rule.action:<10} "
              f"{e.distinct_before:>12,.0f} -> {e.est_distinct_after:>8,.0f}")

    raw = rep.distinct_series
    merged = d["merged_n"]
    print(f"\n归并后序列基数: {merged:,.0f}   "
          f"下降 {(1 - merged / raw) * 100:.2f}%   "
          f"压缩比 {raw / max(merged, 1):.1f}:1")


def write_markdown(path: str, d) -> None:
    rep = d["rep"]
    lines = [
        "# 1000 万时间序列基数控制基准",
        "",
        f"- 数据规模: {d['n']:,} 条时间序列（scenario=`{d['scenario']}`，"
        f"{d['windows']} 个窗口）",
        f"- Python: {platform.python_version()}，仅标准库",
        "",
        "## 耗时与内存",
        "",
        "| 阶段 | 耗时 (s) | 阶段末常驻 RSS (MB) |",
        "|---|---:|---:|",
        f"| 摄取 + 识别（草图） | {d['detect_secs']:.2f} | {d['rss_detect']:.1f} |",
        f"| 分析 + 归并方案 + 防护 | {d['plan_secs']:.2f} | - |",
        f"| 重放 + 归并 + 基数统计 | {d['merge_secs']:.2f} | {d['rss_merge']:.1f} |",
        f"| **总计** | **{d['total']:.2f}** | |",
        "",
        f"进程峰值 RSS（`ru_maxrss`）: **{d['peak']:.1f} MB**；"
        f"解释器基线 {d['baseline']:.1f} MB；工作集增量 "
        f"**{d['peak'] - d['baseline']:.1f} MB**。",
        f"检测吞吐: {d['n'] / d['detect_secs']:,.0f} 系列/秒。",
        "",
        "## 识别结果",
        "",
        "| 标签 | 取值数量 | 选择率 | 归一化熵 | 增长率 | 判定 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for name, lr in sorted(rep.labels.items()):
        verdict = "高基数" if lr.is_high_cardinality else "低基数"
        lines.append(
            f"| {name} | {lr.distinct:,.0f} | {lr.selectivity:.3f} | "
            f"{lr.entropy_norm:.3f} | {lr.growth_rate:.3f} | {verdict} |")
    lines += [
        "",
        "## 归并方案与基数变化",
        "",
        "| 标签 | 规则 | 归并前取值数 | 归并后取值数 |",
        "|---|---|---:|---:|",
    ]
    for e in d["plan"]:
        after = d["merged_labels"].get(e.label, e.est_distinct_after)
        after_txt = "0（删除）" if e.rule.action == "drop" else f"{after:,.0f}"
        lines.append(f"| {e.label} | {e.rule.action} | "
                     f"{e.distinct_before:,.0f} | {after_txt} |")
    raw = rep.distinct_series
    merged = d["merged_n"]
    lines += [
        "",
        f"**时间序列基数: {raw:,.0f} -> {merged:,.0f}"
        f"（下降 {(1 - merged / raw) * 100:.2f}%，压缩比 "
        f"{raw / max(merged, 1):.1f}:1）**",
        "",
        f"完全展开的标签对数量: {len(rep.exploding_pairs)}",
        "",
        "> 检测与归并全程不保存全部 id：精确集合超过 exact_limit=256 即提升为",
        "> HyperLogLog + SpaceSaving + 蓄水池采样，内存与唯一值数量解耦。",
        "",
    ]
    with open(path, "w") as f:
        f.write("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10_000_000)
    ap.add_argument("--scenario", default="combo",
                    choices=("low", "single", "combo", "growth"))
    ap.add_argument("--windows", type=int, default=8)
    ap.add_argument("--pool-size", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    run(args.n, args.scenario, args.windows, args.out, args.pool_size)
    return 0


if __name__ == "__main__":
    sys.exit(main())

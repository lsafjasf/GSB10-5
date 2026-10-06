#!/usr/bin/env python3
"""变更风险评分：分析入口。

子命令：
  demo        展示 4 类典型变更的贡献项分解（含临界区间情形）
  validate    评分与回滚结局的相关性验证（点二列相关 / AUC / 分档失败率）
  sensitivity 权重扰动敏感度分析（全局抖动 / 单因素扫描 / Dirichlet 重采样）
  benchmark   10 万次变更评分耗时与内存峰值
  all         依次运行以上全部
"""
from __future__ import annotations

import argparse
import gc
import resource
import sys
import time
import tracemalloc

from risk_scoring.dataset import generate_labeled, iter_unlabeled
from risk_scoring.model import (
    DEFAULT_WEIGHTS,
    Change,
    score_change,
    score_many,
)
from risk_scoring.sensitivity import (
    dirichlet_stability,
    global_jitter,
    one_factor_sweep,
)
from risk_scoring.validation import validate


# ---------------------------------------------------------------------------
# demo：贡献项分解
# ---------------------------------------------------------------------------
DEMO_CASES = [
    (
        "单因素极高（核心故障区的小改动，其余全优）",
        Change(churn_lines=20, files_touched=1, modules_touched=1,
               criticality=0.98, test_coverage=0.95, author_commits=2000,
               ownership_ratio=0.95, hour_local=11),
    ),
    (
        "多项小风险叠加（每项都不高，但项项都有）",
        Change(churn_lines=250, files_touched=8, modules_touched=3,
               criticality=0.45, test_coverage=0.55, author_commits=40,
               ownership_ratio=0.4, hour_local=20),
    ),
    (
        "全部因素都很低（典型安全变更）",
        Change(churn_lines=5, files_touched=1, modules_touched=1,
               criticality=0.05, test_coverage=0.98, author_commits=5000,
               ownership_ratio=0.99, hour_local=10),
    ),
    (
        "临界区间（分数落在 30 分判定边界附近）",
        Change(churn_lines=120, files_touched=4, modules_touched=2,
               criticality=0.30, test_coverage=0.65, author_commits=80,
               ownership_ratio=0.55, hour_local=15),
    ),
]


def cmd_demo(_args) -> None:
    print("=" * 78)
    print("贡献项分解（分数 0~100；占比 = 该项贡献分 / 总分）")
    print("=" * 78)
    for title, change in DEMO_CASES:
        result = score_change(change)
        print(f"\n■ {title}")
        print(f"  总分 {result.score:5.1f}  等级 {result.band}"
              f"{'  [临界区间]' if result.is_boundary else ''}")
        shares = result.shares()
        print(f"  {'因素':<28}{'权重':>7}{'风险度':>8}{'贡献分':>8}{'占比':>8}")
        for c in sorted(result.contributions, key=lambda x: -x.points):
            print(f"  {c.description:<28}{c.weight:>7.2f}"
                  f"{c.factor_value:>8.2f}{c.points:>8.1f}"
                  f"{shares[c.factor] * 100:>7.1f}%")


# ---------------------------------------------------------------------------
# validate：与实际后果（回滚）的相关性
# ---------------------------------------------------------------------------
def cmd_validate(args) -> None:
    changes, outcomes = generate_labeled(args.n, seed=args.seed)
    scores = [b.score for b in score_many(changes)]
    report = validate(scores, outcomes)

    print("=" * 78)
    print(f"相关性验证（n={report['n']}，实际回滚率 {report['base_failure_rate']:.1%}）")
    print("=" * 78)
    print(f"点二列相关 r_pb      : {report['point_biserial']:+.3f}"
          "   （>0.3 视为中等以上相关）")
    print(f"AUC                  : {report['auc']:.3f}"
          "   （0.5=随机，>0.8 视为良好区分度）")
    print("\n分档实际回滚率（应随分数档位单调上升）：")
    for row in report["band_failure_rates"]:
        print(f"  {row['band']:<7} n={row['n']:<6} 回滚率 {row['failure_rate']:.1%}")
    for key in ("confusion_at_medium", "confusion_at_high"):
        c = report[key]
        print(f"\n阈值 {c['threshold']:.0f} 分判定'会回滚'："
              f"precision={c['precision']:.3f} recall={c['recall']:.3f} "
              f"F1={c['f1']:.3f}")
        print(f"  混淆矩阵: TP={c['tp']} FP={c['fp']} TN={c['tn']} FN={c['fn']}")


# ---------------------------------------------------------------------------
# sensitivity：权重扰动
# ---------------------------------------------------------------------------
def cmd_sensitivity(args) -> None:
    changes, _ = generate_labeled(args.n, seed=args.seed + 1)

    print("=" * 78)
    print(f"敏感度分析（n={args.n}，基准权重见 docs/FACTORS_AND_WEIGHTS.md）")
    print("=" * 78)

    g = global_jitter(changes, delta=0.2, trials=args.trials)
    print(f"\n[1] 全局抖动 ±20% × {g['trials']} 次：")
    print(f"    Spearman rho  min={g['spearman_min']:.3f} mean={g['spearman_mean']:.3f}"
          "   （>=0.9 视为排序稳健）")
    print(f"    等级判定一致率 min={g['agreement_min']:.1%} mean={g['agreement_mean']:.1%}"
          "   （>=75% 视为判定稳健）")

    print("\n[2] 单因素权重 ±20% 扫描（按影响从大到小）：")
    print(f"    {'因素':<16}{'基准权重':>9}{'最差rho':>10}{'最差一致率':>12}")
    for row in one_factor_sweep(changes, delta=0.2):
        print(f"    {row['factor']:<16}{row['base_weight']:>9.2f}"
              f"{row['worst_spearman']:>10.3f}{row['worst_agreement']:>11.1%}")

    d = dirichlet_stability(changes, concentration=200.0, trials=args.trials)
    print(f"\n[3] Dirichlet 重采样（concentration={d['concentration']:.0f}，"
          f"约 ±7% 权重噪声）× {d['trials']} 次：")
    print(f"    等级判定保持率 {d['band_keep_rate']:.1%}")

    robust = g["spearman_min"] >= 0.9 and g["agreement_mean"] >= 0.75
    print(f"\n结论：{'稳健' if robust else '不稳健'} —— "
          "权重 ±20% 扰动下排序与判定基本不变，分数不依赖系数的精确取值。")


# ---------------------------------------------------------------------------
# benchmark：10 万次变更
# ---------------------------------------------------------------------------
def cmd_benchmark(args) -> None:
    n = args.n
    gc.collect()

    # 阶段 0：准备输入（模拟从 VCS/CI 拉取的 10 万条变更）
    tracemalloc.start()
    t0 = time.perf_counter()
    changes = list(iter_unlabeled(n))
    gen_elapsed = time.perf_counter() - t0
    _, peak_inputs = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # 模式 A：流式评分（结果逐个消费，不驻留）
    tracemalloc.start()
    t0 = time.perf_counter()
    total = 0.0
    count = 0
    for breakdown in score_many(changes):
        total += breakdown.score
        count += 1
    elapsed = time.perf_counter() - t0
    _, peak_traced = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024

    # 模式 B：全部结果物化（返回 10 万个 ScoreBreakdown）
    gc.collect()
    tracemalloc.start()
    t1 = time.perf_counter()
    results = list(score_many(changes))
    materialized_elapsed = time.perf_counter() - t1
    _, peak_materialized = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print("=" * 78)
    print(f"基准测试：{count} 次变更评分（流式，不驻留结果）")
    print("=" * 78)
    print(f"输入准备（生成 {n} 条变更） : {gen_elapsed:.2f} s"
          f"，输入对象内存 {peak_inputs / 1024 / 1024:.1f} MiB")
    print()
    print("模式 A：流式评分（推荐的批处理方式）")
    print(f"  评分耗时        : {elapsed:.2f} s")
    print(f"  单次评分        : {elapsed / count * 1e6:.1f} µs")
    print(f"  吞吐            : {count / elapsed:,.0f} 次/秒")
    print(f"  评分增量内存    : {peak_traced / 1024:,.0f} KiB（tracemalloc 峰值）")
    print()
    print("模式 B：结果全部物化（10 万个分解结果驻留内存）")
    print(f"  评分耗时        : {materialized_elapsed:.2f} s")
    print(f"  吞吐            : {count / materialized_elapsed:,.0f} 次/秒")
    print(f"  结果对象增量内存: {peak_materialized / 1024 / 1024:.1f} MiB（tracemalloc 峰值）")
    print(f"进程 RSS 峰值        : {peak_rss / 1024 / 1024:.1f} MiB")
    print(f"校验和（防优化）: {total:.2f}")
    del results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, fn, help_text in [
        ("demo", cmd_demo, "贡献项分解示例"),
        ("validate", cmd_validate, "与回滚结局的相关性验证"),
        ("sensitivity", cmd_sensitivity, "权重敏感度分析"),
        ("benchmark", cmd_benchmark, "10 万次评分性能基准"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--n", type=int,
                       default=100000 if name == "benchmark"
                       else (10000 if name == "sensitivity" else 20000))
        p.add_argument("--seed", type=int, default=2024)
        p.add_argument("--trials", type=int, default=200)
        p.set_defaults(fn=fn)
    p = sub.add_parser("all", help="运行全部分析")
    p.add_argument("--n", type=int, default=20000)
    p.add_argument("--seed", type=int, default=2024)
    p.add_argument("--trials", type=int, default=200)
    p.set_defaults(fn=None)

    args = parser.parse_args(argv)
    if args.cmd == "all":
        cmd_demo(args)
        print()
        cmd_validate(args)
        print()
        cmd_sensitivity(args)
        print()
        bench_args = argparse.Namespace(**{**vars(args), "n": 100000})
        cmd_benchmark(bench_args)
    else:
        args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())

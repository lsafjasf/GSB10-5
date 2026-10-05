"""对比加权还原分布与全量精确分布：分位数、均值、尾部占比、置信区间。

用法: python3 report.py            （自动跑全部场景，数据缺失时先生成）
"""
import json
import os
import time

from sampler import (MIN_ESS, MIN_TAIL_ESS, DurationSampler, UniformSampler,
                     WeightedSample)
from datagen import QS, generate

MAIN_FILE = "queries_10m.csv"
EXTREME_FILE = "queries_extreme.csv"
QS_LABEL = {0.5: "p50", 0.9: "p90", 0.99: "p99", 0.999: "p99.9"}


def fmt(x):
    if x != x:  # nan
        return "NaN"
    return f"{x:,.6g}"


def run_sampling(path, sampler):
    sample = WeightedSample()
    t0 = time.perf_counter()
    n = 0
    with open(path) as f:
        for line in f:
            sampler.maybe_add(float(line), sample)
            n += 1
    return sample, n, time.perf_counter() - t0


def show(name, path, sampler, exact):
    sample, n, elapsed = run_sampling(path, sampler)
    ess = sample.effective_sample_size
    print(f"\n### {name}")
    print(f"- 全量记录 {n:,} 条，入样 {len(sample):,} 条"
          f"（实际采样率 {len(sample)/n:.4%}），ESS={ess:,.0f}，"
          f"采样+还原耗时 {elapsed:.2f}s")
    print(f"- 还原总记录数 {sample.total_weight:,.0f}（真值 {n:,}），"
          f"还原总耗时 {sample.total_estimate():,.4g}ms")
    if not sample.reliable():
        print(f"- **样本量不足（ESS={ess:.1f} < {MIN_ESS:.0f}）：以下点估计均不可靠，"
              f"只能参考置信区间**")
    print()
    print("| 指标 | 全量精确值 | 加权还原 | 相对误差 | 95% 置信区间 | 可靠 |")
    print("|---|---|---|---|---|---|")

    def row(label, ex, est, ci, ok):
        err = abs(est - ex) / ex * 100 if ex else float("nan")
        print(f"| {label} | {fmt(ex)} | {fmt(est)} | {err:.2f}% "
              f"| [{fmt(ci[0])}, {fmt(ci[1])}] | {'是' if ok else '**否**'} |")

    row("均值(ms)", exact["mean"], sample.mean(), sample.mean_ci(), sample.reliable())
    for q in QS:
        row(QS_LABEL[q] + "(ms)", exact["quantiles"][str(q)], sample.quantile(q),
            sample.quantile_ci(q), sample.quantile_reliable(q))
    ts_ex = exact["tail_share"]["0.99"]
    ts_est = sample.tail_share(0.99)
    print(f"| 最慢1%耗时占比 | {ts_ex:.4%} | {ts_est:.4%} "
          f"| {abs(ts_est-ts_ex)/ts_ex*100:.2f}% | - | "
          f"{'是' if sample.quantile_reliable(0.99) else '**否**'} |")
    smax = max(v for v, _ in sample._vw) if len(sample) else float("nan")
    print(f"\n- max：全量 {fmt(exact['max'])}ms，样本中观测到的最大值 {fmt(smax)}ms。"
          f"（max 无法无偏还原，长尾应看 p99.9 等高分位数）")
    return sample


def main():
    if not os.path.exists(MAIN_FILE):
        print("生成主数据集（1000 万条，含长尾）...")
        generate(MAIN_FILE, 10_000_000, seed=42)
    if not os.path.exists(EXTREME_FILE):
        print("生成极端长尾数据集（100 万条，Pareto alpha=1.2）...")
        generate(EXTREME_FILE, 1_000_000, seed=7, extreme=True)
    exact_main = json.load(open(MAIN_FILE + ".stats.json"))
    exact_ext = json.load(open(EXTREME_FILE + ".stats.json"))

    print("全量精确统计（主数据集 1000 万条）："
          f"mean={exact_main['mean']:.3f}ms "
          + " ".join(f"{QS_LABEL[q]}={exact_main['quantiles'][str(q)]:,.1f}ms" for q in QS)
          + f" max={exact_main['max']:,.1f}ms")

    show("场景1：均匀采样 1%（1000 万条）", MAIN_FILE,
         UniformSampler(0.01, seed=1), exact_main)
    show("场景2：按耗时采样（基准 1%，阈值 100ms，1000 万条）", MAIN_FILE,
         DurationSampler(0.01, threshold=100.0, seed=1), exact_main)
    show("场景3：样本量很小（均匀采样 0.002%，约 200 条样本）", MAIN_FILE,
         UniformSampler(2e-5, seed=1), exact_main)
    show("场景4a：极端长尾 + 均匀采样 1%（100 万条）", EXTREME_FILE,
         UniformSampler(0.01, seed=1), exact_ext)
    show("场景4b：极端长尾 + 按耗时采样（基准 1%，阈值 100ms）", EXTREME_FILE,
         DurationSampler(0.01, threshold=100.0, seed=1), exact_ext)


if __name__ == "__main__":
    main()

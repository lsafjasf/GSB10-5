"""性能基准：1000 万条记录、1% 均匀采样，报告处理耗时与内存峰值。

用法: python3 bench.py [数据文件] [采样率]
"""
import resource
import sys
import time

from sampler import UniformSampler, WeightedSample


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "queries_10m.csv"
    rate = float(sys.argv[2]) if len(sys.argv) > 2 else 0.01
    sample = WeightedSample()
    sampler = UniformSampler(rate, seed=7)
    t0 = time.perf_counter()
    n = 0
    with open(path) as f:
        for line in f:
            sampler.maybe_add(float(line), sample)
            n += 1
    # 还原阶段：排序 + 分位数/均值/CI
    t1 = time.perf_counter()
    qs = {q: sample.quantile(q) for q in (0.5, 0.9, 0.99, 0.999)}
    mean = sample.mean()
    ci = sample.mean_ci()
    qci = sample.quantile_ci(0.999)
    t2 = time.perf_counter()
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    print(f"记录数        : {n:,}")
    print(f"采样率        : {rate:.2%} -> 入样 {len(sample):,} 条")
    print(f"采样(流式)耗时: {t1 - t0:.2f}s")
    print(f"还原计算耗时  : {t2 - t1:.2f}s")
    print(f"总耗时        : {t2 - t0:.2f}s")
    print(f"内存峰值      : {rss_mb:.1f} MB (ru_maxrss)")
    print(f"还原结果      : mean={mean:.3f}ms p50={qs[0.5]:.3f} "
          f"p90={qs[0.9]:.3f} p99={qs[0.99]:.3f} p99.9={qs[0.999]:.3f}")


if __name__ == "__main__":
    main()

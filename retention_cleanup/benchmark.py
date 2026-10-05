"""100 万条记录的清理基准：耗时（perf_counter）与内存峰值（tracemalloc + RSS）。

用法：python -m retention_cleanup.benchmark [--records 1000000] [--seed 42]
"""

import argparse
import random
import resource
import time
import tracemalloc

from .core import Policy, Record, evaluate

DAY = 86400.0


def generate(n: int, now: float, seed: int):
    """生成 n 条记录：约 60% 到期、5% 带暂停标记、10% 被引用、1% 时间异常。"""
    rng = random.Random(seed)
    records = []
    referenced_ids = []
    for i in range(n):
        roll = rng.random()
        if roll < 0.60:
            created = now - rng.uniform(31, 400) * DAY
        else:
            created = now - rng.uniform(0, 29) * DAY
        accessed = created + rng.uniform(0, min(now - created, 5 * DAY))
        hold = rng.random() < 0.05
        anomaly = rng.random() < 0.01
        if anomaly:
            accessed = now + rng.uniform(3600, 10 * DAY)  # 远超容忍带的未来
        refs = ()
        if i > 0 and rng.random() < 0.10:
            refs = (rng.randrange(i),)
            referenced_ids.append(refs[0])
        records.append(Record(i, created, accessed, refs, hold))
    return records


def run_benchmark(n: int, seed: int) -> dict:
    now = 1_000_000_000.0
    policy = Policy(retention_seconds=30 * DAY, clock_skew_tolerance_seconds=300.0)

    records = generate(n, now, seed)
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    tracemalloc.start()
    t0 = time.perf_counter()
    report = evaluate(records, now, policy)
    elapsed = time.perf_counter() - t0
    _, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    result = {
        "records": n,
        "evaluate_seconds": round(elapsed, 3),
        "tracemalloc_peak_mb": round(py_peak / 1024 / 1024, 1),
        "maxrss_mb": round(rss_after / 1024, 1),
        "maxrss_delta_mb": round((rss_after - rss_before) / 1024, 1),
        "summary": report.summary(),
    }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="retention cleanup benchmark")
    parser.add_argument("--records", type=int, default=1_000_000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    result = run_benchmark(args.records, args.seed)
    print(f"记录数:            {result['records']:,}")
    print(f"判定耗时:          {result['evaluate_seconds']} s")
    print(f"tracemalloc 峰值:  {result['tracemalloc_peak_mb']} MB")
    print(f"进程峰值 RSS:      {result['maxrss_mb']} MB "
          f"(基准期间新增 {result['maxrss_delta_mb']} MB)")
    print("判定汇总:")
    for key, value in result["summary"].items():
        print(f"  {key}: {value}")
    return result


if __name__ == "__main__":
    main()

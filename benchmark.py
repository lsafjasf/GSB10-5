"""基准：100 万轮查询的耗时与内存峰值。

运行: python3 benchmark.py
说明: 为测引擎吞吐而非预算拒绝，基准使用足够大的总预算；
      预算拒绝路径的开销由 test_dp_agg.py 覆盖。
"""

import resource
import time
import tracemalloc
import random

from dp_agg import DPAnalytics


def make_records(n=100_000, seed=2026):
    rng = random.Random(seed)
    regions = ["R%02d" % i for i in range(10)]
    depts = ["D%02d" % i for i in range(20)]
    channels = ["C%d" % i for i in range(6)]
    return [{
        "region": rng.choice(regions),
        "dept": rng.choice(depts),
        "channel": rng.choice(channels),
        "spend": rng.uniform(0, 500),
    } for _ in range(n)]


def main():
    n_queries = 1_000_000
    records = make_records()
    dp = DPAnalytics(records, epsilon_total=1e12,
                     contribution_bounds={"spend": 500.0}, seed=99)
    rng = random.Random(7)
    regions = ["R%02d" % i for i in range(10)]
    depts = ["D%02d" % i for i in range(20)]
    channels = ["C%d" % i for i in range(6)]

    tracemalloc.start()
    t0 = time.perf_counter()
    for _ in range(n_queries):
        filters = {"region": rng.choice(regions), "dept": rng.choice(depts)}
        if rng.random() < 0.5:
            filters["channel"] = rng.choice(channels)
        dp.query(filters, agg="count", epsilon=0.1)
    elapsed = time.perf_counter() - t0
    _, peak_traced = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    peak_rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    reused = sum(1 for e in dp.ledger if e.reused)
    print("查询轮数        : %d" % n_queries)
    print("唯一切片数      : %d（其余 %d 轮命中缓存复用）"
          % (len(dp._answer_cache), reused))
    print("账本记录数      : %d" % len(dp.ledger))
    print("总耗时          : %.2f s" % elapsed)
    print("吞吐            : %.0f 轮/秒" % (n_queries / elapsed))
    print("Python 堆峰值   : %.1f MB（tracemalloc）" % (peak_traced / 1e6))
    print("进程 RSS 峰值   : %.1f MB（ru_maxrss）" % (peak_rss_kb / 1024))


if __name__ == "__main__":
    main()

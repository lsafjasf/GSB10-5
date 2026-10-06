"""100 万条记录的清理基准：耗时 + 内存峰值（仅标准库）。

运行：
    python3 benchmark.py                 # 1,000,000 条，测耗时与峰值 RSS
    python3 benchmark.py --tracemalloc   # 额外测 Python 分配峰值（更慢）
    python3 benchmark.py --records 100000
"""

import argparse
import random
import resource
import sys
import time
import tracemalloc

from engine import Record, RetentionCleaner, RetentionPolicy

NOW = 1_759_708_800.0
DAY = 86400.0
POLICY = RetentionPolicy(retention_seconds=90 * DAY, grace_seconds=DAY)


def build_dataset(n, seed=42):
    """确定性地构造 n 条记录：约 70% 空闲到期、15% 活跃、5% 暂停、
    3% 时间异常、7% 老记录；约 12% 记录携带引用（含自引用与互引环）。"""
    rng = random.Random(seed)
    records = []
    active_ids = []
    for i in range(n):
        rid = f"rec-{i:08d}"
        roll = rng.random()
        if roll < 0.70:
            created = NOW - rng.uniform(91, 900) * DAY
            accessed = None
            hold = False
        elif roll < 0.85:
            created = NOW - rng.uniform(0, 89) * DAY
            accessed = None
            hold = False
            active_ids.append(rid)
        elif roll < 0.90:
            created = NOW - rng.uniform(100, 2000) * DAY
            accessed = None
            hold = True
        elif roll < 0.93:
            created = NOW + rng.uniform(2, 400) * DAY  # 未来时间戳 -> 异常
            accessed = None
            hold = False
        else:
            created = NOW - rng.uniform(91, 900) * DAY
            accessed = created + rng.uniform(0, 30) * DAY  # 最近被访问过
            hold = False
        records.append([rid, created, accessed, (), hold])

    # 引用方一律来自活跃服务（不会在本轮被删），符合真实业务；
    # 另外制造少量自引用（记录引用自身，必然幸存）。
    refs_budget = int(n * 0.12)
    slots = [i for i in range(n) if records[i][2] is None and not records[i][4]
             and records[i][1] < NOW - 91 * DAY]
    rng.shuffle(slots)
    for i in slots[:refs_budget]:
        rid = records[i][0]
        if rng.random() < 0.05:
            records[i][3] = (rid,)  # 自引用
        else:
            records[i][3] = (active_ids[rng.randrange(len(active_ids))],)
    return [Record(rid, created, accessed, refs, hold)
            for rid, created, accessed, refs, hold in records]


def peak_rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", type=int, default=1_000_000)
    parser.add_argument("--tracemalloc", action="store_true")
    args = parser.parse_args()

    print(f"records={args.records} policy=retention 90d + grace 1d")

    t0 = time.perf_counter()
    records = build_dataset(args.records)
    t_build = time.perf_counter() - t0
    print(f"dataset_build_seconds={t_build:.3f}")

    cleaner = RetentionCleaner(POLICY)
    if args.tracemalloc:
        tracemalloc.start()
    t1 = time.perf_counter()
    report = cleaner.evaluate(records, NOW, apply=True)
    t_eval = time.perf_counter() - t1

    tm_current = tm_peak = None
    if args.tracemalloc:
        tm_current, tm_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    assert report.integrity_ok and not report.cleanup_aborted
    counts = report.manifest()["counts"]
    print(f"cleanup_eval_seconds={t_eval:.3f}")
    print(f"counts={counts}")
    print(f"peak_rss_mb={peak_rss_mb():.1f}  (ru_maxrss, 进程级峰值)")
    if tm_peak is not None:
        print(f"tracemalloc_peak_mb={tm_peak / 1024 / 1024:.1f}  (Python 分配峰值)")
    print(f"total_seconds={t_build + t_eval:.3f}")


if __name__ == "__main__":
    sys.exit(main())

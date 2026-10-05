"""对照实验运行器。

用法：
    python3 simulate.py compare                 # 命中率/缺失代价/元数据 对照（多容量 x 多负载）
    python3 simulate.py bench                   # 100 万次访问的耗时与内存峰值
    python3 simulate.py all                     # 两者都跑（默认）
    python3 simulate.py compare --trace FILE    # 用真实轨迹文件替代合成负载

所有合成负载使用固定随机种子，各策略面对完全相同的访问序列。
"""

import argparse
import sys
import time
import tracemalloc

import cache_policies
import workloads

SEED = 20261005
N_KEYS = 50_000          # 工作集大小（键空间）
CAPACITIES = [100, 1_000, 10_000]   # 均小于工作集，覆盖"容量 < 工作集"情形
COMPARE_ACCESSES = 200_000
BENCH_ACCESSES = 1_000_000
BENCH_CAPACITY = 1_000


def run_once(policy, keys):
    """跑一遍序列，返回 (命中数, 总缺失代价ms)。"""
    hits = 0
    cost = 0.0
    access = policy.access
    miss_cost = workloads.miss_cost
    for key in keys:
        if access(key):
            hits += 1
        else:
            cost += miss_cost(key)
    return hits, cost


def make_keys(workload_name, total):
    import random
    rng = random.Random(SEED)
    return workloads.GENERATORS[workload_name](N_KEYS, total, rng)


def compare(accesses):
    print(f"## 对照实验：{accesses:,} 次访问，工作集 {N_KEYS:,} 键，"
          f"容量 ∈ {CAPACITIES}（均 < 工作集）\n")
    for wl_name in workloads.GENERATORS:
        print(f"### 负载 `{wl_name}`\n")
        print("| 容量 | 策略 | 命中率 | 总缺失代价 (s) | 平均缺失代价 (ms) | 元数据 (B/条) |")
        print("|---:|---|---:|---:|---:|---:|")
        for cap in CAPACITIES:
            for cls in cache_policies.ALL_POLICIES:
                policy = cls(cap)
                hits, cost = run_once(policy, make_keys(wl_name, accesses))
                misses = accesses - hits
                meta_per_entry = policy.bookkeeping_bytes() / max(1, min(cap, N_KEYS))
                print(f"| {cap:,} | {policy.name} | {hits / accesses:.2%} "
                      f"| {cost / 1000:,.1f} | {cost / max(1, misses):.2f} "
                      f"| {meta_per_entry:.0f} |")
        print()


def bench(accesses, capacity):
    print(f"## 基准：{accesses:,} 次访问，负载 `mixed`，容量 {capacity:,}，"
          f"工作集 {N_KEYS:,} 键\n")
    print("| 策略 | 命中率 | 耗时 (s) | 吞吐 (M ops/s) | 内存峰值 (MiB) |")
    print("|---|---:|---:|---:|---:|")
    for cls in cache_policies.ALL_POLICIES:
        # 第一轮：纯计时（不开 tracemalloc，避免测量开销干扰）
        policy = cls(capacity)
        start = time.perf_counter()
        hits, _ = run_once(policy, make_keys("mixed", accesses))
        elapsed = time.perf_counter() - start
        # 第二轮：tracemalloc 测内存峰值
        policy = cls(capacity)
        tracemalloc.start()
        run_once(policy, make_keys("mixed", accesses))
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        print(f"| {policy.name} | {hits / accesses:.2%} | {elapsed:.2f} "
              f"| {accesses / elapsed / 1e6:.2f} | {peak / 2**20:.2f} |")
    print()


def compare_trace(path, capacity):
    print(f"## 真实轨迹 `{path}`，容量 {capacity:,}\n")
    print("| 策略 | 命中率 | 总缺失代价 (s) |")
    print("|---|---:|---:|")
    keys = list(workloads.load_trace(path))
    for cls in cache_policies.ALL_POLICIES:
        policy = cls(capacity)
        hits, cost = run_once(policy, keys)
        print(f"| {policy.name} | {hits / len(keys):.2%} | {cost / 1000:,.1f} |")
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", nargs="?", default="all",
                    choices=["compare", "bench", "all"])
    ap.add_argument("--trace", help="真实轨迹文件路径（配合 compare 使用）")
    ap.add_argument("--accesses", type=int, default=COMPARE_ACCESSES)
    ap.add_argument("--capacity", type=int, default=BENCH_CAPACITY)
    args = ap.parse_args()

    if args.trace:
        compare_trace(args.trace, args.capacity)
    elif args.mode in ("compare", "all"):
        compare(args.accesses)
    if not args.trace and args.mode in ("bench", "all"):
        bench(BENCH_ACCESSES, args.capacity)


if __name__ == "__main__":
    main()

"""基准测试：容量扫描 + 命中率/缺失代价对照 + 100 万次访问耗时与内存峰值。

用法：
    python3 -m cachebench.benchmark            # 默认 1_000_000 次访问
    python3 -m cachebench.benchmark --quick    # 快速冒烟（100_000 次）
"""

import argparse
import gc
import sys
import time
import tracemalloc

from .policies import ALL_POLICIES
from .workloads import WORKLOADS, make_costs

WORKING_SET = 100_000                       # 不同键总数
CAPACITIES = [100, 1_000, 10_000, 50_000, 100_000, 150_000]
WORKLOAD_LABELS = [
    ("scan", "顺序扫描（全表/备份）"),
    ("zipf", "Zipf 热点（Web/CDN, s=1.1）"),
    ("uniform", "均匀随机（无局部性）"),
]


def run_policy(policy_cls, capacity, key_gen, costs):
    pol = policy_cls(capacity)
    total_cost = 0.0
    for key in key_gen:
        cost = costs[key]
        total_cost += cost
        pol.access(key, cost)
    n = pol.hits + pol.misses
    return {
        "hit_rate": pol.hits / n if n else 0.0,
        "miss_cost": pol.miss_cost,
        "cost_ratio": pol.miss_cost / total_cost if total_cost else 0.0,
    }


def sweep(n):
    """不同容量 × 不同负载 × 不同策略：命中率与缺失代价对照。"""
    print(f"# 容量扫描（工作集 N={WORKING_SET:,}，每序列 {n:,} 次访问）\n")
    cap_header = " | ".join(f"{c:,}" for c in CAPACITIES)
    for wl_name, label in WORKLOAD_LABELS:
        costs = make_costs(WORKING_SET)
        print(f"## {label}")
        print(f"\n命中率（%），列=缓存容量\n")
        print("| 策略 | " + cap_header + " |")
        print("|" + "---|" * (len(CAPACITIES) + 1))
        for cls in ALL_POLICIES:
            row = [cls.name]
            for cap in CAPACITIES:
                gen = WORKLOADS[wl_name](n, WORKING_SET)
                r = run_policy(cls, cap, gen, costs)
                row.append(f"{r['hit_rate'] * 100:.1f}")
            print("| " + " | ".join(row) + " |")
        print(f"\n缺失代价占比（%，缺失键回源代价之和 / 全部访问回源代价之和）\n")
        print("| 策略 | " + cap_header + " |")
        print("|" + "---|" * (len(CAPACITIES) + 1))
        for cls in ALL_POLICIES:
            row = [cls.name]
            for cap in CAPACITIES:
                gen = WORKLOADS[wl_name](n, WORKING_SET)
                r = run_policy(cls, cap, gen, costs)
                row.append(f"{r['cost_ratio'] * 100:.1f}")
            print("| " + " | ".join(row) + " |")
        print()


def time_one_million(capacity=10_000):
    """100 万次访问（Zipf 热点，容量=工作集 10%）的纯执行耗时。"""
    n = 1_000_000
    costs = make_costs(WORKING_SET)
    print(f"# {n:,} 次访问耗时（Zipf 热点，容量 {capacity:,}，不含内存跟踪开销）\n")
    print("| 策略 | 耗时(s) | 吞吐量(ops/s) | 命中率(%) | 缺失代价占比(%) |")
    print("|---|---|---|---|---|")
    for cls in ALL_POLICIES:
        gc.collect()
        gen = WORKLOADS["zipf"](n, WORKING_SET)
        t0 = time.perf_counter()
        r = run_policy(cls, capacity, gen, costs)
        dt = time.perf_counter() - t0
        print(f"| {cls.name} | {dt:.3f} | {n / dt:,.0f} | "
              f"{r['hit_rate'] * 100:.1f} | {r['cost_ratio'] * 100:.1f} |")
    print()


def memory_one_million(capacity=10_000):
    """100 万次访问过程中的 Python 堆峰值（tracemalloc）。"""
    n = 1_000_000
    costs = make_costs(WORKING_SET)
    print(f"# {n:,} 次访问内存峰值（Zipf 热点，容量 {capacity:,}，tracemalloc）\n")
    print("| 策略 | 堆峰值(MB) |")
    print("|---|---|")
    from .policies import LRU as _LRU
    for i, cls in enumerate([_LRU] + ALL_POLICIES):  # 第一项用容量 0 作"无缓存基线"
        gc.collect()
        cap = 0 if i == 0 else capacity
        label = "无缓存基线" if i == 0 else cls.name
        tracemalloc.start()
        gen = WORKLOADS["zipf"](n, WORKING_SET)
        run_policy(cls, cap, gen, costs)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        print(f"| {label} | {peak / 1024 / 1024:.2f} |")
    print()


def metadata_per_entry(capacity=100_000):
    """填满缓存后，每个条目的元数据增量（含被结构持有的 key 对象，便于横向比较）。"""
    print(f"# 元数据内存开销（容量 {capacity:,}，填满后增量/条目）\n")
    print("| 策略 | 每条目字节 | 相对LRU |")
    print("|---|---|---|")
    base = None
    for cls in ALL_POLICIES:
        gc.collect()
        tracemalloc.start()
        pol = cls(capacity)
        for k in range(capacity):
            pol.access(k, 1.0)
        current, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        per = current / capacity
        if base is None:
            base = per
        print(f"| {cls.name} | {per:.1f} | {per / base:.2f}x |")
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="快速冒烟：100k 次访问")
    args = ap.parse_args()
    n = 100_000 if args.quick else 1_000_000
    print("> 注：同一负载下所有策略消费完全相同的访问流（相同随机种子）。\n")
    metadata_per_entry()
    sweep(n)
    time_one_million()
    memory_one_million()


if __name__ == "__main__":
    main()

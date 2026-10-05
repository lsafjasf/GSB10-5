# -*- coding: utf-8 -*-
"""10^7 时间序列基准: 处理耗时与内存峰值。

架构: 识别只需一次采样(默认 20 万条), 规则锁定后全量流式归并,
内存与序列总量无关, 只与"归并后的基数"有关 —— 这正是基数控制的意义。

两种模式各自跑在独立子进程里, 保证 ru_maxrss 峰值互不污染:
  python3 benchmark.py                 # 默认 1000 万条, 双模式对比
  python3 benchmark.py --n 1000000     # 快速自检
"""
import argparse
import gc
import json
import os
import resource
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from cardinality_guard import Config, CardinalityGuard  # noqa: E402

METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH")
STATUSES = ("200", "404", "500")
ENDPOINTS = ("/api/v1/users", "/api/v1/orders", "/api/v1/items",
             "/api/v1/cart", "/api/v1/pay", "/health")
N_USERS = 50_000


def series_stream(n):
    """模拟真实事故现场: 订单号被当成了标签。"""
    for i in range(n):
        yield {
            "method": METHODS[i % 5],
            "status": STATUSES[i % 3],
            "endpoint": ENDPOINTS[i % 6],
            "user_id": "u%07d" % (i % N_USERS),
            "order_id": str(900_000_000 + i),   # 每条序列一个新订单号
        }


def peak_rss_mb():
    # Linux: ru_maxrss 单位 KB
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def run_naive(n):
    """朴素基线: 不做基数控制, 直接物化全部标签组合到集合。"""
    t0 = time.perf_counter()
    keys = set()
    for labels in series_stream(n):
        keys.add(tuple(sorted(labels.items())))
    return {
        "cardinality": len(keys),
        "time_s": round(time.perf_counter() - t0, 2),
        "peak_rss_mb": round(peak_rss_mb()),
    }


def run_guarded(n, sample):
    """基数控制: 采样识别 + 流式归并。"""
    # 阶段 1: 采样识别
    t0 = time.perf_counter()
    guard = CardinalityGuard(Config(window=10_000))
    it = series_stream(n)
    for _ in range(sample):
        guard.observe(next(it))
    reports = guard.finalize()
    fit_t = time.perf_counter() - t0
    guard._stats.clear()          # 识别统计可释放, 归并只依赖规则表
    gc.collect()

    # 阶段 2: 全量流式归并(只持有归并后的去重集合)
    t0 = time.perf_counter()
    out_keys = set()
    count = sample
    for labels in it:
        out_keys.add(tuple(sorted(guard.reduce(labels).items())))
        count += 1
    reduce_t = time.perf_counter() - t0
    return {
        "cardinality": len(out_keys),
        "fit_s": round(fit_t, 2),
        "reduce_s": round(reduce_t, 2),
        "throughput": int(count / reduce_t),
        "peak_rss_mb": round(peak_rss_mb()),
        "rules": {label: report.rule for label, report in reports.items()},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10_000_000)
    ap.add_argument("--sample", type=int, default=200_000)
    ap.add_argument("--mode", choices=("naive", "guarded"),
                    help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.mode == "naive":
        print(json.dumps(run_naive(args.n)))
        return
    if args.mode == "guarded":
        print(json.dumps(run_guarded(args.n, args.sample)))
        return

    print(f"Python {sys.version.split()[0]}, N = {args.n:,}, "
          f"识别样本 = {args.sample:,}\n")
    cmd = [sys.executable, os.path.abspath(__file__),
           "--n", str(args.n), "--sample", str(args.sample)]

    print("[1/2] 朴素基线: 物化全部标签组合 ...")
    out = subprocess.run(cmd + ["--mode", "naive"], check=True,
                         capture_output=True, text=True)
    naive = json.loads(out.stdout.strip())
    print(json.dumps(naive, indent=2, ensure_ascii=False))

    print("\n[2/2] 基数控制: 采样识别 + 流式归并 ...")
    out = subprocess.run(cmd + ["--mode", "guarded"], check=True,
                         capture_output=True, text=True)
    guarded = json.loads(out.stdout.strip())
    print(json.dumps(guarded, indent=2, ensure_ascii=False))

    ratio_mem = naive["peak_rss_mb"] / guarded["peak_rss_mb"]
    ratio_time = naive["time_s"] / (guarded["fit_s"] + guarded["reduce_s"])
    ratio_card = naive["cardinality"] / guarded["cardinality"]
    print(f"\n{'指标':<14}{'朴素基线':>16}{'基数控制':>16}{'改善':>12}")
    print("-" * 60)
    print(f"{'基数':<14}{naive['cardinality']:>16,}"
          f"{guarded['cardinality']:>16,}{ratio_card:>10.0f}x")
    print(f"{'耗时(s)':<14}{naive['time_s']:>16.2f}"
          f"{guarded['fit_s'] + guarded['reduce_s']:>16.2f}"
          f"{ratio_time:>10.1f}x")
    print(f"{'峰值内存(MB)':<14}{naive['peak_rss_mb']:>16,}"
          f"{guarded['peak_rss_mb']:>16,}{ratio_mem:>10.1f}x")
    print(f"\n吞吐率: {guarded['throughput']:,} 序列/s (归并阶段)")


if __name__ == "__main__":
    main()

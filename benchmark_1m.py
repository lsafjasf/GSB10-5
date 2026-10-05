"""100 万轮查询基准：耗时与内存峰值（Linux ru_maxrss）。

每个场景在独立子进程中运行，保证峰值内存互不污染：
A. 100 万轮 / 2000 个切片池（绝大多数命中重复缓存，稳态查询吞吐）
B. 100 万个互不相同切片（每轮都全额扣费、写消耗记录，最重路径）
C. 100 万条消耗记录全部留内存（对比"写盘模式"的内存代价）

运行：python3 benchmark_1m.py
产物：bench_1m_records.jsonl、benchmark_result.md
"""

import gc
import json
import os
import resource
import subprocess
import sys
import time

from dpagg import BudgetLedger, PrivateAggregator

N = 1_000_000
POOL = 2_000
ROWS = [{"k": str(i % 8)} for i in range(20)]
SPILL_PATH = "bench_1m_records.jsonl"


def rss_kb() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def _int_stream():
    x = 1
    while True:
        x = (x * 1103515245 + 12345) % 2_147_483_648
        yield x


def scenario_a() -> dict:
    if os.path.exists(SPILL_PATH):
        os.unlink(SPILL_PATH)
    ledger = BudgetLedger(epsilon_total=POOL * 0.01, spill_path=SPILL_PATH)
    agg = PrivateAggregator(ledger, seed=1)
    pool = [{"k": str(i)} for i in range(POOL)]
    plan = [pool[i % POOL] for i in range(N)]  # 查询计划预生成，不计入耗时
    gc.collect()
    t0 = time.perf_counter()
    cache_hits = 0
    for predicate in plan:
        res = agg.ask("count", ROWS, predicate, epsilon=0.01)
        cache_hits += int(res["cached"])
    elapsed = time.perf_counter() - t0
    ledger.close()
    return {"name": "A 重复切片为主（2000 切片池，记录写盘）",
            "rounds": N, "charged": ledger.record_count - cache_hits,
            "cache_hits": cache_hits, "elapsed_s": elapsed, "rps": N / elapsed,
            "rss_peak_mb": rss_kb() / 1024,
            "spill_file_mb": os.path.getsize(SPILL_PATH) / 1e6}


def scenario_b() -> dict:
    if os.path.exists(SPILL_PATH):
        os.unlink(SPILL_PATH)
    eps = 1e-6
    ledger = BudgetLedger(epsilon_total=N * eps, spill_path=SPILL_PATH)
    agg = PrivateAggregator(ledger, seed=2)
    stream = _int_stream()
    plan = [{"k": str(next(stream))} for _ in range(N)]  # 互不相同
    gc.collect()
    t0 = time.perf_counter()
    for predicate in plan:
        agg.ask("count", ROWS, predicate, epsilon=eps)
    elapsed = time.perf_counter() - t0
    ledger.close()
    return {"name": "B 全部为新切片（每轮扣费，记录写盘）",
            "rounds": N, "charged": ledger.record_count, "cache_hits": 0,
            "elapsed_s": elapsed, "rps": N / elapsed,
            "rss_peak_mb": rss_kb() / 1024,
            "spill_file_mb": os.path.getsize(SPILL_PATH) / 1e6}


def scenario_c() -> dict:
    ledger = BudgetLedger(epsilon_total=N * 1e-6)  # 记录全留内存
    gc.collect()
    rss0 = rss_kb()
    t0 = time.perf_counter()
    for _ in range(N):
        ledger.spend(1e-6, note="count")
    elapsed = time.perf_counter() - t0
    rss1 = rss_kb()
    return {"name": "C 消耗记录全留内存（账本直写）",
            "rounds": N, "charged": N, "cache_hits": 0,
            "elapsed_s": elapsed, "rps": N / elapsed,
            "rss_peak_mb": rss1 / 1024,
            "bytes_per_record": max(rss1 - rss0, 0) * 1024 / N,
            "spill_file_mb": 0.0}


SCENARIOS = {"a": scenario_a, "b": scenario_b, "c": scenario_c}


def main():
    if len(sys.argv) == 2 and sys.argv[1] in SCENARIOS:
        print(json.dumps(SCENARIOS[sys.argv[1]]()))
        return
    print("Python", sys.version.split()[0], f"| 目标轮数 {N:,}")
    results = []
    for key in ("a", "b", "c"):
        print(f"运行场景 {key.upper()} ...", flush=True)
        out = subprocess.run([sys.executable, os.path.abspath(__file__), key],
                             capture_output=True, text=True, check=True)
        r = json.loads(out.stdout.strip())
        results.append(r)
        print(f"  {r['name']}: {r['elapsed_s']:.2f}s, "
              f"{r['rps']:,.0f} 轮/秒, 峰值RSS {r['rss_peak_mb']:.1f}MB",
              flush=True)

    lines = [
        "# 100 万轮查询基准结果", "",
        f"环境：Python {sys.version.split()[0]}（仅标准库），Linux；"
        "每场景独立进程，内存峰值取 getrusage(RUSAGE_SELF).ru_maxrss。",
        "查询计划预生成，不计入耗时。",
        "",
        "| 场景 | 轮数 | 扣费轮数 | 缓存命中 | 耗时(s) | 吞吐(轮/秒) | 峰值RSS(MB) | 记录文件(MB) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['name']} | {r['rounds']:,} | {r['charged']:,} | "
            f"{r['cache_hits']:,} | {r['elapsed_s']:.2f} | {r['rps']:,.0f} | "
            f"{r['rss_peak_mb']:.1f} | {r['spill_file_mb']:.1f} |")
    lines += ["",
              f"- 场景 C：每条内存记录约 {results[2]['bytes_per_record']:.0f} 字节；"
              "场景 A/B 用 `spill_path` 将逐轮记录追加写盘，账本内存有界，"
              "记录仍可完整审计（bench_1m_records.jsonl）。",
              "- 场景 B 的内存主要是 100 万个不同切片的缓存与倒排索引"
              "（每切片约 0.5–1KB），这是“重复切片免费”策略的固有代价。"]
    with open("benchmark_result.md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("已写入 benchmark_result.md")


if __name__ == "__main__":
    main()

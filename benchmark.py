#!/usr/bin/env python3
"""压测：N 节点 x M 次失效广播的耗时与内存峰值（仅标准库）。

阶段：
  P0 构建持久失效日志（M 条）
  P1 全量 fan-out 计时（含 5% 重复投递，验证幂等开销）
  P2 有损广播（尾部 1 万条 30% 丢失+乱序）+ 反熵补齐收敛计时
  P3 最终一致性校验（digest + 脏缓存扫描）计时

内存峰值取进程 ru_maxrss（含解释器与全部节点状态）。

用法：python3 benchmark.py [--nodes 1000] [--invalidations 100000]
                           [--keys 10000] [--cache-per-node 512]
                           [--out reports/benchmark_report.json]
"""

import argparse
import json
import os
import platform
import resource
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cache_broadcast import CacheNode, Cluster, DurableLog  # noqa: E402
from cache_broadcast.digest import cluster_summary  # noqa: E402


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nodes", type=int, default=1000)
    ap.add_argument("--invalidations", type=int, default=100000)
    ap.add_argument("--keys", type=int, default=10000)
    ap.add_argument("--cache-per-node", type=int, default=512)
    ap.add_argument("--loss-tail", type=int, default=10000,
                    help="P2 中有损广播的尾部条数")
    ap.add_argument("--out", default="reports/benchmark_report.json")
    args = ap.parse_args()

    n_nodes, n_inv, n_keys = args.nodes, args.invalidations, args.keys
    dup_mod = 20  # 每 20 条多投 1 次 = 5% 重复
    report = {
        "config": {
            "nodes": n_nodes,
            "invalidations": n_inv,
            "keys": n_keys,
            "cache_per_node": args.cache_per_node,
            "duplicate_rate": 1.0 / dup_mod,
            "python": platform.python_version(),
            "machine": platform.platform(),
            "cpu_count": os.cpu_count(),
        },
        "phases": {},
    }
    print(f"配置: {n_nodes} 节点 x {n_inv} 失效, 键空间 {n_keys}, "
          f"每节点缓存 {args.cache_per_node} 条")

    # ---- P0 构建日志 ----
    t0 = time.perf_counter()
    key_pool = [f"k{i}" for i in range(n_keys)]
    log = DurableLog()
    versions = {}
    for i in range(n_inv):
        key = key_pool[i % n_keys]
        v = versions.get(key, 0) + 1
        versions[key] = v
        log.append(key, v)
    t1 = time.perf_counter()
    report["phases"]["P0_build_log"] = {"seconds": round(t1 - t0, 3), "records": len(log)}
    print(f"P0 构建日志: {t1 - t0:.2f}s")

    # ---- 建节点 + 预填缓存 ----
    t0 = time.perf_counter()
    cluster = Cluster(log, seed=7)
    nodes = []
    cache_per_node = args.cache_per_node
    for n in range(n_nodes):
        node = CacheNode(f"n{n}")
        cache = node.cache
        base = n * cache_per_node
        for j in range(cache_per_node):
            cache[key_pool[(base + j) % n_keys]] = ("v", j % 3)
        cluster.add(node)
        nodes.append(node)
    t1 = time.perf_counter()
    print(f"建节点+预填: {t1 - t0:.2f}s, RSS {peak_rss_mb():.0f}MB")

    # ---- P1 全量 fan-out 计时 ----
    t0 = time.perf_counter()
    handle_calls = 0
    for inv in log.replay(1):
        dup = inv.seq % dup_mod == 0
        for node in nodes:
            node.handle(inv)
            if dup:
                node.handle(inv)  # 5% 重复投递，必须幂等
        handle_calls += n_nodes
    t1 = time.perf_counter()
    fanout_sec = t1 - t0
    total_calls = handle_calls + handle_calls // dup_mod
    dups = sum(nd.dups_dropped for nd in nodes)
    all_applied = all(nd.applied_seq == n_inv for nd in nodes)
    report["phases"]["P1_fanout"] = {
        "seconds": round(fanout_sec, 3),
        "handle_calls": total_calls,
        "calls_per_sec": int(total_calls / fanout_sec),
        "invalidations_per_sec_per_node": int(n_inv / fanout_sec),
        "duplicates_dropped": dups,
        "all_applied_to_tail": all_applied,
        "peak_rss_mb": round(peak_rss_mb(), 1),
    }
    print(f"P1 fan-out: {fanout_sec:.2f}s, "
          f"{total_calls / fanout_sec / 1e6:.2f}M 次处理/秒, "
          f"峰值 RSS {peak_rss_mb():.0f}MB, 全部到尾部={all_applied}")

    # ---- P2 有损广播 + 反熵收敛 ----
    t0 = time.perf_counter()
    tail_versions = versions
    for i in range(args.loss_tail):
        key = key_pool[(n_inv + i) % n_keys]
        v = tail_versions.get(key, 0) + 1
        tail_versions[key] = v
        log.append(key, v)
    tail_invs = log.replay(n_inv + 1)
    cluster.fanout(tail_invs, loss=0.3, shuffle=True)
    t1 = time.perf_counter()
    lossy_sec = t1 - t0
    diverged = sum(1 for nd in nodes if nd.applied_seq < len(log))

    t0 = time.perf_counter()
    rounds = cluster.converge(replay_loss=0.1, replay_duplicate=0.05, batch_limit=4096)
    t1 = time.perf_counter()
    converge_sec = t1 - t0
    converged = all(nd.applied_seq == len(log) for nd in nodes)
    report["phases"]["P2_loss_and_repair"] = {
        "loss_tail_records": args.loss_tail,
        "fanout_loss_rate": 0.3,
        "lost_on_fanout": cluster.lost,
        "lossy_fanout_seconds": round(lossy_sec, 3),
        "diverged_nodes_before_repair": diverged,
        "anti_entropy_rounds_applied": rounds,
        "converge_seconds": round(converge_sec, 3),
        "converged": converged,
        "peak_rss_mb": round(peak_rss_mb(), 1),
    }
    print(f"P2 有损广播 {lossy_sec:.2f}s（{diverged} 节点落后）, "
          f"反熵 {len(rounds)} 轮补齐 {converge_sec:.2f}s, 收敛={converged}")

    # ---- P3 最终一致性校验 ----
    t0 = time.perf_counter()
    summary = cluster_summary(nodes)
    t1 = time.perf_counter()
    report["phases"]["P3_consistency_check"] = {
        "seconds": round(t1 - t0, 3),
        "digest_count": summary["digest_count"],
        "digest": summary["digest"],
        "applied_seq_range": summary["applied_seq_range"],
        "stale_total": summary["stale_total"],
    }
    print(f"P3 一致性校验 {t1 - t0:.2f}s: digest 种类={summary['digest_count']}, "
          f"脏缓存={summary['stale_total']}")

    report["peak_rss_mb"] = round(peak_rss_mb(), 1)
    report["total_seconds"] = round(
        report["phases"]["P0_build_log"]["seconds"]
        + report["phases"]["P1_fanout"]["seconds"]
        + report["phases"]["P2_loss_and_repair"]["lossy_fanout_seconds"]
        + report["phases"]["P2_loss_and_repair"]["converge_seconds"]
        + report["phases"]["P3_consistency_check"]["seconds"], 3)
    print(f"总计 {report['total_seconds']}s, 进程峰值 RSS {report['peak_rss_mb']}MB")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"报告已写入 {args.out}")
    ok = (all_applied and converged and summary["digest_count"] == 1
          and summary["stale_total"] == 0)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""缓存失效广播 · 自测（仅标准库）。

覆盖场景：
  S1 全部送达        —— 无丢失无重复，一次收敛
  S2 部分丢失        —— 30% 广播丢失 + 乱序，反熵补齐后收敛
  S3 重复投递        —— 50% 重复率，验证幂等（恰好应用一次）
  S4 广播期间重启    —— checkpoint 崩溃恢复 + 日志追赶后收敛
  S5 广播后的新写入  —— 重复/重放的旧失效不得误伤新数据

用法：python3 run_selftest.py [--out reports/selftest_report.json]
"""

import argparse
import json
import os
import platform
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cache_broadcast import (  # noqa: E402
    CacheNode,
    Cluster,
    DurableLog,
    state_digest,
    count_stale_entries,
)
from cache_broadcast.digest import cluster_summary  # noqa: E402


def make_log(n_invalidations: int, n_keys: int, path=None):
    """生成失效日志：每键版本号单调递增。"""
    log = DurableLog(path)
    versions = {}
    for i in range(n_invalidations):
        key = f"key:{i % n_keys}"
        versions[key] = versions.get(key, 0) + 1
        log.append(key, versions[key])
    return log


def prefill_stale(node: CacheNode, n_keys: int, version: int = 0) -> None:
    """给节点预填一批「旧版本」缓存，等待被失效。"""
    for i in range(n_keys):
        node.cache[f"key:{i}"] = (f"stale-value-{i}", version)


def scenario_full_delivery():
    """S1：全部送达。1000 条失效、5 节点、零丢失零重复。"""
    n_inv, n_keys, n_nodes = 1000, 50, 5
    log = make_log(n_inv, n_keys)
    cluster = Cluster(log, seed=1)
    for i in range(n_nodes):
        node = CacheNode(f"n{i}")
        prefill_stale(node, n_keys)
        cluster.add(node)

    cluster.fanout(log.replay(1), loss=0.0, duplicate=0.0)

    summary = cluster_summary(cluster.nodes.values())
    checks = {
        "all_applied": all(n.applied_seq == n_inv for n in cluster.nodes.values()),
        "exactly_once": all(n.applied_count == n_inv for n in cluster.nodes.values()),
        "no_duplicates": all(n.dups_dropped == 0 for n in cluster.nodes.values()),
        "single_digest": summary["digest_count"] == 1,
        "stale_zero": summary["stale_total"] == 0,
        "cache_evicted": all(len(n.cache) == 0 for n in cluster.nodes.values()),
    }
    return {
        "scenario": "S1_full_delivery",
        "desc": "全部送达：5 节点 x 1000 失效，无丢失无重复",
        "params": {"nodes": n_nodes, "invalidations": n_inv, "keys": n_keys},
        "data": {
            "applied_seq_range": summary["applied_seq_range"],
            "digest_count": summary["digest_count"],
            "digest": summary["digest"],
            "stale_total": summary["stale_total"],
            "dups_dropped_total": sum(n.dups_dropped for n in cluster.nodes.values()),
            "delivered": cluster.delivered,
        },
        "checks": checks,
        "passed": all(checks.values()),
    }


def scenario_partial_loss():
    """S2：部分丢失。30% 丢失 + 乱序，反熵补齐（重放本身还有 10% 丢失）。"""
    n_inv, n_keys, n_nodes = 2000, 60, 8
    log = make_log(n_inv, n_keys)
    cluster = Cluster(log, seed=2)
    for i in range(n_nodes):
        node = CacheNode(f"n{i}")
        prefill_stale(node, n_keys)
        cluster.add(node)

    cluster.fanout(log.replay(1), loss=0.3, shuffle=True)
    before = cluster_summary(cluster.nodes.values())

    rounds = cluster.converge(replay_loss=0.1, replay_duplicate=0.05, batch_limit=256)
    after = cluster_summary(cluster.nodes.values())

    checks = {
        "loss_actually_happened": cluster.lost > 0,
        "diverged_before_repair": before["applied_seq_range"][0] < n_inv,
        "converged": after["converged"],
        "all_applied": after["applied_seq_range"] == [n_inv, n_inv],
        "stale_zero": after["stale_total"] == 0,
        "exactly_once": all(n.applied_count == n_inv for n in cluster.nodes.values()),
    }
    return {
        "scenario": "S2_partial_loss",
        "desc": "部分丢失：30% 广播丢失+乱序，反熵补齐（重放再丢 10%）",
        "params": {"nodes": n_nodes, "invalidations": n_inv, "fanout_loss": 0.3,
                   "replay_loss": 0.1, "replay_duplicate": 0.05},
        "data": {
            "lost_on_fanout": cluster.lost,
            "before_repair": {
                "applied_seq_range": before["applied_seq_range"],
                "digest_count": before["digest_count"],
                "stale_total": before["stale_total"],
            },
            "anti_entropy_rounds_applied": rounds,
            "rounds_to_converge": len(rounds),
            "after_repair": {
                "applied_seq_range": after["applied_seq_range"],
                "digest_count": after["digest_count"],
                "digest": after["digest"],
                "stale_total": after["stale_total"],
            },
        },
        "checks": checks,
        "passed": all(checks.values()),
    }


def scenario_duplicate_delivery():
    """S3：重复投递。50% 重复率 + 乱序，验证幂等。"""
    n_inv, n_keys, n_nodes = 500, 30, 5
    log = make_log(n_inv, n_keys)
    cluster = Cluster(log, seed=3)
    for i in range(n_nodes):
        node = CacheNode(f"n{i}")
        prefill_stale(node, n_keys)
        cluster.add(node)

    cluster.fanout(log.replay(1), loss=0.0, duplicate=0.5, shuffle=True)
    summary = cluster_summary(cluster.nodes.values())
    dups = sum(n.dups_dropped for n in cluster.nodes.values())

    checks = {
        "duplicates_were_sent": cluster.duplicates_sent > 0,
        "all_dups_dropped": dups == cluster.duplicates_sent,
        "exactly_once": all(n.applied_count == n_inv for n in cluster.nodes.values()),
        "converged": summary["converged"],
        "stale_zero": summary["stale_total"] == 0,
    }
    return {
        "scenario": "S3_duplicate_delivery",
        "desc": "重复投递：50% 重复率+乱序，重复消息全部幂等丢弃",
        "params": {"nodes": n_nodes, "invalidations": n_inv, "duplicate_rate": 0.5},
        "data": {
            "duplicates_sent": cluster.duplicates_sent,
            "dups_dropped_total": dups,
            "applied_count_per_node": sorted(n.applied_count for n in cluster.nodes.values()),
            "digest_count": summary["digest_count"],
            "stale_total": summary["stale_total"],
        },
        "checks": checks,
        "passed": all(checks.values()),
    }


def scenario_restart_during_broadcast():
    """S4：广播期间节点重启。checkpoint 崩溃恢复 + 从持久日志追赶。"""
    tmpdir = tempfile.mkdtemp(prefix="cb_restart_")
    try:
        log_path = os.path.join(tmpdir, "inval.log")
        ckpt_path = os.path.join(tmpdir, "node_R.ckpt")
        n_inv, n_keys = 300, 20
        log = make_log(n_inv, n_keys, path=log_path)
        log.close()

        cluster = Cluster(log, seed=4)
        peers = []
        for i in range(2):
            node = CacheNode(f"peer{i}")
            prefill_stale(node, n_keys)
            cluster.add(node)
            peers.append(node)
        node_r = CacheNode("R")
        prefill_stale(node_r, n_keys)
        cluster.add(node_r)

        # 广播前 120 条，R 正常接收后 checkpoint，随后「崩溃」
        cut = 120
        cluster.fanout(log.replay(1, cut))
        # 崩溃前写入一条与当前版本齐平的新数据（前 120 条里 key:0 已失效 6 次）
        node_r.fill("key:0", "fresh-before-crash", 6)
        node_r.checkpoint(ckpt_path)
        applied_before_crash = node_r.applied_seq
        cache_before_crash = len(node_r.cache)
        del cluster.nodes["R"]
        del node_r  # 进程消亡，内存状态全部丢失

        # 崩溃期间继续广播剩余 180 条（R 全部错过）
        cluster.fanout(log.replay(cut + 1, n_inv))

        # R 重启：从 checkpoint 恢复，从持久日志（重新打开文件）追赶
        log2 = DurableLog(log_path)  # 模拟新进程重新加载日志
        revived = CacheNode.restore(ckpt_path)
        applied_after_restart = revived.applied_seq
        fresh_entry = revived.cache.get("key:0")
        fresh_survived = fresh_entry is not None and fresh_entry[0] == "fresh-before-crash"
        caught_up = revived.catchup(log2)
        cluster.add(revived)

        summary = cluster_summary(cluster.nodes.values())
        checks = {
            "checkpoint_preserved_seq": applied_after_restart == applied_before_crash == cut,
            "missed_while_down": caught_up == n_inv - cut,
            "caught_up_to_tail": revived.applied_seq == n_inv,
            "digest_matches_peers": summary["converged"],
            "stale_zero": summary["stale_total"] == 0,
            "log_durable": len(log2) == n_inv,
            "fresh_cache_survives_restart": fresh_survived,
        }
        return {
            "scenario": "S4_restart_during_broadcast",
            "desc": "广播期间重启：120/300 处崩溃，恢复后从持久日志补齐 180 条",
            "params": {"nodes": 3, "invalidations": n_inv, "crash_at_seq": cut},
            "data": {
                "applied_before_crash": applied_before_crash,
                "cache_entries_before_crash": cache_before_crash,
                "applied_after_restart": applied_after_restart,
                "fresh_entry_after_restart": fresh_entry,
                "catchup_applied": caught_up,
                "applied_final": revived.applied_seq,
                "digest_count": summary["digest_count"],
                "digest": summary["digest"],
                "stale_total": summary["stale_total"],
                "restarted_node": {
                    "node_id": revived.node_id,
                    "applied_seq": revived.applied_seq,
                    "applied_count": revived.applied_count,
                    "dups_dropped": revived.dups_dropped,
                    "digest": state_digest(revived),
                },
            },
            "checks": checks,
            "passed": all(checks.values()),
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def scenario_new_write_protection():
    """S5：广播之后写入的新数据，不得被重复/重放的旧失效误伤。"""
    log = DurableLog()
    cluster = Cluster(log, seed=5)
    nodes = {}
    for nid in ("A", "B", "C"):
        node = CacheNode(nid)
        node.fill("user:42", "old", 1)  # 三节点都缓存了 v1
        cluster.add(node)
        nodes[nid] = node

    # 失效广播：user:42 最新版本为 2。C 丢失该广播。
    inv1 = log.append("user:42", 2)
    cluster.fanout([inv1], targets=["A", "B"])
    evicted_ab = all(nodes[n].get("user:42") is None for n in ("A", "B"))

    # 广播之后写入新数据 v3，三节点都回填新缓存
    for node in nodes.values():
        node.fill("user:42", "new", 3)

    # 重复投递风暴：inv1 又给 A、B 投了两轮（每轮必出 1 份重复）
    cluster.fanout([inv1], targets=["A", "B"], duplicate=1.0)
    cluster.fanout([inv1], targets=["A", "B"], duplicate=1.0)
    intact_after_storm = all(nodes[n].get("user:42") == "new" for n in ("A", "B"))
    storm_values = {nid: nodes[nid].get("user:42") for nid in ("A", "B")}

    # C 通过日志补齐重放 inv1：旧失效遇到新数据 v3，必须保留
    c_applied = nodes["C"].catchup(log)
    intact_after_replay = nodes["C"].get("user:42") == "new"

    # 旧版本回填必须被拒绝（防止慢节点把广播前的旧数据 v1 塞回来）
    stale_fill_rejected = not nodes["A"].fill("user:42", "stale", 1)

    # 但更新的失效（version=4）仍然必须生效：把 v3 干掉
    inv2 = log.append("user:42", 4)
    cluster.fanout([inv2])
    newer_invalidation_works = all(n.get("user:42") is None for n in nodes.values())

    checks = {
        "initial_invalidation_evicted": evicted_ab,
        "new_write_survives_duplicates": intact_after_storm,
        "new_write_survives_replay": intact_after_replay,
        "stale_fill_rejected": stale_fill_rejected,
        "newer_invalidation_still_works": newer_invalidation_works,
    }
    return {
        "scenario": "S5_new_write_protection",
        "desc": "新写保护：v2 失效广播后写入 v3，重复/重放的 v2 失效不得误伤 v3",
        "params": {"nodes": 3, "key": "user:42",
                   "versions": {"cached": 1, "invalidated": 2, "rewritten": 3}},
        "data": {
            "dups_dropped_per_node": {n.node_id: n.dups_dropped for n in nodes.values()},
            "node_c_catchup_applied": c_applied,
            "cache_value_after_storm": storm_values,
            "cache_value_after_replay_C": "new" if intact_after_replay else None,
            "stale_fill_v1_rejected": stale_fill_rejected,
            "cache_after_newer_invalidation_v4": {
                n.node_id: n.get("user:42") for n in nodes.values()
            },
        },
        "checks": checks,
        "passed": all(checks.values()),
    }


ALL_SCENARIOS = [
    scenario_full_delivery,
    scenario_partial_loss,
    scenario_duplicate_delivery,
    scenario_restart_during_broadcast,
    scenario_new_write_protection,
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="reports/selftest_report.json")
    args = parser.parse_args()

    started = time.perf_counter()
    results = []
    for fn in ALL_SCENARIOS:
        t0 = time.perf_counter()
        results.append(fn())
        results[-1]["elapsed_sec"] = round(time.perf_counter() - t0, 3)

    report = {
        "suite": "cache-broadcast selftest",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": platform.python_version(),
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "scenarios": results,
        "passed": sum(r["passed"] for r in results),
        "total": len(results),
    }

    print(f"== 缓存失效广播自测 (Python {report['python']}) ==")
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"[{mark}] {r['scenario']:<28} {r['desc']}")
        for k, v in r["checks"].items():
            print(f"       - {k}: {v}")
    print(f"== 通过 {report['passed']}/{report['total']}，耗时 {report['elapsed_sec']}s ==")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"报告已写入 {args.out}")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    sys.exit(main())

"""规模压测: 1000 个节点, 10 万次缓存失效, 含丢包/重复投递/广播期间重启。

场景参数固定随机种子可复现:
  - 40 个失效源, 每个发 2500 条 (共 100,000), 每批 100 条, 25 轮;
  - 每次推送 2% 概率整批丢失, 0.3% 概率整批重复;
  - 每 5 轮做一次反熵; 3 个节点在第 14 轮重启 (落盘 -> 离线 2 轮 -> 恢复补齐)。
"""

import gc
import json
import os
import random
import resource
import tempfile
import time
import tracemalloc

from invalidation import ArrayVersionTable, CacheNode, MonotonicClock, Origin

NODES = 1000
ORIGINS = 40
TOTAL = 100_000
KEYS = 20_000
BATCH = 100
CACHE_PER_NODE = 2000
LOSS_P = 0.02
DUP_P = 0.003
ANTI_ENTROPY_EVERY = 5
RESTART_AT_ROUND = 13          # 0-based: 第 14 轮
RESTART_DOWN_ROUNDS = 2
RESTART_NODES = 3

PER_ORIGIN = TOTAL // ORIGINS
ROUNDS = PER_ORIGIN // BATCH


def rss_mb():
    # Linux: ru_maxrss 单位 KB
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def main():
    rng = random.Random(424242)
    print(f"配置: nodes={NODES} origins={ORIGINS} invalidations={TOTAL} "
          f"keys={KEYS} batch={BATCH} rounds={ROUNDS} loss={LOSS_P} dup={DUP_P}")

    tracemalloc.start()
    t_start = time.perf_counter()
    phase = {}

    clock = MonotonicClock()
    origins = [Origin(f"o{i}") for i in range(ORIGINS)]
    nodes = [CacheNode(i, table=ArrayVersionTable(KEYS)) for i in range(NODES)]

    # 预热: 每节点 2000 条本地缓存
    for n in nodes:
        for _ in range(CACHE_PER_NODE):
            n.write(rng.randrange(KEYS), "v", clock.tick())
    t_warm = time.perf_counter()
    phase["warmup_s"] = round(t_warm - t_start, 3)

    tmpdir = tempfile.mkdtemp(prefix="cache-bench-")
    stats = {"lost_batches": 0, "lost_messages": 0, "dup_batches": 0,
             "repaired": 0, "restarted": []}
    down_until = {}
    restarted_ids = set(range(RESTART_NODES))   # node 0,1,2 演示重启

    max_buffered = 0
    t_broadcast0 = time.perf_counter()
    for rnd in range(ROUNDS):
        # 40 个 origin 各发一批 (同一批内 seq 连续)
        batches = []
        for o in origins:
            batches.append(
                [o.invalidate(rng.randrange(KEYS), clock.tick()) for _ in range(BATCH)]
            )

        # 第 14 轮开始: 选中节点落盘并离线
        if rnd == RESTART_AT_ROUND:
            for i in restarted_ids:
                nodes[i].dump(os.path.join(tmpdir, f"node-{i}.json"))
                down_until[i] = rnd + RESTART_DOWN_ROUNDS

        for n in nodes:
            if down_until.get(n.node_id, -1) >= rnd:
                continue
            for batch in batches:
                r = rng.random()
                if r < LOSS_P:
                    stats["lost_batches"] += 1
                    stats["lost_messages"] += len(batch)
                    continue
                n.receive_batch(batch)
                if rng.random() < DUP_P:
                    n.receive_batch(batch)       # 重复投递
                    stats["dup_batches"] += 1

        # 离线结束: 从检查点恢复 (新进程语义), 再走反熵补齐
        if rnd == RESTART_AT_ROUND + RESTART_DOWN_ROUNDS:
            for i in restarted_ids:
                nodes[i] = CacheNode.load(os.path.join(tmpdir, f"node-{i}.json"))
                gap_before = sum(nodes[i].need_from(o) for o in origins)
                nodes[i].anti_entropy(origins)
                stats["restarted"].append(
                    {"node": i, "gap_at_restart": gap_before,
                     "pulled": nodes[i].repaired})

        if (rnd + 1) % ANTI_ENTROPY_EVERY == 0:
            for n in nodes:
                n.anti_entropy(origins)
            for n in nodes:
                b = sum(len(buf) for buf in n.buffered.values())
                if b > max_buffered:
                    max_buffered = b

    t_broadcast1 = time.perf_counter()
    phase["broadcast_s"] = round(t_broadcast1 - t_broadcast0, 3)

    # 收尾反熵, 直到没有节点能再拉到消息
    ae_rounds = 0
    t_ae0 = time.perf_counter()
    while True:
        ae_rounds += 1
        total = 0
        for n in nodes:
            total += n.anti_entropy(origins)
        if total == 0:
            break
    t_ae1 = time.perf_counter()
    phase["final_anti_entropy_s"] = round(t_ae1 - t_ae0, 3)

    # ---------------- 最终一致性校验 ----------------
    t_v0 = time.perf_counter()
    expected = ArrayVersionTable(KEYS)
    for o in origins:
        for m in o.log:
            expected.merge(m.key, m.version)

    conv = {"converged": 0, "seq_mismatch": 0, "table_mismatch": 0,
            "stale_residue_nodes": 0, "checked": NODES}
    stale_total = 0
    for n in nodes:
        ok = True
        for o in origins:
            if n.contig.get(o.id, 0) != o.seq:
                conv["seq_mismatch"] += 1
                ok = False
        if n.table.data != expected.data:
            conv["table_mismatch"] += 1
            ok = False
        stale = n.stale_entries()
        if stale:
            conv["stale_residue_nodes"] += 1
            stale_total += len(stale)
            ok = False
        if ok:
            conv["converged"] += 1
    t_v1 = time.perf_counter()
    phase["verify_s"] = round(t_v1 - t_v0, 3)
    phase["total_s"] = round(t_v1 - t_start, 3)

    gc.collect()
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    stats["repaired"] = sum(n.repaired for n in nodes)
    stats["dups_dropped"] = sum(n.dups_dropped for n in nodes)
    stats["dup_batches_delivered"] = stats["dup_batches"]
    stats["max_out_of_order_buffer_msgs"] = max_buffered
    stats["final_anti_entropy_rounds"] = ae_rounds

    report = {
        "phase_seconds": phase,
        "convergence": conv,
        "stale_entries_total": stale_total,
        "stats": stats,
        "memory": {
            "tracemalloc_peak_MB": round(peak / 1024 / 1024, 1),
            "tracemalloc_current_after_gc_MB": round(cur / 1024 / 1024, 1),
            "process_rss_peak_MB": round(rss_mb(), 1),
        },
    }
    print("\n=== 压测结果 (1000 节点 / 100,000 次失效) ===")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    assert conv["converged"] == NODES
    assert stale_total == 0
    return report


if __name__ == "__main__":
    main()

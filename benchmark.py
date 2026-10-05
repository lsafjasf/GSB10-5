#!/usr/bin/env python3
"""1 万份资源清单比对的耗时与内存峰值基准（仅标准库）。

模拟方式：确定性随机生成 N 对清单（基线 + 候选），每份含 R 个资源，
候选侧按固定比例注入等价写法/合规差异/违规差异/缺失/新增/字段缺失/未知字段。
逐对调用 compare_manifests（流式，不累积明细），测量总耗时与内存峰值。
"""
import argparse
import json
import random
import resource
import time
import tracemalloc

from manifestdiff import compare_manifests

REGIONS = ["cn-east-1", "cn-north-1", "cn-south-1"]
IMAGES = ["ubuntu-22.04", "centos-7", "debian-12", "alpine-3.20"]
TAGS_POOL = ["env", "team", "tier", "owner_team"]


def make_resource(index: int, rng: random.Random) -> dict:
    return {
        "id": f"res-{index:06d}",
        "type": rng.choice(["compute.vm", "db.mysql", "cache.redis"]),
        "region": rng.choice(REGIONS),
        "cpu": rng.choice([2, 4, 8, 16]),
        "memory_gb": rng.choice([4, 8, 16, 32, 64]),
        "image": rng.choice(IMAGES),
        "encrypted": rng.choice([True, False]),
        "ports": sorted(rng.sample(range(80, 9000), rng.randint(1, 3))),
        "tags": {k: f"v{rng.randint(0, 5)}" for k in rng.sample(TAGS_POOL, 2)},
        "description": f"resource number {index}",
        "owner": f"team-{rng.randint(0, 20)}@example.com",
    }


def make_equivalent(record: dict, rng: random.Random) -> dict:
    """把若干关键字段改写成等价写法（应判合规、不算差异）。"""
    record = dict(record)
    record["cpu"] = str(record["cpu"])
    record["memory_gb"] = f"{record['memory_gb']}GB"
    record["encrypted"] = "TRUE" if record["encrypted"] else "FALSE"
    record["ports"] = ",".join(str(p) for p in reversed(record["ports"]))
    tags = record["tags"]
    if isinstance(tags, dict):
        record["tags"] = [f"{k}={v}" for k, v in tags.items()]
    record["image"] = record["image"].upper()
    return record


def build_pair(pair_id: int, resources_per_manifest: int, rng: random.Random):
    base = [make_resource(pair_id * 1000 + i, rng) for i in range(resources_per_manifest)]
    cand = [dict(r) for r in base]

    removed = 0
    if rng.random() < 0.3:
        del cand[rng.randrange(len(cand))]
        removed = 1
    if rng.random() < 0.3:
        cand.append(make_resource(pair_id * 1000 + 900_000 + pair_id, rng))

    for record in cand:
        roll = rng.random()
        if roll < 0.03:
            record["cpu"] = int(record["cpu"]) + 2  # 关键字段违规
        elif roll < 0.06:
            del record["region"]  # 关键字段缺失
        elif roll < 0.09:
            record["cost_center"] = f"CC-{rng.randint(0, 99)}"  # 未知字段
        elif roll < 0.12:
            record["description"] = record["description"] + " updated"  # 合规差异
        elif roll < 0.22:
            target = make_equivalent(record, rng)  # 等价写法
            record.clear()
            record.update(target)
    rng.shuffle(cand)
    return {"resources": base}, {"resources": cand}, removed


def run(count: int, resources_per_manifest: int, seed: int, trace_memory: bool = True):
    rng = random.Random(seed)
    totals = {"violation": 0, "compliant": 0, "needs_review": 0}
    missing_total = equiv_total = matched = 0

    if trace_memory:
        tracemalloc.start()
    rusage_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    started = time.perf_counter()

    for pair_id in range(count):
        baseline, candidate, _ = build_pair(pair_id, resources_per_manifest, rng)
        report = compare_manifests(baseline, candidate)
        for key in totals:
            totals[key] += report["summary"]["diff_counts"][key]
        missing_total += report["summary"]["key_field_missing_count"]
        equiv_total += report["summary"]["equivalent_notation_count"]
        matched += report["summary"]["matched_resources"]

    elapsed = time.perf_counter() - started
    if trace_memory:
        _, peak_traced = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    else:
        peak_traced = None
    rusage_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    return {
        "manifest_pairs": count,
        "resources_per_manifest": resources_per_manifest,
        "total_resources_compared": count * resources_per_manifest,
        "elapsed_seconds": round(elapsed, 3),
        "avg_seconds_per_pair": round(elapsed / count, 6),
        "throughput_pairs_per_second": round(count / elapsed, 1),
        "peak_python_bytes_tracemalloc": peak_traced,
        "rss_peak_kb_getrusage": max(rusage_before, rusage_after),
        "aggregated_diffs": totals,
        "key_field_missing_total": missing_total,
        "equivalent_notation_total": equiv_total,
        "matched_resources_total": matched,
    }


def main():
    parser = argparse.ArgumentParser(description="1 万份资源清单比对基准")
    parser.add_argument("--count", type=int, default=10_000)
    parser.add_argument("--resources", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20261005)
    parser.add_argument("-o", "--output", default="output/benchmark.json")
    args = parser.parse_args()

    timed = run(args.count, args.resources, args.seed, trace_memory=False)
    traced = run(args.count, args.resources, args.seed, trace_memory=True)
    stats = {
        "manifest_pairs": timed["manifest_pairs"],
        "resources_per_manifest": timed["resources_per_manifest"],
        "total_resources_compared": timed["total_resources_compared"],
        "elapsed_seconds": timed["elapsed_seconds"],
        "avg_seconds_per_pair": timed["avg_seconds_per_pair"],
        "throughput_pairs_per_second": timed["throughput_pairs_per_second"],
        "elapsed_seconds_with_tracemalloc": traced["elapsed_seconds"],
        "peak_python_mib_tracemalloc": round(traced["peak_python_bytes_tracemalloc"] / 1024 / 1024, 2),
        "rss_peak_mib_getrusage": round(max(timed["rss_peak_kb_getrusage"], traced["rss_peak_kb_getrusage"]) / 1024, 2),
        "aggregated_diffs": timed["aggregated_diffs"],
        "key_field_missing_total": timed["key_field_missing_total"],
        "equivalent_notation_total": timed["equivalent_notation_total"],
        "matched_resources_total": timed["matched_resources_total"],
    }
    text = json.dumps(stats, ensure_ascii=False, indent=2)
    print(text)
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(text + "\n")
    print(f"已写入 {args.output}")


if __name__ == "__main__":
    main()

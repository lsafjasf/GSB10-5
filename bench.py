#!/usr/bin/env python3
"""性能自测：对 1 万份清单两两比对，输出耗时与内存峰值。

运行：python3 bench.py [--count 10000] [--entries 20]
"""

import argparse
import json
import random
import resource
import time
import tracemalloc

from manifest_diff import compare_manifests


def generate_pair(index, entries, rnd):
    """生成一对清单：约 2% 条目违规、2% 合规差异、1% 待确认。"""
    resources_a = []
    resources_b = []
    for j in range(entries):
        entry = {
            "name": "pkg-%d" % j,
            "type": "rpm",
            "version": "1.%d.0" % j,
            "checksum": "%016x" % rnd.getrandbits(64),
            "size": 1024 * (j + 1),
            "path": "/opt/app/lib%d.so" % j,
            "description": "library %d" % j,
            "tags": ["core", "group-%d" % (j % 5)],
        }
        other = dict(entry)
        roll = rnd.random()
        if roll < 0.02:
            other["version"] = "9.9.9"            # 违规差异
        elif roll < 0.04:
            other["description"] = "rewritten"    # 合规差异
        elif roll < 0.05:
            other["custom_field"] = "unexpected"  # 待确认
        resources_a.append(entry)
        resources_b.append(other)
    return ({"manifest_id": "m-%d" % index, "resources": resources_a},
            {"manifest_id": "m-%d" % index, "resources": resources_b})


def main():
    parser = argparse.ArgumentParser(description="资源清单比对性能自测")
    parser.add_argument("--count", type=int, default=10000, help="清单对数")
    parser.add_argument("--entries", type=int, default=20, help="每份清单条目数")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rnd = random.Random(args.seed)

    t0 = time.perf_counter()
    pairs = [generate_pair(i, args.entries, rnd) for i in range(args.count)]
    t1 = time.perf_counter()

    keys = ("violation_count", "compliant_diff_count", "pending_count",
            "missing_critical_field_count")
    totals = dict.fromkeys(keys, 0)

    tracemalloc.start()
    t2 = time.perf_counter()
    for manifest_a, manifest_b in pairs:
        result = compare_manifests(manifest_a, manifest_b)
        for key in keys:
            totals[key] += result["summary"][key]
    t3 = time.perf_counter()
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    max_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    report = {
        "manifest_pairs": args.count,
        "entries_per_manifest": args.entries,
        "total_entries_compared": args.count * args.entries,
        "generate_seconds": round(t1 - t0, 3),
        "compare_seconds": round(t3 - t2, 3),
        "avg_compare_ms_per_pair": round((t3 - t2) / args.count * 1000, 4),
        "tracemalloc_peak_mb": round(peak / 1024 / 1024, 2),
        "process_max_rss_mb": round(max_rss_mb, 2),
        "diff_totals": totals,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

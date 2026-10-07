"""Benchmark the full risk check on 1M records: wall time and peak memory."""

from __future__ import annotations

import json
import os
import resource
import sys
import time
import tracemalloc

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from anoncheck import build_report, count_classes, find_risky_records, risky_class_map  # noqa: E402

FIELDS = ["age", "gender", "zipcode", "occupation"]
K = 5


def main(path):
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    tracemalloc.start()
    t0 = time.perf_counter()
    # pass 1: count equivalence classes
    counts, total = count_classes(path, FIELDS)
    report = build_report(counts, total, FIELDS, k=K)
    risky = risky_class_map(counts, K)
    t1 = time.perf_counter()
    # pass 2: locate concrete high-risk records
    records = find_risky_records(path, FIELDS, risky, limit=1000)
    t2 = time.perf_counter()
    _, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    result = {
        "records": total,
        "qi_fields": FIELDS,
        "k": K,
        "distinct_classes": report.num_classes,
        "pass1_counting_seconds": round(t1 - t0, 3),
        "pass2_locate_seconds": round(t2 - t1, 3),
        "total_seconds": round(t2 - t0, 3),
        "python_allocator_peak_mb": round(py_peak / 1024 / 1024, 1),
        "process_peak_rss_mb": round(rss_after / 1024, 1),
        "rss_delta_mb": round((rss_after - rss_before) / 1024, 1),
        "risky_classes": report.rare_classes,
        "risky_records": report.rare_records,
        "unique_classes": report.unique_classes,
        "min_class_size": report.min_class_size,
        "located_sample_records": len(records),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    out_dir = os.path.join(HERE, "out")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "benchmark_1m.json"), "w",
              encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    csv_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        HERE, "data", "benchmark_1m.csv")
    main(csv_path)

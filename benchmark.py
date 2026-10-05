"""1M-record verification benchmark: wall time and Python heap peak."""
import argparse
import json
import os
import sys
import resource
import time
import tracemalloc

from rollback_verify.checker import CompatibilityChecker, verdict_and_steps
import scenarios as S

REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


def run(count):
    checker = CompatibilityChecker(S.OLD, S.SCHEMA_NEW_ENUM,
                                   enum_mappings=S.ENUM_MAPPINGS)
    records = S.gen_records(seed=7, count=count, mode="new_enum")

    tracemalloc.start()
    t0 = time.perf_counter()

    checked = 0
    readable = 0
    enum_bad = 0
    for rec in records:
        issues = checker.check_record(rec, checked)
        blocking = [i for i in issues if i.kind != "unknown_field_ignored"]
        enum_bad += sum(1 for i in blocking if i.kind == "unknown_enum_value")
        checked += 1
        if not blocking:
            readable += 1

    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    result = {
        "records": checked,
        "scenario": "new_enum_value (10% 记录带新枚举 PENDING_DELETE)",
        "elapsed_sec": round(elapsed, 3),
        "records_per_sec": round(checked / elapsed, 1),
        "peak_python_heap_bytes": peak,
        "peak_python_heap_mb": round(peak / 1048576.0, 2),
        "peak_process_rss_mb": round(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 2),
        "readable_records": readable,
        "unreadable_records": enum_bad,
        "note": "流式逐条生成+校验，不持有全量数据；heap 峰值为 tracemalloc 统计的 Python 堆，rss 峰值为 getrusage 统计的进程常驻内存",
    }
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(os.path.join(REPORT_DIR, "benchmark.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1_000_000)
    args = ap.parse_args()
    return run(args.count)


if __name__ == "__main__":
    sys.exit(main())

"""对合成项目计时并测量内存峰值，同时核对 planted issues 全部检出。"""

import argparse
import json
import os
import resource
import sys
import time
import tracemalloc

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from revcheck import check_project  # noqa: E402

DEFAULT_BENCH = os.path.join(REPO_ROOT, "build", "bench")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bench", default=DEFAULT_BENCH)
    args = parser.parse_args()

    meta_path = os.path.join(args.bench, "expected.json")
    with open(meta_path, "r", encoding="utf-8") as fh:
        meta = json.load(fh)

    # 计时与测内存分开两遍：tracemalloc 本身有 2-4 倍耗时开销
    start = time.perf_counter()
    findings = check_project(args.bench)
    elapsed = time.perf_counter() - start

    tracemalloc.start()
    findings2 = check_project(args.bench)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(findings) == len(findings2)
    maxrss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    by_rule = {}
    for f in findings:
        by_rule[f.rule_id] = by_rule.get(f.rule_id, 0) + 1

    print(f"files={meta['files']} lines={meta['lines']}")
    print(f"elapsed={elapsed:.3f}s  ({meta['lines'] / elapsed:,.0f} lines/s)")
    print(f"tracemalloc_peak={peak / 1024 / 1024:.1f} MiB")
    print(f"ru_maxrss={maxrss_kb / 1024:.1f} MiB (整进程, 含解释器)")
    print(f"findings={len(findings)} by_rule={by_rule}")

    # 大规模漏报复核：planted 的每类问题必须全部检出
    ok = True
    for rule, count in meta["planted"].items():
        got = by_rule.get(rule, 0)
        if got != count:
            print(f"MISMATCH {rule}: planted={count} found={got}")
            ok = False
    print("planted-check:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

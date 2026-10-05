"""Benchmark: 1000 version pairs, wall time and peak memory."""

import copy
import random
import resource
import time
import tracemalloc

from compat_check import check_compatibility
from test_compat_check import base_manifest


def make_pairs(n, seed=42):
    rng = random.Random(seed)
    pairs = []
    for i in range(n):
        old = base_manifest(f"1.{i}.0")
        new = copy.deepcopy(old)
        new["version"] = f"1.{i}.1"
        kind = i % 5
        if kind == 1:  # add optional param
            new["functions"]["resize_image"]["params"].append(
                {"name": f"opt{i}", "kind": "keyword_only",
                 "required": False, "default": i})
        elif kind == 2:  # change default
            new["functions"]["resize_image"]["params"][1]["default"] = \
                800 + i
        elif kind == 3:  # change error semantics
            new["functions"]["resize_image"]["errors"]["bad_dimension"] = \
                "TypeError"
        elif kind == 4:  # add function
            new["functions"][f"helper_{i}"] = {
                "params": [], "returns": "int", "errors": {}}
        pairs.append((old, new))
    return pairs


def main():
    pairs = make_pairs(1000)
    tracemalloc.start()
    start = time.perf_counter()
    verdicts = {}
    for old, new in pairs:
        result = check_compatibility(old, new)
        verdicts[result.verdict] = verdicts.get(result.verdict, 0) + 1
    elapsed = time.perf_counter() - start
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    print(f"pairs:            {len(pairs)}")
    print(f"verdicts:         {verdicts}")
    print(f"total wall time:  {elapsed * 1000:.2f} ms")
    print(f"avg per pair:     {elapsed / len(pairs) * 1e6:.1f} us")
    print(f"tracemalloc peak: {peak / 1024:.1f} KiB")
    print(f"max RSS:          {rss_kb / 1024:.1f} MiB")


if __name__ == "__main__":
    main()

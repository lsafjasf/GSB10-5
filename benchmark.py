#!/usr/bin/env python3
"""对 1000 对版本做兼容性检查，输出总耗时、平均每对耗时与内存峰值。

运行: python3 benchmark.py [对数，默认 1000]
生成过程使用固定种子，结果可复现。
"""

import json
import random
import sys
import time
import resource
import tracemalloc

from compat_checker import compare_manifests, report_json


def make_manifest(rng, version):
    funcs = {}
    for i in range(rng.randint(3, 8)):
        params = {}
        for j in range(rng.randint(1, 6)):
            params[f"p{j}"] = {
                "type": rng.choice(["int", "str", "bytes", "bool"]),
                "required": rng.random() < 0.5,
                "default": rng.choice([0, 1, "auto", None, True]),
            }
        funcs[f"fn{i}"] = {
            "params": params,
            "returns": {"type": rng.choice(["bytes", "str", "int"])},
            "raises": {f"Error{k}": f"cond{k}" for k in range(rng.randint(0, 3))},
        }
    return {"name": "bench-plugin", "version": version, "functions": funcs}


def mutate(rng, manifest):
    """对清单做一处随机变更，模拟一次版本演进。"""
    m = json.loads(json.dumps(manifest))
    funcs = m["functions"]
    fname = rng.choice(sorted(funcs))
    fn = funcs[fname]
    action = rng.randrange(6)
    if action == 0 and fn["params"]:
        pname = rng.choice(sorted(fn["params"]))
        fn["params"][pname]["default"] = rng.choice([0, 1, "auto", None, True])
    elif action == 1:
        fn["params"][f"new{rng.randrange(1000)}"] = {
            "type": "int", "required": rng.random() < 0.3, "default": 0}
    elif action == 2 and fn["raises"]:
        ename = rng.choice(sorted(fn["raises"]))
        fn["raises"][ename] = f"cond{rng.randrange(1000)}"
    elif action == 3 and fn["params"]:
        pname = rng.choice(sorted(fn["params"]))
        fn["params"][pname]["type"] = rng.choice(["int", "str", "bytes"])
    elif action == 4:
        fn["returns"]["type"] = rng.choice(["bytes", "str", "int"])
    else:
        fn["raises"][f"Error{rng.randrange(1000)}"] = "new condition"
    return m


def main():
    pairs = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    rng = random.Random(20261005)

    versions = []
    current = make_manifest(rng, "1.0.0")
    for i in range(pairs + 1):
        versions.append(current)
        current = mutate(rng, current)

    tracemalloc.start()
    start = time.perf_counter()
    verdicts = {}
    for i in range(pairs):
        report = compare_manifests(versions[i], versions[i + 1])
        verdicts[report.verdict] = verdicts.get(report.verdict, 0) + 1
        if i == 0:
            report_json(report)
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    print(f"检查对数:          {pairs}")
    print(f"总耗时:            {elapsed * 1000:.2f} ms")
    print(f"平均每对耗时:      {elapsed / pairs * 1000:.4f} ms")
    print(f"检查器内存峰值:    {peak / 1024:.1f} KiB (tracemalloc，仅检查阶段)")
    print(f"进程峰值RSS:       {rss_kib} KiB (ru_maxrss)")
    print(f"判定分布:        {json.dumps(verdicts, sort_keys=True)}")


if __name__ == "__main__":
    main()

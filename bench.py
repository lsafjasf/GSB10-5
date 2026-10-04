"""基准：10 万次展开的耗时与内存峰值（仅标准库）。"""

import argparse
import os
import resource
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from env_expand import expand


def make_env(depth=8, width=4):
    env = {f"LEAF{i}": f"leaf-{i}" for i in range(width)}
    for d in range(depth):
        for i in range(width):
            if d == 0:
                env[f"V{d}_{i}"] = f"$LEAF{i}/d0"
            else:
                env[f"V{d}_{i}"] = f"$V{d-1}_{i}/d{d}"
    return env


def bench(n, template, env, label):
    tracemalloc.start()
    t0 = time.perf_counter()
    for _ in range(n):
        expand(template, env)
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"[{label}]")
    print(f"  次数:           {n}")
    print(f"  总耗时:         {elapsed:.3f} s")
    print(f"  单次均耗:       {elapsed / n * 1e6:.2f} us")
    print(f"  tracemalloc峰值: {peak / 1024:.1f} KiB")
    print(f"  进程RSS峰值:     {rss_kb / 1024:.1f} MiB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=100_000)
    args = ap.parse_args()

    env = make_env()
    bench(args.n, "plain-$LEAF0-end", env, "普通引用")
    bench(args.n, "$V7_0", env, "嵌套引用（8 层链）")
    bench(args.n, r"escaped: \$V7_0 and $V7_0", env, "转义 + 嵌套混合")


if __name__ == "__main__":
    main()

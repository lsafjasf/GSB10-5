#!/usr/bin/env python3
"""1 万个参数项的对比耗时与内存峰值基准。

用法：python3 bench/benchmark.py [参数个数，默认 10000]
"""
import os
import resource
import statistics
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from envdiff import compare


def generate(n=10000, seed=42):
    import random
    rng = random.Random(seed)
    env_a, env_b = {}, {}
    for i in range(n):
        key = f"PARAM_{i:05d}"
        roll = rng.random()
        if roll < 0.90:  # 完全一致
            value = f"value-{rng.randint(0, 10**6)}"
            env_a[key] = env_b[key] = value
        elif roll < 0.94:  # 写法不同但等价
            env_a[key] = f"{rng.randint(1, 1024)}MB"
            env_b[key] = env_a[key].replace("MB", "mb")
        elif roll < 0.96:  # 白名单有意差异
            env_a[key], env_b[key] = "300", "600"
        elif roll < 0.98:  # 疑似遗漏（基线有、B 缺失）
            env_a[key] = f"tuned-{i}"
        elif roll < 0.99:  # 敏感参数（值不同）
            env_a[key] = f"secret-a-{i}"
            env_b[key] = f"secret-b-{i}"
            env_a[key] = "API_SECRET_" + env_a[key]
            env_b[key] = "API_SECRET_" + env_b[key]
        else:  # 待确认
            env_a[key], env_b[key] = f"a-{i}", f"b-{i}"
    whitelist = [{"key": f"PARAM_{i:05d}", "env_a": "300", "env_b": "600",
                  "reason": "bench"} for i in range(n)
                 if env_a.get(f"PARAM_{i:05d}") == "300"]
    baseline = {key: value for key, value in env_a.items()}
    return env_a, env_b, whitelist, baseline


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10000
    env_a, env_b, whitelist, baseline = generate(n)
    print(f"参数规模：env_a={len(env_a)} 项, env_b={len(env_b)} 项")

    # 纯耗时：跑 5 次取中位数（不开 tracemalloc，避免测量开销）
    timings = []
    for _ in range(5):
        start = time.perf_counter()
        report = compare(env_a, env_b, whitelist=whitelist, baseline=baseline)
        timings.append(time.perf_counter() - start)
    median = statistics.median(timings)

    # 内存峰值：tracemalloc 测 Python 对象分配峰值
    tracemalloc.start()
    compare(env_a, env_b, whitelist=whitelist, baseline=baseline)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    print(f"对比耗时（5 次中位数）：{median * 1000:.1f} ms")
    print(f"对比耗时（最快/最慢）：{min(timings) * 1000:.1f} / "
          f"{max(timings) * 1000:.1f} ms")
    print(f"Python 堆分配峰值（tracemalloc）：{peak / 1024 / 1024:.1f} MiB")
    print(f"进程 RSS 峰值（ru_maxrss）：{rss_mb:.1f} MiB")
    print(f"结果：完全一致={report.identical_count} "
          f"等价={len(report.equivalent)} 差异={len(report.diffs)} "
          f"问题项={report.problem_count}")


if __name__ == "__main__":
    main()

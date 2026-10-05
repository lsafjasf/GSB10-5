"""100 万次口令校验基准：耗时 + 内存峰值（仅标准库）。

运行: python3 benchmark.py
"""
import resource
import time
import tracemalloc

from password_policy import PasswordPolicy

N = 1_000_000

# 固定样本池：混合“全通过 / 单条违反 / 多条违反 / 多字节 / 边界长度”
SAMPLES = [
    "Str0ng!Pass",                 # 全部通过
    "Aa1!xyz",                     # 仅太短
    "abc",                         # 多条违反
    "密码Ab1!xy",                  # 多字节，恰好 8 字符（边界）
    "Aa1!" + "wxyz" * 3 + "q",     # 17 字符，刚超最大长度（边界）
    "é" + "Aa1!wxy",          # 组合序列
    "P@ssw0rd",                    # 常见弱口令
    "Abc1!wxyaaaa",                # 连续重复超限
]


def run(policy, samples, n):
    ok_count = 0
    for i in range(n):
        if policy.validate(samples[i % len(samples)]).ok:
            ok_count += 1
    return ok_count


def main():
    policy = PasswordPolicy(min_length=8, max_length=16)
    run(policy, SAMPLES, 10_000)  # 预热

    # --- 耗时 ---
    t0 = time.perf_counter()
    ok_count = run(policy, SAMPLES, N)
    elapsed = time.perf_counter() - t0

    # --- 内存峰值（tracemalloc 统计 Python 堆；ru_maxrss 统计进程 RSS）---
    tracemalloc.start()
    run(policy, SAMPLES, N)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    maxrss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    print(f"校验次数        : {N:,}")
    print(f"通过/拒绝       : {ok_count:,} / {N - ok_count:,}")
    print(f"总耗时          : {elapsed:.3f} s")
    print(f"单次平均        : {elapsed / N * 1e6:.2f} µs")
    print(f"吞吐            : {N / elapsed:,.0f} 次/秒")
    print(f"Python 堆峰值   : {peak / 1024:.1f} KiB (tracemalloc)")
    print(f"进程 RSS 峰值   : {maxrss_kb / 1024:.2f} MiB (ru_maxrss)")


if __name__ == "__main__":
    main()

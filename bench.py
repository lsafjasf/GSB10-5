"""基准：1 万个参数项的对比耗时与内存峰值（tracemalloc + resource）。"""

import resource
import time
import tracemalloc

import envdiff
from envdiff import WhitelistRule, compare_envs

N = 10_000


def build_data(n=N):
    env_a, env_b, expected, baseline = {}, {}, {}, {}
    for i in range(n):
        k = f"param.{i:05d}"
        mod = i % 10
        if mod < 6:      # 60% 完全一致
            env_a[k] = env_b[k] = f"value-{i}"
        elif mod < 8:    # 20% 写法不同但等价
            env_a[k] = f"{1024 + i}MB"
            env_b[k] = f"{(1024 + i) // 1024}GB" if (1024 + i) % 1024 == 0 else f"{1024 + i}mb"
        elif mod == 8:   # 10% 有意差异（声明期望值）
            env_a[k] = "cn"
            env_b[k] = "us"
            expected[k] = {"env_a": "cn", "env_b": "us", "reason": "按地域"}
        else:            # 10% 疑似遗漏
            env_a[k] = "30"
            env_b[k] = "60"
            baseline[k] = {"env_a": "30", "env_b": "30"}
    env_a["db.password"] = "secret-a"
    env_b["db.password"] = "secret-b"
    whitelist = [WhitelistRule(key="param.99998", reason="demo")]
    return env_a, env_b, expected, whitelist, baseline


def main():
    env_a, env_b, expected, whitelist, baseline = build_data()
    print(f"参数项: env_a={len(env_a)}, env_b={len(env_b)}")

    tracemalloc.start()
    t0 = time.perf_counter()
    report = compare_envs(env_a, env_b, expected, whitelist, baseline)
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    print(f"对比耗时: {elapsed*1000:.1f} ms ({elapsed/N*1e6:.2f} us/项)")
    print(f"内存峰值(tracemalloc): {peak/1024/1024:.1f} MiB")
    print(f"进程峰值 RSS: {rss_mb:.1f} MiB")
    print("分类汇总:", {envdiff.CATEGORY_LABELS[k]: v
                        for k, v in report.summary().items()})


if __name__ == "__main__":
    main()

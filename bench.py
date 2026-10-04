"""10 万条路径规范化基准：耗时 + 内存峰值。
运行: python3 bench.py
"""
import os
import random
import resource
import string
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from pathnorm import equivalent, normalize  # noqa: E402

N = 100_000
random.seed(42)


def gen_paths(n):
    """混合生成：windows/posix、正反斜杠、大小写、. 与 ..、少量保留名。"""
    words = ["Users", "alice", "Docs", "Project", "SRC", "data", "Temp",
             "CON", "file.TXT", "report.pdf", "..", ".", "子目录", "文件.txt"]
    seps = ["/", "\\", "/", "/"]  # 偏向 '/'，但含反斜杠混用
    out = []
    for i in range(n):
        if i % 2 == 0:
            parts = [random.choice(words) for _ in range(random.randint(2, 8))]
            p = random.choice(seps).join(parts)
            out.append(random.choice(["C:\\", "D:/"]) + p)
        else:
            parts = [random.choice(words) for _ in range(random.randint(2, 8))]
            out.append("/" + random.choice(seps).join(parts))
    return out


def main():
    paths = gen_paths(N)
    print(f"样本数: {len(paths)}")

    tracemalloc.start()
    t0 = time.perf_counter()

    ok, err = 0, 0
    results = []
    for i, p in enumerate(paths):
        style = "windows" if i % 2 == 0 else "posix"
        try:
            results.append(normalize(p, style=style, case_sensitive=False))
        except Exception:
            err += 1

    t1 = time.perf_counter()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # 等价性判定吞吐：相邻两条互判（命中保留名的记为不可比）
    from pathnorm import PathNormError

    def safe_eq(a, b, style):
        try:
            return equivalent(a, b, style=style)
        except PathNormError:
            return False

    # 构造变体：换分隔符 + 翻转大小写，预期与原文等价（不敏感模式）
    variants = [
        p.replace("/", "\\").swapcase() if "/" in p else p.replace("\\", "/").swapcase()
        for p in paths
    ]
    t2 = time.perf_counter()
    eq = sum(
        safe_eq(paths[i], variants[i],
                "windows" if i % 2 == 0 else "posix")
        for i in range(N)
    )
    t3 = time.perf_counter()

    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    print(f"规范化成功: {ok if (ok := len(results)) else 0}, 校验拒绝(保留名等): {err}")
    print(f"规范化耗时: {t1 - t0:.3f}s  ({(t1 - t0) / N * 1e6:.2f} µs/条)")
    print(f"等价判定耗时(10万对): {t3 - t2:.3f}s, 等价命中: {eq}")
    print(f"tracemalloc 内存峰值: {peak / 1024 / 1024:.1f} MiB")
    print(f"进程 RSS 峰值: {rss_kb / 1024:.1f} MiB")


if __name__ == "__main__":
    main()

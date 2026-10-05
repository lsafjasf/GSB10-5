"""基准：1 万个组件的解析耗时与内存峰值。运行: python3 benchmark.py [n]"""
import gc
import statistics
import sys
import tracemalloc
from time import perf_counter

from di import Container

N = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000
RUNS = 3


def make_chain(n):
    """线性链: c0 <- c1 <- ... <- c(n-1)，根是 c(n-1)。"""
    c = Container()
    c.register("c0", lambda: 0)
    for i in range(1, n):
        c.register(f"c{i}", lambda dep, i=i: i, deps=(f"c{i-1}",))
    return c, f"c{n-1}"


def make_star(n):
    """星型: root 直接依赖 n-1 个叶子。"""
    c = Container()
    leaves = [f"leaf{i}" for i in range(n - 1)]
    for name in leaves:
        c.register(name, lambda: 0)
    c.register("root", lambda *deps: len(deps), deps=tuple(leaves))
    return c, "root"


def make_tree(n):
    """二叉树: 节点 i 依赖 2i+1、2i+2（存在则），根是 0。"""
    c = Container()
    for i in reversed(range(n)):
        deps = tuple(f"n{j}" for j in (2 * i + 1, 2 * i + 2) if j < n)
        c.register(f"n{i}", lambda *deps: len(deps), deps=deps)
    return c, "n0"


def bench(label, make):
    times, peaks, pure = [], [], []
    for _ in range(RUNS):
        gc.collect()
        container, root = make(N)
        # 口径 A: 不带内存采样器的纯解析耗时
        t0 = perf_counter()
        container.resolve(root)
        pure.append((perf_counter() - t0) * 1000)
        del container
        gc.collect()
        # 口径 B: tracemalloc 监控下的耗时 + Python 堆内存峰值
        container, root = make(N)
        tracemalloc.start()
        t0 = perf_counter()
        container.resolve(root)
        elapsed = perf_counter() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        times.append(elapsed * 1000)
        peaks.append(peak / 1024 / 1024)
        del container
    print(f"{label:<8s} n={N}  纯解析耗时(ms): min={min(pure):7.2f} "
          f"median={statistics.median(pure):7.2f} | "
          f"监控下耗时(ms): median={statistics.median(times):7.2f} | "
          f"内存峰值(MiB): median={statistics.median(peaks):6.2f}")


if __name__ == "__main__":
    print(f"Python {sys.version.split()[0]}  每场景运行 {RUNS} 次")
    bench("线性链", make_chain)
    bench("星型", make_star)
    bench("二叉树", make_tree)

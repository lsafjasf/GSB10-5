"""bench.py — 1 万个组件的解析耗时与内存峰值基准（仅标准库）。

图结构：组件 c_i 急切依赖 c_{i-1}, c_{i-2}, c_{i-3}（下界截断），
即一条宽度为 3 的深链 DAG，深度 10000，验证迭代式算法不爆栈。
另附：10000 个组件全部延迟注入的对比场景。
"""

import resource
import sys
import time
import tracemalloc

from di import Container

N = 10_000
FAN = 3


def build_container(lazy: bool) -> Container:
    c = Container()
    c.register("c0", lambda: 0)
    for i in range(1, N):
        deps = {f"d{k}": f"c{i-k}" for k in range(1, FAN + 1) if i - k >= 0}
        if lazy:
            c.register(f"c{i}", lambda **kw: kw, lazy=deps)
        else:
            c.register(f"c{i}",
                       lambda **kw: 1 + max(kw.values()) if kw else 0,
                       deps=deps)
    return c


def bench(label, lazy):
    c = build_container(lazy)
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    t0 = time.perf_counter()
    order = c.resolution_order([f"c{N-1}"])
    t_order = time.perf_counter() - t0
    # 急切场景：拓扑序覆盖全部 N 个；延迟场景：急切图为空，仅根节点
    assert len(order) == (1 if lazy else N)

    tracemalloc.start()
    t0 = time.perf_counter()
    root = c.resolve(f"c{N-1}")
    if lazy:
        # 沿 LazyRef 逐级 get()，强制把全部 N 个组件真正解析出来
        seen, queue = set(), list(root.values())
        while queue:
            ref = queue.pop()
            if ref.name in seen:
                continue
            seen.add(ref.name)
            val = ref.get()
            if isinstance(val, dict):
                queue.extend(val.values())
        assert len(seen) == N - 1  # 根之外的全部组件
    t_resolve = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"[{label}] 组件数={N}")
    print(f"  拓扑排序耗时 : {t_order*1000:8.2f} ms")
    print(f"  全量解析耗时 : {t_resolve*1000:8.2f} ms")
    print(f"  内存峰值(tracemalloc): {peak/1024/1024:8.2f} MiB")
    print(f"  进程 RSS 增量        : "
          f"{(rss_after-rss_before)/1024:8.2f} MiB "
          f"(峰值 RSS {rss_after/1024:.2f} MiB)")
    print(f"  解析轨迹条数         : {len(c.tracer.events)}")


if __name__ == "__main__":
    print(f"Python {sys.version.split()[0]}")
    bench("急切依赖", lazy=False)
    bench("延迟注入", lazy=True)

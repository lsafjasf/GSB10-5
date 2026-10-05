"""1 万个组件依赖图的推导耗时与内存峰值。

运行：python3 bench.py
"""
import random
import time
import tracemalloc

from deploy_order import DependencyGraph, STRONG, WEAK, OPTIONAL, plan, validate


def build_graph(n=10_000, seed=42):
    rng = random.Random(seed)
    g = DependencyGraph()
    for i in range(n):
        g.add_component(f"c{i}")
    # 强依赖：每个节点依赖最多 4 个编号更小的节点 => 无环 DAG
    for i in range(1, n):
        for _ in range(4):
            j = rng.randrange(i)
            g.add_dependency(f"c{i}", f"c{j}", STRONG)
    # 弱依赖 1 万条（方向随机，一部分必然被放弃）
    for _ in range(n):
        a, b = rng.randrange(n), rng.randrange(n)
        g.add_dependency(f"c{a}", f"c{b}", WEAK)
    # 可选依赖 1 万条
    for _ in range(n):
        a, b = rng.randrange(n), rng.randrange(n)
        g.add_dependency(f"c{a}", f"c{b}", OPTIONAL)
    return g


def main():
    n = 10_000
    g = build_graph(n)
    counts = {k: len(g.edges(k)) for k in (STRONG, WEAK, OPTIONAL)}
    print(f"图规模: {n} 组件, 强依赖 {counts[STRONG]}, "
          f"弱依赖 {counts[WEAK]}, 可选依赖 {counts[OPTIONAL]}")

    tracemalloc.start()
    t0 = time.perf_counter()
    p = plan(g)
    t1 = time.perf_counter()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    data = validate(g, p)
    t2 = time.perf_counter()

    print(f"推导耗时: {(t1 - t0) * 1000:.1f} ms")
    print(f"校验耗时: {(t2 - t1) * 1000:.1f} ms")
    print(f"内存峰值(tracemalloc): {peak / 1024 / 1024:.1f} MiB")
    print(f"分层数: {data['layers']}, 最宽层: {data['max_layer_width']}")
    print(f"强依赖满足: {data['strong_satisfied']}/{data['strong_total']}, "
          f"断边: {data['strong_cut']}, 违例: {len(data['strong_violations'])}")
    print(f"弱依赖采纳: {data['weak_honored']}, 放弃: {data['weak_deferred']}")
    print(f"校验通过: {data['ok']}")


if __name__ == "__main__":
    main()

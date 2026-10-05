"""基准：10 万节点、5 层嵌套，统计解析耗时与内存峰值。

运行：python3 bench.py
"""

import random
import time
import tracemalloc

from rlimit import DIMENSIONS, Node, resolve_tree

# 5 层，共 100,000 个节点：10 + 90 + 900 + 9,000 + 90,000
FANOUT = [10, 9, 10, 10, 10]
assert 10 + 90 + 900 + 9000 + 90000 == 100000


def build_tree(seed=42):
    rng = random.Random(seed)
    levels = [[Node("root", {"cpu_cores": 64, "memory_mb": 65536,
                             "pids": 4096, "disk_mb": 1048576})]]
    total = 1
    for depth, fanout in enumerate(FANOUT, start=1):
        cur = []
        idx = 0
        for parent in levels[-1]:
            for _ in range(fanout):
                idx += 1
                limits = {}
                for dim in DIMENSIONS:
                    r = rng.random()
                    if r < 0.45:
                        pass                      # 45% 缺失（继承）
                    elif r < 0.50:
                        limits[dim] = 0           # 5% 显式零值
                    elif r < 0.85:
                        limits[dim] = rng.randint(1, 128)    # 收紧
                    else:
                        limits[dim] = rng.randint(129, 4096) # 可能越限
                node = Node("n%d_%d" % (depth, idx), limits)
                parent.add(node)
                cur.append(node)
                total += 1
        levels.append(cur)
    return levels[0][0], total


def main():
    root, total = build_tree()
    print("节点总数: %d, 层数: 5" % total)

    tracemalloc.start()
    t0 = time.perf_counter()
    result, violations = resolve_tree(root)
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print("解析耗时: %.3f s" % elapsed)
    print("内存峰值(tracemalloc): %.1f MiB" % (peak / 1024 / 1024))
    print("越限收敛(clamp)次数: %d" % len(violations))
    print("平均每节点耗时: %.2f us" % (elapsed / total * 1e6))


if __name__ == "__main__":
    main()
